"""notifier Lambda のハンドラ（設計 8.1 / 8.6）。

`notify-queue` から SQS メッセージを受け、`channels` を順に送信する。
冪等性キーは `{message_id}#{channel}` で、片方のチャンネルだけ成功した場合の
再試行では成功済みチャンネルを再送しない。

失敗は 2 種類に分ける。

- 恒久エラー（スキーマ不一致・添付欠落・Slack 未設定など）: 再試行しても直らない。
  notifier 自身が DLQ へ退避して正常終了扱いにする。
- 一時エラー（SES / Slack の一時障害など）: `batchItemFailures` に入れて
  SQS に再配信させ、`maxReceiveCount` 到達で SQS 側の DLQ に落とす。

ログには `message_id` / `app` / `channel` / 件数だけを出す。例外オブジェクトの
属性（URL・ファイル名など）は秘密を含み得るため出さない。
"""

import json
import os

import boto3
from aws_lambda_powertools.utilities.idempotency import (
    DynamoDBPersistenceLayer,
    IdempotencyConfig,
    idempotent_function,
)

from example_backoffice.logging import LambdaLogger, get_logger

try:  # Lambda ではハンドラと同階層に配置されるため直接 import できる
    from ses_sender import AttachmentNotFoundError, from_address_from_env, send_email
    from slack_sender import SlackApiError, SlackNotConfiguredError, send_slack
except ImportError:  # pytest は infra/core/src を pythonpath に置くので package 経由になる
    from notifier.ses_sender import AttachmentNotFoundError, from_address_from_env, send_email
    from notifier.slack_sender import SlackApiError, SlackNotConfiguredError, send_slack

SCHEMA = "example-backoffice.notify.v1"
EMAIL = "email"
SLACK = "slack"
VALID_CHANNELS = (EMAIL, SLACK)
IDEMPOTENCY_TABLE_ENV = "IDEMPOTENCY_TABLE"
DLQ_URL_ENV = "DLQ_URL"
SSM_PREFIX_ENV = "SSM_PREFIX"
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
PERMANENT_SLACK_ERRORS = ("channel_not_found", "invalid_auth", "not_in_channel")

logger = get_logger()

_sqs_cache = None
_persistence_cache = None
_config_cache = None
_send_channel_cache = None


class PermanentError(Exception):
    """再試行しても直らないエラー。DLQ へ退避して正常終了扱いにする。"""


def reset_clients() -> None:
    """boto3 クライアントと冪等性ストアのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _sqs_cache, _persistence_cache, _config_cache, _send_channel_cache
    _sqs_cache = None
    _persistence_cache = None
    _config_cache = None
    _send_channel_cache = None


def _sqs():
    global _sqs_cache
    if _sqs_cache is None:
        _sqs_cache = boto3.client("sqs")
    return _sqs_cache


def _send_channel(*, payload: str, message: dict, channel: str) -> None:
    """1 チャンネル分の送信。`payload` は冪等性キー `{message_id}#{channel}`。"""
    if channel == EMAIL:
        send_email(message=message, from_address=from_address_from_env())
    else:
        send_slack(message=message, ssm_prefix=os.environ[SSM_PREFIX_ENV])
    logger.info(
        "チャンネルへ送信しました",
        {"message_id": message.get("message_id"), "app": message.get("app"), "channel": channel},
    )


def _idempotent_send():
    """`_send_channel` を冪等化したものを返す（DynamoDB 接続は初回呼び出しまで遅延）。"""
    global _persistence_cache, _config_cache, _send_channel_cache
    if _send_channel_cache is None:
        _persistence_cache = DynamoDBPersistenceLayer(table_name=os.environ[IDEMPOTENCY_TABLE_ENV])
        _config_cache = IdempotencyConfig(expires_after_seconds=IDEMPOTENCY_TTL_SECONDS)
        _send_channel_cache = idempotent_function(
            _send_channel,
            data_keyword_argument="payload",
            persistence_store=_persistence_cache,
            config=_config_cache,
        )
    return _send_channel_cache


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PermanentError(reason)


def validate_message(body: object) -> None:
    """受信側の軽量検証（設計 8.2）。違反は `PermanentError`。"""
    _require(isinstance(body, dict), "メッセージが JSON オブジェクトではありません")
    _require(body.get("schema") == SCHEMA, f"schema が {SCHEMA} ではありません: {body.get('schema')}")
    _require(bool(body.get("message_id")), "message_id がありません")
    _require(bool(body.get("app")), "app がありません")

    channels = body.get("channels")
    _require(isinstance(channels, list) and bool(channels), "channels がありません")
    unknown = [channel for channel in channels if channel not in VALID_CHANNELS]
    _require(not unknown, f"channels に未対応のチャンネルがあります: {unknown}")

    if EMAIL in channels:
        email = body.get(EMAIL)
        _require(isinstance(email, dict), "channels に email があるので email が必要です")
        _require(bool(email.get("to")), "email.to がありません")
        _require(bool(email.get("subject")), "email.subject がありません")
        _require("text" in email, "email.text がありません")
    if SLACK in channels:
        slack = body.get(SLACK)
        _require(isinstance(slack, dict), "channels に slack があるので slack が必要です")
        _require(bool(slack.get("text")), "slack.text がありません")


def _is_permanent(exc: Exception) -> bool:
    """再試行しても直らないエラーかどうか（設計 8.6）。"""
    if isinstance(exc, AttachmentNotFoundError | SlackNotConfiguredError):
        return True
    return isinstance(exc, SlackApiError) and any(code in str(exc) for code in PERMANENT_SLACK_ERRORS)


def process_record(body: object) -> None:
    """1 メッセージ分を処理する。`channels` を順に、チャンネル単位で冪等に送る。"""
    validate_message(body)
    message_id = body["message_id"]
    for channel in body["channels"]:
        try:
            _idempotent_send()(payload=f"{message_id}#{channel}", message=body, channel=channel)
        except Exception as exc:
            if _is_permanent(exc):
                raise PermanentError(str(exc)) from exc
            raise


def _log_fields(body: object) -> dict:
    """ログに出せる形にする。JSON オブジェクト以外（配列・生文字列）は空にする。"""
    return body if isinstance(body, dict) else {}


def _parse_body(raw: str) -> object:
    """SQS の body を JSON として読む。壊れていれば恒久エラー（設計 8.6）。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PermanentError(f"body が JSON として読めません: {exc.msg}") from exc


def send_to_dlq(body: object, reason: str) -> None:
    """恒久エラーのメッセージを notifier 自身が DLQ へ退避する（設計 8.6）。

    `body` は JSON として読めた値、読めなければ元の文字列そのもの。
    """
    fields = _log_fields(body)
    _sqs().send_message(
        QueueUrl=os.environ[DLQ_URL_ENV],
        MessageBody=body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
        MessageAttributes={"reason": {"DataType": "String", "StringValue": reason}},
    )
    logger.error(
        "恒久エラーのため DLQ へ退避しました",
        {"message_id": fields.get("message_id"), "app": fields.get("app"), "reason": reason},
    )


@LambdaLogger.contextualize
def handler(event: dict, context) -> dict:
    """SQS イベントを処理し、再試行させたいレコードだけを返す。"""
    records = event.get("Records") or []
    logger.info("通知メッセージを受信しました", {"records": len(records)})

    _idempotent_send()
    _config_cache.register_lambda_context(context)

    failures = []
    for record in records:
        body: object = record.get("body") or "{}"
        try:
            body = _parse_body(body)
            process_record(body)
        except PermanentError as exc:
            send_to_dlq(body, str(exc) or type(exc).__name__)
        except Exception as exc:
            fields = _log_fields(body)
            logger.error(
                "一時エラーのため再試行します",
                {"message_id": fields.get("message_id"), "app": fields.get("app"), "error": type(exc).__name__},
            )
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
