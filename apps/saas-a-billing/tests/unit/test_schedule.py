"""`schedule.py` のユニットテスト（設計 6.2 / 8 章の 409）。

スケジュール実行の直前に手動実行を受けると、後発の `Init` のログインが先発の
トークンを無効化する（#1006）。境界は「窓の先頭は不可、窓の外と発火時刻は可」。
"""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from saas_a_billing import schedule

JST = timezone(timedelta(hours=9))
HOUR, MINUTE = 6, 0


def jst(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 9, 3, hour, minute, second, tzinfo=JST)


def imminent(now: datetime) -> bool:
    return schedule.is_imminent(now, HOUR, MINUTE, JST)


def test_just_outside_the_window_is_allowed():
    assert imminent(jst(5, 58, 59)) is False


def test_window_head_is_blocked():
    assert imminent(jst(5, 59, 0)) is True


def test_window_tail_is_blocked():
    assert imminent(jst(5, 59, 59)) is True


def test_fire_time_itself_is_allowed():
    """発火後はスケジュール側が RUN を取るため、実行中判定（OTHER_RUN_RUNNING）に任せる。"""
    assert imminent(jst(6, 0, 0)) is False


def test_after_fire_time_is_allowed():
    assert imminent(jst(6, 0, 1)) is False


def test_far_from_the_schedule_is_allowed():
    assert imminent(jst(10, 0, 0)) is False


def test_utc_now_is_judged_in_the_schedule_timezone():
    """Lambda の `datetime.now()` が UTC でも JST の 05:59 を直前と判定する。"""
    utc = jst(5, 59, 30).astimezone(UTC)

    assert utc.hour == 20
    assert imminent(utc) is True


def test_window_seconds_is_configurable():
    assert schedule.is_imminent(jst(5, 55, 0), HOUR, MINUTE, JST, window_seconds=300) is True
    assert schedule.is_imminent(jst(5, 54, 59), HOUR, MINUTE, JST, window_seconds=300) is False


def test_other_schedule_times():
    assert schedule.is_imminent(jst(23, 44, 30), 23, 45, JST) is True
    assert schedule.is_imminent(jst(23, 45, 0), 23, 45, JST) is False


def test_midnight_schedule_blocks_the_previous_day():
    """`ScheduleHour` は 0 も取りうる。同じ日の 00:00 と比べると窓が効かない（#1006 レビュー S2）。"""
    assert schedule.is_imminent(jst(23, 59, 30), 0, 0, JST) is True


def test_midnight_schedule_allows_the_fire_time():
    assert schedule.is_imminent(jst(0, 0, 0), 0, 0, JST) is False


def test_midnight_schedule_allows_just_outside_the_window():
    assert schedule.is_imminent(jst(23, 58, 59), 0, 0, JST) is False


def test_labels_describe_the_schedule_and_the_next_chance():
    assert schedule.schedule_label(HOUR, MINUTE) == "06:00"
    assert schedule.next_allowed_label(HOUR, MINUTE) == "06:01 以降"
    assert schedule.next_allowed_label(0, 0) == "00:01 以降"
    assert schedule.next_allowed_label(23, 59) == "翌 00:00 以降"


def test_timezone_of_resolves_the_scheduler_timezone_name():
    assert schedule.timezone_of("Asia/Tokyo") == ZoneInfo("Asia/Tokyo")


def test_timezone_of_falls_back_to_jst_for_unknown_names():
    """tzdata が無い実行環境でも手動実行を 500 にしない。"""
    assert schedule.timezone_of("Mars/Olympus") == schedule.JST
