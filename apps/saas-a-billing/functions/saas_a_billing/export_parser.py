"""export API の CSV をストリームで解析する（設計 2.4 / 6.4）。

行を保持せず、受信あり地点の集合・カメラ地点の集合・地点ごとの初回/最終受信日時
だけを積む。CSV はフォーマットごとのブロックに分かれ、ブロック先頭にヘッダ行
`field_name,point_cd,point_name,change_datetime,...` が再出現する。ヘッダ行に
「Base64形式データ」列を含むブロックに現れた地点をカメラ地点とみなす。

現場名は複数現場所属だと `"現場A,現場B"` のように引用符付きで来るため
`split(",")` を使わず `csv.reader` に任せる。`change_datetime` は
`YYYY/MM/DD HH:MM:SS` 固定長なので文字列のまま大小比較できる。

`cutoff_time` が `24:00` 以外だと窓の両端の日に課金対象期間外の行が混ざるため、
行ごとに `in_period` で絞り込む（境界日の時刻絞り込み）。
"""

import csv
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from saas_a_billing.period import ClosingPeriod, in_period

ENCODING = "cp932"
HEADER_FIRST_COLUMN = "field_name"
CAMERA_COLUMN = "Base64形式データ"
NO_RECORDS_STATUS = "Error.Export.NoRecords"
POINT_CD_INDEX = 1
CHANGE_DATETIME_INDEX = 3

_MAX_TIMESTAMP = "9999"
_LIMIT_OVER_PATTERN = re.compile(r"^Error\.Export\.LimitOver,(\d+),(\d+)$")


@dataclass
class ExportResult:
    """1 テナント分の受信状況（設計 6.4）。窓をまたいで積み増す。

    `rows` は読んだデータ行数（ヘッダ行・空行は数えない。課金対象期間外の行も
    含む）、`bytes_read` は `parse_response` が読んだバイト数で、いずれも
    ログ用の計数のみ。行そのものは保持しない。
    """

    received: set[str] = field(default_factory=set)
    camera: set[str] = field(default_factory=set)
    first_seen: dict[str, str] = field(default_factory=dict)
    last_seen: dict[str, str] = field(default_factory=dict)
    rows: int = 0
    bytes_read: int = 0

    def merge(self, other: "ExportResult") -> "ExportResult":
        """複数窓の結果を合成した新しい結果を返す（自身と `other` は変えない）。"""
        first_seen = dict(self.first_seen)
        for point_cd, ts in other.first_seen.items():
            if ts < first_seen.get(point_cd, _MAX_TIMESTAMP):
                first_seen[point_cd] = ts
        last_seen = dict(self.last_seen)
        for point_cd, ts in other.last_seen.items():
            if ts > last_seen.get(point_cd, ""):
                last_seen[point_cd] = ts
        return ExportResult(
            received=self.received | other.received,
            camera=self.camera | other.camera,
            first_seen=first_seen,
            last_seen=last_seen,
            rows=self.rows + other.rows,
            bytes_read=self.bytes_read + other.bytes_read,
        )


def parse_lines(lines: Iterable[str], period: ClosingPeriod, result: ExportResult) -> None:
    """CSV の行を読み、既存の `result` に受信状況を積む（設計 6.4）。"""
    in_camera_block = False
    for row in csv.reader(lines):
        if not row:
            continue
        if row[0] == HEADER_FIRST_COLUMN:
            in_camera_block = CAMERA_COLUMN in row
            continue
        result.rows += 1
        point_cd = row[POINT_CD_INDEX]
        if len(row) > CHANGE_DATETIME_INDEX:
            ts = row[CHANGE_DATETIME_INDEX]
            if not in_period(ts, period):
                continue
            if ts < result.first_seen.get(point_cd, _MAX_TIMESTAMP):
                result.first_seen[point_cd] = ts
            if ts > result.last_seen.get(point_cd, ""):
                result.last_seen[point_cd] = ts
        result.received.add(point_cd)
        if in_camera_block:
            result.camera.add(point_cd)


def parse_response(raw_lines: Iterable[bytes], period: ClosingPeriod, result: ExportResult) -> None:
    """`resp.iter_lines()` のバイト列を cp932 で復号して `parse_lines` に流す。"""

    def decoded() -> Iterator[str]:
        for raw in raw_lines:
            result.bytes_read += len(raw)
            yield raw.decode(ENCODING, errors="replace")

    parse_lines(decoded(), period, result)


def is_no_records(body: bytes | str) -> bool:
    """受信なしの応答か（0 バイト、または `errorStatus` が `Error.Export.NoRecords`）。"""
    if not body or not body.strip():
        return True
    try:
        parsed = json.loads(body)
    except ValueError:
        return False
    return isinstance(parsed, dict) and parsed.get("errorStatus") == NO_RECORDS_STATUS


def parse_limit_over(message: str) -> tuple[int, int] | None:
    """`Error.Export.LimitOver,<件数>,<上限>` を (件数, 上限) に分解する。該当しなければ None。"""
    matched = _LIMIT_OVER_PATTERN.match((message or "").strip())
    if matched is None:
        return None
    return int(matched.group(1)), int(matched.group(2))
