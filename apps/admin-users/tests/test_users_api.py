"""`admin_users.app` の 5 エンドポイントのテスト（アプリ設計 3 章 / 4 章）。

moto の Cognito User Pool に実際に読み書きし、JWT クレーム入りのイベントで
ハンドラを一気通貫に呼ぶ。自分自身への操作と最後の admin の保護はサーバ側で
必ず落ちること（画面の disabled は二重防御の片方に過ぎない）を確かめる。
"""

import logging

import boto3
import pytest
from admin_users import cognito

ADMIN = ["admin"]
# conftest の ACTOR_EMAIL と同じ値（テストからは conftest を import できないため）
ACTOR_EMAIL = "actor@example.co.jp"
TARGET_EMAIL = "target@example.co.jp"
REGION = "ap-northeast-1"
UNKNOWN_SUB = "11111111-2222-3333-4444-555555555555"

MUTATIONS = [
    ("GET", "/users", None),
    ("PUT", f"/users/{UNKNOWN_SUB}/admin", {"admin": True}),
    ("PUT", f"/users/{UNKNOWN_SUB}/enabled", {"enabled": False}),
    ("POST", f"/users/{UNKNOWN_SUB}/password-reset", None),
    ("DELETE", f"/users/{UNKNOWN_SUB}", None),
]


@pytest.fixture
def target(create_user):
    """操作対象（一般ユーザー）の sub。"""
    return create_user(TARGET_EMAIL)


def _users(body: dict) -> dict[str, dict]:
    return {user["email"]: user for user in body["data"]["users"]}


def _groups_of(user_pool: str, email: str) -> list[str]:
    client = boto3.client("cognito-idp", region_name=REGION)
    return [
        group["GroupName"]
        for group in client.admin_list_groups_for_user(UserPoolId=user_pool, Username=email)["Groups"]
    ]


@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_non_admin_is_forbidden(call, user_pool, method, path, body):
    """一般ユーザーは読み取りも含めて全ルートで 403（設計 6.3）。"""
    status, payload = call(method, path, body=body, groups=[])

    assert status == 403
    assert payload["error"]["code"] == "FORBIDDEN"


def test_caller_removed_from_the_group_is_forbidden(call, create_user, user_pool):
    """ID トークンは最大 60 分残るため、クレームが admin でも現在の所属を見る。"""
    stale = create_user("stale@example.co.jp")

    status, payload = call("GET", "/users", groups=ADMIN, sub=stale, email="stale@example.co.jp")

    assert status == 403
    assert payload["error"]["code"] == "FORBIDDEN"


def test_disabled_caller_is_forbidden(call, user_pool, actor):
    """無効化された管理者は、トークンが生きていてもこの API を使えない。"""
    boto3.client("cognito-idp", region_name=REGION).admin_disable_user(UserPoolId=user_pool, Username=ACTOR_EMAIL)

    status, payload = call("GET", "/users", groups=ADMIN)

    assert status == 403
    assert payload["error"]["code"] == "FORBIDDEN"


def test_deleted_caller_is_forbidden(call, user_pool, actor):
    boto3.client("cognito-idp", region_name=REGION).admin_delete_user(UserPoolId=user_pool, Username=ACTOR_EMAIL)

    status, payload = call("GET", "/users", groups=ADMIN)

    assert status == 403
    assert payload["error"]["code"] == "FORBIDDEN"


def test_health_is_ok_for_admin(call, user_pool):
    status, payload = call("GET", "/health", groups=ADMIN)

    assert status == 200
    assert payload["data"] == {"status": "ok"}


@pytest.mark.parametrize("body", ["not json", '"a string"'])
def test_body_must_be_a_json_object(api_event, user_pool, target, body):
    from admin_users.app import handler

    event = api_event("PUT", f"/users/{target}/admin", groups=ADMIN)
    event["body"] = body

    result = handler(event, None)

    assert result["statusCode"] == 400


def test_unknown_route_returns_404_envelope(call, user_pool):
    status, payload = call("GET", "/unknown", groups=ADMIN)

    assert status == 404
    assert payload["error"]["code"] == "NOT_FOUND"


def test_request_without_origin_verify_header_is_forbidden(api_event, user_pool, monkeypatch):
    from admin_users.app import handler

    monkeypatch.delenv("ORIGIN_VERIFY_SKIP")
    event = api_event("GET", "/users", groups=ADMIN)
    event["headers"].pop("x-origin-verify")

    assert handler(event, None)["statusCode"] == 403


def test_list_users_returns_admin_flag_and_self_sub(call, actor, target):
    status, payload = call("GET", "/users", groups=ADMIN, sub=actor, email=ACTOR_EMAIL)

    assert status == 200
    assert payload["data"]["self_sub"] == actor
    users = _users(payload)
    assert users[ACTOR_EMAIL]["is_admin"] is True
    assert users[TARGET_EMAIL]["is_admin"] is False
    assert users[TARGET_EMAIL]["sub"] == target
    assert users[TARGET_EMAIL]["enabled"] is True
    assert users[TARGET_EMAIL]["status"] == "CONFIRMED"
    assert users[TARGET_EMAIL]["created_at"].endswith("+00:00")
    assert users[TARGET_EMAIL]["updated_at"].endswith("+00:00")


def test_list_users_is_sorted_by_email(call, create_user, user_pool):
    for email in ("charlie@example.co.jp", "alice@example.co.jp", "bob@example.co.jp"):
        create_user(email)

    _status, payload = call("GET", "/users", groups=ADMIN)

    assert [user["email"] for user in payload["data"]["users"]] == [
        ACTOR_EMAIL,
        "alice@example.co.jp",
        "bob@example.co.jp",
        "charlie@example.co.jp",
    ]


def test_list_users_follows_pagination(call, create_user, user_pool, monkeypatch):
    """1 ページに収まらない User Pool でも全件返す（ページングを辿らないと歯抜けになる）。"""
    monkeypatch.setattr(cognito, "PAGE_SIZE", 2)
    emails = [f"user{index}@example.co.jp" for index in range(5)]
    for email in emails:
        create_user(email, admin=True)

    _status, payload = call("GET", "/users", groups=ADMIN)

    assert [user["email"] for user in payload["data"]["users"]] == [ACTOR_EMAIL, *emails]
    assert all(user["is_admin"] for user in payload["data"]["users"]), "admin 判定もページングを辿ること"


def test_grant_and_revoke_admin(call, user_pool, actor, target):
    status, payload = call(
        "PUT", f"/users/{target}/admin", body={"admin": True}, groups=ADMIN, sub=actor, email=ACTOR_EMAIL
    )

    assert status == 200
    assert payload["data"] == {"sub": target, "is_admin": True}
    assert _groups_of(user_pool, TARGET_EMAIL) == ["admin"]

    status, payload = call(
        "PUT", f"/users/{target}/admin", body={"admin": False}, groups=ADMIN, sub=actor, email=ACTOR_EMAIL
    )

    assert status == 200
    assert payload["data"] == {"sub": target, "is_admin": False}
    assert _groups_of(user_pool, TARGET_EMAIL) == []


@pytest.mark.parametrize("admin", [True, False])
def test_operating_own_admin_is_forbidden(call, user_pool, actor, admin):
    """付与も拒否する。admin を外された本人が、残ったクレームで自分へ戻せてしまうため。"""
    status, payload = call("PUT", f"/users/{actor}/admin", body={"admin": admin}, groups=ADMIN)

    assert status == 403
    assert payload["error"]["code"] == "SELF_OPERATION_FORBIDDEN"


def test_revoking_own_admin_is_forbidden(call, user_pool, actor):
    """自分の admin を外して誰も管理できなくなる事故を防ぐ（UI の disabled と二重防御）。"""
    status, payload = call(
        "PUT", f"/users/{actor}/admin", body={"admin": False}, groups=ADMIN, sub=actor, email=ACTOR_EMAIL
    )

    assert status == 403
    assert payload["error"]["code"] == "SELF_OPERATION_FORBIDDEN"
    assert _groups_of(user_pool, ACTOR_EMAIL) == ["admin"]


def test_revoking_the_last_admin_is_rejected(call, create_user, user_pool, monkeypatch):
    """admin が 1 人だけのとき、その 1 人から admin を外せない。

    呼び出し元も現在の admin である（`_require_current_admin`）ため、実際には admin が
    2 人以上いて到達しない。多重防御の分岐そのものを守るため、グループ一覧だけを
    「対象 1 人」に差し替えて確かめる。
    """
    only_admin = create_user(TARGET_EMAIL, admin=True)
    monkeypatch.setattr(cognito, "admin_subs", lambda: {only_admin})

    status, payload = call("PUT", f"/users/{only_admin}/admin", body={"admin": False}, groups=ADMIN)

    assert status == 409
    assert payload["error"]["code"] == "LAST_ADMIN"
    assert _groups_of(user_pool, TARGET_EMAIL) == ["admin"]


def test_admin_body_must_be_boolean(call, user_pool, target):
    status, payload = call("PUT", f"/users/{target}/admin", body={"admin": "true"}, groups=ADMIN)

    assert status == 400
    assert payload["error"]["code"] == "VALIDATION_ERROR"


def test_disable_and_enable_user(call, user_pool, actor, target):
    status, payload = call(
        "PUT", f"/users/{target}/enabled", body={"enabled": False}, groups=ADMIN, sub=actor, email=ACTOR_EMAIL
    )

    assert status == 200
    assert payload["data"] == {"sub": target, "enabled": False}
    _status, listed = call("GET", "/users", groups=ADMIN, sub=actor)
    assert _users(listed)[TARGET_EMAIL]["enabled"] is False

    status, payload = call(
        "PUT", f"/users/{target}/enabled", body={"enabled": True}, groups=ADMIN, sub=actor, email=ACTOR_EMAIL
    )

    assert status == 200
    assert payload["data"] == {"sub": target, "enabled": True}
    _status, listed = call("GET", "/users", groups=ADMIN, sub=actor)
    assert _users(listed)[TARGET_EMAIL]["enabled"] is True


def test_disabling_self_is_forbidden(call, user_pool, actor):
    status, payload = call(
        "PUT", f"/users/{actor}/enabled", body={"enabled": False}, groups=ADMIN, sub=actor, email=ACTOR_EMAIL
    )

    assert status == 403
    assert payload["error"]["code"] == "SELF_OPERATION_FORBIDDEN"
    _status, listed = call("GET", "/users", groups=ADMIN, sub=actor)
    assert _users(listed)[ACTOR_EMAIL]["enabled"] is True


def test_enabled_body_must_be_boolean(call, user_pool, target):
    status, payload = call("PUT", f"/users/{target}/enabled", body={"enabled": None}, groups=ADMIN)

    assert status == 400
    assert payload["error"]["code"] == "VALIDATION_ERROR"


def test_password_reset_returns_the_target_email(call, user_pool, actor, target):
    status, payload = call("POST", f"/users/{target}/password-reset", groups=ADMIN, sub=actor, email=ACTOR_EMAIL)

    assert status == 200
    assert payload["data"] == {"sub": target, "email": TARGET_EMAIL}


def test_password_reset_on_unconfirmed_user_is_rejected(call, create_user, user_pool):
    """初期パスワード状態・未確認のユーザーは Cognito がリセットを拒む（409 で返す）。"""
    pending = create_user("pending@example.co.jp", confirmed=False)

    status, payload = call("POST", f"/users/{pending}/password-reset", groups=ADMIN)

    assert status == 409
    assert payload["error"]["code"] == "PASSWORD_RESET_NOT_ALLOWED"


def test_delete_user(call, user_pool, actor, target):
    status, payload = call("DELETE", f"/users/{target}", groups=ADMIN, sub=actor, email=ACTOR_EMAIL)

    assert status == 204
    assert payload is None

    status, payload = call("DELETE", f"/users/{target}", groups=ADMIN, sub=actor, email=ACTOR_EMAIL)
    assert status == 404
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_deleting_self_is_forbidden(call, user_pool, actor):
    status, payload = call("DELETE", f"/users/{actor}", groups=ADMIN, sub=actor, email=ACTOR_EMAIL)

    assert status == 403
    assert payload["error"]["code"] == "SELF_OPERATION_FORBIDDEN"
    _status, listed = call("GET", "/users", groups=ADMIN, sub=actor)
    assert ACTOR_EMAIL in _users(listed)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("PUT", f"/users/{UNKNOWN_SUB}/admin", {"admin": True}),
        ("PUT", f"/users/{UNKNOWN_SUB}/enabled", {"enabled": False}),
        ("POST", f"/users/{UNKNOWN_SUB}/password-reset", None),
        ("DELETE", f"/users/{UNKNOWN_SUB}", None),
        ("DELETE", "/users/not-a-uuid", None),
    ],
)
def test_unknown_sub_returns_404(call, user_pool, method, path, body):
    status, payload = call(method, path, body=body, groups=ADMIN)

    assert status == 404
    assert payload["error"]["code"] == "USER_NOT_FOUND"


def test_operations_are_written_to_the_structured_log(call, user_pool, actor, target, caplog):
    """CloudWatch Logs が唯一の証跡なので、誰が誰に何をしたかを必ず残す（アプリ設計 6 章）。"""
    with caplog.at_level(logging.INFO, logger="example_backoffice"):
        call("PUT", f"/users/{target}/admin", body={"admin": True}, groups=ADMIN, sub=actor, email=ACTOR_EMAIL)

    record = next(record for record in caplog.records if record.getMessage().startswith("grant_admin"))
    message = record.getMessage()
    assert '"operation": "grant_admin"' in message
    assert f'"actor_sub": "{actor}"' in message
    assert f'"actor_email": "{ACTOR_EMAIL}"' in message
    assert f'"target_sub": "{target}"' in message
    assert f'"target_email": "{TARGET_EMAIL}"' in message
    assert '"result": "ok"' in message


def test_rejected_operations_are_logged_with_the_reason(call, user_pool, actor, caplog):
    with caplog.at_level(logging.INFO, logger="example_backoffice"):
        call("DELETE", f"/users/{actor}", groups=ADMIN, sub=actor, email=ACTOR_EMAIL)

    record = next(record for record in caplog.records if record.getMessage().startswith("delete_user"))
    assert '"result": "SELF_OPERATION_FORBIDDEN"' in record.getMessage()


def test_logs_do_not_contain_the_origin_verify_secret(call, user_pool, target, caplog):
    with caplog.at_level(logging.DEBUG, logger="example_backoffice"):
        call("GET", "/users", groups=ADMIN)

    assert "test-origin-verify" not in caplog.text
