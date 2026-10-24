from daily_security_report import levels
from daily_security_report.baseline import compare
from daily_security_report.report import build_report


def test_comparison_preserves_counts_of_details_trimmed_by_collectors():
    summary = {
        "date": "2026-10-14",
        "sources": {"insights": {"status": "ok"}},
        "red": [{"section": "insights", "code": "DIRECT", "text": f"IP {i}"} for i in range(100)],
        "red_count": 120,
        "red_counts": {"insights": 120},
    }
    result = compare(summary, [], {})
    assert result["red_count"] == 120
    assert result["red_counts"] == {"insights": 120}
    assert len(result["red"]) == 100


def test_report_displays_missing_metrics_instead_of_only_zero():
    summary = {"date": "2026-10-14", "sources": {"metrics": {"status": "ok", "missing_targets": ["apigw:typo"]}}}
    adapted = levels.normalize_summary(summary)
    assert adapted["sources"]["metrics"]["missing_targets"] == ["apigw:typo"]
    assert "入口:" not in build_report(summary, {})["email"]["text"]


def test_report_does_not_display_zero_when_gps_rejected_is_unavailable():
    summary = {"date": "2026-10-14", "sources": {"insights": {"status": "ok", "gps_rejected_source": "unavailable"}}}
    adapted = levels.normalize_summary(summary)
    assert adapted["sources"]["insights"]["gps_rejected_source"] == "unavailable"
    assert "GPS モック" not in build_report(summary, {})["email"]["text"]
