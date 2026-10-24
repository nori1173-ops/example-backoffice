from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber
from daily_security_report import levels
from daily_security_report.insights_queries import QUERIES, build_query, collect, summarize_results

DAY = "2026-10-14"


def aws_rows(rows):
    return [[{"field": key, "value": str(value)} for key, value in row.items()] for row in rows]


def test_query_text_snapshot():
    fixture = Path(__file__).parents[1] / "fixtures" / "insights-queries.txt"
    assert "\n\n".join(f"{name}\n{text}" for name, text in QUERIES.items()) + "\n" == fixture.read_text()


def test_query_path_regex_and_request_ids_are_escaped_bounded_and_newest_first():
    ids = [{"rid": f"00000000-0000-0000-0000-{i:012}", "latest": i, "count": 1} for i in range(300)]
    q4 = build_query("Q4", request_rows=ids)
    assert len(q4) <= 10000
    assert "000000000299" in q4 and "000000000099" not in q4
    assert q4.count("00000000-0000-0000-0000-") == 200
    assert r"\/admin" in build_query("Q2", admin_path_regex=r"^/api/admin(?:/|$)")
    assert '"bad\\"id"' in build_query("Q4", request_rows=[{"rid": 'bad"id'}])


def test_result_formatting_country_fallback_office_exclusions_denied_gps():
    exclusions = [
        {"kind": "ip", "value": ip, "category": category, "scope": ["all"], "expires_at": ""}
        for ip, category in [("203.0.113.1", "office"), ("198.51.100.9", "")]
    ]
    results = {
        "Q1": [
            {"src": "203.0.113.1", "cc": "US", "cf": "", "count": 4},
            {"src": "203.0.113.2", "cc": "JP", "cf": "cf-id", "count": 2},
            {"src": "198.51.100.9", "cc": "US", "cf": "", "count": 8},
        ],
        "Q2": [
            {"src": "203.0.113.1", "path": "/api/admin", "count": 4},
            {"src": "203.0.113.2", "cc": "JP", "path": "/api/admin", "count": 2},
        ],
        "Q3": [{"rid": "r1", "status": "403", "count": 1}],
        "Q4": [{"src": "203.0.113.2", "path": "/", "count": 1}],
        "Q5": [
            {"src": "203.0.113.3", "method": "POST", "count": 3},
            {"src": "198.51.100.9", "method": "POST", "count": 8},
        ],
    }
    result = summarize_results(results, DAY, {"exclusions": exclusions}, gps_rejected=5)
    assert result["counters"]["alarmhuba.requests"] == 6
    assert result["counters"]["alarmhuba.direct"] == 4
    assert result["counters"]["alarmhuba.admin_external"] == 2
    assert result["counters"]["alarmhuba.denied"] == 1
    assert result["top"]["alarmhuba_statuses"] == [{"status": "403", "count": 1}]
    assert result["seen"]["alarmhuba_countries"] == ["JP"]
    assert result["seen"]["alarmhuba_admin_ips"] == ["203.0.113.2"]
    assert result["counters"]["gps.received"] == 3
    assert result["counters"]["gps.rejected"] == 5
    assert result["excluded"] == {"insights": 8, "gps": 8}
    assert result["gps_rejected_source"] == "alb.4xx"


def test_10000_row_limit_and_seen_top_red_caps():
    results = {"Q1": [{"src": f"2001:db8::{i:x}", "cc": "US", "cf": "", "count": 1} for i in range(10000)]}
    result = summarize_results(results, DAY)
    assert result["truncated"] == ["Q1"]
    assert len(result["seen"]["alarmhuba_countries"]) == 1
    assert len(result["top"]["alarmhuba_ips"]) == 50
    assert len(result["red"]) <= 100
    assert result["counters"]["alarmhuba.requests"] == 10000


def test_q3_zero_skips_q4_and_starts_all_queries_before_polling():
    client = boto3.client("logs", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        for q, group in (
            ("Q1", "/test/alarmhuba"),
            ("Q2", "/test/alarmhuba"),
            ("Q3", "/test/alarmhuba"),
            ("Q5", "/test/gps"),
        ):
            stub.add_response(
                "start_query",
                {"queryId": q},
                {
                    "logGroupName": group,
                    "startTime": 1791903600,
                    "endTime": 1791989999,
                    "queryString": build_query(q),
                    "limit": 10000,
                },
            )
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("get_query_results", {"status": "Complete", "results": []}, {"queryId": q})
        result = collect(DAY, client, poll_interval=0)
        assert result["status"] == "ok"
        assert result["query_ids"].keys() == {"Q1", "Q2", "Q3", "Q5"}
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("status", ["Failed", "Cancelled", "Timeout", "Unknown"])
def test_failed_query_is_failed_source_and_other_queries_are_stopped(status):
    client = boto3.client("logs", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("start_query", {"queryId": q})
        stub.add_response("get_query_results", {"status": status}, {"queryId": "Q1"})
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("stop_query", {"success": True}, {"queryId": q})
        result = collect(DAY, client, poll_interval=0)
        assert result["status"] == "failed"
        assert status in result["error"]
        stub.assert_no_pending_responses()


def test_polling_timeout_stops_queries():
    client = boto3.client("logs", region_name="ap-northeast-1")
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    with Stubber(client) as stub:
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("start_query", {"queryId": q})
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("get_query_results", {"status": "Running"}, {"queryId": q})
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("stop_query", {"success": True}, {"queryId": q})
        result = collect(DAY, client, timeout=2, sleep=sleep, monotonic=lambda: now[0])
        assert result["status"] == "failed" and result["error"] == "TIMEOUT"
        assert now[0] == 2
        stub.assert_no_pending_responses()


def test_no_budget_starts_no_query():
    client = boto3.client("logs", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        result = collect(DAY, client, timeout=0)
        assert result["status"] == "failed" and result["error"] == "TIMEOUT"
        stub.assert_no_pending_responses()


def test_snapshot_has_five_queries():
    assert list(QUERIES) == ["Q1", "Q2", "Q3", "Q4", "Q5"]


def test_request_ids_deduplicate_and_bound_expanded_query_length():
    rows = [
        {"rid": "z" * 9000, "latest": "2026-10-14T00:00:00Z"},
        {"rid": "z" * 9000, "latest": "2026-10-13T00:00:00Z"},
        {"rid": "y" * 9000, "latest": "2026-10-12T00:00:00Z"},
    ]
    assert len(build_query("Q4", request_rows=rows)) <= 10000
    with pytest.raises(ValueError):
        build_query("Q2", admin_path_regex="x" * 10000)


def test_office_and_excluded_denied_ips_and_truncated_attribution():
    rules = [
        {"kind": "ip", "value": ip, "scope": ["insights"], "category": category, "expires_at": ""}
        for ip, category in (("203.0.113.1", "office"), ("203.0.113.2", ""))
    ]
    results = {
        "Q3": [{"rid": str(i), "status": "403", "count": 1} for i in range(220)],
        "Q4": [{"src": "203.0.113.1", "count": 1}, {"src": "203.0.113.2", "count": 2}],
        "Q2": [{"src": "203.0.113.2", "count": 1}],
    }
    result = summarize_results(results, DAY, {"exclusions": rules})
    assert result["counters"]["alarmhuba.denied"] == 218
    assert result["seen"]["alarmhuba_denied_ips"] == []
    assert result["request_ids_truncated"]
    assert result["gps_rejected_source"] == "unavailable"
    assert "gps.rejected" not in result["counters"]


def test_q4_starts_after_q3_and_pending_queries_are_polled_until_complete():
    client = boto3.client("logs", region_name="ap-northeast-1")
    request_rows = [{"rid": "r1", "status": "403", "count": 1, "latest": 1}]
    sleeps = []
    with Stubber(client) as stub:
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("start_query", {"queryId": q})
        stub.add_response("get_query_results", {"status": "Running"}, {"queryId": "Q1"})
        stub.add_response("get_query_results", {"status": "Scheduled"}, {"queryId": "Q2"})
        stub.add_response(
            "get_query_results", {"status": "Complete", "results": aws_rows(request_rows)}, {"queryId": "Q3"}
        )
        stub.add_response(
            "start_query",
            {"queryId": "Q4"},
            {
                "logGroupName": "/test/alarmhuba",
                "startTime": 1791903600,
                "endTime": 1791989999,
                "queryString": build_query("Q4", request_rows=request_rows),
                "limit": 10000,
            },
        )
        for q in ("Q5", "Q1", "Q2", "Q4"):
            stub.add_response("get_query_results", {"status": "Complete", "results": []}, {"queryId": q})
        result = collect(DAY, client, sleep=sleeps.append)
        assert result["status"] == "ok" and "Q4" in result["query_ids"]
        assert len(sleeps) == 1 and sleeps[0] <= 2
        stub.assert_no_pending_responses()


def test_start_failure_stops_queries_already_started_even_if_stop_fails():
    client = boto3.client("logs", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        stub.add_response("start_query", {"queryId": "Q1"})
        stub.add_client_error("start_query", "AccessDeniedException")
        stub.add_client_error("stop_query", "InvalidParameterException")
        result = collect(DAY, client)
        assert result["status"] == "failed" and result["error"] == "Q2 AccessDeniedException"
        stub.assert_no_pending_responses()


def test_complete_query_result_pagination_is_read_before_finishing():
    client = boto3.client("logs", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        for q in ("Q1", "Q2", "Q3", "Q5"):
            stub.add_response("start_query", {"queryId": q})
        stub.add_response(
            "get_query_results",
            {
                "status": "Complete",
                "nextToken": "next",
                "results": aws_rows([{"src": "203.0.113.1", "cc": "JP", "cf": "true", "count": 2}]),
            },
            {"queryId": "Q1"},
        )
        stub.add_response(
            "get_query_results",
            {"status": "Complete", "results": aws_rows([{"src": "203.0.113.2", "cc": "JP", "cf": "true", "count": 3}])},
            {"queryId": "Q1", "nextToken": "next"},
        )
        for q in ("Q2", "Q3", "Q5"):
            stub.add_response("get_query_results", {"status": "Complete", "results": []}, {"queryId": q})
        result = collect(DAY, client)
    assert result["status"] == "ok" and result["counters"]["alarmhuba.requests"] == 5


def test_red_rows_carry_count_outside_text():
    results = {
        "Q1": [{"src": "198.51.100.20", "cc": "US", "cf": "", "count": 7}],
        "Q2": [{"src": "198.51.100.21", "cc": "US", "path": "/api/admin", "count": 1}],
    }
    result = summarize_results(results, DAY)
    rows = {r["code"]: r for r in result["red"]}
    assert {k: rows["DIRECT"][k] for k in ("section", "code", "text", "count")} == {
        "section": "insights",
        "code": "DIRECT",
        "text": "198.51.100.20（US）",
        "count": 7,
    }
    assert rows["ADMIN_EXTERNAL"]["text"] == "198.51.100.21（US）/api/admin" and rows["ADMIN_EXTERNAL"]["count"] == 1
    assert {r["code"]: r["count"] for r in levels.from_result(result)} == {"DIRECT": 7, "ADMIN_EXTERNAL": 1}
