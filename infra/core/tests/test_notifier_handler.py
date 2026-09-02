"""`notifier.app` のテスト（設計 8.1 / 8.6）。

冪等性は moto の DynamoDB に Powertools 標準のテーブル（PK `id`、TTL 属性
`expiration`）を作り、実物の `idempotent_function` で回す。SES と Slack の
送信だけをスタブに差し替え、恒久エラーと一時エラーの振り分けを確かめる。
"""

import json
import logging
import pathlib
import types
import uuid

import boto3
import jsonschema
import pytest
from moto import mock_aws
from notifier import app
from notifier.ses_sender import AttachmentNotFoundError
from notifier.slack_sender import SlackApiError, SlackNotConfiguredError

from example_backoffice import notify

REGION = "ap-northeast-1"
IDEMPOTENCY_TABLE = "example-backoffice-core-idempotency-dev"
DLQ_NAME = "example-backoffice-notify-dlq-dev"
ATTACHMENTS_BUCKET = "example-backoffice-core-attachments-dev"
SSM_PREFIX = "/example-backoffice/dev"
FROM_ADDRESS = "no-reply@backoffice-dev.example-cloud.com"
BOT_TOKEN = "xoxb-dummy-bot-token"
LOGGER_NAME = "example_backoffice"
APP_ID = "saas-a-billing"
CHANNEL_ID = "C0123ABCDEF"

SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[3] / "shared" / "schemas" / "notify.v1.json"


class FakeContext:
    """Powertools Idempotency が使う最小限の Lambda コンテキスト。"""

    aws_request_id = "11111111-1111-1111-1111-111111111111"

    @staticmethod
    def get_remaining_time_in_millis() -> int:
        return 60_000


class FakeSender:
    """`send_email` / `send_slack` の差し替え。呼び出しを記録する。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.errors_by_message_id: dict[str, Exception] = {}

    def __call__(self, *, message: dict, **kwargs) -> None:
        self.calls.append({"message": message, **kwargs})
        error = self.errors_by_message_id.get(message["message_id"], self.error)
        if error is not None:
            raise error


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name=REGION)
        dynamodb.create_table(
            TableName=IDEMPOTENCY_TABLE,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.update_time_to_live(
            TableName=IDEMPOTENCY_TABLE,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expiration"},
        )
        sqs = boto3.client("sqs", region_name=REGION)
        dlq_url = sqs.create_queue(QueueName=DLQ_NAME)["QueueUrl"]
        ssm = boto3.client("ssm", region_name=REGION)

        monkeypatch.setenv("ENV", "dev")
        monkeypatch.setenv("SES_FROM_ADDRESS", FROM_ADDRESS)
        monkeypatch.setenv("IDEMPOTENCY_TABLE", IDEMPOTENCY_TABLE)
        monkeypatch.setenv("DLQ_URL", dlq_url)
        monkeypatch.setenv("SSM_PREFIX", SSM_PREFIX)

        app.reset_clients()
        yield types.SimpleNamespace(sqs=sqs, ssm=ssm, dynamodb=dynamodb, dlq_url=dlq_url)
        app.reset_clients()


@pytest.fixture
def senders(monkeypatch):
    email = FakeSender()
    slack = FakeSender()
    monkeypatch.setattr(app, "send_email", email)
    monkeypatch.setattr(app, "send_slack", slack)
    return types.SimpleNamespace(email=email, slack=slack)


def make_message(channels=("email", "slack"), message_id: str | None = None) -> dict:
    message = {
        "schema": "example-backoffice.notify.v1",
        "message_id": message_id or str(uuid.uuid4()),
        "app": APP_ID,
        "channels": list(channels),
    }
    if "email" in channels:
        message["email"] = {
            "to": ["devA@example.co.jp"],
            "subject": "【SaaS_A】月額ライセンス料金一覧 2026年10月分",
            "text": "2026年10月分の月額課金レポートを送付します。",
        }
    if "slack" in channels:
        message["slack"] = {"channel": CHANNEL_ID, "text": "2026-10 分のレポートを送付しました。"}
    return message


def sqs_event(*messages: dict) -> dict:
    records = [
        {"messageId": f"sqs-{index}", "body": json.dumps(message, ensure_ascii=False)}
        for index, message in enumerate(messages)
    ]
    return {"Records": records}


def receive_dlq(aws) -> list[dict]:
    result = aws.sqs.receive_message(
        QueueUrl=aws.dlq_url,
        MaxNumberOfMessages=10,
        MessageAttributeNames=["All"],
    )
    return result.get("Messages", [])


def test_sends_email_and_slack_for_both_channels(aws, senders):
    message = make_message()

    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(senders.email.calls) == 1
    assert len(senders.slack.calls) == 1
    assert senders.email.calls[0]["from_address"] == FROM_ADDRESS
    assert senders.slack.calls[0]["ssm_prefix"] == SSM_PREFIX
    assert receive_dlq(aws) == []


def test_email_only_message_does_not_call_slack(aws, senders):
    result = app.handler(sqs_event(make_message(channels=("email",))), FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(senders.email.calls) == 1
    assert senders.slack.calls == []


def test_transient_error_returns_batch_item_failure(aws, senders):
    senders.email.error = RuntimeError("SES が一時的に応答しません")

    result = app.handler(sqs_event(make_message(channels=("email",))), FakeContext())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-0"}]}
    assert receive_dlq(aws) == []


def test_permanent_error_is_not_retried_and_goes_to_dlq(aws, senders):
    senders.email.error = AttachmentNotFoundError("添付が見つかりません: s3://bucket/key")
    message = make_message(channels=("email",))

    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    dlq = receive_dlq(aws)
    assert len(dlq) == 1
    assert json.loads(dlq[0]["Body"])["message_id"] == message["message_id"]
    assert dlq[0]["MessageAttributes"]["reason"]["StringValue"]


def test_schema_mismatch_goes_to_dlq(aws, senders):
    message = make_message(channels=("email",))
    message["schema"] = "example-backoffice.notify.v2"

    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    assert senders.email.calls == []
    assert len(receive_dlq(aws)) == 1


def test_slack_not_configured_goes_to_dlq(aws, senders):
    senders.slack.error = SlackNotConfiguredError("Slack の投稿先が登録されていません")

    result = app.handler(sqs_event(make_message(channels=("slack",))), FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(receive_dlq(aws)) == 1


def test_permanent_slack_api_error_goes_to_dlq(aws, senders):
    senders.slack.error = SlackApiError("Slack API がエラーを返しました: channel_not_found")

    result = app.handler(sqs_event(make_message(channels=("slack",))), FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(receive_dlq(aws)) == 1


def test_transient_slack_api_error_returns_batch_item_failure(aws, senders):
    senders.slack.error = SlackApiError("Slack への POST が失敗しました: HTTP 503")

    result = app.handler(sqs_event(make_message(channels=("slack",))), FakeContext())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-0"}]}
    assert receive_dlq(aws) == []


def test_email_success_slack_failure_does_not_resend_email_on_retry(aws, senders):
    event = sqs_event(make_message())
    senders.slack.error = RuntimeError("Slack が一時的に応答しません")

    first = app.handler(event, FakeContext())

    assert first == {"batchItemFailures": [{"itemIdentifier": "sqs-0"}]}

    senders.slack.error = None
    second = app.handler(event, FakeContext())

    assert second == {"batchItemFailures": []}
    assert len(senders.email.calls) == 1
    assert len(senders.slack.calls) == 2


def test_batch_size_one_processes_single_record(aws, senders):
    event = sqs_event(make_message(channels=("email",)))
    assert len(event["Records"]) == 1

    result = app.handler(event, FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(senders.email.calls) == 1


def test_multiple_records_report_only_failed_ones(aws, senders):
    messages = [make_message(channels=("email",)) for _ in range(3)]
    senders.email.errors_by_message_id[messages[1]["message_id"]] = RuntimeError("SES が一時的に応答しません")

    result = app.handler(sqs_event(*messages), FakeContext())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}
    assert len(senders.email.calls) == 3


def test_message_id_and_app_are_logged(aws, senders, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    aws.ssm.put_parameter(Name=f"{SSM_PREFIX}/shared/slack/bot-token", Value=BOT_TOKEN, Type="SecureString")
    message = make_message()

    app.handler(sqs_event(message), FakeContext())

    logged = "\n".join(record.getMessage() for record in caplog.records if record.name == LOGGER_NAME)
    assert message["message_id"] in logged
    assert APP_ID in logged
    assert "email" in logged
    assert "slack" in logged
    assert BOT_TOKEN not in caplog.text
    assert "xoxb-" not in caplog.text


def test_valid_message_from_notify_send_is_accepted(aws, senders):
    message = notify.build_message(
        app=APP_ID,
        channels=["email", "slack"],
        email=notify.Email(
            to=["devA@example.co.jp"],
            subject="【SaaS_A】月額ライセンス料金一覧 2026年10月分",
            text="2026年10月分の月額課金レポートを送付します。",
            html="<p>2026年10月分の月額課金レポートを送付します。</p>",
        ),
        attachments=[
            notify.Attachment(
                key="saas-a-billing/2026/10/saas_a_billing_2026-10.xlsx",
                filename="saas_a_billing_2026-10.xlsx",
                bucket=ATTACHMENTS_BUCKET,
            )
        ],
        slack=notify.Slack(text="2026-10 分のレポートを送付しました。", channel=CHANNEL_ID, mention="<!here>"),
        meta={"run_id": "RUN#production/2026-10"},
    )
    jsonschema.validate(instance=message, schema=json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

    app.validate_message(message)
    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(senders.email.calls) == 1
    assert len(senders.slack.calls) == 1


def test_non_dict_body_goes_to_dlq_without_crash(aws, senders):
    event = {"Records": [{"messageId": "sqs-0", "body": json.dumps([1, 2])}]}

    result = app.handler(event, FakeContext())

    assert result == {"batchItemFailures": []}
    dlq = receive_dlq(aws)
    assert len(dlq) == 1
    assert json.loads(dlq[0]["Body"]) == [1, 2]
    assert senders.email.calls == []


def test_malformed_json_goes_to_dlq(aws, senders):
    event = {"Records": [{"messageId": "sqs-0", "body": '{"schema": '}]}

    result = app.handler(event, FakeContext())

    assert result == {"batchItemFailures": []}
    dlq = receive_dlq(aws)
    assert len(dlq) == 1
    assert dlq[0]["Body"] == '{"schema": '
    assert senders.email.calls == []


def dlq_reason(aws) -> str:
    dlq = receive_dlq(aws)
    assert len(dlq) == 1
    return dlq[0]["MessageAttributes"]["reason"]["StringValue"]


@pytest.mark.parametrize("field", ["to", "cc", "reply_to"])
def test_external_recipient_goes_to_dlq(aws, senders, field):
    """社外ドメインの宛先は恒久エラーで送信しない（SEC-07）。"""
    message = make_message(channels=("email",))
    message["email"][field] = ["leak@example.com"]

    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    assert senders.email.calls == []
    assert f"email.{field}" in dlq_reason(aws)


def test_subdomain_recipient_goes_to_dlq(aws, senders):
    """`example.co.jp` の部分一致を許さない（SEC-07）。"""
    message = make_message(channels=("email",))
    message["email"]["to"] = ["leak@evil-example.co.jp"]

    app.handler(sqs_event(message), FakeContext())

    assert senders.email.calls == []
    assert len(receive_dlq(aws)) == 1


def test_uppercase_internal_recipient_is_sent(aws, senders):
    """大文字のドメインは許可する（SEC-07）。"""
    message = make_message(channels=("email",))
    message["email"]["to"] = ["DevA@EXAMPLE.CO.JP"]

    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(senders.email.calls) == 1


def test_attachment_of_other_app_goes_to_dlq(aws, senders):
    """他アプリのプレフィックスの添付は読み出さない（SEC-07）。"""
    message = make_message(channels=("email",))
    message["attachments"] = [
        {"bucket": ATTACHMENTS_BUCKET, "key": "other-app/2026/10/secret.xlsx", "filename": "secret.xlsx"}
    ]

    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    assert senders.email.calls == []
    assert "attachments" in dlq_reason(aws)


def test_attachment_with_parent_traversal_goes_to_dlq(aws, senders):
    """`..` を含むキーは拒否する（SEC-07）。"""
    message = make_message(channels=("email",))
    message["attachments"] = [
        {"bucket": ATTACHMENTS_BUCKET, "key": f"{APP_ID}/../other-app/secret.xlsx", "filename": "secret.xlsx"}
    ]

    app.handler(sqs_event(message), FakeContext())

    assert senders.email.calls == []
    assert len(receive_dlq(aws)) == 1


def test_attachment_of_own_app_is_sent(aws, senders):
    """自アプリのプレフィックス配下は従来どおり送る（SEC-07）。"""
    message = make_message(channels=("email",))
    message["attachments"] = [
        {
            "bucket": ATTACHMENTS_BUCKET,
            "key": f"{APP_ID}/2026/10/saas_a_billing_2026-10.xlsx",
            "filename": "saas_a_billing_2026-10.xlsx",
        }
    ]

    result = app.handler(sqs_event(message), FakeContext())

    assert result == {"batchItemFailures": []}
    assert len(senders.email.calls) == 1


def test_app_id_with_path_separator_goes_to_dlq(aws, senders):
    """`app` にパス区切りを混ぜたプレフィックス偽装を拒否する（SEC-07）。"""
    message = make_message(channels=("email",))
    message["app"] = "../other-app"

    app.handler(sqs_event(message), FakeContext())

    assert senders.email.calls == []
    assert len(receive_dlq(aws)) == 1
