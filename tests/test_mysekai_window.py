from __future__ import annotations

from datetime import date, datetime

from app.services.mysekai_window import (
    BIRTHDAYS,
    CN,
    birthday_dates,
    flower_start_dates,
    game_day_key,
    is_exempt,
    watch_is_open,
    watch_opens_at,
    window_key,
)


def test_morning_before_five_belongs_to_previous_evening() -> None:
    assert (
        window_key(datetime(2026, 10, 23, 4, 59, tzinfo=CN), birthdays=frozenset())
        == "2026-10-22-17"
    )


def test_five_starts_the_day_window() -> None:
    assert (
        window_key(datetime(2026, 10, 23, 5, 0, tzinfo=CN), birthdays=frozenset())
        == "2026-10-23-05"
    )


def test_seventeen_starts_the_evening_window() -> None:
    assert (
        window_key(datetime(2026, 10, 23, 16, 59, tzinfo=CN), birthdays=frozenset())
        == "2026-10-23-05"
    )
    assert (
        window_key(datetime(2026, 10, 23, 17, 0, tzinfo=CN), birthdays=frozenset())
        == "2026-10-23-17"
    )


def test_birthday_midnight_splits_a_new_window() -> None:
    birthdays = frozenset({date(2026, 11, 12)})
    assert (
        window_key(datetime(2026, 11, 12, 0, 0, tzinfo=CN), birthdays=birthdays)
        == "2026-11-12-00"
    )
    assert (
        window_key(datetime(2026, 11, 12, 3, 0, tzinfo=CN), birthdays=birthdays)
        == "2026-11-12-00"
    )


def test_birthday_after_five_falls_back_to_the_day_window() -> None:
    birthdays = frozenset({date(2026, 11, 12)})
    assert (
        window_key(datetime(2026, 11, 12, 5, 0, tzinfo=CN), birthdays=birthdays)
        == "2026-11-12-05"
    )


def test_birthday_day_has_three_windows() -> None:
    """生日 00:00 下生日花并刷新全图，是独立窗口，不能并进前一天 17:00。"""
    birthdays = frozenset({date(2026, 11, 12)})
    assert (
        window_key(datetime(2026, 11, 11, 23, 50, tzinfo=CN), birthdays=birthdays)
        == "2026-11-11-17"
    )
    assert (
        window_key(datetime(2026, 11, 12, 0, 10, tzinfo=CN), birthdays=birthdays)
        == "2026-11-12-00"
    )
    assert (
        window_key(datetime(2026, 11, 12, 5, 0, tzinfo=CN), birthdays=birthdays)
        == "2026-11-12-05"
    )
    assert (
        window_key(datetime(2026, 11, 12, 17, 0, tzinfo=CN), birthdays=birthdays)
        == "2026-11-12-17"
    )


def test_birthday_calendar_is_complete() -> None:
    assert len(BIRTHDAYS) == 26
    assert date(2026, 11, 12) in birthday_dates(2026)
    assert date(2026, 12, 27) in birthday_dates(2026)


def test_only_watched_users_during_a_birthday_are_exempt() -> None:
    assert is_exempt(True, {"targets": []}) is True
    assert is_exempt(True, None) is False
    assert is_exempt(False, {"targets": []}) is False


def test_flower_start_day_also_gets_a_midnight_window() -> None:
    """生日花在生日前 72 小时的 00:00 开始，那天也会整图刷新。"""
    birthdays = frozenset({date(2026, 10, 27)})
    assert (
        window_key(datetime(2026, 10, 23, 23, 50, tzinfo=CN), birthdays=birthdays)
        == "2026-10-23-17"
    )
    assert (
        window_key(datetime(2026, 10, 24, 0, 10, tzinfo=CN), birthdays=birthdays)
        == "2026-10-24-00"
    )
    assert (
        window_key(datetime(2026, 10, 24, 6, 0, tzinfo=CN), birthdays=birthdays)
        == "2026-10-24-05"
    )


def test_flower_start_is_derived_from_the_birthday() -> None:
    assert date(2026, 10, 24) in flower_start_dates(2026)
    assert date(2026, 10, 27) in birthday_dates(2026)


def test_watch_opens_one_hour_before_the_flower_window() -> None:
    start = datetime(2026, 10, 24, 0, 0, tzinfo=CN)
    end = datetime(2026, 10, 27, 0, 0, tzinfo=CN)

    assert watch_opens_at(start) == datetime(2026, 10, 23, 23, 0, tzinfo=CN)
    assert watch_is_open(datetime(2026, 10, 23, 22, 59, tzinfo=CN), start=start, end=end) is False
    assert watch_is_open(datetime(2026, 10, 23, 23, 0, tzinfo=CN), start=start, end=end) is True
    assert watch_is_open(datetime(2026, 10, 26, 23, 59, tzinfo=CN), start=start, end=end) is True
    assert watch_is_open(end, start=start, end=end) is False


def test_game_day_starts_at_five_and_runs_until_the_next_five() -> None:
    """记忆按游戏日结算：05:00 起算，17:00 的整图刷新不换天。"""
    assert game_day_key(datetime(2026, 10, 23, 4, 59, tzinfo=CN)) == "2026-10-22"
    assert game_day_key(datetime(2026, 10, 23, 5, 0, tzinfo=CN)) == "2026-10-23"
    assert game_day_key(datetime(2026, 10, 23, 16, 59, tzinfo=CN)) == "2026-10-23"
    assert game_day_key(datetime(2026, 10, 23, 17, 0, tzinfo=CN)) == "2026-10-23"
    assert game_day_key(datetime(2026, 10, 23, 23, 59, tzinfo=CN)) == "2026-10-23"
    assert game_day_key(datetime(2026, 10, 24, 0, 30, tzinfo=CN)) == "2026-10-23"
