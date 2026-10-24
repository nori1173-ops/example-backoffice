import boto3
from botocore.stub import ANY, Stubber
from daily_security_report.metrics_summary import build_queries, collect, summarize_metrics

DAY = "2026-10-14"
TARGETS = {
    "waf": ["demo-acl"],
    "apigw": [{"name": "demo-api", "region": "ap-northeast-1"}],
    "alb": ["app/demo/0123"],
    "cloudfront": [{"id": "EXAMPLECF", "label": "demo"}],
}


def test_query_regions_hourly_metrics_and_search():
    plans = build_queries(TARGETS)
    assert set(plans) == {"ap-northeast-1", "us-east-1"}
    text = str(plans)
    assert "SEARCH" in text and "AWS/Lambda" in text and "Errors" in text
    assert "Country" in text and "VulnerabilityCategory" in text and "Rule" in text
    assert "AWS/CloudFront" in str(plans["us-east-1"])
    assert "AWS/ApiGateway" in str(plans["ap-northeast-1"])
    assert all(q.get("MetricStat", {}).get("Period", 3600) == 3600 for plan in plans.values() for q in plan)


def test_apigw_id_uses_http_api_metric_dimensions_and_names():
    plan = build_queries({"apigw": [{"id": "example-api-id", "label": "HTTP API"}]})["ap-northeast-1"]
    metrics = [query["MetricStat"]["Metric"] for query in plan if "MetricStat" in query]
    assert {metric["MetricName"] for metric in metrics} == {"Count", "4xx", "5xx"}
    assert all(metric["Dimensions"] == [{"Name": "ApiId", "Value": "example-api-id"}] for metric in metrics)


def test_hourly_sums_no_points_and_missing_target_are_distinct():
    plans = build_queries({"apigw": ["demo-api", "missing-api"]})
    queries = plans["ap-northeast-1"]
    count = next(
        q
        for q in queries
        if q.get("MetricStat", {}).get("Metric", {}).get("MetricName") == "Count"
        and q["MetricStat"]["Metric"]["Dimensions"][0]["Value"] == "demo-api"
    )
    data = [{"Id": count["Id"], "Values": [1] * 24, "StatusCode": "Complete"}]
    discovery = next(q for q in queries if q.get("Label") == "exists|apigw|demo-api")
    data += [{"Id": discovery["Id"], "Label": discovery["Label"], "Values": [], "StatusCode": "Complete"}]
    result = summarize_metrics(plans, {"ap-northeast-1": data})
    assert result["counters"]["apigw.requests"] == 24
    assert result["counters"]["apigw.4xx"] == 0
    assert result["missing_targets"] == ["apigw:missing-api"]


def test_waf_breakdowns_do_not_double_count_and_lambda_top_functions():
    plans = build_queries({"waf": ["demo-acl"]})
    waf = next(q for q in plans["us-east-1"] if "BlockedRequests" in q.get("Expression", ""))
    errors = next(q for q in plans["ap-northeast-1"] if "AWS/Lambda" in q.get("Expression", ""))
    result = summarize_metrics(
        plans,
        {
            "us-east-1": [
                {"Id": waf["Id"], "Label": label, "Values": [count], "StatusCode": "Complete"}
                for label, count in [
                    ("waf|demo-acl|||ALL", 20),
                    ("waf|demo-acl|JP||ALL", 8),
                    ("waf|demo-acl||SQLi|ALL", 3),
                    ("waf|demo-acl|||rule-x", 4),
                ]
            ],
            "ap-northeast-1": [
                {"Id": errors["Id"], "Label": f"lambda|function-{i}", "Values": [i] * 24, "StatusCode": "Complete"}
                for i in range(1, 5)
            ],
        },
    )
    assert result["counters"]["waf.blocked"] == 20
    assert result["top"]["waf_countries"][0]["country"] == "JP"
    assert result["top"]["waf_vulnerabilities"][0]["category"] == "SQLi"
    assert result["top"]["waf_rules"][0]["rule"] == "rule-x"
    assert result["counters"]["lambda.errors"] == 240
    assert [r["function"] for r in result["top"]["lambda_errors"]] == [f"function-{i}" for i in (4, 3, 2, 1)]


def test_get_metric_data_pagination_and_api_errors():
    client = boto3.client("cloudwatch", region_name="ap-northeast-1")
    plans = build_queries({})
    params = {"MetricDataQueries": plans["ap-northeast-1"], "StartTime": ANY, "EndTime": ANY}
    with Stubber(client) as stub:
        stub.add_response("get_metric_data", {"MetricDataResults": [], "NextToken": "next"}, params)
        stub.add_response("get_metric_data", {"MetricDataResults": []}, {**params, "NextToken": "next"})
        assert collect(DAY, {"ap-northeast-1": client})["status"] == "ok"
        stub.assert_no_pending_responses()
    with Stubber(client) as stub:
        stub.add_client_error("get_metric_data", "AccessDenied")
        result = collect(DAY, {"ap-northeast-1": client})
    assert result["status"] == "failed"


def test_partial_data_cannot_be_reported_as_zero():
    client = boto3.client("cloudwatch", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        stub.add_response(
            "get_metric_data",
            {"MetricDataResults": [{"Id": "lambda_errors", "Values": [], "StatusCode": "InternalError"}]},
        )
        assert collect(DAY, {"ap-northeast-1": client})["status"] == "failed"


def test_partial_page_is_combined_with_next_page_and_hourly_sum_is_preserved():
    client = boto3.client("cloudwatch", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        stub.add_response(
            "get_metric_data",
            {
                "NextToken": "next",
                "MetricDataResults": [
                    {"Id": "lambda_errors", "Label": "lambda|demo", "Values": [1] * 12, "StatusCode": "PartialData"}
                ],
            },
        )
        stub.add_response(
            "get_metric_data",
            {
                "MetricDataResults": [
                    {"Id": "lambda_errors", "Label": "lambda|demo", "Values": [2] * 12, "StatusCode": "Complete"}
                ]
            },
        )
        result = collect(DAY, {"ap-northeast-1": client})
        assert result["status"] == "ok"
        assert result["counters"]["lambda.errors"] == 36


def test_search_limit_is_failed_instead_of_silent_partial_total():
    client = boto3.client("cloudwatch", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        stub.add_response(
            "get_metric_data",
            {"MetricDataResults": [], "Messages": [{"Code": "MaxMetricsExceeded", "Value": "too many series"}]},
        )
        assert collect(DAY, {"ap-northeast-1": client})["status"] == "failed"


def test_waf_multi_dimension_series_are_kept_without_adding_them_to_country_total():
    plans = build_queries({"waf": ["demo-acl"]})
    query = next(q for q in plans["us-east-1"] if "BlockedRequests" in q.get("Expression", ""))
    result = summarize_metrics(
        plans,
        {
            "us-east-1": [
                {"Id": query["Id"], "Label": "waf|demo-acl|JP||ALL", "Values": [8]},
                {"Id": query["Id"], "Label": "waf|demo-acl|JP||rule-x", "Values": [3]},
            ]
        },
    )
    assert result["top"]["waf_countries"][0]["blocked"] == 8
    assert result["top"]["waf_dimensions"][0]["country"] == "JP"


def test_standard_alb_and_cloudfront_metrics_and_region_override():
    targets = {
        "waf": [{"id": "regional-acl", "region": "ap-northeast-1"}],
        "alb": [{"id": "app/demo/0123", "name": "demo"}],
        "cloudfront": ["EXAMPLECF"],
    }
    plans = build_queries(targets)
    results = {}
    for region, queries in plans.items():
        results[region] = [{"Id": query["Id"], "Values": [2] * 24} for query in queries if "MetricStat" in query]
    result = summarize_metrics(plans, results)
    assert result["counters"]["alb.4xx"] == 96
    assert result["counters"]["alb.5xx"] == 96
    assert result["target_counters"]["alb:ap-northeast-1:app/demo/0123"]["4xx"] == 96
    assert result["counters"]["cloudfront.requests.EXAMPLECF"] == 48
    assert result["missing_targets"] == ["waf:regional-acl"]


def test_unknown_response_id_is_ignored_and_bad_waf_label_is_rejected():
    import pytest

    plans = build_queries({"waf": ["demo-acl"]})
    assert (
        summarize_metrics(plans, {"us-east-1": [{"Id": "unrequested", "Values": [20]}]})["counters"]["waf.blocked"] == 0
    )
    query = next(q for q in plans["us-east-1"] if "BlockedRequests" in q.get("Expression", ""))
    with pytest.raises(ValueError):
        summarize_metrics(plans, {"us-east-1": [{"Id": query["Id"], "Label": "wrong", "Values": []}]})
