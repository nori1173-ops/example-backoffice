"""テナント 1 件の収集（設計 6.4 の `CollectTenant`）。

`Init` が RUN に保存したトークンを読むだけで、ここでは決してログインし直さない
（同じ API ユーザーの二重ログインはトークン競合を起こす。設計 6.3 / 6.4）。
地点一覧・現場一覧と課金対象期間の受信状況を集め、S3 の
`parts/{tenant_cd}.json` に置く。ステートマシンへの戻り値は件数サマリだけにする
（ステート入出力のサイズ上限対策。設計 6.4）。

失敗の振り分けは設計 11.1 に従う。時間予算超過・401 などの恒久エラーは例外に
せず `status: "failed"` として parts に記録し（ハードタイムアウトで RUN が
`running` のまま残るのを避ける）、5xx・接続エラーだけを例外のまま上げて Map の
`Retry` に載せる。
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit
from saas_a_billing import period, repo, storage
from saas_a_billing.export_parser import ExportResult
from saas_a_billing.saasa_client import (
    SaasAApiError,
    SaasAClient,
    SaasACredentials,
    TimeBudgetExceeded,
    collect_export,
)
from saas_a_billing.period import ClosingPeriod

from example_backoffice.logging import LambdaLogger, get_logger

logger = get_logger()

METRICS_NAMESPACE = "example-backoffice/saas-a-billing"
TIME_BUDGET_MARGIN_SEC_DEFAULT = 150

OK = "ok"
FAILED = "failed"
TIME_BUDGET_EXCEEDED = "TIME_BUDGET_EXCEEDED"

TOO_MANY_REQUESTS = 429
SERVER_ERROR = 500

metrics = Metrics(namespace=METRICS_NAMESPACE)


class _CountingClient(SaasAClient):
    """`download/export` の呼び出し回数を数えるクライアント（parts の `export_calls`）。"""

    def __init__(self, base_url: str, token: str):
        super().__init__(base_url, token=token)
        self.export_calls = 0

    def export_window(self, *args, **kwargs):
        self.export_calls += 1
        return super().export_window(*args, **kwargs)


@dataclass
class _Collected:
    """収集の途中経過。失敗時も取得済みの分をそのまま parts に残す（設計 7.3）。"""

    devices: list[dict] = field(default_factory=list)
    groups: list[dict] = field(default_factory=list)
    result: ExportResult = field(default_factory=ExportResult)
    status: str = OK
    error: str | None = None


def _margin_sec() -> float:
    return float(os.environ.get("TIME_BUDGET_MARGIN_SEC") or TIME_BUDGET_MARGIN_SEC_DEFAULT)


def _deadline(context) -> Callable[[], float]:
    """残り時間から余裕分を引いた秒数を返す関数（設計 6.4 / 6.10）。窓ごとに評価される。"""
    margin = _margin_sec()
    return lambda: context.get_remaining_time_in_millis() / 1000 - margin


def _closing_period(event: dict) -> ClosingPeriod:
    """入力の `closing_period` から課金対象期間を組み直す（`Init` と同じ締め設定で決まる）。"""
    closing = event["closing_period"]
    return period.closing_period(event["month"], int(closing["cutoff_day"]), closing["cutoff_time"])


def _is_transient(error: SaasAApiError) -> bool:
    """再試行で直りうるか。5xx と 429 だけをステートマシンの `Retry` に載せる。"""
    return error.status_code >= SERVER_ERROR or error.status_code == TOO_MANY_REQUESTS


def _collect(client: _CountingClient, tenant_id: str, closing: ClosingPeriod, deadline) -> _Collected:
    collected = _Collected()
    try:
        collected.devices = client.list_devices(tenant_id)
        collected.groups = client.list_groups(tenant_id)
        device_cds = [device["device_cd"] for device in collected.devices]
        collected.result = collect_export(client, tenant_id, device_cds, closing, deadline)
    except TimeBudgetExceeded:
        collected.status, collected.error = FAILED, TIME_BUDGET_EXCEEDED
    except SaasAApiError as error:
        if _is_transient(error):
            raise
        collected.status, collected.error = FAILED, str(error)
    return collected


def _tenant_part(event: dict, collected: _Collected, export_calls: int) -> dict:
    """S3 に置くテナント別中間結果（設計 6.4 の `TenantPart`）。"""
    result = collected.result
    return {
        "tenant_id": event["tenant_id"],
        "tenant_cd": event["tenant_cd"],
        "status": collected.status,
        "error": collected.error,
        "devices": collected.devices,
        "groups": collected.groups,
        "received": sorted(result.received),
        "camera": sorted(result.camera),
        "first_seen": result.first_seen,
        "last_seen": result.last_seen,
        "export_calls": export_calls,
        "export_bytes": result.bytes_read,
    }


def _emit_metrics(duration_sec: float, export_calls: int, export_bytes: int) -> None:
    """設計 14.2 の実行実績。`TenantDuration` が 600 秒を超えたら 6.10 の拡張を検討する。"""
    metrics.add_metric(name="TenantDuration", unit=MetricUnit.Seconds, value=duration_sec)
    metrics.add_metric(name="ExportCalls", unit=MetricUnit.Count, value=export_calls)
    metrics.add_metric(name="ExportBytes", unit=MetricUnit.Bytes, value=export_bytes)


@metrics.log_metrics
@LambdaLogger.contextualize
def handler(event: dict, context) -> dict:
    started = time.monotonic()
    target_env = event["target_env"]
    month = event["month"]
    attempt = event["attempt"]
    tenant_cd = event["tenant_cd"]
    run = repo.get_run(target_env, month)
    credentials = SaasACredentials.from_ssm(os.environ["SSM_PREFIX"], target_env)
    client = _CountingClient(credentials.base_url, run["api_token"])
    collected = _Collected()
    try:
        collected = _collect(client, event["tenant_id"], _closing_period(event), _deadline(context))
    finally:
        duration_sec = time.monotonic() - started
        _emit_metrics(duration_sec, client.export_calls, collected.result.bytes_read)
    storage.put_part(target_env, month, tenant_cd, _tenant_part(event, collected, client.export_calls))
    repo.increment_tenants_done(target_env, month, attempt)
    logger.info(
        "テナントの収集を終えました",
        {
            "run_id": event["run_id"],
            "tenant_cd": tenant_cd,
            "status": collected.status,
            "error": collected.error,
            "devices": len(collected.devices),
            "received": len(collected.result.received),
            "export_calls": client.export_calls,
            "export_bytes": collected.result.bytes_read,
            "duration_sec": round(duration_sec, 3),
        },
    )
    return {
        "tenant_cd": tenant_cd,
        "devices": len(collected.devices),
        "received": len(collected.result.received),
        "failed": collected.status == FAILED,
    }
