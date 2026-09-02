"""SES v2 によるメール送信（設計 8.4）。

`Content.Simple` + `Attachments[]` を使い、MIME の組み立ては SES に任せる。
添付の実体は添付バケットから `GetObject` して `RawContent` に渡す
（`Attachments` 対応は boto3 1.37.27 以降。`requirements.txt` で固定）。
送信元アドレスは環境変数 `SES_FROM_ADDRESS` から取る。
"""

import os

import boto3
from botocore.exceptions import ClientError

from example_backoffice.logging import get_logger

DISPLAY_NAME = "example-backoffice"
CHARSET = "UTF-8"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
FROM_ADDRESS_ENV = "SES_FROM_ADDRESS"

logger = get_logger()

_sesv2_cache = None
_s3_cache = None


class AttachmentNotFoundError(Exception):
    """添付が S3 に無い。再試行しても直らない恒久エラー（設計 8.6）。"""


def from_address_from_env() -> str:
    """送信元アドレス（環境変数 `SES_FROM_ADDRESS`）。"""
    return os.environ[FROM_ADDRESS_ENV]


def _sesv2(client):
    global _sesv2_cache
    if client is not None:
        return client
    if _sesv2_cache is None:
        _sesv2_cache = boto3.client("sesv2")
    return _sesv2_cache


def _s3(client):
    global _s3_cache
    if client is not None:
        return client
    if _s3_cache is None:
        _s3_cache = boto3.client("s3")
    return _s3_cache


def fetch_attachment(bucket: str, key: str, s3_client=None) -> bytes:
    """添付の実体を S3 から取得する。無ければ `AttachmentNotFoundError`。"""
    try:
        return _s3(s3_client).get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("NoSuchKey", "404") or status == 404:
            raise AttachmentNotFoundError(f"添付が見つかりません: s3://{bucket}/{key}") from exc
        raise


def _attachment(attachment: dict, s3_client) -> dict:
    return {
        "RawContent": fetch_attachment(attachment["bucket"], attachment["key"], s3_client=s3_client),
        "FileName": attachment["filename"],
        "ContentType": attachment.get("content_type") or DEFAULT_CONTENT_TYPE,
    }


def send_email(*, message: dict, from_address: str, s3_client=None, sesv2_client=None) -> str:
    """`message` を SES v2 で 1 通送り、SES の `MessageId` を返す。"""
    email = message["email"]
    body = {"Text": {"Data": email["text"], "Charset": CHARSET}}
    if email.get("html"):
        body["Html"] = {"Data": email["html"], "Charset": CHARSET}

    simple = {"Subject": {"Data": email["subject"], "Charset": CHARSET}, "Body": body}
    attachments = [_attachment(a, s3_client) for a in message.get("attachments") or []]
    if attachments:
        simple["Attachments"] = attachments

    destination = {"ToAddresses": list(email["to"])}
    if email.get("cc"):
        destination["CcAddresses"] = list(email["cc"])

    request = {
        "FromEmailAddress": f"{DISPLAY_NAME} <{from_address}>",
        "Destination": destination,
        "Content": {"Simple": simple},
    }
    if email.get("reply_to"):
        request["ReplyToAddresses"] = list(email["reply_to"])

    ses_message_id = _sesv2(sesv2_client).send_email(**request)["MessageId"]
    logger.info(
        "メールを送信しました",
        {
            "message_id": message.get("message_id"),
            "ses_message_id": ses_message_id,
            "to": len(destination["ToAddresses"]),
            "attachments": len(attachments),
        },
    )
    return ses_message_id
