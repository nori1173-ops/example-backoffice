"""AWS日次セキュリティレポートのテスト用フィクスチャ。

`functions/` を `sys.path` に足し、`daily_security_report_api.app` / `daily_security_report_batch.handlers.*` を
Lambda と同じモジュール名で import できるようにする（`CodeUri: functions/` に対応）。
"""

import json
import pathlib
import sys
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

FUNCTIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

APP_ID = "daily-security-report"
TABLE_NAME = "example-backoffice-daily-security-report-data-test"


@pytest.fixture(autouse=True)
def lambda_environment(monkeypatch):
    """Lambda 環境変数と moto 用のダミー認証情報。

    `ORIGIN_VERIFY_SKIP=1` で `x-origin-verify` 検証を飛ばす（設計 5.5）。
    実際に検証したいテストでは `monkeypatch.delenv("ORIGIN_VERIFY_SKIP")` する。
    """
    monkeypatch.setenv("ALLOWED_MAIL_DOMAINS", "example.co.jp")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("APP_NAME", APP_ID)
    monkeypatch.setenv("ACCOUNT_ID", "000000000000")
    monkeypatch.setenv("GUARDDUTY_DETECTORS", "ap-northeast-1=example-detector")
    monkeypatch.setenv("CLOUDTRAIL_BUCKET", "test-cloudtrail-bucket")
    monkeypatch.setenv("CLOUDTRAIL_PREFIX", "AWSLogs/000000000000/CloudTrail/")
    monkeypatch.setenv("INSIGHTS_LOG_GROUPS", json.dumps({"alarmhuba": "/test/alarmhuba", "gps": "/test/gps"}))
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", APP_ID)
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("TABLE_NAME", TABLE_NAME)
    monkeypatch.setenv("NOTIFY_QUEUE_URL", "https://sqs.ap-northeast-1.amazonaws.com/000000000000/notify-test")
    monkeypatch.setenv("ATTACHMENTS_BUCKET", "example-backoffice-attachments-test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture
def http_event():
    """JWT クレーム付きの HTTP API（payload format 2.0）イベントを作る。"""

    def _event(
        path: str,
        method: str = "GET",
        groups: list[str] | None = None,
        email: str = "user@example.co.jp",
        body: str | None = None,
    ) -> dict:
        full_path = f"/api/{APP_ID}{path}"
        return {
            "version": "2.0",
            "routeKey": f"{method} /api/{APP_ID}/" + "{proxy+}",
            "rawPath": full_path,
            "rawQueryString": "",
            "headers": {"content-type": "application/json", "x-origin-verify": "test-secret"},
            "requestContext": {
                "accountId": "000000000000",
                "apiId": "testapi",
                "domainName": "testapi.execute-api.ap-northeast-1.amazonaws.com",
                "http": {"method": method, "path": full_path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.1"},
                "requestId": "test-request-id",
                "stage": "$default",
                "authorizer": {
                    "jwt": {
                        "claims": {
                            "sub": "00000000-0000-0000-0000-000000000000",
                            "email": email,
                            "token_use": "id",
                            "cognito:groups": f"[{' '.join(groups or [])}]",
                        }
                    }
                },
            },
            "body": body,
            "isBase64Encoded": False,
        }

    return _event


@pytest.fixture
def aws_mock(lambda_environment):
    with mock_aws():
        yield


@pytest.fixture
def resources(aws_mock, monkeypatch):
    from daily_security_report import repo, slack_directory
    from daily_security_report_api import app as api

    from example_backoffice import notify

    repo.reset_clients()
    slack_directory.reset_clients()
    notify.reset_clients()
    api.reset_clients()
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": name, "AttributeType": "S"} for name in ("PK", "SK")],
        BillingMode="PAY_PER_REQUEST",
    )
    sqs = boto3.client("sqs")
    queue = sqs.create_queue(QueueName="notify-test")["QueueUrl"]
    monkeypatch.setenv("NOTIFY_QUEUE_URL", queue)
    s3 = boto3.client("s3")
    s3.create_bucket(
        Bucket="test-cloudtrail-bucket", CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"}
    )
    yield {"table": table, "sqs": sqs, "queue": queue, "s3": s3, "ssm": boto3.client("ssm")}
    repo.reset_clients()
    slack_directory.reset_clients()
    notify.reset_clients()
    api.reset_clients()


class FakeCloudWatch:
    """AWS/S3 の ListMetrics / GetMetricData だけを返す偽物。

    buckets は {バケット名: {"objects": {日付: 値}, "size": {StorageType: {日付: 値}}, "filters": [FilterId],
    "requests": {指標名: 値}}}。日次指標の点は日付の 00:00 UTC、要求指標の点は StartTime に置く。
    """

    def __init__(self, buckets: dict | None = None, *, page: int = 500, error: Exception | None = None):
        self.buckets, self.page, self.error, self.calls = buckets or {}, page, error, []

    def list_metrics(self, **params):
        self.calls.append(("list_metrics", params))
        if self.error:
            raise self.error
        metric, rows = params["MetricName"], []
        for name, data in sorted(self.buckets.items()):
            if metric == "NumberOfObjects" and "objects" in data:
                rows.append({"BucketName": name, "StorageType": "AllStorageTypes"})
            elif metric == "BucketSizeBytes":
                rows.extend({"StorageType": kind, "BucketName": name} for kind in data.get("size", {}))
            elif metric == "AllRequests":
                rows.extend({"BucketName": name, "FilterId": value} for value in data.get("filters", []))
        start = int(params.get("NextToken", 0))
        response = {
            "Metrics": [
                {
                    "Namespace": params["Namespace"],
                    "MetricName": metric,
                    "Dimensions": [{"Name": key, "Value": value} for key, value in row.items()],
                }
                for row in rows[start : start + self.page]
            ]
        }
        if start + self.page < len(rows):
            response["NextToken"] = str(start + self.page)
        return response

    def get_metric_data(self, **params):
        self.calls.append(("get_metric_data", params))
        results = []
        for query in params["MetricDataQueries"]:
            metric = query["MetricStat"]["Metric"]
            dimensions = {row["Name"]: row["Value"] for row in metric["Dimensions"]}
            data, name = self.buckets[dimensions["BucketName"]], metric["MetricName"]
            if name in ("NumberOfObjects", "BucketSizeBytes"):
                series = data["objects"] if name == "NumberOfObjects" else data["size"][dimensions["StorageType"]]
                points = [(datetime.fromisoformat(day).replace(tzinfo=UTC), value) for day, value in series.items()]
            else:
                value, start = data.get("requests", {}).get(name), params["StartTime"]
                half = value // 2 if value is not None else 0
                points = [(start, value - half), (start + timedelta(hours=1), half)] if value is not None else []
            points = sorted((p for p in points if params["StartTime"] <= p[0] < params["EndTime"]), reverse=True)
            results.append(
                {
                    "Id": query["Id"],
                    "Timestamps": [p[0] for p in points],
                    "Values": [p[1] for p in points],
                    "StatusCode": "Complete",
                }
            )
        return {"MetricDataResults": results}


@pytest.fixture
def fake_cloudwatch():
    return FakeCloudWatch
