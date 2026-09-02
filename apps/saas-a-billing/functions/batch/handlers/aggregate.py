"""集約・判定・Excel・配布（設計 6.3 の `Aggregate`）。

Map 出力の `tenant_cd` から parts のキーを組み立てて読む（S3 の一覧走査に依存しない。
設計 6.3）。Map の `Catch` を通ったテナントは parts が無いまま `tenant_cd` だけが返る
ため、その場合も取得失敗として集計に載せる（設計 11.1）。テナント名・説明欄・保存期間は
parts に無いので、`Init` が置いたテナント一覧のスナップショットから解決する。

判定と集計は `billing` の純粋関数に任せ、ここは入出力の組み立てに徹する。`result.json`
（設計 7.3）→ Excel（6.6）→ RUN=`succeeded`（6.9）→ 配布（6.8）の順に進む。添付は S3 に
置いた Excel のキーを参照するだけで、添付バケットに同じ Excel を二重保存しない。

通知の投函に失敗しても RUN は `succeeded` のまま `error` に記録する（設計 11.1）。
`api_token` / `token_expires_at` は `finish_success` が消す。
"""

import os
from dataclasses import asdict, replace
from datetime import date, datetime

from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit
from saas_a_billing import billing, distribution, excel, messages, period, repo, storage
from saas_a_billing.billing import DeviceJudgement
from saas_a_billing.period import JST, ClosingPeriod
from saas_a_billing.pricing import PriceTable

from example_backoffice.logging import LambdaLogger, get_logger
from example_backoffice.notify import Attachment, Email, Slack, send

logger = get_logger()

APP = "saas-a-billing"
METRICS_NAMESPACE = "example-backoffice/saas-a-billing"
RESULT_SCHEMA = "saas-a-billing.result.v1"

OK = "ok"
FAILED = "failed"
NO_DISTRIBUTION_TARGET = "配布先が無効のため送付なし"

metrics = Metrics(namespace=METRICS_NAMESPACE)


def _now() -> datetime:
    return datetime.now(JST)


def _closing_period(event: dict) -> ClosingPeriod:
    """入力の `closing_period` から課金対象期間を組み直す（`Init` と同じ締め設定で決まる）。"""
    closing = event["closing_period"]
    return period.closing_period(event["month"], int(closing["cutoff_day"]), closing["cutoff_time"])


def _price_item(event: dict) -> dict:
    """`Init` が確定した版を引き直す（実行中に締め設定が変わっても版が揺れない）。"""
    return repo.get_price_table_at(date.fromisoformat(event["price_effective_from"]))


def _previous_result(target_env: str, month: str) -> dict | None:
    """同一 `target_env` の直前の対象月で成功した RUN の `result.json`（設計 7.1 末尾）。"""
    previous = period.previous_month(month)
    run = repo.get_run(target_env, previous)
    if run is None or run.get("status") not in repo.DONE_STATUSES:
        return None
    return storage.get_result(target_env, previous)


def _part_of(target_env: str, month: str, tenant_cd: str) -> dict:
    """parts を読む。Map の `Catch` を通ったテナントは parts が無いので失敗として扱う。"""
    try:
        return storage.get_part(target_env, month, tenant_cd)
    except storage.PartNotFoundError as error:
        return {"tenant_cd": tenant_cd, "status": FAILED, "error": str(error), "devices": []}


def _judge_devices(
    part: dict, tenant: dict, settings: dict, table: PriceTable, closing: ClosingPeriod
) -> list[DeviceJudgement]:
    """テナントの全地点を判定する。取得失敗のテナントは受信判定を `failed` にする（設計 7.3）。"""
    received = set(part.get("received") or [])
    camera = set(part.get("camera") or [])
    first_seen = part.get("first_seen") or {}
    last_seen = part.get("last_seen") or {}
    devices = [
        billing.classify(tenant, device, received, camera, first_seen, last_seen, settings, table, closing)
        for device in part.get("devices") or []
    ]
    if part.get("status") == FAILED:
        devices = [replace(device, judgement=FAILED, amount=0) for device in devices]
    return billing.sort_devices(devices)


def _counts(devices: list[DeviceJudgement]) -> dict:
    """テナント別の判定件数。要確認は判定を問わずフラグが付いた地点を数える。"""
    judgements = [device.judgement for device in devices]
    return {
        "billable": judgements.count("billable"),
        "no_data": judgements.count("no_data"),
        "excluded": judgements.count("excluded"),
        "review": sum(1 for device in devices if device.flags),
    }


def _tenant_result(part: dict, tenant: dict, settings: dict, table: PriceTable, closing: ClosingPeriod) -> dict:
    """`result.json` の `tenants[]` 1 件（設計 7.3）。`devices` は判定済みのまま返す。"""
    devices = _judge_devices(part, tenant, settings, table, closing)
    return {
        "tenant_id": part.get("tenant_id") or tenant.get("tenant_id", ""),
        "tenant_cd": part["tenant_cd"],
        "tenant_name": tenant.get("tenant_name", ""),
        "tenant_description": tenant.get("tenant_description", ""),
        "retention_period": tenant.get("retention_period"),
        "device_count_api": int(tenant.get("device_count") or 0),
        "status": part.get("status") or OK,
        "error": part.get("error"),
        "export_calls": int(part.get("export_calls") or 0),
        "export_bytes": int(part.get("export_bytes") or 0),
        "subtotal": sum(device.amount for device in devices),
        "counts": _counts(devices),
        "devices": devices,
    }


def _collect_tenants(event: dict, settings: dict, table: PriceTable, closing: ClosingPeriod) -> list[dict]:
    """Map 出力の `tenant_cd` から parts を読み、`tenant_cd` 昇順で判定結果を並べる（設計 6.5）。"""
    target_env, month = event["target_env"], event["month"]
    attributes = {tenant["tenant_cd"]: tenant for tenant in storage.get_tenants(target_env, month)}
    if event["collected"] and not attributes:
        logger.warning(
            "テナント一覧のスナップショットが読めないため、除外判定と保存期間を既定値で集計します",
            {"target_env": target_env, "month": month, "key": storage.tenants_key(target_env, month)},
        )
    tenants = []
    for collected in event["collected"]:
        tenant_cd = collected["tenant_cd"]
        part = _part_of(target_env, month, tenant_cd)
        tenants.append(_tenant_result(part, attributes.get(tenant_cd, {}), settings, table, closing))
    return billing.sort_tenants(tenants)


def _run_snapshot(run: dict, event: dict, finished_at: datetime) -> dict:
    """`result.json` の `run`（設計 7.3）。画面と Excel が成果物だけで実行条件を判別できるようにする。"""
    return {
        "attempt": event["attempt"],
        "trigger": run.get("trigger", ""),
        "started_at": run.get("started_at", ""),
        "finished_at": finished_at.isoformat(),
        "requested_by": run.get("requested_by"),
        "env": os.environ["ENV"],
        "target_env": event["target_env"],
        "saasa_host": run.get("saasa_host", ""),
        "execution_arn": run.get("execution_arn", ""),
    }


def _settings_snapshot(settings: dict) -> dict:
    return {
        "exclude_keywords": settings.get("exclude_keywords") or [],
        "camera_format_names": settings.get("camera_format_names") or [],
    }


def _price_snapshot(item: dict) -> dict:
    return {
        "effective_from": item["effective_from"],
        "bands": item["bands"],
        "camera": item["camera"],
        "note": item.get("note", ""),
    }


def _duration_sec(run: dict, finished_at: datetime) -> float:
    """RUN の取得から集計完了までの経過秒（設計 14.2 の `RunDuration`）。"""
    started_at = run.get("started_at")
    if not started_at:
        return 0.0
    return (finished_at - datetime.fromisoformat(started_at)).total_seconds()


def _run_metrics(tenants: list[dict], duration_sec: float) -> dict:
    """RUN の `metrics`（設計 7.1）。export の実績はテナント分を足し合わせる。"""
    return {
        "duration_sec": round(duration_sec, 3),
        "export_calls": sum(tenant["export_calls"] for tenant in tenants),
        "export_bytes": sum(tenant["export_bytes"] for tenant in tenants),
    }


def _failed_tenants(tenants: list[dict]) -> list[str]:
    return [f"{tenant['tenant_cd']} {tenant['tenant_name']}" for tenant in tenants if tenant["status"] == FAILED]


def _slack(settings: dict, result: dict, closing: ClosingPeriod, portal_url: str) -> Slack:
    """メンションは本文に含めず `mention` で渡す（notify 側が本文先頭に付ける。設計 6.8）。"""
    text = messages.slack_text(
        settings["slack_template"],
        month=result["month"],
        period=closing,
        summary=result["summary"],
        portal_url=portal_url,
        target_env=result["target_env"],
        failed_tenant_count=result["summary"]["failed_tenant_count"],
    )
    return Slack(text=text, channel=settings["slack_channel_id"], mention=settings["slack_mention"] or None)


def _email(settings: dict, result: dict, closing: ClosingPeriod, portal_url: str) -> Email:
    return Email(
        to=settings["mail_to"],
        cc=settings["mail_cc"],
        subject=messages.mail_subject(
            result["month"], closing.cutoff_day, result["target_env"], result["run"]["attempt"]
        ),
        text=messages.mail_body(
            month=result["month"],
            period=closing,
            summary=result["summary"],
            price_effective_from=result["price_table"]["effective_from"],
            portal_url=portal_url,
            target_env=result["target_env"],
            failed_tenants=_failed_tenants(result["tenants"]),
        ),
    )


def _send_report(settings: dict, result: dict, closing: ClosingPeriod, xlsx_key: str, channels: list[str]) -> str:
    """成功通知を 1 件投函して `message_id` を返す（設計 6.8）。"""
    month, target_env = result["month"], result["target_env"]
    attempt = result["run"]["attempt"]
    portal_url = messages.portal_url(os.environ["ENV"], target_env, month)
    attachment = Attachment(
        key=xlsx_key,
        filename=messages.attachment_filename(month, target_env),
        content_type=storage.XLSX_CONTENT_TYPE,
    )
    return send(
        app=APP,
        channels=channels,
        email=_email(settings, result, closing, portal_url) if distribution.EMAIL_CHANNEL in channels else None,
        attachments=[attachment],
        slack=_slack(settings, result, closing, portal_url) if distribution.SLACK_CHANNEL in channels else None,
        meta={"target_env": target_env, "month": month, "attempt": attempt},
    )


def _distribute(event: dict, settings: dict, result: dict, closing: ClosingPeriod, xlsx_key: str) -> bool:
    """通知を投函して RUN を `distributed` にする。投函の失敗は `succeeded` のまま記録する（設計 11.1）。"""
    target_env, month, attempt = event["target_env"], event["month"], event["attempt"]
    channels = distribution.channels(settings)
    if not channels:
        logger.info(NO_DISTRIBUTION_TARGET, {"target_env": target_env, "month": month})
        repo.set_distribute_note(target_env, month, attempt, NO_DISTRIBUTION_TARGET)
        return False
    try:
        message_id = _send_report(settings, result, closing, xlsx_key, channels)
    except Exception as error:
        logger.error("通知の投函に失敗しました", {"target_env": target_env, "month": month, "error": str(error)})
        repo.set_notify_error(target_env, month, attempt, str(error))
        return False
    repo.finish_distributed(
        target_env,
        month,
        attempt,
        notify_message_id=message_id,
        notify_channels=channels,
        distribute_note="",
    )
    return True


def _emit_metrics(summary: dict, duration_sec: float) -> None:
    """設計 14.2 の実行実績。"""
    metrics.add_metric(name="RunDuration", unit=MetricUnit.Seconds, value=duration_sec)
    metrics.add_metric(name="TenantCount", unit=MetricUnit.Count, value=summary["tenant_count"])
    metrics.add_metric(name="TenantFailed", unit=MetricUnit.Count, value=summary["failed_tenant_count"])
    metrics.add_metric(name="MissingDevices", unit=MetricUnit.Count, value=summary["missing_count"])
    metrics.add_metric(name="TotalAmount", unit=MetricUnit.NoUnit, value=summary["total_amount"])


@metrics.log_metrics
@LambdaLogger.contextualize
def handler(event: dict, context) -> dict:
    target_env = event["target_env"]
    month = event["month"]
    attempt = event["attempt"]
    run = repo.get_run(target_env, month) or {}
    settings = repo.get_settings()
    price_item = _price_item(event)
    closing = _closing_period(event)
    tenants = _collect_tenants(event, settings, PriceTable.from_item(price_item), closing)
    device_cds = {device.device_cd for tenant in tenants for device in tenant["devices"]}
    failed_tenant_cds = frozenset(tenant["tenant_cd"] for tenant in tenants if tenant["status"] == FAILED)
    missing = billing.missing_devices(_previous_result(target_env, month), device_cds, failed_tenant_cds)
    summary = billing.summarize(tenants, missing)
    finished_at = _now()
    result = {
        "schema": RESULT_SCHEMA,
        "target_env": target_env,
        "month": month,
        "closing_period": event["closing_period"],
        "run": _run_snapshot(run, event, finished_at),
        "settings_snapshot": _settings_snapshot(settings),
        "price_table": _price_snapshot(price_item),
        "summary": summary,
        "tenants": [{**tenant, "devices": [asdict(device) for device in tenant["devices"]]} for tenant in tenants],
        "missing_devices": missing,
    }
    result_key = storage.put_result(target_env, month, result)
    xlsx_key = storage.put_xlsx(target_env, month, excel.build_workbook(result))
    duration_sec = _duration_sec(run, finished_at)
    repo.finish_success(
        target_env,
        month,
        attempt,
        summary=summary,
        s3_xlsx_key=xlsx_key,
        s3_result_key=result_key,
        closing_period=event["closing_period"],
        price_effective_from=price_item["effective_from"],
        metrics=_run_metrics(tenants, duration_sec),
    )
    distributed = _distribute(event, settings, result, closing, xlsx_key) if event["distribute"] else False
    _emit_metrics(summary, duration_sec)
    logger.info(
        "集計を終えました",
        {
            "run_id": event["run_id"],
            "target_env": target_env,
            "month": month,
            "summary": summary,
            "distributed": distributed,
            "duration_sec": round(duration_sec, 3),
        },
    )
    return {
        "run_id": event["run_id"],
        "month": month,
        "target_env": target_env,
        "summary": summary,
        "s3_xlsx_key": xlsx_key,
        "s3_result_key": result_key,
        "distributed": distributed,
    }
