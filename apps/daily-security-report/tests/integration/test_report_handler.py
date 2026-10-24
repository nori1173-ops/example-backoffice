import gzip
import json
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from daily_security_report import repo
from daily_security_report.settings import SECTIONS
from daily_security_report_batch.handlers import report

DAY = "2026-10-14"
_REAL_AI_JUDGMENT = report._ai_judgment
_REAL_S3_COLLECT = report.s3_summary.collect
GIB = 1024**3
REGIONS = ["ap-northeast-1", "us-east-1", "us-west-2"]


@pytest.fixture(autouse=True)
def skip_ai_for_collection_tests(monkeypatch):
    monkeypatch.setattr(report, "_ai_judgment", Mock(return_value={"status": "skipped"}))


def queued(resources):
    return [
        json.loads(m["Body"])
        for m in resources["sqs"]
        .receive_message(QueueUrl=resources["queue"], MaxNumberOfMessages=10)
        .get("Messages", [])
    ]


def configure(**patch):
    current = repo.get_settings()
    return repo.put_settings({**current, **patch}, current["version"], "test", "test@example.co.jp")


@pytest.fixture
def pipeline(resources, monkeypatch, fake_cloudwatch):
    @contextmanager
    def build(*, disabled=(), failed=(), cloudtrail_event=False, s3=None):
        clients = {
            (service, region): boto3.client(service, region_name=region)
            for service, region in [
                ("logs", "ap-northeast-1"),
                ("iam", "ap-northeast-1"),
                ("cloudwatch", "ap-northeast-1"),
                ("cloudwatch", "us-east-1"),
                ("cloudwatch", "us-west-2"),
                ("ec2", "ap-northeast-1"),
                ("lightsail", "ap-northeast-1"),
                ("guardduty", "ap-northeast-1"),
                ("guardduty", "us-east-1"),
                ("guardduty", "us-west-2"),
            ]
        }
        clients[("s3", "ap-northeast-1")] = resources["s3"]
        monkeypatch.setenv("GUARDDUTY_DETECTORS", "ap-northeast-1=tokyo,us-east-1=east,us-west-2=west")
        monkeypatch.setattr(report, "_client", lambda service, region="ap-northeast-1": clients[(service, region)])
        # S3 区分は入口・インスタンスと同じ CloudWatch を並行で使うため、Stubber の順序に混ぜず偽物で受ける。
        denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListMetrics")
        fakes = {
            region: fake_cloudwatch((s3 or {}).get(region), error=denied if "s3" in failed else None)
            for region in REGIONS
        }
        s3_calls = []

        def collect_s3(target_date, regional, history, settings):
            s3_calls.append({"regions": list(regional), "history": history})
            return _REAL_S3_COLLECT(target_date, {region: fakes[region] for region in regional}, history, settings)

        monkeypatch.setattr(report.s3_summary, "collect", collect_s3)
        if cloudtrail_event:
            event = {
                "eventTime": "2026-10-14T00:00:00Z",
                "eventSource": "sts.amazonaws.com",
                "eventName": "GetCallerIdentity",
                "userIdentity": {"type": "Root"},
                "sourceIPAddress": "AWS Internal",
            }
            resources["s3"].put_object(
                Bucket="test-cloudtrail-bucket",
                Key="AWSLogs/000000000000/CloudTrail/ap-northeast-1/2026/10/14/test.json.gz",
                Body=gzip.compress(json.dumps({"Records": [event]}).encode(), mtime=0),
            )
        with ExitStack() as stack:
            stubs = {key: stack.enter_context(Stubber(client)) for key, client in clients.items() if key[0] != "s3"}
            if (
                "cloudtrail" not in disabled
                and "cloudtrail" not in failed
                and not (repo.get_daily(DAY) or {}).get("summary", {}).get("actors")
            ):
                stubs[("iam", "ap-northeast-1")].add_response("generate_credential_report", {"State": "COMPLETE"})
                stubs[("iam", "ap-northeast-1")].add_response(
                    "get_credential_report",
                    {"Content": b"user,arn,password_last_used\n<root_account>,arn:aws:iam::000000000000:root,N/A\n"},
                )
                stubs[("iam", "ap-northeast-1")].add_response("list_roles", {"Roles": [], "IsTruncated": False})
            if "guardduty" not in disabled:
                for region in ("ap-northeast-1", "us-east-1", "us-west-2"):
                    stub = stubs[("guardduty", region)]
                    if "guardduty" in failed:
                        if region == "ap-northeast-1":
                            stub.add_client_error("list_findings", "AccessDeniedException")
                    else:
                        detector = {"ap-northeast-1": "tokyo", "us-east-1": "east", "us-west-2": "west"}[region]
                        criteria = {
                            "Criterion": {
                                "updatedAt": {"GreaterThanOrEqual": 1791903600000},
                                "service.archived": {"Equals": ["false"]},
                            }
                        }
                        stub.add_response(
                            "list_findings",
                            {"FindingIds": []},
                            {"DetectorId": detector, "FindingCriteria": criteria, "MaxResults": 50},
                        )
            cw = stubs[("cloudwatch", "ap-northeast-1")]
            if "metrics" not in disabled:
                if "metrics" in failed:
                    cw.add_client_error("get_metric_data", "AccessDeniedException")
                else:
                    cw.add_response(
                        "get_metric_data",
                        {
                            "MetricDataResults": [
                                {
                                    "Id": "lambda_errors",
                                    "Label": "lambda|test",
                                    "Values": [2.0],
                                    "Timestamps": [],
                                    "StatusCode": "Complete",
                                }
                            ]
                        },
                    )
            if "instances" not in disabled:
                if "instances" in failed:
                    stubs[("ec2", "ap-northeast-1")].add_client_error("describe_instances", "UnauthorizedOperation")
                else:
                    stubs[("ec2", "ap-northeast-1")].add_response(
                        "describe_instances",
                        {
                            "Reservations": [
                                {
                                    "Instances": [
                                        {
                                            "InstanceId": "i-00000000000000001",
                                            "State": {"Name": "running"},
                                            "Tags": [{"Key": "Name", "Value": "Test"}],
                                        }
                                    ]
                                }
                            ]
                        },
                    )
                    cw.add_response(
                        "get_metric_data",
                        {
                            "MetricDataResults": [
                                {"Id": key, "Values": [value] * 24, "StatusCode": "Complete"}
                                for key, value in [("cpu_0", 1.25), ("net_0", 10)]
                            ]
                        },
                    )
                    lightsail = stubs[("lightsail", "ap-northeast-1")]
                    lightsail.add_response(
                        "get_instances", {"instances": [{"name": "TestLight", "state": {"name": "running"}}]}
                    )
                    for metric, unit, stat in [
                        ("CPUUtilization", "Percent", "Average"),
                        ("NetworkOut", "Bytes", "Sum"),
                    ]:
                        lightsail.add_response(
                            "get_instance_metric_data",
                            {"metricData": [{stat.lower(): 2.5}] * 24},
                            {
                                "instanceName": "TestLight",
                                "metricName": metric,
                                "period": 3600,
                                "startTime": ANY,
                                "endTime": ANY,
                                "unit": unit,
                                "statistics": [stat],
                            },
                        )
            if "insights" not in disabled:
                logs = stubs[("logs", "ap-northeast-1")]
                if "insights" in failed:
                    logs.add_client_error("start_query", "AccessDeniedException")
                else:
                    for query in ("Q1", "Q2", "Q3", "Q5"):
                        logs.add_response("start_query", {"queryId": query})
                    for query in ("Q1", "Q2", "Q3", "Q5"):
                        logs.add_response(
                            "get_query_results",
                            {"status": "Complete", "results": [], "statistics": {"bytesScanned": 123.5}},
                            {"queryId": query},
                        )
            yield {"s3_calls": s3_calls}
            for stub in stubs.values():
                stub.assert_no_pending_responses()

    return build


@pytest.mark.parametrize("mail,slack", [(False, False), (True, False), (False, True), (True, True)])
def test_full_pipeline_saves_summary_and_real_notify_json(resources, pipeline, mail, slack):
    configure(mail_enabled=mail, slack_enabled=slack, slack_channel_id="D000000001", slack_mention="<!here>")
    with pipeline(cloudtrail_event=True):
        result = report.handler({"trigger": "manual", "date": DAY}, None)
    assert result["status"] == "succeeded"
    run = repo.get_daily(DAY)
    assert run["summary"]["schema"] == "daily-security-report.daily.v2"
    assert all(s["status"] == "ok" for s in run["summary"]["sources"].values())
    assert run["summary"]["instances"]["ec2"][0]["cpu_avg"] == 1.25
    assert run["summary"]["sources"]["insights"]["bytes_scanned"] == 494
    assert run["summary"]["seen"]["cloudtrail_human_ips"] == []
    assert run["level"] == "critical"
    assert run["summary"]["finding_counts"] == {"critical": 1, "warning": 0}
    assert run["summary"]["actors"]["status"] == "initializing"
    assert run["ai"]["status"] == "skipped"
    messages = queued(resources)
    channels = (["email"] if mail else []) + (["slack"] if slack else [])
    assert run["notify_channels"] == channels
    if not channels:
        assert not messages and run["notify_note"] == "配布先が無効のため送付なし"
        assert run["sent_count"] == 0
    else:
        message = messages[0]
        assert message["channels"] == channels
        assert message["message_id"] == run["notify_message_id"]
        assert message["meta"] == {
            "date": DAY,
            "trigger": "manual",
            "attempt": 1,
            "level": "critical",
            "finding_counts": {"critical": 1, "warning": 0},
        }
        assert ("email" in message) == mail and ("slack" in message) == slack
        if mail:
            assert message["email"]["subject"].startswith("【検証環境】[Critical] AWS日次セキュリティレポート")
            assert "<!here>" not in message["email"]["text"]
        if slack:
            assert message["slack"]["channel"] == "D000000001"
            assert message["slack"]["text"].startswith("<!here> 【検証環境】")
            assert len(message["slack"]["blocks"]) <= 50
        assert run["sent_count"] == 1


@pytest.mark.parametrize("disabled", SECTIONS)
def test_off_section_does_not_call_its_apis_or_produce_values(resources, pipeline, disabled):
    configure(sections={name: name != disabled for name in SECTIONS}, mail_enabled=True)
    with pipeline(disabled=[disabled]):
        report.handler({"trigger": "manual", "date": DAY}, None)
    summary = repo.get_daily(DAY)["summary"]
    assert summary["sources"][disabled] == {"status": "disabled"}
    assert all(source["status"] == "ok" for name, source in summary["sources"].items() if name != disabled)
    message = queued(resources)[0]
    assert "停止中（設定）" not in message["email"]["text"]
    assert "[OK]" in message["email"]["subject"]


def test_partial_collection_failures_continue_and_are_not_healthy(resources, pipeline):
    configure(mail_enabled=True)
    with pipeline(failed=["guardduty", "metrics", "insights", "instances", "s3"]):
        report.handler({"trigger": "manual", "date": DAY}, None)
    run = repo.get_daily(DAY)
    assert run["status"] == "succeeded" and run["summary"]["sources"]["cloudtrail"]["status"] == "ok"
    assert run["summary"]["sources"]["guardduty"]["status"] == "failed"
    assert run["summary"]["sources"]["s3"] == {
        "status": "failed",
        "error": "AccessDenied",
        "duration_sec": run["summary"]["sources"]["s3"]["duration_sec"],
    }
    assert any(f["section"] == "s3" and f["code"] == "SOURCE_FAILED" for f in run["summary"]["findings"])
    assert "[Warning]" in queued(resources)[0]["email"]["subject"]


def test_scheduled_disabled_duplicate_and_manual_force_resend(resources, pipeline):
    configure(mail_enabled=True)
    assert report.handler({"trigger": "scheduled"}, None)["reason"] == "AUTO_RUN_DISABLED"
    assert not repo.list_days()
    for force in (False, True):
        with pipeline():
            report.handler({"trigger": "manual", "date": DAY, "force": force}, None)
    assert repo.get_daily(DAY)["attempt"] == 2 and repo.get_daily(DAY)["sent_count"] == 2
    messages = queued(resources)
    assert any("再送（第 2 回）" in message["email"]["text"] for message in messages)
    assert report.handler({"trigger": "manual", "date": DAY}, None)["reason"] == "ALREADY_DONE"


def test_scheduled_uses_jst_yesterday(resources, pipeline, monkeypatch):
    configure(auto_run_enabled=True)
    monkeypatch.setattr(report, "_now", lambda: datetime.fromisoformat("2026-10-15T00:01:00+09:00"))
    with pipeline():
        report.handler({"trigger": "scheduled"}, None)
    assert repo.get_daily(DAY)["trigger"] == "scheduled"


def test_all_enabled_failed_sends_failure_notice_then_raises(resources, pipeline):
    configure(sections={s: s == "guardduty" for s in SECTIONS}, mail_enabled=True)
    with (
        pipeline(disabled=[s for s in SECTIONS if s != "guardduty"], failed=["guardduty"]),
        pytest.raises(RuntimeError, match="集計"),
    ):
        report.handler({"trigger": "manual", "date": DAY}, None)
    assert repo.get_daily(DAY)["status"] == "failed"
    assert queued(resources)[0]["email"]["subject"].endswith("集計失敗")


def test_notify_failure_marks_failed_without_failure_resend(resources, pipeline, monkeypatch):
    configure(mail_enabled=True)
    send = Mock(side_effect=RuntimeError("queue failed"))
    monkeypatch.setattr(report.notify, "send", send)
    with pipeline(), pytest.raises(RuntimeError):
        report.handler({"trigger": "manual", "date": DAY}, None)
    assert repo.get_daily(DAY)["status"] == "failed" and send.call_count == 1


def test_report_failure_sends_short_failure_message(resources, pipeline, monkeypatch):
    configure(mail_enabled=True)
    monkeypatch.setattr(report.report, "build_report", Mock(side_effect=ValueError("report failed")))
    with pipeline(), pytest.raises(ValueError):
        report.handler({"trigger": "manual", "date": DAY}, None)
    assert repo.get_daily(DAY)["status"] == "failed"
    assert queued(resources)[0]["email"]["subject"].endswith("集計失敗")


@pytest.mark.parametrize(
    "event",
    [
        {"trigger": "other"},
        {"trigger": "manual", "date": "bad"},
        {"trigger": "manual", "force": "true"},
        {"trigger": "scheduled", "force": True},
        {"trigger": "manual", "notify": "false"},
        {"trigger": "scheduled", "notify": False},
    ],
)
def test_invalid_invocation_never_acquires(resources, event):
    with pytest.raises(ValueError):
        report.handler(event, None)
    assert not repo.list_days()


def test_disabled_history_is_not_a_baseline(resources, pipeline):
    for i in range(1, 4):
        day = (datetime.fromisoformat(DAY) - timedelta(days=i)).date().isoformat()
        repo.acquire_daily(day, trigger="manual")
        repo.finish_daily(
            day,
            1,
            summary={"date": day, "sources": {"metrics": {"status": "disabled"}}, "counters": {"lambda.errors": 0}},
        )
    with pipeline():
        report.handler({"trigger": "manual", "date": DAY}, None)
    comparison = repo.get_daily(DAY)["summary"]["comparisons"]["lambda.errors"]
    assert comparison["days"] == 0 and comparison["label"] == "基準なし"


def test_failed_run_retains_collection_diagnostics(resources, pipeline):
    configure(sections={s: s == "guardduty" for s in SECTIONS})
    with (
        pipeline(disabled=[s for s in SECTIONS if s != "guardduty"], failed=["guardduty"]),
        pytest.raises(RuntimeError),
    ):
        report.handler({"trigger": "manual", "date": DAY}, None)
    run = repo.get_daily(DAY)
    assert run["summary"]["sources"]["guardduty"]["error"] == "AccessDeniedException"
    assert run["notify_note"] == "配布先が無効のため送付なし"


def test_insights_runs_concurrently_before_other_collectors(resources, monkeypatch):
    from threading import Event

    from daily_security_report.settings import default_settings

    started, other_finished = Event(), Event()
    monkeypatch.setattr(report, "_client", lambda *args: object())

    def insight(*args, **kwargs):
        started.set()
        assert other_finished.wait(2)
        return {"status": "ok", "counters": {}, "bytes_scanned": 123}

    def trail(*args):
        assert started.wait(2)
        other_finished.set()
        return {"status": "ok"}

    monkeypatch.setattr(report.insights_queries, "collect", insight)
    monkeypatch.setattr(report.cloudtrail_summary, "collect", trail)
    monkeypatch.setattr(report.guardduty_summary, "collect", lambda *args: {"status": "ok", "red": []})
    monkeypatch.setattr(report.metrics_summary, "collect", lambda *args: {"status": "ok"})
    monkeypatch.setattr(report.instances_summary, "collect", lambda *args, **kwargs: {"status": "ok"})
    monkeypatch.setattr(report.s3_summary, "collect", lambda *args: {"status": "ok"})
    summary = report._aggregate(DAY, default_settings(), [], 1)
    assert all(s["status"] == "ok" for s in summary["sources"].values())


def test_alert_record_marks_daily_high_finding(resources, monkeypatch):
    from daily_security_report.guardduty_summary import summarize_findings

    finding = {"id": "test", "region": "ap-northeast-1", "severity": 8, "title": "Test", "type": "Test:Finding"}
    repo.claim_alert(finding)
    repo.finish_alert(finding, "test-message")
    payload = summarize_findings(
        [
            {
                "Id": "test",
                "Region": "ap-northeast-1",
                "Severity": 8,
                "Title": "Test",
                "CreatedAt": "2026-10-14T00:00:00Z",
                "UpdatedAt": "2026-10-14T00:00:00Z",
            }
        ],
        DAY,
    )
    monkeypatch.setattr(report, "_client", lambda *args: object())
    monkeypatch.setattr(report.guardduty_summary, "collect", lambda *args: payload)
    result = report._guardduty(DAY, {})
    assert result["red"][0]["alerted"]
    assert result["red"][0]["text"].endswith("即時通知済み")


def test_gps_uses_only_designated_existing_alb(resources, monkeypatch):
    from daily_security_report.settings import default_settings

    settings = default_settings()
    alb = "app/test-alb/0123456789abcdef"
    settings["metric_targets"] = {"alb": [{"id": alb, "name": "test-alb", "label": "gps"}]}
    metrics = {"status": "ok", "target_counters": {f"alb:ap-northeast-1:{alb}": {"4xx": 7}}}
    assert report._gps_rejected(settings, metrics) == 7
    assert report._gps_rejected(settings, {**metrics, "missing_targets": [f"alb:{alb}"]}) is None
    assert report._gps_rejected({"metric_targets": {"alb": [{"name": "test-alb", "label": "gps"}]}}, metrics) is None
    assert report._gps_rejected(settings, {"status": "failed"}) is None
    monkeypatch.setattr(report, "_client", lambda *args: object())
    monkeypatch.setattr(
        report.insights_queries,
        "collect",
        lambda *args, **kwargs: {
            "status": "ok",
            "counters": {},
            "notes": ["未取得"],
            "gps_rejected_source": "unavailable",
        },
    )
    monkeypatch.setattr(report.metrics_summary, "collect", lambda *args: metrics)
    monkeypatch.setattr(report.guardduty_summary, "collect", lambda *args: {"status": "ok", "red": []})
    monkeypatch.setattr(report.cloudtrail_summary, "collect", lambda *args: {"status": "ok"})
    monkeypatch.setattr(report.instances_summary, "collect", lambda *args, **kwargs: {"status": "ok"})
    summary = report._aggregate(DAY, settings, [], 1)
    assert summary["counters"]["gps.rejected"] == 7
    assert summary["sources"]["insights"]["gps_rejected_source"] == "alb.4xx"


def test_unexpected_collection_exception_is_failed_section(resources, pipeline, monkeypatch, caplog):
    monkeypatch.setattr(report.guardduty_summary, "collect", Mock(side_effect=ValueError("private details")))
    with pipeline(disabled=["guardduty"]):
        report.handler({"trigger": "manual", "date": DAY}, None)
    source = repo.get_daily(DAY)["summary"]["sources"]["guardduty"]
    assert source["status"] == "failed" and source["error"] == "ValueError"
    assert "private details" not in str(source)
    assert any(
        r.levelname == "WARNING" and "report._collect" in r.message and "private details" in r.message
        for r in caplog.records
    )


@pytest.mark.parametrize("remaining,expected", [(900000, 480), (600000, 390), (180000, 0), (30000, 0)])
def test_lambda_remaining_time_bounds_insights_budget(resources, monkeypatch, remaining, expected):
    configure(sections={s: s == "insights" for s in SECTIONS})
    monkeypatch.setattr(report, "_client", lambda *a: object())
    collect = Mock(return_value={"status": "ok"})
    monkeypatch.setattr(report.insights_queries, "collect", collect)
    context = Mock()
    context.get_remaining_time_in_millis.return_value = remaining
    report.handler({"trigger": "manual", "date": DAY}, context)
    assert collect.call_args.kwargs["timeout"] == expected


def test_sent_daily_finish_failure_is_logged_and_saved_failed_with_notification(
    resources, pipeline, monkeypatch, caplog
):
    configure(mail_enabled=True)
    monkeypatch.setattr(repo, "finish_daily", Mock(side_effect=ValueError("storage rejected")))
    with pipeline(), pytest.raises(ValueError, match="storage rejected"):
        report.handler({"trigger": "manual", "date": DAY}, None)
    run = repo.get_daily(DAY)
    assert run["status"] == "failed" and run["error"] == "ValueError"
    assert run["notify_message_id"] and run["notify_channels"] == ["email"] and run["notified_at"]
    assert run["sent_count"] == 1 and len(queued(resources)) == 1
    assert any(
        r.levelname == "WARNING" and "storage rejected" in r.message and "Traceback" in r.message
        for r in caplog.records
    )


def test_failure_notice_does_not_count_as_daily_resend(resources, pipeline):
    configure(sections={s: s == "guardduty" for s in SECTIONS}, mail_enabled=True)
    disabled = [s for s in SECTIONS if s != "guardduty"]
    with pipeline(disabled=disabled, failed=["guardduty"]), pytest.raises(RuntimeError):
        report.handler({"trigger": "manual", "date": DAY}, None)
    assert repo.get_daily(DAY)["sent_count"] == 0
    with pipeline(disabled=disabled):
        report.handler({"trigger": "manual", "date": DAY}, None)
    assert repo.get_daily(DAY)["sent_count"] == 1
    assert all("再送" not in m["email"]["text"] for m in queued(resources))


def test_sources_keep_schema_fields_and_details_up_to_20_rows(resources, monkeypatch):
    from daily_security_report.settings import default_settings

    settings = default_settings()
    settings["sections"] = {s: s in ("cloudtrail", "metrics", "insights") for s in SECTIONS}
    row = {"principal": "p" * 300, "ip": "203.0.113.1", "cc": "JP", "event": "e" * 100, "time": "t", "region": "r"}
    trail = {
        "status": "ok",
        "files": 7,
        "records": 100,
        "regions": ["ap-northeast-1"],
        "counters": {"cloudtrail.iam_change": 100},
        "details": {
            "human_operations": [row] * 100,
            "console_logins": [row] * 3,
            "iam_changes": [row] * 100,
            "sg_changes": [],
        },
        "config_changes": [{**row, "resources": [f"i-{n:017d}" for n in range(100)]}] * 100,
        "unexpected_payload": "omit",
    }
    metrics = {
        "status": "ok",
        "counters": {"alb.4xx": 1},
        "target_counters": {"alb:ap-northeast-1:app/demo/0123456789abcdef": {"4xx": 1}},
        "missing_targets": ["waf:demo"],
    }
    insights = {
        "status": "ok",
        "counters": {"alarmhuba.admin_external": 100},
        "details": {"alarmhuba_admin": [{"ip": "203.0.113.2", "cc": "US", "path": "/" * 500, "count": 1}] * 100},
        "truncated": [],
        "query_ids": {"Q1": "query-1"},
        "bytes_scanned": 10,
        "gps_rejected_source": "unavailable",
        "notes": ["note"],
        "request_ids_truncated": True,
    }
    monkeypatch.setattr(report, "_client", lambda *a: object())
    monkeypatch.setattr(report.cloudtrail_summary, "collect", lambda *a: trail)
    monkeypatch.setattr(report.metrics_summary, "collect", lambda *a: metrics)
    monkeypatch.setattr(report.insights_queries, "collect", lambda *a, **kw: insights)
    summary = report._aggregate(DAY, settings, [], 1)
    sources = summary["sources"]
    assert set(sources["cloudtrail"]) == {
        "status",
        "files",
        "records",
        "regions",
        "duration_sec",
        "details",
        "config_changes",
        "details_truncated",
    }
    assert {name: len(rows) for name, rows in sources["cloudtrail"]["details"].items()} == {
        "human_operations": 20,
        "console_logins": 3,
        "iam_changes": 20,
        "sg_changes": 0,
    }
    assert len(sources["cloudtrail"]["config_changes"]) == 20
    assert sources["cloudtrail"]["details_truncated"] == {
        "human_operations": 80,
        "iam_changes": 80,
        "config_changes": 80,
    }
    assert set(sources["metrics"]) == {"status", "missing_targets", "duration_sec"}
    assert set(sources["insights"]) == {
        "status",
        "truncated",
        "query_ids",
        "bytes_scanned",
        "gps_rejected_source",
        "notes",
        "request_ids_truncated",
        "duration_sec",
        "details",
        "details_truncated",
    }
    assert len(sources["insights"]["details"]["alarmhuba_admin"]) == 20
    assert sources["insights"]["details_truncated"] == {"alarmhuba_admin": 80}
    assert len(trail["details"]["iam_changes"]) == 100 and len(trail["config_changes"]) == 100
    assert "unexpected_payload" not in json.dumps(summary) and "target_counters" not in json.dumps(summary)
    assert summary["counters"]["cloudtrail.iam_change"] == 100 and summary["counters"]["alb.4xx"] == 1
    assert len(json.dumps(summary, ensure_ascii=False).encode()) < 400 * 1024
    repo.acquire_daily(DAY, trigger="manual")
    repo.finish_daily(DAY, 1, summary=summary)
    saved = repo.get_daily(DAY)["summary"]["sources"]
    assert saved["cloudtrail"]["details_truncated"]["config_changes"] == 80


def test_s3_client_pool_fits_parallel_reads(monkeypatch):
    session = Mock()
    monkeypatch.setattr(report.boto3.session, "Session", lambda: session)
    report._client("s3")
    assert session.client.call_args.kwargs["config"].max_pool_connections == 32


def test_ai_hook_receives_bounded_context_and_combines_before_notify(resources, pipeline, monkeypatch):
    from daily_security_report.settings import default_settings

    configure(
        metric_targets={"waf": [{"name": "example", "label": "入口"}]},
        exclusions=[
            {
                "kind": "ip",
                "value": "203.0.113.1",
                "scope": ["all"],
                "category": "",
                "reason": "known",
                "expires_at": "",
            }
        ],
    )
    for i in range(1, 9):
        day = (datetime.fromisoformat(DAY) - timedelta(days=i)).date().isoformat()
        repo.acquire_daily(day, trigger="manual")
        repo.finish_daily(
            day,
            1,
            summary={
                "schema": "daily-security-report.daily.v2",
                "date": day,
                "level": "ok",
                "finding_counts": {"critical": 0, "warning": 0},
                "ai": {"status": "ok", "opinion": "文" * 400},
                "sources": {s: {"status": "ok"} for s in SECTIONS},
                "counters": {},
            },
        )
    hook = Mock(
        return_value={
            "status": "ok",
            "sections": [
                {"section": s, "level": "warning" if s == "metrics" else "ok", "reason": "確認"}
                for s in default_settings()["sections"]
            ],
            "opinion": "見解",
        }
    )
    monkeypatch.setattr(report, "_ai_judgment", hook)
    with pipeline(cloudtrail_event=True):
        report.handler({"trigger": "manual", "date": DAY}, None)
    context = hook.call_args.args[2]
    assert len(context["recent_days"]) == 7 and all(len(d["ai_opinion"]) == 200 for d in context["recent_days"])
    assert context["exclusions"] == [{"kind": "ip", "value": "203.0.113.1", "reason": "known"}]
    assert context["rule_level"] == "critical" and context["instance_names"] == ["Test", "TestLight"]
    saved = repo.get_daily(DAY)
    assert saved["summary"]["levels"]["cloudtrail"] == "critical"
    assert saved["summary"]["levels"]["metrics"] == "warning"


def test_iam_unavailable_does_not_stop_daily(resources, pipeline, monkeypatch):
    monkeypatch.setattr(
        report.actors.iam_snapshot,
        "collect",
        lambda *a: {"status": "unavailable", "actors": {}, "note": "AccessDenied"},
    )
    # The pipeline normally expects IAM calls, so provide collectors directly for this failure case.
    monkeypatch.setattr(report, "_client", lambda *a: object())
    for module in (
        report.cloudtrail_summary,
        report.metrics_summary,
        report.instances_summary,
        report.insights_queries,
        report.guardduty_summary,
    ):
        monkeypatch.setattr(module, "collect", lambda *a, **k: {"status": "ok", "red": []})
    report.handler({"trigger": "manual", "date": DAY}, None)
    saved = repo.get_daily(DAY)["summary"]
    assert saved["actors"]["status"] == "unavailable" and saved["ai"]["status"] == "skipped"


def test_insights_v2_counts_mixed_levels_before_legacy_cap(monkeypatch):
    from daily_security_report.settings import default_settings

    client = Mock()
    query_ids = iter(("q1", "q2", "q3", "q5"))
    client.start_query.side_effect = lambda **k: {"queryId": next(query_ids)}
    queries = {
        "q1": [{"src": f"2001:db8::{i:x}", "cf": "false", "cc": "JP", "count": "3"} for i in range(110)],
        "q2": [{"src": f"2001:db8:1::{i:x}", "path": "/api/admin", "cc": "JP", "count": "4"} for i in range(120)],
        "q3": [],
        "q5": [],
    }
    client.get_query_results.side_effect = lambda queryId, **k: {
        "status": "Complete",
        "results": [[{"field": name, "value": value} for name, value in row.items()] for row in queries[queryId]],
    }
    result = report._insights(DAY, client, default_settings(), timeout=480)
    assert result["finding_counts"] == {"critical": 110, "warning": 120}
    assert len(result["findings"]) == 100 and all(r["count"] == 3 for r in result["findings"])
    assert result["findings_truncated"] == {"insights": 130}
    assert result["details_truncated"] == {"alarmhuba_admin": 20}
    assert client.start_query.call_count == 4


@pytest.mark.parametrize("failure", [False, True])
def test_real_ai_path_combines_and_persists_top_level(resources, pipeline, monkeypatch, failure):
    import time

    from daily_security_report import ai

    runtime, control = Mock(), Mock()
    model_id = "jp.anthropic.claude-opus-5-5"
    control.list_inference_profiles.return_value = {
        "inferenceProfileSummaries": [{"inferenceProfileId": model_id, "status": "ACTIVE"}]
    }
    if failure:
        runtime.converse.side_effect = RuntimeError("unavailable")
    else:
        runtime.converse.return_value = {
            "stopReason": "tool_use",
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "submit_verdict",
                                "input": {
                                    "sections": [
                                        {
                                            "section": s,
                                            "level": "critical"
                                            if s == "cloudtrail"
                                            else "warning"
                                            if s == "metrics"
                                            else "ok",
                                            "reason": "確認が必要",
                                        }
                                        for s in SECTIONS
                                    ],
                                    "overall_opinion": "確認が必要な操作がありました。担当者へ確認してください。",
                                    "adjustments": [
                                        {"section": "metrics", "from": "ok", "to": "warning", "reason": "変化を確認"}
                                    ],
                                },
                            }
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 123, "outputTokens": 45},
        }
    monkeypatch.setattr(ai, "_client", lambda service: runtime if service == "bedrock-runtime" else control)
    monkeypatch.setattr(report, "_ai_judgment", _REAL_AI_JUDGMENT)
    configure(mail_enabled=True)
    started = time.monotonic()
    with pipeline(cloudtrail_event=True):
        report.handler({"trigger": "manual", "date": DAY}, None)
    saved = repo.get_daily(DAY)
    assert saved["summary"]["ai"]["status"] == ("failed" if failure else "ok")
    assert saved["ai_status"] == saved["ai"]["status"]
    assert saved["summary"]["levels"]["metrics"] == ("ok" if failure else "warning")
    assert saved["summary"]["levels"]["cloudtrail"] == "critical"
    assert saved["ai_model_label"] == "Claude Opus 5.5"
    assert saved["ai_opinion"] == saved["summary"]["ai"]["opinion"]
    message = queued(resources)[0]["email"]["text"]
    assert ("取得できず" in message) == failure
    assert time.monotonic() - started < 150


@pytest.mark.parametrize("failure", [False, True])
def test_preview_worker_isolated_and_uses_snapshot(resources, monkeypatch, failure):
    import uuid
    from copy import deepcopy

    from daily_security_report import ai, levels

    summary = levels.apply_rules(
        {
            "date": DAY,
            "findings": [],
            "sources": {s: {"status": "ok"} for s in SECTIONS},
            "actors": {"status": "ok", "dormant": [], "new": [], "external": []},
        }
    )
    repo.acquire_daily(DAY, trigger="manual")
    repo.finish_daily(DAY, 1, summary=summary)
    configure(ai_enabled=False, ai_prompt="保存された設定")
    preview_id = str(uuid.uuid4())
    repo.create_preview(
        {
            "preview_id": preview_id,
            "date": DAY,
            "prompt": "試行時の文面",
            "model_family": "haiku",
            "model_pin": "jp.anthropic.claude-haiku-5-5",
            "requested_by": "example",
        }
    )
    before = deepcopy([i for i in resources["table"].scan()["Items"] if i["PK"] != "PREVIEW"])
    verdict = (
        {"status": "failed", "error": "NO_TOOL_USE"}
        if failure
        else {
            "status": "ok",
            "sections": [
                {"section": s, "level": "warning" if s == "metrics" else "ok", "reason": "確認"} for s in SECTIONS
            ],
            "opinion": "見解。確認。",
            "model_id": "example",
            "prompt_hash": "hash",
        }
    )
    hook = Mock(return_value=verdict)
    monkeypatch.setattr(ai, "run_judgment", hook)
    monkeypatch.setattr(report, "_aggregate", Mock(side_effect=AssertionError("must not collect")))
    report.handler({"action": "ai_preview", "preview_id": preview_id}, None)
    result = repo.get_preview(preview_id)
    assert result["status"] == ("failed" if failure else "succeeded")
    if failure:
        assert result["error"]["code"] == "AI_UNAVAILABLE" and result["error"]["http_status"] == 502
    else:
        assert result["result"]["levels"]["metrics"] == "warning" and result["result"]["level"] == "warning"
        assert result["model_id"] == "example" and result["prompt_hash"] == "hash"
    params = hook.call_args
    assert params.args[1]["ai_enabled"] is True
    assert params.args[1]["ai_prompt"] == "試行時の文面" and params.args[1]["ai_model_family"] == "haiku"
    assert params.kwargs["update_state"] is False
    assert params.args[0]["actors"] == repo.get_daily(DAY)["summary"]["actors"]
    assert [i for i in resources["table"].scan()["Items"] if i["PK"] != "PREVIEW"] == before
    assert not queued(resources)
    report.handler({"action": "ai_preview", "preview_id": preview_id}, None)
    assert hook.call_count == 1


def test_preview_expired_or_missing_skips_ai(resources, monkeypatch):
    import uuid

    from daily_security_report import ai

    hook = Mock()
    monkeypatch.setattr(ai, "run_judgment", hook)
    result = report.handler({"action": "ai_preview", "preview_id": str(uuid.uuid4())}, None)
    assert result["status"] == "skipped"
    hook.assert_not_called()


def test_preview_missing_daily_is_failed_without_collection(resources, monkeypatch):
    import uuid

    preview_id = str(uuid.uuid4())
    repo.create_preview({"preview_id": preview_id, "date": DAY})
    collect = Mock()
    monkeypatch.setattr(report, "_aggregate", collect)
    assert report.handler({"action": "ai_preview", "preview_id": preview_id}, None)["status"] == "failed"
    assert repo.get_preview(preview_id)["error"]["code"] == "AI_UNAVAILABLE"
    collect.assert_not_called()


@pytest.mark.parametrize("trail_status", ["ok", "disabled", "failed"])
@pytest.mark.parametrize("weekday", [0, 1, None])
def test_inventory_is_independent_of_cloudtrail_and_shares_iam_snapshot(resources, monkeypatch, trail_status, weekday):
    from daily_security_report.settings import default_settings

    settings = default_settings() | {"inventory_weekday": weekday}
    settings["sections"] = {s: s == "metrics" or (s == "cloudtrail" and trail_status != "disabled") for s in SECTIONS}
    monkeypatch.setattr(report, "_client", lambda *a: object())
    monkeypatch.setattr(report.metrics_summary, "collect", lambda *a: {"status": "ok"})
    monkeypatch.setattr(
        report.cloudtrail_summary, "collect", lambda *a: {"status": trail_status, "error": "AccessDenied"}
    )
    snapshot = Mock(
        return_value={
            "status": "ok",
            "actors": {
                "arn:aws:iam::000000000000:user/example": {
                    "kind": "user",
                    "last_seen": "2026-01-01",
                    "password_enabled": True,
                    "mfa_active": False,
                }
            },
        }
    )
    monkeypatch.setattr(report.actors.iam_snapshot, "collect", snapshot)
    write_actors = Mock()
    monkeypatch.setattr(repo, "batch_write_actors", write_actors)
    summary = report._aggregate("2026-10-19", settings, [], 1)
    actors = summary["actors"]
    assert actors["status"] == {"ok": "initializing", "disabled": "disabled", "failed": "unavailable"}[trail_status]
    if trail_status != "ok":
        assert actors["note"] and actors["dormant"] == actors["new"] == []
        write_actors.assert_not_called()
    if weekday == 0:
        assert actors["inventory"]["counts"] == {"users": 1, "keys": 0, "mfa_missing": 1}
    else:
        assert "inventory" not in actors
    assert snapshot.call_count == int(trail_status == "ok" or weekday == 0)


@pytest.mark.parametrize("preview", [False, True])
def test_failed_source_floor_is_shared_for_daily_and_preview(resources, monkeypatch, preview):
    import uuid

    from daily_security_report import ai, levels

    summary = levels.apply_rules(
        {"date": DAY, "findings": [], "sources": {"metrics": {"status": "failed", "error": "TIMEOUT"}}}
    )
    summary["findings"] = []
    result = {
        "status": "ok",
        "sections": [{"section": "metrics", "level": "ok", "reason": "変化なし"}],
        "opinion": "総評。確認。",
    }
    if preview:
        repo.acquire_daily(DAY, trigger="manual")
        repo.finish_daily(DAY, 1, summary=summary)
        preview_id = str(uuid.uuid4())
        repo.create_preview({"preview_id": preview_id, "date": DAY})
        monkeypatch.setattr(ai, "run_judgment", Mock(return_value=result))
        report.handler({"action": "ai_preview", "preview_id": preview_id}, None)
        final = repo.get_preview(preview_id)["result"]
    else:
        report._apply_ai(summary, result)
        final = summary
    assert final["levels"]["metrics"] == final["level"] == "warning"


def test_manual_run_records_requested_by_in_request_history(resources, pipeline):
    configure(mail_enabled=True)
    with pipeline():
        report.handler({"trigger": "manual", "date": DAY}, None)
    with pipeline():
        result = report.handler(
            {"trigger": "manual", "date": DAY, "force": True, "requested_by": "admin@example.co.jp"}, None
        )
    assert result["status"] == "succeeded"
    item = resources["table"].get_item(Key={"PK": "DAILY", "SK": DAY})["Item"]
    first, second = item["request_history"]
    assert "requested_by" not in first
    assert second["requested_by"] == "admin@example.co.jp" and second["force"] is True
    assert item["trigger"] == "manual" and item["sent_count"] == 2
    assert len(queued(resources)) == 2


@pytest.mark.parametrize(
    "event",
    [
        {"trigger": "manual", "date": DAY, "requested_by": 1},
        {"trigger": "scheduled", "requested_by": "admin@example.co.jp"},
    ],
)
def test_invalid_requested_by_never_acquires(resources, event):
    with pytest.raises(ValueError):
        report.handler(event, None)
    assert not repo.list_days()


@pytest.mark.parametrize("prompt", ["", "試行時の本文"])
def test_preview_worker_converse_combines_snapshot_body_and_fixed_suffix(resources, monkeypatch, prompt):
    import uuid

    from daily_security_report import ai, ai_prompt, levels

    summary = levels.apply_rules({"date": DAY, "findings": [], "sources": {s: {"status": "ok"} for s in SECTIONS}})
    repo.acquire_daily(DAY, trigger="manual")
    repo.finish_daily(DAY, 1, summary=summary)
    configure(ai_enabled=False, ai_prompt="保存済みの別本文")
    preview_id = str(uuid.uuid4())
    repo.create_preview(
        {"preview_id": preview_id, "date": DAY, "prompt": prompt, "model_pin": "jp.anthropic.claude-opus-5-5"}
    )
    runtime = Mock()
    runtime.converse.return_value = {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "test",
                            "name": "submit_verdict",
                            "input": {
                                "sections": [
                                    {"section": section, "level": "ok", "reason": "変化なし"} for section in SECTIONS
                                ],
                                "overall_opinion": "目立った変化はありません。点検を続けます。",
                            },
                        }
                    }
                ]
            }
        },
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }
    monkeypatch.setattr(ai, "_client", lambda service: runtime)
    result = report.handler({"action": "ai_preview", "preview_id": preview_id}, None)
    assert result["status"] == "succeeded"
    expected = prompt + "\n" + ai_prompt.FIXED_SUFFIX if prompt else ai_prompt.DEFAULT_PROMPT
    assert runtime.converse.call_args.kwargs["system"] == [{"text": expected}]
    assert repo.get_preview(preview_id)["result"]["prompt_hash"] == ai_prompt.PROMPT_HASH(expected)
    assert repo.get_settings()["ai_prompt"] == "保存済みの別本文" and not queued(resources)
    assert not [item for item in resources["table"].scan()["Items"] if item["PK"] == "AI_STATE"]


def test_manual_notify_false_saves_without_sending(resources, pipeline, monkeypatch):
    configure(mail_enabled=True, slack_enabled=True, slack_channel_id="D000000001")
    send = Mock()
    monkeypatch.setattr(report.notify, "send", send)
    with pipeline(cloudtrail_event=True):
        result = report.handler({"trigger": "manual", "date": DAY, "notify": False}, None)
    assert result["status"] == "succeeded"
    send.assert_not_called()
    assert not queued(resources)
    run = repo.get_daily(DAY)
    assert run["status"] == "succeeded" and run["summary"]["schema"] == "daily-security-report.daily.v2"
    assert run["notify_channels"] == [] and run["notify_note"] == "送信なし（検証）"
    assert run["sent_count"] == 0 and not run["notify_message_id"] and not run["notified_at"]
    item = resources["table"].get_item(Key={"PK": "DAILY", "SK": DAY})["Item"]
    assert item["request_history"][-1]["notify"] is False


def test_manual_notify_false_failure_sends_no_failure_notice(resources, pipeline, monkeypatch):
    configure(sections={s: s == "guardduty" for s in SECTIONS}, mail_enabled=True)
    send = Mock()
    monkeypatch.setattr(report.notify, "send", send)
    disabled = [s for s in SECTIONS if s != "guardduty"]
    with pipeline(disabled=disabled, failed=["guardduty"]), pytest.raises(RuntimeError):
        report.handler({"trigger": "manual", "date": DAY, "notify": False}, None)
    send.assert_not_called()
    run = repo.get_daily(DAY)
    assert run["status"] == "failed" and run["notify_note"] == "送信なし（検証）" and run["notify_channels"] == []


def test_manual_notify_true_is_the_default_and_recorded_only_when_false(resources, pipeline):
    configure(mail_enabled=True)
    with pipeline():
        report.handler({"trigger": "manual", "date": DAY, "notify": True}, None)
    assert len(queued(resources)) == 1
    item = resources["table"].get_item(Key={"PK": "DAILY", "SK": DAY})["Item"]
    assert "notify" not in item["request_history"][-1]


def test_sources_keep_metric_hourly_and_guardduty_detection_details(resources, monkeypatch):
    from daily_security_report.settings import default_settings

    settings = default_settings()
    settings["sections"] = {s: s in ("guardduty", "metrics") for s in SECTIONS}
    hourly = {"lambda.errors": [1] * 24}
    metrics = {"status": "ok", "counters": {"lambda.errors": 24}, "hourly": hourly, "missing_targets": []}
    guard = {
        "status": "ok",
        "counters": {"guardduty.new.low": 62},
        "red": [],
        "red_count": 0,
        "red_counts": {"guardduty": 0},
        "finding_counts": {"critical": 0, "warning": 0},
        "details": {"findings": [{"id": f"f{i}", "severity": 2} for i in range(50)]},
        "details_truncated": {"findings": 12},
        "regions": {"ap-northeast-1": "ok"},
    }
    monkeypatch.setattr(report, "_client", lambda *a: object())
    monkeypatch.setattr(report.metrics_summary, "collect", lambda *a: metrics)
    monkeypatch.setattr(report.guardduty_summary, "collect", lambda *a: guard)
    sources = report._aggregate(DAY, settings, [], 1)["sources"]
    assert sources["metrics"]["hourly"] == hourly
    assert len(sources["guardduty"]["details"]["findings"]) == 50
    assert sources["guardduty"]["details_truncated"] == {"findings": 12}


def test_insights_pieces_use_the_log_groups_collect_queried(resources, monkeypatch):
    from daily_security_report.settings import default_settings

    class Logs:
        def __init__(self):
            self.ids = iter(range(10))

        def start_query(self, **params):
            return {"queryId": f"q{next(self.ids)}"}

        def get_query_results(self, queryId, **params):
            row = {"src": "198.51.100.20", "cc": "US", "cf": "false", "count": "3"}
            rows = [[{"field": k, "value": v} for k, v in row.items()]] if queryId == "q0" else []
            return {"status": "Complete", "results": rows}

        def stop_query(self, **params):
            return {}

    real = report.insights_queries.collect
    groups = {"alarmhuba": "/given/alarmhuba", "gps": "/given/gps"}
    monkeypatch.setattr(report.insights_queries, "collect", lambda *a, **k: real(*a, log_groups=groups, **k))
    result = report._insights(DAY, Logs(), default_settings(), timeout=60)
    assert result["status"] == "ok"
    direct = next(r for r in result["findings"] if r["code"] == "DIRECT")
    assert direct["where"] == "CloudWatch Logs /given/alarmhuba"


def test_actor_findings_are_placed_in_cloudtrail(resources, monkeypatch):
    from daily_security_report import levels
    from daily_security_report.settings import default_settings

    settings = default_settings()
    settings["sections"] = {s: s == "cloudtrail" for s in SECTIONS}
    row = levels.finding("cloudtrail", "NEW_ACTOR", "actor 198.51.100.1（US）Event")
    monkeypatch.setattr(report, "_client", lambda *a: object())
    monkeypatch.setattr(report.cloudtrail_summary, "collect", lambda *a: {"status": "ok", "observed_actors": []})
    monkeypatch.setattr(
        report.actors,
        "update",
        lambda *a, **k: ({"status": "ok", "dormant": [], "new": [], "external": []}, [row]),
    )
    summary = report._aggregate(DAY, settings, [], 1)
    assert next(r for r in summary["findings"] if r["code"] == "NEW_ACTOR")["where"] == "CloudTrail"
    assert "where" not in row


@pytest.fixture
def log_request(resources):
    from daily_security_report import log_queries

    target = {"function": "example-fn"}
    query_id = log_queries.query_id(DAY, "lambda", target)
    repo.put_log_query(
        DAY, query_id, {"status": "running", "kind": "lambda", "target": target, "requested_by": "a@example.co.jp"}
    )
    return {
        "trigger": "logs",
        "date": DAY,
        "query_id": query_id,
        "kind": "lambda",
        "target": target,
        "requested_by": "a@example.co.jp",
    }


@pytest.mark.parametrize("failure", [False, True])
def test_logs_trigger_runs_query_and_saves_result(resources, log_request, monkeypatch, failure):
    from daily_security_report import log_queries

    result = (
        {"error": "TIMEOUT"}
        if failure
        else {"source": "x", "range": {}, "rows": [{"time": "t", "text": "e"}], "truncated": False}
    )
    hook = Mock(return_value=result)
    monkeypatch.setattr(log_queries, "run", hook)
    monkeypatch.setattr(report, "_aggregate", Mock(side_effect=AssertionError("must not collect")))
    context = Mock()
    context.get_remaining_time_in_millis.return_value = 300_000
    response = report.handler(log_request, context)
    assert response == {"status": "failed" if failure else "succeeded", "query_id": log_request["query_id"]}
    saved = repo.get_log_query(DAY, log_request["query_id"])
    assert saved["status"] == response["status"] and saved["started_at"] and saved["finished_at"]
    if failure:
        assert saved["error"] == "TIMEOUT" and "result" not in saved
    else:
        assert saved["result"] == result and "error" not in saved
    args, kwargs = hook.call_args
    assert args[:3] == ("lambda", {"function": "example-fn"}, DAY) and args[3] is report._client
    assert kwargs["budget"] == 150
    assert not queued(resources)
    assert {i["PK"] for i in resources["table"].scan()["Items"]} == {"LOGS"}


def test_logs_trigger_skips_when_not_running_and_rejects_bad_payload(resources, log_request, monkeypatch):
    from daily_security_report import log_queries

    hook = Mock(return_value={"rows": []})
    monkeypatch.setattr(log_queries, "run", hook)
    repo.update_log_query(DAY, log_request["query_id"], {"status": "succeeded", "result": {"rows": []}})
    assert report.handler(log_request, None)["status"] == "skipped"
    missing = {**log_request, "target": {"function": "other-fn"}}
    missing["query_id"] = log_queries.query_id(DAY, "lambda", missing["target"])
    assert report.handler(missing, None)["status"] == "skipped"
    hook.assert_not_called()
    for broken in (
        {**log_request, "query_id": "mismatch"},
        {**log_request, "kind": "unknown"},
        {**log_request, "target": {"function": "bad name"}},
        {**log_request, "date": "2026-13-01"},
        {**log_request, "date": 5},
        {**log_request, "requested_by": 5},
    ):
        with pytest.raises(ValueError):
            report.handler(broken, None)
    hook.assert_not_called()


def test_logs_trigger_exception_is_saved_as_failed(resources, log_request, monkeypatch):
    from daily_security_report import log_queries

    monkeypatch.setattr(log_queries, "run", Mock(side_effect=RuntimeError("boom")))
    assert report.handler(log_request, None)["status"] == "failed"
    saved = repo.get_log_query(DAY, log_request["query_id"])
    assert saved["status"] == "failed" and saved["error"] == "RuntimeError"


def test_s3_section_reads_cloudwatch_of_each_detector_region_into_the_summary(resources, pipeline):
    camera = {
        "objects": {"2026-10-13": 10_000, DAY: 4_000},
        "size": {"StandardStorage": {"2026-10-13": 5 * GIB, DAY: 2 * GIB}},
    }
    west = {"objects": {"2026-10-13": 10, DAY: 10}, "size": {"StandardStorage": {"2026-10-13": GIB, DAY: GIB}}}
    with pipeline(s3={"ap-northeast-1": {"example-camera-s3": camera}, "us-west-2": {"west-s3": west}}) as run:
        report.handler({"trigger": "manual", "date": DAY}, None)
    summary = repo.get_daily(DAY)["summary"]
    assert run["s3_calls"] == [{"regions": REGIONS, "history": []}]
    source = summary["sources"]["s3"]
    assert {k: source[k] for k in ("status", "regions", "buckets", "metrics_buckets", "truncated")} == {
        "status": "ok",
        "regions": REGIONS,
        "buckets": 2,
        "metrics_buckets": 0,
        "truncated": False,
    }
    assert source["latest"] == {
        "example-camera-s3": {
            "objects": 4_000,
            "size": 2 * GIB,
            "measured_at": f"{DAY}T00:00:00+00:00",
            "region": "ap-northeast-1",
        },
        "west-s3": {"objects": 10, "size": GIB, "measured_at": f"{DAY}T00:00:00+00:00", "region": "us-west-2"},
    }
    assert summary["counters"]["s3.buckets"] == 2 and summary["counters"]["s3.objects"] == 4_010
    assert [(r["bucket"], r["region"]) for r in summary["top"]["s3_buckets"]] == [
        ("example-camera-s3", "ap-northeast-1"),
        ("west-s3", "us-west-2"),
    ]
    [finding] = [f for f in summary["findings"] if f["section"] == "s3"]
    assert (finding["code"], finding["level"], finding["key"]) == (
        "S3_MASS_DELETE",
        "critical",
        "s3.bucket.example-camera-s3",
    )
    assert summary["rule_levels"]["s3"] == "critical" and summary["level"] == "critical"


def test_s3_section_off_is_not_collected(resources, pipeline):
    configure(sections={name: name != "s3" for name in SECTIONS})
    bucket = {"objects": {DAY: 1}, "size": {"StandardStorage": {DAY: 1}}}
    with pipeline(disabled=["s3"], s3={"ap-northeast-1": {"example-camera-s3": bucket}}) as run:
        report.handler({"trigger": "manual", "date": DAY}, None)
    summary = repo.get_daily(DAY)["summary"]
    assert run["s3_calls"] == []
    assert summary["sources"]["s3"] == {"status": "disabled"} and summary["levels"]["s3"] == "ok"
    assert not [key for key in summary["counters"] if key.startswith("s3.")]
    assert "s3_buckets" not in summary["top"] and not [f for f in summary["findings"] if f["section"] == "s3"]
