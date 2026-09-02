"""締め日時・課金対象期間・対象月・窓分割（設計 2.1 / 6.2 / 6.4 / 7.3）。

課金対象期間は `[前月の締め日時, 当月の締め日時)` の半開区間で、受信日時
`change_datetime` を JST で判定する。`cutoff_time` の `"24:00"` は「その日の
終わりまで含む」= 翌日 00:00 を表す。

対象月は締め日**時**ではなく締め**日**が属する年月なので、`cutoff_time` が
`"00:00"` でも `"24:00"` でも同じ月になる。
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
EXPORT_WINDOW_DAYS_DEFAULT = 7

CHANGE_DATETIME_FORMAT = "%Y/%m/%d %H:%M:%S"
_CUTOFF_TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2})$")
_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


def _parse_cutoff_time(cutoff_time: str) -> tuple[int, int, bool]:
    """`HH:MM` を (時, 分, 翌日か) に分解する。`24:00` だけが翌日 00:00 を表す。"""
    matched = _CUTOFF_TIME_PATTERN.match(cutoff_time)
    if matched is None:
        raise ValueError(f"cutoff_time は HH:MM 形式で指定してください: {cutoff_time!r}")
    hour, minute = int(matched.group(1)), int(matched.group(2))
    if (hour, minute) == (24, 0):
        return 0, 0, True
    if hour > 23 or minute > 59:
        raise ValueError(f"cutoff_time が時刻として不正です: {cutoff_time!r}")
    return hour, minute, False


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """`delta` か月ずらした (年, 月) を返す。"""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def cutoff_date(year: int, month: int, cutoff_day: int) -> date:
    """締め日そのもの（`cutoff_time` に依らず `cutoff_day` 日）。"""
    return date(year, month, cutoff_day)


def cutoff_at(year: int, month: int, cutoff_day: int, cutoff_time: str) -> datetime:
    """対象月の締め日時（JST aware）。`cutoff_time` が `24:00` なら翌日 00:00。"""
    hour, minute, next_day = _parse_cutoff_time(cutoff_time)
    day = cutoff_date(year, month, cutoff_day)
    if next_day:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)


def _latest_cutoff_month(now: datetime, cutoff_day: int, cutoff_time: str) -> tuple[int, int]:
    """`now` 以前で最も新しい締め日時を持つ (年, 月)。"""
    year, month = now.year, now.month
    while cutoff_at(year, month, cutoff_day, cutoff_time) > now:
        year, month = _shift_month(year, month, -1)
    return year, month


def latest_cutoff(now: datetime, cutoff_day: int, cutoff_time: str) -> datetime:
    """`now` 以前で最も新しい締め日時（`now` と同時刻ならそれを返す）。"""
    year, month = _latest_cutoff_month(now, cutoff_day, cutoff_time)
    return cutoff_at(year, month, cutoff_day, cutoff_time)


def target_month(now: datetime, cutoff_day: int, cutoff_time: str) -> str:
    """`now` 以前で最も新しい締め日が属する年月 `YYYY-MM`。"""
    year, month = _latest_cutoff_month(now, cutoff_day, cutoff_time)
    return f"{year:04d}-{month:02d}"


def _parse_month(month: str) -> tuple[int, int]:
    matched = _MONTH_PATTERN.match(month)
    if matched is None:
        raise ValueError(f"month は YYYY-MM 形式で指定してください: {month!r}")
    return int(matched.group(1)), int(matched.group(2))


def previous_month(month: str) -> str:
    """対象月 `YYYY-MM` の 1 つ前の対象月。"""
    year, target = _parse_month(month)
    previous_year, previous_target = _shift_month(year, target, -1)
    return f"{previous_year:04d}-{previous_target:02d}"


@dataclass(frozen=True)
class ClosingPeriod:
    """課金対象期間 `[start, end_exclusive)`。`cutoff_date` は当月の締め日。"""

    start: datetime
    end_exclusive: datetime
    cutoff_date: date
    cutoff_day: int
    cutoff_time: str

    @property
    def display_end(self) -> datetime:
        """表示用の期間終了日時（`end_exclusive` の 1 秒前）。"""
        return self.end_exclusive - timedelta(seconds=1)

    @property
    def first_date(self) -> date:
        """受信データを取りに行く最初の日。"""
        return self.start.date()

    @property
    def last_date(self) -> date:
        """受信データを取りに行く最終日（`cutoff_time` が `00:00` なら締め日の前日）。"""
        return self.display_end.date()

    @property
    def month(self) -> str:
        """対象月 `YYYY-MM`（締め日が属する年月）。"""
        return f"{self.cutoff_date.year:04d}-{self.cutoff_date.month:02d}"


def closing_period(month: str, cutoff_day: int, cutoff_time: str) -> ClosingPeriod:
    """対象月 `YYYY-MM` の課金対象期間 `[前月の締め日時, 当月の締め日時)`。"""
    year, target = _parse_month(month)
    previous_year, previous_month_number = _shift_month(year, target, -1)
    return ClosingPeriod(
        start=cutoff_at(previous_year, previous_month_number, cutoff_day, cutoff_time),
        end_exclusive=cutoff_at(year, target, cutoff_day, cutoff_time),
        cutoff_date=cutoff_date(year, target, cutoff_day),
        cutoff_day=cutoff_day,
        cutoff_time=cutoff_time,
    )


def windows(start_date: date, end_date: date, days: int = EXPORT_WINDOW_DAYS_DEFAULT) -> list[tuple[date, date]]:
    """両端を含む日付範囲を `days` 日ごとの窓（両端を含む）に分割する。"""
    if days < 1:
        raise ValueError(f"days は 1 以上で指定してください: {days}")
    result = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(window_start + timedelta(days=days - 1), end_date)
        result.append((window_start, window_end))
        window_start = window_end + timedelta(days=1)
    return result


def split_window(ws: date, we: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """窓を前半・後半に分ける（設計 6.4 の `mid = ws + (days // 2 - 1)`）。"""
    days = (we - ws).days + 1
    if days < 2:
        raise ValueError(f"1 日以下の窓は分割できません: {ws} 〜 {we}")
    mid = ws + timedelta(days=days // 2 - 1)
    return (ws, mid), (mid + timedelta(days=1), we)


def in_period(ts: str, period: ClosingPeriod) -> bool:
    """`YYYY/MM/DD HH:MM:SS` を JST として `[start, end_exclusive)` に入るか判定する。"""
    at = datetime.strptime(ts, CHANGE_DATETIME_FORMAT).replace(tzinfo=JST)
    return period.start <= at < period.end_exclusive
