from copy import deepcopy
from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import ANY, Stubber
from daily_security_report.guardduty_summary import collect, severity_level, summarize_findings

DAY = "2026-10-14"


def finding(identifier="f1", severity=7, **changes):
    return {
        "Id": identifier,
        "Region": "ap-northeast-1",
        "Severity": severity,
        "Title": identifier,
        "CreatedAt": "2026-10-13T15:00:00Z",
        "UpdatedAt": "2026-10-14T14:59:59Z",
        "Service": {},
        **changes,
    }


@pytest.mark.parametrize(
    "value,level",
    [
        (1, "low"),
        (3.9, "low"),
        (4, "medium"),
        (6.9, "medium"),
        (7, "high"),
        (8.9, "high"),
        (9, "critical"),
        (10, "critical"),
    ],
)
def test_severity_boundaries(value, level):
    assert severity_level(value) == level


def test_new_continuing_boundaries_and_alert_marks():
    rows = [
        finding(),
        finding("critical", 9),
        finding("old", 4, CreatedAt="2026-10-13T14:59:59Z"),
        finding("before", 4, CreatedAt="2026-10-12T00:00:00Z", UpdatedAt="2026-10-13T14:59:59Z"),
        finding("after", 4, CreatedAt="2026-10-14T15:00:00Z", UpdatedAt="2026-10-14T15:00:00Z"),
    ]
    original = deepcopy(rows)
    result = summarize_findings(rows, DAY, alerted_findings={"ap-northeast-1#f1"})
    assert result["counters"]["guardduty.new.high"] == 2
    assert result["counters"]["guardduty.continuing"] == 1
    assert result["severity"]["new"]["critical"] == 1
    assert len(result["red"]) == 2
    assert any("即時通知済み" in row["text"] for row in result["red"])
    assert rows == original


@pytest.mark.parametrize(
    "service",
    [
        {"Archived": True},
        {"AdditionalInfo": {"sample": True}},
        {"AdditionalInfo": {"Value": '{"sample": true}'}},
        {"AdditionalInfo": {"Sample": True}},
    ],
)
def test_archived_and_sample_are_not_counted(service):
    assert summarize_findings([finding(Service=service)], DAY)["counters"]["guardduty.new.high"] == 0


def test_top_titles_order_severity_then_occurrences():
    result = summarize_findings(
        [
            finding("low", 3.9),
            finding("m1", 4, Service={"Count": 2}),
            finding("m2", 6, Service={"Count": 9}),
            finding("high", 7),
        ],
        DAY,
        top_n=2,
    )
    assert [row["title"] for row in result["top"]["guardduty"]] == ["m2", "m1"]


def test_get_findings_batches_at_50_and_list_paginates():
    client = boto3.client("guardduty", region_name="ap-northeast-1")
    criteria = {
        "Criterion": {"updatedAt": {"GreaterThanOrEqual": 1791903600000}, "service.archived": {"Equals": ["false"]}}
    }
    with Stubber(client) as stub:
        stub.add_response(
            "list_findings",
            {"FindingIds": [f"f{i}" for i in range(51)], "NextToken": "next"},
            {"DetectorId": "example-detector", "FindingCriteria": criteria, "MaxResults": 50},
        )
        stub.add_response(
            "get_findings",
            {"Findings": []},
            {"DetectorId": "example-detector", "FindingIds": [f"f{i}" for i in range(50)]},
        )
        stub.add_response("get_findings", {"Findings": []}, {"DetectorId": "example-detector", "FindingIds": ["f50"]})
        stub.add_response(
            "list_findings",
            {"FindingIds": []},
            {"DetectorId": "example-detector", "FindingCriteria": criteria, "MaxResults": 50, "NextToken": "next"},
        )
        assert collect(DAY, {"ap-northeast-1": client})["status"] == "ok"
        stub.assert_no_pending_responses()


def test_region_error_does_not_escape_or_return_partial_counts():
    client = boto3.client("guardduty", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        stub.add_client_error("list_findings", "AccessDeniedException")
        result = collect(DAY, {"ap-northeast-1": client})
    assert result["status"] == "failed"
    assert "AccessDeniedException" in result["error"]
    assert "counters" not in result


def test_detector_configuration_is_required(monkeypatch):
    monkeypatch.setenv("GUARDDUTY_DETECTORS", "")
    assert collect(DAY, {})["status"] == "failed"


def test_invalid_sample_metadata_is_not_a_sample_and_equal_severity_sorts_by_count():
    rows = [
        finding("one", 4, Service={"AdditionalInfo": {"Value": "not-json"}, "Count": 2}),
        finding("two", 4, Service={"Count": 5}),
    ]
    result = summarize_findings(rows, DAY)
    assert result["counters"]["guardduty.new.medium"] == 2
    assert [row["title"] for row in result["top"]["guardduty"]] == ["two", "one"]


def test_large_high_finding_list_has_exact_red_count_and_bounded_details():
    result = summarize_findings([finding(f"f{i}", 7) for i in range(120)], DAY)
    assert len(result["red"]) == 100
    assert result["red_count"] == 120


def test_multiple_regions_and_custom_detectors():
    clients = {region: boto3.client("guardduty", region_name=region) for region in ("us-east-1", "us-west-2")}
    with Stubber(clients["us-east-1"]) as east, Stubber(clients["us-west-2"]) as west:
        for stub, detector in ((east, "east"), (west, "west")):
            stub.add_response(
                "list_findings", {"FindingIds": []}, {"DetectorId": detector, "FindingCriteria": ANY, "MaxResults": 50}
            )
        result = collect(DAY, clients, detectors={"us-east-1": "east", "us-west-2": "west"})
    assert result["regions"] == {"us-east-1": "ok", "us-west-2": "ok"}


def test_epoch_fixture_is_correct():
    assert datetime(2026, 10, 13, 15, tzinfo=UTC).timestamp() == 1791903600
