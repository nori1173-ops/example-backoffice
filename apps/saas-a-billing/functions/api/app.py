"""SaaS_A 月額課金レポートの HTTP API ハンドラ（設計 8 章 / 9 章 / 12.1）。

ルートは `/api/saas-a-billing` を剥がした形で書く。接頭辞は CloudFront ではなく
API 側で受けるため、CloudFront 経由でも `execute-api` 直叩きでもパスは同じになる
（設計 5.3）。`admin` 専用の `PUT /settings` / `POST /prices` / `GET /audits` は
`auth.require_group` で保護し、閲覧と実行は認証済みの全ユーザーに開ける（設計 12.1）。

秘密は応答にもログにも出さない。集計対象環境の接続情報 3 件は「登録されているか」
だけを `get_parameters_by_path` の**名前**から判定し、値は読まない。値を読むのは
`base-url` だけで、応答にはそのホスト名しか載せない（設計 8 章の `saasa_host`）。
RUN の応答も `_run_view` が `REDACTED_RUN_ATTRIBUTES` を落としてから返す。実行中の
RUN は生きた SaaS_A の Bearer トークン（`api_token`）を持っているため。

想定外の例外にはハンドラを登録しない。握り潰さず Lambda のエラーとして上げ、
Errors メトリクスとアラームに載せる（shared の `response.install_error_handlers`
の方針）。応答は API Gateway の汎用 500 になるため、スタックトレースは漏れない。
"""

import json
import os
import re
import uuid
from datetime import date, datetime
from urllib.parse import urlparse

import boto3
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from botocore.exceptions import ClientError
from saas_a_billing import execution, messages, period, repo, schedule, slack_directory, storage
from saas_a_billing.pricing import PriceTableNotFoundError

from example_backoffice import auth, mail, origin_verify, response
from example_backoffice.logging import LambdaLogger, get_logger

API_PREFIX = "/api/saas-a-billing"
TRIGGER = "manual"
TIMEOUT_ERROR = "TIMEOUT(推定)"

CREDENTIAL_LEAVES = ("base-url", "user-id", "password")
BASE_URL_LEAF = "base-url"
SLACK_MODE_PARAMETER = "/example-backoffice/{env}/shared/slack/mode"
PARAMETER_NOT_FOUND = "ParameterNotFound"

VALIDATION_ERROR = "VALIDATION_ERROR"
TARGET_ENV_NOT_CONFIGURED = "TARGET_ENV_NOT_CONFIGURED"
TARGET_ENV_FIXED = "TARGET_ENV_FIXED"
MAIL_DOMAIN_NOT_ALLOWED = "MAIL_DOMAIN_NOT_ALLOWED"
VERSION_CONFLICT = "VERSION_CONFLICT"
ALREADY_EXISTS = "ALREADY_EXISTS"
MONTH_NOT_STARTED = "MONTH_NOT_STARTED"
NOT_FOUND = "NOT_FOUND"
OTHER_RUN_RUNNING = "OTHER_RUN_RUNNING"
SCHEDULE_IMMINENT = "SCHEDULE_IMMINENT"
START_EXECUTION_FAILED = "START_EXECUTION_FAILED"
SLACK_NOT_CONFIGURED = "SLACK_NOT_CONFIGURED"
SLACK_API_ERROR = "SLACK_API_ERROR"
SLACK_UNAVAILABLE = "SLACK_UNAVAILABLE"

EDITABLE_SETTINGS = (
    "target_env",
    "cutoff_day",
    "cutoff_time",
    "auto_run_enabled",
    "auto_run_from_month",
    "mail_enabled",
    "slack_enabled",
    "mail_to",
    "mail_cc",
    "mail_attachment_sheets",
    "slack_channel_id",
    "slack_channel_label",
    "slack_mention",
    "slack_template",
    "exclude_keywords",
    "camera_format_names",
)
REDACTED_RUN_ATTRIBUTES = (*repo.TOKEN_ATTRIBUTES, "run_token")
LIST_REDACTED_RUN_ATTRIBUTES = (*REDACTED_RUN_ATTRIBUTES, repo.REQUEST_HISTORY)
STRING_LIST_SETTINGS = ("exclude_keywords", "camera_format_names")
TEXT_SETTINGS = ("slack_channel_label", "slack_mention")
BOOL_SETTINGS = ("auto_run_enabled", "mail_enabled", "slack_enabled")
BAND_FIELDS = ("max_items", "price_3y", "price_1y")
CAMERA_FIELDS = ("price_3y", "price_1y")

CUTOFF_DAY_MIN, CUTOFF_DAY_MAX = 1, 28
RUN_LIST_LIMIT_MAX = 100
AUDIT_LIMIT_MIN = 1
END_OF_DAY = "24:00"
TARGET_ENV_LABEL = " / ".join(repo.TARGET_ENVS)
AUDIT_TARGET_LABEL = " / ".join(repo.AUDIT_TARGETS)

_MONTH_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_SLACK_CHANNEL_PATTERN = re.compile(r"^[CG][A-Za-z0-9]+$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logger = get_logger()
app = APIGatewayHttpResolver(strip_prefixes=[API_PREFIX])
response.install_error_handlers(app)

_ssm_cache = None
_states_cache = None


class ValidationError(Exception):
    """入力が設計 8 章の検証を満たさない（400）。"""

    def __init__(self, message: str, code: str = VALIDATION_ERROR):
        super().__init__(message)
        self.code = code


def reset_clients() -> None:
    """boto3 クライアントのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _ssm_cache, _states_cache
    _ssm_cache = None
    _states_cache = None
    slack_directory.reset_clients()


def _ssm():
    global _ssm_cache
    if _ssm_cache is None:
        _ssm_cache = boto3.client("ssm")
    return _ssm_cache


def _states():
    global _states_cache
    if _states_cache is None:
        _states_cache = boto3.client("stepfunctions")
    return _states_cache


def _now() -> datetime:
    return datetime.now(period.JST)


def _target_path(target_env: str) -> str:
    return f"{os.environ['SSM_PREFIX'].rstrip('/')}/targets/{target_env}"


def _parameter_value(name: str, *, decrypt: bool = False) -> str | None:
    """SSM パラメータの値。未登録なら `None`。"""
    try:
        parameter = _ssm().get_parameter(Name=name, WithDecryption=decrypt)
    except ClientError as error:
        if error.response["Error"]["Code"] != PARAMETER_NOT_FOUND:
            raise
        return None
    return parameter["Parameter"]["Value"]


def _is_target_configured(target_env: str) -> bool:
    """接続情報 3 件が SSM に揃っているか。名前だけを見て値は読まない。"""
    paginator = _ssm().get_paginator("get_parameters_by_path")
    registered = {
        parameter["Name"].rsplit("/", 1)[-1]
        for page in paginator.paginate(Path=_target_path(target_env), WithDecryption=False)
        for parameter in page["Parameters"]
    }
    return set(CREDENTIAL_LEAVES) <= registered


def _saasa_host(target_env: str) -> str | None:
    """接続先のホスト名。`base-url` 以外の秘密は読まない。"""
    base_url = _parameter_value(f"{_target_path(target_env)}/{BASE_URL_LEAF}", decrypt=True)
    return None if base_url is None else urlparse(base_url).hostname


def _slack_mode() -> str | None:
    """共通の Slack 動作方式（`bot` / `webhook`）。秘密ではない。"""
    return _parameter_value(SLACK_MODE_PARAMETER.format(env=os.environ.get("ENV", "dev")))


def _require(condition: bool, message: str, code: str = VALIDATION_ERROR) -> None:
    if not condition:
        raise ValidationError(message, code)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_month(value) -> bool:
    return isinstance(value, str) and bool(_MONTH_PATTERN.match(value))


def _is_effective_from(value) -> bool:
    if not isinstance(value, str) or not _DATE_PATTERN.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _claims() -> auth.Claims:
    return auth.get_claims(app.current_event.raw_event)


def _admin_claims() -> auth.Claims:
    claims = _claims()
    auth.require_group(claims, auth.ADMIN_GROUP)
    return claims


def _body() -> dict:
    """リクエスト本文の JSON オブジェクト。"""
    raw = app.current_event.body
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError as error:
        raise ValidationError("リクエスト本文が JSON ではありません") from error
    _require(isinstance(parsed, dict), "リクエスト本文は JSON オブジェクトで指定してください")
    return parsed


def _query_target_env() -> str | None:
    value = app.current_event.get_query_string_value("target_env")
    if not value:
        return None
    _require(value in repo.TARGET_ENVS, f"target_env は {TARGET_ENV_LABEL} のいずれかで指定してください")
    return value


def _target_env_or_default() -> str:
    return _query_target_env() or repo.target_env()


def _require_fixed_target_env(value) -> None:
    """集計対象環境はサイト固定（計画 R1 / SP-01）。固定値以外は受け付けない。"""
    fixed = repo.target_env()
    _require(value == fixed, f"集計対象環境は {fixed} に固定されています", TARGET_ENV_FIXED)
    _require(
        _is_target_configured(fixed),
        f"{fixed} の接続情報が SSM に登録されていません",
        TARGET_ENV_NOT_CONFIGURED,
    )


def _query_limit(default: int) -> int:
    """`limit` の指定値。`RUN_LIST_LIMIT_MAX` 超過は丸める（拒否はしない）。"""
    value = app.current_event.get_query_string_value("limit")
    if not value:
        return default
    _require(value.isdecimal() and int(value) > 0, "limit は 1 以上の整数で指定してください")
    return min(int(value), RUN_LIST_LIMIT_MAX)


def _query_audit_limit() -> int:
    """`GET /audits` の `limit`。丸めずに範囲外を断る（見えていない件数を黙って変えない）。"""
    value = app.current_event.get_query_string_value("limit")
    if not value:
        return repo.AUDIT_LIST_LIMIT
    _require(
        value.isdecimal() and AUDIT_LIMIT_MIN <= int(value) <= repo.AUDIT_LIST_MAX,
        f"limit は {AUDIT_LIMIT_MIN}〜{repo.AUDIT_LIST_MAX} の整数で指定してください",
    )
    return int(value)


def _query_audit_target() -> str | None:
    value = app.current_event.get_query_string_value("target")
    if not value:
        return None
    _require(value in repo.AUDIT_TARGETS, f"target は {AUDIT_TARGET_LABEL} のいずれかで指定してください")
    return value


def _query_since() -> str | None:
    """`GET /audits` の `since`。`Z` でもオフセット付きでも受け、正規化は repo に任せる。"""
    value = app.current_event.get_query_string_value("since")
    if not value:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise ValidationError("since は ISO 8601 形式で指定してください") from error
    return value


def _query_detail() -> bool:
    """`?detail=true` のときだけ `result.json` を読む。"""
    value = app.current_event.get_query_string_value("detail")
    return bool(value) and value.lower() not in ("false", "0")


def _query_refresh() -> bool:
    """`?refresh=1` のときだけ Slack の候補キャッシュを捨てて取り直す。"""
    value = app.current_event.get_query_string_value("refresh")
    return bool(value) and value.lower() not in ("false", "0")


def _run_view(run: dict, now: datetime, redacted: tuple[str, ...] = REDACTED_RUN_ATTRIBUTES) -> dict:
    """応答用の RUN。機微・内部の属性を落とし、stale な queued / running は失敗として見せる。

    実行中の RUN が持つ `api_token` は生きた SaaS_A の Bearer トークンなので、
    認証済みユーザーにも渡さない（設計 12.1）。一覧では `request_history` も落とす
    （画面で使わず、最大 20 件 × 実行者で応答が膨らむだけ。詳細では返す）。
    """
    view = {name: value for name, value in run.items() if name not in redacted}
    if not repo.is_stale(run, now):
        return view
    return {**view, "status": repo.FAILED, "error": TIMEOUT_ERROR}


def _bool_field(payload: dict, name: str, default: bool) -> bool:
    """真偽値のフィールド。文字列 `"false"` のような truthy な値を受け入れない。"""
    value = payload.get(name, default)
    _require(isinstance(value, bool), f"{name} は真偽値で指定してください")
    return value


def _validate_string_list(values, name: str) -> None:
    _require(isinstance(values, list), f"{name} は文字列の配列で指定してください")
    for item in values:
        _require(isinstance(item, str), f"{name} は文字列の配列で指定してください")


def _validate_emails(values, name: str, *, required: bool) -> None:
    """宛先の形式と許可ドメインを検証する。ドメイン判定は notifier と同じ規則（SEC-07）。"""
    _require(isinstance(values, list), f"{name} は配列で指定してください")
    _require(not required or bool(values), f"{name} は 1 件以上指定してください")
    for address in values:
        _require(
            isinstance(address, str) and bool(_EMAIL_PATTERN.match(address)),
            f"{name} にメール形式でない宛先があります",
        )
        _require(
            mail.is_allowed_address(address),
            f"{name} の {address} は社外ドメインのため指定できません"
            f"（宛先は {mail.allowed_mail_domains_label()} のみ）",
            MAIL_DOMAIN_NOT_ALLOWED,
        )


def _validate_slack_channel(values: dict) -> None:
    """Slack 配布が ON のときだけチャンネル ID を必須にする。非空なら常に形式を検証する。"""
    channel_id = values.get("slack_channel_id")
    _require(isinstance(channel_id, str), "slack_channel_id は文字列で指定してください")
    _require(
        not values.get("slack_enabled") or bool(channel_id),
        "Slack 配布を有効にするには slack_channel_id が必要です",
    )
    _require(
        not channel_id or bool(_SLACK_CHANNEL_PATTERN.match(channel_id)),
        "slack_channel_id は C または G で始まる英数字で指定してください",
    )


def _validate_mail_to(values: dict) -> None:
    """メール配布が ON のときだけ宛先を必須にする。項目の形式検証は常に行う。"""
    mail_to = values.get("mail_to")
    _validate_emails(mail_to, "mail_to", required=False)
    _require(
        not values.get("mail_enabled") or bool(mail_to),
        "メール配布を有効にするには mail_to が必要です",
    )


def _validate_settings(values: dict) -> None:
    """設計 8 章の `PUT /settings` の検証。更新後の設定全体に対して行う。"""
    _require_fixed_target_env(values.get("target_env"))
    cutoff_day = values.get("cutoff_day")
    _require(
        _is_int(cutoff_day) and CUTOFF_DAY_MIN <= cutoff_day <= CUTOFF_DAY_MAX,
        f"cutoff_day は {CUTOFF_DAY_MIN}〜{CUTOFF_DAY_MAX} の整数で指定してください",
    )
    cutoff_time = values.get("cutoff_time")
    _require(
        cutoff_time == END_OF_DAY or (isinstance(cutoff_time, str) and bool(_TIME_PATTERN.match(cutoff_time))),
        f"cutoff_time は HH:MM 形式で指定してください（{END_OF_DAY} を含む）",
    )
    for name in BOOL_SETTINGS:
        _require(isinstance(values.get(name), bool), f"{name} は真偽値で指定してください")
    _require(_is_month(values.get("auto_run_from_month")), "auto_run_from_month は YYYY-MM 形式で指定してください")
    _validate_slack_channel(values)
    _validate_mail_to(values)
    _validate_emails(values.get("mail_cc"), "mail_cc", required=False)
    _require(
        isinstance(values.get("mail_attachment_sheets"), str)
        and values["mail_attachment_sheets"] in ("billing_only", "all"),
        "mail_attachment_sheets は billing_only / all で指定してください",
    )
    for name in STRING_LIST_SETTINGS:
        _validate_string_list(values.get(name), name)
    for name in TEXT_SETTINGS:
        _require(isinstance(values.get(name), str), f"{name} は文字列で指定してください")
    try:
        messages.validate_slack_template(values.get("slack_template") or "")
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _non_negative_int(values, name: str, label: str) -> int:
    value = values.get(name) if isinstance(values, dict) else None
    _require(_is_int(value) and value >= 0, f"{label} は 0 以上の整数で指定してください")
    return value


def _price_item(payload: dict) -> dict:
    """設計 8 章の `POST /prices` を検証し、保存する属性だけを取り出す。"""
    effective_from = payload.get("effective_from")
    _require(_is_effective_from(effective_from), "effective_from は YYYY-MM-DD 形式で指定してください")
    bands = payload.get("bands")
    _require(isinstance(bands, list) and bool(bands), "bands は 1 件以上の配列で指定してください")
    previous = -1
    for index, band in enumerate(bands, start=1):
        for name in BAND_FIELDS:
            _non_negative_int(band, name, f"bands[{index}].{name}")
        _require(band["max_items"] > previous, "bands は max_items の昇順で重複なく指定してください")
        previous = band["max_items"]
    for name in CAMERA_FIELDS:
        _non_negative_int(payload.get("camera"), name, f"camera.{name}")
    return {
        "effective_from": effective_from,
        "bands": [{name: band[name] for name in BAND_FIELDS} for band in bands],
        "camera": {name: payload["camera"][name] for name in CAMERA_FIELDS},
        "note": str(payload.get("note") or ""),
    }


@app.get("/health")
def health() -> dict:
    logger.debug("health", {"path": app.current_event.path})
    return response.ok({"status": "ok"})


@app.get("/settings")
def get_settings() -> dict:
    _claims()
    settings = repo.get_settings()
    target_env = settings["target_env"]
    return response.ok(
        {
            **settings,
            "saasa_host": _saasa_host(target_env),
            "targets": {target_env: _is_target_configured(target_env)},
            "slack_mode": _slack_mode(),
        }
    )


@app.put("/settings")
def put_settings() -> dict:
    claims = _admin_claims()
    payload = _body()
    version = payload.get("version")
    _require(_is_int(version), "version は整数で指定してください")
    current = repo.get_settings()
    updated = {**current, **{name: payload[name] for name in EDITABLE_SETTINGS if name in payload}}
    _validate_settings(updated)
    return response.ok(repo.put_settings(updated, version, claims.sub, claims.email))


# 設定画面の `#` / `@` 候補だけに使う読み取り（設計 8 章 / #1011）。監査には残さない。
@app.get("/slack/directory")
def get_slack_directory() -> dict | Response:
    _admin_claims()
    try:
        return response.ok(slack_directory.get_directory(refresh=_query_refresh()))
    except slack_directory.SlackNotConfiguredError as error:
        return response.error(SLACK_NOT_CONFIGURED, str(error), 503)
    except slack_directory.SlackApiError as error:
        return response.error(SLACK_API_ERROR, str(error), 502)
    except slack_directory.SlackUnavailableError as error:
        return response.error(SLACK_UNAVAILABLE, str(error), 502)


@app.get("/prices")
def get_prices() -> dict:
    _claims()
    try:
        current = repo.get_price_table_at(_now().date())
    except PriceTableNotFoundError:
        current = None
    return response.ok({"versions": repo.list_prices(), "current": current})


@app.post("/prices")
def post_prices() -> dict | Response:
    claims = _admin_claims()
    item = _price_item(_body())
    try:
        return response.ok(repo.put_price(item, claims.sub, claims.email))
    except repo.AlreadyExistsError as error:
        return response.error(ALREADY_EXISTS, str(error), 409)


@app.get("/runs")
def get_runs() -> dict:
    _claims()
    runs = repo.list_runs(_target_env_or_default(), _query_limit(repo.RUN_LIST_LIMIT))
    now = _now()
    return response.ok([_run_view(run, now, LIST_REDACTED_RUN_ATTRIBUTES) for run in runs])


@app.get("/runs/<month>")
def get_run(month: str) -> dict | Response:
    _claims()
    _require(_is_month(month), "month は YYYY-MM 形式で指定してください")
    target_env = _target_env_or_default()
    run = repo.get_run(target_env, month)
    if run is None:
        return response.error(NOT_FOUND, f"{target_env} の {month} の実行はありません", 404)
    result = storage.get_result(target_env, month) if _query_detail() else None
    return response.ok({"run": _run_view(run, _now()), "result": result})


@app.get("/runs/<month>/download")
def download_xlsx(month: str) -> dict | Response:
    _claims()
    _require(_is_month(month), "month は YYYY-MM 形式で指定してください")
    target_env = _target_env_or_default()
    run = repo.get_run(target_env, month) or {}
    key = run.get("s3_xlsx_key")
    if not key or run.get("status") not in repo.DONE_STATUSES:
        return response.error(NOT_FOUND, f"{target_env} の {month} の Excel はありません", 404)
    url, expires_at = storage.presign_xlsx(key, messages.attachment_filename(month, target_env))
    return response.ok({"url": url, "expires_at": expires_at.isoformat()})


@app.post("/runs")
def post_runs() -> Response:
    claims = _claims()
    payload = _body()
    settings = repo.get_settings()
    month = payload.get("month")
    _require(_is_month(month), "month は YYYY-MM 形式で指定してください")
    target_env = payload.get("target_env") or repo.target_env()
    _require_fixed_target_env(target_env)
    now = _now()
    closing = period.closing_period(month, settings["cutoff_day"], settings["cutoff_time"])
    _require(closing.start <= now, f"{month} の課金対象期間はまだ始まっていません", MONTH_NOT_STARTED)

    imminent = _schedule_imminent_message(now)
    if imminent:
        return response.error(SCHEDULE_IMMINENT, imminent, 409)

    running = _other_running_months(target_env, month, now)
    if running:
        message = f"{'、'.join(running)} が実行中です。終わってから実行してください。"
        return response.error(OTHER_RUN_RUNNING, message, 409)

    distribute = _bool_field(payload, "distribute", False)
    force = _bool_field(payload, "force", False)
    run_token = str(uuid.uuid4())
    try:
        run = repo.acquire_run(
            target_env,
            month,
            trigger=TRIGGER,
            force=force,
            run_token=run_token,
            requested_by=claims.sub,
            requested_by_email=claims.email,
            distribute=distribute,
            stale_before=now.replace(hour=0, minute=0, second=0, microsecond=0),
        )
    except repo.RunAcquisitionError as error:
        return response.error(error.reason, str(error), 409)

    attempt = run["attempt"]
    try:
        execution_arn = _start_execution(target_env, month, attempt, run_token, distribute, force)
    except Exception:
        logger.exception("ステートマシンの起動に失敗しました", {"target_env": target_env, "month": month})
        repo.finish_failed(target_env, month, attempt, "StartExecution に失敗しました")
        return response.error(START_EXECUTION_FAILED, "ステートマシンを起動できませんでした", 502)

    logger.info(
        "手動実行を受け付けました",
        {"target_env": target_env, "month": month, "attempt": attempt, "execution_arn": execution_arn},
    )
    _put_run_audit(
        claims,
        {
            "month": month,
            "target_env": target_env,
            "distribute": distribute,
            "force": force,
            "attempt": attempt,
            "execution_name": execution_arn.rsplit(":", 1)[-1],
        },
    )
    return response.json_response(
        response.ok(
            {
                "month": month,
                "target_env": target_env,
                "status": repo.QUEUED,
                "attempt": attempt,
                "execution_arn": execution_arn,
            }
        ),
        202,
    )


def _schedule_imminent_message(now: datetime) -> str | None:
    """スケジュール実行の直前なら案内文を返す（#1006）。窓の外は `None`。

    先に発火されるとログインの奪い合いになるため、実行中判定より手前で断る。
    """
    hour = int(os.environ.get("SCHEDULE_HOUR") or schedule.DEFAULT_HOUR)
    minute = int(os.environ.get("SCHEDULE_MINUTE") or schedule.DEFAULT_MINUTE)
    tz = schedule.timezone_of(os.environ.get("SCHEDULE_TIMEZONE") or schedule.DEFAULT_TIMEZONE)
    if not schedule.is_imminent(now, hour, minute, tz):
        return None
    return (
        f"{schedule.schedule_label(hour, minute)} のスケジュール実行の直前です。"
        f"{schedule.next_allowed_label(hour, minute)}に実行してください。"
    )


def _other_running_months(target_env: str, month: str, now: datetime) -> list[str]:
    """対象月以外で走っている RUN の月（#1006）。

    SaaS_A の管理 API は 1 ユーザー 1 トークンで、後発の `Init` のログインが先発の
    トークンを無効化する。重ねると先発の残りテナントが全部 401 になり、成功扱いのまま
    取得失敗入りの Excel ができるため、手前で断る。対象月自身は `acquire_run` の
    条件式が `ALREADY_RUNNING` として弾く。
    """
    return [run["month"] for run in repo.active_runs(target_env, now) if run["month"] != month]


def _start_execution(target_env: str, month: str, attempt: int, run_token: str, distribute: bool, force: bool) -> str:
    started = _states().start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=execution.execution_name(target_env, month, attempt),
        input=json.dumps(
            execution.execution_input(
                trigger=TRIGGER,
                target_env=target_env,
                month=month,
                attempt=attempt,
                run_token=run_token,
                distribute=distribute,
                force=force,
            ),
            ensure_ascii=False,
        ),
    )
    return started["executionArn"]


def _put_run_audit(claims: auth.Claims, after: dict) -> None:
    """受理した手動実行を監査に残す（ログタブの「手動実行」）。

    RUN は既に始まっているため、記録に失敗しても 202 は返す。断ったとき（409）は
    「実行した」ことにならないので呼ばない。
    """
    try:
        repo.put_run_audit(claims.sub, claims.email, after, _now())
    except Exception:
        logger.exception("手動実行の監査記録に失敗しました", after)


@app.get("/audits")
def get_audits() -> dict:
    _admin_claims()
    limit, target, since = _query_audit_limit(), _query_audit_target(), _query_since()
    return response.ok(repo.list_audits(limit, target=target, since=since))


@app.exception_handler(ValidationError)
def handle_validation_error(exc: ValidationError) -> Response:
    return response.error(exc.code, str(exc), 400)


@app.exception_handler(repo.VersionConflictError)
def handle_version_conflict(exc: repo.VersionConflictError) -> Response:
    return response.error(VERSION_CONFLICT, str(exc), 409)


@LambdaLogger.contextualize
@origin_verify.origin_verify
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
