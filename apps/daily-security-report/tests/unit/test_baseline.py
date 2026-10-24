from datetime import date, timedelta

import pytest
from daily_security_report.baseline import compare
from daily_security_report.settings import default_settings


def summary(value=10, day="2026-10-14", status="ok"):
    return {
        "date": day,
        "sources": {key: {"status": status} for key in default_settings()["sections"]},
        "counters": {"waf.blocked": value},
        "seen": {"cloudtrail_human_ips": ["203.0.113.1"]},
        "red": [],
    }


def history(value=10):
    return [summary(value, (date(2026, 10, 14) - timedelta(days=i)).isoformat()) for i in range(1, 8)]


@pytest.mark.parametrize("baseline,today,expected", [(10, 30, False), (10, 31, True), (0, 9, False), (0, 10, True)])
def test_ratio_and_minimum_boundaries(baseline, today, expected):
    result = compare(summary(today), history(baseline), default_settings())
    assert bool(result["red"]) is expected
    assert result["comparisons"]["waf.blocked"]["baseline"] == baseline


def test_missing_failed_disabled_days_are_not_zero():
    days = history()
    days[0]["sources"]["metrics"]["status"] = "failed"
    days[1]["sources"]["metrics"]["status"] = "disabled"
    del days[2]["counters"]["waf.blocked"]
    result = compare(summary(31), days, default_settings())
    assert result["comparisons"]["waf.blocked"]["days"] == 4
    assert result["comparisons"]["waf.blocked"]["baseline"] == 10


def test_insufficient_baseline_and_history_window():
    days = history()[:2] + [summary(1, "2026-10-01"), summary(1, "2026-10-14"), summary(1, "2026-10-15")]
    current = summary(100)
    current["seen"]["cloudtrail_human_ips"] = ["203.0.113.2"]
    result = compare(current, days, default_settings())
    assert not result["red"]
    assert "waf.blocked" in result["no_baseline"]
    assert result["comparisons"]["waf.blocked"]["label"] == "基準なし"


def test_first_seen_sets_only_supported_keys():
    current = summary()
    current["seen"] = {"cloudtrail_human_ips": ["203.0.113.2"], "alarmhuba_countries": ["ZZ"], "gps_ips": ["203.0.113.3"]}
    result = compare(current, history(), default_settings())
    assert {row["code"] for row in result["red"]} == {"FIRST_SEEN"}
    assert len(result["red"]) == 1  # missing historical alarmhuba set is not a successful baseline


def test_disabled_current_source_never_red_and_always_red_needs_no_baseline():
    current = summary(100)
    current["sources"]["metrics"]["status"] = "disabled"
    current["counters"]["cloudtrail.root"] = 1
    result = compare(current, [], default_settings())
    assert [r["code"] for r in result["red"]] == ["ROOT"]


def test_subject_access_denied_and_instance_ratio():
    current = summary()
    current["top"] = {"access_denied_principals": [{"principal": "test-user", "count": 21}]}
    current["instances"] = {"ec2": [{"id": "i-test", "name": "test", "cpu_avg": 0.4, "net_out": 4}], "lightsail": []}
    days = history()
    for day in days:
        day["top"] = {"access_denied_principals": [{"principal": "test-user", "count": 5}]}
        day["instances"] = {"ec2": [{"id": "i-test", "cpu_avg": 0.1, "net_out": 1}], "lightsail": []}
    result = compare(current, days, default_settings())
    assert {r["code"] for r in result["red"]} == {"ACCESS_DENIED", "INSTANCE_RATIO"}
    assert len(result["red"]) == 3
    settings = default_settings()
    settings["exclusions"] = [
        {"kind": "instance", "value": "i-test", "scope": ["instances"], "category": "", "expires_at": ""}
    ]
    assert len(compare(current, days, settings)["red"]) == 1
    assert current["red"] == []


def test_succeeded_record_filter_duplicates_and_immutable_input():
    days = history()
    records = [{"status": "succeeded", "summary": d} for d in days]
    records += [{"status": "failed", "summary": summary(999, "2026-10-12")}]
    records += [records[0]]
    result = compare(summary(31), records, default_settings())
    assert result["comparisons"]["waf.blocked"]["days"] == 7
    assert result["comparisons"]["waf.blocked"]["baseline"] == 10
    assert days[0]["red"] == []


def test_all_comparison_keys_and_non_comparison_keys():
    from daily_security_report.baseline import ALWAYS_RED, MINIMUMS, RATIO_KEYS

    current = summary()
    current["counters"] = {
        **dict.fromkeys(ALWAYS_RED, 1),
        **dict.fromkeys(RATIO_KEYS, 31),
        **{key: 3 * minimum for key, minimum in MINIMUMS.items()},
        "cloudfront.requests.ETEST": 31,
        "waf.allowed": 9999,
        "guardduty.new.low": 9999,
        "cloudtrail.config_change": 9999,
    }
    days = history()
    for day in days:
        day["counters"] = dict.fromkeys(current["counters"], 10)
    result = compare(current, days, default_settings())
    assert len(result["red"]) == len(ALWAYS_RED) + len(RATIO_KEYS) + 1
    assert "waf.allowed" not in result["comparisons"]


def test_all_first_seen_keys_and_no_first_seen_when_insufficient_or_failed():
    from daily_security_report.baseline import SEEN_SOURCES

    current = summary()
    current["seen"] = {key: ["new"] for key in SEEN_SOURCES}
    days = history()
    for day in days:
        day["seen"] = {key: ["known"] for key in SEEN_SOURCES}
    assert len(compare(current, days, default_settings())["red"]) == 5
    assert not compare(current, days[:2], default_settings())["red"]
    current["sources"]["insights"]["status"] = "failed"
    assert len(compare(current, days, default_settings())["red"]) == 2


def test_login_failures_are_per_principal_and_do_not_need_baseline():
    current = summary()
    current["top"] = {
        "console_login_failures": [{"principal": "test-a", "count": 3}, {"principal": "test-b", "count": 2}]
    }
    assert [r["code"] for r in compare(current, [], default_settings())["red"]] == ["LOGIN_FAILURE"]


def test_existing_red_details_are_not_duplicated_and_disabled_red_is_removed():
    current = summary()
    current["counters"]["cloudtrail.root"] = 1
    current["red"] = [
        {"section": "cloudtrail", "code": "ROOT", "text": "test-user used root"},
        {"section": "insights", "code": "DIRECT", "text": "direct request"},
    ]
    current["sources"]["insights"]["status"] = "disabled"
    assert len(compare(current, [], default_settings())["red"]) == 1


def test_instance_matching_uses_id_for_ec2_and_name_for_lightsail():
    days = history()
    current = summary()
    current["instances"] = {
        "ec2": [{"id": "i-a", "name": "same", "cpu_avg": 4}, {"id": "i-b", "name": "same", "cpu_avg": 4}],
        "lightsail": [{"name": "test-lightsail", "net_out": 4}],
    }
    for day in days:
        day["instances"] = {
            "ec2": [{"id": "i-a", "name": "same", "cpu_avg": 1}, {"id": "i-b", "name": "same", "cpu_avg": 2}],
            "lightsail": [{"name": "test-lightsail", "net_out": 1}],
        }
    result = compare(current, days, default_settings())
    assert len(result["red"]) == 2
    assert not compare(current, days[:2], default_settings())["red"]


def test_daily_red_details_are_bounded_without_losing_total_count():
    current = summary()
    current["seen"]["cloudtrail_human_ips"] = [f"2001:db8::{i:x}" for i in range(150)]
    result = compare(current, history(), default_settings())
    assert len(result["red"]) == 100
    assert result["red_count"] == 150
    assert result["red_counts"] == {"cloudtrail": 150}


def test_v2_medium_needs_no_baseline_and_section_names_replace_red_fields():
    data = summary()
    data.update(schema="daily-security-report.daily.v2", findings=[])
    data["counters"]["guardduty.new.medium"] = 1
    result = compare(data, [], default_settings())
    assert result["finding_counts"] == {"critical": 0, "warning": 1}
    assert result["findings"][0]["code"] == "GUARDDUTY_MEDIUM"
    assert result["level"] == "warning" and set(result["no_baseline"]) == {"cloudtrail", "metrics"}
    assert not any(key in result for key in ("red", "red_count", "red_counts"))


def test_medium_is_not_lost_when_high_details_were_already_truncated():
    from daily_security_report import levels

    data = summary()
    data.update(
        schema="daily-security-report.daily.v2",
        findings=[levels.finding("guardduty", "GUARDDUTY_HIGH", str(i)) for i in range(100)],
        finding_counts={"critical": 120, "warning": 0},
        findings_truncated={"guardduty": 20},
    )
    data["counters"] = {"guardduty.new.high": 120, "guardduty.new.medium": 3}
    result = compare(data, [], default_settings())
    assert result["finding_counts"] == {"critical": 120, "warning": 1}


def test_finding_texts_leave_event_counts_to_count_field():
    current = summary(100)
    current["counters"]["guardduty.new.medium"] = 3
    current["top"] = {"console_login_failures": [{"principal": "user-example", "count": 12}]}
    rows = {r["code"]: r for r in compare(current, history(), default_settings())["red"]}
    assert (rows["GUARDDUTY_MEDIUM"]["text"], rows["GUARDDUTY_MEDIUM"]["count"]) == ("guardduty.new.medium", 3)
    assert (rows["LOGIN_FAILURE"]["text"], rows["LOGIN_FAILURE"]["count"]) == ("user-example: ConsoleLogin 失敗", 12)
    assert (rows["RATIO"]["text"], rows["RATIO"]["count"]) == ("waf.blocked（基準 10 の 3 倍超）", 100)


def test_s3_counters_use_the_ratio_baseline_and_point_to_cloudwatch_without_log_ref():
    from daily_security_report import baseline

    assert {"s3.bytes_downloaded", "s3.delete_requests", "s3.get_requests"} <= baseline.RATIO_KEYS
    assert baseline.source_for("s3.bytes_downloaded") == "s3"
    days = [
        {
            "date": f"2026-10-{day}",
            "sources": {"s3": {"status": "ok"}},
            "counters": {"s3.get_requests": 100, "s3.buckets": 3},
        }
        for day in (11, 12, 13)
    ]
    current = {
        "schema": "daily-security-report.daily.v2",
        "date": "2026-10-14",
        "sources": {"s3": {"status": "ok"}},
        "counters": {"s3.get_requests": 1_000, "s3.buckets": 30},
        "findings": [],
    }
    result = compare(current, days, default_settings())
    assert set(result["comparisons"]) == {"s3.get_requests"}
    [row] = result["findings"]
    assert (row["section"], row["code"], row["key"]) == ("s3", "RATIO", "s3.get_requests")
    assert row["where"] == "CloudWatch メトリクス AWS/S3" and "ref" not in row
    assert result["rule_levels"]["s3"] == "warning"


def test_s3_download_ratio_counts_one_finding_not_bytes():
    gib = 1024**3
    days = [
        {"date": f"2026-10-{day}", "sources": {"s3": {"status": "ok"}}, "counters": {"s3.bytes_downloaded": gib}}
        for day in (11, 12, 13)
    ]
    current = {
        "schema": "daily-security-report.daily.v2",
        "date": "2026-10-14",
        "sources": {"s3": {"status": "ok"}},
        "counters": {"s3.bytes_downloaded": 10 * gib},
        "findings": [],
    }
    [row] = compare(current, days, default_settings())["findings"]
    assert (row["code"], row["key"], row["count"]) == ("RATIO", "s3.bytes_downloaded", 1)
    assert row["text"] == "s3.bytes_downloaded: 10.0 GB（基準 1.0 GB の 3 倍超）"


@pytest.mark.parametrize(
    "key,base,below,at,text",
    [
        ("s3.bytes_downloaded", 1, 1024**3 - 1, 1024**3, "s3.bytes_downloaded: 1.0 GB（基準 1 B の 3 倍超）"),
        ("s3.delete_requests", 1, 999, 1_000, "s3.delete_requests（基準 1 の 3 倍超）"),
        ("s3.get_requests", 1, 999, 1_000, "s3.get_requests（基準 1 の 3 倍超）"),
    ],
)
def test_s3_ratio_needs_the_bucket_level_minimum(key, base, below, at, text):
    days = [
        {"date": f"2026-10-{d}", "sources": {"s3": {"status": "ok"}}, "counters": {key: base}} for d in (11, 12, 13)
    ]

    def judged(value):
        current = {
            "schema": "daily-security-report.daily.v2",
            "date": "2026-10-14",
            "sources": {"s3": {"status": "ok"}},
            "counters": {key: value},
            "findings": [],
        }
        return compare(current, days, default_settings())["findings"]

    assert judged(below) == []
    [row] = judged(at)
    assert row["text"] == text


def test_other_ratio_keys_keep_the_min_count():
    days = [
        {"date": f"2026-10-{d}", "sources": {"metrics": {"status": "ok"}}, "counters": {"waf.blocked": 1}}
        for d in (11, 12, 13)
    ]
    current = {
        "schema": "daily-security-report.daily.v2",
        "date": "2026-10-14",
        "sources": {"metrics": {"status": "ok"}},
        "counters": {"waf.blocked": 10},
        "findings": [],
    }
    assert [r["code"] for r in compare(current, days, default_settings())["findings"]] == ["RATIO"]
