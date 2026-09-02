"""`example_backoffice.mail` のテスト（SEC-07 / findings-security.md）。

宛先ドメインの許可判定は `PUT /settings`（入口）と notifier（送信直前）の両方から
使うため、規則が 1 か所に揃っていることをここで固定する。
"""

import pytest

from example_backoffice import mail

INTERNAL = "devA@example.co.jp"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(mail.ALLOWED_MAIL_DOMAINS_ENV, raising=False)


def test_default_allowed_domain_is_example():
    assert mail.allowed_mail_domains() == ("example.co.jp",)


@pytest.mark.parametrize("address", [INTERNAL, "DEVA@EXAMPLE.CO.JP", "devA@Example.Co.Jp", "a.b+c@example.co.jp"])
def test_internal_addresses_are_allowed(address):
    assert mail.is_allowed_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "evil@example.com",
        "a@evil-example.co.jp",
        "a@example.co.jp.evil.com",
        "a@sub.example.co.jp",
        "a@b@example.co.jp",
        "example.co.jp",
        "",
        None,
        123,
        ["a@example.co.jp"],
    ],
)
def test_other_addresses_are_rejected(address):
    assert mail.is_allowed_address(address) is False


def test_env_overrides_allowed_domains(monkeypatch):
    monkeypatch.setenv(mail.ALLOWED_MAIL_DOMAINS_ENV, " @Example.COM , example.co.jp ")
    assert mail.allowed_mail_domains() == ("example.com", "example.co.jp")
    assert mail.is_allowed_address("a@example.com") is True
    assert mail.is_allowed_address("a@other.com") is False


def test_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(mail.ALLOWED_MAIL_DOMAINS_ENV, " , ")
    assert mail.allowed_mail_domains() == mail.DEFAULT_ALLOWED_MAIL_DOMAINS
