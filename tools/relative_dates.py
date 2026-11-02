"""Pay dates said relative to today, turned into real dates (IST) by the code, the same
way tools/dates turns weekdays into dates. Pure functions: no AI.

- "10 din baad", "दस दिन बाद", "in 10 days", "after 10 days": today + 10.
- "ek hafte mein", "a week", "in one week": today + 7; "2 hafte baad", "two weeks": + 14.
- "agle hafte", "अगले हफ्ते", "next week": today + 7. With a weekday ("agle hafte
  Friday", "next week Friday") it is that weekday in the next calendar week.
- "mahine ke end tak", "महीने के आखिर तक", "month end", "end of the month": the last
  day of this month.
- A day of the month: "12 को", "9 तक", "15 tareekh", "by the 12th": that day this month,
  or next month once it has gone by.

pay_dates() adds these to the weekdays, "kal"/"parson" and "6 November" that tools/dates
finds, in the order the caller said them.
"""
import calendar
import re
from datetime import date, timedelta

from tools import dates, spoken

_L, _R = r"(?<![A-Za-zऀ-ॿ\d])", r"(?![A-Za-zऀ-ॿ])"
# A number as said: digits, or one word ("das", "दस", "ek", "two"); "a" for "a week"
_N = r"(\d{1,3}|[A-Za-z]+|[ऀ-ॿ]+)"

DAYS = re.compile(
    _L + _N + r"\s*(?:din|dino|dinon|दिन|दिनों)\s+(?:(?:ke|के)\s+)?"
    r"(?:baad|bad|mein|me|main|men|andar|tak|बाद|में|अंदर|तक)" + _R
    + r"|" + _L + r"(?:in|after|within)\s+(?:the\s+next\s+)?" + _N + r"\s+days?" + _R
    + r"|" + _L + _N + r"\s+days?\s+(?:later|from\s+now|from\s+today)" + _R,
    re.I)
WEEKS = re.compile(
    _L + _N + r"\s*(?:hafte|hafta|hafton|hafte\s+bhar|हफ्ते|हफ़्ते|हफ्ता|हफ़्ता|हफ्तों|हफ़्तों|"
    r"सप्ताह|weeks?)\s+(?:(?:ke|के)\s+)?"
    r"(?:baad|bad|mein|me|main|men|andar|tak|बाद|में|अंदर|तक|later|from\s+now|time)" + _R
    + r"|" + _L + r"(?:in|after|within)\s+" + _N + r"\s+weeks?" + _R,
    re.I)
NEXT_WEEK = re.compile(
    _L + r"(?:agle|agla|aglay|next|अगले|अगला)\s+(?:hafte|hafta|week|हफ्ते|हफ़्ते|हफ्ता|हफ़्ता|सप्ताह|वीक)" + _R,
    re.I)
MONTH_END = re.compile(
    _L + r"(?:(?:is\s+)?mahine\s+ke\s+(?:end|aakhir|akhir|aakhri|akhri|ant|last)"
    r"|month[\s-]*end|end\s+of\s+(?:the|this)\s+month"
    r"|(?:इस\s+)?(?:महीने|मंथ|month)\s+(?:के\s+)?(?:end|एंड|आखिर|आख़िर|आखिरी|आख़िरी|अंत|last)"
    r"|मंथ\s+एंड)" + _R,
    re.I)
# "12 को", "9 तक", "15 tareekh", "12 तारीख को"; "by the 12th", "on the 12th"
DAY_OF_MONTH = re.compile(
    _L + _N + r"\s*(?:(?:tareekh|tarikh|tarik|तारीख|तारीख़)(?:\s+(?:ko|tak|को|तक))?"
    r"|ko|tak|को|तक)" + _R
    + r"|" + _L + r"(?:by|on|till|until|before)\s+the\s+(\d{1,2})(?:st|nd|rd|th)?" + _R,
    re.I)
_FROM = re.compile(r"\s*(?:se|से|from)" + _R, re.I)
_AMBIGUOUS = {"do", "a", "an", "is", "us", "to", "ab", "aaj", "abhi", "kal", "parson", "the"}


def _count(word, allow_a=False):
    """The number a word says, or None: "10", "das", "दस", "two"; "a"/"one"/"ek" is 1."""
    w = (word or "").lower()
    if allow_a and w in ("a", "an"):
        return 1
    value, kind = spoken._number(w)
    return value if kind == "n" else None


def _day_of_month(word, today):
    w = (word or "").lower()
    if w in _AMBIGUOUS or (w.isascii() and w.isalpha() and len(w) < 3):
        return None  # "do ko", "ek tak": not a date
    n = _count(w)
    if n is None or not 1 <= n <= 31:
        return None
    year, month = today.year, today.month
    for _ in range(3):  # this month, else the next one that has this day
        try:
            d = date(year, month, n)
        except ValueError:
            d = None
        if d is not None and d >= today:
            return d
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def month_end(today):
    return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])


def relative_named(text, today):
    """The relative days in the text, as (start, words said, date)."""
    text = text or ""
    found = []
    for m in DAYS.finditer(text):
        n = _count(next(g for g in m.groups() if g), allow_a=True)
        if n is not None and 0 < n <= 400:
            found.append((m.start(), m.group().strip(), today + timedelta(days=n)))
    for m in WEEKS.finditer(text):
        n = _count(next(g for g in m.groups() if g), allow_a=True)
        if n is not None and 0 < n <= 60:
            found.append((m.start(), m.group().strip(), today + timedelta(weeks=n)))
    for m in NEXT_WEEK.finditer(text):
        found.append((m.start(), m.group().strip(), today + timedelta(days=7)))
    for m in MONTH_END.finditer(text):
        found.append((m.start(), m.group().strip(), month_end(today)))
    taken = [(s, s + len(w)) for s, w, _ in found]
    for m in DAY_OF_MONTH.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue  # the "10" of "10 din tak"
        d = _day_of_month(m.group(1) or m.group(2), today)
        if d is not None:
            found.append((m.start(), m.group().strip(), d))
    return found


def pay_dates(text, today):
    """Every day the caller named, in the order said, as (words said, date): weekdays,
    "kal", "6 November", and the relative days above. "agle hafte Friday" is one day,
    the Friday of next week."""
    text = text or ""
    found = relative_named(text, today)
    week = [(s, w) for s, w, _ in found if NEXT_WEEK.fullmatch(w)]
    for name, i, pattern in dates._WEEKDAY_RE:
        for m in pattern.finditer(text):
            d = dates.next_weekday(today, i)
            if week:  # "agle hafte Friday": the Friday of next week, not this week
                monday_next = today - timedelta(days=today.weekday()) + timedelta(days=7)
                d = monday_next + timedelta(days=i)
            found.append((m.start(), m.group(), d))
    if week and len(found) > len(week):  # the weekday says it; drop "agle hafte" itself
        found = [f for f in found if not NEXT_WEEK.fullmatch(f[1])]
    taken = [(s, s + len(w)) for s, w, _ in found]
    for pattern, days in dates._RELATIVE_RE:
        for m in pattern.finditer(text):
            if any(a <= m.start() < b for a, b in taken) or _FROM.match(text, m.end()):
                continue  # "aaj se 10 din baad": "aaj" is where they count from
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.group(), today + timedelta(days=days)))
    for m in dates.DAY_MONTH.finditer(text):
        d = dates.dates_said(m.group(), today)
        if d:
            found.append((m.start(), m.group(), d[0]))
    return [(w, d) for _, w, d in sorted(found, key=lambda f: f[0])]


def last_pay_date(text, today):
    """The last day the caller named, or None."""
    named = pay_dates(text, today)
    return named[-1][1] if named else None


def note_for(text, today):
    """The line the agent adds to this turn, like "दस दिन बाद = Thursday 12 November"."""
    named = pay_dates(text, today)
    if not named:
        return None
    return "; ".join(f"{word} = {dates.say(d)}" for word, d in named)
