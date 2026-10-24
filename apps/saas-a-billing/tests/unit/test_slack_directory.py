"""`slack_directory.py` のユニットテスト（#1011）。

Slack Web API は `responses` でスタブし、実ネットワークには出ない。ページングは
応答を順番に積む必要があるため `OrderedRegistry` を使う（`test_saasa_client.py`
と同じ流儀）。SSM は moto の `mock_aws` に登録した SecureString を読み、TTL の
判定は `_now` を差し替えて進める。
"""

import logging
from datetime import UTC, datetime, timedelta

import boto3
import pytest
import requests
import responses
from saas_a_billing import slack_directory
from responses import registries

from tests.conftest import ENV, REGION

CONVERSATIONS_URL = "https://slack.com/api/conversations.list"
USERS_URL = "https://slack.com/api/users.list"
BOT_TOKEN_PARAMETER = f"/example-backoffice/{ENV}/shared/slack/bot-token"
BOT_TOKEN = "xoxb-dummy-token-for-test"

NOW = datetime(2026, 9, 4, 2, 30, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    """`_now` を差し替えて時計を手で進める。"""
    holder = {"now": NOW}
    monkeypatch.setattr(slack_directory, "_now", lambda: holder["now"])
    return holder


@pytest.fixture
def slack_token(aws_mock, clock):
    """Bot token を SSM に登録し、モジュールのキャッシュを前後で捨てる。"""
    slack_directory.reset_clients()
    boto3.client("ssm", region_name=REGION).put_parameter(
        Name=BOT_TOKEN_PARAMETER, Value=BOT_TOKEN, Type="SecureString"
    )
    yield BOT_TOKEN
    slack_directory.reset_clients()


@pytest.fixture
def no_slack_token(aws_mock, clock):
    """Bot token が未登録の SSM。"""
    slack_directory.reset_clients()
    yield
    slack_directory.reset_clients()


def make_channel(channel_id: str, name: str, is_private: bool = False, is_member: bool = False) -> dict:
    return {
        "id": channel_id,
        "name": name,
        "is_private": is_private,
        "is_member": is_member,
        "is_archived": False,
        "created": 1700000000,
    }


def make_user(user_id: str, handle: str, real_name: str = "", display_name: str = "", **overrides) -> dict:
    user = {
        "id": user_id,
        "name": handle,
        "deleted": False,
        "is_bot": False,
        "real_name": real_name,
        "profile": {"real_name": real_name, "display_name": display_name},
    }
    user.update(overrides)
    return user


def channels_page(channels: list[dict], cursor: str = "") -> dict:
    return {"ok": True, "channels": channels, "response_metadata": {"next_cursor": cursor}}


def users_page(members: list[dict], cursor: str = "") -> dict:
    return {"ok": True, "members": members, "response_metadata": {"next_cursor": cursor}}


def add_directory(channels: list[dict], members: list[dict]) -> None:
    """1 ページずつの `conversations.list` / `users.list` を積む。"""
    responses.get(CONVERSATIONS_URL, json=channels_page(channels))
    responses.get(USERS_URL, json=users_page(members))


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_joins_pages_and_sorts_channels_by_name(slack_token):
    responses.get(CONVERSATIONS_URL, json=channels_page([make_channel("C2", "zzz-last")], cursor="page2"))
    responses.get(CONVERSATIONS_URL, json=channels_page([make_channel("C1", "proj-saas-a-billing")]))
    responses.get(USERS_URL, json=users_page([]))

    directory = slack_directory.get_directory()

    assert [channel["name"] for channel in directory["channels"]] == ["proj-saas-a-billing", "zzz-last"]
    assert directory["channels"][0] == {
        "id": "C1",
        "name": "proj-saas-a-billing",
        "is_private": False,
        "is_member": False,
    }


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_keeps_private_and_member_flags(slack_token):
    add_directory([make_channel("G1", "secret", is_private=True, is_member=True)], [])

    directory = slack_directory.get_directory()

    assert directory["channels"] == [
        {"id": "G1", "name": "secret", "is_private": True, "is_member": True}
    ]


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_joins_user_pages(slack_token):
    responses.get(CONVERSATIONS_URL, json=channels_page([]))
    responses.get(USERS_URL, json=users_page([make_user("U2", "colleagueb", "ColleagueB", "関係者B")], cursor="page2"))
    responses.get(USERS_URL, json=users_page([make_user("U1", "devA", "DevA", "開発者本人")]))

    directory = slack_directory.get_directory()

    assert [user["id"] for user in directory["users"]] == ["U1", "U2"]
    assert directory["users"][0] == {
        "id": "U1",
        "handle": "devA",
        "real_name": "DevA",
        "display_name": "開発者本人",
    }


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_excludes_bots_deleted_and_slackbot(slack_token):
    add_directory(
        [],
        [
            make_user("U1", "devA", "DevA", "開発者本人"),
            make_user("U2", "leaver", "Leaver", "退職者", deleted=True),
            make_user("B1", "examplebot", "ExampleBot", "ExampleBot", is_bot=True),
            make_user("USLACKBOT", "slackbot", "Slackbot", "Slackbot"),
        ],
    )

    directory = slack_directory.get_directory()

    assert [user["id"] for user in directory["users"]] == ["U1"]


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_keeps_display_name_empty_and_sorts_by_real_name(slack_token):
    """`display_name` が空でも捨てず、並び順だけ `real_name` で決める（表示は画面側）。"""
    add_directory(
        [],
        [
            make_user("U2", "colleaguec", "ColleagueC", ""),
            make_user("U1", "devA", "DevA", "開発者本人"),
        ],
    )

    directory = slack_directory.get_directory()

    assert [user["display_name"] for user in directory["users"]] == ["", "開発者本人"]
    assert [user["real_name"] for user in directory["users"]] == ["ColleagueC", "DevA"]


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_sends_bearer_token_and_paging_params(slack_token):
    add_directory([make_channel("C1", "general")], [make_user("U1", "devA", "DevA", "開発者本人")])

    slack_directory.get_directory()

    request = responses.calls[0].request
    assert request.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert request.params["types"] == "public_channel,private_channel"
    assert request.params["exclude_archived"] == "true"
    assert request.params["limit"] == "200"


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_returns_fetched_at_in_utc(slack_token, clock):
    add_directory([], [])

    directory = slack_directory.get_directory()

    assert directory["fetched_at"] == "2026-09-04T02:30:00Z"


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_serves_cache_within_ttl(slack_token, clock):
    add_directory([make_channel("C1", "general")], [])

    first = slack_directory.get_directory()
    clock["now"] = NOW + timedelta(seconds=slack_directory.CACHE_TTL_SEC - 1)
    second = slack_directory.get_directory()

    assert second == first
    assert len(responses.calls) == 2


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_refetches_after_ttl(slack_token, clock):
    add_directory([make_channel("C1", "general")], [])
    add_directory([make_channel("C2", "renamed")], [])

    slack_directory.get_directory()
    clock["now"] = NOW + timedelta(seconds=slack_directory.CACHE_TTL_SEC)
    second = slack_directory.get_directory()

    assert [channel["id"] for channel in second["channels"]] == ["C2"]
    assert second["fetched_at"] == "2026-09-04T02:35:00Z"


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_refresh_ignores_cache(slack_token, clock):
    add_directory([make_channel("C1", "general")], [])
    add_directory([make_channel("C2", "renamed")], [])

    slack_directory.get_directory()
    second = slack_directory.get_directory(refresh=True)

    assert [channel["id"] for channel in second["channels"]] == ["C2"]
    assert len(responses.calls) == 4


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_raises_api_error_with_slack_error_string(slack_token):
    responses.get(CONVERSATIONS_URL, json={"ok": False, "error": "missing_scope"})

    with pytest.raises(slack_directory.SlackApiError) as error:
        slack_directory.get_directory()

    assert "missing_scope" in str(error.value)


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_does_not_cache_failures(slack_token):
    responses.get(CONVERSATIONS_URL, json={"ok": False, "error": "ratelimited"})
    add_directory([make_channel("C1", "general")], [])

    with pytest.raises(slack_directory.SlackApiError):
        slack_directory.get_directory()
    directory = slack_directory.get_directory()

    assert [channel["id"] for channel in directory["channels"]] == ["C1"]


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_raises_unavailable_on_network_error(slack_token):
    responses.get(CONVERSATIONS_URL, body=requests.ConnectionError("boom"))

    with pytest.raises(slack_directory.SlackUnavailableError):
        slack_directory.get_directory()


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_raises_unavailable_on_non_json_body(slack_token):
    responses.get(CONVERSATIONS_URL, body="<html>502</html>", content_type="text/html")

    with pytest.raises(slack_directory.SlackUnavailableError):
        slack_directory.get_directory()


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_raises_not_configured_without_token(no_slack_token):
    with pytest.raises(slack_directory.SlackNotConfiguredError):
        slack_directory.get_directory()

    assert len(responses.calls) == 0


@responses.activate(registry=registries.OrderedRegistry)
def test_not_configured_message_hides_the_parameter_path(no_slack_token, caplog):
    """画面に出る文言に SSM の内部パスを載せない。パスはログにだけ残す。"""
    with (
        caplog.at_level(logging.WARNING, logger="example_backoffice"),
        pytest.raises(slack_directory.SlackNotConfiguredError) as error,
    ):
        slack_directory.get_directory()

    assert "/example-backoffice/" not in str(error.value)
    assert BOT_TOKEN_PARAMETER in caplog.text


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_reports_rate_limit_in_plain_words(slack_token):
    responses.get(CONVERSATIONS_URL, json={"ok": False, "error": "ratelimited"}, status=429)

    with pytest.raises(slack_directory.SlackUnavailableError) as error:
        slack_directory.get_directory()

    assert "混み合" in str(error.value)


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_includes_http_status_for_server_error(slack_token):
    responses.get(CONVERSATIONS_URL, json={"ok": False}, status=503)

    with pytest.raises(slack_directory.SlackUnavailableError) as error:
        slack_directory.get_directory()

    assert "503" in str(error.value)
    assert BOT_TOKEN not in str(error.value)


@responses.activate(registry=registries.OrderedRegistry)
def test_get_directory_stops_after_page_max_and_warns(slack_token, caplog):
    """`next_cursor` が返り続けても `PAGE_MAX` 回で止め、切り捨てを警告に残す。"""
    for index in range(slack_directory.PAGE_MAX):
        responses.get(
            CONVERSATIONS_URL,
            json=channels_page([make_channel(f"C{index:02d}", f"ch-{index:02d}")], cursor="more"),
        )
    responses.get(USERS_URL, json=users_page([]))

    with caplog.at_level(logging.WARNING, logger="example_backoffice"):
        directory = slack_directory.get_directory()

    assert len(directory["channels"]) == slack_directory.PAGE_MAX
    assert "打ち切" in caplog.text
