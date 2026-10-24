"""実行判定（設計 6.2）。EventBridge Scheduler が毎日 06:00 JST に非同期 Invoke する。

最新の締め日時を過ぎた対象月を、その月にまだ自動実行していなければ 1 回だけ集計して
配布する（#1015）。判定は純粋関数 `decide()` に閉じ込め、AWS へのアクセスは
`handler()` 側だけに置く。

- 自動実行は月 1 回きり。RUN の実行者履歴（`request_history`）に `scheduled` が 1 件で
  もあれば、結果（成功・失敗・stale）を問わず 2 回目は走らせない。失敗しても翌朝に自動
  では再試行せず、失敗通知（`MarkFailed`）を見て手動で実行してもらう。履歴は直近
  `repo.REQUEST_HISTORY_LIMIT` 件に切り詰められるが、1 か月にそれを超える attempt は
  運用上起きないので取りこぼしは許容する
- 締め後に配布まで済んだ月は走らせない。配布した attempt の開始時刻
  （`distributed_started_at`。無い旧 RUN は `distributed` の `started_at`）が締め日時
  以上なら配布済みとみなす
- それ以外（締め前に始めた手動実行の結果、失敗、stale、締め後でも配布なしの成功）は
  確定値ではないので、`force` で奪って集計し直し、配布する

起動すると決めたあとも、同じ集計環境で別の RUN が走っている間は最長 `WAIT_LIMIT_SEC`
秒だけ待つ。SaaS_A の管理 API は 1 ユーザー 1 トークンで、後発の `Init` のログインが
先発のトークンを無効化するため（#1006）。空かなければ起動せず、手動実行を促す通知を
送る。見送った日は実行者履歴に `scheduled` が積まれないので、翌朝また評価される。
待っている間に手動実行が配布まで済ませることがあるため、待ったあとは判定をやり直す。

RUN を取ったあとに `StartExecution` が失敗した月は、実行者履歴に `scheduled` が残るため
翌日以降も自動では走らない。この経路だけは `MarkFailed` を通らないので、配布先へ失敗
通知を送ってから例外を送出し直す（Lambda Errors アラームも残す。#1015）。
"""

import json
import os
import time
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
ALREADY_SCHEDULED = "ALREADY_SCHEDULED"
IN_PROGRESS = "IN_PROGRESS"
ALREADY_DISTRIBUTED = "ALREADY_DISTRIBUTED"
BUSY_SKIP = "BUSY_SKIP"
ACQUIRE_CONFLICT = "ACQUIRE_CONFLICT"
START = "START"

START_FAILURE_MESSAGE = "スケジュール実行の起動（StartExecution）に失敗しました。手動で実行してください"

WAIT_INTERVAL_SEC = 15
WAIT_LIMIT_SEC = 600

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


def _sleep(seconds: int) -> None:
    time.sleep(seconds)


def _target_month(settings: dict, now: datetime) -> str:
    return period.target_month(now, settings["cutoff_day"], settings["cutoff_time"])


def _as_jst(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=period.JST) if moment.tzinfo is None else moment


def _scheduled_before(run: dict) -> bool:
    """その月にスケジュール実行を 1 度でも起動したか（実行者履歴で見る）。"""
    return any(entry.get("trigger") == TRIGGER for entry in run.get(repo.REQUEST_HISTORY) or [])


def _distributed_after_cutoff(settings: dict, now: datetime, run: dict) -> bool:
    """締め日時以降に始めた attempt が配布まで済ませたか。

    `distributed_started_at` を持たない旧 RUN は、`distributed` の `started_at` で代える。
    """
    started_at = run.get("distributed_started_at")
    if not started_at and run.get("status") == repo.DISTRIBUTED:
        started_at = run.get("started_at")
    if not started_at:
        return False
    return _as_jst(started_at) >= period.latest_cutoff(now, settings["cutoff_day"], settings["cutoff_time"])


def decide(settings: dict, now: datetime, run: dict | None) -> tuple[bool, str]:
    """設計 6.2 の判定。AWS を呼ばずに `(実行するか, 理由コード)` を返す。

    `run` は `now` から決まる対象月の RUN を渡すこと。締め日時の比較は `now` 以前で最も
    新しい締め日時（その対象月の締め日時）を基準にしているため、別の月の RUN は正しく
    判定できない。
    """
    if not settings["auto_run_enabled"]:
        return False, AUTO_RUN_DISABLED
    if _target_month(settings, now) < settings["auto_run_from_month"]:
        return False, BEFORE_AUTO_RUN_FROM_MONTH
    if run is None:
        return True, START
    if _scheduled_before(run):
        return False, ALREADY_SCHEDULED
    if run.get("status") in repo.ACTIVE_STATUSES and not repo.is_stale(run, now):
        return False, IN_PROGRESS
    if _distributed_after_cutoff(settings, now, run):
        return False, ALREADY_DISTRIBUTED
    return True, START


def _wait_for_idle(target_env: str, now: datetime) -> tuple[int, list[str]]:
    """同じ集計環境の RUN が空くまで待つ。`(待った秒数, まだ走っている月)` を返す（#1006）。

    SaaS_A の管理 API は 1 ユーザー 1 トークンで、後発の `Init` のログインが先発の
    トークンを無効化する。締め直後は手動実行と重なりやすいので、`WAIT_LIMIT_SEC` まで
    は待ってから起動する。stale な RUN は `active_runs` が最初から外す。
    """
    waited = 0
    while True:
        running = [run["month"] for run in repo.active_runs(target_env, now)]
        if not running or waited >= WAIT_LIMIT_SEC:
            return waited, running
        _sleep(WAIT_INTERVAL_SEC)
        waited += WAIT_INTERVAL_SEC


def _alert_busy_skip(settings: dict, target_env: str, month: str, running_months: list[str]) -> None:
    """実行中の RUN が空かず見送ったことを知らせる（設計 6.2 / 6.8）。

    翌日 06:00 に同じ判定をやり直し、起動するまで毎朝知らせる。二重送信の防止は置かない。
    """
    channels = distribution.channels(settings)
    if not channels:
        logger.warning(
            "実行中の RUN があるためスケジュール実行を見送りましたが、配布先が無効のため通知しません",
            {"month": month, "running_months": running_months},
        )
        return
    portal_url = messages.portal_url(os.environ["ENV"], target_env, month)
    subject, body = messages.busy_skip_message(month, running_months, portal_url, target_env)
    send(
        app=APP,
        channels=channels,
        email=Email(to=settings["mail_to"], cc=settings["mail_cc"], subject=subject, text=body)
        if distribution.EMAIL_CHANNEL in channels
        else None,
        slack=Slack(text=body, channel=settings["slack_channel_id"], mention=settings["slack_mention"] or None)
        if distribution.SLACK_CHANNEL in channels
        else None,
        meta={"target_env": target_env, "month": month, "running_months": running_months},
    )


def _alert_start_failure(settings: dict, target_env: str, month: str) -> None:
    """`StartExecution` の失敗を設定で有効な配布先に知らせる（設計 6.2 / 6.8）。

    通知自体の失敗はログに留め、呼び出し側が送出し直す元の例外を隠さない。
    """
    channels = distribution.channels(settings)
    if not channels:
        logger.warning(
            "スケジュール実行の起動に失敗しましたが、配布先が無効のため失敗通知を送付しません",
            {"target_env": target_env, "month": month},
        )
        return
    portal_url = messages.portal_url(os.environ["ENV"], target_env, month)
    subject, body = messages.failure_message(month, START_FAILURE_MESSAGE, portal_url)
    try:
        send(
            app=APP,
            channels=channels,
            email=Email(to=settings["mail_to"], cc=settings["mail_cc"], subject=subject, text=body)
            if distribution.EMAIL_CHANNEL in channels
            else None,
            slack=Slack(text=body, channel=settings["slack_channel_id"], mention=settings["slack_mention"] or None)
            if distribution.SLACK_CHANNEL in channels
            else None,
            meta={"target_env": target_env, "month": month},
        )
    except Exception as error:
        logger.error(
            "スケジュール実行の起動失敗の通知に失敗しました",
            {"target_env": target_env, "month": month, "error": str(error)},
        )


def _start_execution(target_env: str, month: str, now: datetime) -> str:
    """RUN を `queued` で取得してからステートマシンを起動し、実行 ARN を返す。

    起動に失敗したら RUN を `failed` にしてから送出し直す。`queued` のまま残すと当日中の
    手動実行が 409 になるため（api 側 `post_runs` と同じ扱い）。

    `force=True` は締め前の手動実行の成功結果や失敗の RUN を奪うために要る（#1013 /
    #1015）。奪ってよいかは `decide()` が判断済みで、スケジュール実行済みの月や締め後に
    配布済みの月はここまで来ない。`guard_active=True` で
    「実行中（stale でない queued / running）の RUN は奪わない」条件だけは残す。判定と
    待機を抜けた数百 ms の隙に手動実行が RUN を取る場合があるため。
    """
    run_token = str(uuid.uuid4())
    run = repo.acquire_run(
        target_env,
        month,
        trigger=TRIGGER,
        force=True,
        guard_active=True,
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
        force=True,
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


def _result(
    *, started: bool, reason: str, month: str, target_env: str, execution_arn: str | None, waited_sec: int
) -> dict:
    return {
        "started": started,
        "reason": reason,
        "month": month,
        "target_env": target_env,
        "execution_arn": execution_arn,
        "waited_sec": waited_sec,
        "skipped_reason": None if started else reason,
    }


@LambdaLogger.contextualize
def handler(event: dict, context) -> dict:
    settings = repo.get_settings()
    target_env = repo.target_env()
    now = _now()
    month = _target_month(settings, now)
    run = repo.get_run(target_env, month)
    started, reason = decide(settings, now, run)
    if not started:
        logger.info("実行を見送りました", {"reason": reason, "target_env": target_env, "month": month})
        return _result(
            started=False, reason=reason, month=month, target_env=target_env, execution_arn=None, waited_sec=0
        )
    waited_sec, running_months = _wait_for_idle(target_env, now)
    if running_months:
        _alert_busy_skip(settings, target_env, month, running_months)
        logger.warning(
            "実行中の RUN が空かないためスケジュール実行を見送りました",
            {"target_env": target_env, "month": month, "running_months": running_months, "waited_sec": waited_sec},
        )
        return _result(
            started=False,
            reason=BUSY_SKIP,
            month=month,
            target_env=target_env,
            execution_arn=None,
            waited_sec=waited_sec,
        )
    if waited_sec:
        started, reason = decide(settings, now, repo.get_run(target_env, month))
        if not started:
            logger.info(
                "待機中に状況が変わったため実行を見送りました",
                {"reason": reason, "target_env": target_env, "month": month, "waited_sec": waited_sec},
            )
            return _result(
                started=False,
                reason=reason,
                month=month,
                target_env=target_env,
                execution_arn=None,
                waited_sec=waited_sec,
            )
    try:
        execution_arn = _start_execution(target_env, month, now)
    except repo.RunAcquisitionError:
        # 待機明けの隙に手動実行が RUN を取った場合。実行者履歴に scheduled は積まれない
        # ので、翌朝の判定に委ねる。当日は見送るだけにして通知は出さない
        logger.warning(
            "RUN を取得できなかったためスケジュール実行を見送りました",
            {"target_env": target_env, "month": month, "waited_sec": waited_sec},
        )
        return _result(
            started=False,
            reason=ACQUIRE_CONFLICT,
            month=month,
            target_env=target_env,
            execution_arn=None,
            waited_sec=waited_sec,
        )
    except Exception:
        _alert_start_failure(settings, target_env, month)
        raise
    logger.info(
        "ステートマシンを起動しました",
        {"target_env": target_env, "month": month, "execution_arn": execution_arn, "waited_sec": waited_sec},
    )
    return _result(
        started=True,
        reason=reason,
        month=month,
        target_env=target_env,
        execution_arn=execution_arn,
        waited_sec=waited_sec,
    )
