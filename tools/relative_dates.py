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
- Ranges: "10-15 din", "10 se 15 din mein", "10 to 15 days", "2-3 hafte": from the first
  to the last day. "agle mahine", "next month": the whole of next month.

Each day the caller named is an Offer (words said, first day, last day); a single day has
first == last. The limit is checked on the last day, and notes keep both.
"""
import calendar
import re
from datetime import date, timedelta
from typing import NamedTuple

from tools import dates, spoken

_L, _R = r"(?<![A-Za-zऀ-ॿ\d])", r"(?![A-Za-zऀ-ॿ])"
# A number as said: digits, or one word ("das", "दस", "ek", "two"); "a" for "a week"
_N = r"(\d{1,3}|[A-Za-z]+|[ऀ-ॿ]+)"
# "15 din lagenge" (it will take 15 days) says the same as "15 din mein"
_TAKES = r"lagenge|lagega|lagegi|lag\s+jayenge|lag\s+jaenge|लगेंगे|लगेगा|लगेगी|लग\s+जाएंगे"
_AFTER = (r"(?:baad|bad|mein|me|main|men|andar|tak|बाद|में|अंदर|तक|later|from\s+now"
          r"|from\s+today|time|" + _TAKES + ")")

DAYS = re.compile(
    _L + _N + r"\s*(?:din|dino|dinon|दिन|दिनों)\s+(?:(?:ke|के)\s+)?"
    r"(?:baad|bad|mein|me|main|men|andar|tak|बाद|में|अंदर|तक|" + _TAKES + ")" + _R
    + r"|" + _L + r"(?:in|after|within)\s+(?:the\s+next\s+)?" + _N + r"\s+days?" + _R
    + r"|" + _L + _N + r"\s+days?\s+(?:later|from\s+now|from\s+today)" + _R,
    re.I)
WEEKS = re.compile(
    _L + _N + r"\s*(?:hafte|hafta|hafton|hafte\s+bhar|हफ्ते|हफ़्ते|हफ्ता|हफ़्ता|हफ्तों|हफ़्तों|"
    r"सप्ताह|weeks?)\s+(?:(?:ke|के)\s+)?" + _AFTER + _R
    + r"|" + _L + r"(?:in|after|within)\s+" + _N + r"\s+weeks?" + _R,
    re.I)
# "10-15 din", "10 se 15 din mein", "10 to 15 days", "दस से पंद्रह दिन", "2-3 hafte"
RANGE = re.compile(
    _L + _N + r"\s*(?:-|–|—|se|से|to|or|ya|या)\s*" + _N
    + r"\s*(din|dino|dinon|दिन|दिनों|days?|hafte|hafta|hafton|हफ्ते|हफ़्ते|हफ्ता|हफ़्ता|हफ्तों|"
    r"सप्ताह|weeks?)(?:\s+(?:(?:ke|के)\s+)?" + _AFTER + r")?" + _R,
    re.I)
NEXT_WEEK = re.compile(
    _L + r"(?:agle|agla|aglay|next|अगले|अगला)\s+(?:hafte|hafta|week|हफ्ते|हफ़्ते|हफ्ता|हफ़्ता|सप्ताह|वीक)" + _R,
    re.I)
NEXT_MONTH = re.compile(
    _L + r"(?:agle|agla|aglay|next|अगले|अगला)\s+(?:mahine|mahina|month|महीने|महीना|मंथ)" + _R,
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
_WEEK_UNITS = re.compile(r"hafte|hafta|hafton|हफ्ते|हफ़्ते|हफ्ता|हफ़्ता|हफ्तों|सप्ताह|week", re.I)


class Offer(NamedTuple):
    """A day (or days) the caller named. The limit is checked on `last`."""
    word: str
    first: date
    last: date

    @property
    def is_range(self):
        return self.first != self.last


def single(d, word=""):
    return Offer(word, d, d)


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


def _next_month(today):
    first = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    return first, month_end(first)


def _overlaps(start, taken):
    return any(a <= start < b for a, b in taken)


def relative_named(text, today):
    """The relative days in the text, as (start, Offer)."""
    text = text or ""
    found = []
    for m in RANGE.finditer(text):
        a, b = _count(m.group(1)), _count(m.group(2))
        if a is None or b is None or not 0 < a < b:
            continue
        unit = 7 if _WEEK_UNITS.match(m.group(3)) else 1
        if b * unit > 400:
            continue
        found.append((m.start(), Offer(m.group().strip(), today + timedelta(days=a * unit),
                                       today + timedelta(days=b * unit))))
    taken = [(s, s + len(o.word)) for s, o in found]
    for pattern, unit, limit in ((DAYS, 1, 400), (WEEKS, 7, 60)):
        for m in pattern.finditer(text):
            if _overlaps(m.start(), taken) or any(m.start() <= a < m.end() for a, _ in taken):
                continue  # the "15 din mein" of "10-15 din mein"
            n = _count(next(g for g in m.groups() if g), allow_a=True)
            if n is not None and 0 < n <= limit:
                found.append((m.start(), single(today + timedelta(days=n * unit), m.group().strip())))
    for m in NEXT_WEEK.finditer(text):
        found.append((m.start(), single(today + timedelta(days=7), m.group().strip())))
    for m in NEXT_MONTH.finditer(text):
        found.append((m.start(), Offer(m.group().strip(), *_next_month(today))))
    for m in MONTH_END.finditer(text):
        found.append((m.start(), single(month_end(today), m.group().strip())))
    taken = [(s, s + len(o.word)) for s, o in found]
    for m in DAY_OF_MONTH.finditer(text):
        if _overlaps(m.start(), taken):
            continue  # the "10" of "10 din tak"
        d = _day_of_month(m.group(1) or m.group(2), today)
        if d is not None:
            found.append((m.start(), single(d, m.group().strip())))
    return found


def offers(text, today):
    """Every day the caller named, in the order said, as Offers: weekdays, "kal",
    "6 November", and the relative days and ranges above. "agle hafte Friday" is one day,
    the Friday of next week."""
    text = text or ""
    found = relative_named(text, today)
    week = [(s, o) for s, o in found if NEXT_WEEK.fullmatch(o.word)]
    for name, i, pattern in dates._WEEKDAY_RE:
        for m in pattern.finditer(text):
            d = dates.next_weekday(today, i)
            if week:  # "agle hafte Friday": the Friday of next week, not this week
                monday_next = today - timedelta(days=today.weekday()) + timedelta(days=7)
                d = monday_next + timedelta(days=i)
            found.append((m.start(), single(d, m.group())))
    if week and len(found) > len(week):  # the weekday says it; drop "agle hafte" itself
        found = [f for f in found if not NEXT_WEEK.fullmatch(f[1].word)]
    taken = [(s, s + len(o.word)) for s, o in found]
    for pattern, days in dates._RELATIVE_RE:
        for m in pattern.finditer(text):
            if _overlaps(m.start(), taken) or _FROM.match(text, m.end()):
                continue  # "aaj se 10 din baad": "aaj" is where they count from
            taken.append((m.start(), m.end()))
            found.append((m.start(), single(today + timedelta(days=days), m.group())))
    for m in dates.DAY_MONTH.finditer(text):
        d = dates.dates_said(m.group(), today)
        if d:
            found.append((m.start(), single(d[0], m.group())))
    return [o for _, o in sorted(found, key=lambda f: f[0])]


def pay_dates(text, today):
    """(words said, date) for every day named; a range gives its last day."""
    return [(o.word, o.last) for o in offers(text, today)]


def last_offer(text, today):
    """The last day (or range) the caller named, or None."""
    found = offers(text, today)
    return found[-1] if found else None


def last_pay_date(text, today):
    """The last day the caller named (a range's last day), or None."""
    o = last_offer(text, today)
    return o.last if o else None


def say_offer(o, lang="en", for_note=False):
    """"12 November", "13 to 18 November" / "13 से 18 November". "Next month" is said as
    the caller said it ("अगले महीने" / "next month"); in a note, "next month (December)"."""
    first, last = o.first, o.last
    if first == last:
        return f"{last.day} {last:%B}"
    to = " से " if lang == "hi" else " to "
    if NEXT_MONTH.fullmatch(o.word or ""):
        if for_note:
            return f"next month ({first:%B})"
        return "अगले महीने" if lang == "hi" else "next month"
    if first.day == 1 and last == month_end(first):
        return f"{first:%B}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day}{to}{last.day} {last:%B}"
    return f"{first.day} {first:%B}{to}{last.day} {last:%B}"


def note_for(text, today):
    """The line the agent adds to this turn, like "दस दिन बाद = Thursday 12 November" or
    "10-15 din mein = Friday 13 November to Wednesday 18 November"."""
    found = offers(text, today)
    if not found:
        return None
    return "; ".join(f"{o.word} = {dates.say(o.first)}" if not o.is_range
                     else f"{o.word} = {dates.say(o.first)} to {dates.say(o.last)}"
                     for o in found)
