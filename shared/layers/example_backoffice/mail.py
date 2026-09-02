"""メール宛先の許可ドメイン判定（設計 12.1 / SEC-07）。

課金明細の社外配布を止めるため、宛先はドメイン部の完全一致だけを通す。
`evil-example.co.jp` のような似せたドメイン、`example.co.jp.evil.com` のような
サフィックス偽装、サブドメイン（`sub.example.co.jp`）はいずれも通さない。

設定を書く側（`PUT /settings`）と送信直前（notifier）の両方から同じ規則を使う。
"""

import os

ALLOWED_MAIL_DOMAINS_ENV = "ALLOWED_MAIL_DOMAINS"
DEFAULT_ALLOWED_MAIL_DOMAINS = ("example.co.jp",)


def allowed_mail_domains() -> tuple[str, ...]:
    """許可する宛先ドメイン。環境変数 `ALLOWED_MAIL_DOMAINS`（カンマ区切り）で上書きできる。"""
    configured = (os.environ.get(ALLOWED_MAIL_DOMAINS_ENV) or "").split(",")
    domains = tuple(part.strip().lstrip("@").lower() for part in configured if part.strip())
    return domains or DEFAULT_ALLOWED_MAIL_DOMAINS


def is_allowed_address(address: object) -> bool:
    """`@` がちょうど 1 個で、ドメイン部が許可リストと完全一致するか。"""
    if not isinstance(address, str) or address.count("@") != 1:
        return False
    return address.split("@")[1].strip().lower() in allowed_mail_domains()


def allowed_mail_domains_label() -> str:
    """エラーメッセージ用の `@example.co.jp` 形式の表記。"""
    return " / ".join(f"@{domain}" for domain in allowed_mail_domains())
