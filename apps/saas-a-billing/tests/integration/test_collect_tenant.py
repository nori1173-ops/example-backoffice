"""`batch.handlers.collect_tenant` のテスト（設計 6.4 / 6.10 / 7.3 / 11.1 / 14.2）。

moto の DynamoDB / S3 / SSM に実際に読み書きし、SaaS_A API は `responses` で
差し替える。トークンは `Init` が RUN に保存済みという前提のため `getToken` は
登録しない（呼べば `responses` が接続エラーにする）。

残り時間は `get_remaining_time_in_millis` だけを持つスタブで与え、時間予算超過が
例外ではなく `failed` の記録になることを実測で確かめる。
"""

import json
import logging
from datetime import datetime
from urllib.parse import parse_qsl, urlparse

import pytest
import requests
import responses
from aws_lambda_powertools.utilities import parameters
from batch.handlers import collect_tenant
from saas_a_billing import repo, storage
from saas_a_billing.period import JST

from tests.conftest import make_device

VERIFICATION = "verification"
MONTH = "2026-09"
ATTEMPT = 1
RUN_TOKEN = "run-token-1"
ACCESS_TOKEN = "dummy-access-token-for-test"
EXPIRES_AT = "2026-09-21 08:48:25"
TENANTS_TOTAL = 3
TENANTS_DONE = 2

TENANT_ID = "00000000-0000-0000-0000-000000000011"
TENANT_CD = "TNT001"

BASE_URL = "https://saasa-verification.example.com"
TOKEN_URL = f"{BASE_URL}/api/v2/getToken"
DEVICE_LIST_URL = f"{BASE_URL}/api/v2/device/list"
GROUP_LIST_URL = f"{BASE_URL}/api/v2/group/list"
EXPORT_URL = f"{BASE_URL}/api/v2/download/export"

WATER = "10000001-01-LoggerA-400"
GAUGE = "10000002-02-LoggerA-400"
CAMERA = "30000001-01-CamA-MC"

GROUPS = [{"group_cd": "GRP001", "group_name": "現場A"}, {"group_cd": "GRP002", "group_name": "現場B"}]

HEADER_LINE = "field_name,point_cd,point_name,change_datetime,パケット種別,水位(m)"
CAMERA_HEADER_LINE = "field_name,point_cd,point_name,change_datetime,パケット種別,Base64形式データ"
FIRST_SEEN = "2026/08/22 10:00:00"
LAST_SEEN = "2026/09/19 08:30:00"

EMPTY = (200, b"")
LIMIT_OVER_BODY = json.dumps({"errorStatus": "Bad Request", "message": "Error.Export.LimitOver,123456,100000"})
LIMIT_OVER = (400, LIMIT_OVER_BODY.encode())

SEVEN_DAY_WINDOWS = [
    ("2026-08-21", "2026-08-27"),
    ("2026-08-28", "2026-09-03"),
    ("2026-09-04", "2026-09-10"),
    ("2026-09-11", "2026-09-17"),
    ("2026-09-18", "2026-09-20"),
]
FIRST_WINDOW = (*SEVEN_DAY_WINDOWS[0], "all")
LAST_WINDOW = (*SEVEN_DAY_WINDOWS[4], "all")

REMAINING_MS = 900_000
OUT_OF_TIME_MS = 1_000
NOW = datetime(2026, 9, 21, 6, 0, tzinfo=JST)

LOGGER_NAME = "example_backoffice"
METRIC_NAMES = {"TenantDuration", "ExportCalls", "ExportBytes"}


class Context:
    """Lambda コンテキストの最小スタブ。ハンドラは残り時間しか見ない。"""

    def __init__(self, remaining_ms: int = REMAINING_MS):
        self._remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self._remaining_ms


class ExportStub:
    """`download/export` の応答を (start_date, end_date, device_cd) ごとに決める。

    `calls` に呼び出しを順番に積むので、窓の分割順序をそのまま検証できる。
    """

    def __init__(self, by_key: dict[tuple[str, str, str], tuple[int, bytes]] | None = None, default=EMPTY):
        self.by_key = by_key or {}
        self.default = default
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, request):
        query = query_of(request)
        key = (query["start_date"], query["end_date"], query["device_cd"])
        self.calls.append(key)
        status, body = self.by_key.get(key, self.default)
        return status, {}, body


@pytest.fixture
def aws(dynamodb_table, s3_bucket, ssm_targets):
    """テーブル・添付バケット・検証環境の接続情報を立て、クライアントのキャッシュを捨てる。"""
    repo.reset_clients()
    storage.reset_clients()
    parameters.clear_caches()
    yield dynamodb_table
    repo.reset_clients()
    storage.reset_clients()
    parameters.clear_caches()


def query_of(request) -> dict[str, str]:
    return dict(parse_qsl(urlparse(request.url).query))


def csv_body(rows: list[tuple[str, str]], camera_rows: list[tuple[str, str]] | None = None) -> bytes:
    """フォーマットブロック（必要ならカメラブロックも）を含む CSV（cp932）。"""
    lines = [HEADER_LINE, *[f"現場A,{device_cd},観測局,{ts},定時,1.23" for device_cd, ts in rows]]
    for device_cd, ts in camera_rows or []:
        lines += [CAMERA_HEADER_LINE, f"現場A,{device_cd},カメラ,{ts},定時,AAAA"]
    return "\r\n".join(lines).encode("cp932")


def csv_page(rows: list[tuple[str, str]], camera_rows: list[tuple[str, str]] | None = None) -> tuple[int, bytes]:
    return 200, csv_body(rows, camera_rows)


def make_devices(count: int = 3) -> list[dict]:
    if count == 3:
        return [
            make_device(device_cd=WATER),
            make_device(device_cd=GAUGE, device_name="観測局2"),
            make_device(device_cd=CAMERA, device_name="カメラ地点1", format_name="CamA-MC"),
        ]
    return [make_device(device_cd=f"{index:08d}-01-LoggerA-400", device_name=f"観測局{index}") for index in range(count)]


def register_devices(devices: list[dict], per_page: int = 999) -> list[dict]:
    for offset in range(0, max(len(devices), 1), per_page):
        responses.get(
            DEVICE_LIST_URL,
            json={"data": {"count": len(devices), "rows": devices[offset : offset + per_page]}},
        )
    return devices


def register_groups(groups: list[dict] = GROUPS) -> list[dict]:
    responses.get(GROUP_LIST_URL, json={"groups": groups})
    return groups


def register_export(stub: ExportStub) -> ExportStub:
    responses.add_callback(responses.GET, EXPORT_URL, callback=stub, content_type="application/octet-stream")
    return stub


def prepare_run(tenants_done: int = TENANTS_DONE) -> None:
    """`Init` が終わった直後の RUN（`running` + トークン保持）を作る。"""
    repo.acquire_run(VERIFICATION, MONTH, trigger="scheduled", force=False, run_token=RUN_TOKEN, stale_before=NOW)
    repo.start_run(VERIFICATION, MONTH, ATTEMPT, RUN_TOKEN)
    repo.set_token(VERIFICATION, MONTH, ATTEMPT, ACCESS_TOKEN, EXPIRES_AT)
    repo.set_progress(VERIFICATION, MONTH, ATTEMPT, TENANTS_TOTAL, tenants_done)


def prepare(devices: list[dict] | None = None, stub: ExportStub | None = None) -> ExportStub:
    """RUN と `device/list` / `group/list` / `download/export` の応答を用意する。"""
    prepare_run()
    register_devices(make_devices() if devices is None else devices)
    register_groups()
    return register_export(stub or ExportStub())


def make_event(**overrides) -> dict:
    """Map の `ItemSelector` が注入する入力（設計 5.2 の 2 行目 / 6.4）。"""
    event = {
        "run_id": f"{VERIFICATION}-{MONTH}-{ATTEMPT}",
        "target_env": VERIFICATION,
        "month": MONTH,
        "tenant_id": TENANT_ID,
        "tenant_cd": TENANT_CD,
        "attempt": ATTEMPT,
        "closing_period": {
            "from": "2026-08-21T00:00:00+09:00",
            "to": "2026-09-21T00:00:00+09:00",
            "cutoff_day": 20,
            "cutoff_time": "24:00",
            "open": False,
        },
    }
    event.update(overrides)
    return event


def part_of() -> dict:
    return storage.get_part(VERIFICATION, MONTH, TENANT_CD)


def requested_pages(url: str) -> list[str]:
    return [query_of(call.request)["page"] for call in responses.calls if call.request.url.startswith(url)]


def windows_of(stub: ExportStub) -> list[tuple[str, str]]:
    return [(start, end) for start, end, _ in stub.calls]


def emf_documents(captured: str) -> list[dict]:
    """標準出力に流れた Powertools Metrics の EMF ドキュメント。"""
    documents = []
    for line in captured.splitlines():
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if "_aws" in parsed:
            documents.append(parsed)
    return documents


def emitted_names(documents: list[dict]) -> set[str]:
    groups = [group for document in documents for group in document["_aws"]["CloudWatchMetrics"]]
    return {metric["Name"] for group in groups for metric in group["Metrics"]}


def metric_value(document: dict, name: str):
    value = document[name]
    return value[0] if isinstance(value, list) else value


@responses.activate
def test_writes_part_to_s3_with_expected_key(aws):
    prepare()

    collect_tenant.handler(make_event(), Context())

    expected = f"saas-a-billing/{VERIFICATION}/2026/09/parts/{TENANT_CD}.json"
    assert storage.part_key(VERIFICATION, MONTH, TENANT_CD) == expected
    assert part_of()["tenant_cd"] == TENANT_CD


@responses.activate
def test_return_value_contains_only_counts(aws):
    prepare(stub=ExportStub({FIRST_WINDOW: csv_page([(WATER, FIRST_SEEN), (GAUGE, FIRST_SEEN)])}))

    result = collect_tenant.handler(make_event(), Context())

    assert result == {"tenant_cd": TENANT_CD, "devices": 3, "received": 2, "failed": False}


@responses.activate
def test_reads_token_from_run_item(aws):
    """`getToken` を登録しなくても動く（登録が無い URL は `responses` が接続エラーにする）。"""
    prepare()

    collect_tenant.handler(make_event(), Context())

    assert all(not call.request.url.startswith(TOKEN_URL) for call in responses.calls)
    assert responses.calls[0].request.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"


@responses.activate
def test_does_not_relogin_on_401(aws):
    """401 は当該テナントの失敗として記録し、再ログインしない（設計 6.3 / 6.4）。"""
    prepare_run()
    register_devices(make_devices())
    register_groups()
    responses.post(TOKEN_URL, json={"token_info": {"access_token": "another-token"}})
    responses.get(EXPORT_URL, json={"errorStatus": "Unauthorized", "message": ""}, status=401)

    result = collect_tenant.handler(make_event(), Context())

    assert result["failed"] is True
    assert part_of()["status"] == "failed"
    assert all(not call.request.url.startswith(TOKEN_URL) for call in responses.calls)


@responses.activate
def test_lists_all_device_pages(aws):
    prepare(devices=make_devices(1000))

    collect_tenant.handler(make_event(), Context())

    assert requested_pages(DEVICE_LIST_URL) == ["1", "2"]
    assert len(part_of()["devices"]) == 1000


@responses.activate
def test_part_contains_devices_groups_received_camera(aws):
    devices = make_devices()
    prepare(
        devices=devices,
        stub=ExportStub({FIRST_WINDOW: csv_page([(WATER, FIRST_SEEN), (GAUGE, FIRST_SEEN)], [(CAMERA, FIRST_SEEN)])}),
    )

    collect_tenant.handler(make_event(), Context())

    part = part_of()
    assert part["devices"] == devices
    assert part["groups"] == GROUPS
    assert part["received"] == sorted([WATER, GAUGE, CAMERA])
    assert part["camera"] == [CAMERA]
    assert part["status"] == "ok"
    assert part["error"] is None


@responses.activate
def test_part_contains_first_and_last_seen(aws):
    prepare(
        stub=ExportStub(
            {
                FIRST_WINDOW: csv_page([(WATER, FIRST_SEEN)]),
                LAST_WINDOW: csv_page([(WATER, LAST_SEEN)]),
            }
        )
    )

    collect_tenant.handler(make_event(), Context())

    part = part_of()
    assert part["first_seen"] == {WATER: FIRST_SEEN}
    assert part["last_seen"] == {WATER: LAST_SEEN}


@responses.activate
def test_export_uses_seven_day_windows(aws):
    """課金対象期間 8/21〜9/20 を 7 日窓で切ると 5 回（設計 6.4）。"""
    stub = prepare()

    collect_tenant.handler(make_event(), Context())

    assert windows_of(stub) == SEVEN_DAY_WINDOWS
    assert part_of()["export_calls"] == 5


@responses.activate
def test_export_window_days_env_override(aws, monkeypatch):
    monkeypatch.setenv("EXPORT_WINDOW_DAYS", "3")
    stub = prepare()

    collect_tenant.handler(make_event(), Context())

    assert len(stub.calls) == 11
    assert windows_of(stub)[0] == ("2026-08-21", "2026-08-23")
    assert windows_of(stub)[-1] == ("2026-09-20", "2026-09-20")


@responses.activate
def test_limit_over_splits_window(aws):
    stub = prepare(stub=ExportStub({FIRST_WINDOW: LIMIT_OVER}))

    collect_tenant.handler(make_event(), Context())

    assert windows_of(stub)[:3] == [
        ("2026-08-21", "2026-08-27"),
        ("2026-08-21", "2026-08-23"),
        ("2026-08-24", "2026-08-27"),
    ]
    assert part_of()["export_calls"] == 7


@responses.activate
def test_time_budget_exceeded_marks_failed_with_reason(aws):
    """時間予算超過は例外にせず `failed` として記録する（設計 6.10 / 11.1）。"""
    prepare()

    result = collect_tenant.handler(make_event(), Context(remaining_ms=OUT_OF_TIME_MS))

    part = part_of()
    assert part["status"] == "failed"
    assert part["error"] == "TIME_BUDGET_EXCEEDED"
    assert result["failed"] is True
    assert len(part["devices"]) == 3


@responses.activate
def test_transient_5xx_raises_for_state_machine_retry(aws):
    """5xx はステートマシンの `Retry` に載せるため例外のまま上げる（設計 11.1）。"""
    prepare_run()
    responses.get(DEVICE_LIST_URL, json={"errorStatus": "Internal Server Error", "message": ""}, status=500)

    with pytest.raises(requests.RequestException):
        collect_tenant.handler(make_event(), Context())

    with pytest.raises(storage.PartNotFoundError):
        part_of()


@responses.activate
def test_device_list_failure_writes_empty_devices(aws):
    """`device/list` 自体に失敗した場合は空配列（設計 7.3）。"""
    prepare_run()
    responses.get(DEVICE_LIST_URL, json={"errorStatus": "Forbidden", "message": ""}, status=403)

    result = collect_tenant.handler(make_event(), Context())

    part = part_of()
    assert part["devices"] == []
    assert part["status"] == "failed"
    assert "403" in part["error"]
    assert result == {"tenant_cd": TENANT_CD, "devices": 0, "received": 0, "failed": True}


@responses.activate
def test_progress_is_updated_on_run(aws):
    prepare()

    collect_tenant.handler(make_event(), Context())

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["tenants_done"] == TENANTS_DONE + 1
    assert run["tenants_total"] == TENANTS_TOTAL


@responses.activate
def test_metrics_are_emitted(aws, capsys):
    prepare(stub=ExportStub({FIRST_WINDOW: csv_page([(WATER, FIRST_SEEN)])}))

    collect_tenant.handler(make_event(), Context())

    documents = emf_documents(capsys.readouterr().out)
    assert METRIC_NAMES.issubset(emitted_names(documents))
    assert documents[0]["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "example-backoffice/saas-a-billing"
    assert metric_value(documents[0], "ExportCalls") == 5
    assert metric_value(documents[0], "ExportBytes") == part_of()["export_bytes"]


@responses.activate
def test_token_is_not_logged(aws, caplog):
    """アプリのロガーを最も詳細にしてもトークンが現れない（設計 12.3）。"""
    prepare()

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        collect_tenant.handler(make_event(), Context())

    assert "テナントの収集を終えました" in caplog.text
    assert ACCESS_TOKEN not in caplog.text
    assert ACCESS_TOKEN not in json.dumps(part_of(), ensure_ascii=False)
