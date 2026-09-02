"""`period.py` のユニットテスト（設計 2.1 / 6.2 / 6.4 / 7.3 / 13 章）。"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta

import pytest
from saas_a_billing.period import (
    EXPORT_WINDOW_DAYS_DEFAULT,
    JST,
    closing_period,
    cutoff_at,
    cutoff_date,
    in_period,
    latest_cutoff,
    split_window,
    target_month,
    windows,
)


def jst(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=JST)


def test_cutoff_at_24_00_is_next_day_midnight():
    assert cutoff_at(2026, 10, 20, "24:00") == jst(2026, 10, 21)


def test_cutoff_at_00_00_is_same_day_midnight():
    assert cutoff_at(2026, 10, 20, "00:00") == jst(2026, 10, 20)


def test_cutoff_at_arbitrary_time():
    assert cutoff_at(2026, 10, 20, "18:30") == jst(2026, 10, 20, 18, 30)


@pytest.mark.parametrize("value", ["25:00", "1830", "24:01", "18:60", "9:30", "18:30:00", ""])
def test_cutoff_at_rejects_invalid_time(value):
    with pytest.raises(ValueError):
        cutoff_at(2026, 10, 20, value)


@pytest.mark.parametrize("cutoff_day", list(range(1, 29)))
def test_cutoff_day_1_to_28(cutoff_day):
    assert cutoff_at(2026, 2, cutoff_day, "18:30") == jst(2026, 2, cutoff_day, 18, 30)
    assert cutoff_at(2026, 2, cutoff_day, "24:00") == jst(2026, 2, cutoff_day) + timedelta(days=1)
    assert cutoff_date(2026, 2, cutoff_day) == date(2026, 2, cutoff_day)


def test_cutoff_at_february_end_handles_leap_year():
    assert cutoff_at(2028, 2, 28, "24:00") == jst(2028, 2, 29)
    assert cutoff_at(2026, 2, 28, "24:00") == jst(2026, 3, 1)


def test_latest_cutoff_before_cutoff_returns_previous_month():
    assert latest_cutoff(jst(2026, 10, 20, 5), 20, "24:00") == jst(2026, 9, 21)


def test_latest_cutoff_on_cutoff_day_after_time_returns_this_month():
    assert latest_cutoff(jst(2026, 10, 21, 6), 20, "24:00") == jst(2026, 10, 21)
    assert latest_cutoff(jst(2026, 10, 21), 20, "24:00") == jst(2026, 10, 21)


def test_latest_cutoff_crosses_year():
    assert latest_cutoff(jst(2026, 1, 5, 6), 20, "24:00") == jst(2025, 12, 21)


def test_latest_cutoff_when_this_and_previous_cutoff_are_future():
    now = jst(2026, 2, 28, 23)
    assert latest_cutoff(now, 28, "24:00") == jst(2026, 1, 29)
    assert target_month(now, 28, "24:00") == "2026-01"


def test_target_month_for_24_00():
    assert target_month(jst(2026, 10, 21, 6), 20, "24:00") == "2026-10"


def test_target_month_for_normal_time():
    assert target_month(jst(2026, 10, 20, 19), 20, "18:30") == "2026-10"


def test_target_month_for_00_00():
    assert target_month(jst(2026, 10, 1, 6), 1, "00:00") == "2026-10"


def test_closing_period_default():
    period = closing_period("2026-10", 20, "24:00")
    assert period.start == jst(2026, 9, 21)
    assert period.end_exclusive == jst(2026, 10, 21)
    assert period.display_end == jst(2026, 10, 20, 23, 59, 59)
    assert period.first_date == date(2026, 9, 21)
    assert period.last_date == date(2026, 10, 20)
    assert period.cutoff_date == date(2026, 10, 20)
    assert period.cutoff_day == 20
    assert period.cutoff_time == "24:00"
    assert period.month == "2026-10"
    with pytest.raises(FrozenInstanceError):
        period.start = jst(2026, 9, 1)


def test_closing_period_cutoff_date_equals_last_date_for_24_00():
    period = closing_period("2026-10", 20, "24:00")
    assert period.cutoff_date == period.last_date


def test_closing_period_cutoff_date_differs_from_last_date_for_00_00():
    period = closing_period("2026-10", 20, "00:00")
    assert period.end_exclusive == jst(2026, 10, 20)
    assert period.display_end == jst(2026, 10, 19, 23, 59, 59)
    assert period.last_date == date(2026, 10, 19)
    assert period.cutoff_date == date(2026, 10, 20)
    assert period.month == "2026-10"


def test_closing_period_crosses_year():
    period = closing_period("2026-01", 20, "24:00")
    assert period.start == jst(2025, 12, 21)
    assert period.end_exclusive == jst(2026, 1, 21)
    assert period.month == "2026-01"


@pytest.mark.parametrize("value", ["2026-1", "202610", "2026-10-01", "2026-13", ""])
def test_closing_period_rejects_invalid_month(value):
    with pytest.raises(ValueError):
        closing_period(value, 20, "24:00")


def test_closing_period_start_is_inclusive_end_is_exclusive():
    period = closing_period("2026-10", 20, "24:00")
    assert in_period("2026/09/21 00:00:00", period) is True
    assert in_period("2026/10/21 00:00:00", period) is False
    assert in_period("2026/10/20 23:59:59", period) is True


def test_windows_splits_31_days_into_5():
    assert EXPORT_WINDOW_DAYS_DEFAULT == 7
    assert windows(date(2026, 8, 21), date(2026, 9, 20)) == [
        (date(2026, 8, 21), date(2026, 8, 27)),
        (date(2026, 8, 28), date(2026, 9, 3)),
        (date(2026, 9, 4), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 9, 17)),
        (date(2026, 9, 18), date(2026, 9, 20)),
    ]


def test_windows_single_day():
    assert windows(date(2026, 9, 20), date(2026, 9, 20)) == [(date(2026, 9, 20), date(2026, 9, 20))]


def test_windows_exact_multiple():
    assert windows(date(2026, 9, 1), date(2026, 9, 14)) == [
        (date(2026, 9, 1), date(2026, 9, 7)),
        (date(2026, 9, 8), date(2026, 9, 14)),
    ]


@pytest.mark.parametrize("days", [0, -1])
def test_windows_rejects_non_positive_days(days):
    with pytest.raises(ValueError):
        windows(date(2026, 9, 1), date(2026, 9, 14), days=days)


def test_split_window_even_days():
    assert split_window(date(2026, 8, 21), date(2026, 8, 28)) == (
        (date(2026, 8, 21), date(2026, 8, 24)),
        (date(2026, 8, 25), date(2026, 8, 28)),
    )


def test_split_window_odd_days():
    assert split_window(date(2026, 8, 21), date(2026, 8, 27)) == (
        (date(2026, 8, 21), date(2026, 8, 23)),
        (date(2026, 8, 24), date(2026, 8, 27)),
    )


def test_split_window_single_day_raises():
    with pytest.raises(ValueError):
        split_window(date(2026, 8, 21), date(2026, 8, 21))


def test_in_period_boundary_seconds():
    period = closing_period("2026-10", 20, "24:00")
    assert in_period("2026/09/21 00:00:00", period) is True
    assert in_period("2026/09/20 23:59:59", period) is False
    assert in_period("2026/10/20 23:59:59", period) is True
    assert in_period("2026/10/21 00:00:00", period) is False
