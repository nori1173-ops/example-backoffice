"""JWT クレームの取り出しとグループ判定（設計 6.3 / 10.2）。

HTTP API（payload format 2.0）の JWT authorizer は
`requestContext.authorizer.jwt.claims` にクレームを置く。
`cognito:groups` は文字列 `"[admin dev]"` 形式と配列のどちらでも渡り得る
（設計 17 の #6 が未実測）ため、両形式を受理する。
"""

from dataclasses import dataclass, field
from typing import Any

GROUPS_CLAIM = "cognito:groups"
ADMIN_GROUP = "admin"


class UnauthorizedError(Exception):
    """認証されていない（401）。"""


class ForbiddenError(Exception):
    """認証済みだが権限が足りない（403）。"""


@dataclass(frozen=True)
class Claims:
    """ID トークンから取り出した呼び出し元の情報。"""

    sub: str
    email: str
    groups: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def normalize_groups(value: Any) -> list[str]:
    """`cognito:groups` クレームを文字列の配列に正規化する。"""
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []

    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [part for part in text.replace(",", " ").split() if part]


def get_claims(event: dict) -> Claims:
    """イベントから `Claims` を取り出す。クレームまたは `sub` が無ければ `UnauthorizedError`。"""
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    raw = (authorizer.get("jwt") or {}).get("claims")
    if not isinstance(raw, dict):
        raise UnauthorizedError("JWT クレームがありません")

    sub = raw.get("sub")
    if not sub:
        raise UnauthorizedError("sub クレームがありません")

    return Claims(
        sub=str(sub),
        email=str(raw.get("email") or ""),
        groups=normalize_groups(raw.get(GROUPS_CLAIM)),
        raw=raw,
    )


def require_group(claims: Claims, group: str) -> None:
    """`group` に所属していなければ `ForbiddenError` を投げる。"""
    if group not in claims.groups:
        raise ForbiddenError(f"グループ {group} の権限が必要です")


def is_admin(claims: Claims) -> bool:
    """`admin` グループに所属しているか。"""
    return ADMIN_GROUP in claims.groups
