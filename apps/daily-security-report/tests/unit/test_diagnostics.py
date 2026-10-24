import boto3
import pytest
from botocore.stub import Stubber
from daily_security_report import (
    cloudtrail_summary,
    guardduty_summary,
    insights_queries,
    instances_summary,
    metrics_summary,
)
from daily_security_report.diagnostics import _redact, log_failure

DAY = "2026-10-14"


class Broken:
    def __getattr__(self, name):
        def call(*args, **kwargs):
            raise RuntimeError(f"boom {name}")

        return call


def warnings(caplog):
    return [record.message for record in caplog.records if record.levelname == "WARNING"]


def test_log_failure_keeps_type_message_traceback_and_redacts_secrets(monkeypatch, caplog):
    monkeypatch.setenv("DEMO_API_TOKEN", "demo-token-value")
    try:
        raise ValueError("call failed demo-token-value Authorization: Bearer demo.jwt.value password=demo-pass")
    except ValueError as error:
        log_failure("demo.operation", error, date=DAY)
    [message] = warnings(caplog)
    assert all(text in message for text in ("demo.operation", "ValueError", "call failed", "Traceback", DAY))
    assert not any(secret in message for secret in ("demo-token-value", "demo.jwt.value", "demo-pass"))


def test_guardduty_access_denied_is_logged_in_full_but_saved_as_code(caplog):
    client = boto3.client("guardduty", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        stub.add_client_error("list_findings", "AccessDeniedException", "not authorized on detector/demo/findings")
        result = guardduty_summary.collect(DAY, {"ap-northeast-1": client}, detectors={"ap-northeast-1": "demo"})
    assert result == {"status": "failed", "error": "AccessDeniedException", "regions": {"ap-northeast-1": "failed"}}
    [message] = warnings(caplog)
    assert "guardduty_summary.collect" in message and '"exception_type": "AccessDeniedException"' in message
    assert "detector/demo/findings" in message and "Traceback" in message


@pytest.mark.parametrize(
    "operation,collect,error",
    [
        (
            "cloudtrail_summary.collect",
            lambda: cloudtrail_summary.collect(DAY, Broken(), bucket="b", prefix="p/"),
            "RuntimeError",
        ),
        ("metrics_summary.collect", lambda: metrics_summary.collect(DAY, {"ap-northeast-1": Broken()}), "RuntimeError"),
        (
            "instances_summary.collect",
            lambda: instances_summary.collect(DAY, Broken(), Broken(), Broken()),
            "RuntimeError",
        ),
        (
            "insights_queries.collect",
            lambda: insights_queries.collect(DAY, Broken(), log_groups={"alarmhuba": "n", "gps": "g"}),
            "Q1 RuntimeError boom start_query",
        ),
    ],
)
def test_each_collect_logs_warning_with_details(caplog, operation, collect, error):
    result = collect()
    assert result["status"] == "failed" and result["error"] == error
    [message] = warnings(caplog)
    assert operation in message and "RuntimeError" in message and "boom" in message and "Traceback" in message


@pytest.mark.parametrize(
    "text,hidden,kept",
    [
        ('{"cookies": ["sid=abc123; Path=/", "theme=dark"], "rawPath": "/x"}', ["abc123", "theme=dark"], ["rawPath"]),
        ('"set-cookie": "sid=abc123; HttpOnly"', ["abc123"], ["set-cookie"]),
        (
            '"headers": {"x-origin-verify": "verify-secret-value", "host": "example.test"}',
            ["verify-secret-value"],
            ["host"],
        ),
        ("requested by user.name+tag@example.co.jp at 10:00", ["user.name+tag@example.co.jp"], ["requested by", "10:00"]),
        ('{"Cookie": "a=b", "cookie": \'c=d\'}', ["a=b", "c=d"], []),
        ("arn:aws:iam::123456789012:user/example", [], ["arn:aws:iam::123456789012:user/example"]),
    ],
)
def test_redact_hides_cookie_arrays_origin_verify_and_emails(text, hidden, kept):
    result = _redact(text)
    assert all(value not in result for value in hidden)
    assert all(value in result for value in kept)
    assert "[REDACTED]" in result or not hidden
