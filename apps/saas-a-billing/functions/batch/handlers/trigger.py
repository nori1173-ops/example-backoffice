"""実行判定（設計 6.2）。EventBridge Scheduler が毎日 06:00 JST に非同期 Invoke する。

「最新の締め日時が現在時刻以前」かつ「その対象月の RUN が成功していない」ならステート
マシンを起動する。当月の RUN が成功するまで毎日再評価されるため、未実行の検知もこの
判定が兼ねる（設計 3.1）。判定は純粋関数 `decide()` に閉じ込め、AWS へのアクセスは
`handler()` 側だけに置く。

`failed` の RUN はスケジュール起動で 1 日 1 回だけ再実行し、`auto_attempts` が
`auto_retry_days` に達したら止めて「要手動」アラームを送る（設計 11.1）。アラームの
二重送信は RUN の `retry_alert_sent` で防ぐ。手動実行は `auto_attempts` を増やさない
ため、この上限枠を消費しない。
"""

import json
import os
import uuid
from datetime import datetime

import boto3
from saas_a_billing import distribution, execution, messages, period, repo

from example_backoffice.logging import LambdaLogger, get_logger
from example_backoffice.notify import Email, Slack, send

logger = get_logger()

APP = "saas-a-billing"
TRIGGER = "scheduled"

AUTO_RUN_DISABLED = "AUTO_RUN_DISABLED"
BEFORE_AUTO_RUN_FROM_MONTH = "BEFORE_AUTO_RUN_FROM_MONTH"
ALREADY_SUCCEEDED = "ALREADY_SUCCEEDED"
IN_PROGRESS = "IN_PROGRESS"
AUTO_RETRY_EXHAUSTED = "AUTO_RETRY_EXHAUSTED"
START = "START"

_states_cache = None


def reset_clients() -> None:
    """テストが moto のモックを張り直したときにキャッシュを捨てる。"""
    global _states_cache
    _states_cache = None


def _states():
    global _states_cache
    if _states_cache is None:
        _states_cache = boto3.client("stepfunctions")
    return _states_cache


def _now() -> datetime:
    return datetime.now(period.JST)


def _target_month(settings: dict, now: datetime) -> str:
    return period.target_month(now, settings["cutoff_day"], settings["cutoff_time"])


def decide(settings: dict, now: datetime, run: dict | None) -> tuple[bool, str]:
    """設計 6.2 の判定。AWS を呼ばずに `(実行するか, 理由コード)` を返す。"""
    if not settings["auto_run_enabled"]:
        return False, AUTO_RUN_DISABLED
    if _target_month(settings, now) < settings["auto_run_from_month"]:
        return False, BEFORE_AUTO_RUN_FROM_MONTH
    if run is None:
        return True, START
    status = run["status"]
    if status in repo.DONE_STATUSES:
        return False, ALREADY_SUCCEEDED
    if status in repo.ACTIVE_STATUSES and not repo.is_stale(run, now):
        return False, IN_PROGRESS
    if status == repo.FAILED and run["auto_attempts"] >= settings["auto_retry_days"]:
        return False, AUTO_RETRY_EXHAUSTED
    return True, START


def _alert_retry_exhausted(settings: dict, target_env: str, month: str, run: dict) -> None:
    """自動再実行の上限到達を 1 回だけ知らせる（設計 6.8 / 11.1）。"""
    if run.get("retry_alert_sent"):
        return
    attempts = run["auto_attempts"]
    channels = distribution.channels(settings)
    if not channels:
        repo.mark_retry_alert_sent(target_env, month)
        logger.warning(
            "自動再実行の上限に達しましたが、配布先が無効のため要手動アラームは送付しません",
            {"month": month, "attempts": attempts},
        )
        return
    portal_url = messages.portal_url(os.environ["ENV"], target_env, month)
    subject, body = messages.retry_exhausted_message(month, attempts, portal_url)
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
        meta={"target_env": target_env, "month": month, "auto_attempts": attempts},
    )
    repo.mark_retry_alert_sent(target_env, month)
    logger.warning("自動再実行の上限に達したため要手動アラームを送りました", {"month": month, "attempts": attempts})


def _start_execution(target_env: str, month: str, now: datetime) -> str:
    """RUN を `queued` で取得してからステートマシンを起動し、実行 ARN を返す。

    起動に失敗したら RUN を `failed` にしてから送出し直す。`queued` のまま残すと当日中の
    手動実行が 409 になるため（api 側 `post_runs` と同じ扱い）。
    """
    run_token = str(uuid.uuid4())
    run = repo.acquire_run(
        target_env,
        month,
        trigger=TRIGGER,
        force=False,
        run_token=run_token,
        distribute=True,
        stale_before=now.replace(hour=0, minute=0, second=0, microsecond=0),
    )
    attempt = run["attempt"]
    payload = execution.execution_input(
        trigger=TRIGGER,
        target_env=target_env,
        month=month,
        attempt=attempt,
        run_token=run_token,
        distribute=True,
        force=False,
    )
    try:
        response = _states().start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=execution.execution_name(target_env, month, attempt),
            input=json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        logger.exception("ステートマシンの起動に失敗しました", {"target_env": target_env, "month": month})
        repo.finish_failed(target_env, month, attempt, "StartExecution に失敗しました")
        raise
    return response["executionArn"]


@LambdaLogger.contextualize
def handler(event: dict, context) -> dict:
    settings = repo.get_settings()
    target_env = settings["target_env"]
    now = _now()
    month = _target_month(settings, now)
    run = repo.get_run(target_env, month)
    started, reason = decide(settings, now, run)
    if not started:
        if reason == AUTO_RETRY_EXHAUSTED:
            _alert_retry_exhausted(settings, target_env, month, run)
        logger.info("実行を見送りました", {"reason": reason, "target_env": target_env, "month": month})
        return {"started": False, "reason": reason, "month": month, "target_env": target_env, "execution_arn": None}
    execution_arn = _start_execution(target_env, month, now)
    logger.info(
        "ステートマシンを起動しました",
        {"target_env": target_env, "month": month, "execution_arn": execution_arn},
    )
    return {
        "started": True,
        "reason": reason,
        "month": month,
        "target_env": target_env,
        "execution_arn": execution_arn,
    }
