"""`example_backoffice.notify` のテスト（設計 8.2 / 8.3 / 11.2）。

実行時の検証は自前の軽量バリデータで行うが、ここでは組み上がったメッセージが
`shared/schemas/notify.v1.json` にも適合することを `jsonschema` で確かめる。
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import boto3
import pytest
from jsonschema import Draft202012Validator
from moto import mock_aws

from example_backoffice import notify
from example_backoffice.notify import JST, SCHEMA, Attachment, Email, NotifyValidationError, Slack

REGION = "ap-northeast-1"
QUEUE_NAME = "example-backoffice-core-notify-queue-test"
BUCKET = "example-backoffice-core-attachments-test"
APP = "saas-a-billing"
MESSAGE_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def aws(monkeypatch):
    """moto の SQS キューと S3 バケットを作り、環境変数を差し替える。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        monkeypatch.setenv("NOTIFY_QUEUE_URL", queue_url)
        monkeypatch.setenv("ATTACHMENTS_BUCKET", BUCKET)
        notify.reset_clients()
        yield SimpleNamespace(sqs=sqs, s3=s3, queue_url=queue_url, bucket=BUCKET)
    notify.reset_clients()


def an_email(**overrides) -> Email:
    values = {"to": ["devA@example.co.jp"], "subject": "件名", "text": "本文"}
    values.update(overrides)
    return Email(**values)


def receive_one(aws) -> dict:
    response = aws.sqs.receive_message(QueueUrl=aws.queue_url, MaxNumberOfMessages=10, MessageAttributeNames=["All"])
    messages = response["Messages"]
    assert len(messages) == 1
    return messages[0]


def body_of(aws) -> dict:
    return json.loads(receive_one(aws)["Body"])


def assert_matches_schema(schema: dict, message: dict) -> None:
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(message)


def test_send_puts_message_on_queue(aws, notify_schema):
    subject = "【SaaS_A】月額ライセンス料金一覧 2026年10月分"

    message_id = notify.send(app=APP, channels=["email"], email=an_email(subject=subject))

    body = body_of(aws)
    assert body["message_id"] == message_id
    assert body["schema"] == SCHEMA
    assert body["app"] == APP
    assert body["email"]["subject"] == subject
    assert_matches_schema(notify_schema, body)


def test_send_includes_slack_and_attachments(aws, notify_schema):
    notify.send(
        app=APP,
        channels=["email", "slack"],
        email=an_email(cc=["devbot@example.co.jp"], reply_to=["devA@example.co.jp"], html="<p>本文</p>"),
        attachments=[
            Attachment(
                key=f"{APP}/production/2026/10/saas_a_billing_2026-10.xlsx",
                filename="saas_a_billing_2026-10.xlsx",
                content_type=XLSX,
            )
        ],
        slack=Slack(text="送付しました。", channel="C0123ABCD", mention="<!here>", blocks=[{"type": "divider"}]),
        meta={"run_id": "RUN#production/2026-10", "target_month": "2026-10"},
    )

    body = body_of(aws)
    assert body["attachments"][0]["bucket"] == BUCKET
    assert body["attachments"][0]["key"] == f"{APP}/production/2026/10/saas_a_billing_2026-10.xlsx"
    assert body["slack"]["channel"] == "C0123ABCD"
    assert body["meta"]["run_id"] == "RUN#production/2026-10"
    assert_matches_schema(notify_schema, body)


def test_send_generates_unique_message_ids(aws):
    first = notify.send(app=APP, channels=["email"], email=an_email())
    second = notify.send(app=APP, channels=["email"], email=an_email())

    assert first != second


def test_send_rejects_email_channel_without_email(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=["email"])


def test_send_rejects_slack_channel_without_slack(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=["slack"])


def test_send_rejects_unknown_channel(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=["teams"], email=an_email())


def test_send_rejects_empty_channels(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=[], email=an_email())


def test_send_rejects_invalid_app_id(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app="Bad_App", channels=["email"], email=an_email())


def test_put_attachment_uses_key_convention(aws):
    attachment = notify.put_attachment(
        APP, "saas_a_billing_2026-08.xlsx", b"xlsx-bytes", when=datetime(2026, 8, 21, tzinfo=JST)
    )

    assert attachment.key == "saas-a-billing/2026/08/saas_a_billing_2026-08.xlsx"
    assert attachment.bucket == BUCKET
    assert aws.s3.get_object(Bucket=BUCKET, Key=attachment.key)["Body"].read() == b"xlsx-bytes"


def test_put_attachment_with_subprefix(aws):
    attachment = notify.put_attachment(
        APP, "x.xlsx", b"x", subprefix="production", when=datetime(2026, 10, 5, tzinfo=JST)
    )

    assert attachment.key == "saas-a-billing/production/2026/10/x.xlsx"


def test_put_attachment_sets_content_type(aws):
    attachment = notify.put_attachment(APP, "x.xlsx", b"x", content_type=XLSX, when=datetime(2026, 10, 5, tzinfo=JST))

    assert aws.s3.head_object(Bucket=BUCKET, Key=attachment.key)["ContentType"] == XLSX
    assert attachment.content_type == XLSX


def test_put_attachment_tags_for_expiration_by_default(aws):
    """既定では `lifecycle=expire` を付け、core の 400 日ライフサイクルの対象にする（設計 11.2）。"""
    attachment = notify.put_attachment(APP, "x.xlsx", b"x", when=datetime(2026, 10, 5, tzinfo=JST))

    tags = aws.s3.get_object_tagging(Bucket=BUCKET, Key=attachment.key)["TagSet"]
    assert tags == [{"Key": "lifecycle", "Value": "expire"}]


def test_put_attachment_retain_skips_tag(aws):
    """`retain=True` はタグを付けない。タグの無いオブジェクトは削除されない（設計 11.2）。"""
    attachment = notify.put_attachment(APP, "x.xlsx", b"x", when=datetime(2026, 10, 5, tzinfo=JST), retain=True)

    assert aws.s3.get_object_tagging(Bucket=BUCKET, Key=attachment.key)["TagSet"] == []


@pytest.mark.parametrize("channels", [["email"], ["slack"], ["email", "slack"]])
def test_build_message_matches_schema_for_every_channel_combination(notify_schema, channels):
    message = notify.build_message(
        app=APP,
        channels=channels,
        email=an_email() if "email" in channels else None,
        slack=Slack(text="本文") if "slack" in channels else None,
        message_id=MESSAGE_ID,
    )

    assert message["message_id"] == MESSAGE_ID
    assert_matches_schema(notify_schema, message)


def test_send_sets_app_message_attribute(aws):
    notify.send(app=APP, channels=["email"], email=an_email())

    attributes = receive_one(aws)["MessageAttributes"]
    assert attributes["app"]["DataType"] == "String"
    assert attributes["app"]["StringValue"] == APP


def test_send_requires_queue_url_env(aws, monkeypatch):
    monkeypatch.delenv("NOTIFY_QUEUE_URL")

    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=["email"], email=an_email())


def test_send_rejects_duplicate_channels(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=["email", "email"], email=an_email())


def test_send_rejects_oversized_message(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=["email"], email=an_email(text="x" * (300 * 1024)))


def test_build_message_rejects_invalid_message_id():
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["slack"], slack=Slack(text="本文"), message_id="not-a-uuid")


def test_build_message_omits_blank_slack_channel(notify_schema):
    message = notify.build_message(app=APP, channels=["slack"], slack=Slack(text="本文", channel=""))

    assert "channel" not in message["slack"]
    assert_matches_schema(notify_schema, message)


def test_build_message_rejects_slack_channel_name():
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["slack"], slack=Slack(text="本文", channel="#proj-saas-a-billing"))


def test_build_message_rejects_empty_slack_text():
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["slack"], slack=Slack(text=""))


def test_build_message_rejects_email_without_recipients():
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["email"], email=an_email(to=[]))


def test_build_message_rejects_address_without_at_sign():
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["email"], email=an_email(to=["devA.example.co.jp"]))


@pytest.mark.parametrize("field", ["subject", "text"])
def test_build_message_rejects_blank_email_field(field):
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["email"], email=an_email(**{field: ""}))


def test_build_message_rejects_non_ascii_attachment_filename(aws):
    with pytest.raises(NotifyValidationError):
        notify.build_message(
            app=APP,
            channels=["email"],
            email=an_email(),
            attachments=[Attachment(key=f"{APP}/2026/10/x.xlsx", filename="請求書.xlsx")],
        )


def test_build_message_rejects_attachment_without_key(aws):
    with pytest.raises(NotifyValidationError):
        notify.build_message(
            app=APP, channels=["email"], email=an_email(), attachments=[Attachment(key="", filename="x.xlsx")]
        )


def test_build_message_keeps_explicit_attachment_bucket(aws, notify_schema):
    message = notify.build_message(
        app=APP,
        channels=["email"],
        email=an_email(),
        attachments=[Attachment(key=f"{APP}/2026/10/x.xlsx", filename="x.xlsx", bucket="other-bucket")],
        message_id=MESSAGE_ID,
    )

    assert message["attachments"][0]["bucket"] == "other-bucket"
    assert_matches_schema(notify_schema, message)


def test_build_message_rejects_non_dict_meta():
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["slack"], slack=Slack(text="本文"), meta=["run_id"])


@pytest.mark.parametrize(
    ("field", "value"),
    [("email", {"to": ["devA@example.co.jp"]}), ("slack", {"text": "本文"})],
)
def test_build_message_rejects_plain_dict_payloads(field, value):
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=[field], **{field: value})


def test_attachment_key_converts_to_jst():
    when = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)

    assert notify.attachment_key(APP, "x.xlsx", None, when) == "saas-a-billing/2026/10/x.xlsx"


def test_attachment_key_treats_naive_datetime_as_jst():
    assert notify.attachment_key(APP, "x.xlsx", None, datetime(2026, 9, 30, 23, 30)) == "saas-a-billing/2026/09/x.xlsx"


def test_put_attachment_defaults_to_now(aws):
    now = datetime.now(JST)

    attachment = notify.put_attachment(APP, "x.xlsx", b"x")

    assert attachment.key == f"{APP}/{now:%Y}/{now:%m}/x.xlsx"


def test_attachments_bucket_requires_env(aws, monkeypatch):
    monkeypatch.delenv("ATTACHMENTS_BUCKET")

    with pytest.raises(NotifyValidationError):
        notify.attachments_bucket()


def test_reset_clients_discards_cached_clients(aws):
    notify.send(app=APP, channels=["email"], email=an_email())
    before = notify._sqs_client()

    notify.reset_clients()

    assert notify._sqs_client() is not before


def test_build_message_rejects_non_list_recipients():
    with pytest.raises(NotifyValidationError):
        notify.build_message(app=APP, channels=["email"], email=an_email(to="devA@example.co.jp"))


def test_build_message_rejects_plain_dict_attachment(aws):
    with pytest.raises(NotifyValidationError):
        notify.build_message(
            app=APP,
            channels=["email"],
            email=an_email(),
            attachments=[{"key": f"{APP}/2026/10/x.xlsx", "filename": "x.xlsx"}],
        )


def test_send_rejects_unserializable_meta(aws):
    with pytest.raises(NotifyValidationError):
        notify.send(app=APP, channels=["email"], email=an_email(), meta={"when": datetime(2026, 10, 5, tzinfo=JST)})
