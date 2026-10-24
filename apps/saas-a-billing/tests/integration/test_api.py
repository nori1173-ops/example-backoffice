"""`api.app` の 9 エンドポイントのテスト（設計 8 章 / 9 章 / 12.1）。

moto の DynamoDB / S3 / SSM / Step Functions に実際に読み書きし、ハンドラを
`make_api_event()` のイベントで一気通貫に呼ぶ。現在時刻は `api.app._now` を
差し替えて固定する（対象月・課金対象期間・stale 判定が日付に依存するため）。

`x-origin-verify` の検証は `ORIGIN_VERIFY_SKIP=1` で飛ばし、実際に検証したい
テストだけ秘密を差し替えて確かめる。
"""

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import boto3
import pytest
import requests
import responses
from api import app as api_app
from botocore.exceptions import ClientError
from saas_a_billing import repo, slack_directory, storage
from saas_a_billing.messages import DEFAULT_SLACK_TEMPLATE
from saas_a_billing.period import JST
from responses import registries

from example_backoffice import origin_verify as origin_verify_module
from tests.conftest import (
    ENV,
    ORIGIN_VERIFY_SECRET,
    REGION,
    SSM_PREFIX,
    make_api_event,
    make_price_table,
    make_result,
    make_settings,
)

VERIFICATION = "verification"
PRODUCTION = "production"
SLACK_CHANNEL = "C0123ABCD"
SLACK_USER = "U0000DEVA1"
SLACK_BOT_TOKEN_PARAMETER = f"/example-backoffice/{ENV}/shared/slack/bot-token"
SLACK_BOT_TOKEN = "xoxb-dummy-token-for-test"
ADMIN = ["admin"]
ACTOR_EMAIL = "devA@example.co.jp"

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=JST)
CLOSED_MONTH = "2026-08"
OPEN_MONTH = "2026-09"
FUTURE_MONTH = "2026-10"
OTHER_MONTH = "2026-07"

SENSITIVE_RUN_FIELDS = ("api_token", "token_expires_at", "run_token")
SAASA_TOKEN = "live-saasa-bearer-token"
INTERNAL_RUN_TOKEN = "internal-run-token"
TOKEN_EXPIRES_AT = "2026-08-31T11:00:00+09:00"

EXECUTION_NAME_PATTERN = re.compile(r"^verification-2026-08-1-[0-9a-f]{8}$")
EXECUTION_INPUT_KEYS = {"trigger", "target_env", "month", "attempt", "run_token", "distribute", "force"}


@pytest.fixture
def api(dynamodb_table, s3_bucket, state_machine, ssm_targets, monkeypatch):
    """4 リソースを立て、各モジュールの boto3 クライアントキャッシュを捨てる。"""
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")
    monkeypatch.setattr(api_app, "_now", lambda: NOW)
    reset_clients()
    yield SimpleNamespace(
        table=dynamodb_table,
        bucket=s3_bucket,
        state_machine_arn=state_machine,
        states=boto3.client("stepfunctions", region_name=REGION),
        ssm=boto3.client("ssm", region_name=REGION),
    )
    reset_clients()


def reset_clients() -> None:
    repo.reset_clients()
    storage.reset_clients()
    api_app.reset_clients()


def call(method: str, path: str, **kwargs) -> dict:
    return api_app.handler(make_api_event(method, path, **kwargs), None)


def body(result: dict) -> dict:
    return json.loads(result["body"])


def data(result: dict):
    return body(result)["data"]


def error_code(result: dict) -> str:
    return body(result)["error"]["code"]


def put_settings_item(api, **overrides) -> dict:
    settings = make_settings(**overrides)
    api.table.put_item(Item={**settings, "PK": "SETTINGS", "SK": "current"})
    return settings


def put_run_item(api, target_env: str = VERIFICATION, month: str = CLOSED_MONTH, **overrides) -> dict:
    run = {
        "target_env": target_env,
        "status": repo.SUCCEEDED,
        "trigger": "manual",
        "attempt": 1,
        "run_token": "previous-token",
        "distribute": True,
        "force": False,
        "started_at": NOW.isoformat(),
    }
    run.update(overrides)
    api.table.put_item(Item={**run, "PK": f"RUN#{target_env}", "SK": month})
    return run


def put_price_item(api, effective_from: str) -> dict:
    price = make_price_table(effective_from=effective_from)
    api.table.put_item(Item={**price, "PK": "PRICE", "SK": f"EFFECTIVE#{effective_from}"})
    return price


def put_audit_item(api, at: str, index: int, target: str = "settings") -> None:
    api.table.put_item(
        Item={
            "PK": "AUDIT",
            "SK": f"{at}#{index:04d}",
            "target": target,
            "actor": "test-sub",
            "actor_email": ACTOR_EMAIL,
            "before": {},
            "after": {},
            "at": at,
        }
    )


def unregister_target(api, target_env: str = VERIFICATION, leaf: str = "base-url") -> None:
    """固定環境の接続情報を 1 本落とし、SSM 未登録の状態を作る。"""
    api.ssm.delete_parameter(Name=f"{SSM_PREFIX}/targets/{target_env}/{leaf}")


def settings_payload(**overrides) -> dict:
    payload = {
        "target_env": VERIFICATION,
        "cutoff_day": 20,
        "cutoff_time": "24:00",
        "auto_run_enabled": True,
        "auto_run_from_month": "2026-08",
        "mail_to": [ACTOR_EMAIL],
        "mail_cc": [],
        "slack_channel_id": SLACK_CHANNEL,
        "slack_channel_label": "#proj-saas-a-billing",
        "slack_mention": "",
        "slack_template": DEFAULT_SLACK_TEMPLATE,
        "exclude_keywords": ["デモ"],
        "camera_format_names": [],
        "version": 0,
    }
    payload.update(overrides)
    return payload


def price_payload(**overrides) -> dict:
    payload = {
        "effective_from": "2026-09-01",
        "bands": [
            {"max_items": 500, "price_3y": 650, "price_1y": 450},
            {"max_items": 800, "price_3y": 1000, "price_1y": 700},
        ],
        "camera": {"price_3y": 1800, "price_1y": 1400},
        "note": "値上げ",
    }
    payload.update(overrides)
    return payload


def run_payload(**overrides) -> dict:
    payload = {"month": CLOSED_MONTH, "distribute": True, "force": False}
    payload.update(overrides)
    return payload


def raise_audit_error(*args, **kwargs) -> None:
    raise ClientError({"Error": {"Code": "InternalServerError"}}, "PutItem")


def start_run_with_token(api, month: str = CLOSED_MONTH) -> None:
    """running の RUN に生きた SaaS_A トークンを持たせる（設計 7.1 の `api_token`）。"""
    put_run_item(api, status=repo.RUNNING, month=month, run_token=INTERNAL_RUN_TOKEN)
    repo.set_token(VERIFICATION, month, 1, SAASA_TOKEN, TOKEN_EXPIRES_AT)


def assert_no_sensitive_fields(result: dict) -> None:
    """応答のどこにも機微・内部フィールドが現れないこと。"""
    for name in SENSITIVE_RUN_FIELDS:
        assert name not in result["body"]
    assert SAASA_TOKEN not in result["body"]
    assert INTERNAL_RUN_TOKEN not in result["body"]


def test_health_returns_ok_envelope(api):
    result = call("GET", "/health")

    assert result["statusCode"] == 200
    assert data(result) == {"status": "ok"}


def test_get_settings_seeds_and_returns_defaults(api):
    result = call("GET", "/settings")

    assert result["statusCode"] == 200
    assert data(result)["target_env"] == VERIFICATION
    assert data(result)["cutoff_day"] == 20
    assert data(result)["version"] == 0
    assert api.table.get_item(Key={"PK": "SETTINGS", "SK": "current"})["Item"]["cutoff_time"] == "24:00"


def test_get_settings_includes_saasa_host_targets_and_slack_mode(api):
    result = call("GET", "/settings")

    assert data(result)["saasa_host"] == "saasa-verification.example.com"
    assert data(result)["targets"] == {VERIFICATION: True}
    assert data(result)["slack_mode"] == "webhook"


def test_get_settings_returns_fixed_target_env(api, monkeypatch):
    """集計対象環境はサイト固定（計画 R1 / SP-01）。保存済みの値ではなく `TARGET_ENV` を返す。"""
    monkeypatch.setenv("TARGET_ENV", PRODUCTION)
    put_settings_item(api, target_env=VERIFICATION)

    result = call("GET", "/settings")

    assert data(result)["target_env"] == PRODUCTION


def test_get_settings_overrides_stored_target_env(api):
    """固定値と違う値が保存されていても、読み出しで固定値に上書きする。"""
    put_settings_item(api, target_env=PRODUCTION)

    result = call("GET", "/settings")

    assert data(result)["target_env"] == VERIFICATION


def test_get_settings_does_not_return_password(api):
    result = call("GET", "/settings")

    assert "dummy-password" not in result["body"]
    assert "dummy-user" not in result["body"]


def test_get_settings_allowed_for_non_admin(api):
    result = call("GET", "/settings", groups=[])

    assert result["statusCode"] == 200


def test_put_settings_requires_admin(api):
    result = call("PUT", "/settings", body=settings_payload(), groups=[])

    assert result["statusCode"] == 403
    assert error_code(result) == "FORBIDDEN"


def test_put_settings_updates_and_increments_version(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(cutoff_day=15), groups=ADMIN)

    assert result["statusCode"] == 200
    assert data(result)["cutoff_day"] == 15
    assert data(result)["version"] == 1


def test_put_settings_version_conflict_returns_409(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(version=5), groups=ADMIN)

    assert result["statusCode"] == 409
    assert error_code(result) == "VERSION_CONFLICT"


def test_settings_tolerate_retired_auto_retry_days(api):
    """自動再試行の撤去後も、旧属性が残った SETTINGS / RUN で API が壊れない（#1015）。"""
    put_settings_item(api, auto_retry_days=0)
    put_run_item(api, auto_attempts=3, retry_alert_sent=True)

    got = call("GET", "/settings")
    put = call("PUT", "/settings", body=settings_payload(auto_retry_days=99, cutoff_day=15), groups=ADMIN)
    run = call("GET", f"/runs/{CLOSED_MONTH}")

    assert got["statusCode"] == 200
    assert put["statusCode"] == 200
    assert data(put)["cutoff_day"] == 15
    assert data(put)["auto_retry_days"] == 0
    assert run["statusCode"] == 200


def test_put_settings_rejects_target_env_change(api):
    """固定値と異なる `target_env` は 400（計画 R1 / SP-01）。"""
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(target_env=PRODUCTION), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "TARGET_ENV_FIXED"


def test_put_settings_keeps_fixed_target_env_when_omitted(api):
    """`target_env` を省略した保存は通り、固定値のまま保たれる。"""
    put_settings_item(api)
    payload = settings_payload()
    del payload["target_env"]

    result = call("PUT", "/settings", body=payload, groups=ADMIN)

    assert result["statusCode"] == 200
    assert data(result)["target_env"] == VERIFICATION


def test_put_settings_rejects_unregistered_target_env(api):
    """固定環境の接続情報が SSM に無ければ保存できない。"""
    put_settings_item(api)
    unregister_target(api)

    result = call("PUT", "/settings", body=settings_payload(), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "TARGET_ENV_NOT_CONFIGURED"


def test_put_settings_validates_cutoff_day_range(api):
    put_settings_item(api)

    too_small = call("PUT", "/settings", body=settings_payload(cutoff_day=0), groups=ADMIN)
    too_large = call("PUT", "/settings", body=settings_payload(cutoff_day=29), groups=ADMIN)

    assert [too_small["statusCode"], too_large["statusCode"]] == [400, 400]
    assert [error_code(too_small), error_code(too_large)] == ["VALIDATION_ERROR", "VALIDATION_ERROR"]


def test_put_settings_accepts_cutoff_time_24_00(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(cutoff_time="24:00"), groups=ADMIN)

    assert result["statusCode"] == 200
    assert data(result)["cutoff_time"] == "24:00"


def test_put_settings_rejects_invalid_cutoff_time(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(cutoff_time="25:00"), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"


def test_put_settings_requires_slack_channel_id_format(api):
    put_settings_item(api)

    rejected = call("PUT", "/settings", body=settings_payload(slack_channel_id="#proj-saas-a-billing"), groups=ADMIN)
    accepted = call("PUT", "/settings", body=settings_payload(slack_channel_id="C0123ABCD"), groups=ADMIN)

    assert rejected["statusCode"] == 400
    assert error_code(rejected) == "VALIDATION_ERROR"
    assert accepted["statusCode"] == 200


def test_get_settings_returns_distribution_toggles_off_by_default(api):
    result = call("GET", "/settings")

    assert data(result)["mail_enabled"] is False
    assert data(result)["slack_enabled"] is False


def test_put_settings_stores_distribution_toggles(api):
    put_settings_item(api)

    result = call(
        "PUT",
        "/settings",
        body=settings_payload(mail_enabled=True, slack_enabled=True),
        groups=ADMIN,
    )

    assert result["statusCode"] == 200
    assert data(result)["mail_enabled"] is True
    assert data(result)["slack_enabled"] is True


def test_put_settings_rejects_non_bool_distribution_toggles(api):
    put_settings_item(api)

    mail = call("PUT", "/settings", body=settings_payload(mail_enabled="true"), groups=ADMIN)
    slack = call("PUT", "/settings", body=settings_payload(slack_enabled=1), groups=ADMIN)

    assert [mail["statusCode"], slack["statusCode"]] == [400, 400]
    assert [error_code(mail), error_code(slack)] == ["VALIDATION_ERROR", "VALIDATION_ERROR"]


def test_put_settings_allows_empty_slack_channel_when_slack_disabled(api):
    put_settings_item(api)

    result = call(
        "PUT",
        "/settings",
        body=settings_payload(slack_enabled=False, slack_channel_id=""),
        groups=ADMIN,
    )

    assert result["statusCode"] == 200
    assert data(result)["slack_channel_id"] == ""


def test_put_settings_validates_slack_channel_format_even_when_disabled(api):
    put_settings_item(api)

    result = call(
        "PUT",
        "/settings",
        body=settings_payload(slack_enabled=False, slack_channel_id="#proj-saas-a-billing"),
        groups=ADMIN,
    )

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"


def test_put_settings_requires_slack_channel_when_slack_enabled(api):
    put_settings_item(api)

    result = call(
        "PUT",
        "/settings",
        body=settings_payload(slack_enabled=True, slack_channel_id=""),
        groups=ADMIN,
    )

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"
    assert "Slack 配布を有効にするには slack_channel_id が必要です" in result["body"]


def test_put_settings_accepts_slack_channel_when_slack_enabled(api):
    put_settings_item(api)

    result = call(
        "PUT",
        "/settings",
        body=settings_payload(slack_enabled=True, slack_channel_id=SLACK_CHANNEL),
        groups=ADMIN,
    )

    assert result["statusCode"] == 200
    assert data(result)["slack_enabled"] is True


def test_put_settings_allows_empty_mail_to_when_mail_disabled(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_enabled=False, mail_to=[]), groups=ADMIN)

    assert result["statusCode"] == 200
    assert data(result)["mail_to"] == []


def test_put_settings_validates_mail_format_even_when_disabled(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_enabled=False, mail_to=["not-an-email"]), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"


def test_put_settings_requires_mail_to_when_mail_enabled(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_enabled=True, mail_to=[]), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"
    assert "メール配布を有効にするには mail_to が必要です" in result["body"]


def test_put_settings_accepts_mail_to_when_mail_enabled(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_enabled=True, mail_to=[ACTOR_EMAIL]), groups=ADMIN)

    assert result["statusCode"] == 200
    assert data(result)["mail_enabled"] is True


def test_put_settings_rejects_external_mail_to(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_to=["evil@example.com"]), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "MAIL_DOMAIN_NOT_ALLOWED"
    assert "evil@example.com" in result["body"]


def test_put_settings_rejects_external_mail_cc(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_cc=["evil@example.com"]), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "MAIL_DOMAIN_NOT_ALLOWED"
    assert "evil@example.com" in result["body"]


@pytest.mark.parametrize("address", ["a@evil-example.co.jp", "a@example.co.jp.evil.com", "a@sub.example.co.jp"])
def test_put_settings_rejects_lookalike_mail_domain(api, address):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_to=[address]), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "MAIL_DOMAIN_NOT_ALLOWED"
    assert address in result["body"]


def test_put_settings_rejects_external_mail_to_even_when_mail_disabled(api):
    put_settings_item(api)

    result = call(
        "PUT", "/settings", body=settings_payload(mail_enabled=False, mail_to=["evil@example.com"]), groups=ADMIN
    )

    assert result["statusCode"] == 400
    assert error_code(result) == "MAIL_DOMAIN_NOT_ALLOWED"


def test_put_settings_accepts_uppercase_internal_mail_domain(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(mail_to=["DEVA@EXAMPLE.CO.JP"]), groups=ADMIN)

    assert result["statusCode"] == 200
    assert data(result)["mail_to"] == ["DEVA@EXAMPLE.CO.JP"]


def test_put_settings_rejects_unknown_slack_placeholder(api):
    put_settings_item(api)

    result = call("PUT", "/settings", body=settings_payload(slack_template="{unknown} を送りました"), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"


def test_put_settings_rejects_non_list_keyword_settings(api):
    """文字列だと 1 文字ずつの除外キーワード・部分一致のカメラ判定になり課金額が壊れる。"""
    put_settings_item(api)

    excluded = call("PUT", "/settings", body=settings_payload(exclude_keywords="デモ"), groups=ADMIN)
    cameras = call("PUT", "/settings", body=settings_payload(camera_format_names="CamA-MC"), groups=ADMIN)

    assert [excluded["statusCode"], cameras["statusCode"]] == [400, 400]
    assert [error_code(excluded), error_code(cameras)] == ["VALIDATION_ERROR", "VALIDATION_ERROR"]


def test_put_settings_rejects_non_string_slack_labels(api):
    put_settings_item(api)

    label = call("PUT", "/settings", body=settings_payload(slack_channel_label=123), groups=ADMIN)
    mention = call("PUT", "/settings", body=settings_payload(slack_mention=["@here"]), groups=ADMIN)

    assert [label["statusCode"], mention["statusCode"]] == [400, 400]
    assert [error_code(label), error_code(mention)] == ["VALIDATION_ERROR", "VALIDATION_ERROR"]


def test_put_settings_writes_audit_with_actor_email(api):
    put_settings_item(api)

    call("PUT", "/settings", body=settings_payload(cutoff_day=15), groups=ADMIN)

    audits = repo.list_audits()
    assert [audit["target"] for audit in audits] == ["settings"]
    assert audits[0]["actor_email"] == ACTOR_EMAIL
    assert audits[0]["before"]["cutoff_day"] == 20
    assert audits[0]["after"]["cutoff_day"] == 15


def test_get_prices_returns_versions_and_current(api):
    put_price_item(api, "2025-09-01")
    put_price_item(api, "2026-09-01")

    result = call("GET", "/prices")

    assert [version["effective_from"] for version in data(result)["versions"]] == ["2026-09-01", "2025-09-01"]
    assert data(result)["current"]["effective_from"] == "2025-09-01"


def test_post_prices_requires_admin(api):
    result = call("POST", "/prices", body=price_payload(), groups=[])

    assert result["statusCode"] == 403
    assert error_code(result) == "FORBIDDEN"


def test_post_prices_rejects_unsorted_bands(api):
    bands = [
        {"max_items": 800, "price_3y": 1000, "price_1y": 700},
        {"max_items": 500, "price_3y": 650, "price_1y": 450},
    ]

    result = call("POST", "/prices", body=price_payload(bands=bands), groups=ADMIN)

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"


def test_post_prices_rejects_duplicate_effective_from(api):
    created = call("POST", "/prices", body=price_payload(), groups=ADMIN)
    duplicated = call("POST", "/prices", body=price_payload(), groups=ADMIN)

    assert created["statusCode"] == 200
    assert data(created)["created_by_email"] == ACTOR_EMAIL
    assert duplicated["statusCode"] == 409
    assert error_code(duplicated) == "ALREADY_EXISTS"


def test_get_runs_returns_month_descending(api):
    put_run_item(api, month="2026-06")
    put_run_item(api, month="2026-08")
    put_run_item(api, month="2026-07")

    result = call("GET", "/runs")

    assert [run["month"] for run in data(result)] == ["2026-08", "2026-07", "2026-06"]


def test_get_runs_filters_by_target_env(api):
    put_run_item(api, target_env=VERIFICATION)
    put_run_item(api, target_env=PRODUCTION)

    result = call("GET", "/runs", query={"target_env": PRODUCTION})

    assert [run["target_env"] for run in data(result)] == [PRODUCTION]


def test_get_runs_defaults_to_fixed_target_env(api):
    """クエリ省略時はサイト固定環境だけを返す（設計 8 章 / FINAL-review Must-1）。"""
    put_run_item(api, target_env=VERIFICATION, month="2026-08")
    put_run_item(api, target_env=PRODUCTION, month="2026-09")

    result = call("GET", "/runs")

    assert [run["target_env"] for run in data(result)] == [VERIFICATION]
    assert [run["month"] for run in data(result)] == ["2026-08"]


def test_get_runs_omits_request_history(api):
    """一覧は実行者メールの履歴を返さない（画面で使わず応答が膨らむだけ。Should-5）。"""
    put_run_item(api, request_history=[{"actor_email": ACTOR_EMAIL, "at": NOW.isoformat()}])

    result = call("GET", "/runs")

    assert "request_history" not in data(result)[0]
    assert ACTOR_EMAIL not in result["body"]


def test_get_run_includes_request_history(api):
    """詳細では従来どおり返す（誰がいつ実行したかの追跡に使う）。"""
    history = [{"actor_email": ACTOR_EMAIL, "at": NOW.isoformat()}]
    put_run_item(api, request_history=history)

    result = call("GET", f"/runs/{CLOSED_MONTH}")

    assert data(result)["run"]["request_history"] == history


def test_get_runs_caps_limit_at_100(api, monkeypatch):
    captured = {}

    def fake_list_runs(target_env, limit):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(repo, "list_runs", fake_list_runs)

    call("GET", "/runs", query={"limit": "500"})

    assert captured["limit"] == 100


def test_get_runs_maps_stale_running_to_failed(api):
    put_run_item(api, status=repo.RUNNING, started_at=(NOW - timedelta(days=1)).isoformat())

    result = call("GET", "/runs")

    assert data(result)[0]["status"] == repo.FAILED
    assert data(result)[0]["error"] == "TIMEOUT(推定)"


def test_get_runs_redacts_token_and_internal_fields(api):
    """実行中の RUN の `api_token` は生きた SaaS_A の Bearer トークンなので応答に出さない。"""
    start_run_with_token(api)

    result = call("GET", "/runs")

    assert data(result)[0]["status"] == repo.RUNNING
    assert_no_sensitive_fields(result)


def test_get_run_redacts_token_and_internal_fields(api):
    put_settings_item(api)
    start_run_with_token(api)
    storage.put_result(VERIFICATION, CLOSED_MONTH, make_result(month=CLOSED_MONTH, target_env=VERIFICATION))

    without_detail = call("GET", f"/runs/{CLOSED_MONTH}")
    with_detail = call("GET", f"/runs/{CLOSED_MONTH}", query={"detail": "true"})

    assert data(without_detail)["run"]["status"] == repo.RUNNING
    assert data(with_detail)["result"]["summary"]["total_amount"] == 7200
    assert_no_sensitive_fields(without_detail)
    assert_no_sensitive_fields(with_detail)


def test_get_run_detail_includes_result_json(api):
    put_settings_item(api)
    put_run_item(api)
    storage.put_result(VERIFICATION, CLOSED_MONTH, make_result(month=CLOSED_MONTH, target_env=VERIFICATION))

    result = call("GET", f"/runs/{CLOSED_MONTH}", query={"detail": "true"})

    assert data(result)["run"]["status"] == repo.SUCCEEDED
    assert data(result)["result"]["summary"]["total_amount"] == 7200


def test_get_run_detail_false_returns_run_only(api):
    put_settings_item(api)
    put_run_item(api)
    storage.put_result(VERIFICATION, CLOSED_MONTH, make_result(month=CLOSED_MONTH, target_env=VERIFICATION))

    result = call("GET", f"/runs/{CLOSED_MONTH}", query={"detail": "false"})

    assert data(result)["run"]["month"] == CLOSED_MONTH
    assert data(result)["result"] is None


def test_get_run_detail_defaults_to_false(api):
    """`detail` を省いたら明細は付けない（result.json の読み出しを既定にしない）。"""
    put_settings_item(api)
    put_run_item(api)
    storage.put_result(VERIFICATION, CLOSED_MONTH, make_result(month=CLOSED_MONTH, target_env=VERIFICATION))

    result = call("GET", f"/runs/{CLOSED_MONTH}")

    assert data(result)["run"]["month"] == CLOSED_MONTH
    assert data(result)["result"] is None


def test_get_runs_rejects_non_decimal_limit(api):
    """`"\u00b2".isdigit()` は True だが `int()` は落ちる。500 にせず 400 で返す。"""
    put_settings_item(api)

    result = call("GET", "/runs", groups=ADMIN, query={"limit": "\u00b2"})

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"


def test_get_run_not_found_returns_404(api):
    put_settings_item(api)

    result = call("GET", f"/runs/{CLOSED_MONTH}")

    assert result["statusCode"] == 404
    assert error_code(result) == "NOT_FOUND"


def test_post_runs_starts_execution_and_returns_202(api):
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 202
    started = data(result)
    assert (started["month"], started["target_env"], started["status"], started["attempt"]) == (
        CLOSED_MONTH,
        VERIFICATION,
        repo.QUEUED,
        1,
    )
    execution_input = json.loads(api.states.describe_execution(executionArn=started["execution_arn"])["input"])
    assert set(execution_input) == EXECUTION_INPUT_KEYS
    assert execution_input["trigger"] == "manual"
    assert repo.get_run(VERIFICATION, CLOSED_MONTH)["requested_by_email"] == ACTOR_EMAIL


def test_post_runs_allowed_for_non_admin(api):
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload(), groups=[])

    assert result["statusCode"] == 202


def test_post_runs_writes_run_audit(api):
    put_settings_item(api)

    call("POST", "/runs", body=run_payload(force=True))

    result = call("GET", "/audits", groups=ADMIN, query={"target": "run"})
    entries = data(result)
    assert len(entries) == 1
    assert entries[0]["actor_email"] == ACTOR_EMAIL
    assert entries[0]["before"] is None
    assert entries[0]["after"]["month"] == CLOSED_MONTH
    assert entries[0]["after"]["target_env"] == VERIFICATION
    assert entries[0]["after"]["distribute"] is True
    assert entries[0]["after"]["force"] is True
    assert entries[0]["after"]["attempt"] == 1
    assert EXECUTION_NAME_PATTERN.match(entries[0]["after"]["execution_name"])


def test_post_runs_conflict_is_not_recorded(api):
    """受理しなかった試行は残さない（409 は「実行した」ことにならない）。"""
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 409
    assert repo.list_audits() == []


def test_post_runs_returns_202_even_if_audit_write_fails(api, monkeypatch):
    """監査は best-effort。RUN は既に始まっているため 202 を返す。"""
    put_settings_item(api)
    monkeypatch.setattr(repo, "put_run_audit", raise_audit_error)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 202
    assert repo.list_audits() == []


def test_post_runs_conflict_when_already_running(api):
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 409
    assert error_code(result) == "ALREADY_RUNNING"


def test_post_runs_conflict_just_before_the_schedule(api, monkeypatch):
    """06:00 のスケジュール実行と重ならないよう直前 1 分は断る（#1006）。"""
    monkeypatch.setattr(api_app, "_now", lambda: NOW.replace(hour=5, minute=59, second=30))
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 409
    assert error_code(result) == "SCHEDULE_IMMINENT"
    message = body(result)["error"]["message"]
    assert "06:00" in message
    assert "06:01 以降" in message
    assert repo.get_run(VERIFICATION, CLOSED_MONTH) is None


def test_post_runs_allowed_just_outside_the_schedule_window(api, monkeypatch):
    monkeypatch.setattr(api_app, "_now", lambda: NOW.replace(hour=5, minute=58, second=59))
    put_settings_item(api)

    assert call("POST", "/runs", body=run_payload())["statusCode"] == 202


def test_post_runs_schedule_window_follows_the_environment(api, monkeypatch):
    """スケジュール時刻はテンプレートのパラメータが正（`SCHEDULE_HOUR` / `SCHEDULE_MINUTE`）。"""
    monkeypatch.setenv("SCHEDULE_HOUR", "23")
    monkeypatch.setenv("SCHEDULE_MINUTE", "45")
    monkeypatch.setattr(api_app, "_now", lambda: NOW.replace(hour=23, minute=44, second=30))
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload())

    assert error_code(result) == "SCHEDULE_IMMINENT"
    assert "23:45" in body(result)["error"]["message"]


def test_post_runs_schedule_check_precedes_other_running_month(api, monkeypatch):
    """直前判定が先。実行中の月があっても「06:01 以降に」と案内する。"""
    monkeypatch.setattr(api_app, "_now", lambda: NOW.replace(hour=5, minute=59, second=30))
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING, month=OPEN_MONTH)

    result = call("POST", "/runs", body=run_payload())

    assert error_code(result) == "SCHEDULE_IMMINENT"


def test_post_runs_conflict_when_other_month_is_running(api):
    """SaaS_A の管理 API は 1 ユーザー 1 トークン。別月と重ねると先発が 401 で全滅する（#1006）。"""
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING, month=OPEN_MONTH)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 409
    assert error_code(result) == "OTHER_RUN_RUNNING"
    assert OPEN_MONTH in body(result)["error"]["message"]
    assert repo.get_run(VERIFICATION, CLOSED_MONTH) is None


def test_post_runs_conflict_lists_every_running_month(api):
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING, month=OPEN_MONTH)
    put_run_item(api, status=repo.QUEUED, month=OTHER_MONTH)

    result = call("POST", "/runs", body=run_payload())

    message = body(result)["error"]["message"]
    assert error_code(result) == "OTHER_RUN_RUNNING"
    assert OTHER_MONTH in message
    assert OPEN_MONTH in message


def test_post_runs_force_does_not_bypass_other_running_month(api):
    """強制再実行でも他の月のトークンは奪えるため、`force` では抜けられない（#1006）。"""
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING, month=OPEN_MONTH)

    result = call("POST", "/runs", body=run_payload(force=True))

    assert result["statusCode"] == 409
    assert error_code(result) == "OTHER_RUN_RUNNING"


def test_post_runs_ignores_stale_run_of_other_month(api):
    """落ちたまま running の RUN は翌日以降ブロックしない（設計 6.2 の stale 規則）。"""
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING, month=OPEN_MONTH, started_at=(NOW - timedelta(days=1)).isoformat())

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 202


def test_post_runs_conflict_when_succeeded_without_force(api):
    put_settings_item(api)
    put_run_item(api, status=repo.SUCCEEDED)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 409
    assert error_code(result) == "ALREADY_SUCCEEDED"


def test_post_runs_force_overrides_succeeded(api):
    put_settings_item(api)
    put_run_item(api, status=repo.SUCCEEDED)

    result = call("POST", "/runs", body=run_payload(force=True))

    assert result["statusCode"] == 202
    assert data(result)["attempt"] == 2


def test_post_runs_rejects_future_month(api):
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload(month=FUTURE_MONTH))

    assert result["statusCode"] == 400
    assert error_code(result) == "MONTH_NOT_STARTED"


def test_post_runs_allows_open_period_month(api):
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload(month=OPEN_MONTH))

    assert result["statusCode"] == 202
    assert data(result)["month"] == OPEN_MONTH


def test_post_runs_rejects_target_env_mismatch(api):
    """固定値と異なる `target_env` の実行要求は 400（計画 R1 / SP-01）。"""
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload(target_env=PRODUCTION))

    assert result["statusCode"] == 400
    assert error_code(result) == "TARGET_ENV_FIXED"


def test_post_runs_accepts_matching_target_env(api):
    """固定値と同じ `target_env` の明示指定は受け付ける（省略可）。"""
    put_settings_item(api)

    result = call("POST", "/runs", body=run_payload(target_env=VERIFICATION))

    assert result["statusCode"] == 202
    assert data(result)["target_env"] == VERIFICATION


def test_post_runs_ignores_stored_target_env(api):
    """保存済みの設定が本番でも、実行は固定環境で走る。"""
    put_settings_item(api, target_env=PRODUCTION)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 202
    assert data(result)["target_env"] == VERIFICATION
    assert repo.get_run(PRODUCTION, CLOSED_MONTH) is None


def test_post_runs_rejects_unregistered_target_env(api):
    """固定環境の接続情報が SSM に無ければ実行できない。"""
    put_settings_item(api)
    unregister_target(api)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 400
    assert error_code(result) == "TARGET_ENV_NOT_CONFIGURED"


def test_post_runs_defaults_distribute_to_false(api):
    """`distribute` を省略した curl / スクリプトからの実行で配布しない（SP-07）。"""
    put_settings_item(api)

    result = call("POST", "/runs", body={"month": CLOSED_MONTH})

    assert result["statusCode"] == 202
    assert repo.get_run(VERIFICATION, CLOSED_MONTH)["distribute"] is False


def test_post_runs_records_requester_in_history(api):
    """誰がいつ実行したかを RUN に残す（SEC-10 / 計画 R8）。"""
    put_settings_item(api)

    call("POST", "/runs", body=run_payload())

    history = repo.get_run(VERIFICATION, CLOSED_MONTH)["request_history"]
    assert len(history) == 1
    assert history[0]["requested_by"] == "test-sub"
    assert history[0]["requested_by_email"] == ACTOR_EMAIL
    assert history[0]["requested_at"]
    assert history[0]["trigger"] == "manual"


def test_post_runs_appends_history_without_overwriting(api):
    """再実行しても前回の実行者を消さない（SEC-10 / 計画 R8）。"""
    put_settings_item(api)
    call("POST", "/runs", body=run_payload())
    repo.finish_failed(VERIFICATION, CLOSED_MONTH, 1, "失敗")

    result = call(
        "POST",
        "/runs",
        body=run_payload(distribute=False),
        sub="other-sub",
        email="devbot@example.co.jp",
    )

    assert result["statusCode"] == 202
    history = repo.get_run(VERIFICATION, CLOSED_MONTH)["request_history"]
    assert [entry["requested_by"] for entry in history] == ["test-sub", "other-sub"]
    assert [entry["distribute"] for entry in history] == [True, False]


def test_post_runs_rejects_non_boolean_distribute_and_force(api):
    """文字列 `"false"` を truthy として拾うと、配布しないつもりの実行が実配信になる。"""
    put_settings_item(api)

    distribute = call("POST", "/runs", body=run_payload(distribute="false"))
    force = call("POST", "/runs", body=run_payload(force="true"))

    assert [distribute["statusCode"], force["statusCode"]] == [400, 400]
    assert [error_code(distribute), error_code(force)] == ["VALIDATION_ERROR", "VALIDATION_ERROR"]
    assert repo.get_run(VERIFICATION, CLOSED_MONTH) is None


def test_post_runs_rolls_back_run_to_failed_when_start_execution_fails(api, monkeypatch):
    put_settings_item(api)
    monkeypatch.setattr(api_app, "_states", _raising_states)

    result = call("POST", "/runs", body=run_payload())

    assert result["statusCode"] == 502
    assert error_code(result) == "START_EXECUTION_FAILED"
    run = repo.get_run(VERIFICATION, CLOSED_MONTH)
    assert run["status"] == repo.FAILED
    assert run["error"]


def test_post_runs_execution_name_format(api):
    put_settings_item(api)

    call("POST", "/runs", body=run_payload())

    executions = api.states.list_executions(stateMachineArn=api.state_machine_arn)["executions"]
    assert EXECUTION_NAME_PATTERN.match(executions[0]["name"])


def test_download_returns_presigned_url_and_expiry(api):
    put_settings_item(api)
    key = storage.xlsx_key(VERIFICATION, CLOSED_MONTH)
    put_run_item(api, status=repo.SUCCEEDED, s3_xlsx_key=key)

    result = call("GET", f"/runs/{CLOSED_MONTH}/download")

    assert result["statusCode"] == 200
    assert key in data(result)["url"]
    assert "X-Amz-Signature" in data(result)["url"]
    assert datetime.fromisoformat(data(result)["expires_at"]) > datetime.now(JST)


def test_download_404_when_no_xlsx_key(api):
    put_settings_item(api)
    put_run_item(api, status=repo.SUCCEEDED)

    result = call("GET", f"/runs/{CLOSED_MONTH}/download")

    assert result["statusCode"] == 404
    assert error_code(result) == "NOT_FOUND"


def test_download_404_when_run_not_completed(api):
    """再実行中に前回 attempt の `s3_xlsx_key` が残っていても配らない。"""
    put_settings_item(api)
    put_run_item(api, status=repo.RUNNING, s3_xlsx_key=storage.xlsx_key(VERIFICATION, CLOSED_MONTH))

    result = call("GET", f"/runs/{CLOSED_MONTH}/download")

    assert result["statusCode"] == 404
    assert error_code(result) == "NOT_FOUND"


def test_get_audits_requires_admin(api):
    result = call("GET", "/audits", groups=[])

    assert result["statusCode"] == 403
    assert error_code(result) == "FORBIDDEN"


def test_get_audits_returns_latest_hundred_descending(api):
    moments = [(NOW - timedelta(minutes=index)).isoformat() for index in range(105)]
    for index, at in enumerate(moments):
        put_audit_item(api, at, index)

    result = call("GET", "/audits", groups=ADMIN)

    assert [audit["at"] for audit in data(result)] == moments[:100]


def test_get_audits_filters_by_target(api):
    moments = [(NOW - timedelta(minutes=index)).isoformat() for index in range(4)]
    for index, at in enumerate(moments):
        put_audit_item(api, at, index, "run" if index % 2 == 0 else "settings")

    result = call("GET", "/audits", groups=ADMIN, query={"target": "run", "limit": "1"})

    assert [audit["at"] for audit in data(result)] == [moments[0]]


def test_get_audits_since_drops_older_entries(api):
    moments = [(NOW - timedelta(days=index)).isoformat() for index in range(3)]
    for index, at in enumerate(moments):
        put_audit_item(api, at, index)

    since = (NOW - timedelta(days=1)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    result = call("GET", "/audits", groups=ADMIN, query={"since": since})

    assert [audit["at"] for audit in data(result)] == moments[:2]


@pytest.mark.parametrize(
    "query",
    [
        {"limit": "0"},
        {"limit": "201"},
        {"limit": "abc"},
        {"limit": "\u00b2"},
        {"target": "foo"},
        {"since": "abc"},
    ],
)
def test_get_audits_rejects_invalid_query(api, query):
    result = call("GET", "/audits", groups=ADMIN, query=query)

    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"


def test_get_audits_includes_actor_email_and_before_after(api):
    put_settings_item(api)
    call("PUT", "/settings", body=settings_payload(cutoff_day=15), groups=ADMIN)

    result = call("GET", "/audits", groups=ADMIN)

    assert data(result)[0]["actor_email"] == ACTOR_EMAIL
    assert data(result)[0]["before"]["cutoff_day"] == 20
    assert data(result)[0]["after"]["cutoff_day"] == 15


def put_slack_bot_token(api) -> None:
    api.ssm.put_parameter(Name=SLACK_BOT_TOKEN_PARAMETER, Value=SLACK_BOT_TOKEN, Type="SecureString")


def add_slack_directory(channel_name: str = "proj-saas-a-billing") -> None:
    """`conversations.list` / `users.list` の 1 ページ応答を順番に積む。"""
    responses.get(
        slack_directory.CONVERSATIONS_LIST_URL,
        json={
            "ok": True,
            "channels": [{"id": SLACK_CHANNEL, "name": channel_name, "is_private": False, "is_member": False}],
            "response_metadata": {"next_cursor": ""},
        },
    )
    responses.get(
        slack_directory.USERS_LIST_URL,
        json={
            "ok": True,
            "members": [
                {
                    "id": SLACK_USER,
                    "name": "devA",
                    "deleted": False,
                    "is_bot": False,
                    "real_name": "DevA",
                    "profile": {"real_name": "DevA", "display_name": "開発者本人"},
                }
            ],
            "response_metadata": {"next_cursor": ""},
        },
    )


@responses.activate(registry=registries.OrderedRegistry)
def test_get_slack_directory_returns_channels_and_users(api):
    put_slack_bot_token(api)
    add_slack_directory()

    result = call("GET", "/slack/directory", groups=ADMIN)

    assert result["statusCode"] == 200
    assert data(result)["channels"] == [
        {"id": SLACK_CHANNEL, "name": "proj-saas-a-billing", "is_private": False, "is_member": False}
    ]
    assert data(result)["users"] == [
        {"id": SLACK_USER, "handle": "devA", "real_name": "DevA", "display_name": "開発者本人"}
    ]
    assert data(result)["fetched_at"].endswith("Z")


@responses.activate(registry=registries.OrderedRegistry)
def test_get_slack_directory_does_not_leak_bot_token(api):
    put_slack_bot_token(api)
    add_slack_directory()

    result = call("GET", "/slack/directory", groups=ADMIN)

    assert result["statusCode"] == 200
    assert SLACK_BOT_TOKEN not in result["body"]


def test_get_slack_directory_requires_admin(api):
    """非 admin は Slack を叩く前に 403 で弾く（登録していない応答を使わない）。"""
    result = call("GET", "/slack/directory")

    assert result["statusCode"] == 403
    assert error_code(result) == "FORBIDDEN"


@responses.activate(registry=registries.OrderedRegistry)
def test_get_slack_directory_serves_cache_until_refresh(api):
    put_slack_bot_token(api)
    add_slack_directory()
    add_slack_directory(channel_name="renamed")

    call("GET", "/slack/directory", groups=ADMIN)
    cached = call("GET", "/slack/directory", groups=ADMIN)
    refreshed = call("GET", "/slack/directory", groups=ADMIN, query={"refresh": "1"})

    assert data(cached)["channels"][0]["name"] == "proj-saas-a-billing"
    assert data(refreshed)["channels"][0]["name"] == "renamed"


def test_get_slack_directory_returns_503_without_bot_token(api):
    result = call("GET", "/slack/directory", groups=ADMIN)

    assert result["statusCode"] == 503
    assert error_code(result) == "SLACK_NOT_CONFIGURED"


@responses.activate(registry=registries.OrderedRegistry)
def test_get_slack_directory_returns_502_with_slack_error(api):
    put_slack_bot_token(api)
    responses.get(slack_directory.CONVERSATIONS_LIST_URL, json={"ok": False, "error": "missing_scope"})

    result = call("GET", "/slack/directory", groups=ADMIN)

    assert result["statusCode"] == 502
    assert error_code(result) == "SLACK_API_ERROR"
    assert "missing_scope" in body(result)["error"]["message"]


@responses.activate(registry=registries.OrderedRegistry)
def test_get_slack_directory_returns_502_when_slack_unreachable(api):
    put_slack_bot_token(api)
    responses.get(slack_directory.CONVERSATIONS_LIST_URL, body=requests.ConnectionError("boom"))

    result = call("GET", "/slack/directory", groups=ADMIN)

    assert result["statusCode"] == 502
    assert error_code(result) == "SLACK_UNAVAILABLE"


def test_unauthenticated_event_returns_401(api):
    event = make_api_event("GET", "/settings")
    event["requestContext"].pop("authorizer")

    result = api_app.handler(event, None)

    assert result["statusCode"] == 401
    assert error_code(result) == "UNAUTHORIZED"


def test_origin_verify_mismatch_returns_403(api, monkeypatch):
    monkeypatch.delenv("ORIGIN_VERIFY_SKIP")
    monkeypatch.setattr(origin_verify_module, "_get_secret", lambda: f"not-{ORIGIN_VERIFY_SECRET}")

    result = call("GET", "/settings")

    assert result["statusCode"] == 403
    assert error_code(result) == "ORIGIN_FORBIDDEN"


def test_unexpected_error_propagates(api, monkeypatch):
    """想定外の例外は握り潰さず Lambda のエラーとして上げる（shared の response.py の方針）。"""

    def explode():
        raise RuntimeError("想定外の失敗")

    monkeypatch.setattr(repo, "get_settings", explode)

    with pytest.raises(RuntimeError):
        call("GET", "/settings")


def _raising_states():
    """`StartExecution` が必ず失敗する Step Functions クライアントの代わり。"""

    def start_execution(**_kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "StartExecution")

    return SimpleNamespace(start_execution=start_execution)


@pytest.mark.parametrize("value", ["billing_only", "all"])
def test_mail_attachment_sheets_put_and_audit(api, value):
    current = put_settings_item(api)
    result = call(
        "PUT", "/settings", body={"version": current["version"], "mail_attachment_sheets": value}, groups=ADMIN
    )
    assert result["statusCode"] == 200
    assert data(result)["mail_attachment_sheets"] == value
    assert data(call("GET", "/settings"))["mail_attachment_sheets"] == value
    audit = repo.list_audits()[0]
    assert audit["before"]["mail_attachment_sheets"] == "billing_only"
    assert audit["after"]["mail_attachment_sheets"] == value


@pytest.mark.parametrize("value", [None, "", "invalid", 1, True, [], {}])
def test_mail_attachment_sheets_rejects_invalid_value(api, value):
    current = put_settings_item(api)
    result = call(
        "PUT", "/settings", body={"version": current["version"], "mail_attachment_sheets": value}, groups=ADMIN
    )
    assert result["statusCode"] == 400
    assert error_code(result) == "VALIDATION_ERROR"
    assert "mail_attachment_sheets" in body(result)["error"]["message"]
    assert repo.get_settings()["version"] == current["version"]
    assert repo.list_audits() == []


@pytest.mark.parametrize("stored", [None, "billing_only", "all"])
def test_mail_attachment_sheets_omitted_put_keeps_current_value(api, stored):
    current = put_settings_item(api, **({"mail_attachment_sheets": stored} if stored else {}))
    result = call("PUT", "/settings", body={"version": current["version"], "cutoff_day": 15}, groups=ADMIN)
    assert result["statusCode"] == 200
    expected = stored or "billing_only"
    assert data(result)["mail_attachment_sheets"] == expected
    assert repo.get_settings()["mail_attachment_sheets"] == expected
    audit = repo.list_audits()[0]
    assert audit["before"]["mail_attachment_sheets"] == expected
    assert audit["after"]["mail_attachment_sheets"] == expected
