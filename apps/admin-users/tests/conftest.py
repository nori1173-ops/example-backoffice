"""ユーザー管理のテスト共通フィクスチャ。

`functions/` を `sys.path` に足し、`admin_users.app` を Lambda と同じモジュール名で
import できるようにする（`CodeUri: functions/`、`Handler: admin_users.app.handler`）。
ハンドラのモジュール名を雛形の `api.app` から変えているのは、複数アプリのテストを
1 つの pytest セッションで走らせると `api` という同名パッケージが sys.modules で
衝突するため（ADD-APP-GUIDE 2 章ステップ 4-7 の「パッケージ名前空間化」）。
"""

import json
import pathlib
import sys

import boto3
import pytest
from moto import mock_aws

FUNCTIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

REGION = "ap-northeast-1"
ENV = "dev"
APP_ID = "admin-users"
API_PREFIX = f"/api/{APP_ID}"
ADMIN_GROUP = "admin"
ORIGIN_VERIFY_SECRET = "test-origin-verify"
# 呼び出し元。API は毎リクエストで Cognito の現在状態を見るため、実在する有効な admin が要る
ACTOR_EMAIL = "actor@example.co.jp"
USER_POOL_NAME = f"example-backoffice-{ENV}"
TEST_PASSWORD = "Passw0rdTest"


@pytest.fixture(autouse=True)
def _lambda_environment(monkeypatch):
    """Lambda 環境変数と moto 用のダミー認証情報。

    `ORIGIN_VERIFY_SKIP=1` で `x-origin-verify` 検証を飛ばす（設計 5.5）。
    実際に検証したいテストでは `monkeypatch.delenv("ORIGIN_VERIFY_SKIP")` する。
    """
    monkeypatch.setenv("ENV", ENV)
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture
def aws_mock():
    """moto のモックを 1 テストにつき 1 回だけ張る。"""
    with mock_aws():
        yield


@pytest.fixture
def user_pool(aws_mock, monkeypatch):
    """core と同じ形（email がサインイン識別子・admin グループあり）の User Pool を作る。"""
    from admin_users import cognito

    client = boto3.client("cognito-idp", region_name=REGION)
    user_pool_id = client.create_user_pool(
        PoolName=USER_POOL_NAME,
        UsernameAttributes=["email"],
        AutoVerifiedAttributes=["email"],
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": False,
            }
        },
    )["UserPool"]["Id"]
    client.create_group(GroupName=ADMIN_GROUP, UserPoolId=user_pool_id)
    monkeypatch.setenv("USER_POOL_ID", user_pool_id)
    cognito.reset_client()
    yield user_pool_id
    cognito.reset_client()


@pytest.fixture
def create_user(user_pool):
    """確認済み（CONFIRMED・有効・メール検証済み）のユーザーを作り、その `sub` を返す。"""
    client = boto3.client("cognito-idp", region_name=REGION)

    def _create(email: str, *, admin: bool = False, confirmed: bool = True) -> str:
        created = client.admin_create_user(
            UserPoolId=user_pool,
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )["User"]
        if confirmed:
            client.admin_set_user_password(UserPoolId=user_pool, Username=email, Password=TEST_PASSWORD, Permanent=True)
        if admin:
            client.admin_add_user_to_group(UserPoolId=user_pool, Username=email, GroupName=ADMIN_GROUP)
        return next(a["Value"] for a in created["Attributes"] if a["Name"] == "sub")

    return _create


@pytest.fixture
def actor(create_user):
    """呼び出し元の sub。Cognito 側でも有効な `admin` として作る。"""
    return create_user(ACTOR_EMAIL, admin=True)


@pytest.fixture
def api_event(actor):
    """イベント生成。`sub` / `email` の既定は呼び出し元（`actor`）。"""

    def _event(method: str, path: str, **kwargs) -> dict:
        kwargs.setdefault("sub", actor)
        kwargs.setdefault("email", ACTOR_EMAIL)
        return make_api_event(method, path, **kwargs)

    return _event


@pytest.fixture
def call(api_event):
    """ハンドラを呼び、`(ステータス, 本文)` を返す fixture。"""

    def _call(method: str, path: str, **kwargs) -> tuple[int, dict | None]:
        from admin_users.app import handler

        result = handler(api_event(method, path, **kwargs), None)
        body = json.loads(result["body"]) if result.get("body") else None
        return result["statusCode"], body

    return _call


def make_api_event(
    method: str,
    path: str,
    body: dict | None = None,
    groups: list[str] | None = None,
    email: str = ACTOR_EMAIL,
    sub: str = "00000000-0000-0000-0000-000000000000",
) -> dict:
    """HTTP API（payload format 2.0）のイベントを作る。`path` は `API_PREFIX` からの相対パス。"""
    full_path = f"{API_PREFIX}{path}"
    return {
        "version": "2.0",
        "routeKey": f"ANY {API_PREFIX}/" + "{proxy+}",
        "rawPath": full_path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json", "x-origin-verify": ORIGIN_VERIFY_SECRET},
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
