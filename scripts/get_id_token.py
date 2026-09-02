#!/usr/bin/env python3
"""Cognito の ID トークンを取得して標準出力へ 1 行で出す（E2E / 手動 API 確認用）。

使い方::

    ID_TOKEN=$(uv run python scripts/get_id_token.py dev devA@example.co.jp)
    uv run python scripts/get_id_token.py dev devA@example.co.jp --totp-code 123456

User Pool ID と App Client ID は SSM（設計 4.2 の契約キー）から読む。SPA 用 App Client
（ALLOW_USER_SRP_AUTH・シークレット無し）をそのまま使うため、E2E 専用の App Client は
作らない。パスワードは引数では受け取らず、環境変数 COGNITO_PASSWORD か対話入力で渡す。
アクセストークン・リフレッシュトークン・パスワードは出力しない。
"""

import argparse
import getpass
import os
import sys

import boto3
from pycognito import Cognito
from pycognito.exceptions import SoftwareTokenMFAChallengeException

PROJECT = "example-backoffice"
VALID_ENVS = ("dev", "prod")
DEFAULT_REGION = "ap-northeast-1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="get_id_token.py",
        description="Cognito の ID トークンを標準出力へ出す",
    )
    parser.add_argument("env", help="dev または prod")
    parser.add_argument("email", help="サインインするユーザーのメールアドレス")
    parser.add_argument("--totp-code", default=None, help="TOTP の 6 桁コード（省略時は対話入力）")
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", DEFAULT_REGION),
        help=f"SSM / Cognito のリージョン（既定 {DEFAULT_REGION}）",
    )
    return parser


def get_ssm_parameter(ssm_client, name: str) -> str:
    return ssm_client.get_parameter(Name=name)["Parameter"]["Value"]


def resolve_password(email: str) -> str:
    password = os.environ.get("COGNITO_PASSWORD")
    if password:
        return password
    return getpass.getpass(f"Password for {email}: ")


def resolve_totp_code(totp_code: str | None) -> str:
    if totp_code:
        return totp_code.strip()
    return input("TOTP code: ").strip()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.env not in VALID_ENVS:
        parser.print_usage(sys.stderr)
        print(f"env は {' / '.join(VALID_ENVS)} のいずれかを指定してください", file=sys.stderr)
        return 1

    base = f"/{PROJECT}/{args.env}"
    ssm_client = boto3.client("ssm", region_name=args.region)
    user_pool_id = get_ssm_parameter(ssm_client, f"{base}/cognito/user-pool-id")
    client_id = get_ssm_parameter(ssm_client, f"{base}/cognito/client-id")

    password = resolve_password(args.email)
    if not password:
        print("パスワードが空です", file=sys.stderr)
        return 1

    user = Cognito(user_pool_id, client_id, user_pool_region=args.region, username=args.email)
    try:
        user.authenticate(password=password)
    except SoftwareTokenMFAChallengeException:
        code = resolve_totp_code(args.totp_code)
        if not code:
            print("TOTP コードが空です", file=sys.stderr)
            return 1
        user.respond_to_software_token_mfa_challenge(code)

    if not user.id_token:
        print("ID トークンを取得できませんでした", file=sys.stderr)
        return 1

    print(user.id_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
