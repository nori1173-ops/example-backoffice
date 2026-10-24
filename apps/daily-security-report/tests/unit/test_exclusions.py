import pytest
from daily_security_report.exclusions import instance_is_excluded, is_excluded, is_office


def rule(kind="ip", value="203.0.113.0/24", **kw):
    return {"kind": kind, "value": value, "scope": ["all"], "category": "", "expires_at": "", **kw}


@pytest.mark.parametrize(
    "record,exclusion",
    [
        ({"sourceIPAddress": "203.0.113.5"}, rule()),
        ({"ip": "2001:db8::5"}, rule(value="2001:db8::/32")),
        ({"ip": "203.0.113.5"}, rule(value="203.0.113.5")),
        (
            {"userIdentity": {"arn": "arn:aws:iam::000000000000:user/test-user", "userName": "test-user"}},
            rule("principal", "test-user"),
        ),
        (
            {"principal": "arn:aws:iam::000000000000:user/test-user"},
            rule("principal", "arn:aws:iam::000000000000:user/test-user"),
        ),
        (
            {"eventSource": "ec2.amazonaws.com", "eventName": "RunInstances"},
            rule("event", "ec2.amazonaws.com:RunInstances"),
        ),
    ],
)
def test_matching(record, exclusion):
    assert is_excluded(record, [exclusion], "cloudtrail", "2026-10-14")


@pytest.mark.parametrize(
    "exclusion", [rule(scope=["insights"]), rule(expires_at="2026-10-13"), rule(value="198.51.100.0/24")]
)
def test_not_applicable(exclusion):
    assert not is_excluded({"ip": "203.0.113.5"}, [exclusion], "cloudtrail", "2026-10-14")


def test_expiration_day_is_inclusive_and_root_never_excluded():
    assert is_excluded({"ip": "203.0.113.5"}, [rule(expires_at="2026-10-14")], "cloudtrail", "2026-10-14")
    root = {"sourceIPAddress": "203.0.113.5", "userIdentity": {"type": "Root"}}
    assert not is_excluded(root, [rule()], "cloudtrail", "2026-10-14")


def test_office_and_instance_keep_aggregate_values():
    office = [rule(category="office")]
    assert is_office("203.0.113.5", office, "insights", "2026-10-14")
    assert not is_excluded({"ip": "203.0.113.5"}, office, "insights", "2026-10-14")
    items = [rule("instance", "i-example", scope=["instances"])]
    assert instance_is_excluded({"id": "i-example", "name": "test"}, items, "2026-10-14")
    assert not is_excluded({"id": "i-example"}, items, "instances", "2026-10-14")
    assert not instance_is_excluded({"id": "i-other"}, items, "2026-10-14")


def test_service_source_is_not_ip():
    assert not is_excluded({"ip": "AWS Internal"}, [rule()], "cloudtrail", "2026-10-14")
