"""単価表の版解決と単価引き（設計 2.3 / 6.5 / 7.1）。

単価表はモジュール定数に持たず DynamoDB の版から受け取る（現行スキル
`aggregate_report.py` の `calc_fee` からの移植点）。締め日時点で有効な最新版を
`select_table` が選び、`resolve_price` が保存期間・項目数・カメラ判定から
単価と「要確認」フラグを返す。

`retention_period` が 1 / 3 以外は 3 年扱いの仮単価、項目数 0 は 0 円、
上限帯超過は上限帯の仮単価で、いずれも合計に含めたうえでフラグを付ける。
"""

from dataclasses import dataclass
from datetime import date


class PriceTableNotFoundError(Exception):
    """指定日時点で有効な単価表の版が無い。"""


@dataclass(frozen=True)
class Band:
    """項目数帯。項目数が `max_items` 以下の地点にこの帯の単価を適用する。"""

    max_items: int
    price_3y: int
    price_1y: int


@dataclass(frozen=True)
class PriceTable:
    """`effective_from` から有効な単価表の 1 版。`bands` は `max_items` 昇順。"""

    effective_from: str
    bands: list[Band]
    camera_3y: int
    camera_1y: int
    note: str = ""

    @classmethod
    def from_item(cls, item: dict) -> "PriceTable":
        """DynamoDB の `PRICE#EFFECTIVE#...` アイテムから作る（数値は `Decimal` で来る）。"""
        camera = item["camera"]
        bands = [
            Band(max_items=int(band["max_items"]), price_3y=int(band["price_3y"]), price_1y=int(band["price_1y"]))
            for band in item["bands"]
        ]
        return cls(
            effective_from=item["effective_from"],
            bands=sorted(bands, key=lambda band: band.max_items),
            camera_3y=int(camera["price_3y"]),
            camera_1y=int(camera["price_1y"]),
            note=item.get("note", ""),
        )


def resolve_price(
    table: PriceTable, retention: int | None, item_count: int | None, is_camera: bool
) -> tuple[int, list[str]]:
    """(単価, 要確認フラグ) を返す。`retention` が 1 のときだけ 1 年列を使う。"""
    use_1y = retention == 1
    flags = [] if retention in (1, 3) else ["RETENTION_UNKNOWN"]
    if is_camera:
        return (table.camera_1y if use_1y else table.camera_3y), flags
    if item_count is None or item_count <= 0:
        return 0, [*flags, "NO_ITEMS"]
    for band in table.bands:
        if item_count <= band.max_items:
            return (band.price_1y if use_1y else band.price_3y), flags
    last = table.bands[-1]
    return (last.price_1y if use_1y else last.price_3y), [*flags, "OVER_MAX_ITEMS"]


def select_table(items: list[dict], as_of: date) -> PriceTable:
    """`effective_from <= as_of` の版のうち最新（同日の版は有効）。"""
    as_of_text = as_of.isoformat()
    effective = [item for item in items if item["effective_from"] <= as_of_text]
    if not effective:
        raise PriceTableNotFoundError(f"{as_of_text} 時点で有効な単価表の版がありません")
    return PriceTable.from_item(max(effective, key=lambda item: item["effective_from"]))


def default_price_table_item(effective_from: str = "2025-09-01") -> dict:
    """設計 2.3 の既定値（support-tracker #2001）を持つ単価表アイテムの属性。seed 用。"""
    return {
        "effective_from": effective_from,
        "bands": [
            {"max_items": 500, "price_3y": 650, "price_1y": 450},
            {"max_items": 800, "price_3y": 1000, "price_1y": 700},
            {"max_items": 1600, "price_3y": 1200, "price_1y": 900},
            {"max_items": 1700, "price_3y": 1350, "price_1y": 1000},
        ],
        "camera": {"price_3y": 1800, "price_1y": 1400},
        "note": "support-tracker #2001",
    }
