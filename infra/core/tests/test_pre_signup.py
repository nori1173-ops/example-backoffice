"""`pre_signup.app` のテスト（設計 6.2）。"""

import pytest
from pre_signup.app import handler


def make_event(email: str | None) -> dict:
    """Cognito PreSignUp トリガーのイベントを組む。`email=None` で属性そのものを欠落させる。"""
    user_attributes = {} if email is None else {"email": email}
    return {
        "request": {"userAttributes": user_attributes},
        "response": {},
    }


def test_allows_example_domain():
    event = make_event("devA@example.co.jp")

    assert handler(event, None) == event


def test_allows_uppercase_domain():
    event = make_event("DEVA@EXAMPLE.CO.JP")

    assert handler(event, None) == event


def test_rejects_external_domain():
    with pytest.raises(Exception, match="example.co.jp"):
        handler(make_event("someone@gmail.com"), None)


def test_rejects_lookalike_domain():
    with pytest.raises(Exception, match="example.co.jp"):
        handler(make_event("a@fake-example.co.jp"), None)


def test_rejects_missing_email():
    with pytest.raises(Exception, match="email"):
        handler(make_event(None), None)


def test_rejects_email_without_at():
    with pytest.raises(Exception, match="format"):
        handler(make_event("noatmark"), None)


def test_rejects_multiple_at_marks():
    """`a@evil.com@example.co.jp` のような多重 `@` を弾く（SEC-13）。"""
    with pytest.raises(Exception, match="format"):
        handler(make_event("a@evil.com@example.co.jp"), None)


def test_allows_surrounding_whitespace_and_uppercase():
    """前後空白と大文字は正規化してから判定する（SEC-13）。"""
    event = make_event(" DEVA@EXAMPLE.CO.JP ")

    assert handler(event, None) == event


def test_rejects_domain_with_suffix():
    """`example.co.jp` で始まるだけのドメインを弾く（SEC-13）。"""
    with pytest.raises(Exception, match="example.co.jp"):
        handler(make_event("a@example.co.jp.evil.com"), None)


def test_rejects_subdomain():
    with pytest.raises(Exception, match="example.co.jp"):
        handler(make_event("a@mail.example.co.jp"), None)
