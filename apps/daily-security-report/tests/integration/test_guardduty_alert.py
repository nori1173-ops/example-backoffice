import json
from unittest.mock import Mock

import pytest
from daily_security_report import repo
from daily_security_report_batch.handlers import guardduty_alert


def event(severity=8, region="ap-northeast-1"):
    return {
        "source": "aws.guardduty",
        "detail-type": "GuardDuty Finding",
        "region": region,
        "detail": {
            "id": "finding-test",
            "type": "Test:Finding",
            "title": "Test finding",
            "severity": severity,
            "region": region,
            "createdAt": "2026-10-14T01:00:00Z",
            "resource": {"instanceDetails": {"instanceId": "i-test"}},
        },
    }


def configure(**patch):
    current = repo.get_settings()
    return repo.put_settings({**current, **patch}, current["version"], "test", "test@example.co.jp")


@pytest.mark.parametrize("mail,slack", [(False, False), (True, False), (False, True), (True, True)])
def test_toggles_dedup_and_disabled_guardduty_section(resources, mail, slack):
    configure(
        alert_enabled=True,
        mail_enabled=mail,
        slack_enabled=slack,
        slack_channel_id="D000000001",
        slack_mention="<@W000000001>",
        sections={"guardduty": False},
    )
    result = guardduty_alert.handler(event(), None)
    messages = resources["sqs"].receive_message(QueueUrl=resources["queue"], MaxNumberOfMessages=10).get("Messages", [])
    if not mail and not slack:
        assert result["reason"] == "NO_DESTINATIONS" and not messages
        assert not [i for i in resources["table"].scan()["Items"] if i["PK"] == "ALERT"]
        return
    message = json.loads(messages[0]["Body"])
    assert message["channels"] == (["email"] if mail else []) + (["slack"] if slack else [])
    assert ("email" in message) == mail and ("slack" in message) == slack
    if mail:
        assert message["email"]["subject"] == "【検証環境】[セキュリティ即時] High 8.0 Test finding"
        assert "<@W" not in message["email"]["text"]
    if slack:
        assert message["slack"]["channel"] == "D000000001"
        assert message["slack"]["text"].startswith("<@W000000001> 【検証環境】")
        assert len(message["slack"]["blocks"]) == 3
    assert guardduty_alert.handler(event(), None)["reason"] == "ALREADY_NOTIFIED"
    assert repo.alerted_findings(["ap-northeast-1#finding-test"]) == {"ap-northeast-1#finding-test"}


@pytest.mark.parametrize(
    "finding,reason", [(event(6.9), "NOT_HIGH"), (event(8, "us-east-1"), "NOT_TOKYO"), (event(), "ALERT_DISABLED")]
)
def test_suppressed_events(resources, finding, reason):
    assert guardduty_alert.handler(finding, None)["reason"] == reason
    assert not [i for i in resources["table"].scan()["Items"] if i["PK"] == "ALERT"]


def test_send_failure_deletes_claim_for_retry(resources, monkeypatch):
    configure(alert_enabled=True, mail_enabled=True)
    original = guardduty_alert.notify.send
    monkeypatch.setattr(guardduty_alert.notify, "send", Mock(side_effect=RuntimeError("queue failed")))
    with pytest.raises(RuntimeError):
        guardduty_alert.handler(event(), None)
    assert not [i for i in resources["table"].scan()["Items"] if i["PK"] == "ALERT"]
    monkeypatch.setattr(guardduty_alert.notify, "send", original)
    assert guardduty_alert.handler(event(), None)["status"] == "sent"


def test_finish_alert_failure_logs_warning_without_resending(resources, monkeypatch, caplog):
    configure(alert_enabled=True, mail_enabled=True)
    monkeypatch.setattr(repo, "finish_alert", Mock(side_effect=ValueError("alert write failed")))
    with pytest.raises(ValueError, match="alert write failed"):
        guardduty_alert.handler(event(), None)
    assert guardduty_alert.handler(event(), None)["reason"] == "ALREADY_NOTIFIED"
    assert any(
        r.levelname == "WARNING" and "alert write failed" in r.message and "Traceback" in r.message
        for r in caplog.records
    )


def test_delete_alert_failure_logs_warning_and_preserves_send_error(resources, monkeypatch, caplog):
    configure(alert_enabled=True, mail_enabled=True)
    monkeypatch.setattr(guardduty_alert.notify, "send", Mock(side_effect=RuntimeError("send failed")))
    monkeypatch.setattr(repo, "delete_alert", Mock(side_effect=ValueError("delete failed")))
    with pytest.raises(RuntimeError, match="send failed"):
        guardduty_alert.handler(event(), None)
    assert any(r.levelname == "WARNING" and "delete failed" in r.message for r in caplog.records)
