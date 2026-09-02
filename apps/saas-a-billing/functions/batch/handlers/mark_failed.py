"""失敗の記録と通知（設計 6.3 の `MarkFailed`）。

ステートマシンの全体 `Catch` が `$.error` に積んだ `{"Error", "Cause"}` からエラー
要約を作り、RUN を `failed` にして、配布ありの RUN なら設定で有効な配布先に知らせる
（設計 6.8 / 11.1。配布チェックを外した RUN は失敗しても何も送らない）。
`Cause` は Lambda が投げた例外を Step Functions が JSON にしたものなので、
`errorMessage` が取れればそれを使い、取れなければ `Error` をそのまま使う。要約は
RUN の属性と通知の本文に載るため 1,000 文字で切る。

要約は上流の例外文字列をそのまま運ぶため、RUN の `api_token` が混ざっていれば
（HTTP エラーが `Authorization` ヘッダを反映する等）伏せてから配る。RUN を消す前に
読むので、`repo.finish_failed` より先にトークンを取っておく。SSM のパスワードは
このハンドラからは読めないため対象外（防衛線は接続情報の `repr` 秘匿）。

通知自体の失敗はログに残すだけで例外にしない（ここで落ちると `MarkFailed` ごと
失敗し、RUN が `running` のまま残る。設計 6.3 / 11.1）。`api_token` /
`token_expires_at` は `repo.finish_failed` が消す。

`Init` が落ちた場合は `run_id` がまだ無いため、実行入力の要素から組み直す。
"""

import json
import os

from saas_a_billing import distribution, messages, repo

from example_backoffice.logging import LambdaLogger, get_logger
from example_backoffice.notify import Email, Slack, send

logger = get_logger()

APP = "saas-a-billing"
FAILED = "failed"
MAX_ERROR_LENGTH = 1000
MASK = "***"


def _error_message(cause: str) -> str:
    """`Cause` の JSON から `errorMessage` を取り出す。JSON でなければ空文字。"""
    try:
        parsed = json.loads(cause)
    except ValueError:
        return ""
    return parsed.get("errorMessage") or "" if isinstance(parsed, dict) else ""


def _error_summary(error: dict, token: str | None) -> str:
    """`$.error` を通知に載せられる長さの要約にする（設計 6.3）。トークンは伏せてから切る。"""
    summary = _error_message(error.get("Cause") or "") or error.get("Error") or ""
    if token:
        summary = summary.replace(token, MASK)
    return summary[:MAX_ERROR_LENGTH]


def _notify(settings: dict, target_env: str, month: str, attempt: int, summary: str) -> None:
    """失敗を設定で有効な配布先に知らせる（設計 6.8）。メールと Slack は同じ本文を使う。"""
    channels = distribution.channels(settings)
    if not channels:
        logger.info("配布先が無効のため失敗通知を送付しません", {"target_env": target_env, "month": month})
        return
    subject, body = messages.failure_message(month, summary, messages.portal_url(os.environ["ENV"], target_env, month))
    channel = settings["slack_channel_id"]
    send(
        app=APP,
        channels=channels,
        email=Email(to=settings["mail_to"], cc=settings["mail_cc"], subject=subject, text=body)
        if distribution.EMAIL_CHANNEL in channels
        else None,
        slack=Slack(text=body, channel=channel, mention=settings["slack_mention"] or None)
        if distribution.SLACK_CHANNEL in channels
        else None,
        meta={"target_env": target_env, "month": month, "attempt": attempt},
    )


@LambdaLogger.contextualize
def handler(event: dict, context) -> dict:
    target_env = event["target_env"]
    month = event["month"]
    attempt = event["attempt"]
    run_id = event.get("run_id") or f"{target_env}-{month}-{attempt}"
    run = repo.get_run(target_env, month) or {}
    distribute = bool(run.get("distribute", event.get("distribute", True)))
    summary = _error_summary(event.get("error") or {}, run.get("api_token"))
    repo.finish_failed(target_env, month, attempt, summary)
    if distribute:
        try:
            _notify(repo.get_settings(), target_env, month, attempt, summary)
        except Exception as error:
            logger.error("失敗通知の投函に失敗しました", {"run_id": run_id, "month": month, "error": str(error)})
    else:
        logger.info("配布なしの RUN のため失敗通知を送付しません", {"run_id": run_id, "month": month})
    logger.error(
        "RUN を失敗として記録しました",
        {"run_id": run_id, "target_env": target_env, "month": month, "error": summary},
    )
    return {"run_id": run_id, "month": month, "target_env": target_env, "status": FAILED, "error": summary}
