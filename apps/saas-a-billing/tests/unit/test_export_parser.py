"""`export_parser.py` のユニットテスト（設計 2.4 / 6.4 / 13 章）。"""

import dataclasses
import json

from saas_a_billing.export_parser import (
    ExportResult,
    is_no_records,
    parse_limit_over,
    parse_lines,
    parse_response,
)
from saas_a_billing.period import closing_period

from tests.conftest import FIXTURES_DIR

DEVICE_CD = "10000001-01-LoggerA-400"
MULTI_GROUP_CD = "10000002-02-LoggerA-400"
CAMERA_CD = "10000003-03-CamA-MC"
RAIN_CD = "10000005-05-LoggerA-210"

HEADER = "field_name,point_cd,point_name,change_datetime,パケット種別,水位(m)"
CAMERA_HEADER = "field_name,point_cd,point_name,change_datetime,パケット種別,ファイル名,Base64形式データ"

FIXTURE_PERIOD = closing_period("2026-09", 20, "24:00")
BOUNDARY_PERIOD = closing_period("2026-10", 20, "24:00")

EXPORT_FIELDS = {"received", "camera", "first_seen", "last_seen", "rows", "bytes_read"}


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def parse_fixture() -> ExportResult:
    """cp932 のフィクスチャを `resp.iter_lines()` 相当のバイト列として読ませる。"""
    result = ExportResult()
    parse_response(fixture_bytes("export_multi_block.csv").splitlines(), FIXTURE_PERIOD, result)
    return result


def parse_text(lines: list[str], period=BOUNDARY_PERIOD) -> ExportResult:
    result = ExportResult()
    parse_lines(iter(lines), period, result)
    return result


def data_line(point_cd: str, ts: str, group: str = "現場A") -> str:
    return f"{group},{point_cd},観測局,{ts},定時,1.23"


def test_parses_multiple_blocks():
    result = parse_fixture()
    assert result.received == {DEVICE_CD, MULTI_GROUP_CD, RAIN_CD, CAMERA_CD}
    assert result.rows == 7


def test_header_row_is_not_treated_as_data():
    result = parse_fixture()
    assert "point_cd" not in result.received
    assert "point_cd" not in result.camera
    assert "point_cd" not in result.first_seen
    assert result.rows == 7
    header_only = parse_text([HEADER, CAMERA_HEADER])
    assert header_only.received == set()
    assert header_only.camera == set()
    assert header_only.rows == 0


def test_camera_block_detected_by_base64_column():
    result = parse_fixture()
    assert result.camera == {CAMERA_CD}
    assert result.first_seen[CAMERA_CD] == "2026/08/21 06:00:00"
    assert result.last_seen[CAMERA_CD] == "2026/08/22 06:00:00"


def test_non_camera_block_devices_not_in_camera_set():
    assert parse_fixture().received - parse_fixture().camera == {DEVICE_CD, MULTI_GROUP_CD, RAIN_CD}
    reset = parse_text(
        [
            CAMERA_HEADER,
            data_line(CAMERA_CD, "2026/09/22 06:00:00"),
            HEADER,
            data_line(DEVICE_CD, "2026/09/22 00:05:00"),
        ]
    )
    assert reset.received == {CAMERA_CD, DEVICE_CD}
    assert reset.camera == {CAMERA_CD}


def test_quoted_group_name_with_comma_is_handled():
    result = parse_fixture()
    assert MULTI_GROUP_CD in result.received
    assert result.first_seen[MULTI_GROUP_CD] == "2026/08/21 00:10:00"
    assert not any(point_cd.startswith("現場") for point_cd in result.received)


def test_first_and_last_seen_are_min_and_max():
    result = parse_fixture()
    assert result.first_seen[DEVICE_CD] == "2026/08/21 00:05:00"
    assert result.last_seen[DEVICE_CD] == "2026/08/22 00:05:00"
    assert result.first_seen[RAIN_CD] == "2026/08/23 01:00:00"
    assert result.last_seen[RAIN_CD] == "2026/08/24 01:00:00"
    descending = parse_text(
        [
            HEADER,
            data_line(DEVICE_CD, "2026/10/05 00:00:00"),
            data_line(DEVICE_CD, "2026/09/25 00:00:00"),
        ]
    )
    assert descending.first_seen[DEVICE_CD] == "2026/09/25 00:00:00"
    assert descending.last_seen[DEVICE_CD] == "2026/10/05 00:00:00"


def test_rows_outside_period_are_skipped():
    result = parse_text(
        [
            HEADER,
            data_line(DEVICE_CD, "2026/09/20 23:59:59"),
            data_line(RAIN_CD, "2026/10/05 12:00:00"),
        ]
    )
    assert result.received == {RAIN_CD}
    assert DEVICE_CD not in result.first_seen
    assert DEVICE_CD not in result.last_seen
    assert result.rows == 2


def test_boundary_row_at_period_start_is_included():
    result = parse_text([HEADER, data_line(DEVICE_CD, "2026/09/21 00:00:00")])
    assert result.received == {DEVICE_CD}
    assert result.first_seen[DEVICE_CD] == "2026/09/21 00:00:00"


def test_boundary_row_at_period_end_is_included():
    result = parse_text([HEADER, data_line(DEVICE_CD, "2026/10/20 23:59:59")])
    assert result.received == {DEVICE_CD}
    assert result.last_seen[DEVICE_CD] == "2026/10/20 23:59:59"


def test_row_at_exclusive_end_is_excluded():
    result = parse_text([HEADER, data_line(DEVICE_CD, "2026/10/21 00:00:00")])
    assert result.received == set()
    assert result.first_seen == {}
    assert result.rows == 1


def test_empty_lines_are_skipped():
    result = parse_text(["", HEADER, "", data_line(DEVICE_CD, "2026/10/01 00:00:00"), "", ""])
    assert result.received == {DEVICE_CD}
    assert result.rows == 1
    short_row = parse_text([HEADER, f"現場A,{RAIN_CD}"])
    assert short_row.received == {RAIN_CD}
    assert short_row.first_seen == {}
    assert short_row.rows == 1


def test_merge_combines_two_windows():
    first = parse_text(
        [
            HEADER,
            data_line(DEVICE_CD, "2026/09/25 00:00:00"),
            data_line(DEVICE_CD, "2026/09/26 00:00:00"),
        ]
    )
    second = parse_text(
        [
            HEADER,
            data_line(DEVICE_CD, "2026/09/23 00:00:00"),
            data_line(DEVICE_CD, "2026/10/02 00:00:00"),
            CAMERA_HEADER,
            data_line(CAMERA_CD, "2026/10/03 06:00:00"),
        ]
    )
    merged = first.merge(second)
    assert merged.received == {DEVICE_CD, CAMERA_CD}
    assert merged.camera == {CAMERA_CD}
    assert merged.first_seen[DEVICE_CD] == "2026/09/23 00:00:00"
    assert merged.last_seen[DEVICE_CD] == "2026/10/02 00:00:00"
    assert merged.first_seen[CAMERA_CD] == "2026/10/03 06:00:00"
    assert merged.last_seen[CAMERA_CD] == "2026/10/03 06:00:00"
    assert merged.rows == first.rows + second.rows == 5
    assert merged.bytes_read == 0
    assert second.merge(first).first_seen == merged.first_seen
    assert second.merge(first).last_seen == merged.last_seen
    assert first.received == {DEVICE_CD}
    assert first.camera == set()
    assert first.first_seen[DEVICE_CD] == "2026/09/25 00:00:00"


def test_parse_response_decodes_cp932():
    result = parse_fixture()
    assert result.camera == {CAMERA_CD}
    raw = fixture_bytes("export_multi_block.csv")
    assert result.bytes_read == sum(len(line) for line in raw.splitlines())
    as_utf8 = ExportResult()
    parse_response(raw.decode("cp932").encode("utf-8").splitlines(), FIXTURE_PERIOD, as_utf8)
    assert as_utf8.camera == set()


def test_parse_response_replaces_undecodable_bytes():
    lines = [
        CAMERA_HEADER.encode("cp932"),
        b"\x81\x20,10000003-03-CamA-MC,\xff\xfe,2026/10/02 06:00:00,\x89\xe6\x91\x9c,a.jpg,SUCCESS",
    ]
    result = ExportResult()
    parse_response(lines, BOUNDARY_PERIOD, result)
    assert result.received == {CAMERA_CD}
    assert result.camera == {CAMERA_CD}
    assert result.bytes_read == sum(len(line) for line in lines)


def test_is_no_records_for_empty_body():
    assert is_no_records(b"") is True
    assert is_no_records("") is True
    assert is_no_records(b"\r\n") is True
    assert is_no_records(fixture_bytes("export_empty.csv")) is True


def test_is_no_records_for_json():
    raw = fixture_bytes("export_no_records.json")
    assert is_no_records(raw) is True
    assert is_no_records(raw.decode("utf-8")) is True


def test_is_no_records_false_for_csv():
    assert is_no_records(fixture_bytes("export_multi_block.csv")) is False
    assert is_no_records(fixture_bytes("export_limit_over.json")) is False
    assert is_no_records(b'"Error.Export.NoRecords"') is False


def test_parse_limit_over():
    assert parse_limit_over("Error.Export.LimitOver,123456,100000") == (123456, 100000)
    message = json.loads(fixture_bytes("export_limit_over.json"))["message"]
    assert parse_limit_over(message) == (123456, 100000)
    assert parse_limit_over("Error.Export.LimitOver,7,5") == (7, 5)


def test_parse_limit_over_returns_none_for_other_message():
    assert parse_limit_over("Error.Export.NoRecords") is None
    assert parse_limit_over("Error.Export.LimitOver") is None
    assert parse_limit_over("Error.Export.LimitOver,123456") is None
    assert parse_limit_over("Bad Request") is None
    assert parse_limit_over("") is None


def test_does_not_accumulate_rows():
    def many_lines():
        yield HEADER
        for index in range(2000):
            yield data_line(f"DEV-{index % 10:02d}", "2026/10/01 00:00:00")

    result = ExportResult()
    parse_lines(many_lines(), BOUNDARY_PERIOD, result)
    assert {field.name for field in dataclasses.fields(ExportResult)} == EXPORT_FIELDS
    assert set(vars(result)) == EXPORT_FIELDS
    assert result.rows == 2000
    assert len(result.received) == 10
    assert len(result.first_seen) == 10
    assert len(result.last_seen) == 10
