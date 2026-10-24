"""設定画面の Slack 候補（チャンネル・ユーザー）を Slack Web API から集める。

「Slack チャンネル」「Slack メンション」の欄で `#` / `@` を打ったときに出す候補を
作るためだけに読む。投稿は notifier の担当で、このモジュールは書き込み系の API を
呼ばない。Bot token は SSM の SecureString から読み、応答にもログにも出さない。

`conversations.list` / `users.list` は 1 ワークスペースで数百件になるため
`response_metadata.next_cursor` を辿って全ページを結合し、整形済みのディレクトリを
モジュール変数に `CACHE_TTL_SEC` 秒だけ持つ（Lambda コンテナ単位）。設定画面から
`refresh=True` で取り直せる。失敗はキャッシュしない。
"""

import os
from datetime import UTC, datetime

import boto3
import requests
from botocore.exceptions import ClientError

from example_backoffice.logging import get_logger

CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"
USERS_LIST_URL = "https://slack.com/api/users.list"
SLACK_BOT_TOKEN_PARAMETER = "/example-backoffice/{env}/shared/slack/bot-token"  # noqa: S105 パラメータ名（値ではない）
PARAMETER_NOT_FOUND = "ParameterNotFound"

CHANNEL_TYPES = "public_channel,private_channel"
PAGE_LIMIT = 200
PAGE_MAX = 20
TIMEOUT = 10
CACHE_TTL_SEC = 300
SLACKBOT_ID = "USLACKBOT"
RATE_LIMITED = 429

_ssm_cache = None
_directory_cache: dict | None = None


class SlackNotConfiguredError(Exception):
    """Bot token が SSM に登録されていない（503）。"""


class SlackApiError(Exception):
    """Slack Web API が `ok: false` を返した（502）。`error` は Slack の文字列。"""

    def __init__(self, error: str):
        super().__init__(f"Slack API がエラーを返しました: {error}")
        self.error = error


class SlackUnavailableError(Exception):
    """Slack Web API に到達できない（502）。"""


def reset_clients() -> None:
    """boto3 クライアントとディレクトリのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _ssm_cache, _directory_cache
    _ssm_cache = None
    _directory_cache = None


def _ssm():
    global _ssm_cache
    if _ssm_cache is None:
        _ssm_cache = boto3.client("ssm")
    return _ssm_cache


def _now() -> datetime:
    return datetime.now(UTC)


def _bot_token() -> str:
    """SSM の SecureString。値は返り値としてしか出さない（ログにも例外にも載せない）。"""
    name = SLACK_BOT_TOKEN_PARAMETER.format(env=os.environ.get("ENV", "dev"))
    try:
        parameter = _ssm().get_parameter(Name=name, WithDecryption=True)
    except ClientError as error:
        if error.response["Error"]["Code"] != PARAMETER_NOT_FOUND:
            raise
        # パラメータ名は画面に出さず（内部構造を晒さない）ログにだけ残す
        get_logger().warning("Slack の Bot token が SSM にありません", {"parameter": name})
        raise SlackNotConfiguredError("Slack の Bot token が登録されていません") from error
    return parameter["Parameter"]["Value"]


def _http_error_message(status_code: int) -> str:
    """HTTP エラーの画面向け文言。トークンも内部パスも載せない。"""
    if status_code == RATE_LIMITED:
        return "Slack が混み合っています。少し待って再取得してください"
    return f"Slack API が HTTP {status_code} を返しました"


def _call(url: str, token: str, params: dict) -> dict:
    """Slack Web API を 1 回叩いて JSON を返す。`ok: false` は `SlackApiError`。"""
    try:
        result = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=TIMEOUT,
        )
        result.raise_for_status()
        payload = result.json()
    except requests.HTTPError as error:
        raise SlackUnavailableError(_http_error_message(result.status_code)) from error
    except requests.RequestException as error:
        raise SlackUnavailableError(f"Slack API に接続できませんでした（{url}）") from error
    if not payload.get("ok"):
        raise SlackApiError(str(payload.get("error") or "unknown_error"))
    return payload


def _pages(url: str, token: str, params: dict, key: str) -> list[dict]:
    """`next_cursor` を辿って全ページの `key` を結合する。`PAGE_MAX` で打ち切る。"""
    items: list[dict] = []
    cursor = ""
    for _ in range(PAGE_MAX):
        query = {**params, "limit": PAGE_LIMIT}
        if cursor:
            query["cursor"] = cursor
        payload = _call(url, token, query)
        items.extend(payload.get(key) or [])
        cursor = (payload.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    if cursor:
        get_logger().warning("Slack の候補を打ち切りました", {"url": url, "pages": PAGE_MAX, "items": len(items)})
    return items


def _channel_view(channel: dict) -> dict:
    return {
        "id": channel.get("id", ""),
        "name": channel.get("name", ""),
        "is_private": bool(channel.get("is_private")),
        "is_member": bool(channel.get("is_member")),
    }


def _is_person(member: dict) -> bool:
    return not (member.get("deleted") or member.get("is_bot") or member.get("id") == SLACKBOT_ID)


def _user_view(member: dict) -> dict:
    """`display_name` は空のまま返す。表示名の決め方（空なら `real_name`）は画面側。"""
    profile = member.get("profile") or {}
    return {
        "id": member.get("id", ""),
        "handle": member.get("name", ""),
        "real_name": profile.get("real_name") or member.get("real_name") or "",
        "display_name": profile.get("display_name") or "",
    }


def _fetch() -> dict:
    token = _bot_token()
    channels = _pages(
        CONVERSATIONS_LIST_URL,
        token,
        {"types": CHANNEL_TYPES, "exclude_archived": "true"},
        "channels",
    )
    members = _pages(USERS_LIST_URL, token, {}, "members")
    users = [_user_view(member) for member in members if _is_person(member)]
    return {
        "channels": sorted((_channel_view(channel) for channel in channels), key=lambda view: view["name"]),
        "users": sorted(users, key=lambda view: view["display_name"] or view["real_name"]),
    }


def get_directory(*, refresh: bool = False) -> dict:
    """候補ディレクトリ。`refresh` が真なら TTL 内でも取り直す。"""
    global _directory_cache
    now = _now()
    cached = _directory_cache
    if not refresh and cached and (now - cached["at"]).total_seconds() < CACHE_TTL_SEC:
        return cached["directory"]
    logger = get_logger()
    directory = {**_fetch(), "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    _directory_cache = {"at": now, "directory": directory}
    logger.info(
        "Slack の候補を取得しました",
        {"channels": len(directory["channels"]), "users": len(directory["users"])},
    )
    return directory
