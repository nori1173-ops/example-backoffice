"""`batch.handlers.trigger` のテスト（設計 3.1 / 6.1 / 6.2 / 11.1 / 11.2）。

moto の DynamoDB / SQS / Step Functions に実際に読み書きし、判定の結果として
`StartExecution` が呼ばれたか・「要手動」アラームが投函されたかを実測で確かめる。
現在時刻は `trigger._now` を差し替えて固定する（締め日時の判定が日付に依存するため）。
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
OCTOBER = "2026-10"
SEPTEMBER = "2026-09"


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


def put_run(aws, target_env: str = VERIFICATION, month: str = OCTOBER, **overrides) -> dict:
    run = {
        "target_env": target_env,
        "status": repo.FAILED,
        "trigger": "scheduled",
        "attempt": 1,
        "auto_attempts": 1,
        "run_token": "previous-token",
        "distribute": True,
        "force": False,
        "started_at": AFTER_CUTOFF.isoformat(),
        "retry_alert_sent": False,
    }
    run.update(overrides)
    aws.table.put_item(Item={**run, "PK": f"RUN#{target_env}", "SK": month})
    return run


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
    assert run["auto_attempts"] == 1


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


def test_no_start_before_cutoff(aws, monkeypatch):
    freeze(monkeypatch, BEFORE_CUTOFF)
    put_settings(aws)
    put_run(aws, month=SEPTEMBER, status=repo.SUCCEEDED)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_SUCCEEDED
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


def test_no_start_when_run_succeeded(aws):
    put_settings(aws)
    put_run(aws, status=repo.SUCCEEDED)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_SUCCEEDED
    assert executions(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER)["attempt"] == 1


def test_no_start_when_run_distributed(aws):
    put_settings(aws)
    put_run(aws, status=repo.DISTRIBUTED)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.ALREADY_SUCCEEDED
    assert executions(aws) == []


def test_no_start_when_run_is_running_and_not_stale(aws):
    put_settings(aws)
    put_run(aws, status=repo.RUNNING, started_at=AFTER_CUTOFF.replace(hour=2).isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.IN_PROGRESS
    assert executions(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER)["attempt"] == 1


def test_starts_when_run_is_stale(aws):
    put_settings(aws)
    put_run(aws, status=repo.RUNNING, started_at=(AFTER_CUTOFF - timedelta(days=1)).isoformat())

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert len(executions(aws)) == 1
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["status"] == repo.QUEUED
    assert run["attempt"] == 2
    assert run["auto_attempts"] == 2


def test_retries_failed_run_next_day(aws):
    put_settings(aws)
    put_run(aws, status=repo.FAILED, auto_attempts=1, error="前日の失敗")

    result = trigger.handler({}, None)

    assert result["started"] is True
    assert len(executions(aws)) == 1
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["status"] == repo.QUEUED
    assert run["attempt"] == 2
    assert run["auto_attempts"] == 2
    assert "error" not in run


def test_stops_when_auto_attempts_reaches_limit(aws):
    put_settings(aws, auto_retry_days=5, mail_enabled=True, slack_enabled=True)
    put_run(aws, status=repo.FAILED, attempt=5, auto_attempts=5)

    result = trigger.handler({}, None)

    assert result["started"] is False
    assert result["reason"] == trigger.AUTO_RETRY_EXHAUSTED
    assert executions(aws) == []
    messages = notified(aws)
    assert len(messages) == 1
    assert messages[0]["channels"] == ["email", "slack"]
    assert messages[0]["email"]["to"] == [MAIL_TO]
    assert "【要手動】" in messages[0]["email"]["subject"]
    assert "5 回失敗" in messages[0]["email"]["subject"]
    assert messages[0]["slack"]["channel"] == SLACK_CHANNEL
    assert f"/saas-a-billing/runs/{VERIFICATION}/{OCTOBER}" in messages[0]["email"]["text"]
    assert repo.get_run(VERIFICATION, OCTOBER)["retry_alert_sent"] is True


def test_retry_alert_is_sent_only_once(aws):
    put_settings(aws, auto_retry_days=5, mail_enabled=True, slack_enabled=True)
    put_run(aws, status=repo.FAILED, attempt=5, auto_attempts=5)

    first = trigger.handler({}, None)
    second = trigger.handler({}, None)

    assert first["reason"] == trigger.AUTO_RETRY_EXHAUSTED
    assert second["reason"] == trigger.AUTO_RETRY_EXHAUSTED
    assert len(notified(aws)) == 1


def test_retry_alert_skipped_when_distribution_disabled(aws):
    """配布が両方 OFF なら要手動アラームは送らず、送付済みフラグだけ立てる。"""
    put_settings(aws, auto_retry_days=5)
    put_run(aws, status=repo.FAILED, attempt=5, auto_attempts=5)

    result = trigger.handler({}, None)

    assert result["reason"] == trigger.AUTO_RETRY_EXHAUSTED
    assert notified(aws) == []
    assert repo.get_run(VERIFICATION, OCTOBER)["retry_alert_sent"] is True


def test_manual_force_does_not_consume_auto_attempts(aws):
    put_settings(aws, auto_retry_days=5)
    put_run(aws, status=repo.FAILED, attempt=4, auto_attempts=4)

    manual = repo.acquire_run(
        VERIFICATION,
        OCTOBER,
        trigger="manual",
        force=True,
        run_token="manual-token",
        stale_before=AFTER_CUTOFF.replace(hour=0),
    )
    assert manual["attempt"] == 5
    assert manual["auto_attempts"] == 4
    repo.finish_failed(VERIFICATION, OCTOBER, 5, "手動実行も失敗")

    result = trigger.handler({}, None)

    assert result["started"] is True
    run = repo.get_run(VERIFICATION, OCTOBER)
    assert run["attempt"] == 6
    assert run["auto_attempts"] == 5


def test_execution_input_contains_target_env(aws):
    put_settings(aws)

    trigger.handler({}, None)

    payload = execution_input(aws)
    assert payload["target_env"] == VERIFICATION
    assert payload["trigger"] == "scheduled"
    assert payload["month"] == OCTOBER
    assert payload["attempt"] == 1
    assert payload["distribute"] is True
    assert payload["force"] is False
    assert payload["run_token"] == repo.get_run(VERIFICATION, OCTOBER)["run_token"]


def test_execution_name_format(aws):
    put_settings(aws)

    trigger.handler({}, None)

    name = executions(aws)[0]["name"]
    assert re.fullmatch(rf"{VERIFICATION}-{OCTOBER}-1-[0-9a-f]{{8}}", name)


def test_uses_settings_target_env(aws):
    put_settings(aws, target_env=PRODUCTION)

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
    settings = make_settings(auto_run_enabled=True, auto_run_from_month="2026-08", auto_retry_days=5)
    running = {"status": repo.RUNNING, "started_at": AFTER_CUTOFF.replace(hour=2).isoformat()}
    stale = {"status": repo.RUNNING, "started_at": (AFTER_CUTOFF - timedelta(days=1)).isoformat()}

    assert trigger.decide(make_settings(auto_run_enabled=False), AFTER_CUTOFF, None) == (
        False,
        trigger.AUTO_RUN_DISABLED,
    )
    assert trigger.decide({**settings, "auto_run_from_month": "2026-11"}, AFTER_CUTOFF, None) == (
        False,
        trigger.BEFORE_AUTO_RUN_FROM_MONTH,
    )
    assert trigger.decide(settings, AFTER_CUTOFF, None) == (True, trigger.START)
    assert trigger.decide(settings, AFTER_CUTOFF, {"status": repo.SUCCEEDED}) == (False, trigger.ALREADY_SUCCEEDED)
    assert trigger.decide(settings, AFTER_CUTOFF, {"status": repo.DISTRIBUTED}) == (False, trigger.ALREADY_SUCCEEDED)
    assert trigger.decide(settings, AFTER_CUTOFF, running) == (False, trigger.IN_PROGRESS)
    assert trigger.decide(settings, AFTER_CUTOFF, stale) == (True, trigger.START)
    assert trigger.decide(settings, AFTER_CUTOFF, {"status": repo.FAILED, "auto_attempts": 4}) == (
        True,
        trigger.START,
    )
    assert trigger.decide(settings, AFTER_CUTOFF, {"status": repo.FAILED, "auto_attempts": 5}) == (
        False,
        trigger.AUTO_RETRY_EXHAUSTED,
    )


def _raising_states():
    """`StartExecution` が必ず失敗する Step Functions クライアントの代わり。"""

    def start_execution(**_kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "StartExecution")

    return SimpleNamespace(start_execution=start_execution)
