"""`batch.handlers.init` のテスト（設計 6.3 / 6.9 / 7.1 / 11.1 / 12.3）。

moto の DynamoDB / SSM に実際に読み書きし、SaaS_A API は `responses` で差し替える。
現在時刻は `init._now` を差し替えて固定する（`closing_period.open` が時刻に依存するため）。
トークンが出力にもログにも現れないことを実測で確かめる。
"""

import json
import logging
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlparse

import boto3
import pytest
import responses
from aws_lambda_powertools.utilities import parameters
from batch.handlers import init
from botocore.exceptions import ClientError
from saas_a_billing import repo, storage
from saas_a_billing.saasa_client import SaasAApiError, TargetEnvNotConfiguredError
from saas_a_billing.period import JST

from tests.conftest import REGION, SSM_PREFIX, make_price_table, make_settings, make_tenant

VERIFICATION = "verification"
PRODUCTION = "production"
MONTH = "2026-10"
RUN_TOKEN = "run-token-1"
ACCESS_TOKEN = "dummy-access-token-for-test"
EXPIRES_AT = "2026-10-21 08:48:25"
EXECUTION_ARN = "arn:aws:states:ap-northeast-1:000000000000:execution:saas-a-billing-run:verification-2026-10-1"

VERIFICATION_URL = "https://saasa-verification.example.com"
PRODUCTION_URL = "https://saasa-production.example.com"
AUTH_PATH = "/api/v2/getToken"
TENANT_LIST_PATH = "/api/v2/tenant/list"

LOGGER_NAME = "example_backoffice"

AFTER_CUTOFF = datetime(2026, 10, 21, 6, 0, tzinfo=JST)
BEFORE_CUTOFF = datetime(2026, 10, 15, 6, 0, tzinfo=JST)


@pytest.fixture
def aws(dynamodb_table, s3_bucket, ssm_targets, monkeypatch):
    """テーブル・添付バケットと検証環境の接続情報を立て、クライアントとパラメータキャッシュを捨てる。"""
    reset()
    freeze(monkeypatch, AFTER_CUTOFF)
    yield SimpleNamespace(table=dynamodb_table, ssm=boto3.client("ssm", region_name=REGION))
    reset()


def reset() -> None:
    repo.reset_clients()
    storage.reset_clients()
    parameters.clear_caches()


def freeze(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(init, "_now", lambda: moment)


def put_settings(aws, **overrides) -> dict:
    settings = make_settings(**overrides)
    aws.table.put_item(Item={**settings, "PK": "SETTINGS", "SK": "current"})
    return settings


def put_price(aws, effective_from: str = "2025-09-01") -> dict:
    price = make_price_table(effective_from=effective_from)
    aws.table.put_item(Item={**price, "PK": "PRICE", "SK": f"EFFECTIVE#{effective_from}"})
    return price


def put_credentials(aws, target_env: str, base_url: str, leaves: tuple[str, ...] = ("base-url", "user-id", "password")):
    values = {"base-url": base_url, "user-id": "dummy-user", "password": "dummy-password"}
    for leaf in leaves:
        aws.ssm.put_parameter(
            Name=f"{SSM_PREFIX}/targets/{target_env}/{leaf}",
            Value=values[leaf],
            Type="SecureString",
            Overwrite=True,
        )


def acquire(target_env: str = VERIFICATION, month: str = MONTH) -> dict:
    return repo.acquire_run(
        target_env,
        month,
        trigger="scheduled",
        force=False,
        run_token=RUN_TOKEN,
        stale_before=AFTER_CUTOFF.replace(hour=0),
    )


def make_event(**overrides) -> dict:
    event = {
        "trigger": "scheduled",
        "target_env": VERIFICATION,
        "month": MONTH,
        "attempt": 1,
        "run_token": RUN_TOKEN,
        "distribute": True,
        "force": False,
        "execution_arn": EXECUTION_ARN,
    }
    event.update(overrides)
    return event


def token_body() -> dict:
    return {
        "userInfo": {"user_cd": "dummy-user", "level": 1},
        "token_info": {
            "token_type": "Bearer",
            "access_token": ACCESS_TOKEN,
            "refresh_token": "dummy-refresh-token-for-test",
            "expires_at": EXPIRES_AT,
        },
    }


def make_tenants(count: int) -> list[dict]:
    return [
        make_tenant(tenant_id=f"00000000-0000-0000-0000-{index:012d}", tenant_cd=f"TNT{index:03d}")
        for index in range(count)
    ]


def register_saasa(tenants: list[dict], base_url: str = VERIFICATION_URL, per_page: int = 100) -> list[dict]:
    """`getToken` と `tenant/list`（ページ分割）を登録する。"""
    responses.post(f"{base_url}{AUTH_PATH}", json=token_body())
    for offset in range(0, max(len(tenants), 1), per_page):
        responses.get(
            f"{base_url}{TENANT_LIST_PATH}",
            json={"data": {"count": len(tenants), "rows": tenants[offset : offset + per_page]}},
        )
    return tenants


def pages_requested() -> list[str]:
    """`tenant/list` に投げた `page` の並び（先頭の `getToken` は除く）。"""
    return [dict(parse_qsl(urlparse(call.request.url).query))["page"] for call in responses.calls[1:]]


def prepare(aws, target_env: str = VERIFICATION, tenant_count: int = 2) -> list[dict]:
    """設定・単価表・`queued` の RUN と SaaS_A の応答を用意する。"""
    put_settings(aws, target_env=target_env)
    put_price(aws)
    acquire(target_env)
    return register_saasa(make_tenants(tenant_count))


@responses.activate
def test_transitions_run_from_queued_to_running(aws):
    prepare(aws)

    result = init.handler(make_event(), None)

    assert result["run_id"] == f"{VERIFICATION}-{MONTH}-1"
    assert repo.get_run(VERIFICATION, MONTH)["status"] == repo.RUNNING


@responses.activate
def test_execution_arn_is_recorded_on_run(aws):
    prepare(aws)

    init.handler(make_event(), None)

    assert repo.get_run(VERIFICATION, MONTH)["execution_arn"] == EXECUTION_ARN


def test_start_run_fails_when_run_token_mismatch(aws):
    prepare(aws)

    with pytest.raises(ClientError) as raised:
        init.handler(make_event(run_token="other-token"), None)

    assert raised.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
    assert repo.get_run(VERIFICATION, MONTH)["status"] == repo.QUEUED


@responses.activate
def test_returns_tenant_id_and_cd_only(aws):
    tenants = prepare(aws)

    result = init.handler(make_event(), None)

    assert result["tenants"] == [
        {"tenant_id": tenant["tenant_id"], "tenant_cd": tenant["tenant_cd"]} for tenant in tenants
    ]
    assert result["target_env"] == VERIFICATION
    assert result["month"] == MONTH
    assert result["attempt"] == 1
    assert result["distribute"] is True


@responses.activate
def test_token_is_saved_to_run_item(aws):
    prepare(aws)

    init.handler(make_event(), None)

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["api_token"] == ACCESS_TOKEN
    assert run["token_expires_at"] == EXPIRES_AT


@responses.activate
def test_token_is_not_in_output(aws):
    prepare(aws)

    result = init.handler(make_event(), None)

    assert ACCESS_TOKEN not in json.dumps(result, ensure_ascii=False)
    assert "api_token" not in result


@responses.activate
def test_token_is_not_logged(aws, caplog):
    """アプリのロガーを最も詳細にしてもトークンとパスワードが現れない（設計 12.3）。"""
    prepare(aws)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        init.handler(make_event(), None)

    assert "RUN を開始しました" in caplog.text
    assert ACCESS_TOKEN not in caplog.text
    assert "dummy-password" not in caplog.text


@responses.activate
def test_execution_input_target_env_overrides_settings(aws):
    put_settings(aws, target_env=VERIFICATION)
    put_price(aws)
    put_credentials(aws, PRODUCTION, PRODUCTION_URL)
    acquire(PRODUCTION)
    register_saasa(make_tenants(1), base_url=PRODUCTION_URL)

    result = init.handler(make_event(target_env=PRODUCTION), None)

    assert result["target_env"] == PRODUCTION
    assert result["run_id"] == f"{PRODUCTION}-{MONTH}-1"
    assert repo.get_run(PRODUCTION, MONTH)["status"] == repo.RUNNING
    assert repo.get_run(PRODUCTION, MONTH)["saasa_host"] == "saasa-production.example.com"
    assert repo.get_run(VERIFICATION, MONTH) is None


@responses.activate
def test_uses_settings_target_env_when_input_absent(aws):
    prepare(aws)
    event = make_event()
    del event["target_env"]

    result = init.handler(event, None)

    assert result["target_env"] == VERIFICATION
    assert repo.get_run(VERIFICATION, MONTH)["status"] == repo.RUNNING
    assert repo.get_run(VERIFICATION, MONTH)["saasa_host"] == "saasa-verification.example.com"


def test_missing_ssm_raises_target_env_not_configured(aws):
    put_settings(aws)
    put_price(aws)
    put_credentials(aws, PRODUCTION, PRODUCTION_URL, leaves=("base-url", "user-id"))
    acquire(PRODUCTION)

    with pytest.raises(TargetEnvNotConfiguredError) as raised:
        init.handler(make_event(target_env=PRODUCTION), None)

    assert "password" in str(raised.value)


@responses.activate
def test_empty_price_table_seeds_default_2355(aws):
    """単価表が 1 版も無ければ既定値（support-tracker #2001）を自動シードして続行する。"""
    put_settings(aws)
    acquire()
    register_saasa(make_tenants(2))

    result = init.handler(make_event(), None)

    assert result["price_effective_from"] == "2025-09-01"


@responses.activate
def test_price_effective_from_is_resolved_at_cutoff_date(aws):
    prepare(aws)
    put_price(aws, effective_from="2026-10-01")
    put_price(aws, effective_from="2026-11-01")

    result = init.handler(make_event(), None)

    assert result["price_effective_from"] == "2026-10-01"


@responses.activate
def test_closing_period_in_output_matches_settings(aws, monkeypatch):
    prepare(aws)
    put_settings(aws, cutoff_day=25, cutoff_time="12:00")
    freeze(monkeypatch, datetime(2026, 10, 26, 6, 0, tzinfo=JST))

    result = init.handler(make_event(), None)

    assert result["closing_period"] == {
        "from": "2026-09-25T12:00:00+09:00",
        "to": "2026-10-25T12:00:00+09:00",
        "cutoff_day": 25,
        "cutoff_time": "12:00",
        "open": False,
    }


@responses.activate
def test_closing_period_open_flag(aws, monkeypatch):
    prepare(aws)
    freeze(monkeypatch, BEFORE_CUTOFF)

    result = init.handler(make_event(), None)

    assert result["closing_period"]["to"] == "2026-10-21T00:00:00+09:00"
    assert result["closing_period"]["open"] is True


@responses.activate
def test_lists_all_tenant_pages(aws):
    put_settings(aws)
    put_price(aws)
    acquire()
    register_saasa(make_tenants(250))

    result = init.handler(make_event(), None)

    assert len(result["tenants"]) == 250
    assert pages_requested() == ["1", "2", "3"]


@responses.activate
def test_get_token_401_propagates(aws):
    put_settings(aws)
    put_price(aws)
    acquire()
    responses.post(f"{VERIFICATION_URL}{AUTH_PATH}", json={"errorStatus": "Unauthorized", "message": ""}, status=401)

    with pytest.raises(SaasAApiError) as raised:
        init.handler(make_event(), None)

    assert raised.value.status_code == 401
    assert repo.get_run(VERIFICATION, MONTH)["status"] == repo.RUNNING


@responses.activate
def test_tenant_attributes_are_saved_to_s3(aws):
    """`Aggregate` が使うテナント属性（名称・説明欄・保存期間）を S3 に残す（設計 7.3）。"""
    tenants = prepare(aws)

    init.handler(make_event(), None)

    assert storage.get_tenants(VERIFICATION, MONTH) == tenants
    assert storage.tenants_key(VERIFICATION, MONTH) == f"saas-a-billing/{VERIFICATION}/2026/10/tenants.json"


@responses.activate
def test_tenants_total_recorded_on_run(aws):
    prepare(aws, tenant_count=3)

    init.handler(make_event(), None)

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["tenants_total"] == 3
    assert run["tenants_done"] == 0
