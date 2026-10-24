"""`batch.handlers.trigger` のテスト（設計 3.1 / 6.1 / 6.2 / 11.1 / 11.2）。

moto の DynamoDB / SQS / Step Functions に実際に読み書きし、判定の結果として
`StartExecution` が呼ばれたか・見送り通知が投函されたかを実測で確かめる。
現在時刻は `trigger._now` を差し替えて固定する（締め日時の判定が日付に依存するため）。
自動実行は月 1 回きりで、締め後に配布済みの月は走らない（#1015）。
"""

import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import boto3
import pytest
from batch.handlers import trigger
from botocore.exceptions import ClientError
from saas_a_billing import repo
from saas_a_billing.period import JST

from example_backoffice import notify
from tests.conftest import REGION, make_settings

VERIFICATION = "verification"
PRODUCTION = "production"
SLACK_CHANNEL = "C0123456789"
MAIL_TO = "devA@example.co.jp"

AFTER_CUTOFF = datetime(2026, 10, 21, 6, 0, tzinfo=JST)
BEFORE_CUTOFF = datetime(2026, 10, 20, 6, 0, tzinfo=JST)
OCTOBER_CUTOFF = datetime(2026, 10, 21, 0, 0, tzinfo=JST)
AFTER_CUTOFF_MANUAL = datetime(2026, 10, 21, 1, 30, tzinfo=JST)
BEFORE_CUTOFF_MANUAL = datetime(2026, 10, 20, 15, 0, tzinfo=JST)
OCTOBER = "2026-10"
SEPTEMBER = "2026-09"
AUGUST = "2026-08"


@pytest.fixture
def aws(dynamodb_table, notify_queue, state_machine, monkeypatch):
    """3 リソースを立て、各モジュールの boto3 クライアントキャッシュを捨てる。"""
    repo.reset_clients()
    notify.reset_clients()
    trigger.reset_clients()
    freeze(monkeypatch, AFTER_CUTOFF)
    yield SimpleNamespace(
        table=dynamodb_table,
        queue_url=notify_queue,
        state_machine_arn=state_machine,
        sqs=boto3.client("sqs", region_name=REGION),
        states=boto3.client("stepfunctions", region_name=REGION),
    )
    repo.reset_clients()
    notify.reset_clients()
    trigger.reset_clients()


def freeze(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(trigger, "_now", lambda: moment)


def put_settings(aws, **overrides) -> dict:
    values = {"auto_run_enabled": True, "slack_channel_id": SLACK_CHANNEL, "auto_run_from_month": "2026-08"}
    values.update(overrides)
    settings = make_settings(**values)
    aws.table.put_item(Item={**settings, "PK": "SETTINGS", "SK": "current"})
    return settings


def put_run(
    aws, target_env: str = VERIFICATION, month: str = OCTOBER, *, history: tuple[str, ...] | None = None, **overrides
) -> dict:
    """RUN を直接置く。`history` を省くと、今の attempt の `trigger` 1 件だけを実行者履歴に積む。"""
    run = {
        "target_env": target_env,
        "status": repo.FAILED,
        "trigger": "scheduled",
        "attempt": 1,
        "run_token": "previous-token",
        "distribute": True,
        "force": False,
        "started_at": AFTER_CUTOFF.isoformat(),
    }
    run.update(overrides)
    triggers = (run["trigger"],) if history is None else history
    run[repo.REQUEST_HISTORY] = [request_entry(value, run["started_at"]) for value in triggers]
    aws.table.put_item(Item={**run, "PK": f"RUN#{target_env}", "SK": month})
    return run


def request_entry(trigger_name: str, requested_at: str) -> dict:
    return {
        "requested_by": "system" if trigger_name == "scheduled" else "test-sub",
        "requested_by_email": "",
        "requested_at": requested_at,
        "trigger": trigger_name,
        "distribute": True,
        "force": trigger_name == "scheduled",
    }


def scheduled_history(run: dict) -> list[dict]:
    return [entry for entry in run[repo.REQUEST_HISTORY] if entry["trigger"] == "scheduled"]


def sleeps(monkeypatch, on_call=None) -> list[int]:
    """`_sleep` を差し替えて呼ばれた秒数を記録する（テストでは実時間を待たない）。"""
    calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        calls.append(seconds)
        if on_call is not None:
            on_call(len(calls))

    monkeypatch.setattr(trigger, "_sleep", fake_sleep)
    return calls


def executions(aws) -> list[dict]:
    return aws.states.list_executions(stateMachineArn=aws.state_machine_arn)["executions"]


def execution_input(aws) -> dict:
    started = executions(aws)
    assert len(started) == 1
    return json.loads(aws.states.describe_execution(executionArn=started[0]["executionArn"])["input"])


def notified(aws) -> list[dict]:
    response = aws.sqs.receive_message(QueueUrl=aws.queue_url, MaxNumberOfMessages=10)
    return [json.loads(message["Body"]) for message in response.get("Messages", [])]


def test_starts_execution_when_cutoff_passed_and_no_run(aws):
    put_settings(aws)

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert result["reason"] == trigger.START
    assert result["month"] == OCTOBER
    assert result["target_env"] == VERIFICATION
    assert len(executions(aws)) == 1
    assert result["execution_arn"] == executions(aws)[0]["executionArn"]
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["status"] == repo.QUEUED
    assert run["trigger"] == "scheduled"
    assert run["attempt"] == 1
    assert len(scheduled_history(run)) == 1


def test_waits_until_the_other_run_finishes(aws, monkeypatch):
    """同じ集計環境で別の月が走っている間は起動を待つ（#1006）。"""
    put_settings(aws)
    put_run(aws, month=SEPTEMBER, status=repo.RUNNING)

    def finish(count: int) -> None:
        if count == 2:
            repo.finish_failed(VERIFICATION, SEPTEMBER, 1, "先発が終わった")

    slept = sleeps(monkeypatch, finish)

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert result["waited_sec"] == 2 * trigger.WAIT_INTERVAL_SEC
    assert result["skipped_reason"] is None
    assert slept == [trigger.WAIT_INTERVAL_SEC] * 2
    assert len(executions(aws)) == 1


def test_does_not_wait_when_no_other_run_is_active(aws, monkeypatch):
    put_settings(aws)
    slept = sleeps(monkeypatch)

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert result["waited_sec"] == 0
    assert result["skipped_reason"] is None
    assert slept == []


def test_does_not_wait_for_a_stale_run(aws, monkeypatch):
    """落ちたまま running の RUN は空くのを待っても仕方がない（設計 6.2 の stale 規則）。"""
    put_settings(aws)
    put_run(aws, month=SEPTEMBER, status=repo.RUNNING, started_at=(AFTER_CUTOFF - timedelta(days=1)).isoformat())
    slept = sleeps(monkeypatch)

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert slept == []


def test_skips_and_notifies_when_the_wait_limit_is_reached(aws, monkeypatch):
    """10 分待っても空かなければ起動せず、手動実行を促す（#1006）。"""
    put_settings(aws, mail_enabled=True, slack_enabled=True)
    put_run(aws, month=SEPTEMBER, status=repo.RUNNING)
    slept = sleeps(monkeypatch)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.BUSY_SKIP
    assert result["skipped_reason"] == trigger.BUSY_SKIP
    assert result["waited_sec"] == trigger.WAIT_LIMIT_SEC
    assert len(slept) == trigger.WAIT_LIMIT_SEC // trigger.WAIT_INTERVAL_SEC
    assert executions(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER) is None
    messages = notified(aws)
    assert len(messages) == 1
    assert messages[0]["channels"] == ["email", "slack"]
    assert SEPTEMBER in messages[0]["email"]["text"]
    assert f"/saas-a-billing/runs/{VERIFICATION}/{OCTOBER}" in messages[0]["email"]["text"]


def test_busy_skip_notification_lists_every_running_month(aws, monkeypatch):
    """複数の月が走っているときは全部を通知に載せる（どれを待てばよいか運用が判断できる）。"""
    put_settings(aws, mail_enabled=True)
    put_run(aws, month=AUGUST, status=repo.RUNNING)
    put_run(aws, month=SEPTEMBER, status=repo.RUNNING)
    sleeps(monkeypatch)

    result = trigger.handler({}, None)

    assert result["reason"] == trigger.BUSY_SKIP
    assert f"{AUGUST}、{SEPTEMBER}" in notified(aws)[0]["email"]["text"]


def test_busy_skip_alert_is_skipped_when_distribution_disabled(aws, monkeypatch):
    put_settings(aws)
    put_run(aws, month=SEPTEMBER, status=repo.RUNNING)
    sleeps(monkeypatch)

    result = trigger.handler({}, None)

    assert result["reason"] == trigger.BUSY_SKIP
    assert notified(aws) == []


def test_skips_when_the_run_is_taken_during_the_wait(aws, monkeypatch):
    """待機明けの隙に手動実行が RUN を取ったら奪わずに見送る（#1013 レビュー M2）。"""
    put_settings(aws, mail_enabled=True, slack_enabled=True)
    put_run(aws, status=repo.FAILED, trigger="manual", started_at=BEFORE_CUTOFF_MANUAL.isoformat())
    wait_for_idle = trigger._wait_for_idle

    def take_run(target_env: str, now: datetime):
        waited = wait_for_idle(target_env, now)
        put_run(aws, status=repo.RUNNING, trigger="manual", attempt=2, started_at=AFTER_CUTOFF.isoformat())
        return waited

    monkeypatch.setattr(trigger, "_wait_for_idle", take_run)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ACQUIRE_CONFLICT
    assert result["skipped_reason"] == trigger.ACQUIRE_CONFLICT
    assert executions(aws) == []
    assert notified(aws) == []
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["status"] == repo.RUNNING
    assert run["trigger"] == "manual"
    assert run["attempt"] == 2
    assert scheduled_history(run) == []


def test_start_execution_failure_releases_the_run(aws, monkeypatch):
    """`StartExecution` に失敗したら RUN を `queued` で残さず `failed` にする（当日の手動実行を塞がない）。"""
    put_settings(aws)
    monkeypatch.setattr(trigger, "_states", _raising_states)

    with pytest.raises(ClientError):
        trigger.handler({}, None)

    assert executions(aws) == []
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["status"] == repo.FAILED
    assert run["error"]


def test_start_execution_failure_notifies_distribution_targets(aws, monkeypatch):
    """起動に失敗した月は翌日も自動では走らないので、配布先へ失敗を知らせる（#1015 レビュー S1）。"""
    put_settings(aws, mail_enabled=True, slack_enabled=True)
    monkeypatch.setattr(trigger, "_states", _raising_states)

    with pytest.raises(ClientError):
        trigger.handler({}, None)

    messages = notified(aws)
    assert len(messages) == 1
    assert messages[0]["channels"] == ["email", "slack"]
    assert messages[0]["email"]["to"] == [MAIL_TO]
    assert "集計に失敗しました" in messages[0]["email"]["subject"]
    assert "StartExecution" in messages[0]["email"]["text"]
    assert f"/saas-a-billing/runs/{VERIFICATION}/{OCTOBER}" in messages[0]["email"]["text"]
    assert messages[0]["slack"]["channel"] == SLACK_CHANNEL


def test_start_execution_failure_is_not_notified_when_distribution_disabled(aws, monkeypatch):
    put_settings(aws)
    monkeypatch.setattr(trigger, "_states", _raising_states)

    with pytest.raises(ClientError):
        trigger.handler({}, None)

    assert notified(aws) == []


def test_start_execution_failure_keeps_the_original_error_when_notify_fails(aws, monkeypatch):
    """通知自体の失敗で元の例外を隠さない（Lambda Errors アラームを残す）。"""
    put_settings(aws, mail_enabled=True)
    monkeypatch.setattr(trigger, "_states", _raising_states)

    def broken_send(**_kwargs):
        raise RuntimeError("notify down")

    monkeypatch.setattr(trigger, "send", broken_send)

    with pytest.raises(ClientError):
        trigger.handler({}, None)

    assert repo.get_run(VERIFICATION, OCTOBER)["status"] == repo.FAILED


def test_no_start_before_cutoff(aws, monkeypatch):
    freeze(monkeypatch, BEFORE_CUTOFF)
    put_settings(aws)
    put_run(aws, month=SEPTEMBER, status=repo.DISTRIBUTED)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_SCHEDULED
    assert result["month"] == SEPTEMBER
    assert executions(aws) == []


def test_no_start_when_auto_run_disabled(aws):
    put_settings(aws, auto_run_enabled=False)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.AUTO_RUN_DISABLED
    assert executions(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER) is None


def test_no_start_before_auto_run_from_month(aws, monkeypatch):
    freeze(monkeypatch, BEFORE_CUTOFF)
    put_settings(aws, auto_run_from_month=OCTOBER)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.BEFORE_AUTO_RUN_FROM_MONTH
    assert result["month"] == SEPTEMBER
    assert executions(aws) == []


@pytest.mark.parametrize("status", [repo.SUCCEEDED, repo.DISTRIBUTED, repo.FAILED])
def test_no_start_when_scheduled_already_ran(aws, status):
    """自動実行はその月に 1 回きり。結果が成功でも失敗でも 2 回目は走らない（#1015）。"""
    put_settings(aws)
    put_run(aws, status=status, trigger="scheduled")

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_SCHEDULED
    assert executions(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER)["attempt"] == 1


@pytest.mark.parametrize("status", [repo.SUCCEEDED, repo.DISTRIBUTED, repo.FAILED])
def test_no_start_when_manual_ran_after_the_scheduled_run(aws, status):
    """締め後の自動実行のあとに手動実行があっても、その結果にかかわらず自動では走らない（#1015）。"""
    put_settings(aws)
    put_run(
        aws,
        status=status,
        trigger="manual",
        attempt=2,
        started_at=AFTER_CUTOFF_MANUAL.isoformat(),
        history=("scheduled", "manual"),
    )

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_SCHEDULED
    assert executions(aws) == []


def test_no_start_when_manual_distributed_after_cutoff(aws):
    """締め後に手動実行が配布まで済ませた月は自動実行しない（9/25 の 3 通目の再発防止。#1015）。"""
    put_settings(aws)
    put_run(
        aws,
        status=repo.DISTRIBUTED,
        trigger="manual",
        started_at=AFTER_CUTOFF_MANUAL.isoformat(),
        distributed_started_at=AFTER_CUTOFF_MANUAL.isoformat(),
    )

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_DISTRIBUTED
    assert executions(aws) == []


def test_no_start_when_legacy_manual_distributed_after_cutoff(aws):
    """`distributed_started_at` を持たない旧 RUN は `status` と `started_at` で判定する（本番 2026-09 の形）。"""
    put_settings(aws)
    put_run(aws, status=repo.DISTRIBUTED, trigger="manual", started_at=AFTER_CUTOFF_MANUAL.isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_DISTRIBUTED
    assert executions(aws) == []


def test_distributed_at_exactly_the_cutoff_counts_as_after(aws):
    put_settings(aws)
    put_run(aws, status=repo.DISTRIBUTED, trigger="manual", started_at=OCTOBER_CUTOFF.isoformat())

    result = trigger.handler({}, None)

    assert result["reason"] == trigger.ALREADY_DISTRIBUTED


def test_no_start_when_a_later_manual_attempt_failed_after_distribution(aws):
    """締め後に配布したあと、再実行が失敗しても配布済みの扱いは変わらない（`distributed_started_at` は残る）。"""
    put_settings(aws)
    put_run(
        aws,
        status=repo.FAILED,
        trigger="manual",
        attempt=2,
        started_at=(AFTER_CUTOFF_MANUAL + timedelta(hours=1)).isoformat(),
        distributed_started_at=AFTER_CUTOFF_MANUAL.isoformat(),
        history=("manual", "manual"),
    )

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_DISTRIBUTED


@pytest.mark.parametrize("status", [repo.SUCCEEDED, repo.DISTRIBUTED])
def test_overwrites_manual_success_started_before_cutoff(aws, status):
    """締め前の手動実行の結果は確定値ではないので、締め後の自動実行が上書きする（#1015）。"""
    put_settings(aws)
    put_run(
        aws,
        status=status,
        trigger="manual",
        started_at=BEFORE_CUTOFF_MANUAL.isoformat(),
        distributed_started_at=BEFORE_CUTOFF_MANUAL.isoformat(),
    )

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert result["reason"] == trigger.START
    assert len(executions(aws)) == 1
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["status"] == repo.QUEUED
    assert run["trigger"] == "scheduled"
    assert run["attempt"] == 2
    assert run["force"] is True
    assert len(scheduled_history(run)) == 1


def test_overwrites_legacy_manual_distribution_started_before_cutoff(aws):
    put_settings(aws)
    put_run(aws, status=repo.DISTRIBUTED, trigger="manual", started_at=BEFORE_CUTOFF_MANUAL.isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert result["reason"] == trigger.START


def test_overwrites_manual_success_after_cutoff_without_distribution(aws):
    """締め後の手動実行でも配布なし（`succeeded` 止まり）なら自動実行が集計し直して配る（#1015）。"""
    put_settings(aws)
    put_run(aws, status=repo.SUCCEEDED, trigger="manual", distribute=False, started_at=AFTER_CUTOFF_MANUAL.isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert result["reason"] == trigger.START
    assert repo.get_run(VERIFICATION, OCTOBER)["trigger"] == "scheduled"


def test_starts_when_only_manual_runs_failed(aws):
    """手動実行の失敗だけなら、まだ自動実行していないので走る。"""
    put_settings(aws)
    put_run(aws, status=repo.FAILED, trigger="manual", error="手動の失敗", started_at=AFTER_CUTOFF_MANUAL.isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert result["reason"] == trigger.START
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["attempt"] == 2
    assert "error" not in run


def test_does_not_retry_a_failed_scheduled_run_next_day(aws, monkeypatch):
    """自動実行が失敗しても翌朝に自動では再試行しない。失敗通知を見て手動で実行する（#1015）。"""
    put_settings(aws, mail_enabled=True, slack_enabled=True)
    put_run(aws, status=repo.FAILED, trigger="scheduled", error="前日の失敗")

    for day in range(1, 4):
        freeze(monkeypatch, AFTER_CUTOFF + timedelta(days=day))
        result = trigger.handler({}, None)
        assert result["started"] is False
        assert result["reason"] == trigger.ALREADY_SCHEDULED

    assert executions(aws) == []
    assert notified(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER)["attempt"] == 1


def test_runs_only_once_a_month(aws, monkeypatch):
    """1 日目に起動した月は、2 日目以降は何があっても自動では起動しない。"""
    put_settings(aws)

    first = trigger.handler({}, None)
    repo.finish_failed(VERIFICATION, OCTOBER, 1, "失敗")
    freeze(monkeypatch, AFTER_CUTOFF + timedelta(days=1))
    second = trigger.handler({}, None)

    assert first["started"] is True
    assert second["started"] is False
    assert second["reason"] == trigger.ALREADY_SCHEDULED
    assert len(executions(aws)) == 1


def test_reevaluates_the_day_after_a_busy_skip(aws, monkeypatch):
    """見送った日は実行者履歴に scheduled が積まれないので、翌朝もう一度評価する。"""
    put_settings(aws)
    put_run(aws, month=SEPTEMBER, status=repo.RUNNING)
    sleeps(monkeypatch)

    skipped = trigger.handler({}, None)
    repo.finish_failed(VERIFICATION, SEPTEMBER, 1, "先発が終わった")
    freeze(monkeypatch, AFTER_CUTOFF + timedelta(days=1))
    next_day = trigger.handler({}, None)

    assert skipped["reason"] == trigger.BUSY_SKIP
    assert next_day["started"] is True
    assert next_day["reason"] == trigger.START
    assert len(executions(aws)) == 1


def test_reevaluates_the_day_after_an_acquire_conflict(aws, monkeypatch):
    """待機明けに手動実行に RUN を取られた日も scheduled は積まれない。翌朝の判定に委ねる。"""
    put_settings(aws)
    wait_for_idle = trigger._wait_for_idle

    def take_run(target_env: str, now: datetime):
        waited = wait_for_idle(target_env, now)
        put_run(aws, status=repo.RUNNING, trigger="manual", started_at=AFTER_CUTOFF.isoformat())
        return waited

    monkeypatch.setattr(trigger, "_wait_for_idle", take_run)
    conflict = trigger.handler({}, None)
    monkeypatch.setattr(trigger, "_wait_for_idle", wait_for_idle)
    repo.finish_failed(VERIFICATION, OCTOBER, 1, "手動実行も失敗")
    freeze(monkeypatch, AFTER_CUTOFF + timedelta(days=1))
    next_day = trigger.handler({}, None)

    assert conflict["reason"] == trigger.ACQUIRE_CONFLICT
    assert next_day["started"] is True
    assert next_day["reason"] == trigger.START


def test_reevaluates_after_waiting(aws, monkeypatch):
    """待っている間に手動実行が締め後の配布まで済ませたら、奪わずに見送る（#1015）。"""
    put_settings(aws)
    put_run(aws, month=SEPTEMBER, status=repo.RUNNING)

    def manual_distributes(count: int) -> None:
        if count == 1:
            repo.finish_failed(VERIFICATION, SEPTEMBER, 1, "先発が終わった")
            put_run(
                aws,
                status=repo.DISTRIBUTED,
                trigger="manual",
                started_at=AFTER_CUTOFF_MANUAL.isoformat(),
                distributed_started_at=AFTER_CUTOFF_MANUAL.isoformat(),
            )

    sleeps(monkeypatch, manual_distributes)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_DISTRIBUTED
    assert result["waited_sec"] == trigger.WAIT_INTERVAL_SEC
    assert executions(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER)["trigger"] == "manual"


def test_no_start_when_run_is_running_and_not_stale(aws):
    put_settings(aws)
    put_run(aws, status=repo.RUNNING, trigger="manual", started_at=AFTER_CUTOFF.replace(hour=2).isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.IN_PROGRESS
    assert executions(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER)["attempt"] == 1


def test_starts_when_manual_run_is_stale(aws):
    put_settings(aws)
    put_run(aws, status=repo.RUNNING, trigger="manual", started_at=(AFTER_CUTOFF - timedelta(days=1)).isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert len(executions(aws)) == 1
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["status"] == repo.QUEUED
    assert run["attempt"] == 2


def test_no_start_when_scheduled_run_is_stale(aws):
    """自動実行が刺さったままでも 2 回目は走らせない（手動で取り直す。#1015）。"""
    put_settings(aws)
    put_run(aws, status=repo.RUNNING, started_at=(AFTER_CUTOFF - timedelta(days=1)).isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_SCHEDULED


def test_execution_input_contains_target_env(aws):
    put_settings(aws)

    trigger.handler({}, None)

    payload = execution_input(aws)
    assert payload["target_env"] == VERIFICATION
    assert payload["trigger"] == "scheduled"
    assert payload["month"] == OCTOBER
    assert payload["attempt"] == 1
    assert payload["distribute"] is True
    assert payload["force"] is True
    assert payload["run_token"] == repo.get_run(VERIFICATION, OCTOBER)["run_token"]


def test_execution_name_format(aws):
    put_settings(aws)

    trigger.handler({}, None)

    name = executions(aws)[0]["name"]
    assert re.fullmatch(rf"{VERIFICATION}-{OCTOBER}-1-[0-9a-f]{{8}}", name)


def test_uses_environment_target_env(aws, monkeypatch):
    """集計対象環境は `TARGET_ENV` が正で、保存済みの設定値は使わない（計画 R1 / SP-01）。"""
    monkeypatch.setenv("TARGET_ENV", PRODUCTION)
    put_settings(aws, target_env=VERIFICATION)

    result = trigger.handler({}, None)

    assert result["target_env"] == PRODUCTION
    assert execution_input(aws)["target_env"] == PRODUCTION
    assert executions(aws)[0]["name"].startswith(f"{PRODUCTION}-{OCTOBER}-")
    assert repo.get_run(PRODUCTION, OCTOBER)["status"] == repo.QUEUED
    assert repo.get_run(VERIFICATION, OCTOBER) is None


def test_cutoff_change_reflected_next_day(aws):
    put_settings(aws, cutoff_day=20)

    default_cutoff = trigger.handler({}, None)

    put_settings(aws, cutoff_day=25)
    changed_cutoff = trigger.handler({}, None)

    assert default_cutoff["month"] == OCTOBER
    assert changed_cutoff["month"] == SEPTEMBER
    assert sorted(execution["name"].split("-1-")[0] for execution in executions(aws)) == [
        f"{VERIFICATION}-{SEPTEMBER}",
        f"{VERIFICATION}-{OCTOBER}",
    ]


def test_decide_is_pure():
    """AWS リソース（fixture）を一切使わずに設計 6.2 の全分岐を判定できる。"""
    settings = make_settings(auto_run_enabled=True, auto_run_from_month="2026-08")
    after = AFTER_CUTOFF_MANUAL.isoformat()
    before = BEFORE_CUTOFF_MANUAL.isoformat()

    def run(status: str, *triggers: str, started_at: str = after, **extra) -> dict:
        history = [{"trigger": value} for value in triggers or ("manual",)]
        return {"status": status, "started_at": started_at, repo.REQUEST_HISTORY: history, **extra}

    def decide(value: dict | None) -> tuple[bool, str]:
        return trigger.decide(settings, AFTER_CUTOFF, value)

    assert trigger.decide(make_settings(auto_run_enabled=False), AFTER_CUTOFF, None) == (
        False,
        trigger.AUTO_RUN_DISABLED,
    )
    assert trigger.decide({**settings, "auto_run_from_month": "2026-11"}, AFTER_CUTOFF, None) == (
        False,
        trigger.BEFORE_AUTO_RUN_FROM_MONTH,
    )
    assert decide(None) == (True, trigger.START)
    for status in (repo.SUCCEEDED, repo.DISTRIBUTED, repo.FAILED, repo.RUNNING):
        assert decide(run(status, "scheduled")) == (False, trigger.ALREADY_SCHEDULED)
        assert decide(run(status, "manual", "scheduled", "manual")) == (False, trigger.ALREADY_SCHEDULED)
    assert decide(run(repo.RUNNING, started_at=AFTER_CUTOFF.replace(hour=2).isoformat())) == (
        False,
        trigger.IN_PROGRESS,
    )
    assert decide(run(repo.RUNNING, started_at=(AFTER_CUTOFF - timedelta(days=1)).isoformat())) == (
        True,
        trigger.START,
    )
    assert decide(run(repo.DISTRIBUTED, distributed_started_at=after)) == (False, trigger.ALREADY_DISTRIBUTED)
    assert decide(run(repo.DISTRIBUTED)) == (False, trigger.ALREADY_DISTRIBUTED)
    assert decide(run(repo.FAILED, distributed_started_at=after)) == (False, trigger.ALREADY_DISTRIBUTED)
    assert decide(run(repo.DISTRIBUTED, started_at=before, distributed_started_at=before)) == (True, trigger.START)
    assert decide(run(repo.DISTRIBUTED, started_at=before)) == (True, trigger.START)
    assert decide(run(repo.SUCCEEDED, started_at=before)) == (True, trigger.START)
    assert decide(run(repo.SUCCEEDED)) == (True, trigger.START)
    assert decide(run(repo.FAILED)) == (True, trigger.START)
    assert decide({"status": repo.SUCCEEDED, "started_at": after}) == (True, trigger.START)


def _raising_states():
    """`StartExecution` が必ず失敗する Step Functions クライアントの代わり。"""

    def start_execution(**_kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "StartExecution")

    return SimpleNamespace(start_execution=start_execution)
