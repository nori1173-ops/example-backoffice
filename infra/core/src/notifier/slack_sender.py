"""Slack 投稿（設計 8.5）。

`bot-token` が SSM にあれば `chat.postMessage`、無ければ Incoming Webhook に
フォールバックする。HTTP は標準ライブラリの `urllib` のみを使う。
トークンと Webhook URL は秘密なので、ログにも例外メッセージにも出さない。
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable

from aws_lambda_powertools.utilities import parameters

from example_backoffice.logging import get_logger

POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
PARAMETER_SUFFIXES = (
    ("bot", "/shared/slack/bot-token"),
    ("webhook", "/shared/slack/webhook-url"),
)
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
PARAMETER_MAX_AGE = 300
TIMEOUT_SECONDS = 10
HTTPS_SCHEME = "https://"

logger = get_logger()


class SlackNotConfiguredError(Exception):
    """Slack の投稿先が決まらない。再試行しても直らない恒久エラー（設計 8.6）。"""


class SlackApiError(Exception):
    """Slack API が `ok: false` を返した。"""


def http_post_json(url: str, payload: dict, headers: dict[str, str]) -> str:
    """JSON を POST してレスポンス本文を返す（既定の HTTP 実装）。

    通信失敗は `SlackApiError` に包み直す。元の例外は URL や本文を持つため、
    メッセージにはステータスコードだけを残す。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise SlackApiError(f"Slack への POST が失敗しました: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SlackApiError("Slack への POST が失敗しました: 接続エラー") from exc


def _optional_parameter(name: str) -> str | None:
    """SSM のパラメータを読む。存在しなければ `None`（他のエラーは投げ直す）。"""
    try:
        return parameters.get_parameter(name, decrypt=True, max_age=PARAMETER_MAX_AGE)
    except parameters.GetParameterError as exc:
        if "ParameterNotFound" in str(exc):
            return None
        raise


def resolve_mode(ssm_prefix: str) -> tuple[str, str]:
    """`("bot", token)` か `("webhook", url)` を返す。どちらも無ければ例外。"""
    prefix = ssm_prefix.rstrip("/")
    for mode, suffix in PARAMETER_SUFFIXES:
        secret = _optional_parameter(f"{prefix}{suffix}")
        if not secret:
            continue
        if mode == "webhook" and not secret.startswith(HTTPS_SCHEME):
            raise SlackNotConfiguredError(f"webhook-url が {HTTPS_SCHEME} で始まっていません")
        return mode, secret
    raise SlackNotConfiguredError(f"Slack の bot-token / webhook-url がどちらも {prefix} に登録されていません")


def build_text(message: dict) -> str:
    """`slack.mention` があれば本文の先頭に付ける。"""
    slack = message.get("slack") or {}
    text = slack.get("text") or ""
    mention = (slack.get("mention") or "").strip()
    return f"{mention}\n{text}" if mention else text


def _raise_for_api_error(body: str) -> None:
    result = json.loads(body)
    if not result.get("ok"):
        raise SlackApiError(f"Slack API がエラーを返しました: {result.get('error', 'unknown')}")


def send_slack(*, message: dict, ssm_prefix: str, http_post: Callable[[str, dict, dict], str] | None = None) -> None:
    """Slack に 1 件投稿する。"""
    slack = message.get("slack") or {}
    mode, secret = resolve_mode(ssm_prefix)
    post = http_post or http_post_json
    text = build_text(message)

    channel = None
    if mode == "bot":
        channel = slack.get("channel")
        if not channel:
            raise SlackNotConfiguredError("bot モードでは slack.channel（チャンネル ID）が必要です")
        payload = {"channel": channel, "text": text}
        if slack.get("blocks"):
            payload["blocks"] = list(slack["blocks"])
        headers = {"Authorization": f"Bearer {secret}", "Content-Type": JSON_CONTENT_TYPE}
        _raise_for_api_error(post(POST_MESSAGE_URL, payload, headers))
    else:
        post(secret, {"text": text}, {"Content-Type": JSON_CONTENT_TYPE})

    logger.info(
        "Slack に投稿しました",
        {"message_id": message.get("message_id"), "mode": mode, "channel": channel},
    )
