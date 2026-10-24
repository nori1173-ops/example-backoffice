from copy import deepcopy

import pytest
from daily_security_report import levels
from daily_security_report.settings import SECTIONS


@pytest.mark.parametrize(
    "code,expected",
    [
        ("ROOT", "critical"),
        ("GUARDDUTY_HIGH", "critical"),
        ("SG_WORLD_OPEN", "critical"),
        ("LIGHTSAIL_WORLD_OPEN", "critical"),
        ("IMAGE_PUBLIC", "critical"),
        ("DIRECT", "critical"),
        ("GUARDDUTY_MEDIUM", "warning"),
        ("HUMAN_FOREIGN", "warning"),
        ("ADMIN_EXTERNAL", "warning"),
        ("RATIO", "warning"),
        ("FIRST_SEEN", "warning"),
        ("INSTANCE_RATIO", "warning"),
        ("INSTANCE_NEW", "warning"),
        ("INSTANCE_GONE", "warning"),
        ("ACCESS_DENIED", "warning"),
        ("LOGIN_FAILURE", "warning"),
        ("LOGIN_FOREIGN", "warning"),
        ("IAM_CHANGE", "warning"),
        ("DORMANT_ACTOR", "warning"),
        ("NEW_ACTOR", "warning"),
        ("EXTERNAL_ACCOUNT", "warning"),
        ("SOURCE_FAILED", "warning"),
        ("S3_MASS_DELETE", "critical"),
        ("S3_OBJECTS_DROP", "warning"),
        ("S3_SIZE_DROP", "warning"),
        ("S3_DOWNLOAD_SPIKE", "warning"),
        ("S3_DELETE_SPIKE", "warning"),
        ("S3_BUCKET_GONE", "critical"),
        ("S3_METRICS_STALE", "warning"),
        ("unknown", "ok"),
    ],
)
def test_code_table(code, expected):
    assert levels.level_for(code) == expected


def test_actor_foreign_escalation_and_overall():
    assert levels.finding("cloudtrail", "NEW_ACTOR", "new", foreign=True)["level"] == "critical"
    rows = [levels.finding("cloudtrail", "ROOT", "root"), levels.finding("metrics", "RATIO", "rise")]
    rules = levels.rule_levels(rows)
    assert rules["cloudtrail"] == "critical" and rules["metrics"] == "warning"
    assert levels.overall(rules) == "critical"
    assert levels.overall({}) == "ok"


@pytest.mark.parametrize("status", ["failed", "refused", "timeout", "disabled", "skipped"])
def test_failed_ai_keeps_rules(status):
    rules = dict.fromkeys(SECTIONS, "ok") | {"cloudtrail": "warning"}
    assert levels.combine(rules, [], {"status": status}) == rules
    assert levels.combine(rules, [], None) == rules


def test_combine_preserves_critical_failed_and_disabled_sections():
    rows = [levels.finding("cloudtrail", "ROOT", "root"), levels.finding("metrics", "SOURCE_FAILED", "failed")]
    rules = levels.rule_levels(rows)
    ai = {"status": "ok", "sections": [{"section": s, "level": "ok"} for s in SECTIONS if s != "instances"]}
    result = levels.combine(rules, rows, ai)
    assert result["cloudtrail"] == "critical" and result["metrics"] == "warning"
    assert result["instances"] == "ok"  # missing from AI evaluation means disabled
    assert rules["instances"] == "ok"
    rules["insights"] = "warning"
    ai["sections"].append({"section": "insights", "level": "ok"})
    assert levels.combine(rules, rows, ai)["insights"] == "ok"


def test_v1_conversion_is_warning_and_does_not_mutate():
    old = {
        "date": "2026-10-16",
        "schema": "daily-security-report.daily.v1",
        "red_count": 4,
        "red_counts": {"cloudtrail": 4},
        "red": [{"section": "cloudtrail", "code": "ROOT", "text": "root"}],
    }
    before = deepcopy(old)
    result = levels.normalize_summary(old)
    assert result["legacy"] and result["level"] == "warning"
    assert result["finding_counts"] == {"critical": 0, "warning": 4}
    assert result["findings"][0]["level"] == "warning" and result["findings"][0]["count"] == 1
    assert result["findings_truncated"] == {"cloudtrail": 3}
    assert result["ai"]["status"] == "disabled"
    assert old == before and not any(k in result for k in ("red", "red_count", "red_counts"))


def test_rules_count_rows_sort_cap_preserve_critical_and_failures():
    summary = {
        "schema": "daily-security-report.daily.v2",
        "sources": {s: {"status": "ok"} for s in SECTIONS},
        "findings": [levels.finding("metrics", "RATIO", str(i), count=100) for i in range(120)],
    }
    summary["sources"]["insights"] = {"status": "failed", "error": "TIMEOUT"}
    summary["sources"]["instances"] = {"status": "disabled"}
    summary["findings"].append(levels.finding("cloudtrail", "ROOT", "root"))
    result = levels.apply_rules(summary)
    assert len(result["findings"]) == 100 and result["findings"][0]["code"] == "ROOT"
    assert result["finding_counts"] == {"critical": 1, "warning": 121}
    assert sum(result["findings_truncated"].values()) == 22
    assert result["rule_levels"]["insights"] == "warning"


def test_critical_rule_cannot_be_downgraded_when_its_row_was_truncated():
    rules = dict.fromkeys(SECTIONS, "ok") | {"guardduty": "critical"}
    assert (
        levels.combine(rules, [], {"status": "ok", "sections": [{"section": "guardduty", "level": "ok"}]})["guardduty"]
        == "critical"
    )


def test_v1_count_without_details_still_renders_warning():
    result = levels.normalize_summary({"red_count": 3})
    assert levels.overall(result["levels"]) == "warning"


def test_real_v1_baseline_keys_normalized_without_mutating_fixture():
    import json
    from pathlib import Path

    original = json.loads((Path(__file__).parents[1] / "fixtures/daily_v1.json").read_text())
    before = deepcopy(original)
    assert "baseline_missing" not in original and "no_baseline" in original
    normalized = levels.normalize_summary(original)
    assert normalized["baseline_missing"] == ["cloudtrail", "metrics", "insights", "instances"]
    assert normalized["sources"]["cloudtrail"]["details"] == original["sources"]["cloudtrail"]["details"]
    assert original == before


def test_s3_section_takes_part_in_rule_and_final_levels():
    rows = [levels.finding("s3", "S3_MASS_DELETE", "S3 バケット x のオブジェクト数が前日から 60.0% 減", 1_200)]
    rules = levels.rule_levels(rows)
    assert SECTIONS[-1] == "s3" and rules["s3"] == "critical" and rules["instances"] == "ok"
    summary = {"rule_levels": rules, "findings": rows, "sources": {s: {"status": "ok"} for s in SECTIONS}}
    verdict = {"status": "ok", "sections": [{"section": "s3", "level": "ok", "reason": "大きな変化なし"}]}
    assert levels.final_levels(summary, verdict)["s3"] == "critical"
    summary["sources"]["s3"] = {"status": "disabled"}
    assert levels.final_levels(summary, verdict)["s3"] == "ok"
