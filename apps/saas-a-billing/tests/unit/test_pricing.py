"""`pricing.py` のユニットテスト（設計 2.3 / 6.5 / 7.1 / 13 章）。"""

from datetime import date
from decimal import Decimal

import pytest
from saas_a_billing.pricing import (
    Band,
    PriceTable,
    PriceTableNotFoundError,
    default_price_table_item,
    resolve_price,
    select_table,
)

from tests.conftest import make_price_table


@pytest.fixture
def table() -> PriceTable:
    return PriceTable.from_item(make_price_table())


def price_items(*effective_froms: str) -> list[dict]:
    return [make_price_table(effective_from=value) for value in effective_froms]


@pytest.mark.parametrize(
    ("item_count", "expected"),
    [(500, 650), (501, 1000), (800, 1000), (801, 1200), (1600, 1200), (1601, 1350), (1700, 1350)],
)
def test_band_boundaries(table, item_count, expected):
    assert resolve_price(table, 3, item_count, False) == (expected, [])


@pytest.mark.parametrize(("item_count", "expected"), [(500, 450), (501, 700), (1600, 900), (1700, 1000)])
def test_band_boundaries_1y(table, item_count, expected):
    assert resolve_price(table, 1, item_count, False) == (expected, [])


def test_over_max_items_uses_last_band_and_flags(table):
    assert resolve_price(table, 3, 1701, False) == (1350, ["OVER_MAX_ITEMS"])


def test_camera_price_3y(table):
    assert resolve_price(table, 3, 100, True) == (1800, [])


def test_camera_price_1y(table):
    assert resolve_price(table, 1, 100, True) == (1400, [])


def test_camera_ignores_item_count(table):
    assert resolve_price(table, 3, 5000, True) == (1800, [])
    assert resolve_price(table, 1, 5000, True) == (1400, [])


def test_zero_items_returns_zero_with_no_items_flag(table):
    assert resolve_price(table, 3, 0, False) == (0, ["NO_ITEMS"])


def test_none_items_returns_zero_with_no_items_flag(table):
    assert resolve_price(table, 3, None, False) == (0, ["NO_ITEMS"])


def test_retention_none_uses_3y_and_flags_unknown(table):
    assert resolve_price(table, None, 450, False) == (650, ["RETENTION_UNKNOWN"])


def test_retention_5_uses_3y_and_flags_unknown(table):
    assert resolve_price(table, 5, 450, False) == (650, ["RETENTION_UNKNOWN"])


def test_camera_with_unknown_retention_flags_and_uses_3y(table):
    assert resolve_price(table, None, 100, True) == (1800, ["RETENTION_UNKNOWN"])


def test_zero_items_with_unknown_retention_has_both_flags(table):
    assert resolve_price(table, None, 0, False) == (0, ["RETENTION_UNKNOWN", "NO_ITEMS"])


def test_select_table_picks_latest_effective_on_or_before():
    items = price_items("2025-09-01", "2026-04-01")
    assert select_table(items, date(2026, 3, 31)).effective_from == "2025-09-01"


def test_select_table_same_day_is_included():
    items = price_items("2025-09-01", "2026-04-01")
    assert select_table(items, date(2026, 4, 1)).effective_from == "2026-04-01"


def test_select_table_ignores_future_versions():
    items = price_items("2025-09-01", "2026-04-01", "2027-01-01")
    assert select_table(items, date(2026, 12, 31)).effective_from == "2026-04-01"


@pytest.mark.parametrize("effective_froms", [(), ("2026-09-01", "2027-01-01")])
def test_select_table_raises_when_none_effective(effective_froms):
    with pytest.raises(PriceTableNotFoundError):
        select_table(price_items(*effective_froms), date(2026, 8, 31))


def test_default_price_table_matches_2355():
    table = PriceTable.from_item(default_price_table_item())
    assert table.effective_from == "2025-09-01"
    assert table.bands == [
        Band(max_items=500, price_3y=650, price_1y=450),
        Band(max_items=800, price_3y=1000, price_1y=700),
        Band(max_items=1600, price_3y=1200, price_1y=900),
        Band(max_items=1700, price_3y=1350, price_1y=1000),
    ]
    assert (table.camera_3y, table.camera_1y) == (1800, 1400)
    assert default_price_table_item("2026-04-01")["effective_from"] == "2026-04-01"


def test_price_table_from_item_converts_decimal_to_int():
    item = make_price_table(
        bands=[{"max_items": Decimal("800"), "price_3y": Decimal("1000"), "price_1y": Decimal("700")}],
        camera={"price_3y": Decimal("1800"), "price_1y": Decimal("1400")},
    )
    table = PriceTable.from_item(item)
    assert table.bands == [Band(max_items=800, price_3y=1000, price_1y=700)]
    values = (table.bands[0].max_items, table.bands[0].price_3y, table.camera_3y, table.camera_1y)
    assert all(isinstance(value, int) for value in values)


def test_from_item_sorts_bands_by_max_items():
    item = make_price_table(
        bands=[
            {"max_items": 1700, "price_3y": 1350, "price_1y": 1000},
            {"max_items": 500, "price_3y": 650, "price_1y": 450},
            {"max_items": 800, "price_3y": 1000, "price_1y": 700},
        ]
    )
    table = PriceTable.from_item(item)
    assert [band.max_items for band in table.bands] == [500, 800, 1700]
    assert resolve_price(table, 3, 501, False) == (1000, [])
