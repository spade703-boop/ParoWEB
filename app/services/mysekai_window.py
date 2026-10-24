"""MySekai refresh windows: which "same refresh" an upload belongs to.

国服每天 05:00 与 17:00 刷新烤森；角色生日当天 00:00 还会额外刷新一次
（生日花下掉，整张地图重刷），所以那天有三个窗口。

``BIRTHDAYS`` 是 ``mysekai-re-pipeline`` 的 ``service/birthday.py`` 的镜像：
ParoWEB 拿不到那个模块，而窗口判定是纯本地逻辑，不能在推送前依赖渲染服务。
改角色生日时两处要一起改。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

CN = timezone(timedelta(hours=8))

#: 26 位角色的生日 (月, 日)，与日服一致。
BIRTHDAYS: dict[int, tuple[int, int]] = {
    1: (8, 11), 2: (5, 9), 3: (10, 27), 4: (1, 8), 5: (4, 14),
    6: (10, 5), 7: (3, 19), 8: (12, 6), 9: (3, 2), 10: (7, 26),
    11: (11, 12), 12: (5, 25), 13: (5, 17), 14: (9, 9), 15: (7, 20),
    16: (6, 24), 17: (2, 10), 18: (1, 27), 19: (4, 30), 20: (8, 27),
    21: (8, 31), 22: (12, 27), 23: (12, 27), 24: (1, 30), 25: (11, 5),
    26: (2, 17),
}

#: 国服两个固定刷新点（小时）。
REFRESH_HOURS: tuple[int, ...] = (5, 17)


#: 生日花开放时长：生日前 72 小时开始，生日当天 00:00 下掉。
FLOWER_WINDOW = timedelta(hours=72)

#: 监听可在生日花窗口开始前这么久开启；再早会被拒绝。
WATCH_OPEN_LEAD = timedelta(hours=1)


def watch_opens_at(start: datetime) -> datetime:
    """The first moment a player may open a watch for this flower window."""
    return start - WATCH_OPEN_LEAD


def watch_is_open(now: datetime, *, start: datetime, end: datetime) -> bool:
    """Whether ``now`` is inside the interval where a watch may be opened."""
    return watch_opens_at(start) <= now < end


def birthday_dates(year: int) -> frozenset[date]:
    """Every character birthday in ``year`` as calendar dates."""
    return frozenset(date(year, month, day) for month, day in BIRTHDAYS.values())


def flower_start_dates(year: int) -> frozenset[date]:
    """The dates birthday flowers appear: three days before each birthday."""
    return frozenset(moment - FLOWER_WINDOW for moment in birthday_dates(year))


def _midnight_boundary_dates(year: int) -> frozenset[date]:
    """Days that get an extra 00:00 refresh.

    生日花在开始那天 00:00 出现、在生日当天 00:00 下掉，两次都会重刷整图。
    """
    return birthday_dates(year) | flower_start_dates(year)


def window_key(
    now: datetime, *, birthdays: frozenset[date] | None = None
) -> str:
    """The refresh window ``now`` falls in, as ``YYYY-MM-DD-HH``.

    05:00–16:59 → today's 05; 17:00–23:59 → today's 17; 00:00–04:59 → the
    00 window on a birthday-flower boundary day (flowers appear, or are taken
    down on the birthday), otherwise the previous day's 17.

    ``birthdays`` are character birthday dates; the flower-window start
    (three days earlier) is derived here, because that 00:00 refresh also
    rebuilds the whole map.
    """
    if birthdays is None:
        boundaries = _midnight_boundary_dates(now.year)
    else:
        # 调用方传的是生日日期；生日花开始那天同样要切窗口，这里一并推导。
        boundaries = frozenset(birthdays) | {
            moment - FLOWER_WINDOW for moment in birthdays
        }
    today = now.date()
    if now.hour < REFRESH_HOURS[0]:
        if today in boundaries:
            return f"{today.isoformat()}-00"
        return f"{(today - timedelta(days=1)).isoformat()}-{REFRESH_HOURS[1]:02d}"
    if now.hour < REFRESH_HOURS[1]:
        return f"{today.isoformat()}-{REFRESH_HOURS[0]:02d}"
    return f"{today.isoformat()}-{REFRESH_HOURS[1]:02d}"


def is_exempt(watch_on: bool, birthday: dict | None) -> bool:
    """Whether this upload skips the once-per-window rule.

    Only a player watering during an open birthday window is exempt: that is
    when they re-upload to hunt rare drops. The window closes at the birthday
    itself, so the exemption ends when the flowers come down.
    """
    return bool(watch_on and birthday)
