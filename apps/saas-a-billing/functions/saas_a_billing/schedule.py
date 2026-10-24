"""スケジュール実行の直前判定（設計 6.2 / 8 章の 409）。

SaaS_A の管理 API は 1 ユーザー 1 トークンで、後発の `Init` のログインが先発の
トークンを無効化する。EventBridge Scheduler の起動と手動実行が重なると先発の残り
テナントが全部 401 になるため、発火直前の窓では手動実行を受け付けない（#1006）。

スケジュール時刻はテンプレートのパラメータが正で、`SCHEDULE_HOUR` /
`SCHEDULE_MINUTE` / `SCHEDULE_TIMEZONE` として api に渡る。判定は純粋関数に閉じ、
AWS も環境変数も見ない。
"""

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from example_backoffice.logging import get_logger

logger = get_logger()

JST = timezone(timedelta(hours=9))
DEFAULT_HOUR = 6
DEFAULT_MINUTE = 0
DEFAULT_TIMEZONE = "Asia/Tokyo"
WINDOW_SECONDS = 60


def timezone_of(name: str) -> tzinfo:
    """`SCHEDULE_TIMEZONE` の名前を tzinfo にする。読めなければ JST に落として警告する。

    ここで送出すると手動実行が丸ごと 500 になるため、既定の運用時間帯（JST）で続ける。
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("スケジュールのタイムゾーンを解決できないため JST で判定します", {"timezone": name})
        return JST


def is_imminent(now: datetime, hour: int, minute: int, tz: tzinfo, window_seconds: int = WINDOW_SECONDS) -> bool:
    """`now` が次の発火時刻の直前 `window_seconds` 秒に入っているか。

    窓は「発火の `window_seconds` 秒前から発火時刻の直前まで」。発火時刻そのものは
    含めない（そこから先はスケジュール側が RUN を取るため、実行中判定に任せる）。
    `now` の tz は問わず、`tz` に直してから比べる。当日の発火時刻を過ぎていれば翌日の
    発火時刻と比べる（`hour=0` のとき 23:59 台で窓が効かなくなるのを避ける）。
    """
    local = now.astimezone(tz)
    fires_at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fires_at < local:
        fires_at += timedelta(days=1)
    return timedelta(0) < fires_at - local <= timedelta(seconds=window_seconds)


def schedule_label(hour: int, minute: int) -> str:
    """`06:00`。"""
    return f"{hour:02d}:{minute:02d}"


def next_allowed_label(hour: int, minute: int) -> str:
    """`06:01 以降`。窓を抜ける最初の分を案内する。日を跨ぐときは `翌 00:00 以降`。"""
    passed = datetime(2000, 1, 1, hour, minute) + timedelta(minutes=1)
    prefix = "翌 " if passed.day != 1 else ""
    return f"{prefix}{passed:%H:%M} 以降"
