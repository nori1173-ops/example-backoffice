"""地点ごとの課金判定・金額計算と RUN 全体の集計（設計 2.2 / 2.4 / 6.5 / 7.3）。

判定の優先順位は **除外 > 通信なし > 課金**。除外と通信なしは金額 0 円で、単価は
表示用に残す。除外は**テナント説明欄と地点備考欄だけ**を見る（地点名・地点CD・
現場名は判定に使わない）。カメラ判定は export のヘッダ由来と設定のフォーマット名
由来の OR で、どちらで当たったかを `camera_source` に残す。

現行スキル `aggregate_report.py` からの移植点: (1) 除外対象を説明欄・備考欄に限定、
(2) 通信なしは 0 円（現行は課金加算）、(3) 単価表を引数で受け取る（現行はハードコード）。

「境界日のみ受信」は判定・金額を変えず、備考の印（`note`）として残すだけ。
"""

from dataclasses import dataclass
from datetime import datetime

from saas_a_billing.period import CHANGE_DATETIME_FORMAT, ClosingPeriod
from saas_a_billing.pricing import PriceTable, resolve_price

BOUNDARY_NOTE = "境界日のみ受信"


@dataclass(frozen=True)
class DeviceJudgement:
    """`result.json` の `devices[]` 1 件分（設計 7.3）。"""

    device_cd: str
    device_name: str
    group_cds: list[str]
    group_names: list[str]
    format_name: str
    item_count: int
    device_description: str
    is_camera: bool
    camera_source: str | None
    retention: int | None
    received: bool
    first_received_at: str | None
    last_received_at: str | None
    boundary_only: bool
    judgement: str
    unit_price: int
    amount: int
    flags: list[str]
    note: str


def contains_any(text: str | None, keywords: list[str]) -> bool:
    """大文字小文字を無視した部分一致。`text` が空、または当たる語が無ければ False。"""
    if not text:
        return False
    lowered = text.lower()
    return any(keyword and keyword.lower() in lowered for keyword in keywords)


def boundary_only(first_seen: str | None, last_seen: str | None, period: ClosingPeriod) -> bool:
    """受信が課金対象期間の初日だけ、または最終日だけに収まっているか。"""
    if not first_seen or not last_seen:
        return False
    first = datetime.strptime(first_seen, CHANGE_DATETIME_FORMAT).date()
    last = datetime.strptime(last_seen, CHANGE_DATETIME_FORMAT).date()
    return first == last and first in (period.first_date, period.last_date)


def classify(
    tenant: dict,
    device: dict,
    received: set[str],
    camera_by_export: set[str],
    first_seen: dict[str, str],
    last_seen: dict[str, str],
    settings: dict,
    table: PriceTable,
    period: ClosingPeriod,
) -> DeviceJudgement:
    """1 地点を判定して金額を決める（設計 2.2 / 6.5）。"""
    device_cd = device["device_cd"]
    keywords = settings.get("exclude_keywords") or []
    device_description = device.get("device_description") or ""
    excluded = contains_any(tenant.get("tenant_description"), keywords) or contains_any(device_description, keywords)

    format_name = device.get("format_name") or ""
    if device_cd in camera_by_export:
        camera_source = "export"
    elif format_name in (settings.get("camera_format_names") or []):
        camera_source = "settings"
    else:
        camera_source = None

    item_count = len(device.get("format_detail") or [])
    retention_period = tenant.get("retention_period")
    retention = int(retention_period) if retention_period in (1, 3) else None
    unit_price, flags = resolve_price(table, retention, item_count, camera_source is not None)

    is_received = device_cd in received
    if excluded:
        judgement, amount = "excluded", 0
    elif not is_received:
        judgement, amount = "no_data", 0
    else:
        judgement, amount = "billable", unit_price

    first_received_at = first_seen.get(device_cd)
    last_received_at = last_seen.get(device_cd)
    on_boundary = boundary_only(first_received_at, last_received_at, period)

    return DeviceJudgement(
        device_cd=device_cd,
        device_name=device.get("device_name") or "",
        group_cds=list(device.get("group_cds") or []),
        group_names=list(device.get("group_names") or []),
        format_name=format_name,
        item_count=item_count,
        device_description=device_description,
        is_camera=camera_source is not None,
        camera_source=camera_source,
        retention=retention,
        received=is_received,
        first_received_at=first_received_at,
        last_received_at=last_received_at,
        boundary_only=on_boundary,
        judgement=judgement,
        unit_price=unit_price,
        amount=amount,
        flags=flags,
        note=BOUNDARY_NOTE if on_boundary else "",
    )


def missing_devices(
    prev_result: dict | None, current_device_cds: set[str], failed_tenant_cds: frozenset[str] = frozenset()
) -> list[dict]:
    """前回 RUN の `result.json` にあり今回の `device/list` に無い地点（設計 2.5 / 7.3）。

    今回取得に失敗したテナントは地点一覧そのものが不明なので差分から外す。撤去された
    のか取れなかったのか区別できず、撤去として並べると請求停止の誤判断につながる。
    """
    if not prev_result:
        return []
    last_seen_month = prev_result.get("month", "")
    return [
        {
            "tenant_cd": tenant.get("tenant_cd", ""),
            "tenant_name": tenant.get("tenant_name", ""),
            "device_cd": device.get("device_cd", ""),
            "device_name": device.get("device_name", ""),
            "group_names": list(device.get("group_names") or []),
            "last_seen_month": last_seen_month,
        }
        for tenant in prev_result.get("tenants", [])
        if tenant.get("tenant_cd") not in failed_tenant_cds
        for device in tenant.get("devices", [])
        if device.get("device_cd") not in current_device_cds
    ]


def summarize(tenants: list[dict], missing: list[dict]) -> dict:
    """`result.json` の `summary`（設計 7.3）。消えた地点の金額は計上しない。"""
    devices = [device for tenant in tenants for device in tenant.get("devices", [])]
    judgements = [device.judgement for device in devices]
    return {
        "tenant_count": len(tenants),
        "device_count": len(devices),
        "billable_count": judgements.count("billable"),
        "no_data_count": judgements.count("no_data"),
        "excluded_count": judgements.count("excluded"),
        "review_count": sum(1 for device in devices if device.flags),
        "failed_tenant_count": sum(1 for tenant in tenants if tenant.get("status") == "failed"),
        "missing_count": len(missing),
        "total_amount": sum(int(device.amount) for device in devices),
    }


def sort_devices(devices: list[DeviceJudgement]) -> list[DeviceJudgement]:
    """先頭 `group_cd` 昇順 → `device_cd` 昇順（設計 6.5）。"""
    return sorted(devices, key=lambda device: (device.group_cds[0] if device.group_cds else "", device.device_cd))


def sort_tenants(tenants: list[dict]) -> list[dict]:
    """`tenant_cd` 昇順（設計 6.5）。"""
    return sorted(tenants, key=lambda tenant: tenant.get("tenant_cd") or "")
