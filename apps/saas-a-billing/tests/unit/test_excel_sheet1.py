"""`excel.py` のシート1「請求照合」のユニットテスト（設計 3.5 / 6.6 / 13 章）。"""

import io
import zipfile

import openpyxl
import pytest
from saas_a_billing.excel import (
    COLOR_REVIEW_TEXT,
    COLOR_TOTAL_HIGHLIGHT,
    CURRENCY_FORMAT,
    FONT_NAME,
    RETENTION_1Y,
    RETENTION_3Y,
    SHEET1_COLUMNS,
    SHEET1_NAME,
    TOTAL_FONT_SIZE,
    TOTAL_LABEL,
    build_workbook,
    column_width,
    footnote_lines,
    spec_block_lines,
)

from tests.conftest import make_result

CHINESE_FONTS = ("SimSun", "SimHei", "Microsoft YaHei", "PingFang SC", "Noto Sans SC")
SPEC_BLOCK_ROWS = 7
BILLABLE_DEVICE_CDS = (
    "10000001-01-LoggerA-400",
    "10000002-01-LoggerA-400",
    "10000005-01-LoggerA-400",
    "20000001-01-CamA-MC",
    "20000002-01-CamA-MC-2",
    "20000003-01-LoggerA-400",
    "40000001-01-LoggerA-400",
)
UNLISTED_DEVICE_CDS = (
    "10000003-01-LoggerA-400",
    "10000004-01-LoggerA-400",
    "30000001-01-LoggerA-400",
    "50000001-01-LoggerA-400",
    "10000009-01-LoggerA-400",
)


def argb(color: str) -> str:
    """`#C00000` → openpyxl が返す `FFC00000`。"""
    return f"FF{color.lstrip('#').upper()}"


def load(result: dict) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(build_workbook(result)))


def values(worksheet) -> list[list]:
    return [[cell.value for cell in row] for row in worksheet.iter_rows()]


def texts(worksheet) -> list[str]:
    return [value for row in values(worksheet) for value in row if isinstance(value, str)]


def at(worksheet, row: int, col: int):
    """0 始まりの行・列でセルを取る。"""
    return worksheet.cell(row=row + 1, column=col + 1)


def find_row(worksheet, text: str, col: int = 0) -> int:
    """`col` の値が `text` と一致する最初の行（0 始まり）。"""
    for index, row in enumerate(values(worksheet)):
        if row[col] == text:
            return index
    raise AssertionError(f"{text!r} の行がありません")


def find_row_containing(worksheet, text: str, col: int = 0) -> int:
    for index, row in enumerate(values(worksheet)):
        if isinstance(row[col], str) and text in row[col]:
            return index
    raise AssertionError(f"{text!r} を含む行がありません")


def font_color(cell) -> str | None:
    return cell.font.color.rgb if cell.font.color is not None else None


def fill_color(cell) -> str | None:
    return cell.fill.start_color.rgb if cell.fill.patternType else None


def device_rows(worksheet, tenant_cd: str) -> list[list]:
    """テナント見出しの 2 行下から、現場列が空になるまでの地点行。"""
    start = find_row_containing(worksheet, f"■ {tenant_cd}") + 2
    collected = []
    for row in values(worksheet)[start:]:
        if not isinstance(row[0], str):
            break
        collected.append(row)
    return collected


@pytest.fixture
def result() -> dict:
    return make_result()


@pytest.fixture
def sheet1(result):
    return load(result)[SHEET1_NAME]


def test_workbook_has_two_sheets_named_correctly(result):
    assert load(result).sheetnames == ["請求照合", "全地点（管理用）"]


def test_sheet1_starts_with_spec_block(result, sheet1):
    lines = spec_block_lines(result)
    assert len(lines) == SPEC_BLOCK_ROWS
    assert [at(sheet1, index, 0).value for index in range(SPEC_BLOCK_ROWS)] == lines
    block = "\n".join(lines)
    for keyword in (
        "2026年10月分",
        "締め日時: 2026/10/20 24:00",
        "集計対象: ",
        "課金対象期間: ",
        "除外: ",
        "使用単価表: 2025/09/01 適用版",
        "取得失敗テナント: ",
    ):
        assert keyword in block


def test_spec_block_shows_verification_host_for_verification(result):
    run = {**result["run"], "target_env": "verification", "saasa_host": "verify.saas-a.example.net"}
    verification = make_result(target_env="verification", run=run)
    assert "集計対象: 検証環境（verify.saas-a.example.net）" in spec_block_lines(verification)


def test_spec_block_shows_production_host(result):
    assert "集計対象: 本番環境（saas-a.example.jp）" in spec_block_lines(result)


def test_spec_block_display_period_ends_at_23_59_59(result):
    period_line = spec_block_lines(result)[3]
    assert "2026/09/21 00:00:00 〜 2026/10/20 23:59:59" in period_line
    assert "2026/10/21" not in period_line


def test_spec_block_period_is_based_on_observation_datetime(result):
    assert spec_block_lines(result)[3].endswith("に受信データがある地点を課金対象とする（観測データの日時基準・JST）")


def test_spec_block_exclusion_is_judged_at_aggregation_time(result):
    exclusion = spec_block_lines(result)[4]
    assert exclusion.startswith("除外: テナント説明欄または地点備考欄に「デモ」")
    assert exclusion.endswith("を含む地点は課金対象外（集計を実行した時点の内容で判定）")


def test_spec_block_lists_failed_tenants_in_red_when_present(result, sheet1):
    failed_line = at(sheet1, SPEC_BLOCK_ROWS - 1, 0)
    assert "TNT005 取得失敗テナント（集計外）" in failed_line.value
    assert font_color(failed_line) == argb(COLOR_REVIEW_TEXT)

    healthy = make_result(tenants=[tenant for tenant in result["tenants"] if tenant["status"] != "failed"])
    healthy_line = at(load(healthy)[SHEET1_NAME], SPEC_BLOCK_ROWS - 1, 0)
    assert healthy_line.value == "取得失敗テナント: なし"
    assert font_color(healthy_line) != argb(COLOR_REVIEW_TEXT)


def test_total_block_has_count_and_amount(sheet1):
    row = find_row(sheet1, "課金地点")
    assert at(sheet1, row, 1).value == 7
    assert at(sheet1, row, 2).value == 7200


def test_total_block_has_retention_breakdown(sheet1):
    three_years = find_row(sheet1, "保存期間 3年")
    assert (at(sheet1, three_years, 1).value, at(sheet1, three_years, 2).value) == (4, 4200)
    one_year = find_row(sheet1, "保存期間 1年")
    assert (at(sheet1, one_year, 1).value, at(sheet1, one_year, 2).value) == (3, 3000)


def test_total_block_has_camera_breakdown(sheet1):
    row = find_row(sheet1, "うちカメラ")
    assert (at(sheet1, row, 1).value, at(sheet1, row, 2).value) == (2, 3000)


def test_spec_block_ends_with_total_amount_row(sheet1):
    assert TOTAL_LABEL == "合計金額"
    label, amount = at(sheet1, SPEC_BLOCK_ROWS, 0), at(sheet1, SPEC_BLOCK_ROWS, 1)
    assert label.value == TOTAL_LABEL
    assert amount.value == 7200
    assert amount.data_type == "n"
    assert amount.number_format == CURRENCY_FORMAT


def test_total_amount_row_is_bold_large_and_highlighted(sheet1):
    label, amount = at(sheet1, SPEC_BLOCK_ROWS, 0), at(sheet1, SPEC_BLOCK_ROWS, 1)
    assert TOTAL_FONT_SIZE >= 14
    assert label.font.bold is True and amount.font.bold is True
    assert label.font.size == amount.font.size == TOTAL_FONT_SIZE
    assert amount.alignment.horizontal == "right"
    assert fill_color(label) == fill_color(amount) == argb(COLOR_TOTAL_HIGHLIGHT)


def test_total_block_grand_total_is_bold_and_highlighted(sheet1):
    amount = at(sheet1, find_row(sheet1, "課金地点"), 2)
    assert amount.value == 7200
    assert amount.font.bold is True
    assert amount.number_format == CURRENCY_FORMAT
    assert fill_color(amount) == argb(COLOR_TOTAL_HIGHLIGHT)


def test_tenant_header_shows_cd_name_retention_count_subtotal(sheet1):
    row = find_row_containing(sheet1, "■ TNT001")
    assert at(sheet1, row, 0).value == "■ TNT001 サンプル建設"
    assert at(sheet1, row, 5).value == "3年"
    assert "課金 3 地点" in at(sheet1, row, 7).value
    assert "小計" in at(sheet1, row, 7).value
    assert at(sheet1, row, 8).value == 3450


def test_only_billable_devices_are_listed(sheet1):
    listed = texts(sheet1)
    for device_cd in BILLABLE_DEVICE_CDS:
        assert device_cd in listed
    for device_cd in UNLISTED_DEVICE_CDS:
        assert device_cd not in listed


def test_review_devices_are_listed_in_red(sheet1):
    row = find_row(sheet1, "10000005-01-LoggerA-400", col=1)
    assert font_color(at(sheet1, row, 1)) == argb(COLOR_REVIEW_TEXT)
    assert font_color(at(sheet1, row, 9)) == argb(COLOR_REVIEW_TEXT)
    assert "OVER_MAX_ITEMS" in at(sheet1, row, 9).value


def test_device_row_columns(sheet1):
    assert SHEET1_COLUMNS == (
        "現場",
        "地点CD",
        "地点名",
        "フォーマット",
        "項目数",
        "保存期間",
        "カメラ",
        "単価",
        "金額",
        "要確認",
    )
    header = find_row_containing(sheet1, "■ TNT001") + 1
    assert [at(sheet1, header, col).value for col in range(len(SHEET1_COLUMNS))] == list(SHEET1_COLUMNS)
    first = device_rows(sheet1, "TNT001")[0]
    assert first[: len(SHEET1_COLUMNS)] == [
        "現場A",
        "10000002-01-LoggerA-400",
        "観測局2",
        "LoggerA-400",
        984,
        "3年",
        "-",
        1300,
        1300,
        None,
    ]


def test_device_rows_sorted_by_group_then_device_cd(sheet1):
    assert [row[1] for row in device_rows(sheet1, "TNT001")] == [
        "10000002-01-LoggerA-400",
        "10000001-01-LoggerA-400",
        "10000005-01-LoggerA-400",
    ]


def test_tenants_sorted_by_tenant_cd(sheet1):
    assert [text for text in texts(sheet1) if text.startswith("■ ")] == [
        "■ TNT001 サンプル建設",
        "■ TNT002 第二テナント",
        "■ TNT004 未設定テナント",
    ]


def test_amount_is_numeric_with_currency_format(sheet1):
    assert CURRENCY_FORMAT == '"¥"#,##0'
    row = find_row(sheet1, "10000002-01-LoggerA-400", col=1)
    unit_price, amount = at(sheet1, row, 7), at(sheet1, row, 8)
    assert (unit_price.value, amount.value) == (1300, 1300)
    assert unit_price.data_type == "n" and amount.data_type == "n"
    assert unit_price.number_format == CURRENCY_FORMAT
    assert amount.number_format == CURRENCY_FORMAT


def test_subtotal_equals_sum_of_device_amounts(sheet1):
    rows = device_rows(sheet1, "TNT001")
    subtotal_row = find_row_containing(sheet1, "■ TNT001") + 2 + len(rows)
    assert at(sheet1, subtotal_row, 7).value == "小計"
    subtotal = at(sheet1, subtotal_row, 8)
    assert subtotal.value == sum(row[8] for row in rows) == 3450
    assert subtotal.data_type != "f"


def test_retention_cell_color_3y(sheet1):
    cell = at(sheet1, find_row(sheet1, "10000002-01-LoggerA-400", col=1), 5)
    assert cell.value == "3年"
    assert fill_color(cell) == argb(RETENTION_3Y[0])
    assert font_color(cell) == argb(RETENTION_3Y[1])


def test_retention_cell_color_1y(sheet1):
    cell = at(sheet1, find_row(sheet1, "20000001-01-CamA-MC", col=1), 5)
    assert cell.value == "1年"
    assert fill_color(cell) == argb(RETENTION_1Y[0])
    assert font_color(cell) == argb(RETENTION_1Y[1])


def test_retention_unknown_is_red_text_with_label(sheet1):
    cell = at(sheet1, find_row(sheet1, "40000001-01-LoggerA-400", col=1), 5)
    assert cell.value == "不明(3年扱い)"
    assert fill_color(cell) is None
    assert font_color(cell) == argb(COLOR_REVIEW_TEXT)


def test_font_is_meiryo_everywhere(result):
    assert FONT_NAME == "Meiryo"
    for worksheet in load(result).worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                assert cell.font.name == FONT_NAME


def test_no_chinese_font_used(result):
    with zipfile.ZipFile(io.BytesIO(build_workbook(result))) as archive:
        styles = archive.read("xl/styles.xml").decode("utf-8")
    assert FONT_NAME in styles
    for font in CHINESE_FONTS:
        assert font not in styles


def test_tenants_without_billable_devices_are_listed_at_the_end(sheet1):
    heading = find_row(sheet1, "課金地点なしのテナント")
    assert heading > find_row_containing(sheet1, "■ TNT004")
    assert [at(sheet1, heading + offset, 0).value for offset in (1, 2)] == [
        "TNT003 デモ商会（除外）",
        "TNT005 取得失敗テナント（取得失敗）",
    ]


def test_price_table_used_is_printed(sheet1):
    heading = find_row(sheet1, "使用単価表（2025/09/01 適用版）")
    band = find_row(sheet1, "〜500")
    camera = find_row(sheet1, "カメラ地点")
    assert heading < band < camera
    assert (at(sheet1, band, 1).value, at(sheet1, band, 2).value) == (650, 450)
    assert (at(sheet1, camera, 1).value, at(sheet1, camera, 2).value) == (1800, 1400)


def test_footnotes_present(result, sheet1):
    start = find_row(sheet1, "脚注")
    count = len(footnote_lines(result))
    notes = "\n".join(str(at(sheet1, start + offset, 0).value) for offset in range(1, count + 1))
    for keyword in ("通信なし", "0 円", "課金対象外", "「デモ」", "日割り", "境界日のみ受信", "消えた地点"):
        assert keyword in notes


def test_footnote_on_deleted_devices_compares_with_previous_month(result):
    notes = footnote_lines(result)
    assert (
        "・集計を実行するまでに削除された地点は地点一覧に現れない。前月分の結果との差分を"
        "「消えた地点」としてシート2に列挙する（金額は計上しない）"
    ) in notes


def test_footnotes_explain_camera_then_provisional_price_after_retention(result):
    notes = footnote_lines(result)
    retention = next(index for index, line in enumerate(notes) if line.startswith("・保存期間が 1 年 / 3 年以外"))
    assert notes[retention + 1] == (
        "・カメラ地点: 課金対象期間の受信データに「Base64形式データ」の列がある地点、"
        "または設定の「カメラ扱いフォーマット名」に一致する地点にカメラ単価を適用する（項目数は見ない）"
    )
    assert notes[retention + 2] == (
        "・カメラ地点以外で、項目数が単価表の上限帯を超える地点は上限帯の仮単価で計算し、"
        "項目数が 0 の地点は 0 円とする。どちらも赤字で要確認を付ける"
    )


def test_column_width_counts_fullwidth_as_two():
    assert column_width(["現場A"]) == 7
    assert column_width(["abc"]) == 5
    assert column_width(["現場A", "abcdefgh"]) == 10
    assert column_width([]) == 2


def test_print_settings_are_landscape_a4_fit_to_one_page_wide(sheet1):
    assert sheet1.page_setup.orientation == "landscape"
    assert sheet1.page_setup.paperSize == 9
    assert sheet1.sheet_properties.pageSetUpPr.fitToPage is True
    assert int(sheet1.page_setup.fitToWidth or 1) == 1
    assert sheet1.page_setup.fitToHeight == 0
    assert sheet1.print_title_rows == "$1:$1"
    assert sheet1.oddFooter.center.text == "&P / &N"
