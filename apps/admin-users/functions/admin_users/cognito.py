"""Cognito User Pool の管理 API を薄く包む（アプリ設計 3 章 / 5 章）。

ユーザーの識別子は `sub` である。User Pool は `UsernameAttributes: [email]` で作って
あり Username はメールアドレスだが、メールは変わり得るうえ画面から渡された文字列を
そのまま Username として使うと別人を掴む余地が残る。`sub = "..."` の Filter で
User Pool を引き、返ってきた Username で Admin 系 API を呼ぶ。

boto3 クライアントはモジュールに 1 つ持つ。`reset_client()` は moto の `mock_aws` を
張り直すテスト用（saas-a-billing の api と同じ流儀）。
"""

import os
import re
from datetime import UTC, datetime

import boto3

ADMIN_GROUP = "admin"
SUB_ATTRIBUTE = "sub"
EMAIL_ATTRIBUTE = "email"

# ListUsers / ListUsersInGroup の Limit の上限（AWS API の仕様）。
PAGE_SIZE = 60

# Cognito の sub は UUID。Filter 式へ埋め込む前にこの形だけを通す。
_SUB_PATTERN = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")

_client_cache = None


def reset_client() -> None:
    """boto3 クライアントのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _client_cache
    _client_cache = None


def client():
    global _client_cache
    if _client_cache is None:
        _client_cache = boto3.client("cognito-idp")
    return _client_cache


def user_pool_id() -> str:
    return os.environ["USER_POOL_ID"]


def attribute(user: dict, name: str) -> str:
    """ユーザー属性の値。無ければ空文字。"""
    for item in user.get("Attributes") or user.get("UserAttributes") or []:
        if item.get("Name") == name:
            return item.get("Value") or ""
    return ""


def _iso(value: object) -> str | None:
    """Cognito の日時を UTC の ISO 8601 にする。秒未満は落とす。"""
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def to_view(user: dict, admin_subs: set[str]) -> dict:
    """API 応答 1 件分。秘密になり得る属性は載せない。"""
    sub = attribute(user, SUB_ATTRIBUTE)
    return {
        "sub": sub,
        "email": attribute(user, EMAIL_ATTRIBUTE),
        "status": user.get("UserStatus"),
        "enabled": bool(user.get("Enabled")),
        "is_admin": sub in admin_subs,
        "created_at": _iso(user.get("UserCreateDate")),
        "updated_at": _iso(user.get("UserLastModifiedDate")),
    }


def list_users() -> list[dict]:
    """User Pool の全ユーザー。ページングを辿らないと 60 人目以降が歯抜けになる。"""
    users: list[dict] = []
    token = None
    while True:
        page = client().list_users(
            UserPoolId=user_pool_id(),
            Limit=PAGE_SIZE,
            **({"PaginationToken": token} if token else {}),
        )
        users.extend(page.get("Users") or [])
        token = page.get("PaginationToken")
        if not token:
            return users


def admin_subs() -> set[str]:
    """`admin` グループに所属するユーザーの `sub`（ページング込み）。"""
    subs: set[str] = set()
    token = None
    while True:
        page = client().list_users_in_group(
            UserPoolId=user_pool_id(),
            GroupName=ADMIN_GROUP,
            Limit=PAGE_SIZE,
            **({"NextToken": token} if token else {}),
        )
        subs.update(attribute(user, SUB_ATTRIBUTE) for user in page.get("Users") or [])
        token = page.get("NextToken")
        if not token:
            return subs


def find_by_sub(sub: str) -> dict | None:
    """`sub` からユーザーを引く。見つからない・`sub` の形でないなら `None`。"""
    if not _SUB_PATTERN.match(sub or ""):
        return None
    # sub は User Pool 内で一意なので 1 件で足りる（ページングしない）。
    users = client().list_users(UserPoolId=user_pool_id(), Filter=f'sub = "{sub}"', Limit=1).get("Users") or []
    return users[0] if users else None


def is_enabled(username: str) -> bool:
    """ユーザーが有効か。無効化された呼び出し元をその場で弾くのに使う。"""
    return bool(client().admin_get_user(UserPoolId=user_pool_id(), Username=username).get("Enabled"))


def groups_of(username: str) -> set[str]:
    """所属グループ名（ページング込み）。ID トークンのクレームではなく現在の所属を見るのに使う。"""
    names: set[str] = set()
    token = None
    while True:
        page = client().admin_list_groups_for_user(
            UserPoolId=user_pool_id(),
            Username=username,
            Limit=PAGE_SIZE,
            **({"NextToken": token} if token else {}),
        )
        names.update(group.get("GroupName") for group in page.get("Groups") or [])
        token = page.get("NextToken")
        if not token:
            return names


def set_admin(username: str, admin: bool) -> None:
    if admin:
        client().admin_add_user_to_group(UserPoolId=user_pool_id(), Username=username, GroupName=ADMIN_GROUP)
    else:
        client().admin_remove_user_from_group(UserPoolId=user_pool_id(), Username=username, GroupName=ADMIN_GROUP)


def set_enabled(username: str, enabled: bool) -> None:
    if enabled:
        client().admin_enable_user(UserPoolId=user_pool_id(), Username=username)
    else:
        client().admin_disable_user(UserPoolId=user_pool_id(), Username=username)


def reset_password(username: str) -> None:
    """本人へリセットコードをメールする（パスワードはここでは決めない）。"""
    client().admin_reset_user_password(UserPoolId=user_pool_id(), Username=username)


def delete_user(username: str) -> None:
    client().admin_delete_user(UserPoolId=user_pool_id(), Username=username)
