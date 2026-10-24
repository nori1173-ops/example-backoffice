from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import Stubber
from daily_security_report import cloudtrail_summary, guardduty_summary, insights_queries, metrics_summary, report
from daily_security_report.baseline import compare
from daily_security_report.settings import SettingsValidationError, default_settings, update_settings

DAY = "2026-10-14"


def test_guardduty_created_today_updated_tomorrow_is_new_and_query_has_no_upper_bound():
    row = {
        "Id": "cross-midnight",
        "AccountId": "000000000000",
        "Arn": "arn:aws:guardduty:us-east-1:000000000000:detector/demo/finding/demo",
        "Type": "Test:Finding",
        "Resource": {"ResourceType": "Instance"},
        "SchemaVersion": "2.0",
        "Region": "us-east-1",
        "Severity": 8.0,
        "Title": "Cross midnight",
        "CreatedAt": "2026-10-14T14:59:59Z",
        "UpdatedAt": "2026-10-14T15:30:00Z",
    }
    client = boto3.client("guardduty", region_name="us-east-1")
    criteria = {
        "Criterion": {"updatedAt": {"GreaterThanOrEqual": 1791903600000}, "service.archived": {"Equals": ["false"]}}
    }
    with Stubber(client) as stub:
        stub.add_response(
            "list_findings",
            {"FindingIds": [row["Id"]]},
            {"DetectorId": "demo", "MaxResults": 50, "FindingCriteria": criteria},
        )
        stub.add_response("get_findings", {"Findings": [row]})
        result = guardduty_summary.collect(DAY, {"us-east-1": client}, detectors={"us-east-1": "demo"})
        assert result["status"] == "ok"
        assert result["counters"]["guardduty.new.high"] == 1
        stub.assert_no_pending_responses()
    assert guardduty_summary.summarize_findings([row], "2026-10-15")["counters"]["guardduty.continuing"] == 1
    assert (
        guardduty_summary.summarize_findings([{**row, "CreatedAt": "2026-10-12T00:00:00Z"}], DAY)["counters"][
            "guardduty.continuing"
        ]
        == 0
    )


def test_waf_and_cloudfront_search_match_actual_dimension_sets():
    plans = metrics_summary.build_queries({"waf": ["demo-acl"], "cloudfront": ["EXAMPLECF"]})
    expressions = [q["Expression"] for q in plans["us-east-1"] if "Expression" in q]
    for metric in ("AllowedRequests", "BlockedRequests"):
        assert any(
            f'{{AWS/WAFV2,Rule,WebACL}} MetricName="{metric}" WebACL="demo-acl" Rule="ALL"' in e for e in expressions
        )
        for dimension in ("Country", "VulnerabilityCategory"):
            assert any(
                f'{{AWS/WAFV2,{dimension},WebACL}} MetricName="{metric}" WebACL="demo-acl"' in e for e in expressions
            )
    assert len([e for e in expressions if "AWS/WAFV2" in e]) == 6
    assert any(
        '{AWS/CloudFront,DistributionId,Region} DistributionId="EXAMPLECF" Region="Global"' in e for e in expressions
    )


@pytest.mark.parametrize(
    "entry", ["demo", {"name": "demo"}, {"id": "demo"}, {"id": "app/demo/XYZ"}, {"id": "app/demo/123/extra"}]
)
def test_alb_requires_dimension_id(entry):
    with pytest.raises(SettingsValidationError, match="alb"):
        update_settings(default_settings(), {"version": 0, "metric_targets": {"alb": [entry]}})


def test_alb_uses_id_even_with_display_name_and_gps_label():
    target = {"id": "app/demo/0123456789abcdef", "name": "display-name", "label": "gps"}
    updated = update_settings(default_settings(), {"version": 0, "metric_targets": {"alb": [target]}})
    queries = metrics_summary.build_queries(updated["metric_targets"])["ap-northeast-1"]
    assert all(
        q["MetricStat"]["Metric"]["Dimensions"] == [{"Name": "LoadBalancer", "Value": target["id"]}]
        for q in queries
        if "MetricStat" in q
    )


@pytest.mark.parametrize("source", ["cloudformation.amazonaws.com", "lambda.amazonaws.com", "AWS Internal"])
@pytest.mark.parametrize(
    "identity",
    [{"type": "IAMUser"}, {"type": "AWSService"}, {"type": "AssumedRole", "invokedBy": "cloudformation.amazonaws.com"}],
)
def test_service_source_is_separate_from_human_and_automatic_and_not_foreign(source, identity):
    event = {
        "eventTime": "2026-10-14T00:00:00Z",
        "eventName": "CreateRole",
        "eventSource": "iam.amazonaws.com",
        "readOnly": False,
        "sourceIPAddress": source,
        "userIdentity": identity,
    }
    assert cloudtrail_summary.classify_actor(event) == "aws_service"
    result = cloudtrail_summary.summarize_events([event], DAY)
    assert result["counters"]["cloudtrail.iam_change"] == 1
    assert result["findings"] == []
    assert not result["seen"]["cloudtrail_human_ips"]
    row = result["details"]["iam_changes"][0]
    assert row["actor"] == "aws_service" and row["cc"] == "AWS サービス"


def test_insights_normalizes_ids_before_grouping_paths_without_mutating_input():
    paths = [
        "/api/admin/1234/LoggerD-abc123/user@example.test/00000000-0000-0000-0000-000000000001",
        "/api/admin/56789/LoggerD-xyz456/other@example.test/00000000-0000-0000-0000-000000000002",
    ]
    rows = [{"src": "203.0.113.1", "cc": "JP", "path": path, "count": 3} for path in paths]
    rows += [
        {"src": "203.0.113.2", "cc": "JP", "path": paths[0], "count": 2},
        {"src": "203.0.113.1", "cc": "US", "path": paths[0], "count": 1},
    ]
    data = {"Q2": rows, "Q4": deepcopy(rows)}
    before = deepcopy(data)
    result = insights_queries.summarize_results(data, DAY)
    expected = "/api/admin/{id}/{id}/{id}/{id}"
    for key in ("alarmhuba_admin", "alarmhuba_denied"):
        details = result["details"][key]
        assert len(details) == 3
        row = next(r for r in details if r["ip"] == "203.0.113.1" and r["cc"] == "JP")
        assert {k: row[k] for k in ("ip", "cc", "path", "count")} == {
            "ip": "203.0.113.1",
            "cc": "JP",
            "path": expected,
            "count": 6,
        }
    assert result["red_count"] == 3 and all(expected in r["text"] for r in result["red"])
    assert result["counters"]["alarmhuba.admin_external"] == 9
    assert data == before


def test_insights_q4_shares_deadline_with_q3():
    now = [0.0]
    client = Mock()
    client.start_query.side_effect = lambda **kw: {
        "queryId": "Q4"
        if "filter rid in" in kw["queryString"]
        else "Q3"
        if "latest(@timestamp)" in kw["queryString"]
        else "other"
    }

    def results(queryId):
        if queryId == "Q3":
            now[0] = 8.0
            return {"status": "Complete", "results": [[{"field": "rid", "value": "r1"}]]}
        return {"status": "Running"} if queryId == "Q4" else {"status": "Complete", "results": []}

    client.get_query_results.side_effect = results
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    result = insights_queries.collect(DAY, client, timeout=10, monotonic=lambda: now[0], sleep=sleep)
    assert result["status"] == "failed" and result["error"] == "TIMEOUT"
    assert now[0] == 10 and sum(slept) == 2
    client.stop_query.assert_called_once_with(queryId="Q4")
    assert all(c.kwargs["endTime"] == 1791989999 for c in client.start_query.call_args_list)


def test_count_sums_and_report_and_baseline_do_not_show_integer_decimals():
    plans = metrics_summary.build_queries({})
    metrics = metrics_summary.summarize_metrics(
        plans, {"ap-northeast-1": [{"Id": "lambda_errors", "Values": [1.0, 2.0], "Label": "lambda|demo"}]}
    )
    assert type(metrics["counters"]["lambda.errors"]) is int
    assert type(metrics["top"]["lambda_errors"][0]["count"]) is int
    settings = default_settings()
    summary = {
        "date": DAY,
        "sources": {s: {"status": "ok"} for s in settings["sections"]},
        "counters": {"waf.blocked": 40.0, "cloudfront.requests.EXAMPLE": 1526185.0},
        "red": [],
    }
    history = [
        {
            **summary,
            "date": (datetime.fromisoformat(DAY) - timedelta(days=i)).date().isoformat(),
            "counters": {"waf.blocked": 10.0},
        }
        for i in range(1, 8)
    ]
    result = compare(summary, history, settings)
    assert "40.0" not in result["red"][0]["text"] and "10.00" not in result["red"][0]["text"]
    payload = report.build_report(result, settings)
    assert "40.0" not in payload["slack"]["text"] and "10.00" not in payload["email"]["text"]
    assert type(result["red"][0]["count"]) is int and result["red"][0]["count"] == 40
    assert "waf.blocked（基準 10 の 3 倍超）" in payload["slack"]["text"]
    assert result["counters"]["cloudfront.requests.EXAMPLE"] == 1526185
