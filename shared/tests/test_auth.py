"""`example_backoffice.auth` のテスト（設計 6.3 / 10.2）。"""

import pytest

from example_backoffice.auth import (
    Claims,
    ForbiddenError,
    UnauthorizedError,
    get_claims,
    is_admin,
    normalize_groups,
    require_group,
)


def make_event(claims: dict | None) -> dict:
    """HTTP API（payload format 2.0）の JWT authorizer イベントを組む。"""
    if claims is None:
        return {"requestContext": {"http": {"method": "GET", "path": "/health"}}}
    return {
        "requestContext": {
            "http": {"method": "GET", "path": "/health"},
            "authorizer": {"jwt": {"claims": claims, "scopes": None}},
        }
    }


def make_claims(groups: list[str]) -> Claims:
    return Claims(sub="s-1", email="devA@example.co.jp", groups=groups, raw={})


def test_get_claims_reads_sub_and_email():
    event = make_event({"sub": "s-1", "email": "devA@example.co.jp"})

    claims = get_claims(event)

    assert claims.sub == "s-1"
    assert claims.email == "devA@example.co.jp"
    assert claims.groups == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("[admin dev]", ["admin", "dev"]),
        ("[admin]", ["admin"]),
        ("[]", []),
        ("admin", ["admin"]),
        ("admin,dev", ["admin", "dev"]),
        (["admin", "dev"], ["admin", "dev"]),
        (None, []),
        ("", []),
    ],
)
def test_normalize_groups_accepts_both_formats(value, expected):
    assert normalize_groups(value) == expected


def test_get_claims_normalizes_group_string():
    event = make_event({"sub": "s-1", "email": "devA@example.co.jp", "cognito:groups": "[admin]"})

    assert get_claims(event).groups == ["admin"]


def test_get_claims_normalizes_group_list():
    event = make_event({"sub": "s-1", "email": "devA@example.co.jp", "cognito:groups": ["admin"]})

    assert get_claims(event).groups == ["admin"]


def test_get_claims_without_authorizer_raises_unauthorized():
    with pytest.raises(UnauthorizedError):
        get_claims(make_event(None))


def test_get_claims_without_sub_raises_unauthorized():
    with pytest.raises(UnauthorizedError):
        get_claims(make_event({"email": "devA@example.co.jp"}))


def test_require_group_passes_for_member():
    require_group(make_claims(["admin"]), "admin")


def test_require_group_raises_forbidden_for_non_member():
    with pytest.raises(ForbiddenError):
        require_group(make_claims([]), "admin")


def test_is_admin():
    assert is_admin(make_claims(["admin"])) is True
    assert is_admin(make_claims(["dev"])) is False


def test_get_claims_rejects_access_token():
    """access トークンには email が無く監査が欠けるため拒否する（SEC-12）。"""
    with pytest.raises(UnauthorizedError):
        get_claims(make_event({"sub": "s-1", "token_use": "access"}))


def test_get_claims_accepts_id_token():
    claims = get_claims(make_event({"sub": "s-1", "email": "devA@example.co.jp", "token_use": "id"}))

    assert claims.sub == "s-1"
    assert claims.email == "devA@example.co.jp"
