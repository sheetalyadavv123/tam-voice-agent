from datetime import date, datetime, timedelta, timezone

from tools.rules import (
    IST, can_call_now, in_call_window, next_window_start,
    validate_callback_time, validate_promise_date,
)


def ist(day, hour=10, minute=0, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


NOW = ist(28, 11)  # 28 Oct 2026, 11:00 IST
TODAY = date(2026, 10, 28)


def test_window_edges():
    assert in_call_window(ist(28, 7, 59)) is False
    assert in_call_window(ist(28, 8, 0)) is True
    assert in_call_window(ist(28, 18, 59)) is True
    assert in_call_window(ist(28, 19, 0)) is False


def test_window_uses_ist_not_utc():
    assert in_call_window(datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)) is True    # 08:30 IST
    assert in_call_window(datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)) is False  # 19:30 IST


def test_do_not_call_blocks():
    assert can_call_now({"do_not_call": True}, [], NOW) == {"allowed": False, "reason": "do_not_call"}


def test_outside_window_blocks():
    assert can_call_now({}, [], ist(28, 20))["reason"] == "outside_window"


def test_daily_limit():
    attempts = [ist(28, 9), ist(28, 10)]
    assert can_call_now({}, attempts, NOW)["reason"] == "daily_limit"
    assert can_call_now({}, attempts[:1], NOW)["allowed"] is True


def test_yesterday_does_not_count_for_daily():
    assert can_call_now({}, [ist(27, 9), ist(27, 10)], NOW)["allowed"] is True


def test_weekly_limit():
    attempts = [NOW - timedelta(days=i) for i in range(1, 6)]
    assert can_call_now({}, attempts, NOW)["reason"] == "weekly_limit"


def test_old_attempts_do_not_count_for_weekly():
    attempts = [NOW - timedelta(days=8 + i) for i in range(5)]
    assert can_call_now({}, attempts, NOW)["allowed"] is True


def test_next_window_start():
    assert next_window_start(ist(28, 6)) == ist(28, 8)
    assert next_window_start(ist(28, 11)) == ist(28, 11)
    assert next_window_start(ist(28, 20)) == ist(29, 8)


def test_promise_today_and_seven_days_ok():
    assert validate_promise_date(28, 10, 2026, TODAY)["ok"] is True
    assert validate_promise_date(4, 11, 2026, TODAY)["ok"] is True


def test_promise_too_far():
    assert validate_promise_date(5, 11, 2026, TODAY)["reason"] == "too_far"


def test_promise_in_past():
    assert validate_promise_date(27, 10, 2026, TODAY)["reason"] == "in_the_past"


def test_promise_impossible_date():
    assert validate_promise_date(31, 2, 2026, TODAY)["reason"] == "impossible_date"


def test_callback_rules():
    assert validate_callback_time(ist(29, 10), NOW)["ok"] is True
    assert validate_callback_time(ist(28, 10), NOW)["reason"] == "in_the_past"
    assert validate_callback_time(ist(29, 21), NOW)["reason"] == "outside_window"
    assert validate_callback_time(ist(20, 10, month=11), NOW)["reason"] == "too_far"