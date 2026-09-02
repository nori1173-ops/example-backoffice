"""`notifier.slack_sender` のテスト（設計 8.5）。

秘密は moto の SSM に実際に `put_parameter` して読ませ、HTTP だけをスタブに
差し替える。トークンと Webhook URL がログ・例外メッセージに出ないことも確かめる。
"""

import logging
import urllib.error
import urllib.request

import boto3
import pytest
from aws_lambda_powertools.utilities import parameters
from moto import mock_aws
from notifier.slack_sender import (
    TIMEOUT_SECONDS,
    SlackApiError,
    SlackNotConfiguredError,
    http_post_json,
    send_slack,
)

REGION = "ap-northeast-1"
SSM_PREFIX = "/example-backoffice/dev"
BOT_TOKEN = "xoxb-dummy-bot-token"
WEBHOOK_URL = "https://hooks.slack.com/services/T00000000/B00000000/dummy"
CHANNEL = "C0123ABCDEF"
POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
LOGGER_NAME = "example_backoffice"


class FakeHttpPost:
    """`http_post` の差し替え。POST の引数を記録し、決めた本文を返す。"""

    def __init__(self, body: str = '{"ok": true}') -> None:
        self.body = body
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, headers: dict) -> str:
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self.body

    @property
    def call(self) -> dict:
        assert len(self.calls) == 1
        return self.calls[0]


@pytest.fixture
def ssm():
    with mock_aws():
        parameters.clear_caches()
        yield boto3.client("ssm", region_name=REGION)
        parameters.clear_caches()


def put_secret(ssm, name: str, value: str) -> None:
    ssm.put_parameter(Name=f"{SSM_PREFIX}/shared/slack/{name}", Value=value, Type="SecureString")


def make_message(**slack_extra) -> dict:
    slack = {"channel": CHANNEL, "text": "月次請求データを出力しました。"}
    slack.update(slack_extra)
    return {
        "schema": "example-backoffice.notify.v1",
        "message_id": "11111111-2222-3333-4444-555555555555",
        "app": "saas-a-billing",
        "channels": ["slack"],
        "slack": slack,
    }


def test_uses_bot_token_when_present(ssm):
    put_secret(ssm, "bot-token", BOT_TOKEN)
    put_secret(ssm, "webhook-url", WEBHOOK_URL)
    http_post = FakeHttpPost()

    send_slack(message=make_message(), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert http_post.call["url"] == POST_MESSAGE_URL
    assert http_post.call["headers"]["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert http_post.call["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert http_post.call["payload"]["channel"] == CHANNEL
    assert http_post.call["payload"]["text"] == "月次請求データを出力しました。"


def test_falls_back_to_webhook_when_bot_token_absent(ssm, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    put_secret(ssm, "webhook-url", WEBHOOK_URL)
    http_post = FakeHttpPost(body="ok")

    send_slack(message=make_message(), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert http_post.call["url"] == WEBHOOK_URL
    assert http_post.call["payload"] == {"text": "月次請求データを出力しました。"}
    assert "Authorization" not in http_post.call["headers"]
    assert WEBHOOK_URL not in caplog.text


def test_raises_when_neither_configured(ssm):
    http_post = FakeHttpPost()

    with pytest.raises(SlackNotConfiguredError):
        send_slack(message=make_message(), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert http_post.calls == []


def test_mention_is_prepended(ssm):
    put_secret(ssm, "bot-token", BOT_TOKEN)
    http_post = FakeHttpPost()

    send_slack(message=make_message(mention="<!here>"), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert http_post.call["payload"]["text"] == "<!here>\n月次請求データを出力しました。"


def test_empty_mention_leaves_text_unchanged(ssm):
    put_secret(ssm, "bot-token", BOT_TOKEN)
    http_post = FakeHttpPost()

    send_slack(message=make_message(mention=""), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert http_post.call["payload"]["text"] == "月次請求データを出力しました。"


def test_blocks_are_forwarded_in_bot_mode(ssm):
    put_secret(ssm, "bot-token", BOT_TOKEN)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*月次請求データ*"}}]
    http_post = FakeHttpPost()

    send_slack(message=make_message(blocks=blocks), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert http_post.call["payload"]["blocks"] == blocks


def test_bot_api_error_raises(ssm):
    put_secret(ssm, "bot-token", BOT_TOKEN)
    http_post = FakeHttpPost(body='{"ok": false, "error": "channel_not_found"}')

    with pytest.raises(SlackApiError) as excinfo:
        send_slack(message=make_message(), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert "channel_not_found" in str(excinfo.value)
    assert BOT_TOKEN not in str(excinfo.value)
    assert BOT_TOKEN not in repr(excinfo.value)


def test_token_is_never_logged(ssm, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    put_secret(ssm, "bot-token", BOT_TOKEN)
    http_post = FakeHttpPost()

    send_slack(message=make_message(), ssm_prefix=SSM_PREFIX, http_post=http_post)

    logged = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert logged
    assert BOT_TOKEN not in caplog.text
    assert "xoxb-" not in caplog.text


def test_non_https_webhook_url_is_rejected(ssm):
    put_secret(ssm, "webhook-url", "hooks.slack.com/services/T00000000/B00000000/abcdefghijkl")
    http_post = FakeHttpPost()

    with pytest.raises(SlackNotConfiguredError) as excinfo:
        send_slack(message=make_message(), ssm_prefix=SSM_PREFIX, http_post=http_post)

    assert "hooks.slack.com" not in str(excinfo.value)
    assert http_post.calls == []


class FakeResponse:
    """`urlopen` の戻り値（コンテキストマネージャ）の代役。"""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_http_post_json_posts_utf8_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    body = http_post_json(POST_MESSAGE_URL, {"text": "月次請求"}, {"Content-Type": "application/json; charset=utf-8"})

    assert body == '{"ok": true}'
    assert captured["url"] == POST_MESSAGE_URL
    assert captured["method"] == "POST"
    assert captured["data"] == '{"text": "月次請求"}'.encode()
    assert captured["timeout"] == TIMEOUT_SECONDS
    assert captured["headers"]["Content-type"] == "application/json; charset=utf-8"


def test_http_post_json_wraps_http_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SlackApiError) as excinfo:
        http_post_json(WEBHOOK_URL, {"text": "月次請求"}, {})

    assert "503" in str(excinfo.value)
    assert WEBHOOK_URL not in str(excinfo.value)
