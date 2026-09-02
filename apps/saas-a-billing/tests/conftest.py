"""saas-a-billing のテスト共通フィクスチャ（設計 10 章の環境変数表に対応）。

リソースに依存しない環境変数は autouse の `_lambda_environment` が入れる。
`TABLE_NAME` / `ATTACHMENTS_BUCKET` / `NOTIFY_QUEUE_URL` / `STATE_MACHINE_ARN` は
それぞれのリソース fixture が moto でリソースを作ってから設定する。
"""

import json
import pathlib
from urllib.parse import urlencode

import boto3
import pytest
from moto import mock_aws

REGION = "ap-northeast-1"
ENV = "dev"
TARGET_ENV = "verification"
APP_ID = "saas-a-billing"
FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"
SSM_PREFIX = f"/example-backoffice/{ENV}/apps/{APP_ID}"
API_PREFIX = f"/api/{APP_ID}"
TABLE_NAME = f"example-backoffice-{APP_ID}-data-{ENV}"
ATTACHMENTS_BUCKET = f"example-backoffice-attachments-{ENV}-000000000000"
NOTIFY_QUEUE_NAME = f"example-backoffice-notify-queue-{ENV}"
STATE_MACHINE_NAME = f"{APP_ID}-run-{ENV}"
ORIGIN_VERIFY_SECRET = "test-origin-verify"

STATE_MACHINE_DEFINITION = {
    "Comment": "テスト用のダミー定義",
    "StartAt": "Done",
    "States": {"Done": {"Type": "Pass", "End": True}},
}


@pytest.fixture(autouse=True)
def _aws_credentials(monkeypatch):
    """ダミー認証情報とリソース非依存の Lambda 環境変数。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("ENV", ENV)
    monkeypatch.setenv("TARGET_ENV", TARGET_ENV)
    monkeypatch.setenv("APP_NAME", APP_ID)
    monkeypatch.setenv("S3_PREFIX", APP_ID)
    monkeypatch.setenv("SSM_PREFIX", SSM_PREFIX)
    monkeypatch.setenv("PORTAL_URL", "https://backoffice-dev.example-cloud.com")
    monkeypatch.setenv("API_PREFIX", API_PREFIX)
    monkeypatch.setenv("EXPORT_WINDOW_DAYS", "7")
    monkeypatch.setenv("TIME_BUDGET_MARGIN_SEC", "150")


@pytest.fixture
def aws_mock():
    """moto のモックを 1 テストにつき 1 回だけ張る（リソース fixture 間で状態を共有する）。"""
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_table(aws_mock, monkeypatch):
    """PK/SK・PAY_PER_REQUEST のデータテーブルを作り `TABLE_NAME` を設定する。"""
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    monkeypatch.setenv("TABLE_NAME", TABLE_NAME)
    return table


@pytest.fixture
def s3_bucket(aws_mock, monkeypatch):
    """core の添付バケットを作り `ATTACHMENTS_BUCKET` を設定する。バケット名を返す。"""
    client = boto3.client("s3", region_name=REGION)
    client.create_bucket(
        Bucket=ATTACHMENTS_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    monkeypatch.setenv("ATTACHMENTS_BUCKET", ATTACHMENTS_BUCKET)
    return ATTACHMENTS_BUCKET


@pytest.fixture
def notify_queue(aws_mock, monkeypatch):
    """core の通知キューを作り `NOTIFY_QUEUE_URL` を設定する。キュー URL を返す。"""
    client = boto3.client("sqs", region_name=REGION)
    queue_url = client.create_queue(QueueName=NOTIFY_QUEUE_NAME)["QueueUrl"]
    monkeypatch.setenv("NOTIFY_QUEUE_URL", queue_url)
    return queue_url


@pytest.fixture
def state_machine(aws_mock, monkeypatch):
    """ダミーのステートマシンを作り `STATE_MACHINE_ARN` を設定する。ARN を返す。"""
    client = boto3.client("stepfunctions", region_name=REGION)
    arn = client.create_state_machine(
        name=STATE_MACHINE_NAME,
        definition=json.dumps(STATE_MACHINE_DEFINITION),
        roleArn=f"arn:aws:iam::000000000000:role/{STATE_MACHINE_NAME}-role",
    )["stateMachineArn"]
    monkeypatch.setenv("STATE_MACHINE_ARN", arn)
    return arn


@pytest.fixture
def ssm_targets(aws_mock):
    """検証環境の接続情報（ダミー）と Slack 動作方式を SSM に登録する。接続情報のプレフィックスを返す。"""
    client = boto3.client("ssm", region_name=REGION)
    prefix = f"{SSM_PREFIX}/targets/verification"
    for leaf, value in (
        ("base-url", "https://saasa-verification.example.com"),
        ("user-id", "dummy-user"),
        ("password", "dummy-password"),
    ):
        client.put_parameter(Name=f"{prefix}/{leaf}", Value=value, Type="SecureString")
    client.put_parameter(Name=f"/example-backoffice/{ENV}/shared/slack/mode", Value="webhook", Type="String")
    return prefix


def make_api_event(
    method: str,
    path: str,
    body: dict | None = None,
    query: dict | None = None,
    groups: list[str] | None = None,
    email: str = "devA@example.co.jp",
    sub: str = "test-sub",
) -> dict:
    """HTTP API（payload format 2.0）のイベントを作る。`path` は `API_PREFIX` からの相対パス。"""
    full_path = f"{API_PREFIX}{path}"
    raw_query = urlencode(query or {})
    return {
        "version": "2.0",
        "routeKey": f"ANY {API_PREFIX}/" + "{proxy+}",
        "rawPath": full_path,
        "rawQueryString": raw_query,
        "headers": {"content-type": "application/json", "x-origin-verify": ORIGIN_VERIFY_SECRET},
        "queryStringParameters": dict(query) if query else None,
        "requestContext": {
            "accountId": "000000000000",
            "apiId": "testapi",
            "domainName": "testapi.execute-api.ap-northeast-1.amazonaws.com",
            "http": {"method": method, "path": full_path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.1"},
            "requestId": "test-request-id",
            "stage": "$default",
            "authorizer": {
                "jwt": {"claims": {"sub": sub, "email": email, "cognito:groups": f"[{' '.join(groups or [])}]"}}
            },
        },
        "body": json.dumps(body, ensure_ascii=False) if body is not None else None,
        "isBase64Encoded": False,
    }


def make_settings(**overrides) -> dict:
    """`SETTINGS#current` の既定値（設計 3.3）。"""
    settings = {
        "target_env": "verification",
        "cutoff_day": 20,
        "cutoff_time": "24:00",
        "auto_retry_days": 5,
        "auto_run_enabled": False,
        "auto_run_from_month": "2026-08",
        "mail_enabled": False,
        "slack_enabled": False,
        "mail_to": ["devA@example.co.jp"],
        "mail_cc": [],
        "slack_channel_id": "",
        "slack_channel_label": "#proj-saas-a-billing",
        "slack_mention": "",
        "slack_template": (
            "{month} の SaaS_A 月額課金レポートを送付しました。"
            "ベンダーA請求書との照合を忘れずに。合計 {total}（課金 {billable} 地点）"
        ),
        "exclude_keywords": ["デモ"],
        "camera_format_names": [],
        "version": 0,
        "updated_at": "2026-08-01T00:00:00+09:00",
        "updated_by": "test-sub",
        "updated_by_email": "devA@example.co.jp",
    }
    settings.update(overrides)
    return settings


def make_price_table(**overrides) -> dict:
    """単価表の既定版（設計 2.3 / support-tracker #2001）。"""
    price_table = {
        "effective_from": "2025-09-01",
        "bands": [
            {"max_items": 500, "price_3y": 650, "price_1y": 450},
            {"max_items": 800, "price_3y": 1000, "price_1y": 700},
            {"max_items": 1600, "price_3y": 1200, "price_1y": 900},
            {"max_items": 1700, "price_3y": 1350, "price_1y": 1000},
        ],
        "camera": {"price_3y": 1800, "price_1y": 1400},
        "note": "support-tracker #2001",
        "created_at": "2025-09-01T00:00:00+09:00",
        "created_by": "test-sub",
        "created_by_email": "devA@example.co.jp",
    }
    price_table.update(overrides)
    return price_table


def make_device(**overrides) -> dict:
    """`device/list` の 1 行（設計 4 章の応答形）。"""
    device = {
        "device_id": "00000000-0000-0000-0000-000000000101",
        "device_cd": "10000001-01-LoggerA-400",
        "device_name": "観測局1",
        "device_description": "",
        "format_id": "00000000-0000-0000-0000-0000000000f1",
        "format_name": "LoggerA-400",
        "format_detail": [{"item_name": "水位"}, {"item_name": "電圧"}, {"item_name": "気温"}],
        "group_cds": ["GRP001"],
        "group_names": ["現場A"],
    }
    device.update(overrides)
    return device


def make_tenant(**overrides) -> dict:
    """`tenant/list` の 1 行（設計 4 章の応答形）。"""
    tenant = {
        "tenant_id": "00000000-0000-0000-0000-000000000011",
        "tenant_cd": "TNT001",
        "tenant_name": "サンプル建設",
        "tenant_description": "",
        "retention_period": 3,
        "device_count": 4,
    }
    tenant.update(overrides)
    return tenant


def make_result_device(**overrides) -> dict:
    """`result.json` の `tenants[].devices[]` 1 件分（設計 7.3）。"""
    device = {
        "device_cd": "10000001-01-LoggerA-400",
        "device_name": "観測局1",
        "group_cds": ["GRP001"],
        "group_names": ["現場A"],
        "format_name": "LoggerA-400",
        "item_count": 3,
        "device_description": "",
        "is_camera": False,
        "camera_source": None,
        "retention": 3,
        "received": True,
        "first_received_at": "2026/09/21 00:05:00",
        "last_received_at": "2026/10/20 23:55:00",
        "boundary_only": False,
        "judgement": "billable",
        "unit_price": 750,
        "amount": 750,
        "flags": [],
        "note": "",
    }
    device.update(overrides)
    return device


def make_result_tenant(**overrides) -> dict:
    """`result.json` の `tenants[]` 1 件分（設計 7.3）。"""
    tenant = {
        "tenant_id": "00000000-0000-0000-0000-000000000011",
        "tenant_cd": "TNT001",
        "tenant_name": "サンプル建設",
        "tenant_description": "",
        "retention_period": 3,
        "device_count_api": 1,
        "status": "ok",
        "error": None,
        "export_calls": 5,
        "export_bytes": 1024,
        "subtotal": 0,
        "counts": {"billable": 0, "no_data": 0, "excluded": 0, "review": 0},
        "devices": [],
    }
    tenant.update(overrides)
    return tenant


def _sample_result_tenants() -> list[dict]:
    """`tenant_cd` 昇順でない 5 テナント（並べ替え・判定・色分けの検証用）。"""
    return [
        make_result_tenant(
            tenant_cd="TNT002",
            tenant_name="第二テナント",
            retention_period=1,
            device_count_api=3,
            subtotal=3000,
            counts={"billable": 3, "no_data": 0, "excluded": 0, "review": 1},
            devices=[
                make_result_device(
                    device_cd="20000001-01-CamA-MC",
                    device_name="カメラ地点1",
                    group_cds=["GRP101"],
                    group_names=["現場D"],
                    format_name="CamA-MC",
                    item_count=5,
                    is_camera=True,
                    camera_source="export",
                    retention=1,
                    first_received_at="2026/09/21 08:00:00",
                    last_received_at="2026/09/21 08:00:00",
                    boundary_only=True,
                    unit_price=1500,
                    amount=1500,
                    note="境界日のみ受信",
                ),
                make_result_device(
                    device_cd="20000002-01-CamA-MC-2",
                    device_name="カメラ地点2",
                    group_cds=["GRP101"],
                    group_names=["現場D"],
                    format_name="CamA-MC-2",
                    item_count=4,
                    is_camera=True,
                    camera_source="settings",
                    retention=1,
                    unit_price=1500,
                    amount=1500,
                ),
                make_result_device(
                    device_cd="20000003-01-LoggerA-400",
                    device_name="観測局6",
                    group_cds=["GRP102"],
                    group_names=["現場E"],
                    item_count=0,
                    retention=1,
                    unit_price=0,
                    amount=0,
                    flags=["NO_ITEMS"],
                ),
            ],
        ),
        make_result_tenant(
            tenant_cd="TNT005",
            tenant_name="取得失敗テナント",
            status="failed",
            error="HTTPError: 500 Internal Server Error",
            device_count_api=1,
            devices=[
                make_result_device(
                    device_cd="50000001-01-LoggerA-400",
                    device_name="観測局9",
                    group_cds=["GRP501"],
                    group_names=["現場F"],
                    received=False,
                    first_received_at=None,
                    last_received_at=None,
                    judgement="failed",
                    amount=0,
                )
            ],
        ),
        make_result_tenant(
            tenant_cd="TNT001",
            device_count_api=5,
            subtotal=3450,
            counts={"billable": 3, "no_data": 1, "excluded": 1, "review": 2},
            devices=[
                make_result_device(group_cds=["GRP002"], group_names=["現場B"]),
                make_result_device(
                    device_cd="10000002-01-LoggerA-400",
                    device_name="観測局2",
                    device_description="堤防右岸",
                    item_count=984,
                    unit_price=1300,
                    amount=1300,
                ),
                make_result_device(
                    device_cd="10000003-01-LoggerA-400",
                    device_name="観測局3",
                    item_count=0,
                    received=False,
                    first_received_at=None,
                    last_received_at=None,
                    judgement="no_data",
                    unit_price=0,
                    amount=0,
                    flags=["NO_ITEMS"],
                ),
                make_result_device(
                    device_cd="10000004-01-LoggerA-400",
                    device_name="デモ機A",
                    device_description="デモ機",
                    judgement="excluded",
                    amount=0,
                ),
                make_result_device(
                    device_cd="10000005-01-LoggerA-400",
                    device_name="観測局5",
                    group_cds=["GRP003"],
                    group_names=["現場C"],
                    item_count=2000,
                    unit_price=1400,
                    amount=1400,
                    flags=["OVER_MAX_ITEMS"],
                ),
            ],
        ),
        make_result_tenant(
            tenant_cd="TNT004",
            tenant_name="未設定テナント",
            retention_period=None,
            subtotal=750,
            counts={"billable": 1, "no_data": 0, "excluded": 0, "review": 1},
            devices=[
                make_result_device(
                    device_cd="40000001-01-LoggerA-400",
                    device_name="観測局8",
                    group_cds=["GRP401"],
                    group_names=["現場G"],
                    retention=None,
                    flags=["RETENTION_UNKNOWN"],
                )
            ],
        ),
        make_result_tenant(
            tenant_cd="TNT003",
            tenant_name="デモ商会",
            tenant_description="デモ用テナント",
            counts={"billable": 0, "no_data": 0, "excluded": 1, "review": 0},
            devices=[
                make_result_device(
                    device_cd="30000001-01-LoggerA-400",
                    device_name="観測局7",
                    group_cds=["GRP301"],
                    group_names=["現場H"],
                    judgement="excluded",
                    amount=0,
                )
            ],
        ),
    ]


def make_result(**overrides) -> dict:
    """`result.json` 1 件分（設計 7.3）。Excel 生成の入力に使う。"""
    result = {
        "schema": "saas-a-billing.result.v1",
        "target_env": "production",
        "month": "2026-10",
        "closing_period": {
            "from": "2026-09-21T00:00:00+09:00",
            "to": "2026-10-21T00:00:00+09:00",
            "cutoff_day": 20,
            "cutoff_time": "24:00",
            "open": False,
        },
        "run": {
            "attempt": 1,
            "trigger": "scheduled",
            "started_at": "2026-10-21T06:00:12+09:00",
            "finished_at": "2026-10-21T06:04:40+09:00",
            "requested_by": None,
            "env": "prod",
            "target_env": "production",
            "saasa_host": "saas-a.example.jp",
            "execution_arn": f"arn:aws:states:{REGION}:000000000000:execution:{STATE_MACHINE_NAME}:test",
        },
        "settings_snapshot": {"exclude_keywords": ["デモ"], "camera_format_names": ["CamA-MC-2"]},
        "price_table": make_price_table(),
        "summary": {
            "tenant_count": 5,
            "device_count": 11,
            "billable_count": 7,
            "no_data_count": 1,
            "excluded_count": 2,
            "review_count": 4,
            "failed_tenant_count": 1,
            "missing_count": 1,
            "total_amount": 7200,
        },
        "tenants": _sample_result_tenants(),
        "missing_devices": [
            {
                "tenant_cd": "TNT001",
                "tenant_name": "サンプル建設",
                "device_cd": "10000009-01-LoggerA-400",
                "device_name": "撤去済み地点",
                "group_names": ["現場A"],
                "last_seen_month": "2026-09",
            }
        ],
    }
    result.update(overrides)
    return result
