"""Cognito PreSignUp トリガー（設計 6.2）。

`@example.co.jp` 以外のメールアドレスによるセルフサインアップを拒否する。
Cognito は例外が投げられた場合に登録を失敗させる。
"""

ALLOWED_DOMAIN = "example.co.jp"


def handler(event: dict, context) -> dict:
    email = event.get("request", {}).get("userAttributes", {}).get("email", "")
    if not email:
        raise Exception("email attribute is required")

    address = str(email).strip().lower()
    if address.count("@") != 1:
        raise Exception("email format is invalid")
    if address.split("@")[1] != ALLOWED_DOMAIN:
        raise Exception(f"Signup is restricted to @{ALLOWED_DOMAIN} domain only")

    return event
