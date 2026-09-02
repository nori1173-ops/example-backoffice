"""通知の送信クライアント（設計 8.2 / 8.3 / 11.2）。

アプリはこのモジュールだけを使い、SES / Slack の資格情報を持たない。
`send()` はメッセージを検証して `notify-queue` に投函し、`message_id` を返す。
添付の実体は `put_attachment()` で S3 に置くか、既にアプリが置いたキーを
`Attachment(key=...)` で参照する。

実行時の検証は `jsonschema` に依存せず、`shared/schemas/notify.v1.json` と同じ
制約を自前で確かめる（Lambda レイヤの依存を標準ライブラリ + boto3 に保つため）。
スキーマとの一致は `shared/tests/test_notify.py` が `jsonschema` で担保する。
"""

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3

from example_backoffice.logging import get_logger

SCHEMA = "example-backoffice.notify.v1"
VALID_CHANNELS = ("email", "slack")
JST = timezone(timedelta(hours=9))
MAX_MESSAGE_BYTES = 256 * 1024
EXPIRE_TAGGING = "lifecycle=expire"
FORBIDDEN_FILENAME_CHARS = '\r\n"'

APP_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
APP_MIN_LENGTH = 3
APP_MAX_LENGTH = 30
SLACK_CHANNEL_PATTERN = re.compile(r"^[CGD][A-Z0-9]{8,}$")

logger = get_logger()


class NotifyValidationError(ValueError):
    """メッセージが `example-backoffice.notify.v1` の契約を満たさない。"""


@dataclass
class Email:
    to: list[str]
    subject: str
    text: str
    cc: list[str] | None = None
    reply_to: list[str] | None = None
    html: str | None = None


@dataclass
class Attachment:
    key: str
    filename: str
    bucket: str | None = None
    content_type: str | None = None


@dataclass
class Slack:
    text: str
    channel: str | None = None
    mention: str | None = None
    blocks: list[dict] | None = None


_sqs_cache = None
_s3_cache = None


def reset_clients() -> None:
    """boto3 クライアントのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _sqs_cache, _s3_cache
    _sqs_cache = None
    _s3_cache = None


def _sqs_client():
    global _sqs_cache
    if _sqs_cache is None:
        _sqs_cache = boto3.client("sqs")
    return _sqs_cache


def _s3_client():
    global _s3_cache
    if _s3_cache is None:
        _s3_cache = boto3.client("s3")
    return _s3_cache


def attachments_bucket() -> str:
    """添付バケット名（環境変数 `ATTACHMENTS_BUCKET`）。"""
    return _required_env("ATTACHMENTS_BUCKET")


def _queue_url() -> str:
    """通知キューの URL（環境変数 `NOTIFY_QUEUE_URL`）。"""
    return _required_env("NOTIFY_QUEUE_URL")


def _required_env(name: str) -> str:
    value = os.environ.get(name) or ""
    if not value:
        raise NotifyValidationError(f"環境変数 {name} が設定されていません")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise NotifyValidationError(f"{field} は空でない文字列である必要があります")
    return value


def _ascii_filename(value: str, field: str) -> str:
    """ファイル名は ASCII のみ。CR / LF / `"` はヘッダ差し込みに使えるので拒否する（SEC-07）。

    違反時のメッセージには値を載せない（生の CRLF をログへ流し込ませないため）。
    """
    if any(char in value for char in FORBIDDEN_FILENAME_CHARS):
        raise NotifyValidationError(f'{field} に改行と " は使えません')
    if not value.isascii():
        raise NotifyValidationError(f"{field} は ASCII のみ使えます: {value}")
    return value


def _validated_app(app: object) -> str:
    value = _text(app, "app")
    if not APP_PATTERN.match(value) or not APP_MIN_LENGTH <= len(value) <= APP_MAX_LENGTH:
        raise NotifyValidationError(f"app は 3〜30 文字の kebab-case である必要があります: {value}")
    return value


def _validated_message_id(message_id: object) -> str:
    if message_id is None:
        return str(uuid.uuid4())
    value = _text(message_id, "message_id")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise NotifyValidationError(f"message_id は UUID である必要があります: {value}") from exc


def _validated_channels(channels: object) -> list[str]:
    if not isinstance(channels, list | tuple) or not channels:
        raise NotifyValidationError("channels は 1 つ以上必要です")
    values = list(channels)
    unknown = [c for c in values if c not in VALID_CHANNELS]
    if unknown:
        raise NotifyValidationError(f"channels に未対応のチャンネルがあります: {unknown}")
    if len(set(values)) != len(values):
        raise NotifyValidationError(f"channels が重複しています: {values}")
    return values


def _validated_addresses(values: object, field: str, *, required: bool = False) -> list[str]:
    if not isinstance(values, list | tuple):
        raise NotifyValidationError(f"{field} は文字列のリストである必要があります")
    if required and not values:
        raise NotifyValidationError(f"{field} は 1 件以上必要です")
    for value in values:
        if "@" not in _text(value, field):
            raise NotifyValidationError(f"{field} にメールアドレスでない値があります: {value}")
    return list(values)


def _email_dict(email: object) -> dict:
    if not isinstance(email, Email):
        raise NotifyValidationError("email は Email である必要があります")
    result = {
        "to": _validated_addresses(email.to, "email.to", required=True),
        "subject": _text(email.subject, "email.subject"),
        "text": _text(email.text, "email.text"),
    }
    if email.cc:
        result["cc"] = _validated_addresses(email.cc, "email.cc")
    if email.reply_to:
        result["reply_to"] = _validated_addresses(email.reply_to, "email.reply_to")
    if email.html:
        result["html"] = email.html
    return result


def _attachment_dict(attachment: object) -> dict:
    if not isinstance(attachment, Attachment):
        raise NotifyValidationError("attachments[] は Attachment である必要があります")
    bucket = attachment.bucket or attachments_bucket()
    result = {
        "bucket": _text(bucket, "attachments[].bucket"),
        "key": _text(attachment.key, "attachments[].key"),
        "filename": _ascii_filename(_text(attachment.filename, "attachments[].filename"), "attachments[].filename"),
    }
    if attachment.content_type:
        result["content_type"] = attachment.content_type
    return result


def _slack_dict(slack: object) -> dict:
    if not isinstance(slack, Slack):
        raise NotifyValidationError("slack は Slack である必要があります")
    result = {"text": _text(slack.text, "slack.text")}
    if slack.channel:
        if not SLACK_CHANNEL_PATTERN.match(slack.channel):
            raise NotifyValidationError(f"slack.channel はチャンネル ID である必要があります: {slack.channel}")
        result["channel"] = slack.channel
    if slack.mention is not None:
        result["mention"] = slack.mention
    if slack.blocks:
        result["blocks"] = list(slack.blocks)
    return result


def build_message(
    *,
    app: str,
    channels: list[str],
    email: Email | None = None,
    attachments: list[Attachment] | None = None,
    slack: Slack | None = None,
    meta: dict | None = None,
    message_id: str | None = None,
) -> dict:
    """検証済みのメッセージ本体を組み立てる。違反は `NotifyValidationError`。"""
    message: dict = {
        "schema": SCHEMA,
        "message_id": _validated_message_id(message_id),
        "app": _validated_app(app),
        "channels": _validated_channels(channels),
    }
    if "email" in message["channels"] and email is None:
        raise NotifyValidationError("channels に email があるので email が必要です")
    if "slack" in message["channels"] and slack is None:
        raise NotifyValidationError("channels に slack があるので slack が必要です")

    if email is not None:
        message["email"] = _email_dict(email)
    if attachments:
        message["attachments"] = [_attachment_dict(a) for a in attachments]
    if slack is not None:
        message["slack"] = _slack_dict(slack)
    if meta is not None:
        if not isinstance(meta, dict):
            raise NotifyValidationError("meta は dict である必要があります")
        message["meta"] = dict(meta)
    return message


def _serialize(message: dict) -> str:
    try:
        body = json.dumps(message, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise NotifyValidationError("メッセージを JSON にできません") from exc
    size = len(body.encode("utf-8"))
    if size > MAX_MESSAGE_BYTES:
        raise NotifyValidationError(f"メッセージが SQS の上限 {MAX_MESSAGE_BYTES} バイトを超えます（{size} バイト）")
    return body


def send(
    *,
    app: str,
    channels: list[str],
    email: Email | None = None,
    attachments: list[Attachment] | None = None,
    slack: Slack | None = None,
    meta: dict | None = None,
) -> str:
    """通知キューに 1 件投函し、`message_id` を返す。"""
    queue_url = _queue_url()
    message = build_message(app=app, channels=channels, email=email, attachments=attachments, slack=slack, meta=meta)
    _sqs_client().send_message(
        QueueUrl=queue_url,
        MessageBody=_serialize(message),
        MessageAttributes={"app": {"DataType": "String", "StringValue": message["app"]}},
    )
    logger.info(
        "通知メッセージを送信しました",
        {"message_id": message["message_id"], "app": message["app"], "channels": message["channels"]},
    )
    return message["message_id"]


def attachment_key(app: str, filename: str, subprefix: str | None, when: datetime) -> str:
    """添付の S3 キー `{app}/[{subprefix}/]{yyyy}/{mm}/{filename}`（設計 11.2）。"""
    validated_app = _validated_app(app)
    validated_filename = _ascii_filename(_text(filename, "filename"), "filename")
    at = when.replace(tzinfo=JST) if when.tzinfo is None else when.astimezone(JST)
    parts = [validated_app]
    if subprefix:
        parts.append(_text(subprefix, "subprefix").strip("/"))
    parts += [f"{at:%Y}", f"{at:%m}", validated_filename]
    return "/".join(parts)


def put_attachment(
    app: str,
    filename: str,
    data: bytes,
    subprefix: str | None = None,
    content_type: str | None = None,
    when: datetime | None = None,
    retain: bool = False,
) -> Attachment:
    """添付を添付バケットに置き、`Attachment` を返す。

    既定では `lifecycle=expire` タグを付け、core の 400 日削除の対象にする。S3 の
    ライフサイクルはプレフィックスの除外を表現できないため、除外ではなく「消してよい
    ものに印を付ける」向きで運用する（設計 11.2）。長期保持が必要な添付は
    `retain=True` にする（タグが付かないので削除されない）。
    """
    bucket = attachments_bucket()
    key = attachment_key(app, filename, subprefix, when or datetime.now(JST))
    extra = {"ContentType": content_type} if content_type else {}
    if not retain:
        extra["Tagging"] = EXPIRE_TAGGING
    _s3_client().put_object(Bucket=bucket, Key=key, Body=data, **extra)
    logger.info("添付を保存しました", {"bucket": bucket, "key": key, "bytes": len(data), "retain": retain})
    return Attachment(key=key, filename=filename, bucket=bucket, content_type=content_type)
