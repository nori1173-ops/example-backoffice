"""{{cookiecutter.app_title}} のテスト用フィクスチャ。

`functions/` を `sys.path` に足し、`api.app` / `batch.handlers.*` を Lambda と同じ
モジュール名で import できるようにする（`CodeUri: functions/` に対応）。
"""

import pathlib
import sys

import pytest

FUNCTIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

APP_ID = "{{cookiecutter.app_id}}"
TABLE_NAME = "example-backoffice-{{cookiecutter.app_id}}-data-test"


@pytest.fixture(autouse=True)
def lambda_environment(monkeypatch):
    """Lambda 環境変数と moto 用のダミー認証情報。

    `ORIGIN_VERIFY_SKIP=1` で `x-origin-verify` 検証を飛ばす（設計 5.5）。
    実際に検証したいテストでは `monkeypatch.delenv("ORIGIN_VERIFY_SKIP")` する。
    """
    monkeypatch.setenv("ENV", "dev")
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
                            "cognito:groups": f"[{' '.join(groups or [])}]",
                        }
                    }
                },
            },
            "body": body,
            "isBase64Encoded": False,
        }

    return _event
