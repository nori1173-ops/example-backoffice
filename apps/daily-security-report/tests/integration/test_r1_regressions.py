from datetime import date, timedelta
from decimal import Decimal

import pytest
from daily_security_report import baseline, exclusions, ip_country, report, settings


def day(value=10, offset=0):
    return {
        "date": (date(2026, 10, 14) - timedelta(days=offset)).isoformat(),
        "sources": {s: {"status": "ok"} for s in settings.SECTIONS},
        "counters": {"waf.blocked": value},
        "red": [],
    }


def test_decimal_settings_and_summaries_are_safe():
    config = {k: Decimal(str(v)) if type(v) is int else v for k, v in settings.default_settings().items()}
    config["ratio"] = Decimal("3.2")
    normalized = settings.normalize_settings(config)
    assert type(normalized["baseline_days"]) is int
    assert type(normalized["ratio"]) is float
    updated = settings.update_settings(config, {"version": 0, "slack_mention": "<@W012345678>"})
    result = baseline.compare(day(Decimal("40.5")), [day(Decimal("10.1"), i) for i in range(1, 8)], config)
    assert result["red_count"] == 1
    assert report.build_report(result, updated)["email"]["subject"].startswith("[Warning]")
    assert report.build_report(day(Decimal("10.1")), config)["slack"]["blocks"]


@pytest.mark.parametrize("field,value", [("top_n", True), ("baseline_days", Decimal("1.5")), ("version", True)])
def test_numeric_validation_rejects_bool_and_fraction(field, value):
    with pytest.raises(settings.SettingsValidationError):
        settings.update_settings({}, {"version": 0, field: value})


@pytest.mark.parametrize("principal", ["arn:aws:iam::000000000000:role/TestRole", "TestRole"])
def test_assumed_role_can_be_excluded_by_issuer(principal):
    record = {
        "userIdentity": {
            "type": "AssumedRole",
            "arn": "arn:aws:sts::000000000000:assumed-role/TestRole/session",
            "sessionContext": {
                "sessionIssuer": {"arn": "arn:aws:iam::000000000000:role/TestRole", "userName": "TestRole"}
            },
        }
    }
    rule = {"kind": "principal", "value": principal, "scope": ["all"], "expires_at": ""}
    assert exclusions.is_excluded(record, [rule], "cloudtrail", "2026-10-14")
    assert not exclusions.is_excluded(record, [rule], "guardduty", "2026-10-14")


def test_office_ip_expires_and_date_is_required():
    rules = [{"kind": "ip", "value": "203.0.113.5", "category": "office", "scope": ["all"], "expires_at": "2026-10-13"}]
    assert not ip_country.classify_ip("203.0.113.5", rules, on_date="2026-10-14")["is_office"]
    with pytest.raises(TypeError):
        ip_country.classify_ip("203.0.113.5", rules)


@pytest.mark.parametrize("kind,scope,category", [("principal", ["all"], "office"), ("instance", ["cloudtrail"], "")])
def test_invalid_exclusion_combinations_have_line_number(kind, scope, category):
    with pytest.raises(settings.SettingsValidationError, match="1行目"):
        settings.update_settings(
            {},
            {
                "version": 0,
                "exclusions": [
                    {
                        "kind": kind,
                        "value": "test",
                        "scope": scope,
                        "category": category,
                        "reason": "test",
                        "expires_at": "",
                    }
                ],
            },
        )


def test_mail_domains_follow_shared_configuration(monkeypatch):
    monkeypatch.setenv("ALLOWED_MAIL_DOMAINS", "example.test")
    result = settings.update_settings({}, {"version": 0, "mail_to": ["test@example.test"]})
    assert result["mail_to"] == ["test@example.test"]
    with pytest.raises(settings.SettingsValidationError) as exc:
        settings.update_settings({}, {"version": 0})
    assert exc.value.code == "MAIL_DOMAIN_NOT_ALLOWED"


def test_zero_instance_baseline_is_not_red():
    today = day()
    today["instances"] = {"ec2": [{"id": "i-test", "cpu_avg": 4, "net_out": 4}]}
    history = [day(offset=i) for i in range(1, 8)]
    for item in history:
        item["instances"] = {"ec2": [{"id": "i-test", "cpu_avg": 0, "net_out": 0}]}
    result = baseline.compare(today, history, {})
    assert not result["red"]
    assert result["comparisons"]["instances.ec2.i-test.cpu_avg"]["label"] == "基準なし"


def test_ok_notification_is_compact_and_failed_section_gets_mention():
    current = day()
    current["sources"]["guardduty"]["severity"] = {"continuing": {"low": 2}}
    current["counters"].update({"guardduty.continuing": 2, "cloudtrail.console_login.success": 3})
    config = {"slack_mention": "<!here>", "slack_mention_level": "warning"}
    message = report.build_report(current, config)
    assert "異常なし" in message["slack"]["text"]
    assert "GuardDuty:" not in message["slack"]["text"] and "<!here>" not in str(message)
    current["sources"]["metrics"]["status"] = "failed"
    message = report.build_report(current, config)
    assert message["slack"]["text"].startswith("<!here>")
    assert "未集計" in message["slack"]["text"] and "異常なし" not in message["slack"]["text"]
