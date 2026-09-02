"""課金レポート Excel の生成（設計 3.5 / 6.5 / 6.6）。

`result.json` 相当の dict だけを入力に、XlsxWriter で `io.BytesIO` に書いて bytes を
返す（`/tmp` は使わない）。シート1「請求照合」はベンダーA請求書との突合用で判定が
「課金」の地点だけを載せ、シート2「全地点（管理用）」は全地点をテーブルに載せる。

全セルの既定書式は Meiryo 10pt（中華フォントへフォールバックさせない）。数値は
数値型・通貨は `"¥"#,##0`、日時は JST の naive `datetime` で書き、合計は Python 側で
計算した値を書く（数式は使わない）。色分けも Python 側で判定して静的に塗る。
"""

import io
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timedelta

import xlsxwriter

from saas_a_billing import billing
from saas_a_billing.period import CHANGE_DATETIME_FORMAT

COLOR_NO_DATA = "#FDF3DC"
COLOR_EXCLUDED = "#DFE9F8"
COLOR_FAILED = "#E0E0E0"
COLOR_MISSING_TEXT = "#808080"
COLOR_REVIEW_TEXT = "#C00000"
RETENTION_3Y = ("#C6EFCE", "#006100")
RETENTION_1Y = ("#E4DFEC", "#3F3151")

FONT_NAME = "Meiryo"
FONT_SIZE = 10
CURRENCY_FORMAT = '"¥"#,##0'
DATETIME_FORMAT = "yyyy/mm/dd hh:mm"
COUNT_FORMAT = "#,##0"

SHEET1_NAME = "請求照合"
SHEET2_NAME = "全地点（管理用）"

SHEET1_COLUMNS = (
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

SUMMARY_COLUMNS = (
    "テナントCD",
    "テナント名",
    "保存期間",
    "地点数",
    "課金",
    "通信なし",
    "除外",
    "要確認",
    "取得失敗",
    "小計",
)

DETAIL_COLUMNS = (
    "テナントCD",
    "テナント名",
    "保存期間",
    "現場",
    "地点CD",
    "地点名",
    "フォーマット",
    "項目数",
    "カメラ",
    "最終受信日時",
    "判定",
    "単価",
    "金額",
    "要確認",
    "備考",
)
DETAIL_TABLE_NAME = "all_devices"

JUDGEMENT_LABELS = {"billable": "課金", "no_data": "通信なし", "excluded": "除外", "failed": "取得失敗"}
CAMERA_SOURCE_LABELS = {"export": "export", "settings": "設定"}
MISSING_LABEL = "消えた地点"
ROW_FILLS = {"no_data": COLOR_NO_DATA, "excluded": COLOR_EXCLUDED, "failed": COLOR_FAILED}
REVIEW_REASONS = {
    "OVER_MAX_ITEMS": "項目数が上限帯を超過・上限帯の仮単価（OVER_MAX_ITEMS）",
    "NO_ITEMS": "項目数が 0・0 円（NO_ITEMS）",
    "RETENTION_UNKNOWN": "保存期間が不明・3年扱いの仮単価（RETENTION_UNKNOWN）",
}
TARGET_ENV_LABELS = {"verification": "検証環境", "production": "本番環境"}

_DISPLAY_DATE_FORMAT = "%Y/%m/%d"
_DISPLAY_DATETIME_FORMAT = "%Y/%m/%d %H:%M"
_DISPLAY_SECONDS_FORMAT = "%Y/%m/%d %H:%M:%S"


def _text_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in value)


def column_width(values: Iterable[str]) -> int:
    """全角を 2 と数えた最大幅 + 2（設計 6.6）。"""
    return max((_text_width(value) for value in values), default=0) + 2


def _month_label(month: str) -> str:
    """`2026-10` → `2026年10月分`。"""
    year, target = month.split("-")
    return f"{int(year)}年{int(target)}月分"


def _parse_iso(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _retention_label(retention: int | None) -> str:
    return f"{retention}年" if retention in (1, 3) else "不明(3年扱い)"


def _review_reason(flags: list[str]) -> str:
    return "、".join(REVIEW_REASONS.get(flag, flag) for flag in flags)


def _parse_received(value: str | None) -> datetime | None:
    """`2026/10/20 23:55:00` を JST の naive `datetime` にする。"""
    return datetime.strptime(value, CHANGE_DATETIME_FORMAT) if value else None


def _remarks(device: dict) -> str:
    parts = (device.get("device_description") or "", device.get("note") or "")
    return " / ".join(part for part in parts if part)


def _exclude_keywords_label(result: dict) -> str:
    keywords = (result.get("settings_snapshot") or {}).get("exclude_keywords") or []
    return "".join(f"「{keyword}」" for keyword in keywords) if keywords else "（設定なし）"


def _failed_tenants(result: dict) -> list[str]:
    return [
        f"{tenant.get('tenant_cd', '')} {tenant.get('tenant_name', '')}（集計外）"
        for tenant in result.get("tenants") or []
        if tenant.get("status") == "failed"
    ]


def _sort_devices(devices: list[dict]) -> list[dict]:
    """`billing.sort_devices` はデータクラス向けなので、dict の `devices[]` はここで並べる。"""
    return sorted(devices, key=lambda device: ((device.get("group_cds") or [""])[0], device.get("device_cd") or ""))


def _billable(tenant: dict) -> list[dict]:
    return [device for device in tenant.get("devices") or [] if device.get("judgement") == "billable"]


def _billable_devices(result: dict) -> list[dict]:
    return [device for tenant in result.get("tenants") or [] for device in _billable(tenant)]


def _amount(device: dict) -> int:
    return int(device.get("amount") or 0)


def _cell_properties(fill: str | None = None, font_color: str | None = None, **extra) -> dict:
    properties = dict(extra)
    if fill:
        properties["bg_color"] = fill
    if font_color:
        properties["font_color"] = font_color
    return properties


def spec_block_lines(result: dict) -> list[str]:
    """両シートの先頭に置く処理仕様ブロック 7 行（設計 6.6）。"""
    run = result.get("run") or {}
    period = result.get("closing_period") or {}
    month = result.get("month") or ""
    year, target = (int(part) for part in month.split("-"))
    processed_at = _parse_iso(run.get("finished_at") or run.get("started_at"))
    start = _parse_iso(period.get("from"))
    end = _parse_iso(period.get("to"))
    cutoff = datetime(year, target, int(period.get("cutoff_day") or 0))
    target_env = result.get("target_env") or run.get("target_env") or ""
    failed = _failed_tenants(result)
    return [
        f"SaaS_A 月額課金レポート  {_month_label(month)}",
        f"集計処理日時: {processed_at.strftime(_DISPLAY_DATETIME_FORMAT)}"
        f"（締め日時: {cutoff.strftime(_DISPLAY_DATE_FORMAT)} {period.get('cutoff_time', '')}）",
        f"集計対象: {TARGET_ENV_LABELS.get(target_env, target_env)}（{run.get('saasa_host', '')}）",
        f"課金対象期間: {start.strftime(_DISPLAY_SECONDS_FORMAT)} 〜 "
        f"{(end - timedelta(seconds=1)).strftime(_DISPLAY_SECONDS_FORMAT)}"
        " に受信データがある地点を課金対象とする（受信日時基準・JST）",
        f"除外: テナント説明欄または地点備考欄に{_exclude_keywords_label(result)}を含む地点は課金対象外",
        f"使用単価表: {(result.get('price_table') or {}).get('effective_from', '').replace('-', '/')} 適用版"
        f"   環境: {run.get('env', '')}   試行: {run.get('attempt', '')} 回目",
        f"取得失敗テナント: {'、'.join(failed) if failed else 'なし'}",
    ]


def footnote_lines(result: dict) -> list[str]:
    """シート1 末尾の脚注（設計 2.5 / 6.6）。"""
    return [
        "・課金対象期間に受信データがある地点だけを課金する。通信なしの地点は単価を表示するが 0 円で計上する",
        f"・テナント説明欄または地点備考欄に{_exclude_keywords_label(result)}を含む地点は課金対象外（0 円）。"
        "地点名・地点CD・現場名は除外判定に使わない",
        "・保存期間が 1 年 / 3 年以外のテナントは 3 年扱いの仮単価で計算し、赤字で要確認を付ける",
        "・課金対象期間中に削除された地点は地点一覧に現れない。前回 RUN との差分を"
        "「消えた地点」としてシート2に列挙する（金額は計上しない）",
        "・課金対象期間中に追加された地点は、受信があれば 1 か月分として課金する（日割りなし）",
        "・ベンダーA側の締め処理時刻と微差が出うるため、受信が課金対象期間の初日のみ・最終日のみだった地点は"
        "シート2の備考に「境界日のみ受信」と印を付ける",
    ]


class _Formats:
    """同じ書式を使い回すキャッシュ。既定は Meiryo 10pt。"""

    def __init__(self, workbook):
        self._workbook = workbook
        self._cache: dict[tuple, object] = {}

    def get(self, **properties):
        key = tuple(sorted(properties.items()))
        if key not in self._cache:
            self._cache[key] = self._workbook.add_format({"font_name": FONT_NAME, "font_size": FONT_SIZE, **properties})
        return self._cache[key]

    def heading(self, size: int = FONT_SIZE + 1):
        return self.get(bold=True, font_size=size)

    def header(self):
        return self.get(bold=True, bottom=1, align="center")

    def retention(self, retention: int | None, fill: str | None = None):
        """保存期間セル（行色より優先して塗る。不明は塗らずに赤字）。"""
        if retention == 3:
            return self.get(bg_color=RETENTION_3Y[0], font_color=RETENTION_3Y[1], align="center")
        if retention == 1:
            return self.get(bg_color=RETENTION_1Y[0], font_color=RETENTION_1Y[1], align="center")
        return self.get(**_cell_properties(fill, COLOR_REVIEW_TEXT, align="center"))


class _Writer:
    """列幅を数えながら書き込むワークシートのラッパ。"""

    def __init__(self, worksheet):
        self.worksheet = worksheet
        self._widths: dict[int, int] = {}

    def write(self, row: int, col: int, value, fmt=None) -> None:
        if isinstance(value, datetime):
            self.worksheet.write_datetime(row, col, value, fmt)
        elif value is None or value == "":
            self.worksheet.write_blank(row, col, None, fmt)
        else:
            self.worksheet.write(row, col, value, fmt)
        self._widths[col] = max(self._widths.get(col, 0), column_width([_display(value)]))

    def row(self, row: int, cells: list[tuple]) -> None:
        for col, (value, fmt) in enumerate(cells):
            self.write(row, col, value, fmt)

    def line(self, row: int, text: str, fmt=None) -> None:
        """列幅に数えない 1 行のテキスト（処理仕様・見出し・脚注）。"""
        self.worksheet.write(row, 0, text, fmt)

    def apply_widths(self) -> None:
        for col, width in self._widths.items():
            self.worksheet.set_column(col, col, width)


def _display(value) -> str:
    if isinstance(value, datetime):
        return value.strftime(_DISPLAY_DATETIME_FORMAT)
    if isinstance(value, int):
        return f"{value:,}"
    return "" if value is None else str(value)


def build_workbook(result: dict) -> bytes:
    """`result.json` 相当の dict から Excel のバイト列を作る（設計 6.6 / 6.7）。"""
    stream = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        stream,
        {"in_memory": True, "default_format_properties": {"font_name": FONT_NAME, "font_size": FONT_SIZE}},
    )
    _write_sheet1(workbook, result)
    _write_sheet2(workbook, result)
    workbook.close()
    return stream.getvalue()


def _write_spec_block(writer: _Writer, formats: _Formats, result: dict) -> int:
    lines = spec_block_lines(result)
    has_failed = bool(_failed_tenants(result))
    for index, line in enumerate(lines):
        if index == 0:
            fmt = formats.heading(FONT_SIZE + 2)
        elif index == len(lines) - 1 and has_failed:
            fmt = formats.get(font_color=COLOR_REVIEW_TEXT)
        else:
            fmt = formats.get()
        writer.line(index, line, fmt)
    return len(lines) + 1


def _write_total_block(writer: _Writer, formats: _Formats, result: dict, row: int) -> int:
    devices = _billable_devices(result)
    breakdown = [
        ("課金地点", devices),
        (f"保存期間 {_retention_label(3)}", [device for device in devices if device.get("retention") != 1]),
        (f"保存期間 {_retention_label(1)}", [device for device in devices if device.get("retention") == 1]),
        ("うちカメラ", [device for device in devices if device.get("is_camera")]),
    ]
    writer.line(row, "合計", formats.heading())
    writer.row(row + 1, [(name, formats.header()) for name in ("区分", "地点数", "金額")])
    for offset, (name, group) in enumerate(breakdown, start=2):
        writer.row(
            row + offset,
            [
                (name, formats.get()),
                (len(group), formats.get(num_format=COUNT_FORMAT)),
                (sum(_amount(device) for device in group), formats.get(num_format=CURRENCY_FORMAT)),
            ],
        )
    return row + len(breakdown) + 3


def _sheet1_device_cells(formats: _Formats, device: dict) -> list[tuple]:
    flags = device.get("flags") or []
    color = COLOR_REVIEW_TEXT if flags else None
    text = formats.get(**_cell_properties(font_color=color))
    count = formats.get(**_cell_properties(font_color=color, num_format=COUNT_FORMAT))
    currency = formats.get(**_cell_properties(font_color=color, num_format=CURRENCY_FORMAT))
    center = formats.get(**_cell_properties(font_color=color, align="center"))
    retention = device.get("retention")
    return [
        ("、".join(device.get("group_names") or []), text),
        (device.get("device_cd") or "", text),
        (device.get("device_name") or "", text),
        (device.get("format_name") or "", text),
        (int(device.get("item_count") or 0), count),
        (_retention_label(retention), formats.retention(retention)),
        ("○" if device.get("is_camera") else "-", center),
        (int(device.get("unit_price") or 0), currency),
        (_amount(device), currency),
        (_review_reason(flags), text),
    ]


def _write_tenant_block(writer: _Writer, formats: _Formats, tenant: dict, row: int) -> int:
    devices = _sort_devices(_billable(tenant))
    subtotal = sum(_amount(device) for device in devices)
    retention = tenant.get("retention_period")
    writer.write(row, 0, f"■ {tenant.get('tenant_cd', '')} {tenant.get('tenant_name', '')}", formats.heading())
    writer.write(row, 5, _retention_label(retention), formats.retention(retention))
    writer.write(row, 7, f"課金 {len(devices)} 地点 ｜ 小計", formats.get(bold=True, align="right"))
    writer.write(row, 8, subtotal, formats.get(bold=True, num_format=CURRENCY_FORMAT))
    writer.row(row + 1, [(name, formats.header()) for name in SHEET1_COLUMNS])
    for offset, device in enumerate(devices, start=2):
        writer.row(row + offset, _sheet1_device_cells(formats, device))
    subtotal_row = row + len(devices) + 2
    writer.write(subtotal_row, 7, "小計", formats.get(bold=True, align="right", top=1))
    writer.write(subtotal_row, 8, subtotal, formats.get(bold=True, num_format=CURRENCY_FORMAT, top=1))
    return subtotal_row + 2


def _no_billable_reason(tenant: dict) -> str:
    if tenant.get("status") == "failed":
        return JUDGEMENT_LABELS["failed"]
    judgements = {device.get("judgement") for device in tenant.get("devices") or []}
    labels = [JUDGEMENT_LABELS[key] for key in ("no_data", "excluded", "failed") if key in judgements]
    return "・".join(labels) or "地点なし"


def _write_no_billable_tenants(writer: _Writer, formats: _Formats, tenants: list[dict], row: int) -> int:
    targets = [tenant for tenant in tenants if not _billable(tenant)]
    writer.line(row, "課金地点なしのテナント", formats.heading())
    lines = [
        f"{tenant.get('tenant_cd', '')} {tenant.get('tenant_name', '')}（{_no_billable_reason(tenant)}）"
        for tenant in targets
    ] or ["（なし）"]
    for offset, line in enumerate(lines, start=1):
        writer.line(row + offset, line, formats.get())
    return row + len(lines) + 2


def _write_price_table(writer: _Writer, formats: _Formats, price_table: dict, row: int) -> int:
    effective_from = (price_table.get("effective_from") or "").replace("-", "/")
    writer.line(row, f"使用単価表（{effective_from} 適用版）", formats.heading())
    writer.row(row + 1, [(name, formats.header()) for name in ("項目数帯", "3年保存", "1年保存")])
    currency = formats.get(num_format=CURRENCY_FORMAT)
    bands = [
        (f"〜{int(band['max_items']):,}", int(band["price_3y"]), int(band["price_1y"]))
        for band in price_table.get("bands") or []
    ]
    camera = price_table.get("camera") or {}
    bands.append(("カメラ地点", int(camera.get("price_3y") or 0), int(camera.get("price_1y") or 0)))
    for offset, (label, price_3y, price_1y) in enumerate(bands, start=2):
        writer.row(row + offset, [(label, formats.get()), (price_3y, currency), (price_1y, currency)])
    return row + len(bands) + 3


def _write_footnotes(writer: _Writer, formats: _Formats, result: dict, row: int) -> int:
    writer.line(row, "脚注", formats.heading())
    lines = footnote_lines(result)
    for offset, line in enumerate(lines, start=1):
        writer.line(row + offset, line, formats.get())
    return row + len(lines) + 1


def _write_sheet1(workbook, result: dict) -> None:
    """判定が「課金」の地点だけを載せる請求照合シート（設計 6.6）。"""
    worksheet = workbook.add_worksheet(SHEET1_NAME)
    formats = _Formats(workbook)
    writer = _Writer(worksheet)
    tenants = billing.sort_tenants(result.get("tenants") or [])
    row = _write_spec_block(writer, formats, result)
    row = _write_total_block(writer, formats, result, row)
    for tenant in tenants:
        if _billable(tenant):
            row = _write_tenant_block(writer, formats, tenant, row)
    row = _write_no_billable_tenants(writer, formats, tenants, row)
    row = _write_price_table(writer, formats, result.get("price_table") or {}, row)
    _write_footnotes(writer, formats, result, row)
    writer.apply_widths()
    worksheet.set_landscape()
    worksheet.set_paper(9)
    worksheet.fit_to_pages(1, 0)
    worksheet.repeat_rows(0)
    worksheet.set_footer("&C&P / &N")


def _write_judgement_counts(writer: _Writer, formats: _Formats, result: dict, row: int) -> int:
    summary = result.get("summary") or {}
    items = [
        (JUDGEMENT_LABELS["billable"], summary.get("billable_count", 0)),
        (JUDGEMENT_LABELS["no_data"], summary.get("no_data_count", 0)),
        (JUDGEMENT_LABELS["excluded"], summary.get("excluded_count", 0)),
        ("要確認（判定を問わず）", summary.get("review_count", 0)),
        ("取得失敗（テナント）", summary.get("failed_tenant_count", 0)),
        (MISSING_LABEL, summary.get("missing_count", 0)),
    ]
    writer.line(row, "判定別件数", formats.heading())
    writer.row(row + 1, [(name, formats.header()) for name in ("判定", "件数")])
    for offset, (name, count) in enumerate(items, start=2):
        writer.row(row + offset, [(name, formats.get()), (int(count), formats.get(num_format=COUNT_FORMAT))])
    return row + len(items) + 3


def _tenant_counts(tenant: dict) -> dict:
    """テナント別サマリの件数と小計（要確認は判定を問わずフラグの付いた地点）。"""
    devices = tenant.get("devices") or []
    judgements = [device.get("judgement") for device in devices]
    return {
        "device_count": len(devices),
        "billable": judgements.count("billable"),
        "no_data": judgements.count("no_data"),
        "excluded": judgements.count("excluded"),
        "review": sum(1 for device in devices if device.get("flags")),
        "failed": judgements.count("failed"),
        "subtotal": sum(_amount(device) for device in devices),
    }


def _write_tenant_summary(writer: _Writer, formats: _Formats, tenants: list[dict], row: int) -> int:
    writer.line(row, "テナント別サマリ", formats.heading())
    writer.row(row + 1, [(name, formats.header()) for name in SUMMARY_COLUMNS])
    count = formats.get(num_format=COUNT_FORMAT)
    for offset, tenant in enumerate(tenants, start=2):
        counts = _tenant_counts(tenant)
        retention = tenant.get("retention_period")
        writer.row(
            row + offset,
            [
                (tenant.get("tenant_cd") or "", formats.get()),
                (tenant.get("tenant_name") or "", formats.get()),
                (_retention_label(retention), formats.retention(retention)),
                (counts["device_count"], count),
                (counts["billable"], count),
                (counts["no_data"], count),
                (counts["excluded"], count),
                (counts["review"], count),
                (counts["failed"], count),
                (counts["subtotal"], formats.get(num_format=CURRENCY_FORMAT)),
            ],
        )
    return row + len(tenants) + 3


def _detail_cells(formats: _Formats, tenant: dict, device: dict) -> list[tuple]:
    judgement = device.get("judgement") or ""
    flags = device.get("flags") or []
    fill = ROW_FILLS.get(judgement)
    color = COLOR_REVIEW_TEXT if flags else None
    retention = device.get("retention")
    strikeout = {"font_strikeout": True} if judgement == "excluded" else {}
    text = formats.get(**_cell_properties(fill, color))
    count = formats.get(**_cell_properties(fill, color, num_format=COUNT_FORMAT))
    center = formats.get(**_cell_properties(fill, color, align="center"))
    received_at = formats.get(**_cell_properties(fill, color, num_format=DATETIME_FORMAT))
    unit_price = formats.get(**_cell_properties(fill, color, num_format=CURRENCY_FORMAT, **strikeout))
    currency = formats.get(**_cell_properties(fill, color, num_format=CURRENCY_FORMAT))
    return [
        (tenant.get("tenant_cd") or "", text),
        (tenant.get("tenant_name") or "", text),
        (_retention_label(retention), formats.retention(retention, fill)),
        ("、".join(device.get("group_names") or []), text),
        (device.get("device_cd") or "", text),
        (device.get("device_name") or "", text),
        (device.get("format_name") or "", text),
        (int(device.get("item_count") or 0), count),
        (CAMERA_SOURCE_LABELS.get(device.get("camera_source"), "-"), center),
        (_parse_received(device.get("last_received_at")), received_at),
        (JUDGEMENT_LABELS.get(judgement, judgement), center),
        (int(device.get("unit_price") or 0), unit_price),
        (None if judgement == "failed" else _amount(device), currency),
        (_review_reason(flags), text),
        (_remarks(device), text),
    ]


def _missing_cells(formats: _Formats, missing: dict) -> list[tuple]:
    """消えた地点は灰字で明細の末尾に並べる。金額は計上しない（設計 6.5）。"""
    text = formats.get(font_color=COLOR_MISSING_TEXT)
    center = formats.get(font_color=COLOR_MISSING_TEXT, align="center")
    blank = (None, text)
    return [
        (missing.get("tenant_cd") or "", text),
        (missing.get("tenant_name") or "", text),
        blank,
        ("、".join(missing.get("group_names") or []), text),
        (missing.get("device_cd") or "", text),
        (missing.get("device_name") or "", text),
        blank,
        blank,
        ("-", center),
        blank,
        (MISSING_LABEL, center),
        blank,
        blank,
        blank,
        (f"前回 RUN（{missing.get('last_seen_month', '')}）に存在", text),
    ]


def _write_detail_table(writer: _Writer, formats: _Formats, result: dict, row: int) -> int:
    tenants = billing.sort_tenants(result.get("tenants") or [])
    rows = [
        _detail_cells(formats, tenant, device)
        for tenant in tenants
        for device in _sort_devices(tenant.get("devices") or [])
    ]
    rows += [_missing_cells(formats, missing) for missing in result.get("missing_devices") or []]
    header_format = formats.get(bold=True)
    writer.line(row, "全地点明細", formats.heading())
    header_row = row + 1
    writer.row(header_row, [(name, header_format) for name in DETAIL_COLUMNS])
    for offset, cells in enumerate(rows, start=1):
        writer.row(header_row + offset, cells)
    if rows:
        writer.worksheet.add_table(
            header_row,
            0,
            header_row + len(rows),
            len(DETAIL_COLUMNS) - 1,
            {
                "name": DETAIL_TABLE_NAME,
                "banded_rows": False,
                "columns": [{"header": name, "header_format": header_format} for name in DETAIL_COLUMNS],
            },
        )
    return header_row + len(rows) + 1


def _write_sheet2(workbook, result: dict) -> None:
    """全地点を載せる管理用シート（設計 6.6）。サマリが上にあるため枠の固定はしない。"""
    worksheet = workbook.add_worksheet(SHEET2_NAME)
    formats = _Formats(workbook)
    writer = _Writer(worksheet)
    row = _write_spec_block(writer, formats, result)
    row = _write_judgement_counts(writer, formats, result, row)
    row = _write_tenant_summary(writer, formats, billing.sort_tenants(result.get("tenants") or []), row)
    _write_detail_table(writer, formats, result, row)
    writer.apply_widths()
