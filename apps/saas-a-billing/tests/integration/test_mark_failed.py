"""`batch.handlers.mark_failed` のテスト（設計 6.3 / 6.8 / 11.1）。

moto の DynamoDB / SQS に実際に読み書きし、ステートマシンの全体 `Catch` が積んだ
`$.error` を入力にして RUN の失敗記録と失敗通知を確かめる。`Cause` は Lambda が
投げた例外を Step Functions が JSON にしたものなので、`errorMessage` が取れる形と
取れない形の双方を通す。

通知の投函が落ちてもハンドラは例外にしない（設計 11.1）ことを、キューを差し替えた
`send` の失敗で実測する。
"""

import json
import logging
from datetime import datetime
from types import SimpleNamespace

import boto3
import pytest
from batch.handlers import mark_failed
from botocore.exceptions import ClientError
from saas_a_billing import repo
from saas_a_billing.period import JST

from example_backoffice import notify
from tests.conftest import make_settings

VERIFICATION = "verification"
MONTH = "2026-10"
ATTEMPT = 1
RUN_ID = f"{VERIFICATION}-{MONTH}-{ATTEMPT}"
RUN_TOKEN = "run-token-1"
ACCESS_TOKEN = "dummy-access-token-for-test"
PASSWORD = "dummy-password"
EXPIRES_AT = "2026-10-21 08:48:25"
EXECUTION_ARN = "arn:aws:states:ap-northeast-1:000000000000:execution:saas-a-billing-run:verification-2026-10-1"
SLACK_CHANNEL = "C0123456789"
SLACK_MENTION = "<!here>"
NOW = datetime(2026, 10, 21, 6, 0, tzinfo=JST)

ERROR_NAME = "SaasAApiError"
ERROR_MESSAGE = "SaasAApiError: getToken が 401 を返しました"
TARGET_ENV_NOT_CONFIGURED = "TARGET_ENV_NOT_CONFIGURED: verification の接続情報が SSM にありません"
MAX_ERROR_LENGTH = 1000
MASK = "***"

FAILURE_SUBJECT = "【SaaS_A 月額課金レポート】2026年10月分の集計に失敗しました"
PORTAL_URL = f"https://backoffice-dev.example-cloud.com/saas-a-billing/runs/{VERIFICATION}/{MONTH}"
LOGGER_NAME = "example_backoffice"


def reset() -> None:
    repo.reset_clients()
    notify.reset_clients()


@pytest.fixture
def aws(dynamodb_table, notify_queue):
    """テーブルと通知キューを立て、boto3 クライアントのキャッシュを捨てる。"""
    reset()
    yield SimpleNamespace(table=dynamodb_table, queue=notify_queue)
    reset()


def cause_json(message: str) -> str:
    """Lambda が投げた例外を Step Functions が `Cause` に詰めた形。"""
    return json.dumps({"errorType": ERROR_NAME, "errorMessage": message, "trace": []}, ensure_ascii=False)


def make_event(**overrides) -> dict:
    """`Init` が失敗したときの `MarkFailed` の入力（実行入力 + 全体 Catch の `$.error`）。"""
    event = {
        "trigger": "scheduled",
        "target_env": VERIFICATION,
        "month": MONTH,
        "attempt": ATTEMPT,
        "run_token": RUN_TOKEN,
        "distribute": True,
        "force": False,
        "execution_arn": EXECUTION_ARN,
        "error": {"Error": ERROR_NAME, "Cause": cause_json(ERROR_MESSAGE)},
    }
    event.update(overrides)
    return event


def put_settings(aws, **overrides) -> dict:
    settings = make_settings(**overrides)
    aws.table.put_item(Item={**settings, "PK": "SETTINGS", "SK": "current"})
    return settings


def prepare_run(target_env: str = VERIFICATION) -> None:
    """`Init` がトークンを保存した直後の RUN（`running`）。"""
    repo.acquire_run(target_env, MONTH, trigger="scheduled", force=False, run_token=RUN_TOKEN, stale_before=NOW)
    repo.start_run(target_env, MONTH, ATTEMPT, RUN_TOKEN)
    repo.set_token(target_env, MONTH, ATTEMPT, ACCESS_TOKEN, EXPIRES_AT)


def prepare(aws, *, target_env: str = VERIFICATION, **overrides) -> None:
    put_settings(aws, target_env=target_env, **overrides)
    prepare_run(target_env)


def raise_send_error(**kwargs) -> str:
    """SQS への投函が失敗する状況（設計 11.1）。"""
    error = {"Error": {"Code": "AWS.SimpleQueueService.NonExistentQueue", "Message": "queue does not exist"}}
    raise ClientError(error, "SendMessage")


def run_of(target_env: str = VERIFICATION) -> dict:
    return repo.get_run(target_env, MONTH)


def raw_run(aws, target_env: str = VERIFICATION) -> dict:
    return aws.table.get_item(Key={"PK": f"RUN#{target_env}", "SK": MONTH})["Item"]


def queued_messages(aws) -> list[dict]:
    response = boto3.client("sqs").receive_message(QueueUrl=aws.queue, MaxNumberOfMessages=10)
    return [json.loads(message["Body"]) for message in response.get("Messages", [])]


def test_run_becomes_failed_with_error_summary(aws):
    prepare(aws)

    output = mark_failed.handler(make_event(), None)

    run = run_of()
    assert run["status"] == repo.FAILED
    assert run["error"] == ERROR_MESSAGE
    assert run["finished_at"]
    assert output == {
        "run_id": RUN_ID,
        "month": MONTH,
        "target_env": VERIFICATION,
        "status": "failed",
        "error": ERROR_MESSAGE,
    }


def test_error_summary_extracted_from_cause_json(aws):
    prepare(aws)
    message = "PriceTableNotFoundError: 2026-10-20 時点で有効な単価表の版がありません"

    output = mark_failed.handler(make_event(error={"Error": ERROR_NAME, "Cause": cause_json(message)}), None)

    assert output["error"] == message
    assert run_of()["error"] == message


@pytest.mark.parametrize(
    "cause",
    ["States.Timeout により実行を打ち切りました", json.dumps({"errorType": ERROR_NAME}), json.dumps("boom")],
)
def test_error_summary_falls_back_to_error_field(aws, cause):
    prepare(aws)

    output = mark_failed.handler(make_event(error={"Error": ERROR_NAME, "Cause": cause}), None)

    assert output["error"] == ERROR_NAME
    assert run_of()["error"] == ERROR_NAME


def test_error_summary_is_truncated(aws):
    prepare(aws)
    long_message = "え" * 1500

    output = mark_failed.handler(make_event(error={"Error": ERROR_NAME, "Cause": cause_json(long_message)}), None)

    assert len(output["error"]) == MAX_ERROR_LENGTH
    assert output["error"] == long_message[:MAX_ERROR_LENGTH]
    assert run_of()["error"] == output["error"]


def test_token_attributes_are_removed(aws):
    """`Aggregate` が落ちた場合の入力（`Init` の出力 + `$.error`）でもトークンを残さない。"""
    prepare(aws)
    assert "api_token" in run_of()

    mark_failed.handler(make_event(run_id=RUN_ID), None)

    run = run_of()
    assert "api_token" not in run
    assert "token_expires_at" not in run
    assert "api_token" not in raw_run(aws)


def test_sends_failure_notification_to_mail_and_slack(aws):
    prepare(aws, mail_enabled=True, slack_enabled=True, slack_channel_id=SLACK_CHANNEL, slack_mention=SLACK_MENTION)

    mark_failed.handler(make_event(), None)

    sent = queued_messages(aws)
    assert len(sent) == 1
    message = sent[0]
    assert message["app"] == "saas-a-billing"
    assert message["channels"] == ["email", "slack"]
    assert message["email"]["to"] == ["devA@example.co.jp"]
    assert message["email"]["subject"] == FAILURE_SUBJECT
    assert f"原因: {ERROR_MESSAGE}" in message["email"]["text"]
    assert message["slack"]["channel"] == SLACK_CHANNEL
    assert message["slack"]["mention"] == SLACK_MENTION
    assert message["slack"]["text"] == message["email"]["text"]


def test_failure_notification_contains_portal_url(aws):
    prepare(aws, mail_enabled=True, slack_enabled=True, slack_channel_id=SLACK_CHANNEL)

    mark_failed.handler(make_event(), None)

    message = queued_messages(aws)[0]
    assert message["email"]["text"].endswith(PORTAL_URL)
    assert f"\n\n{PORTAL_URL}" in message["slack"]["text"]


def test_notify_failure_is_swallowed_and_logged(aws, caplog, monkeypatch):
    prepare(aws, mail_enabled=True, slack_channel_id=SLACK_CHANNEL)
    monkeypatch.setattr(mark_failed, "send", raise_send_error)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        output = mark_failed.handler(make_event(), None)

    assert output["status"] == "failed"
    assert run_of()["status"] == repo.FAILED
    assert queued_messages(aws) == []
    assert "NonExistentQueue" in caplog.text


def test_slack_channel_empty_sends_email_only(aws):
    prepare(aws, mail_enabled=True, slack_enabled=True)

    mark_failed.handler(make_event(), None)

    message = queued_messages(aws)[0]
    assert message["channels"] == ["email"]
    assert "slack" not in message


def test_slack_disabled_keeps_channel_silent(aws):
    """チャンネル ID が保存されていても Slack 配布 OFF なら投稿しない（2026-09-01 の実測バグ）。"""
    prepare(aws, mail_enabled=True, slack_channel_id=SLACK_CHANNEL)

    mark_failed.handler(make_event(), None)

    message = queued_messages(aws)[0]
    assert message["channels"] == ["email"]
    assert "slack" not in message


def test_both_disabled_sends_no_notification(aws):
    """配布が両方 OFF なら失敗通知は投函しない（失敗の記録と応答は従来どおり）。"""
    prepare(aws, slack_channel_id=SLACK_CHANNEL)

    output = mark_failed.handler(make_event(), None)

    assert output["status"] == "failed"
    assert run_of()["status"] == repo.FAILED
    assert queued_messages(aws) == []


def test_no_notification_when_run_distribute_is_false(aws):
    """配布チェックを外した RUN は、設定でメール・Slack が ON でも失敗通知を送らない。

    手動実行の「配布しない」は「このテストでは一切送るな」の意思表示で、設定より優先する。
    """
    put_settings(aws, mail_enabled=True, slack_enabled=True, slack_channel_id=SLACK_CHANNEL)
    repo.acquire_run(
        VERIFICATION, MONTH, trigger="manual", force=False, run_token=RUN_TOKEN, distribute=False, stale_before=NOW
    )
    repo.start_run(VERIFICATION, MONTH, ATTEMPT, RUN_TOKEN)

    output = mark_failed.handler(make_event(), None)

    assert output["status"] == "failed"
    assert run_of()["status"] == repo.FAILED
    assert queued_messages(aws) == []


def test_target_env_not_configured_error_is_reported(aws):
    prepare(aws, mail_enabled=True, slack_enabled=True, slack_channel_id=SLACK_CHANNEL)

    output = mark_failed.handler(
        make_event(error={"Error": "ValueError", "Cause": cause_json(TARGET_ENV_NOT_CONFIGURED)}), None
    )

    message = queued_messages(aws)[0]
    assert output["error"] == TARGET_ENV_NOT_CONFIGURED
    assert run_of()["error"] == TARGET_ENV_NOT_CONFIGURED
    assert TARGET_ENV_NOT_CONFIGURED in message["email"]["text"]
    assert TARGET_ENV_NOT_CONFIGURED in message["slack"]["text"]


def test_secrets_are_not_included_in_error_summary(aws, ssm_targets, caplog):
    """上流例外にトークンが混ざっても RUN・通知本文・ログには出さない（設計 12.3）。"""
    prepare(aws, mail_enabled=True, slack_enabled=True, slack_channel_id=SLACK_CHANNEL)
    leaked = f"SaasAApiError: 401 Unauthorized（Authorization: Bearer {ACCESS_TOKEN}）"

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        output = mark_failed.handler(make_event(error={"Error": ERROR_NAME, "Cause": cause_json(leaked)}), None)

    sent = json.dumps(queued_messages(aws), ensure_ascii=False)
    assert output["error"] == leaked.replace(ACCESS_TOKEN, MASK)
    assert f"Bearer {MASK}" in sent
    for secret in (ACCESS_TOKEN, PASSWORD):
        assert secret not in sent
        assert secret not in caplog.text
        assert secret not in json.dumps(output, ensure_ascii=False)
        assert secret not in json.dumps(run_of(), ensure_ascii=False)
