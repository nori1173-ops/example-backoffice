from copy import deepcopy
from unittest.mock import Mock

import pytest
from daily_security_report import actors
from daily_security_report.settings import default_settings

DAY = "2026-10-16"
USER = "arn:aws:iam::000000000000:user/example"
ROLE = "arn:aws:iam::000000000000:role/example"


def observed(**changes):
    return {
        "actor": USER,
        "kind": "user",
        "ip": "203.0.113.1",
        "country": "JP",
        "foreign": False,
        "event": "ListBuckets",
        "count": 3,
        "first_at": DAY + "T09:00:00+09:00",
        "last_at": DAY + "T10:00:00+09:00",
        **changes,
    }


def evaluate(*, old=None, snapshot=None, initialized=True, rows=None, settings=None):
    return actors.evaluate(
        rows or [observed()],
        DAY,
        settings or default_settings(),
        old or {},
        snapshot or {"status": "ok", "actors": {}},
        initialized=initialized,
    )


@pytest.mark.parametrize(
    "identity,key,kind",
    [
        ({"type": "IAMUser", "arn": USER}, USER, "user"),
        (
            {
                "type": "AssumedRole",
                "arn": "arn:aws:sts::000000000000:assumed-role/example/session",
                "sessionContext": {"sessionIssuer": {"arn": ROLE}},
            },
            ROLE,
            "role",
        ),
        ({"type": "Root"}, "root", "root"),
        ({"type": "IAMUser", "arn": USER, "accountId": "111111111111"}, "account:111111111111", "external_account"),
        ({"type": "AWSService", "accountId": "111111111111"}, None, None),
    ],
)
def test_actor_keys(identity, key, kind):
    assert actors.actor_identity({"userIdentity": identity}, "000000000000") == ((key, kind) if key else None)


@pytest.mark.parametrize("last,expected", [("2026-09-16", False), ("2026-09-15", True)])
def test_dormant_strict_boundary(last, expected):
    result, findings, updates = evaluate(old={USER: {"last_seen": last, "seen_days": 2}})
    assert bool(result["dormant"]) is expected
    assert bool(findings) is expected
    assert updates[USER]["last_seen"] == DAY and updates[USER]["seen_days"] == 3


def test_initialization_suppresses_dormant_and_seeds_iam():
    snapshot = {
        "status": "ok",
        "actors": {
            USER: {"kind": "user", "last_seen": "2026-01-01"},
            ROLE: {"kind": "role", "last_seen": "2026-02-01"},
        },
    }
    result, findings, updates = evaluate(snapshot=snapshot, initialized=False)
    assert result["status"] == "initializing" and not result["dormant"] and not findings
    assert updates[ROLE]["source"] == "iam" and updates[ROLE]["seen_days"] == 0
    assert updates[USER]["source"] == "cloudtrail"


@pytest.mark.parametrize("last", [None, DAY])
def test_iam_previous_unknown_is_not_new_or_dormant(last):
    result, findings, _ = evaluate(snapshot={"status": "ok", "actors": {USER: {"last_seen": last}}})
    assert result["new"] == result["dormant"] == [] and not findings


def test_new_and_foreign_and_external_including_office():
    result, findings, _ = evaluate(rows=[observed(actor="account:111111111111", kind="external_account", foreign=True)])
    assert len(result["new"]) == len(result["external"]) == 1
    assert {f["code"]: f["level"] for f in findings} == {"NEW_ACTOR": "critical", "EXTERNAL_ACCOUNT": "warning"}
    assert evaluate()[1][0]["level"] == "warning"
    assert evaluate(old={USER: {"last_seen": None}})[0]["new"]


def test_unavailable_does_not_guess_new_and_keeps_known_detection():
    snapshot = {"status": "unavailable", "note": "AccessDenied", "actors": {}}
    result, findings, updates = evaluate(snapshot=snapshot)
    assert result["status"] == "unavailable" and not findings and USER in updates
    assert evaluate(old={USER: {"last_seen": "2026-01-01"}}, snapshot=snapshot)[0]["dormant"]


def test_same_day_and_historical_replay_do_not_increase_seen_or_rewind_last():
    old = {
        USER: {
            "last_seen": DAY,
            "first_seen": "2026-10-01",
            "seen_days": 2,
            "last_ip": "203.0.113.9",
            "last_event": "latest",
        }
    }
    before = deepcopy(old)
    assert evaluate(old=old)[2][USER]["seen_days"] == 2
    old[USER]["last_seen"] = "2026-10-20"
    update = evaluate(old=old)[2][USER]
    assert update["last_seen"] == "2026-10-20" and update["last_ip"] == "203.0.113.9"
    assert before[USER]["seen_days"] == 2


def test_latest_observation_selected_and_counts_aggregated():
    rows = [observed(), observed(ip="10.0.0.1", event="GetObject", last_at=DAY + "T11:00:00+09:00")]
    _, _, updates = evaluate(rows=rows)
    assert updates[USER]["last_ip"] == "10.0.0.1" and updates[USER]["last_event"] == "GetObject"
    assert updates[USER]["seen_days"] == 1


def test_inventory_threshold_unused_creation_mfa_and_unknown():
    settings = default_settings() | {"inventory_weekday": 4}
    user = {
        "kind": "user",
        "name": "example",
        "last_seen": "2026-07-18",
        "created_at": "2026-01-01",
        "password_enabled": True,
        "mfa_active": False,
        "keys": [
            {"slot": 1, "active": True, "last_used": None, "created_at": "2026-07-18"},
            {"slot": 2, "active": False, "last_used": "2026-01-01"},
        ],
    }
    result, _, _ = evaluate(settings=settings, snapshot={"status": "ok", "actors": {USER: user}})
    assert result["inventory"]["counts"] == {"users": 1, "keys": 1, "mfa_missing": 1}
    assert result["inventory"]["keys"][0]["slot"] == 1
    settings["inventory_weekday"] = None
    assert "inventory" not in evaluate(settings=settings)[0]


def test_update_uses_saved_actors_for_rerun_without_iam_or_writes():
    storage, iam = Mock(), Mock()
    saved = {"status": "ok", "dormant": [observed()], "new": [], "external": []}
    storage.batch_get_actors.return_value = {}
    result = actors.update([], DAY, default_settings(), iam, saved=saved, storage=storage)
    assert result[0] == saved
    iam.generate_credential_report.assert_not_called()
    storage.batch_write_actors.assert_not_called()


def test_historical_observation_day_counts_once_even_after_later_day():
    old = {
        USER: {
            "source": "cloudtrail",
            "last_seen": "2026-10-20",
            "first_seen": "2026-10-20",
            "seen_days": 1,
            "observed_dates": ["2026-10-20"],
            "last_ip": "203.0.113.9",
            "last_event": "later",
        }
    }
    update = evaluate(old=old)[2][USER]
    assert update["seen_days"] == 2 and update["first_seen"] == DAY
    assert evaluate(old={USER: update})[2][USER]["seen_days"] == 2
    assert update["last_seen"] == "2026-10-20" and update["last_event"] == "later"


def test_actor_lists_bound_and_counts_preserve_more_than_100_rows():
    rows = [observed(actor=f"arn:aws:iam::000000000000:user/example-{i}", foreign=True) for i in range(110)]
    result, findings, _ = evaluate(rows=rows)
    assert len(result["new"]) == 100 and result["truncated"]["new"] == 10
    assert len(findings) == 110 and result["finding_counts"] == {"critical": 110, "warning": 0}


@pytest.mark.parametrize("kind,key", [("user", USER), ("role", ROLE)])
def test_iam_without_last_use_is_not_seeded_as_new_next_day(kind, key):
    snapshot = {"status": "ok", "actors": {key: {"kind": kind, "last_seen": None}}}
    _, _, updates = actors.evaluate([], "2026-10-15", default_settings(), {}, snapshot, initialized=False)
    result, findings, updates = evaluate(
        old=updates, snapshot=snapshot, rows=[observed(actor=key, kind=kind)], initialized=True
    )
    assert not result["new"] and not result["dormant"] and not findings
    assert updates[key]["source"] == "cloudtrail" and updates[key]["seen_days"] == 1


def test_existing_iam_seed_without_last_use_is_previous_unknown():
    result, findings, _ = evaluate(old={USER: {"last_seen": None, "source": "iam", "seen_days": 0}})
    assert not result["new"] and not findings


@pytest.mark.parametrize("window", [7, 30, 365])
def test_observed_dates_bounded_relative_to_latest_day_even_for_historical_replay(window):
    from datetime import date, timedelta

    latest = date.fromisoformat(DAY)
    dates = [(latest - timedelta(days=i)).isoformat() for i in range(400)]
    old = {USER: {"last_seen": DAY, "seen_days": 400, "observed_dates": dates}}
    settings = default_settings() | {"dormant_days": window}
    updated = evaluate(old=old, settings=settings)[2][USER]
    expected = sorted(dates[:window])
    assert updated["observed_dates"] == expected and updated["seen_days"] == 400
    _, _, replayed = actors.evaluate(
        [observed()], "2025-01-01", settings, {USER: updated}, {"status": "ok", "actors": {}}, initialized=True
    )
    assert replayed[USER]["observed_dates"] == expected and replayed[USER]["seen_days"] == 400
