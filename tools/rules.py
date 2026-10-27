"""Business rules for collection calls. Pure functions: no database, no AI.

Always pass timezone-aware datetimes.
"""
from datetime import date, datetime, timedelta, timezone

from tools import policy

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST)


def in_call_window(now: datetime) -> bool:
    """True from CALL_WINDOW_START:00 up to (not including) CALL_WINDOW_END:00 IST."""
    local = now.astimezone(IST)
    return policy.CALL_WINDOW_START <= local.hour < policy.CALL_WINDOW_END


def can_call_now(customer: dict, attempts: list, now: datetime | None = None) -> dict:
    """May we call this customer right now?

    customer: dict that may contain "do_not_call": True
    attempts: datetimes of earlier call attempts
    """
    now = (now or now_ist()).astimezone(IST)
    if customer.get("do_not_call"):
        return {"allowed": False, "reason": "do_not_call"}
    if not in_call_window(now):
        return {"allowed": False, "reason": "outside_window"}
    times = [a.astimezone(IST) for a in attempts]
    today = [t for t in times if t.date() == now.date()]
    if len(today) >= policy.MAX_ATTEMPTS_PER_DAY:
        return {"allowed": False, "reason": "daily_limit"}
    last_week = [t for t in times if now - timedelta(days=7) <= t <= now]
    if len(last_week) >= policy.MAX_ATTEMPTS_PER_WEEK:
        return {"allowed": False, "reason": "weekly_limit"}
    return {"allowed": True, "reason": None}


def next_window_start(now: datetime | None = None) -> datetime:
    """The earliest moment we may call: now if inside the window, else the next opening."""
    now = (now or now_ist()).astimezone(IST)
    opening = now.replace(hour=policy.CALL_WINDOW_START, minute=0, second=0, microsecond=0)
    if now.hour < policy.CALL_WINDOW_START:
        return opening
    if in_call_window(now):
        return now
    return opening + timedelta(days=1)


def make_date(day, month, year) -> date | None:
    """A real date from three numbers, or None if impossible (like 31 February)."""
    try:
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def validate_promise_date(day, month, year, today: date | None = None) -> dict:
    """A promised payment date must be real, not in the past, and within MAX_PROMISE_DAYS."""
    today = today or now_ist().date()
    promised = make_date(day, month, year)
    if promised is None:
        return {"ok": False, "reason": "impossible_date"}
    if promised < today:
        return {"ok": False, "reason": "in_the_past"}
    if promised > today + timedelta(days=policy.MAX_PROMISE_DAYS):
        return {"ok": False, "reason": "too_far"}
    return {"ok": True, "date": promised}


def validate_callback_time(due_at: datetime, now: datetime | None = None) -> dict:
    """A callback must be in the future, soon enough, and inside the calling window."""
    now = (now or now_ist()).astimezone(IST)
    due = due_at.astimezone(IST)
    if due <= now:
        return {"ok": False, "reason": "in_the_past"}
    if due > now + timedelta(days=policy.MAX_PROMISE_DAYS):
        return {"ok": False, "reason": "too_far"}
    if not in_call_window(due):
        return {"ok": False, "reason": "outside_window"}
    return {"ok": True}