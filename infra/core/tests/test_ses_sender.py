"""`notifier.ses_sender` のテスト（設計 8.4）。

SES v2 のリクエストはフェイククライアントで受け取り、`botocore` の実シェイプで
検証してからキー・値を確かめる（moto 5.2.3 の sesv2 は `Attachments` /
`ReplyToAddresses` / `Html` を保持しないため）。添付の実体は moto の S3 に置く。
"""

import boto3
import pytest
from botocore.validate import ParamValidator
from moto import mock_aws
from notifier.ses_sender import AttachmentNotFoundError, from_address_from_env, send_email

REGION = "ap-northeast-1"
BUCKET = "example-backoffice-attachments-dev"
FROM_ADDRESS = "noreply@backoffice-dev.example-cloud.com"
SES_MESSAGE_ID = "0100000000000000-11111111-2222-3333-4444-555555555555-000000"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class FakeSesv2Client:
    """SES v2 `send_email` に渡された kwargs を記録するフェイク。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        model = boto3.client("sesv2", region_name=REGION).meta.service_model
        self._input_shape = model.operation_model("SendEmail").input_shape

    def send_email(self, **kwargs) -> dict:
        report = ParamValidator().validate(kwargs, self._input_shape)
        if report.has_errors():
            raise AssertionError(report.generate_report())
        self.calls.append(kwargs)
        return {"MessageId": SES_MESSAGE_ID}

    @property
    def request(self) -> dict:
        assert len(self.calls) == 1
        return self.calls[0]


@pytest.fixture
def ses() -> FakeSesv2Client:
    return FakeSesv2Client()


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield client


def make_message(*, email_extra: dict | None = None, attachments: list[dict] | None = None) -> dict:
    email = {
        "to": ["ops@example.co.jp"],
        "subject": "月次請求データ",
        "text": "月次請求データを添付します。",
    }
    email.update(email_extra or {})
    message: dict = {
        "schema": "example-backoffice.notify.v1",
        "message_id": "11111111-2222-3333-4444-555555555555",
        "app": "saas-a-billing",
        "channels": ["email"],
        "email": email,
    }
    if attachments:
        message["attachments"] = attachments
    return message


def test_sends_simple_email(ses):
    result = send_email(message=make_message(), from_address=FROM_ADDRESS, sesv2_client=ses)

    simple = ses.request["Content"]["Simple"]
    assert result == SES_MESSAGE_ID
    assert ses.request["Destination"]["ToAddresses"] == ["ops@example.co.jp"]
    assert simple["Subject"] == {"Data": "月次請求データ", "Charset": "UTF-8"}
    assert simple["Body"]["Text"] == {"Data": "月次請求データを添付します。", "Charset": "UTF-8"}
    assert "Html" not in simple["Body"]
    assert "Attachments" not in simple


def test_includes_cc_and_reply_to(ses):
    message = make_message(email_extra={"cc": ["boss@example.co.jp"], "reply_to": ["dev@example.co.jp"]})

    send_email(message=message, from_address=FROM_ADDRESS, sesv2_client=ses)

    assert ses.request["Destination"]["CcAddresses"] == ["boss@example.co.jp"]
    assert ses.request["ReplyToAddresses"] == ["dev@example.co.jp"]


def test_includes_html_body_when_present(ses):
    message = make_message(email_extra={"html": "<p>月次請求データ</p>"})

    send_email(message=message, from_address=FROM_ADDRESS, sesv2_client=ses)

    body = ses.request["Content"]["Simple"]["Body"]
    assert body["Html"] == {"Data": "<p>月次請求データ</p>", "Charset": "UTF-8"}
    assert body["Text"]["Data"] == "月次請求データを添付します。"


def test_attaches_object_from_s3(ses, s3):
    key = "saas-a-billing/2026/08/saas_a_billing_2026-08.xlsx"
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"PK\x03\x04binary-body")
    message = make_message(
        attachments=[
            {
                "bucket": BUCKET,
                "key": key,
                "filename": "saas_a_billing_2026-08.xlsx",
                "content_type": XLSX_CONTENT_TYPE,
            }
        ]
    )

    send_email(message=message, from_address=FROM_ADDRESS, s3_client=s3, sesv2_client=ses)

    attachment = ses.request["Content"]["Simple"]["Attachments"][0]
    assert attachment["RawContent"] == b"PK\x03\x04binary-body"
    assert attachment["FileName"] == "saas_a_billing_2026-08.xlsx"
    assert attachment["ContentType"] == XLSX_CONTENT_TYPE


def test_attachment_is_sent_as_base64(ses, s3):
    key = "saas-a-billing/2026/08/saas_a_billing_2026-08.xlsx"
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"PK\x03\x04\x80\xff\r\nbinary")
    message = make_message(
        attachments=[{"bucket": BUCKET, "key": key, "filename": "saas_a_billing_2026-08.xlsx"}]
    )

    send_email(message=message, from_address=FROM_ADDRESS, s3_client=s3, sesv2_client=ses)

    assert ses.request["Content"]["Simple"]["Attachments"][0]["ContentTransferEncoding"] == "BASE64"


def test_attachment_content_type_defaults_to_octet_stream(ses, s3):
    key = "saas-a-billing/2026/08/report.csv"
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"a,b,c\n")
    message = make_message(attachments=[{"bucket": BUCKET, "key": key, "filename": "report.csv"}])

    send_email(message=message, from_address=FROM_ADDRESS, s3_client=s3, sesv2_client=ses)

    assert ses.request["Content"]["Simple"]["Attachments"][0]["ContentType"] == "application/octet-stream"


def test_missing_attachment_raises_attachment_not_found(ses, s3):
    key = "saas-a-billing/2026/08/missing.xlsx"
    message = make_message(attachments=[{"bucket": BUCKET, "key": key, "filename": "missing.xlsx"}])

    with pytest.raises(AttachmentNotFoundError, match="missing.xlsx"):
        send_email(message=message, from_address=FROM_ADDRESS, s3_client=s3, sesv2_client=ses)

    assert ses.calls == []


def test_from_address_uses_display_name(ses, monkeypatch):
    monkeypatch.setenv("SES_FROM_ADDRESS", FROM_ADDRESS)

    send_email(message=make_message(), from_address=from_address_from_env(), sesv2_client=ses)

    assert ses.request["FromEmailAddress"] == f"example-backoffice <{FROM_ADDRESS}>"


def test_japanese_subject_is_utf8(ses):
    subject = "【example-backoffice】月次請求データ 2026-08"
    message = make_message(email_extra={"subject": subject})

    send_email(message=message, from_address=FROM_ADDRESS, sesv2_client=ses)

    assert ses.request["Content"]["Simple"]["Subject"] == {"Data": subject, "Charset": "UTF-8"}
