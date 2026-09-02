"""`saasa_client.py` のユニットテスト（設計 4 章 / 6.4 / 12.3 / 13 章）。

HTTP は `responses` でモックする（実 API を呼ばない）。再試行は `responses` が
アダプタの `max_retries` を解釈するため、`OrderedRegistry` で応答を順番に積む。
"""

import json
import logging
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlparse

import boto3
import pytest
import requests
import responses
from aws_lambda_powertools.utilities import parameters
from saas_a_billing.saasa_client import (
    LimitOverError,
    SaasAApiError,
    SaasAClient,
    SaasACredentials,
    TargetEnvNotConfiguredError,
    TimeBudgetExceeded,
    collect_export,
)
from saas_a_billing.period import JST, ClosingPeriod
from responses import registries

from tests.conftest import REGION, SSM_PREFIX, make_device, make_tenant

BASE_URL = "https://saasa.example.com"
TOKEN_URL = f"{BASE_URL}/api/v2/getToken"
TENANT_LIST_URL = f"{BASE_URL}/api/v2/tenant/list"
GROUP_LIST_URL = f"{BASE_URL}/api/v2/group/list"
DEVICE_LIST_URL = f"{BASE_URL}/api/v2/device/list"
EXPORT_URL = f"{BASE_URL}/api/v2/download/export"

TENANT_ID = "00000000-0000-0000-0000-000000000011"
USER_ID = "billing-bot"
PASSWORD = "dummy-passphrase-for-test"
ACCESS_TOKEN = "dummy-access-token-for-test"
EXPIRES_AT = "2026-06-15 08:48:25"

DEVICE_CDS = ["10000001-01-LoggerA-400", "10000002-02-LoggerA-400", "10000003-03-CamA-MC"]

HEADER_LINE = "field_name,point_cd,point_name,change_datetime,パケット種別,水位(m)"
NO_RECORDS_BODY = json.dumps({"errorStatus": "Error.Export.NoRecords", "message": ""}).encode()
LIMIT_OVER_BODY = json.dumps({"errorStatus": "Bad Request", "message": "Error.Export.LimitOver,123456,100000"}).encode()
LIMIT_OVER = (400, LIMIT_OVER_BODY)
NO_RECORDS = (200, NO_RECORDS_BODY)
EMPTY = (200, b"")


def make_period(first: date, last: date) -> ClosingPeriod:
    """`first` 〜 `last`（両端を含む）を課金対象期間とする `ClosingPeriod`。"""
    start = datetime(first.year, first.month, first.day, tzinfo=JST)
    end_exclusive = datetime(last.year, last.month, last.day, tzinfo=JST) + timedelta(days=1)
    return ClosingPeriod(
        start=start,
        end_exclusive=end_exclusive,
        cutoff_date=last,
        cutoff_day=last.day,
        cutoff_time="24:00",
    )


WEEK = make_period(date(2026, 8, 21), date(2026, 8, 27))
ONE_DAY = make_period(date(2026, 8, 21), date(2026, 8, 21))
MONTH = make_period(date(2026, 8, 21), date(2026, 9, 20))


def csv_body(*device_cds: str, ts: str = "2026/08/22 10:00:00") -> bytes:
    """フォーマットブロック 1 つ分の CSV（cp932）。"""
    lines = [HEADER_LINE, *[f"現場A,{cd},観測局,{ts},定時,1.23" for cd in device_cds]]
    return "\r\n".join(lines).encode("cp932")


def csv_page(*device_cds: str, ts: str = "2026/08/22 10:00:00") -> tuple[int, bytes]:
    return 200, csv_body(*device_cds, ts=ts)


def query_of(request) -> dict[str, str]:
    return dict(parse_qsl(urlparse(request.url).query))


class ExportStub:
    """`download/export` の応答を (start_date, end_date, device_cd) ごとに決める。

    `calls` に呼び出しを順番に積むので、窓の分割順序をそのまま検証できる。
    """

    def __init__(self, by_key: dict[tuple[str, str, str], tuple[int, bytes]], default: tuple[int, bytes] = EMPTY):
        self.by_key = by_key
        self.default = default
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, request):
        query = query_of(request)
        key = (query["start_date"], query["end_date"], query["device_cd"])
        self.calls.append(key)
        status, body = self.by_key.get(key, self.default)
        return status, {}, body


def register_export(stub: ExportStub) -> ExportStub:
    responses.add_callback(responses.GET, EXPORT_URL, callback=stub, content_type="application/octet-stream")
    return stub


def make_client(token: str | None = ACCESS_TOKEN) -> SaasAClient:
    return SaasAClient(BASE_URL, token=token)


def plenty_of_time() -> float:
    return 600.0


def out_of_time() -> float:
    return -1.0


def token_body() -> dict:
    return {
        "userInfo": {"user_cd": USER_ID, "level": 1},
        "token_info": {
            "token_type": "Bearer",
            "access_token": ACCESS_TOKEN,
            "refresh_token": "dummy-refresh-token-for-test",
            "expires_at": EXPIRES_AT,
        },
    }


def tenant_page(rows: list[dict], count: int) -> dict:
    return {"data": {"count": count, "rows": rows}}


@pytest.fixture(autouse=True)
def _reset_parameter_cache():
    """Powertools のパラメータキャッシュと既定プロバイダをテストごとに捨てる。"""
    parameters.clear_caches()
    yield
    parameters.clear_caches()


@responses.activate
def test_get_token_returns_access_token_and_expires_at():
    responses.post(TOKEN_URL, json=token_body())

    access_token, expires_at = make_client(token=None).get_token(USER_ID, PASSWORD)

    assert (access_token, expires_at) == (ACCESS_TOKEN, EXPIRES_AT)
    assert json.loads(responses.calls[0].request.body) == {"user_cd": USER_ID, "passwd": PASSWORD}


@responses.activate
def test_get_token_401_raises_without_retry():
    responses.post(TOKEN_URL, json={"errorStatus": "Unauthorized", "message": ""}, status=401)

    with pytest.raises(SaasAApiError) as raised:
        make_client(token=None).get_token(USER_ID, PASSWORD)

    assert raised.value.status_code == 401
    assert len(responses.calls) == 1


@responses.activate
def test_get_token_does_not_log_password(caplog):
    responses.post(TOKEN_URL, json=token_body())

    with caplog.at_level(logging.DEBUG):
        make_client(token=None).get_token(USER_ID, PASSWORD)

    assert PASSWORD not in caplog.text
    assert ACCESS_TOKEN not in caplog.text


@responses.activate
def test_list_tenants_single_page():
    rows = [make_tenant(tenant_cd=f"TNT{index:03d}") for index in range(3)]
    responses.get(TENANT_LIST_URL, json=tenant_page(rows, count=3))

    tenants = make_client().list_tenants()

    assert [tenant["tenant_cd"] for tenant in tenants] == ["TNT000", "TNT001", "TNT002"]
    assert query_of(responses.calls[0].request)["perPage"] == "100"
    assert responses.calls[0].request.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"


@responses.activate
def test_list_tenants_pages_until_count_reached():
    rows = [make_tenant(tenant_cd=f"TNT{index:03d}") for index in range(250)]
    for offset in (0, 100, 200):
        responses.get(TENANT_LIST_URL, json=tenant_page(rows[offset : offset + 100], count=250))

    tenants = make_client().list_tenants()

    assert len(tenants) == 250
    assert [query_of(call.request)["page"] for call in responses.calls] == ["1", "2", "3"]


@responses.activate
def test_list_devices_uses_per_page_999():
    responses.get(DEVICE_LIST_URL, json=tenant_page([make_device()], count=1))

    make_client().list_devices(TENANT_ID)

    query = query_of(responses.calls[0].request)
    assert query["perPage"] == "999"
    assert query["tenant_id"] == TENANT_ID


@responses.activate
def test_list_devices_pages_until_count_reached():
    rows = [make_device(device_cd=f"1000{index:04d}-01-LoggerA-400") for index in range(1200)]
    for offset in (0, 999):
        responses.get(DEVICE_LIST_URL, json=tenant_page(rows[offset : offset + 999], count=1200))

    devices = make_client().list_devices(TENANT_ID)

    assert len(devices) == 1200
    assert [query_of(call.request)["page"] for call in responses.calls] == ["1", "2"]


@responses.activate
def test_list_groups_returns_groups_array():
    groups = [{"id": "00000000-0000-0000-0000-0000000000g1", "name": "現場A", "group_cd": "GRP001"}]
    responses.get(GROUP_LIST_URL, json={"groups": groups})

    assert make_client().list_groups(TENANT_ID) == groups
    assert query_of(responses.calls[0].request) == {"tenant_id": TENANT_ID}


@responses.activate
def test_export_window_builds_expected_query():
    responses.get(EXPORT_URL, body=csv_body(DEVICE_CDS[0]))

    with make_client().export_window(TENANT_ID, date(2026, 8, 21), date(2026, 8, 27)) as response:
        assert response.status_code == 200

    assert query_of(responses.calls[0].request) == {
        "tenant_id": TENANT_ID,
        "start_date": "2026-08-21",
        "end_date": "2026-08-27",
        "group_cd": "all",
        "device_cd": "all",
        "push_type": "csv",
        "output_type": "raw_data",
    }
    assert responses.calls[0].request.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"


@responses.activate
def test_export_window_limit_over_raises_with_counts():
    responses.get(EXPORT_URL, body=LIMIT_OVER_BODY, status=400, content_type="application/json")

    with pytest.raises(LimitOverError) as raised:
        make_client().export_window(TENANT_ID, date(2026, 8, 21), date(2026, 8, 27))

    assert (raised.value.result_count, raised.value.limit_count) == (123456, 100000)
    assert raised.value.status_code == 400


@responses.activate
def test_collect_export_splits_window_on_limit_over():
    stub = register_export(
        ExportStub(
            {
                ("2026-08-21", "2026-08-27", "all"): LIMIT_OVER,
                ("2026-08-21", "2026-08-23", "all"): csv_page(DEVICE_CDS[0]),
                ("2026-08-24", "2026-08-27", "all"): csv_page(DEVICE_CDS[1], ts="2026/08/25 10:00:00"),
            }
        )
    )

    result = collect_export(make_client(), TENANT_ID, DEVICE_CDS, WEEK, plenty_of_time, window_days=7)

    assert stub.calls == [
        ("2026-08-21", "2026-08-27", "all"),
        ("2026-08-21", "2026-08-23", "all"),
        ("2026-08-24", "2026-08-27", "all"),
    ]
    assert result.received == {DEVICE_CDS[0], DEVICE_CDS[1]}


@responses.activate
def test_collect_export_recursive_split_until_one_day():
    stub = register_export(
        ExportStub(
            {
                ("2026-08-21", "2026-08-27", "all"): LIMIT_OVER,
                ("2026-08-21", "2026-08-23", "all"): LIMIT_OVER,
                ("2026-08-21", "2026-08-21", "all"): csv_page(DEVICE_CDS[0], ts="2026/08/21 10:00:00"),
            }
        )
    )

    collect_export(make_client(), TENANT_ID, DEVICE_CDS, WEEK, plenty_of_time, window_days=7)

    assert stub.calls == [
        ("2026-08-21", "2026-08-27", "all"),
        ("2026-08-21", "2026-08-23", "all"),
        ("2026-08-21", "2026-08-21", "all"),
        ("2026-08-22", "2026-08-23", "all"),
        ("2026-08-24", "2026-08-27", "all"),
    ]


@responses.activate
def test_collect_export_falls_back_to_per_device_when_single_day_over():
    stub = register_export(
        ExportStub(
            {
                ("2026-08-21", "2026-08-21", "all"): LIMIT_OVER,
                ("2026-08-21", "2026-08-21", DEVICE_CDS[0]): csv_page(DEVICE_CDS[0], ts="2026/08/21 10:00:00"),
                ("2026-08-21", "2026-08-21", DEVICE_CDS[1]): NO_RECORDS,
                ("2026-08-21", "2026-08-21", DEVICE_CDS[2]): EMPTY,
            }
        )
    )

    result = collect_export(make_client(), TENANT_ID, DEVICE_CDS, ONE_DAY, plenty_of_time, window_days=7)

    assert stub.calls == [
        ("2026-08-21", "2026-08-21", "all"),
        ("2026-08-21", "2026-08-21", DEVICE_CDS[0]),
        ("2026-08-21", "2026-08-21", DEVICE_CDS[1]),
        ("2026-08-21", "2026-08-21", DEVICE_CDS[2]),
    ]
    assert result.received == {DEVICE_CDS[0]}


@responses.activate
def test_collect_export_per_device_limit_over_marks_received():
    register_export(
        ExportStub(
            {
                ("2026-08-21", "2026-08-21", "all"): LIMIT_OVER,
                ("2026-08-21", "2026-08-21", DEVICE_CDS[1]): LIMIT_OVER,
            }
        )
    )

    result = collect_export(make_client(), TENANT_ID, DEVICE_CDS, ONE_DAY, plenty_of_time, window_days=7)

    assert result.received == {DEVICE_CDS[1]}
    assert DEVICE_CDS[1] not in result.first_seen


@responses.activate
def test_collect_export_skips_already_received_devices_in_per_device_mode():
    stub = register_export(
        ExportStub(
            {
                ("2026-08-21", "2026-08-21", "all"): csv_page(DEVICE_CDS[0], ts="2026/08/21 10:00:00"),
                ("2026-08-22", "2026-08-22", "all"): LIMIT_OVER,
            }
        )
    )
    period = make_period(date(2026, 8, 21), date(2026, 8, 22))

    result = collect_export(make_client(), TENANT_ID, DEVICE_CDS, period, plenty_of_time, window_days=1)

    assert stub.calls == [
        ("2026-08-21", "2026-08-21", "all"),
        ("2026-08-22", "2026-08-22", "all"),
        ("2026-08-22", "2026-08-22", DEVICE_CDS[1]),
        ("2026-08-22", "2026-08-22", DEVICE_CDS[2]),
    ]
    assert result.received == {DEVICE_CDS[0]}


@responses.activate
def test_collect_export_handles_no_records_json():
    register_export(ExportStub({("2026-08-21", "2026-08-21", "all"): NO_RECORDS}))

    result = collect_export(make_client(), TENANT_ID, DEVICE_CDS, ONE_DAY, plenty_of_time, window_days=7)

    assert result.received == set()
    assert result.rows == 0


@responses.activate
def test_collect_export_handles_empty_body():
    register_export(ExportStub({("2026-08-21", "2026-08-21", "all"): EMPTY}))

    result = collect_export(make_client(), TENANT_ID, DEVICE_CDS, ONE_DAY, plenty_of_time, window_days=7)

    assert result.received == set()
    assert result.rows == 0


@responses.activate
def test_collect_export_merges_all_windows():
    windows_and_rows = {
        ("2026-08-21", "2026-08-27"): ("W1", "2026/08/21 01:00:00"),
        ("2026-08-28", "2026-09-03"): ("W2", "2026/08/28 01:00:00"),
        ("2026-09-04", "2026-09-10"): ("W3", "2026/09/04 01:00:00"),
        ("2026-09-11", "2026-09-17"): ("W4", "2026/09/11 01:00:00"),
        ("2026-09-18", "2026-09-20"): ("W5", "2026/09/20 23:59:59"),
    }
    stub = register_export(
        ExportStub({(*window, "all"): csv_page(cd, ts=ts) for window, (cd, ts) in windows_and_rows.items()})
    )

    result = collect_export(make_client(), TENANT_ID, DEVICE_CDS, MONTH, plenty_of_time, window_days=7)

    assert len(stub.calls) == 5
    assert result.received == {"W1", "W2", "W3", "W4", "W5"}
    assert result.rows == 5
    assert result.first_seen["W1"] == "2026/08/21 01:00:00"
    assert result.last_seen["W5"] == "2026/09/20 23:59:59"


@responses.activate
def test_collect_export_raises_time_budget_exceeded():
    stub = register_export(ExportStub({}))

    with pytest.raises(TimeBudgetExceeded):
        collect_export(make_client(), TENANT_ID, DEVICE_CDS, WEEK, out_of_time, window_days=7)

    assert stub.calls == []


@responses.activate
def test_collect_export_uses_export_window_days_env(monkeypatch):
    monkeypatch.setenv("EXPORT_WINDOW_DAYS", "1")
    stub = register_export(ExportStub({}))

    collect_export(make_client(), TENANT_ID, DEVICE_CDS, WEEK, plenty_of_time)

    assert [call[0] for call in stub.calls] == [f"2026-08-{day}" for day in range(21, 28)]


@responses.activate(registry=registries.OrderedRegistry)
def test_retries_on_429_and_5xx():
    responses.get(TENANT_LIST_URL, json={}, status=429)
    responses.get(TENANT_LIST_URL, json={}, status=500)
    responses.get(TENANT_LIST_URL, json=tenant_page([make_tenant()], count=1))

    tenants = make_client().list_tenants()

    assert len(tenants) == 1
    assert len(responses.calls) == 3


@responses.activate
def test_does_not_retry_on_401():
    responses.get(TENANT_LIST_URL, json={"errorStatus": "Unauthorized", "message": ""}, status=401)

    with pytest.raises(SaasAApiError) as raised:
        make_client().list_tenants()

    assert raised.value.status_code == 401
    assert len(responses.calls) == 1


def test_from_ssm_reads_three_parameters(ssm_targets):
    credentials = SaasACredentials.from_ssm(SSM_PREFIX, "verification")

    assert credentials.base_url == "https://saasa-verification.example.com"
    assert credentials.user_id == "dummy-user"
    assert credentials.password == "dummy-password"


def test_from_ssm_raises_when_any_parameter_missing(aws_mock):
    client = boto3.client("ssm", region_name=REGION)
    prefix = f"{SSM_PREFIX}/targets/production"
    client.put_parameter(Name=f"{prefix}/base-url", Value=BASE_URL, Type="SecureString")
    client.put_parameter(Name=f"{prefix}/user-id", Value=USER_ID, Type="SecureString")

    with pytest.raises(TargetEnvNotConfiguredError) as raised:
        SaasACredentials.from_ssm(SSM_PREFIX, "production")

    assert "password" in str(raised.value)


def test_from_ssm_uses_target_env_in_key(ssm_targets):
    client = boto3.client("ssm", region_name=REGION)
    prefix = f"{SSM_PREFIX}/targets/production"
    for leaf, value in (("base-url", BASE_URL), ("user-id", USER_ID), ("password", PASSWORD)):
        client.put_parameter(Name=f"{prefix}/{leaf}", Value=value, Type="SecureString")

    verification = SaasACredentials.from_ssm(SSM_PREFIX, "verification")
    production = SaasACredentials.from_ssm(SSM_PREFIX, "production")

    assert verification.base_url == "https://saasa-verification.example.com"
    assert production.base_url == BASE_URL


def test_credentials_repr_hides_secrets():
    credentials = SaasACredentials(base_url=BASE_URL, user_id=USER_ID, password=PASSWORD)

    assert USER_ID not in repr(credentials)
    assert PASSWORD not in repr(credentials)
    assert BASE_URL in repr(credentials)


@responses.activate
def test_export_window_returns_streaming_response():
    responses.get(EXPORT_URL, body=csv_body(DEVICE_CDS[0]))

    with make_client().export_window(TENANT_ID, date(2026, 8, 21), date(2026, 8, 27)) as response:
        assert isinstance(response, requests.Response)
        assert next(response.iter_lines()).decode("cp932") == HEADER_LINE
