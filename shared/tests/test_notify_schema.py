"""通知メッセージスキーマ `example-backoffice.notify.v1` の契約テスト（設計 8.2）。

送信側（shared の `notify.py`）と受信側（core の notifier）は、このスキーマ 1 ファイルだけを
共通の契約とする。フィールドを増減する場合は設計 8.2 の表とここのテストを同時に更新する。
"""

import pytest
from jsonschema import Draft202012Validator, ValidationError


@pytest.fixture
def validator(notify_schema) -> Draft202012Validator:
    return Draft202012Validator(notify_schema)


def test_schema_itself_is_valid(notify_schema):
    Draft202012Validator.check_schema(notify_schema)


def test_valid_message_passes(validator, valid_message):
    validator.validate(valid_message)


def test_schema_field_must_be_exact_constant(validator, valid_message):
    valid_message["schema"] = "example-backoffice.notify.v2"
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


@pytest.mark.parametrize("field", ["schema", "message_id", "app", "channels"])
def test_required_top_level_fields(validator, valid_message, field):
    del valid_message[field]
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_message_id_must_be_uuid(validator, valid_message):
    valid_message["message_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_channels_must_be_email_or_slack(validator, valid_message):
    valid_message["channels"] = ["teams"]
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_channels_must_not_be_empty(validator, valid_message):
    valid_message["channels"] = []
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_email_channel_requires_email_object(validator, valid_message):
    valid_message["channels"] = ["email"]
    del valid_message["email"]
    del valid_message["slack"]
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_slack_channel_requires_slack_text(validator, valid_message):
    del valid_message["slack"]["text"]
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_slack_channel_must_be_channel_id(validator, valid_message):
    """`slack.channel` はチャンネル ID のみ許可する（設計 8.2。`#name` 形式は使わない）。"""
    valid_message["slack"]["channel"] = "C0123ABCD"
    validator.validate(valid_message)

    valid_message["slack"]["channel"] = "#proj-saas-a-billing"
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_email_only_message_without_slack_passes(validator, valid_message):
    valid_message["channels"] = ["email"]
    del valid_message["slack"]
    validator.validate(valid_message)


def test_attachment_requires_bucket_key_filename(validator, valid_message):
    del valid_message["attachments"][0]["filename"]
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_unknown_top_level_property_is_rejected(validator, valid_message):
    valid_message["priority"] = "high"
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


@pytest.mark.parametrize(
    "key",
    [
        "/saas-a-billing/2026/10/x.xlsx",
        "../saas-a-billing/2026/10/x.xlsx",
        "saas-a-billing/../secret.xlsx",
        "saas_a_billing/2026/10/x.xlsx",
        "saas-a-billing//x.xlsx",
        "x.xlsx",
    ],
)
def test_attachment_key_must_be_under_an_app_prefix(validator, valid_message, key):
    """添付キーは `{app}/` 配下に限る（設計 8.2 / SEC-07）。app との一致は notifier が見る。"""
    valid_message["attachments"][0]["key"] = key
    with pytest.raises(ValidationError):
        validator.validate(valid_message)


def test_attachment_key_under_app_prefix_passes(validator, valid_message):
    valid_message["attachments"][0]["key"] = "saas-a-billing/production/2026/10/x.xlsx"
    validator.validate(valid_message)
