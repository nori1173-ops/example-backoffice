"""`saas_a_billing.repo` のテスト（設計 7.1 のアイテム種別と 6.9 の条件式の表）。

moto の実テーブルに対して読み書きし、条件付き書き込みが実際に弾かれることを
確認する。条件不成立は botocore の `ConditionalCheckFailedException` で表れる。
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from saas_a_billing import repo
from saas_a_billing.messages import DEFAULT_SLACK_TEMPLATE
from saas_a_billing.period import JST
from saas_a_billing.pricing import PriceTableNotFoundError

MONTH = "2026-08"
VERIFICATION = "verification"
PRODUCTION = "production"
RUN_TOKEN = "run-token-1"
ACTOR = "test-sub"
ACTOR_EMAIL = "devA@example.co.jp"
EXECUTION_ARN = "arn:aws:states:ap-northeast-1:000000000000:execution:saas-a-billing-run:verification-2026-08-1"
SAASA_HOST = "saasa-verification.example.com"
PREVIOUS_ATTEMPT_FIELDS = ("error", "finished_at", "execution_arn", "summary", "api_token", "token_expires_at")
TOKEN_FIELDS = ("api_token", "token_expires_at")
LOGGER_NAME = "example_backoffice"


def test_target_env_falls_back_to_verification_with_warning(monkeypatch, caplog):
    """`TARGET_ENV` が不正なら本番ではなく検証環境として扱い、気付けるよう警告する（計画 R1）。"""
    monkeypatch.setenv("TARGET_ENV", "staging")
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    assert repo.target_env() == VERIFICATION
    assert "TARGET_ENV" in caplog.text


def test_target_env_unset_falls_back_to_verification_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("TARGET_ENV", raising=False)
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    assert repo.target_env() == VERIFICATION
    assert "TARGET_ENV" in caplog.text


def test_target_env_does_not_warn_when_configured(monkeypatch, caplog):
    monkeypatch.setenv("TARGET_ENV", PRODUCTION)
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    assert repo.target_env() == PRODUCTION
    assert caplog.text == ""


@pytest.fixture
def table(dynamodb_table):
    """moto のテーブルを作り、repo の boto3 クライアントキャッシュを捨てる。"""
    repo.reset_clients()
    yield dynamodb_table
    repo.reset_clients()


def today_start() -> datetime:
    return datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)


def acquire(
    target_env: str = VERIFICATION,
    month: str = MONTH,
    *,
    trigger: str = "manual",
    force: bool = False,
    run_token: str = RUN_TOKEN,
    stale_before: datetime | None = None,
    **kwargs,
) -> dict:
    return repo.acquire_run(
        target_env,
        month,
        trigger=trigger,
        force=force,
        run_token=run_token,
        stale_before=stale_before or today_start(),
        **kwargs,
    )


def raw_run(table, target_env: str = VERIFICATION, month: str = MONTH) -> dict:
    return table.get_item(Key={"PK": f"RUN#{target_env}", "SK": month})["Item"]


def assert_condition_failed(call, *args, **kwargs) -> None:
    with pytest.raises(ClientError) as excinfo:
        call(*args, **kwargs)
    assert excinfo.value.response["Error"]["Code"] == "ConditionalCheckFailedException"


def price_item(effective_from: str) -> dict:
    return {
        "effective_from": effective_from,
        "bands": [{"max_items": 500, "price_3y": 650, "price_1y": 450}],
        "camera": {"price_3y": 1800, "price_1y": 1400},
        "note": f"{effective_from} 版",
    }


def test_get_settings_seeds_defaults_when_absent(table):
    settings = repo.get_settings()

    assert settings["version"] == 0
    assert settings["target_env"] == VERIFICATION
    assert settings["cutoff_day"] == 20
    assert settings["cutoff_time"] == "24:00"
    assert settings["auto_retry_days"] == 5
    assert settings["auto_run_enabled"] is False
    assert settings["auto_run_from_month"] == datetime.now(JST).strftime("%Y-%m")
    assert settings["mail_to"] == [ACTOR_EMAIL]
    assert settings["mail_cc"] == []
    assert settings["slack_channel_id"] == ""
    assert settings["slack_channel_label"] == "#proj-saas-a-billing"
    assert settings["slack_mention"] == ""
    assert settings["slack_template"] == DEFAULT_SLACK_TEMPLATE
    assert settings["exclude_keywords"] == ["デモ"]
    assert settings["camera_format_names"] == []
    assert settings["mail_enabled"] is False
    assert settings["slack_enabled"] is False
    assert table.get_item(Key={"PK": "SETTINGS", "SK": "current"})["Item"]["version"] == 0


def test_get_settings_returns_existing(table):
    table.put_item(Item={"PK": "SETTINGS", "SK": "current", "version": 3, "cutoff_day": 15, "mail_to": ["a@b.jp"]})

    settings = repo.get_settings()

    assert settings["version"] == 3
    assert settings["cutoff_day"] == 15
    assert settings["mail_to"] == ["a@b.jp"]
    assert "PK" not in settings


def test_get_settings_overrides_stored_target_env(table):
    """集計対象環境はサイト固定。保存済みの値が違っても読み出しで固定値に直す（計画 R1 / SP-01）。"""
    table.put_item(Item={"PK": "SETTINGS", "SK": "current", "version": 3, "target_env": PRODUCTION})

    assert repo.get_settings()["target_env"] == VERIFICATION


def test_get_settings_seeds_fixed_target_env(table, monkeypatch):
    monkeypatch.setenv("TARGET_ENV", PRODUCTION)

    settings = repo.get_settings()

    assert settings["target_env"] == PRODUCTION
    assert table.get_item(Key={"PK": "SETTINGS", "SK": "current"})["Item"]["target_env"] == PRODUCTION


def test_get_settings_fills_distribution_toggles_for_legacy_item(table):
    """配布トグルを持たない保存済みの設定でも、読み出しは両キーを False で補完する。"""
    table.put_item(Item={"PK": "SETTINGS", "SK": "current", "version": 3, "slack_channel_id": "C0123ABCD"})

    settings = repo.get_settings()

    assert settings["mail_enabled"] is False
    assert settings["slack_enabled"] is False


def test_get_settings_keeps_stored_distribution_toggles(table):
    table.put_item(Item={"PK": "SETTINGS", "SK": "current", "version": 3, "mail_enabled": True, "slack_enabled": True})

    settings = repo.get_settings()

    assert settings["mail_enabled"] is True
    assert settings["slack_enabled"] is True


def test_put_settings_stores_distribution_toggles(table):
    repo.get_settings()

    updated = repo.put_settings({"mail_enabled": True, "slack_enabled": True}, 0, ACTOR, ACTOR_EMAIL)

    assert updated["mail_enabled"] is True
    assert updated["slack_enabled"] is True
    assert repo.get_settings()["mail_enabled"] is True
    assert repo.get_settings()["slack_enabled"] is True


def test_put_settings_increments_version_and_writes_audit(table):
    repo.get_settings()

    updated = repo.put_settings({"cutoff_day": 15}, 0, ACTOR, ACTOR_EMAIL)

    assert updated["version"] == 1
    assert updated["cutoff_day"] == 15
    assert updated["updated_by"] == ACTOR
    assert updated["updated_by_email"] == ACTOR_EMAIL
    assert repo.get_settings()["version"] == 1
    assert len(repo.list_audits()) == 1


def test_put_settings_version_mismatch_raises_conflict(table):
    repo.get_settings()

    with pytest.raises(repo.VersionConflictError):
        repo.put_settings({"cutoff_day": 15}, 99, ACTOR, ACTOR_EMAIL)

    assert repo.get_settings()["cutoff_day"] == 20
    assert repo.list_audits() == []


def test_put_settings_audit_records_actor_and_before_after(table):
    repo.get_settings()
    repo.put_settings({"cutoff_day": 15}, 0, ACTOR, ACTOR_EMAIL)

    audit = repo.list_audits()[0]

    assert audit["target"] == "settings"
    assert audit["actor"] == ACTOR
    assert audit["actor_email"] == ACTOR_EMAIL
    assert audit["before"]["cutoff_day"] == 20
    assert audit["after"]["cutoff_day"] == 15
    assert audit["after"]["version"] == 1


def test_seed_defaults_creates_price_table_2355(table):
    repo.seed_defaults()

    prices = repo.list_prices()

    assert len(prices) == 1
    assert prices[0]["effective_from"] == "2025-09-01"
    assert prices[0]["bands"] == [
        {"max_items": 500, "price_3y": 650, "price_1y": 450},
        {"max_items": 800, "price_3y": 1000, "price_1y": 700},
        {"max_items": 1600, "price_3y": 1200, "price_1y": 900},
        {"max_items": 1700, "price_3y": 1350, "price_1y": 1000},
    ]
    assert prices[0]["camera"] == {"price_3y": 1800, "price_1y": 1400}
    assert repo.get_settings()["version"] == 0


def test_seed_defaults_is_idempotent(table):
    repo.seed_defaults()
    repo.put_settings({"cutoff_day": 15}, 0, ACTOR, ACTOR_EMAIL)
    created_at = repo.list_prices()[0]["created_at"]

    repo.seed_defaults()

    assert len(repo.list_prices()) == 1
    assert repo.list_prices()[0]["created_at"] == created_at
    assert repo.get_settings()["cutoff_day"] == 15


def test_get_price_table_at_picks_latest_on_or_before(table):
    for effective_from in ("2025-09-01", "2026-04-01", "2099-01-01"):
        repo.put_price(price_item(effective_from), ACTOR, ACTOR_EMAIL)

    assert repo.get_price_table_at(date(2026, 6, 1))["effective_from"] == "2026-04-01"
    assert repo.get_price_table_at(date(2026, 4, 1))["effective_from"] == "2026-04-01"
    assert repo.get_price_table_at(date(2026, 3, 31))["effective_from"] == "2025-09-01"


def test_list_prices_seeds_default_when_empty(table):
    """1 版も無いときの一覧は既定値（support-tracker #2001）を自動シードして返す。"""
    prices = repo.list_prices()

    assert len(prices) == 1
    assert prices[0]["effective_from"] == "2025-09-01"


def test_get_price_table_at_seeds_default_when_no_version_exists(table):
    """1 版も無いときの解決は既定値を自動シードして続行する。"""
    price = repo.get_price_table_at(date(2026, 8, 20))

    assert price["effective_from"] == "2025-09-01"
    assert price["camera"] == {"price_3y": 1800, "price_1y": 1400}


def test_get_price_table_at_ignores_future(table):
    repo.put_price(price_item("2099-01-01"), ACTOR, ACTOR_EMAIL)

    with pytest.raises(PriceTableNotFoundError):
        repo.get_price_table_at(date(2026, 6, 1))


def test_put_price_rejects_duplicate_effective_from(table):
    repo.put_price(price_item("2026-04-01"), ACTOR, ACTOR_EMAIL)

    with pytest.raises(repo.AlreadyExistsError):
        repo.put_price({**price_item("2026-04-01"), "note": "上書き"}, ACTOR, ACTOR_EMAIL)

    assert repo.list_prices()[0]["note"] == "2026-04-01 版"
    assert len(repo.list_audits()) == 1


def test_acquire_run_creates_new_queued_run(table):
    run = acquire(requested_by=ACTOR, requested_by_email=ACTOR_EMAIL, distribute=False)

    assert run["attempt"] == 1
    assert run["status"] == "queued"
    assert run["month"] == MONTH
    assert run["target_env"] == VERIFICATION
    assert run["run_token"] == RUN_TOKEN
    assert run["trigger"] == "manual"
    assert run["distribute"] is False
    assert run["retry_alert_sent"] is False
    assert run["requested_by"] == ACTOR
    assert repo.get_run(VERIFICATION, MONTH)["attempt"] == 1


def test_acquire_run_scheduled_sets_auto_attempts_1(table):
    run = acquire(trigger="scheduled")

    assert run["auto_attempts"] == 1
    assert run["attempt"] == 1


def test_acquire_run_manual_does_not_increment_auto_attempts(table):
    acquire(trigger="scheduled")
    repo.finish_failed(VERIFICATION, MONTH, 1, "失敗")

    run = acquire(trigger="manual", run_token="run-token-2")

    assert run["attempt"] == 2
    assert run["auto_attempts"] == 1


def test_acquire_run_on_failed_increments_attempt(table):
    acquire()
    repo.finish_failed(VERIFICATION, MONTH, 1, "失敗")

    run = acquire(trigger="scheduled", run_token="run-token-2")

    assert run["attempt"] == 2
    assert run["status"] == "queued"
    assert run["auto_attempts"] == 1
    assert run["run_token"] == "run-token-2"


def test_acquire_run_on_succeeded_without_force_raises_already_succeeded(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    finish_success(1)

    with pytest.raises(repo.RunAcquisitionError) as excinfo:
        acquire(run_token="run-token-2")

    assert excinfo.value.reason == "ALREADY_SUCCEEDED"
    assert repo.get_run(VERIFICATION, MONTH)["attempt"] == 1


def test_acquire_run_on_running_without_force_raises_already_running(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    with pytest.raises(repo.RunAcquisitionError) as excinfo:
        acquire(run_token="run-token-2")

    assert excinfo.value.reason == "ALREADY_RUNNING"
    assert repo.get_run(VERIFICATION, MONTH)["status"] == "running"


def test_acquire_run_on_stale_running_succeeds(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    yesterday = today_start() - timedelta(hours=18)
    table.update_item(
        Key={"PK": f"RUN#{VERIFICATION}", "SK": MONTH},
        UpdateExpression="SET started_at = :started_at",
        ExpressionAttributeValues={":started_at": yesterday.isoformat()},
    )

    run = acquire(run_token="run-token-2")

    assert run["attempt"] == 2
    assert run["status"] == "queued"


def test_acquire_run_with_force_bypasses_condition(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    finish_success(1)

    run = acquire(force=True, run_token="run-token-2")

    assert run["attempt"] == 2
    assert run["status"] == "queued"
    assert run["force"] is True


def test_acquire_run_removes_previous_attempt_fields(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.finish_failed(VERIFICATION, MONTH, 1, "getToken に失敗しました")
    table.update_item(
        Key={"PK": f"RUN#{VERIFICATION}", "SK": MONTH},
        UpdateExpression="SET #summary = :summary, #execution_arn = :arn, #api_token = :token, #expires = :expires",
        ExpressionAttributeNames={
            "#summary": "summary",
            "#execution_arn": "execution_arn",
            "#api_token": "api_token",
            "#expires": "token_expires_at",
        },
        ExpressionAttributeValues={
            ":summary": {"total_amount": 312000},
            ":arn": EXECUTION_ARN,
            ":token": "api-token",
            ":expires": "2026-08-31 08:48:25",
        },
    )
    assert set(PREVIOUS_ATTEMPT_FIELDS) <= set(raw_run(table))

    run = acquire(run_token="run-token-2")

    assert run["attempt"] == 2
    assert run["status"] == "queued"
    for name in PREVIOUS_ATTEMPT_FIELDS:
        assert name not in run
        assert name not in raw_run(table)


def test_acquire_run_with_force_removes_previous_attempt_fields(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.set_token(VERIFICATION, MONTH, 1, "api-token", "2026-08-31 08:48:25")
    repo.set_execution_arn(VERIFICATION, MONTH, 1, EXECUTION_ARN)

    run = acquire(force=True, run_token="run-token-2")

    assert run["attempt"] == 2
    for name in PREVIOUS_ATTEMPT_FIELDS:
        assert name not in run
        assert name not in raw_run(table)


def test_acquire_run_keeps_only_the_latest_history_entries(table):
    """履歴は直近 `REQUEST_HISTORY_LIMIT` 件に切り詰める（SEC-10 / 計画 R8）。"""
    acquire(requested_by="actor-0")
    for index in range(1, repo.REQUEST_HISTORY_LIMIT + 3):
        repo.finish_failed(VERIFICATION, MONTH, index, "失敗")
        acquire(requested_by=f"actor-{index}", run_token=f"run-token-{index}")

    history = repo.get_run(VERIFICATION, MONTH)["request_history"]

    assert len(history) == repo.REQUEST_HISTORY_LIMIT
    assert history[-1]["requested_by"] == f"actor-{repo.REQUEST_HISTORY_LIMIT + 2}"
    assert history[0]["requested_by"] == "actor-3"


def test_acquire_run_history_survives_previous_attempt_cleanup(table):
    """再取得の REMOVE で履歴を消さない（SEC-10 / 計画 R8）。"""
    acquire(requested_by=ACTOR, requested_by_email=ACTOR_EMAIL)
    repo.finish_failed(VERIFICATION, MONTH, 1, "失敗")

    run = acquire(trigger="scheduled", run_token="run-token-2")

    assert [entry["requested_by"] for entry in run["request_history"]] == [ACTOR, repo.SYSTEM_ACTOR]
    assert [entry["trigger"] for entry in run["request_history"]] == ["manual", "scheduled"]


def test_run_keys_are_separated_by_target_env(table):
    acquire(VERIFICATION)
    acquire(PRODUCTION, run_token="run-token-prod")

    assert repo.get_run(VERIFICATION, MONTH)["attempt"] == 1
    assert repo.get_run(PRODUCTION, MONTH)["attempt"] == 1
    assert raw_run(table, VERIFICATION)["run_token"] == RUN_TOKEN
    assert raw_run(table, PRODUCTION)["run_token"] == "run-token-prod"

    repo.start_run(PRODUCTION, MONTH, 1, "run-token-prod")

    assert repo.get_run(VERIFICATION, MONTH)["status"] == "queued"
    assert repo.get_run(PRODUCTION, MONTH)["status"] == "running"
    assert repo.get_run(PRODUCTION, "2026-07") is None


def test_start_run_requires_matching_run_token(table):
    acquire()

    assert_condition_failed(repo.start_run, VERIFICATION, MONTH, 1, "other-token")
    assert repo.get_run(VERIFICATION, MONTH)["status"] == "queued"

    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    assert repo.get_run(VERIFICATION, MONTH)["status"] == "running"


def test_set_token_and_clear_token(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    repo.set_token(VERIFICATION, MONTH, 1, "api-token", "2026-08-31 08:48:25")

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["api_token"] == "api-token"
    assert run["token_expires_at"] == "2026-08-31 08:48:25"
    assert_condition_failed(repo.set_token, VERIFICATION, MONTH, 2, "api-token", "2026-08-31 08:48:25")


def test_token_is_removed_by_clear_token(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.set_token(VERIFICATION, MONTH, 1, "api-token", "2026-08-31 08:48:25")

    repo.clear_token(VERIFICATION, MONTH, 1)

    run = repo.get_run(VERIFICATION, MONTH)
    assert "api_token" not in run
    assert "token_expires_at" not in run


def test_set_progress_records_counts(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    repo.set_progress(VERIFICATION, MONTH, 1, 12, 3)

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["tenants_total"] == 12
    assert run["tenants_done"] == 3


def test_increment_tenants_done_is_atomic(table):
    """同じ値を読んだ 2 つの `CollectTenant` が進めても取りこぼさない（Map の並列度 2）。"""
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.set_progress(VERIFICATION, MONTH, 1, 12, 3)
    read = repo.get_run(VERIFICATION, MONTH)

    repo.increment_tenants_done(VERIFICATION, MONTH, 1)
    repo.increment_tenants_done(VERIFICATION, MONTH, 1)

    run = repo.get_run(VERIFICATION, MONTH)
    assert read["tenants_done"] == 3
    assert run["tenants_done"] == 5
    assert run["tenants_total"] == 12


def test_increment_tenants_done_requires_running_and_attempt(table):
    acquire()

    assert_condition_failed(repo.increment_tenants_done, VERIFICATION, MONTH, 1)

    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    assert_condition_failed(repo.increment_tenants_done, VERIFICATION, MONTH, 2)

    repo.increment_tenants_done(VERIFICATION, MONTH, 1)

    assert repo.get_run(VERIFICATION, MONTH)["tenants_done"] == 1


def test_finish_success_requires_running_and_attempt(table):
    acquire()

    assert_condition_failed(finish_success, 1)

    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    assert_condition_failed(finish_success, 2)

    finish_success(1)

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["status"] == "succeeded"
    assert run["s3_xlsx_key"].endswith(".xlsx")
    assert run["summary"]["total_amount"] == 312000
    assert run["price_effective_from"] == "2025-09-01"
    assert run["closing_period"]["cutoff_day"] == 20
    assert run["finished_at"]


def test_finish_success_removes_api_token(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.set_token(VERIFICATION, MONTH, 1, "api-token", "2026-08-31 08:48:25")

    finish_success(1)

    for name in TOKEN_FIELDS:
        assert name not in repo.get_run(VERIFICATION, MONTH)
        assert name not in raw_run(table)


def test_finish_distributed_requires_succeeded(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    assert_condition_failed(finish_distributed, 1)

    finish_success(1)
    finish_distributed(1)

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["status"] == "distributed"
    assert run["notify_message_id"] == "message-1"
    assert run["notify_channels"] == ["email", "slack"]
    assert run["distribute_note"] == ""
    assert run["notified_at"]


def test_set_distribute_note_keeps_succeeded(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    finish_success(1)

    repo.set_distribute_note(VERIFICATION, MONTH, 1, "配布先が無効のため送付なし")

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["status"] == "succeeded"
    assert run["distribute_note"] == "配布先が無効のため送付なし"


def test_set_distribute_note_requires_succeeded(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)

    assert_condition_failed(repo.set_distribute_note, VERIFICATION, MONTH, 1, "配布先が無効のため送付なし")


def test_finish_failed_only_requires_attempt(table):
    acquire()

    repo.finish_failed(VERIFICATION, MONTH, 1, "getToken に失敗しました")

    run = repo.get_run(VERIFICATION, MONTH)
    assert run["status"] == "failed"
    assert run["error"] == "getToken に失敗しました"
    assert_condition_failed(repo.finish_failed, VERIFICATION, MONTH, 2, "別試行")


def test_finish_failed_removes_api_token(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.set_token(VERIFICATION, MONTH, 1, "api-token", "2026-08-31 08:48:25")

    repo.finish_failed(VERIFICATION, MONTH, 1, "getToken に失敗しました")

    for name in TOKEN_FIELDS:
        assert name not in repo.get_run(VERIFICATION, MONTH)
        assert name not in raw_run(table)


def test_set_execution_arn_records_arn_with_attempt_condition(table):
    acquire()

    repo.set_execution_arn(VERIFICATION, MONTH, 1, EXECUTION_ARN)

    assert repo.get_run(VERIFICATION, MONTH)["execution_arn"] == EXECUTION_ARN

    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.set_execution_arn(VERIFICATION, MONTH, 1, f"{EXECUTION_ARN}-2")

    assert repo.get_run(VERIFICATION, MONTH)["execution_arn"] == f"{EXECUTION_ARN}-2"


def test_set_execution_arn_rejects_stale_attempt(table):
    acquire()

    assert_condition_failed(repo.set_execution_arn, VERIFICATION, MONTH, 2, EXECUTION_ARN)
    assert "execution_arn" not in repo.get_run(VERIFICATION, MONTH)


def test_set_saasa_host_records_host_with_attempt_condition(table):
    acquire()

    assert_condition_failed(repo.set_saasa_host, VERIFICATION, MONTH, 2, SAASA_HOST)
    assert "saasa_host" not in repo.get_run(VERIFICATION, MONTH)

    repo.set_saasa_host(VERIFICATION, MONTH, 1, SAASA_HOST)

    assert repo.get_run(VERIFICATION, MONTH)["saasa_host"] == SAASA_HOST


def test_mark_retry_alert_sent_sets_flag(table):
    acquire(trigger="scheduled")
    repo.finish_failed(VERIFICATION, MONTH, 1, "失敗")

    assert repo.get_run(VERIFICATION, MONTH)["retry_alert_sent"] is False

    repo.mark_retry_alert_sent(VERIFICATION, MONTH)

    assert repo.get_run(VERIFICATION, MONTH)["retry_alert_sent"] is True


def test_mark_retry_alert_sent_requires_existing_run(table):
    assert_condition_failed(repo.mark_retry_alert_sent, VERIFICATION, MONTH)


def test_list_audits_returns_latest_first(table):
    repo.get_settings()
    for version, cutoff_day in enumerate((19, 18, 17)):
        repo.put_settings({"cutoff_day": cutoff_day}, version, ACTOR, ACTOR_EMAIL)

    audits = repo.list_audits()

    assert [audit["after"]["cutoff_day"] for audit in audits] == [17, 18, 19]
    assert [audit["at"] for audit in audits] == sorted((audit["at"] for audit in audits), reverse=True)
    assert len(repo.list_audits(limit=2)) == 2


def test_list_runs_returns_month_descending(table):
    for month in ("2026-06", "2026-08", "2026-07"):
        acquire(month=month)

    runs = repo.list_runs(VERIFICATION)

    assert [run["month"] for run in runs] == ["2026-08", "2026-07", "2026-06"]
    assert [run["month"] for run in repo.list_runs(VERIFICATION, limit=2)] == ["2026-08", "2026-07"]


def test_list_runs_without_target_env_merges_both(table):
    acquire(VERIFICATION, "2026-07")
    acquire(VERIFICATION, "2026-08")
    acquire(PRODUCTION, "2026-08")

    runs = repo.list_runs()

    assert [(run["month"], run["target_env"]) for run in runs] == [
        ("2026-08", "verification"),
        ("2026-08", "production"),
        ("2026-07", "verification"),
    ]
    assert len(repo.list_runs(limit=2)) == 2


def test_is_stale_true_for_yesterday_running(table):
    now = datetime(2026, 8, 31, 6, 0, tzinfo=JST)
    run = {"status": "running", "started_at": datetime(2026, 8, 30, 23, 59, tzinfo=JST).isoformat()}

    assert repo.is_stale(run, now) is True
    assert repo.is_stale({**run, "status": "queued"}, now) is True
    assert repo.is_stale({**run, "status": "failed"}, now) is False


def test_is_stale_false_for_today_running(table):
    now = datetime(2026, 8, 31, 6, 0, tzinfo=JST)
    run = {"status": "running", "started_at": datetime(2026, 8, 31, 0, 0, tzinfo=JST).isoformat()}

    assert repo.is_stale(run, now) is False
    assert repo.is_stale({"status": "running"}, now) is False


def test_decimal_values_are_returned_as_int(table):
    acquire()
    repo.start_run(VERIFICATION, MONTH, 1, RUN_TOKEN)
    repo.set_progress(VERIFICATION, MONTH, 1, 12, 12)
    finish_success(1, metrics={"duration_sec": 268.5, "export_calls": 60, "export_bytes": 1200000})

    run = repo.get_run(VERIFICATION, MONTH)

    assert isinstance(run["attempt"], int)
    assert isinstance(run["tenants_total"], int)
    assert isinstance(run["summary"]["total_amount"], int)
    assert isinstance(run["metrics"]["export_calls"], int)
    assert run["metrics"]["duration_sec"] == 268.5
    assert not isinstance(raw_run(table)["attempt"], int)
    assert isinstance(raw_run(table)["attempt"], Decimal)


def finish_success(attempt: int, **overrides) -> None:
    values = {
        "summary": {
            "tenant_count": 12,
            "device_count": 340,
            "billable_count": 300,
            "no_data_count": 25,
            "excluded_count": 15,
            "review_count": 2,
            "failed_tenant_count": 0,
            "missing_count": 1,
            "total_amount": 312000,
        },
        "s3_xlsx_key": f"saas-a-billing/{VERIFICATION}/2026/08/saas_a_billing_2026-08_verification.xlsx",
        "s3_result_key": f"saas-a-billing/{VERIFICATION}/2026/08/result.json",
        "closing_period": {
            "from": "2026-07-21T00:00:00+09:00",
            "to": "2026-08-21T00:00:00+09:00",
            "cutoff_day": 20,
            "cutoff_time": "24:00",
            "open": False,
        },
        "price_effective_from": "2025-09-01",
        "metrics": {"duration_sec": 268, "export_calls": 60, "export_bytes": 1200000},
    }
    values.update(overrides)
    repo.finish_success(VERIFICATION, MONTH, attempt, **values)


def finish_distributed(attempt: int) -> None:
    repo.finish_distributed(
        VERIFICATION,
        MONTH,
        attempt,
        notify_message_id="message-1",
        notify_channels=["email", "slack"],
        distribute_note="",
    )
