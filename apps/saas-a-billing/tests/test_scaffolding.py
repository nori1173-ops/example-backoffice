"""テスト土台（conftest のフィクスチャとフィクスチャファイル）が使えることを確認する。"""

import csv
import json
import os

import boto3
import pytest

from tests.conftest import FIXTURES_DIR, make_api_event, make_device, make_price_table, make_settings, make_tenant


def test_lambda_environment_is_set_without_resource_vars():
    assert os.environ["AWS_DEFAULT_REGION"] == "ap-northeast-1"
    assert os.environ["ENV"] == "dev"
    assert os.environ["TARGET_ENV"] == "verification"
    assert os.environ["APP_NAME"] == "saas-a-billing"
    assert os.environ["S3_PREFIX"] == "saas-a-billing"
    assert os.environ["SSM_PREFIX"] == "/example-backoffice/dev/apps/saas-a-billing"
    assert os.environ["PORTAL_URL"] == "https://backoffice-dev.example-cloud.com"
    assert os.environ["API_PREFIX"] == "/api/saas-a-billing"
    assert os.environ["EXPORT_WINDOW_DAYS"] == "7"
    assert os.environ["TIME_BUDGET_MARGIN_SEC"] == "150"
    for key in ("TABLE_NAME", "ATTACHMENTS_BUCKET", "NOTIFY_QUEUE_URL", "STATE_MACHINE_ARN"):
        assert key not in os.environ


def test_dynamodb_table_fixture_accepts_pk_sk_items(dynamodb_table):
    assert os.environ["TABLE_NAME"] == "example-backoffice-saas-a-billing-data-dev"
    dynamodb_table.put_item(Item={"PK": "SETTINGS", "SK": "current", "version": 0})
    got = dynamodb_table.get_item(Key={"PK": "SETTINGS", "SK": "current"})["Item"]
    assert got["version"] == 0


def test_s3_bucket_fixture_stores_objects(s3_bucket):
    bucket = os.environ["ATTACHMENTS_BUCKET"]
    client = boto3.client("s3", region_name="ap-northeast-1")
    client.put_object(Bucket=bucket, Key="saas-a-billing/verification/2026/08/result.json", Body=b"{}")
    body = client.get_object(Bucket=bucket, Key="saas-a-billing/verification/2026/08/result.json")["Body"].read()
    assert body == b"{}"
    assert s3_bucket == bucket


def test_notify_queue_fixture_accepts_messages(notify_queue):
    client = boto3.client("sqs", region_name="ap-northeast-1")
    client.send_message(QueueUrl=notify_queue, MessageBody='{"schema": "example-backoffice.notify.v1"}')
    received = client.receive_message(QueueUrl=notify_queue)["Messages"]
    assert json.loads(received[0]["Body"])["schema"] == "example-backoffice.notify.v1"
    assert os.environ["NOTIFY_QUEUE_URL"] == notify_queue


def test_state_machine_fixture_is_startable(state_machine):
    client = boto3.client("stepfunctions", region_name="ap-northeast-1")
    described = client.describe_state_machine(stateMachineArn=state_machine)
    assert described["name"] == "saas-a-billing-run-dev"
    assert os.environ["STATE_MACHINE_ARN"] == state_machine
    execution = client.start_execution(stateMachineArn=state_machine, input=json.dumps({"month": "2026-08"}))
    assert execution["executionArn"].startswith(state_machine.replace(":stateMachine:", ":execution:"))


def test_ssm_targets_fixture_registers_secure_strings(ssm_targets):
    client = boto3.client("ssm", region_name="ap-northeast-1")
    prefix = "/example-backoffice/dev/apps/saas-a-billing/targets/verification"
    for leaf in ("base-url", "user-id", "password"):
        param = client.get_parameter(Name=f"{prefix}/{leaf}", WithDecryption=True)["Parameter"]
        assert param["Type"] == "SecureString"
        assert param["Value"]
    mode = client.get_parameter(Name="/example-backoffice/dev/shared/slack/mode")["Parameter"]
    assert mode["Type"] == "String"
    assert mode["Value"] == "webhook"
    assert ssm_targets == prefix


def test_make_api_event_carries_jwt_claims_and_origin_verify():
    event = make_api_event(
        "POST", "/runs", body={"month": "2026-08"}, query={"target_env": "verification"}, groups=["admin"]
    )
    assert event["version"] == "2.0"
    assert event["rawPath"] == "/api/saas-a-billing/runs"
    assert event["requestContext"]["http"]["method"] == "POST"
    assert event["rawQueryString"] == "target_env=verification"
    assert json.loads(event["body"]) == {"month": "2026-08"}
    assert event["headers"]["x-origin-verify"]
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    assert claims["sub"] == "test-sub"
    assert claims["email"] == "devA@example.co.jp"
    assert claims["cognito:groups"] == "[admin]"


def test_factories_return_design_defaults_and_accept_overrides():
    settings = make_settings()
    assert settings["target_env"] == "verification"
    assert settings["cutoff_day"] == 20
    assert settings["cutoff_time"] == "24:00"
    assert settings["auto_retry_days"] == 5
    assert settings["auto_run_enabled"] is False
    assert settings["exclude_keywords"] == ["デモ"]
    assert settings["camera_format_names"] == []
    assert make_settings(cutoff_day=25)["cutoff_day"] == 25

    price = make_price_table()
    assert price["effective_from"] == "2025-09-01"
    assert [band["max_items"] for band in price["bands"]] == [500, 800, 1600, 1700]
    assert [band["price_3y"] for band in price["bands"]] == [650, 1000, 1200, 1350]
    assert [band["price_1y"] for band in price["bands"]] == [450, 700, 900, 1000]
    assert price["camera"] == {"price_3y": 1800, "price_1y": 1400}
    assert make_price_table(effective_from="2026-04-01")["effective_from"] == "2026-04-01"

    device = make_device()
    assert device["device_cd"]
    assert device["device_description"] == ""
    assert isinstance(device["format_detail"], list)
    assert make_device(device_description="デモ機")["device_description"] == "デモ機"

    tenant = make_tenant()
    assert tenant["tenant_cd"]
    assert tenant["retention_period"] == 3
    assert tenant["tenant_description"] == ""
    assert make_tenant(retention_period=1)["retention_period"] == 1


def test_export_csv_fixture_is_cp932_with_three_blocks():
    raw = (FIXTURES_DIR / "export_multi_block.csv").read_bytes()
    text = raw.decode("cp932")
    rows = list(csv.reader(text.splitlines()))
    headers = [row for row in rows if row and row[0] == "field_name"]
    assert len(headers) == 3
    assert sum(1 for row in headers if "Base64形式データ" in row) == 1
    multi_group = [row for row in rows if len(row) > 1 and row[1] == "10000002-02-LoggerA-400"]
    assert [row[0] for row in multi_group] == ["現場A,現場B"]
    assert all(len(row) > 3 for row in rows if row and row[0] != "field_name")


def test_json_and_empty_fixtures_are_loadable():
    assert (FIXTURES_DIR / "export_empty.csv").stat().st_size == 0

    no_records = json.loads((FIXTURES_DIR / "export_no_records.json").read_text(encoding="utf-8"))
    assert no_records == {"errorStatus": "Error.Export.NoRecords", "message": ""}

    limit_over = json.loads((FIXTURES_DIR / "export_limit_over.json").read_text(encoding="utf-8"))
    assert limit_over["message"] == "Error.Export.LimitOver,123456,100000"

    token = json.loads((FIXTURES_DIR / "get_token.json").read_text(encoding="utf-8"))
    assert token["token_info"]["token_type"] == "Bearer"
    assert token["token_info"]["access_token"]

    tenants = json.loads((FIXTURES_DIR / "tenant_list.json").read_text(encoding="utf-8"))
    assert tenants["data"]["count"] == len(tenants["data"]["rows"])

    devices = json.loads((FIXTURES_DIR / "device_list.json").read_text(encoding="utf-8"))
    assert devices["data"]["count"] == len(devices["data"]["rows"])
    device_cds = {row["device_cd"] for row in devices["data"]["rows"]}
    assert {"10000001-01-LoggerA-400", "10000002-02-LoggerA-400", "10000003-03-CamA-MC"} <= device_cds

    groups = json.loads((FIXTURES_DIR / "group_list.json").read_text(encoding="utf-8"))
    assert {group["group_cd"] for group in groups["groups"]} >= {"GRP001", "GRP002"}


@pytest.mark.parametrize("package", ["saas_a_billing", "api", "batch", "batch.handlers"])
def test_functions_packages_are_importable(package):
    __import__(package)
