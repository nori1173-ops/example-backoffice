"""設計 8 章の設定・レポート一覧・Slack 候補 API。"""

import json
import os
import re
import uuid
from datetime import date as calendar_date
from datetime import datetime, timedelta

import boto3
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from botocore.exceptions import ClientError
from daily_security_report import glossary, log_queries, prompt_text, repo, settings, slack_directory

from example_backoffice import auth, origin_verify, response
from example_backoffice.logging import LambdaLogger, get_logger

app = APIGatewayHttpResolver(strip_prefixes=["/api/daily-security-report"])
response.install_error_handlers(app)
_ssm_cache = None
RUN_MAX_AGE_DAYS = 400
LOGS_MAX_AGE_DAYS = 90
LOGS_MAX_RUNNING = 3
LOG_VIEW_FIELDS = ("query_id", "status", "kind", "target", "requested_at", "finished_at", "result", "error")


def _now() -> datetime:
    return datetime.now(repo.JST)


def reset_clients() -> None:
    global _ssm_cache
    _ssm_cache = None


def _claims(*, admin: bool = False) -> auth.Claims:
    claims = auth.get_claims(app.current_event.raw_event)
    if claims.raw.get("token_use") != "id":
        raise auth.UnauthorizedError("ID トークンで呼び出してください")
    if admin:
        auth.require_group(claims, "admin")
    return claims


def _slack_mode() -> str | None:
    global _ssm_cache
    if _ssm_cache is None:
        _ssm_cache = boto3.client("ssm")
    try:
        return _ssm_cache.get_parameter(Name=f"/example-backoffice/{os.environ.get('ENV', 'dev')}/shared/slack/mode")[
            "Parameter"
        ]["Value"]
    except ClientError as error:
        if error.response["Error"]["Code"] != "ParameterNotFound":
            raise
        return None


@app.get("/health")
def health() -> dict:
    _claims()
    return response.ok({"status": "ok"})


@app.get("/settings")
def get_settings() -> dict:
    _claims()
    return response.ok(
        {
            **repo.get_settings(),
            "ai_prompt_default": prompt_text.DEFAULT_PROMPT_BODY,
            "ai_prompt_fixed": prompt_text.FIXED_PARTS,
            "target_account": os.environ["ACCOUNT_ID"],
            "slack_mode": _slack_mode(),
        }
    )


@app.put("/settings")
def put_settings() -> dict:
    claims = _claims(admin=True)
    try:
        patch = app.current_event.json_body
    except (ValueError, TypeError) as error:
        raise settings.SettingsValidationError("本文は JSON オブジェクトで指定してください") from error
    current = repo.get_settings(seed=False)
    updated = settings.update_settings(current, patch)
    return response.ok(repo.put_settings(updated, updated["version"], claims.sub, claims.email))


@app.get("/slack/directory")
def get_slack_directory():
    _claims(admin=True)
    refresh = app.current_event.get_query_string_value("refresh") == "1"
    try:
        return response.ok(slack_directory.get_directory(refresh=refresh))
    except slack_directory.SlackNotConfiguredError as error:
        return response.error("SLACK_NOT_CONFIGURED", str(error), 503)
    except slack_directory.SlackApiError as error:
        return response.error("SLACK_API_ERROR", str(error), 502)
    except slack_directory.SlackUnavailableError as error:
        return response.error("SLACK_UNAVAILABLE", str(error), 502)


@app.get("/days")
def list_days() -> dict:
    _claims()
    raw = app.current_event.get_query_string_value("limit", "30")
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= 90:
        raise settings.SettingsValidationError("limit は 1〜90 の整数で指定してください")
    return response.ok(repo.list_days(int(raw)))


@app.get("/days/<date>")
def get_daily(date: str) -> dict:
    _claims()
    try:
        if calendar_date.fromisoformat(date).isoformat() != date:
            raise ValueError("invalid date format")
    except ValueError as error:
        raise settings.SettingsValidationError("date は実在する YYYY-MM-DD で指定してください") from error
    item = repo.get_daily(date)
    if item is None:
        return response.error("NOT_FOUND", "指定日の集計がありません", 404)
    summary = item.get("summary") or {}
    buckets = [row["key"] for row in summary.get("top", {}).get("s3_buckets", []) if "key" in row]
    keys = dict.fromkeys([*summary.get("counters", {}), *summary.get("comparisons", {}), *buckets])
    item["glossary"] = glossary.glossary_for(keys, repo.get_settings(seed=False)) if keys else {}
    return response.ok(item)


def _run_date(date: str, max_age_days: int = RUN_MAX_AGE_DAYS) -> None:
    try:
        target = calendar_date.fromisoformat(date)
        if target.isoformat() != date:
            raise ValueError("invalid date format")
    except ValueError as error:
        raise settings.SettingsValidationError("date は実在する YYYY-MM-DD で指定してください") from error
    today = _now().date()
    if not today - timedelta(days=max_age_days) <= target <= today:
        raise settings.SettingsValidationError(f"date は当日から {max_age_days} 日前までの日付で指定してください")


def _require_empty_body() -> None:
    raw = app.current_event.decoded_body
    if not raw or not raw.strip():
        return
    try:
        if json.loads(raw) == {}:
            return
    except ValueError:
        pass
    raise settings.SettingsValidationError("本文は空か {} で指定してください")


@app.post("/days/<date>/run")
def run_daily(date: str):
    claims = _claims(admin=True)
    _run_date(date)
    _require_empty_body()
    now = _now()
    daily = repo.get_daily(date)
    if daily and daily.get("status") == "running" and not repo.is_stale(daily, now):
        return response.error("RUN_IN_PROGRESS", "同じ日を実行中です。完了してから実行してください", 409)
    requested_by = claims.email or claims.sub
    log = {"date": date, "requested_by": requested_by, "requested_by_sub": claims.sub}
    payload = {"trigger": "manual", "date": date, "force": True, "requested_by": requested_by}
    try:
        result = _lambda_client().invoke(
            FunctionName=os.environ["REPORT_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        if result.get("StatusCode") != 202:
            raise RuntimeError("Invoke not accepted")
    except Exception as error:
        get_logger().warning(
            "レポートの今すぐ実行を開始できませんでした", {**log, "exception_type": type(error).__name__}
        )
        return response.error("RUN_UNAVAILABLE", "実行を開始できませんでした。再試行してください", 502)
    get_logger().info("レポートの今すぐ実行を受け付けました", log)
    return response.json_response(response.ok({"date": date, "accepted_at": now.isoformat()}), 202)


def _lambda_client():
    from botocore.config import Config

    return boto3.client("lambda", config=Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}))


def _preview_request() -> dict:
    try:
        body = app.current_event.json_body
    except (ValueError, TypeError) as error:
        raise settings.SettingsValidationError("本文は JSON オブジェクトで指定してください") from error
    if not isinstance(body, dict) or set(body) - {"date", "prompt", "model_family", "model_pin"}:
        raise settings.SettingsValidationError("試行の本文または項目が不正です")
    target_date = body.get("date")
    try:
        if not isinstance(target_date, str) or calendar_date.fromisoformat(target_date).isoformat() != target_date:
            raise ValueError("invalid date")
    except ValueError as error:
        raise settings.SettingsValidationError("date は実在する YYYY-MM-DD で指定してください") from error
    current = repo.get_settings(seed=False)
    overrides = {f"ai_{key}": value for key, value in body.items() if key != "date"}
    merged = {**current, **overrides}
    settings.validate_settings(merged)
    return {
        "date": target_date,
        "prompt": merged["ai_prompt"],
        "model_family": merged["ai_model_family"],
        "model_pin": merged["ai_model_pin"],
    }


@app.post("/ai/preview")
def create_ai_preview():
    claims = _claims(admin=True)
    values = _preview_request()
    daily = repo.get_daily(values["date"])
    if daily is None:
        return response.error("NOT_FOUND", "指定日の集計がありません", 404)
    if not daily.get("summary"):
        return response.error("VERSION_CONFLICT", "集計完了後に試してください", 409)
    preview_id = str(uuid.uuid4())
    try:
        repo.create_preview({**values, "preview_id": preview_id, "requested_by": claims.sub})
    except repo.PreviewLimitError as error:
        return response.error("PREVIEW_LIMIT", str(error), 429)
    except repo.PreviewBusyError as error:
        return response.error("AI_UNAVAILABLE", str(error), 502)
    try:
        result = _lambda_client().invoke(
            FunctionName=os.environ["REPORT_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=json.dumps({"action": "ai_preview", "preview_id": preview_id}).encode("utf-8"),
        )
        if result.get("StatusCode") != 202:
            raise RuntimeError("Invoke not accepted")
    except Exception:
        message = "AI の試行を開始できませんでした。再試行してください"
        repo.update_preview(
            preview_id,
            {
                "status": "failed",
                "error": {
                    "code": "AI_UNAVAILABLE",
                    "http_status": 502,
                    "message": message,
                },
            },
        )
        return response.error("AI_UNAVAILABLE", message, 502)
    return response.json_response(response.ok({"preview_id": preview_id}), 202)


@app.get("/ai/preview/<preview_id>")
def get_ai_preview(preview_id: str):
    _claims(admin=True)
    item = repo.get_preview(preview_id)
    if item is None:
        return response.error("NOT_FOUND", "指定の試行がありません", 404)
    return response.ok(item)


def _logs_request(date: str) -> tuple[str, dict]:
    """本文は保存済みの ref そのもの（kind がトップレベルにある平らな形）。{kind, target} の入れ子も受ける。"""
    try:
        body = app.current_event.json_body
    except (ValueError, TypeError) as error:
        raise settings.SettingsValidationError("本文は JSON オブジェクトで指定してください") from error
    if not isinstance(body, dict) or "kind" not in body:
        raise settings.SettingsValidationError("本文は ref（kind と問い合わせ先）で指定してください", "INVALID_QUERY")
    kind = body["kind"]
    if isinstance(body.get("target"), dict):
        if set(body) != {"kind", "target"}:
            raise settings.SettingsValidationError("target とほかの項目は同時に指定できません", "INVALID_QUERY")
        target = body["target"]
    else:
        target = {key: value for key, value in body.items() if key != "kind"}
    try:
        target = log_queries.normalize_target(kind, target)
    except ValueError as error:
        raise settings.SettingsValidationError(str(error), "INVALID_QUERY") from error
    if kind != "cloudtrail":
        summary = (repo.get_daily(date) or {}).get("summary") or {}
        if kind == "metric":
            recorded = {*summary.get("counters", {}), *summary.get("comparisons", {})}
        else:
            recorded = log_queries.recorded_targets(summary)[kind]
        if target[log_queries.RECORD_FIELDS.get(kind, "key")] not in recorded:
            raise settings.SettingsValidationError("指定日の記録にありません", "INVALID_QUERY")
    return kind, target


def _log_view(item: dict) -> dict:
    return {key: item[key] for key in LOG_VIEW_FIELDS if key in item}


@app.post("/days/<date>/logs")
def start_logs(date: str):
    claims = _claims(admin=True)
    _run_date(date, LOGS_MAX_AGE_DAYS)
    kind, target = _logs_request(date)
    query_id = log_queries.query_id(date, kind, target)
    requested_by = claims.email or claims.sub
    log = {"date": date, "query_id": query_id, "kind": kind, "requested_by": requested_by}
    existing = repo.get_log_query(date, query_id)
    if existing and existing.get("status") == "succeeded":
        return response.ok(_log_view(existing))
    if existing and existing.get("status") == "running":
        return response.json_response(response.ok(_log_view(existing)), 202)
    if repo.count_running_log_queries(requested_by) >= LOGS_MAX_RUNNING:
        return response.error(
            "TOO_MANY_QUERIES", f"同時に見られるのは {LOGS_MAX_RUNNING} 件までです。完了してから再試行してください", 429
        )
    item = repo.put_log_query(
        date, query_id, {"status": "running", "kind": kind, "target": target, "requested_by": requested_by}
    )
    payload = {"trigger": "logs", "date": date, "query_id": query_id, "kind": kind, "target": target}
    payload["requested_by"] = requested_by
    try:
        result = _lambda_client().invoke(
            FunctionName=os.environ["REPORT_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if result.get("StatusCode") != 202:
            raise RuntimeError("Invoke not accepted")
    except Exception as error:
        get_logger().warning("ログの問い合わせを開始できませんでした", {**log, "exception_type": type(error).__name__})
        repo.update_log_query(
            date, query_id, {"status": "failed", "error": "LOGS_UNAVAILABLE", "finished_at": _now().isoformat()}
        )
        return response.error("LOGS_UNAVAILABLE", "ログの問い合わせを開始できませんでした。再試行してください", 502)
    get_logger().info("ログの問い合わせを受け付けました", log)
    return response.json_response(response.ok(_log_view(item)), 202)


@app.get("/days/<date>/logs/<query_id>")
def get_logs(date: str, query_id: str):
    _claims(admin=True)
    try:
        if calendar_date.fromisoformat(date).isoformat() != date:
            raise ValueError("invalid date format")
    except ValueError as error:
        raise settings.SettingsValidationError("date は実在する YYYY-MM-DD で指定してください") from error
    item = repo.get_log_query(date, query_id) if re.fullmatch(r"[0-9a-f]{16}", query_id) else None
    if item is None:
        return response.error("NOT_FOUND", "指定の問い合わせがありません", 404)
    return response.ok(_log_view(item))


@app.exception_handler(settings.SettingsValidationError)
def validation_error(error):
    return response.error(error.code, error.message, 400)


@app.exception_handler(repo.VersionConflictError)
def version_conflict(error):
    return response.error("VERSION_CONFLICT", str(error), 409)


@LambdaLogger.contextualize
@origin_verify.origin_verify
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
