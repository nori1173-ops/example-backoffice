"""`GET /api/daily-security-report/health` の一気通貫テスト。"""

import json

import pytest
from daily_security_report import repo, slack_directory
from daily_security_report_api import app as api
from daily_security_report_api.app import handler


def test_health_returns_ok_envelope(http_event):
    result = handler(http_event("/health"), None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["ok"] is True
    assert body["data"] == {"status": "ok"}
    assert body["error"] is None


def test_unknown_route_returns_404_envelope(http_event):
    """例外もエンベロープで返る（`response.install_error_handlers`）。"""
    result = handler(http_event("/unknown"), None)

    assert result["statusCode"] == 404
    body = json.loads(result["body"])
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"


def test_request_without_origin_verify_header_is_forbidden(http_event, monkeypatch):
    monkeypatch.delenv("ORIGIN_VERIFY_SKIP")
    event = http_event("/health")
    event["headers"].pop("x-origin-verify")

    result = handler(event, None)

    assert result["statusCode"] == 403


def call(http_event, path, method="GET", patch=None, groups=None, query=None):
    event = http_event(path, method, groups=groups, body=json.dumps(patch) if patch is not None else None)
    if query:
        event["queryStringParameters"] = query
    result = api.handler(event, None)
    return result["statusCode"], json.loads(result["body"])


def test_get_settings_seed_and_slack_mode(resources, http_event):
    resources["ssm"].put_parameter(Name="/example-backoffice/dev/shared/slack/mode", Value="webhook", Type="String")
    status, body = call(http_event, "/settings")
    assert status == 200 and body["data"]["version"] == 0
    assert body["data"]["slack_mode"] == "webhook"
    assert body["data"]["target_account"] == "000000000000"
    assert len(resources["table"].scan()["Items"]) == 1
    assert call(http_event, "/settings")[1]["data"]["version"] == 0


def test_settings_without_mode_and_partial_update_audit(resources, http_event):
    assert call(http_event, "/settings")[1]["data"]["slack_mode"] is None
    status, body = call(
        http_event, "/settings", "PUT", {"version": 0, "ratio": 3.5, "sections": {"insights": False}}, ["admin"]
    )
    assert status == 200 and body["data"]["version"] == 1
    assert body["data"]["ratio"] == 3.5 and body["data"]["sections"]["metrics"]
    assert body["data"]["updated_by_email"] == "user@example.co.jp"
    assert call(http_event, "/settings", "PUT", {"version": 0}, ["admin"])[0] == 409
    assert len(resources["table"].scan()["Items"]) == 2


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"version": 0, "ratio": True}, "VALIDATION_ERROR"),
        ({"version": 0, "mail_to": ["test@example.test"]}, "MAIL_DOMAIN_NOT_ALLOWED"),
        (
            {
                "version": 0,
                "sections": dict.fromkeys(["guardduty", "cloudtrail", "metrics", "insights", "instances", "s3"], False),
            },
            "VALIDATION_ERROR",
        ),
        (
            {
                "version": 0,
                "exclusions": [
                    {
                        "kind": "instance",
                        "value": "Test",
                        "scope": ["cloudtrail"],
                        "category": "",
                        "reason": "test",
                        "expires_at": "",
                    }
                ],
            },
            "VALIDATION_ERROR",
        ),
        ([], "VALIDATION_ERROR"),
        (None, "VALIDATION_ERROR"),
    ],
)
def test_invalid_settings_never_write_audit(resources, http_event, patch, code):
    status, body = call(http_event, "/settings", "PUT", patch, ["admin"])
    assert status == 400 and body["error"]["code"] == code
    assert not [i for i in resources["table"].scan()["Items"] if i["PK"] == "AUDIT"]


@pytest.mark.parametrize(
    "path,method",
    [
        ("/health", "GET"),
        ("/settings", "GET"),
        ("/settings", "PUT"),
        ("/days", "GET"),
        ("/slack/directory", "GET"),
        ("/days/2026-10-15/run", "POST"),
        ("/days/2026-10-15/logs", "POST"),
        ("/days/2026-10-15/logs/0123456789abcdef", "GET"),
    ],
)
def test_all_routes_reject_access_tokens_and_missing_claims(resources, http_event, path, method):
    for token_use in ("access", None):
        event = http_event(path, method, groups=["admin"], body="{}")
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        if token_use:
            claims["token_use"] = token_use
        else:
            event["requestContext"].pop("authorizer")
        assert api.handler(event, None)["statusCode"] == 401


@pytest.mark.parametrize(
    "path,method",
    [
        ("/settings", "PUT"),
        ("/slack/directory", "GET"),
        ("/days/2026-10-15/logs", "POST"),
        ("/days/2026-10-15/logs/0123456789abcdef", "GET"),
    ],
)
def test_admin_required(resources, http_event, path, method):
    assert call(http_event, path, method, {"version": 0})[0] == 403
    assert not resources["table"].scan()["Items"]


@pytest.mark.parametrize("limit", ["0", "91", "abc", "1.5", ""])
def test_days_invalid_limit(resources, http_event, limit):
    status, body = call(http_event, "/days", query={"limit": limit})
    assert status == 400 and body["error"]["code"] == "VALIDATION_ERROR"


def test_days_are_array_newest_first_and_private_fields_omitted(resources, http_event):
    for day in ("2026-10-12", "2026-10-14", "2026-10-13"):
        repo.acquire_daily(day, trigger="manual")
        repo.finish_daily(day, 1, summary={"date": day, "red_count": 4})
    status, body = call(http_event, "/days", query={"limit": "2"})
    assert status == 200 and [r["date"] for r in body["data"]] == ["2026-10-14", "2026-10-13"]
    assert all("summary" not in r and "request_history" not in r for r in body["data"])


def test_slack_directory_cache_refresh_and_filtered_users(resources, http_event, monkeypatch):
    from unittest.mock import Mock

    resources["ssm"].put_parameter(
        Name="/example-backoffice/dev/shared/slack/bot-token", Value="test-fixture", Type="SecureString"
    )
    pages = [
        {"ok": True, "channels": [{"id": "C000000001", "name": "test"}], "response_metadata": {"next_cursor": "next"}},
        {"ok": True, "channels": [{"id": "G000000002", "name": "private", "is_private": True, "is_member": True}]},
        {
            "ok": True,
            "members": [
                {"id": "W000000001", "name": "test", "profile": {"display_name": "Test"}},
                {"id": "U000000002", "deleted": True},
                {"id": "U000000003", "is_bot": True},
                {"id": "USLACKBOT"},
            ],
        },
    ]
    get = Mock(side_effect=[Mock(json=Mock(return_value=p)) for p in pages * 2])
    monkeypatch.setattr(slack_directory.requests, "get", get)
    status, body = call(http_event, "/slack/directory", groups=["admin"])
    assert status == 200 and len(body["data"]["channels"]) == 2
    assert body["data"]["users"] == [{"id": "W000000001", "handle": "test", "real_name": "", "display_name": "Test"}]
    assert call(http_event, "/slack/directory", groups=["admin"])[1] == body
    assert get.call_count == 3
    call(http_event, "/slack/directory", groups=["admin"], query={"refresh": "1"})
    assert get.call_count == 6


def test_slack_directory_missing_token(resources, http_event):
    status, body = call(http_event, "/slack/directory", groups=["admin"])
    assert status == 503 and body["error"]["code"] == "SLACK_NOT_CONFIGURED"


@pytest.mark.parametrize(
    "kind,code", [("api", "SLACK_API_ERROR"), ("network", "SLACK_UNAVAILABLE"), ("rate", "SLACK_UNAVAILABLE")]
)
def test_slack_directory_errors_are_not_cached(resources, http_event, monkeypatch, kind, code):
    from unittest.mock import Mock

    import requests

    resources["ssm"].put_parameter(
        Name="/example-backoffice/dev/shared/slack/bot-token", Value="test-fixture", Type="SecureString"
    )
    if kind == "network":
        get = Mock(side_effect=requests.ConnectionError("unavailable"))
    else:
        result = Mock(status_code=429, json=Mock(return_value={"ok": False, "error": "missing_scope"}))
        if kind == "rate":
            result.raise_for_status.side_effect = requests.HTTPError()
        get = Mock(return_value=result)
    monkeypatch.setattr(slack_directory.requests, "get", get)
    for _ in range(2):
        status, body = call(http_event, "/slack/directory", groups=["admin"])
        assert status == 502 and body["error"]["code"] == code
        assert "test-fixture" not in str(body)
    assert get.call_count == 2


def test_malformed_json_is_400(resources, http_event):
    result = api.handler(http_event("/settings", "PUT", groups=["admin"], body="{"), None)
    assert result["statusCode"] == 400
    assert not resources["table"].scan()["Items"]


def test_slack_directory_expires_after_300_seconds(resources, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import Mock

    now = datetime(2026, 10, 15, tzinfo=UTC)
    clock = Mock(return_value=now)
    fetch = Mock(return_value={"channels": [], "users": []})
    monkeypatch.setattr(slack_directory, "_now", clock)
    monkeypatch.setattr(slack_directory, "_fetch", fetch)
    slack_directory.get_directory()
    clock.return_value = now + timedelta(seconds=299)
    slack_directory.get_directory()
    assert fetch.call_count == 1
    clock.return_value = now + timedelta(seconds=300)
    slack_directory.get_directory()
    assert fetch.call_count == 2


@pytest.mark.parametrize("alb", ["demo-alb", {"name": "demo-alb"}])
def test_settings_reject_alb_without_dimension_id(resources, http_event, alb):
    call(http_event, "/settings")
    status, body = call(http_event, "/settings", "PUT", {"version": 0, "metric_targets": {"alb": [alb]}}, ["admin"])
    assert status == 400 and body["error"]["code"] == "VALIDATION_ERROR"
    assert repo.get_settings()["version"] == 0


def test_days_returns_notification_counts_and_timestamp(resources, http_event):
    from datetime import datetime

    at = datetime.fromisoformat("2026-10-15T07:01:00+09:00")
    repo.acquire_daily("2026-10-14", trigger="manual", now=at)
    repo.finish_daily(
        "2026-10-14", 1, summary={"red_count": 7}, message_id="message", channels=["email", "slack"], now=at
    )
    status, body = call(http_event, "/days")
    assert status == 200
    row = body["data"][0]
    assert row["finding_counts"] == {"critical": 0, "warning": 7} and row["notify_channels"] == ["email", "slack"]
    assert row["sent_count"] == 1 and row["notified_at"] == at.isoformat()
    assert "summary" not in row and "request_history" not in row


@pytest.mark.parametrize(
    "date,status,code",
    [
        ("2026-10-16", 200, None),
        ("2026-10-15", 404, "NOT_FOUND"),
        ("bad", 400, "VALIDATION_ERROR"),
        ("2026-1-01", 400, "VALIDATION_ERROR"),
        ("2026-02-30", 400, "VALIDATION_ERROR"),
    ],
)
def test_daily_detail_authenticated_non_admin(resources, http_event, date, status, code):
    from daily_security_report import levels

    summary = levels.apply_rules({"date": "2026-10-16", "findings": [], "ai": {"status": "skipped"}})
    repo.acquire_daily("2026-10-16", trigger="manual")
    repo.finish_daily("2026-10-16", 1, summary=summary)
    result = api.handler(http_event(f"/days/{date}"), None)
    assert result["statusCode"] == status
    body = json.loads(result["body"])
    if code:
        assert body["error"]["code"] == code
    else:
        assert "request_history" not in body["data"]
        assert body["data"]["summary"]["schema"] == "daily-security-report.daily.v2"
        assert body["data"]["ai"]["status"] == "skipped"


def test_daily_detail_requires_id_token(resources, http_event):
    event = http_event("/days/2026-10-16")
    event["requestContext"]["authorizer"]["jwt"]["claims"]["token_use"] = "access"
    assert api.handler(event, None)["statusCode"] == 401


def test_v2_settings_are_validated_before_storage(resources, http_event):
    for field, value in (("ai_prompt", "x" * 8001), ("dormant_days", True), ("inventory_weekday", 7)):
        result = api.handler(
            http_event("/settings", "PUT", ["admin"], body=json.dumps({"version": 0, field: value})), None
        )
        assert result["statusCode"] == 400
    assert resources["table"].scan()["Items"] == []


@pytest.fixture
def preview_api(resources, monkeypatch):
    from unittest.mock import Mock

    from daily_security_report.settings import SECTIONS

    monkeypatch.setenv("REPORT_FUNCTION_NAME", "report-test")
    client = Mock()
    client.invoke.return_value = {"StatusCode": 202}
    monkeypatch.setattr(api, "_lambda_client", lambda: client)
    repo.acquire_daily("2026-10-14", trigger="manual")
    repo.finish_daily(
        "2026-10-14",
        1,
        summary={
            "schema": "daily-security-report.daily.v2",
            "date": "2026-10-14",
            "findings": [],
            "rule_levels": dict.fromkeys(SECTIONS, "ok"),
            "sources": {s: {"status": "ok"} for s in SECTIONS},
        },
    )
    return client


def test_preview_202_saves_request_snapshot_and_invokes_async(preview_api, http_event, resources):
    status, body = call(
        http_event,
        "/ai/preview",
        "POST",
        {"date": "2026-10-14", "prompt": "", "model_family": "haiku", "model_pin": ""},
        ["admin"],
    )
    assert status == 202
    preview_id = body["data"]["preview_id"]
    request = preview_api.invoke.call_args.kwargs
    assert request["FunctionName"] == "report-test" and request["InvocationType"] == "Event"
    assert json.loads(request["Payload"])["preview_id"] == preview_id
    saved = repo.get_preview(preview_id)
    assert saved["prompt"] == "" and saved["model_family"] == "haiku"
    assert saved["requested_by"] == "00000000-0000-0000-0000-000000000000"
    assert call(http_event, f"/ai/preview/{preview_id}", groups=["admin"])[1]["data"] == saved
    assert not [i for i in resources["table"].scan()["Items"] if i["PK"] in {"AI_STATE", "SETTINGS", "AUDIT"}]


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"date": "2026-02-30"},
        {"date": "20261014"},
        {"date": 123},
        {"date": "2026-10-14", "prompt": "a" * 8001},
        {"date": "2026-10-14", "prompt": None},
        {"date": "2026-10-14", "model_family": "mythos"},
        {"date": "2026-10-14", "model_family": []},
        {"date": "2026-10-14", "model_pin": "arbitrary"},
        {"date": "2026-10-14", "extra": True},
    ],
)
def test_preview_validation_errors_do_not_create(preview_api, http_event, body):
    status, result = call(http_event, "/ai/preview", "POST", body, ["admin"])
    assert status == 400 and result["error"]["code"] == "VALIDATION_ERROR"
    preview_api.invoke.assert_not_called()


def test_preview_malformed_body(preview_api, http_event):
    assert api.handler(http_event("/ai/preview", "POST", groups=["admin"], body="{"), None)["statusCode"] == 400


def test_preview_missing_daily_and_missing_summary(preview_api, http_event):
    assert call(http_event, "/ai/preview", "POST", {"date": "2026-10-13"}, ["admin"])[0] == 404
    repo.acquire_daily("2026-10-13", trigger="manual")
    assert call(http_event, "/ai/preview", "POST", {"date": "2026-10-13"}, ["admin"])[0] == 409
    assert call(http_event, "/ai/preview/00000000-0000-0000-0000-000000000001", groups=["admin"])[0] == 404
    assert call(http_event, "/ai/preview/limit%232026-10-16", groups=["admin"])[0] == 404


def test_preview_daily_limit_and_invoke_failure(preview_api, http_event):
    preview_api.invoke.side_effect = RuntimeError("unavailable")
    status, body = call(http_event, "/ai/preview", "POST", {"date": "2026-10-14"}, ["admin"])
    assert status == 502 and body["error"]["code"] == "AI_UNAVAILABLE"
    preview_api.invoke.side_effect = None
    for _ in range(19):
        assert call(http_event, "/ai/preview", "POST", {"date": "2026-10-14"}, ["admin"])[0] == 202
    status, body = call(http_event, "/ai/preview", "POST", {"date": "2026-10-14"}, ["admin"])
    assert status == 429 and body["error"]["code"] == "PREVIEW_LIMIT"
    assert preview_api.invoke.call_count == 20


@pytest.mark.parametrize(
    "path,method", [("/ai/preview", "POST"), ("/ai/preview/00000000-0000-0000-0000-000000000001", "GET")]
)
def test_preview_admin_required(resources, http_event, path, method):
    assert call(http_event, path, method, {"date": "2026-10-14"})[0] == 403
    event = http_event(path, method, groups=["admin"], body="{}")
    event["requestContext"]["authorizer"]["jwt"]["claims"]["token_use"] = "access"
    assert api.handler(event, None)["statusCode"] == 401


def test_preview_uses_current_settings_when_fields_omitted(preview_api, http_event):
    current = repo.get_settings()
    repo.put_settings(
        {
            **current,
            "ai_prompt": "現在の文面",
            "ai_model_family": "sonnet",
            "ai_model_pin": "global.anthropic.claude-sonnet-5-5",
        },
        current["version"],
        "example",
        "example@example.co.jp",
    )
    status, body = call(http_event, "/ai/preview", "POST", {"date": "2026-10-14"}, ["admin"])
    assert status == 202
    item = repo.get_preview(body["data"]["preview_id"])
    assert item["prompt"] == "現在の文面" and item["model_family"] == "sonnet"
    assert item["model_pin"] == "global.anthropic.claude-sonnet-5-5"


def test_preview_lock_busy_is_502_and_nonaccepted_invoke_is_failed(preview_api, http_event, monkeypatch):
    from unittest.mock import Mock

    create = repo.create_preview
    monkeypatch.setattr(repo, "create_preview", Mock(side_effect=repo.PreviewBusyError("再試行してください")))
    status, body = call(http_event, "/ai/preview", "POST", {"date": "2026-10-14"}, ["admin"])
    assert status == 502 and body["error"]["code"] == "AI_UNAVAILABLE"
    preview_api.invoke.assert_not_called()
    monkeypatch.setattr(repo, "create_preview", create)
    preview_api.invoke.return_value = {"StatusCode": 500}
    assert call(http_event, "/ai/preview", "POST", {"date": "2026-10-14"}, ["admin"])[0] == 502


def test_preview_get_expires_running_and_ttl(preview_api, http_event, monkeypatch):
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 16, 0, 1, tzinfo=repo.JST)
    monkeypatch.setattr(repo, "_now", lambda: now)
    _, body = call(http_event, "/ai/preview", "POST", {"date": "2026-10-14"}, ["admin"])
    preview_id = body["data"]["preview_id"]
    now += timedelta(minutes=5)
    status, body = call(http_event, f"/ai/preview/{preview_id}", groups=["admin"])
    assert status == 200 and body["data"]["status"] == "failed"
    assert body["data"]["error"]["message"] == "処理時間超過"
    assert not repo.update_preview(preview_id, {"status": "succeeded"})
    now += timedelta(days=1)
    assert call(http_event, f"/ai/preview/{preview_id}", groups=["admin"])[0] == 404


RUN_NOW = "2026-10-16T09:30:00+09:00"


@pytest.fixture
def run_api(resources, monkeypatch):
    from datetime import datetime
    from unittest.mock import Mock

    monkeypatch.setenv("REPORT_FUNCTION_NAME", "report-test")
    client = Mock()
    client.invoke.return_value = {"StatusCode": 202}
    monkeypatch.setattr(api, "_lambda_client", lambda: client)
    monkeypatch.setattr(api, "_now", lambda: datetime.fromisoformat(RUN_NOW))
    return client


def run(http_event, date, groups=("admin",), body=None, email="admin@example.co.jp"):
    event = http_event(f"/days/{date}/run", "POST", groups=list(groups), email=email, body=body)
    result = api.handler(event, None)
    return result["statusCode"], json.loads(result["body"])


@pytest.mark.parametrize("date,body", [("2026-10-15", None), ("2026-10-16", "{}"), ("2025-09-11", "")])
def test_run_202_invokes_report_like_manual_batch(run_api, http_event, resources, caplog, date, body):
    status, result = run(http_event, date, body=body)
    assert status == 202
    assert result["data"] == {"date": date, "accepted_at": RUN_NOW}
    request = run_api.invoke.call_args.kwargs
    assert request["FunctionName"] == "report-test" and request["InvocationType"] == "Event"
    assert json.loads(request["Payload"]) == {
        "trigger": "manual",
        "date": date,
        "force": True,
        "requested_by": "admin@example.co.jp",
    }
    assert resources["table"].scan()["Items"] == []
    assert any(
        r.levelname == "INFO" and "admin@example.co.jp" in r.getMessage() and date in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.parametrize(
    "date,body",
    [
        ("2026-10-17", None),
        ("2025-09-10", None),
        ("2026-02-30", None),
        ("20261015", None),
        ("2026-1-05", None),
        ("2026-10-15", json.dumps({"date": "2026-10-14"})),
        ("2026-10-15", "{"),
        ("2026-10-15", "[]"),
    ],
)
def test_run_rejects_invalid_date_and_body(run_api, http_event, resources, date, body):
    status, result = run(http_event, date, body=body)
    assert status == 400 and result["error"]["code"] == "VALIDATION_ERROR"
    run_api.invoke.assert_not_called()


def test_run_conflicts_only_while_fresh_running(run_api, http_event, resources):
    from datetime import datetime, timedelta

    now = datetime.fromisoformat(RUN_NOW)
    repo.acquire_daily("2026-10-15", trigger="scheduled", now=now - timedelta(minutes=19))
    status, result = run(http_event, "2026-10-15")
    assert status == 409 and result["error"]["code"] == "RUN_IN_PROGRESS"
    run_api.invoke.assert_not_called()
    repo.acquire_daily("2026-10-14", trigger="scheduled", now=now - timedelta(minutes=21))
    assert run(http_event, "2026-10-14")[0] == 202
    repo.acquire_daily("2026-10-13", trigger="manual", now=now - timedelta(hours=1))
    repo.finish_daily("2026-10-13", 1, summary={"schema": "daily-security-report.daily.v2", "findings": []})
    assert run(http_event, "2026-10-13")[0] == 202
    assert run_api.invoke.call_count == 2


def test_run_requires_admin(run_api, http_event, resources):
    status, result = run(http_event, "2026-10-15", groups=())
    assert status == 403
    run_api.invoke.assert_not_called()


@pytest.mark.parametrize("outcome", [RuntimeError("unavailable"), {"StatusCode": 500}])
def test_run_invoke_failure_is_502(run_api, http_event, resources, outcome):
    if isinstance(outcome, Exception):
        run_api.invoke.side_effect = outcome
    else:
        run_api.invoke.return_value = outcome
    status, result = run(http_event, "2026-10-15")
    assert status == 502 and result["error"]["code"] == "RUN_UNAVAILABLE"
    assert resources["table"].scan()["Items"] == []


def test_get_settings_exposes_prompt_text_without_storing_read_only_fields(resources, http_event):
    from daily_security_report import prompt_text

    status, body = call(http_event, "/settings")
    assert status == 200
    assert body["data"]["ai_prompt_default"] == prompt_text.DEFAULT_PROMPT_BODY
    assert body["data"]["ai_prompt_fixed"] == prompt_text.FIXED_PARTS
    assert body["data"]["slack_mention_level"] == "critical" and body["data"]["ai_prompt_presets"] == []
    stored = resources["table"].get_item(Key={"PK": "SETTINGS", "SK": "current"})["Item"]
    assert "ai_prompt_default" not in stored and "ai_prompt_fixed" not in stored


@pytest.mark.parametrize(
    "field,value",
    [("ai_prompt_default", "本文"), ("ai_prompt_fixed", {"system_suffix": "固定", "data_prefix": "固定"})],
)
def test_put_settings_rejects_read_only_prompt_fields(resources, http_event, field, value):
    status, body = call(http_event, "/settings", "PUT", {"version": 0, field: value}, ["admin"])
    assert status == 400 and body["error"]["code"] == "VALIDATION_ERROR"
    assert "不明な項目" in body["error"]["message"] and field in body["error"]["message"]
    assert resources["table"].scan()["Items"] == []


def test_put_default_body_normalizes_and_presets_round_trip_in_settings_and_audit(resources, http_event):
    from daily_security_report import prompt_text

    rows = [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "確認用",
            "prompt": "独自の本文",
            "updated_at": "2026-10-16T00:00:00Z",
        }
    ]
    patch = {
        "version": 0,
        "ai_prompt": " \n" + prompt_text.DEFAULT_PROMPT_BODY + " ",
        "ai_prompt_presets": rows,
        "slack_mention_level": "always",
    }
    status, body = call(http_event, "/settings", "PUT", patch, ["admin"])
    assert status == 200 and body["data"]["ai_prompt"] == "" and body["data"]["ai_prompt_presets"] == rows
    saved = call(http_event, "/settings")[1]["data"]
    assert saved["ai_prompt"] == "" and saved["ai_prompt_presets"] == rows and saved["slack_mention_level"] == "always"
    [audit] = [item for item in resources["table"].scan()["Items"] if item["PK"] == "AUDIT"]
    assert audit["after"]["ai_prompt_presets"] == rows and audit["after"]["ai_prompt"] == ""
    assert audit["after"]["slack_mention_level"] == "always"


@pytest.mark.parametrize("prompt", ["", "独自の本文", "  独自の本文  "])
def test_preview_keeps_prompt_body_snapshot(preview_api, http_event, prompt):
    status, body = call(http_event, "/ai/preview", "POST", {"date": "2026-10-14", "prompt": prompt}, ["admin"])
    assert status == 202
    assert repo.get_preview(body["data"]["preview_id"])["prompt"] == prompt


@pytest.mark.parametrize("body", ["   ", "\t\n\u3000"])
def test_put_whitespace_prompt_is_stored_as_empty(resources, http_event, body):
    status, result = call(http_event, "/settings", "PUT", {"version": 0, "ai_prompt": body}, ["admin"])
    assert status == 200 and result["data"]["ai_prompt"] == ""
    assert call(http_event, "/settings")[1]["data"]["ai_prompt"] == ""
    [audit] = [item for item in resources["table"].scan()["Items"] if item["PK"] == "AUDIT"]
    assert audit["after"]["ai_prompt"] == ""


@pytest.mark.parametrize("failure", ["stored_size", "item_size", "transaction_size"])
def test_put_settings_size_errors_return_400_without_settings_or_audit_changes(
    resources, http_event, monkeypatch, failure
):
    from unittest.mock import Mock

    from botocore.exceptions import ClientError

    call(http_event, "/settings")
    before = resources["table"].scan()["Items"]
    patch = {"version": 0}
    if failure == "stored_size":
        patch["exclusions"] = [
            {
                "kind": "principal",
                "value": "test-user",
                "scope": ["all"],
                "category": "",
                "reason": "文" * 60000,
                "expires_at": "",
            }
        ]
        transaction = Mock(wraps=repo._client().transact_write_items)
    else:
        message = (
            "Item size has exceeded the maximum allowed size"
            if failure == "item_size"
            else "Transaction size cannot exceed 4MB"
        )
        transaction = Mock(
            side_effect=ClientError(
                {"Error": {"Code": "ValidationException", "Message": message}}, "TransactWriteItems"
            )
        )
    monkeypatch.setattr(repo._client(), "transact_write_items", transaction)
    status, result = call(http_event, "/settings", "PUT", patch, ["admin"])
    assert status == 400 and result["error"]["code"] == "SETTINGS_TOO_LARGE"
    assert result["error"]["message"] == "設定が大きすぎます。名前付きプロンプトか除外リストを減らしてください"
    assert result["ok"] is False and result["data"] is None
    assert resources["table"].scan()["Items"] == before
    if failure == "stored_size":
        transaction.assert_not_called()
    else:
        transaction.assert_called_once()


@pytest.fixture
def logs_api(resources, monkeypatch):
    from datetime import datetime
    from unittest.mock import Mock

    monkeypatch.setenv("REPORT_FUNCTION_NAME", "report-test")
    client = Mock()
    client.invoke.return_value = {"StatusCode": 202}
    monkeypatch.setattr(api, "_lambda_client", lambda: client)
    monkeypatch.setattr(api, "_now", lambda: datetime.fromisoformat(RUN_NOW))
    monkeypatch.setattr(repo, "_now", lambda: datetime.fromisoformat(RUN_NOW))
    for date in ("2026-10-15", "2026-10-16", "2026-07-18"):
        save_record(date)
    return client


GD_ID = "0123456789abcdef0123456789abcdef"
GD_ID2 = "fedcba9876543210fedcba9876543210"


def save_record(date):
    """「ログを見る」の照合に使う指定日の記録（所見・明細・上位表の ref と集計値）。"""
    from daily_security_report import levels

    summary = levels.apply_rules(
        {
            "schema": "daily-security-report.daily.v2",
            "date": date,
            "findings": [
                {
                    "section": "insights",
                    "code": "ADMIN_EXTERNAL",
                    "level": "warning",
                    "text": "x",
                    "count": 1,
                    "ref": {"kind": "alarmhuba", "ip": "198.51.100.21", "path": "/api/admin/{id}/settings"},
                },
                {
                    "section": "metrics",
                    "code": "RATIO",
                    "level": "warning",
                    "text": "x",
                    "count": 1,
                    "key": "lambda.errors",
                    "ref": {"kind": "lambda", "function": "ref-fn"},
                },
                {
                    "section": "guardduty",
                    "code": "GUARDDUTY_HIGH",
                    "level": "critical",
                    "text": "x",
                    "count": 1,
                    "ref": {"kind": "guardduty", "region": "ap-northeast-1", "id": GD_ID},
                },
            ],
            "counters": {"lambda.errors": 3, "alb.4xx": 1},
            "comparisons": {"instances.ec2.i-0example.cpu_avg": {"value": 1, "baseline": 1}},
            "top": {
                "lambda_errors": [
                    {"function": f, "count": 1} for f in ("fn", "example-fn", "fn-1", "fn-2", "fn-3", "fn-4")
                ],
                "gps_ips": [{"ip": "198.51.100.23", "count": 1, "ref": {"kind": "gps", "ip": "198.51.100.23"}}],
                "guardduty": [{"id": GD_ID2, "region": "ap-northeast-1", "title": "x"}],
            },
            "sources": {
                "guardduty": {"status": "ok", "details": {"findings": [{"id": GD_ID, "region": "ap-northeast-1"}]}},
                "insights": {"status": "ok", "details": {"alarmhuba_denied": [{"ip": "198.51.100.22", "count": 1}]}},
            },
            "ai": {"status": "skipped"},
        }
    )
    repo.acquire_daily(date, trigger="manual")
    repo.finish_daily(date, 1, summary=summary)


def logs(http_event, date, body, groups=("admin",), email="admin@example.co.jp"):
    event = http_event(f"/days/{date}/logs", "POST", groups=list(groups), email=email, body=body)
    result = api.handler(event, None)
    return result["statusCode"], json.loads(result["body"])


LOGS_BODY = {"kind": "alarmhuba", "ip": "198.51.100.21", "path": "/api/admin/{id}/settings"}
LOGS_TARGET = {"ip": "198.51.100.21", "path": "/api/admin/{id}/settings"}


def test_logs_202_saves_running_and_invokes_report(logs_api, http_event, resources, caplog):
    from daily_security_report import log_queries

    status, body = logs(http_event, "2026-10-15", json.dumps(LOGS_BODY))
    assert status == 202
    query_id = log_queries.query_id("2026-10-15", "alarmhuba", LOGS_TARGET)
    assert body["data"] == {
        "query_id": query_id,
        "status": "running",
        "kind": "alarmhuba",
        "target": LOGS_TARGET,
        "requested_at": RUN_NOW,
    }
    request = logs_api.invoke.call_args.kwargs
    assert request["FunctionName"] == "report-test" and request["InvocationType"] == "Event"
    assert json.loads(request["Payload"]) == {
        "trigger": "logs",
        "date": "2026-10-15",
        "query_id": query_id,
        "kind": "alarmhuba",
        "target": LOGS_TARGET,
        "requested_by": "admin@example.co.jp",
    }
    saved = repo.get_log_query("2026-10-15", query_id)
    assert saved["status"] == "running" and saved["requested_by"] == "admin@example.co.jp"
    assert saved["kind"] == "alarmhuba" and saved["target"] == LOGS_TARGET
    assert any(r.levelname == "INFO" and query_id in r.getMessage() for r in caplog.records)
    status, body = call(http_event, f"/days/2026-10-15/logs/{query_id}", groups=["admin"])
    assert status == 200 and body["data"]["status"] == "running" and "requested_by" not in body["data"]
    assert logs(http_event, "2026-10-15", json.dumps(LOGS_BODY))[0] == 202
    nested = {"kind": "alarmhuba", "target": LOGS_TARGET}
    assert logs(http_event, "2026-10-15", json.dumps(nested))[1]["data"]["query_id"] == query_id
    assert logs_api.invoke.call_count == 1


def test_logs_returns_cached_success_and_get_returns_result(logs_api, http_event, resources):
    from daily_security_report import log_queries

    query_id = log_queries.query_id("2026-10-15", "lambda", {"function": "fn"})
    result = {
        "source": "CloudWatch Logs /aws/lambda/fn",
        "range": {},
        "rows": [{"time": "t", "text": "x"}],
        "truncated": False,
    }
    repo.put_log_query("2026-10-15", query_id, {"status": "running", "kind": "lambda", "target": {"function": "fn"}})
    repo.update_log_query("2026-10-15", query_id, {"status": "succeeded", "result": result, "finished_at": RUN_NOW})
    status, body = logs(http_event, "2026-10-15", json.dumps({"kind": "lambda", "function": "fn"}))
    assert status == 200 and body["data"] == {
        "query_id": query_id,
        "status": "succeeded",
        "kind": "lambda",
        "target": {"function": "fn"},
        "requested_at": RUN_NOW,
        "finished_at": RUN_NOW,
        "result": result,
    }
    logs_api.invoke.assert_not_called()
    status, body = call(http_event, f"/days/2026-10-15/logs/{query_id}", groups=["admin"])
    assert status == 200 and body["data"]["result"] == result and body["data"]["query_id"] == query_id
    repo.update_log_query("2026-10-15", query_id, {"status": "failed", "error": "TIMEOUT"})
    status, body = call(http_event, f"/days/2026-10-15/logs/{query_id}", groups=["admin"])
    assert status == 200 and body["data"]["error"] == "TIMEOUT"
    assert logs(http_event, "2026-10-15", json.dumps({"kind": "lambda", "function": "fn"}))[0] == 202
    assert logs_api.invoke.call_count == 1


@pytest.mark.parametrize(
    "date,body,code",
    [
        ("2026-10-17", json.dumps(LOGS_BODY), "VALIDATION_ERROR"),
        ("2026-07-17", json.dumps(LOGS_BODY), "VALIDATION_ERROR"),
        ("2026-1-05", json.dumps(LOGS_BODY), "VALIDATION_ERROR"),
        ("2026-10-15", "{", "VALIDATION_ERROR"),
        ("2026-10-15", "[]", "INVALID_QUERY"),
        ("2026-10-15", None, "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "alarmhuba"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "unknown"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "alarmhuba", "target": LOGS_TARGET, "ip": "198.51.100.21"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "alarmhuba", "ip": "bad"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "alarmhuba", "target": {"ip": "bad"}}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "alarmhuba", "ip": "198.51.100.1", "path": "/api?x=1"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "lambda", "function": "fn; rm"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "guardduty", "region": "eu-west-1", "id": "0" * 32}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "guardduty", "region": "us-east-1", "id": "0123abcd"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "metric", "key": "alarmhuba.requests"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({"kind": "metric", "key": "apigw.5xx"}), "INVALID_QUERY"),
        ("2026-10-15", json.dumps({**LOGS_BODY, "extra": 1}), "INVALID_QUERY"),
    ],
)
def test_logs_rejects_invalid_date_and_body(logs_api, http_event, resources, date, body, code):
    status, result = logs(http_event, date, body)
    assert status == 400 and result["error"]["code"] == code
    logs_api.invoke.assert_not_called()
    assert not [i for i in resources["table"].scan()["Items"] if i["PK"] == "LOGS"]


def test_logs_90_day_boundary_is_accepted(logs_api, http_event, resources):
    assert logs(http_event, "2026-07-18", json.dumps(LOGS_BODY))[0] == 202
    assert logs(http_event, "2026-10-16", json.dumps(LOGS_BODY))[0] == 202


def test_logs_limits_running_queries_per_user_to_three(logs_api, http_event, resources):
    for function in ("fn-1", "fn-2", "fn-3"):
        assert logs(http_event, "2026-10-15", json.dumps({"kind": "lambda", "function": function}))[0] == 202
    status, body = logs(http_event, "2026-10-15", json.dumps({"kind": "lambda", "function": "fn-4"}))
    assert status == 429 and body["error"]["code"] == "TOO_MANY_QUERIES"
    assert logs(http_event, "2026-10-15", json.dumps({"kind": "lambda", "function": "fn-1"}))[0] == 202
    other = json.dumps({"kind": "lambda", "function": "fn-4"})
    assert logs(http_event, "2026-10-15", other, email="other@example.co.jp")[0] == 202
    assert logs_api.invoke.call_count == 4


@pytest.mark.parametrize("outcome", [RuntimeError("unavailable"), {"StatusCode": 500}])
def test_logs_invoke_failure_is_502_and_marks_failed(logs_api, http_event, resources, outcome):
    from daily_security_report import log_queries

    if isinstance(outcome, Exception):
        logs_api.invoke.side_effect = outcome
    else:
        logs_api.invoke.return_value = outcome
    status, body = logs(http_event, "2026-10-15", json.dumps(LOGS_BODY))
    assert status == 502 and body["error"]["code"] == "LOGS_UNAVAILABLE"
    saved = repo.get_log_query("2026-10-15", log_queries.query_id("2026-10-15", "alarmhuba", LOGS_TARGET))
    assert saved["status"] == "failed" and saved["error"] == "LOGS_UNAVAILABLE"


def test_logs_get_not_found_and_admin_required(logs_api, http_event, resources):
    assert call(http_event, "/days/2026-10-15/logs/0123456789abcdef", groups=["admin"])[0] == 404
    assert call(http_event, "/days/2026-10-15/logs/not-hex", groups=["admin"])[0] == 404
    assert call(http_event, "/days/2026-13-15/logs/0123456789abcdef", groups=["admin"])[0] == 400
    assert call(http_event, "/days/20261015/logs/0123456789abcdef", groups=["admin"])[0] == 400
    assert logs(http_event, "2026-10-15", json.dumps(LOGS_BODY), groups=())[0] == 403
    assert call(http_event, "/days/2026-10-15/logs/0123456789abcdef")[0] == 403
    logs_api.invoke.assert_not_called()
    assert not [i for i in resources["table"].scan()["Items"] if i["PK"] == "LOGS"]


def test_logs_metric_key_must_exist_in_the_days_counters_or_comparisons(logs_api, http_event, resources):
    assert logs(http_event, "2026-10-15", json.dumps({"kind": "metric", "key": "lambda.errors"}))[0] == 202
    key = "instances.ec2.i-0example.cpu_avg"
    assert logs(http_event, "2026-10-15", json.dumps({"kind": "metric", "key": key}))[0] == 202
    status, body = logs(http_event, "2026-10-15", json.dumps({"kind": "metric", "key": "waf.blocked"}))
    assert status == 400 and body["error"]["code"] == "INVALID_QUERY"
    assert logs(http_event, "2026-10-14", json.dumps({"kind": "metric", "key": "lambda.errors"}))[0] == 400
    assert logs_api.invoke.call_count == 2


@pytest.mark.parametrize(
    "body,target",
    [
        ({"kind": "alarmhuba", "ip": " 198.51.100.21 ", "path": "/api/admin/{id}/settings"}, LOGS_TARGET),
        ({"kind": "alarmhuba", "target": {"ip": "198.51.100.21", "path": "/api/admin/{id}/settings"}}, LOGS_TARGET),
        ({"kind": "gps", "ip": "198.51.100.23"}, {"ip": "198.51.100.23"}),
        (
            {"kind": "cloudtrail", "region": "us-east-1", "event": "CreateUser", "actor": "root", "event_id": "ev-1"},
            {"actor": "root", "event": "CreateUser", "event_id": "ev-1", "region": "us-east-1"},
        ),
        (
            {"kind": "guardduty", "region": "ap-northeast-1", "id": "0123456789abcdef0123456789abcdef"},
            {"id": "0123456789abcdef0123456789abcdef", "region": "ap-northeast-1"},
        ),
    ],
)
def test_logs_accepts_flat_ref_body_and_returns_normalized_target(logs_api, http_event, resources, body, target):
    from daily_security_report import log_queries

    status, result = logs(http_event, "2026-10-15", json.dumps(body))
    assert status == 202 and result["data"]["target"] == target
    assert result["data"]["query_id"] == log_queries.query_id("2026-10-15", body["kind"], target)
    assert json.loads(logs_api.invoke.call_args.kwargs["Payload"])["target"] == target


def test_logs_flat_metric_body_keeps_string_target_and_mixed_forms_are_rejected(logs_api, http_event, resources):
    body = {"kind": "metric", "key": "alb.4xx", "target": "alb:ap-northeast-1:app/example/0123456789abcdef"}
    status, result = logs(http_event, "2026-10-15", json.dumps(body))
    assert status == 202 and result["data"]["target"] == {"key": body["key"], "target": body["target"]}
    for mixed in (
        {"kind": "alarmhuba", "target": LOGS_TARGET, "ip": "198.51.100.21"},
        {"kind": "alarmhuba", "target": LOGS_TARGET, "extra": 1},
        {"kind": "metric", "target": {"key": "alb.4xx"}, "key": "alb.4xx"},
    ):
        status, result = logs(http_event, "2026-10-15", json.dumps(mixed))
        assert status == 400 and result["error"]["code"] == "INVALID_QUERY"
    assert logs_api.invoke.call_count == 1


@pytest.mark.parametrize(
    "body,status",
    [
        ({"kind": "lambda", "function": "ref-fn"}, 202),
        ({"kind": "lambda", "function": "other-fn"}, 400),
        ({"kind": "alarmhuba", "ip": "198.51.100.22"}, 202),
        ({"kind": "alarmhuba", "ip": "198.51.100.99"}, 400),
        ({"kind": "gps", "ip": "198.51.100.23"}, 202),
        ({"kind": "gps", "ip": "198.51.100.21"}, 400),
        ({"kind": "gps", "ip": "198.51.100.99"}, 400),
        ({"kind": "guardduty", "region": "ap-northeast-1", "id": GD_ID2}, 202),
        ({"kind": "guardduty", "region": "ap-northeast-1", "id": "9" * 32}, 400),
        ({"kind": "cloudtrail", "region": "ap-northeast-1", "event_id": "unknown-event"}, 202),
    ],
)
def test_logs_accepts_only_targets_recorded_for_the_day(logs_api, http_event, resources, body, status):
    code, result = logs(http_event, "2026-10-15", json.dumps(body))
    assert code == status
    if status == 400:
        assert result["error"] == {"code": "INVALID_QUERY", "message": "指定日の記録にありません"}
        logs_api.invoke.assert_not_called()
    assert logs(http_event, "2026-10-14", json.dumps(body))[0] == (202 if body["kind"] == "cloudtrail" else 400)
