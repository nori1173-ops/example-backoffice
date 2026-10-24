"""`admin_users.cognito` の薄いラッパのテスト。"""

import boto3
import pytest
from admin_users import cognito

REGION = "ap-northeast-1"


def test_find_by_sub_returns_the_matching_user(user_pool, create_user):
    sub = create_user("someone@example.co.jp")

    user = cognito.find_by_sub(sub)

    assert user is not None
    assert cognito.attribute(user, "email") == "someone@example.co.jp"
    # UsernameAttributes: [email] の User Pool では Username は生成 ID で、メールは別名。
    # Admin 系 API にはこの Username を渡す（画面から来た文字列は渡さない）。
    assert user["Username"] != "someone@example.co.jp"


@pytest.mark.parametrize(
    "sub",
    [
        "",
        "not-a-uuid",
        '00000000-0000-0000-0000-000000000000" or email ^= "',
    ],
)
def test_find_by_sub_rejects_values_that_are_not_a_sub(user_pool, create_user, sub):
    """`sub` は Filter 式へ埋め込むため、UUID 以外は問い合わせずに未存在として扱う。"""
    create_user("someone@example.co.jp")

    assert cognito.find_by_sub(sub) is None


def test_admin_subs_is_empty_when_nobody_is_admin(user_pool, create_user):
    create_user("someone@example.co.jp")

    assert cognito.admin_subs() == set()


def test_to_view_maps_cognito_attributes(user_pool, create_user):
    sub = create_user("someone@example.co.jp", admin=True)
    user = cognito.find_by_sub(sub)

    view = cognito.to_view(user, {sub})

    assert view["sub"] == sub
    assert view["email"] == "someone@example.co.jp"
    assert view["enabled"] is True
    assert view["is_admin"] is True
    assert view["status"] == "CONFIRMED"
    assert view["created_at"].endswith("+00:00")


def test_disabled_user_is_reported_as_disabled(user_pool, create_user):
    sub = create_user("someone@example.co.jp")
    boto3.client("cognito-idp", region_name=REGION).admin_disable_user(
        UserPoolId=user_pool, Username="someone@example.co.jp"
    )

    assert cognito.to_view(cognito.find_by_sub(sub), set())["enabled"] is False
