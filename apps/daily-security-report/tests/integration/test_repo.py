import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from daily_security_report import repo
from daily_security_report.settings import SettingsValidationError, update_settings

NOW = datetime.fromisoformat("2026-10-15T07:00:00+09:00")
DAY = "2026-10-14"


def test_seed_settings_and_decimal_round_trip(resources):
    assert repo.get_settings(seed=False)["version"] == 0
    assert resources["table"].item_count == 0
    seeded = repo.get_settings()
    assert seeded["version"] == 0 and seeded["updated_by"] == "system"
    updated = repo.put_settings({**seeded, "ratio": 3.25}, 0, "actor", "test@example.co.jp")
    assert updated["version"] == 1
    assert repo.get_settings()["ratio"] == 3.25
    assert type(repo.get_settings()["ratio"]) is float
    items = resources["table"].scan()["Items"]
    audit = next(i for i in items if i["PK"] == "AUDIT")
    assert audit["before"]["version"] == 0
    assert audit["after"]["version"] == 1 and audit["actor"] == "actor"
    assert isinstance(audit["after"]["ratio"], Decimal)
    with pytest.raises(repo.VersionConflictError):
        repo.put_settings(seeded, 0, "other", "test@example.co.jp")
    assert len(resources["table"].scan()["Items"]) == 2


def test_daily_acquisition_force_stale_and_attempt_guard(resources):
    run = repo.acquire_daily(DAY, trigger="manual", force=False, now=NOW)
    assert run["attempt"] == 1 and run["status"] == "running"
    assert run["ttl"] == int((NOW + timedelta(days=400)).timestamp())
    for force in (False, True):
        with pytest.raises(repo.DailyAcquisitionError) as exc:
            repo.acquire_daily(DAY, trigger="manual", force=force, now=NOW + timedelta(minutes=20))
        assert exc.value.reason == "IN_PROGRESS"
    run = repo.acquire_daily(DAY, trigger="manual", force=False, now=NOW + timedelta(minutes=20, seconds=1))
    assert run["attempt"] == 2
    with pytest.raises(ClientError):
        repo.finish_daily(DAY, 1, summary={}, now=NOW)
    repo.finish_daily(DAY, 2, summary={"date": DAY, "value": 1.5}, message_id="test", channels=["slack"], now=NOW)
    assert repo.get_daily(DAY)["sent_count"] == 1
    with pytest.raises(repo.DailyAcquisitionError) as exc:
        repo.acquire_daily(DAY, trigger="scheduled", now=NOW + timedelta(hours=1))
    assert exc.value.reason == "ALREADY_DONE"
    run = repo.acquire_daily(DAY, trigger="manual", force=True, now=NOW + timedelta(hours=1))
    assert run["attempt"] == 3 and run["sent_count"] == 1
    assert "summary" not in run
    repo.fail_daily(DAY, 3, "TestError", now=NOW)
    assert repo.get_daily(DAY)["status"] == "failed"
    assert repo.acquire_daily(DAY, trigger="manual", now=NOW)["attempt"] == 4


def test_history_list_projection_and_stale_display(resources):
    for _ in range(12):
        run = repo.acquire_daily(DAY, trigger="manual", force=True, now=NOW)
        repo.finish_daily(DAY, run["attempt"], summary={"date": DAY}, now=NOW)
    assert len(resources["table"].get_item(Key={"PK": "DAILY", "SK": DAY})["Item"]["request_history"]) == 10
    repo.acquire_daily("2026-10-13", trigger="manual", now=NOW - timedelta(minutes=21))
    repo.acquire_daily("2026-10-12", trigger="manual", now=NOW)
    repo.fail_daily("2026-10-12", 1, "failed", now=NOW)
    rows = repo.list_days(limit=2, now=NOW)
    assert [row["date"] for row in rows] == [DAY, "2026-10-13"]
    assert rows[1]["status"] == "failed" and rows[1]["error"] == "TIMEOUT(推定)"
    assert all("summary" not in r and "request_history" not in r for r in rows)
    assert len(repo.history("2026-10-15", 7)) == 1
    assert not repo.history("2026-10-14", 7)


def test_alert_claim_dedup_finish_delete_and_batch_get(resources):
    finding = {"id": "test-id", "region": "ap-northeast-1", "severity": 8.1, "title": "Test", "type": "Test:Finding"}
    assert repo.claim_alert(finding, now=NOW)
    assert not repo.claim_alert(finding, now=NOW)
    key = "ap-northeast-1#test-id"
    assert not repo.alerted_findings([key])
    repo.finish_alert(finding, "message", now=NOW)
    assert repo.alerted_findings([key, "ap-northeast-1#missing"]) == {key}
    item = resources["table"].get_item(Key={"PK": "ALERT", "SK": key})["Item"]
    assert item["ttl"] == int((NOW + timedelta(days=90)).timestamp())
    repo.delete_alert(finding)
    assert repo.claim_alert(finding, now=NOW)


def test_force_clears_old_notify_and_failed_unsent_attempt_stays_empty(resources):
    repo.acquire_daily(DAY, trigger="manual", now=NOW)
    repo.finish_daily(DAY, 1, summary={"red_count": 3}, message_id="previous", channels=["slack"], now=NOW)
    run = repo.acquire_daily(DAY, trigger="manual", force=True, now=NOW)
    assert not any(run.get(k) for k in ("notify_message_id", "notify_channels", "notified_at"))
    repo.fail_daily(DAY, 2, "TestError", channels=["email"], now=NOW)
    row = repo.list_days(now=NOW)[0]
    assert row["notify_channels"] == [] and row["notified_at"] == "" and row["sent_count"] == 1


def test_fail_daily_clears_stale_notification_even_without_acquire(resources):
    repo.acquire_daily(DAY, trigger="manual", now=NOW)
    resources["table"].update_item(
        Key={"PK": "DAILY", "SK": DAY},
        UpdateExpression="SET notify_message_id = :m, notify_channels = :c, notified_at = :t",
        ExpressionAttributeValues={":m": "old", ":c": ["slack"], ":t": NOW.isoformat()},
    )
    repo.fail_daily(DAY, 1, "Unsent", now=NOW)
    row = repo.get_daily(DAY)
    assert row["notify_message_id"] == row["notified_at"] == "" and row["notify_channels"] == []


def test_actor_batch_roundtrip_and_initialization(resources):
    assert not repo.has_actors()
    actors = {
        f"arn:aws:iam::000000000000:user/example-{i}": {
            "kind": "user",
            "last_seen": DAY,
            "first_seen": DAY,
            "last_ip": "203.0.113.1",
            "last_event": "ListBuckets",
            "seen_days": 1,
            "source": "cloudtrail",
        }
        for i in range(111)
    }
    repo.batch_write_actors(actors)
    assert repo.has_actors()
    assert repo.batch_get_actors(list(actors) + list(actors)) == actors


def test_v2_daily_list_fields_and_detail_hides_request_history(resources):
    from daily_security_report import levels

    summary = levels.apply_rules(
        {
            "date": DAY,
            "sources": {"cloudtrail": {"status": "ok"}},
            "findings": [levels.finding("cloudtrail", "ROOT", "root")],
            "ai": {"status": "skipped"},
        }
    )
    repo.acquire_daily(DAY, trigger="manual", now=NOW)
    repo.finish_daily(DAY, 1, summary=summary, now=NOW)
    detail, row = repo.get_daily(DAY), repo.list_days(now=NOW)[0]
    assert "request_history" not in detail and "summary" not in row
    assert detail["level"] == row["level"] == "critical"
    assert row["finding_counts"] == {"critical": 1, "warning": 0}
    assert row["ai_status"] == "skipped" and row["ai_model_label"] == row["ai_opinion"] == ""
    assert not row["legacy"] and detail["ai"] == {"status": "skipped"}
    assert "red_count" not in resources["table"].get_item(Key={"PK": "DAILY", "SK": DAY})["Item"]


def test_legacy_list_and_detail_are_adapted_without_writes(resources):
    resources["table"].put_item(
        Item={
            "PK": "DAILY",
            "SK": DAY,
            "status": "succeeded",
            "red_count": 3,
            "request_history": [{"force": False}],
            "summary": {
                "date": DAY,
                "red_count": 3,
                "red_counts": {"cloudtrail": 3},
                "red": [{"section": "cloudtrail", "code": "ROOT", "text": "root"}],
            },
        }
    )
    row, detail = repo.list_days()[0], repo.get_daily(DAY)
    assert row["legacy"] and row["level"] == "warning" and row["finding_counts"]["warning"] == 3
    assert detail["summary"]["findings"][0]["level"] == "warning"
    assert detail["ai"]["status"] == "disabled" and "request_history" not in detail
    assert "red_count" in resources["table"].get_item(Key={"PK": "DAILY", "SK": DAY})["Item"]


def test_actor_batches_retry_unprocessed_and_respect_limits(monkeypatch):
    from unittest.mock import Mock

    client = Mock()
    key = repo._key("ACTOR", "example")
    client.batch_get_item.side_effect = [
        {"Responses": {}, "UnprocessedKeys": {repo._table(): {"Keys": [key]}}},
        {"Responses": {repo._table(): [{**key, "seen_days": {"N": "1"}}]}},
    ]
    monkeypatch.setattr(repo, "_client", lambda: client)
    assert repo.batch_get_actors(["example"]) == {"example": {"seen_days": 1}}
    pending = {repo._table(): [{"PutRequest": {"Item": key}}]}
    client.batch_write_item.side_effect = [{"UnprocessedItems": pending}, {}]
    repo.batch_write_actors({"example": {"seen_days": 1}})
    assert client.batch_write_item.call_count == 2


def test_daily_size_budget_includes_ai_copy_and_keeps_evidence_omission_counts(resources):
    import json

    from daily_security_report import levels

    rows = [{"ip": "203.0.113.1", "event": "架" * 1000} for _ in range(20)]
    summary = levels.apply_rules(
        {
            "schema": "daily-security-report.daily.v2",
            "date": DAY,
            "findings": [],
            "sources": {"cloudtrail": {"status": "ok", "details": {str(i): rows for i in range(12)}}},
            "ai": {"status": "ok", "opinion": "見解" * 200},
            "actors": {"status": "ok"},
        }
    )
    repo.acquire_daily(DAY, trigger="manual", now=NOW)
    repo.finish_daily(DAY, 1, summary=summary, now=NOW)
    saved = resources["table"].get_item(Key={"PK": "DAILY", "SK": DAY})["Item"]
    assert len(json.dumps(saved, ensure_ascii=False, default=str).encode()) < 400 * 1024
    assert saved["summary"]["sources"]["cloudtrail"]["details_truncated"]
    assert saved["ai"]["opinion"] == saved["summary"]["ai"]["opinion"] == "見解" * 200


def test_ai_state_updates_preserve_unspecified_fields(resources):
    assert repo.get_ai_state() is None
    repo.put_ai_state({"last_good_model_id": "old", "changed_at": "first"})
    repo.put_ai_state({"last_model_id": "new"})
    assert repo.get_ai_state() == {"last_good_model_id": "old", "last_model_id": "new", "changed_at": "first"}


def test_preview_ttl_limit_and_expiry(resources, monkeypatch):
    import uuid
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 16, 0, 5, tzinfo=repo.JST)
    monkeypatch.setattr(repo, "_now", lambda: now)
    ids = [str(uuid.uuid4()) for _ in range(21)]
    for preview_id in ids[:20]:
        repo.create_preview({"preview_id": preview_id, "date": "2026-10-14", "requested_by": "example"})
    assert repo.count_previews("2026-10-16") == 20
    assert repo.count_previews("2026-10-15") == 0
    assert repo.get_preview(ids[0])["status"] == "running"
    assert repo.get_preview(ids[0])["ttl"] == int((now + timedelta(days=1)).timestamp())
    with pytest.raises(repo.PreviewLimitError):
        repo.create_preview({"preview_id": ids[20], "date": "2026-10-14"})
    assert repo.get_preview("limit#2026-10-16") is None
    assert repo.update_preview(ids[0], {"status": "succeeded", "result": {"duration_sec": 1.25}})
    assert not repo.update_preview(ids[0], {"status": "failed"})
    assert repo.get_preview(ids[0])["result"]["duration_sec"] == 1.25
    now += timedelta(minutes=5)
    stale = repo.get_preview(ids[1])
    assert stale["status"] == "failed" and stale["error"]["code"] == "AI_UNAVAILABLE"
    assert not repo.update_preview(ids[1], {"status": "succeeded"})
    now += timedelta(days=1)
    assert repo.get_preview(ids[0]) is None
    repo.create_preview({"preview_id": ids[20], "date": "2026-10-14"})
    assert repo.count_previews(now.date().isoformat()) == 1


def test_preview_lock_is_exclusive_and_lease_recovers(resources, monkeypatch):
    import uuid
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 16, 10, tzinfo=repo.JST)
    monkeypatch.setattr(repo, "_now", lambda: now)
    resources["table"].put_item(
        Item={"PK": "PREVIEW", "SK": "limit#2026-10-16", "owner": "other", "lock_until": int(now.timestamp()) + 45}
    )
    with pytest.raises(repo.PreviewBusyError):
        repo.create_preview({"preview_id": str(uuid.uuid4()), "date": "2026-10-14"})
    assert repo.count_previews("2026-10-16") == 0
    now += timedelta(seconds=46)
    repo.create_preview({"preview_id": str(uuid.uuid4()), "date": "2026-10-14"})
    assert repo.count_previews("2026-10-16") == 1
    assert resources["table"].get_item(Key={"PK": "PREVIEW", "SK": "limit#2026-10-16"}).get("Item") is None


def test_preview_count_paginates_consistently(monkeypatch):
    from unittest.mock import Mock

    client = Mock()
    client.query.side_effect = [
        {"Count": 12, "LastEvaluatedKey": {"PK": {"S": "PREVIEW"}, "SK": {"S": "next"}}},
        {"Count": 8},
    ]
    monkeypatch.setattr(repo, "_client", lambda: client)
    assert repo.count_previews("2026-10-16") == 20
    assert all(call.kwargs["ConsistentRead"] for call in client.query.call_args_list)
    assert client.query.call_args.kwargs["ExclusiveStartKey"]["SK"] == {"S": "next"}


@pytest.mark.parametrize("status", ["ok", "initializing", "disabled", "unavailable"])
def test_rerun_reuses_only_completed_actor_evaluation(resources, status):
    actors = {"status": status, "dormant": [], "new": [], "external": []}
    repo.acquire_daily(DAY, trigger="manual")
    repo.finish_daily(DAY, 1, summary={"schema": "daily-security-report.daily.v2", "actors": actors})
    rerun = repo.acquire_daily(DAY, trigger="manual", force=True)
    if status in {"ok", "initializing"}:
        assert rerun["previous_actors"] == actors | {"truncated": {}}
        assert rerun["previous_actor_findings"] == []
    else:
        assert "previous_actors" not in rerun and "previous_actor_findings" not in rerun


@pytest.mark.parametrize("byte_size", [180000, 180001])
def test_settings_size_guard_counts_stored_utf8_and_preserves_existing_data(resources, monkeypatch, byte_size):
    monkeypatch.setattr(repo, "_now", lambda: NOW)
    current = repo.get_settings()
    before = resources["table"].scan()["Items"]
    exclusion = {
        "kind": "principal",
        "value": "test-user",
        "scope": ["all"],
        "category": "",
        "reason": "文" * 59000,
        "expires_at": "",
    }
    stored = {
        **current,
        "version": 1,
        "updated_at": NOW.isoformat(),
        "updated_by": "actor",
        "updated_by_email": "test@example.co.jp",
        "exclusions": [exclusion],
    }
    padding = byte_size - len(json.dumps(stored, ensure_ascii=False).encode("utf-8"))
    assert padding > 0
    exclusion["reason"] += "a" * padding
    new = update_settings(current, {"version": 0, "exclusions": [exclusion]})
    assert len(json.dumps(stored, ensure_ascii=False).encode("utf-8")) == byte_size
    transaction = Mock(wraps=repo._client().transact_write_items)
    monkeypatch.setattr(repo._client(), "transact_write_items", transaction)
    if byte_size == 180000:
        saved = repo.put_settings(new, 0, "actor", "test@example.co.jp")
        assert saved == stored
        assert repo.put_settings(saved, 1, "actor", "test@example.co.jp")["version"] == 2
        assert transaction.call_count == 2
        audits = [item for item in resources["table"].scan()["Items"] if item["PK"] == "AUDIT"]
        assert len(audits) == 2 and repo.get_settings()["exclusions"] == [exclusion]
    else:
        with pytest.raises(SettingsValidationError) as error:
            repo.put_settings(new, 0, "actor", "test@example.co.jp")
        assert error.value.code == "SETTINGS_TOO_LARGE"
        assert error.value.message == "設定が大きすぎます。名前付きプロンプトか除外リストを減らしてください"
        transaction.assert_not_called()
        assert resources["table"].scan()["Items"] == before


@pytest.mark.parametrize(
    "code,message,is_size_error",
    [
        ("ValidationException", "Item size to update has exceeded the maximum allowed size", True),
        ("ValidationException", "Item size has exceeded the maximum allowed size", True),
        ("ValidationException", "Transaction size cannot exceed 4MB", True),
        ("ValidationException", "Invalid UpdateExpression", False),
        ("AccessDeniedException", "Item size unavailable", False),
        ("TransactionCanceledException", "Transaction size unavailable", False),
    ],
)
def test_settings_transaction_converts_only_validation_size_errors(
    resources, monkeypatch, code, message, is_size_error
):
    current = repo.get_settings()
    before = resources["table"].scan()["Items"]
    original = ClientError({"Error": {"Code": code, "Message": message}}, "TransactWriteItems")
    transaction = Mock(side_effect=original)
    monkeypatch.setattr(repo._client(), "transact_write_items", transaction)
    with pytest.raises(SettingsValidationError if is_size_error else ClientError) as error:
        repo.put_settings(current, 0, "actor", "test@example.co.jp")
    if is_size_error:
        assert error.value.code == "SETTINGS_TOO_LARGE" and error.value.__cause__ is original
        assert error.value.message == "設定が大きすぎます。名前付きプロンプトか除外リストを減らしてください"
    else:
        assert error.value is original
    transaction.assert_called_once()
    assert resources["table"].scan()["Items"] == before


def test_log_query_put_get_update_cache_and_running_count(resources, monkeypatch):
    now = datetime.fromisoformat("2026-10-16T10:00:00+09:00")
    monkeypatch.setattr(repo, "_now", lambda: now)
    item = repo.put_log_query(
        "2026-10-15",
        "0123456789abcdef",
        {"status": "running", "kind": "lambda", "target": {"function": "fn"}, "requested_by": "a@example.co.jp"},
    )
    assert item["requested_at"] == now.isoformat() and item["ttl"] == int((now + timedelta(days=1)).timestamp())
    saved = resources["table"].get_item(Key={"PK": "LOGS", "SK": "2026-10-15#0123456789abcdef"})["Item"]
    assert saved["status"] == "running" and saved["query_id"] == "0123456789abcdef" and saved["date"] == "2026-10-15"
    fetched = repo.get_log_query("2026-10-15", "0123456789abcdef")
    assert fetched["status"] == "running" and fetched["target"] == {"function": "fn"}
    assert repo.count_running_log_queries("a@example.co.jp") == 1
    assert repo.count_running_log_queries("b@example.co.jp") == 0
    assert repo.update_log_query(
        "2026-10-15",
        "0123456789abcdef",
        {"status": "succeeded", "result": {"rows": [{"count": 1.5}], "truncated": False}},
    )
    fetched = repo.get_log_query("2026-10-15", "0123456789abcdef")
    assert fetched["status"] == "succeeded" and fetched["result"]["rows"] == [{"count": 1.5}]
    assert fetched["ttl"] == item["ttl"] and fetched["requested_at"] == item["requested_at"]
    assert repo.count_running_log_queries("a@example.co.jp") == 0
    assert not repo.update_log_query("2026-10-15", "ffffffffffffffff", {"status": "failed"})
    assert repo.get_log_query("2026-10-15", "ffffffffffffffff") is None
    assert not [i for i in resources["table"].scan()["Items"] if i["SK"] == "2026-10-15#ffffffffffffffff"]


def test_log_query_running_expires_after_ten_minutes_and_ttl(resources, monkeypatch):
    now = datetime.fromisoformat("2026-10-16T10:00:00+09:00")
    monkeypatch.setattr(repo, "_now", lambda: now)
    for index in range(3):
        repo.put_log_query("2026-10-15", f"{index:016x}", {"status": "running", "requested_by": "a@example.co.jp"})
    assert repo.count_running_log_queries("a@example.co.jp") == 3
    later = now + timedelta(minutes=10)
    monkeypatch.setattr(repo, "_now", lambda: later)
    assert repo.count_running_log_queries("a@example.co.jp") == 0
    expired = repo.get_log_query("2026-10-15", "0000000000000000")
    assert (
        expired["status"] == "failed" and expired["error"] == "TIMEOUT" and expired["finished_at"] == later.isoformat()
    )
    assert repo.count_running_log_queries("a@example.co.jp") == 0
    monkeypatch.setattr(repo, "_now", lambda: now + timedelta(days=1))
    assert repo.get_log_query("2026-10-15", "0000000000000001") is None


def test_log_query_update_reraises_other_errors_and_count_paginates(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        def update_item(self, **params):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")

        def query(self, **params):
            self.calls.append(params)
            if "ExclusiveStartKey" in params:
                return {"Count": 1}
            return {"Count": 2, "LastEvaluatedKey": {"PK": {"S": "LOGS"}, "SK": {"S": "x"}}}

    client = Client()
    monkeypatch.setattr(repo, "_client", lambda: client)
    monkeypatch.setenv("TABLE_NAME", "t")
    with pytest.raises(ClientError):
        repo.update_log_query("2026-10-15", "0123456789abcdef", {"status": "failed"})
    assert repo.count_running_log_queries("a@example.co.jp") == 3
    assert len(client.calls) == 2 and client.calls[1]["ExclusiveStartKey"] == {"PK": {"S": "LOGS"}, "SK": {"S": "x"}}
    assert client.calls[0]["Select"] == "COUNT" and client.calls[0]["ConsistentRead"] is True
