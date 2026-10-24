from daily_security_report import (
    baseline,
    cloudtrail_summary,
    guardduty_summary,
    insights_queries,
    instances_summary,
    levels,
    metrics_summary,
)

DAY = "2026-10-14"


def test_empty_collectors_write_zero_counters_and_empty_seen():
    guard = guardduty_summary.summarize_findings([], DAY)
    trail = cloudtrail_summary.summarize_events([], DAY)
    plans = metrics_summary.build_queries(
        {"waf": ["test-acl"], "apigw": ["test-api"], "alb": ["test-alb"], "cloudfront": ["ETEST"]}
    )
    metrics = metrics_summary.summarize_metrics(plans, {})
    insights = insights_queries.summarize_results({}, DAY, gps_rejected=0)
    instances = instances_summary.summarize_instances(DAY, [], [])
    for payload in (guard, trail, metrics, insights):
        assert payload["counters"] and all(v == 0 for v in payload["counters"].values())
        assert all(v == [] for v in payload.get("seen", {}).values())
    assert set(trail["seen"]) | set(insights["seen"]) == set(baseline.SEEN_SOURCES)
    assert all(
        key in metrics["counters"] for key in ("waf.blocked", "apigw.4xx", "alb.5xx", "cloudfront.requests.ETEST")
    )
    assert set(metrics["missing_targets"]) == {"waf:test-acl", "apigw:test-api", "alb:test-alb", "cloudfront:ETEST"}
    assert instances["instances"] == {"ec2": [], "lightsail": [], "new": [], "gone": []}


def test_always_red_codes_and_five_section_contract():
    guard = guardduty_summary.summarize_findings(
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
    trail = cloudtrail_summary.summarize_events(
        [
            {
                "eventTime": "2026-10-14T00:00:00Z",
                "eventSource": "sts.amazonaws.com",
                "eventName": "GetCallerIdentity",
                "userIdentity": {"type": "Root"},
                "sourceIPAddress": "AWS Internal",
            }
        ],
        DAY,
    )
    insights = insights_queries.summarize_results(
        {
            "Q1": [{"src": "203.0.113.4", "cc": "JP", "cf": "false", "count": "1"}],
            "Q2": [{"src": "203.0.113.4", "cc": "JP", "path": "/api/admin", "count": "1"}],
        },
        DAY,
    )
    from daily_security_report.settings import SECTIONS

    payloads = {"guardduty": guard, "cloudtrail": trail, "insights": insights}
    summary = {
        "date": DAY,
        "sources": {key: {"status": "ok"} for key in payloads},
        "counters": {},
        "seen": {},
        "red": [],
        "red_counts": {},
    }
    for payload in payloads.values():
        rows = levels.from_result(payload)
        assert all(r["section"] in SECTIONS for r in rows)
        assert all(r["code"] in baseline.ALWAYS_RED.values() for r in rows)
        for key in ("counters", "seen", "red_counts"):
            summary[key].update(payload.get(key, {}))
        summary["red"].extend(rows)
    assert baseline.compare(summary, [], {})["red_count"] == 4
