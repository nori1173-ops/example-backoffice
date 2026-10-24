"""日次詳細の用語集（glossary）と、保存時の時間帯（hourly）の上限。"""

import json

from daily_security_report import levels, repo
from daily_security_report_api import app as api

DAY = "2026-10-16"


def call(http_event, path):
    result = api.handler(http_event(path), None)
    return result["statusCode"], json.loads(result["body"])


def configure(**patch):
    current = repo.get_settings()
    return repo.put_settings({**current, **patch}, current["version"], "test", "test@example.co.jp")


def save(summary):
    repo.acquire_daily(DAY, trigger="manual")
    repo.finish_daily(DAY, 1, summary=summary)


def test_daily_detail_returns_glossary_for_counters_and_comparisons(resources, http_event):
    configure(metric_targets={"cloudfront": [{"id": "EXAMPLECF", "label": "配信サイト"}]})
    summary = levels.apply_rules(
        {
            "schema": "daily-security-report.daily.v2",
            "date": DAY,
            "findings": [],
            "counters": {"waf.blocked": 3, "cloudfront.requests.EXAMPLECF": 4, "custom.counter": 1},
            "comparisons": {"instances.ec2.i-0example.cpu_avg": {"value": 1, "baseline": 1}},
            "ai": {"status": "skipped"},
        }
    )
    save(summary)
    status, body = call(http_event, f"/days/{DAY}")
    assert status == 200
    glossary = body["data"]["glossary"]
    assert set(glossary) == {
        "waf.blocked",
        "cloudfront.requests.EXAMPLECF",
        "custom.counter",
        "instances.ec2.i-0example.cpu_avg",
    }
    assert glossary["waf.blocked"] == {"label": "WAF 遮断", "description": glossary["waf.blocked"]["description"]}
    assert glossary["waf.blocked"]["description"]
    assert glossary["cloudfront.requests.EXAMPLECF"]["label"] == "CloudFront 配信サイト"
    assert glossary["custom.counter"] == {"label": "custom.counter", "description": ""}
    assert glossary["instances.ec2.i-0example.cpu_avg"]["label"] == "i-0example CPU 平均"


def test_running_daily_without_summary_has_empty_glossary(resources, http_event):
    repo.acquire_daily(DAY, trigger="manual")
    status, body = call(http_event, f"/days/{DAY}")
    assert status == 200 and body["data"]["glossary"] == {}


def test_hourly_is_capped_at_60_counters_of_24_values(resources):
    hourly = {f"cloudfront.requests.E{i:03}": [1] * 30 for i in range(70)}
    summary = levels.apply_rules(
        {
            "schema": "daily-security-report.daily.v2",
            "date": DAY,
            "findings": [],
            "sources": {"metrics": {"status": "ok", "hourly": hourly}},
            "ai": {"status": "skipped"},
        }
    )
    save(summary)
    saved = repo.get_daily(DAY)["summary"]["sources"]["metrics"]
    assert len(saved["hourly"]) == 60 and saved["hourly_truncated"] is True
    assert all(len(values) == 24 for values in saved["hourly"].values())
    assert list(saved["hourly"]) == sorted(hourly)[:60]
    assert len(summary["sources"]["metrics"]["hourly"]) == 70


def test_hourly_within_limit_is_saved_as_is(resources):
    hourly = {"lambda.errors": list(range(24))}
    summary = levels.apply_rules(
        {
            "schema": "daily-security-report.daily.v2",
            "date": DAY,
            "findings": [],
            "sources": {"metrics": {"status": "ok", "hourly": hourly}},
            "top": {
                "lambda_errors": [
                    {
                        "function": f"fn-{i}",
                        "count": 1,
                        "hourly": [1] * 24,
                        "where": f"CloudWatch Logs /aws/lambda/fn-{i}",
                        "ref": {"kind": "lambda", "function": f"fn-{i}"},
                    }
                    for i in range(20)
                ]
            },
            "ai": {"status": "skipped"},
        }
    )
    save(summary)
    saved = repo.get_daily(DAY)["summary"]
    assert saved["sources"]["metrics"]["hourly"] == hourly
    assert "hourly_truncated" not in saved["sources"]["metrics"]
    assert len(saved["top"]["lambda_errors"]) == 20 and not saved.get("top_truncated")


def test_daily_detail_glossary_names_s3_bucket_keys(resources, http_event):
    summary = levels.apply_rules(
        {
            "schema": "daily-security-report.daily.v2",
            "date": DAY,
            "findings": [],
            "counters": {"s3.buckets": 1},
            "top": {"s3_buckets": [{"bucket": "example-camera-s3", "key": "s3.bucket.example-camera-s3"}]},
            "ai": {"status": "skipped"},
        }
    )
    save(summary)
    status, body = call(http_event, f"/days/{DAY}")
    assert status == 200
    glossary = body["data"]["glossary"]
    assert set(glossary) == {"s3.buckets", "s3.bucket.example-camera-s3"}
    assert glossary["s3.bucket.example-camera-s3"]["label"] == "S3 バケット example-camera-s3"
