"""Turn the days a caller names into real dates. Pure functions: no AI, no audio.

A small model gets weekdays wrong ("Friday, 6 or 7 November?"), so the code works
the dates out from today in IST and gives them to the AI.

- A weekday means its next one after today; today's own weekday means next week.
  "is Friday", "agle Monday" and "next Monday" mean the same next one.
- "aaj"/"आज"/"today" is today, "kal"/"कल"/"tomorrow" is tomorrow, and
  "parson"/"परसों"/"day after tomorrow" is the day after.
- dates_said() finds dates written like "6 November" in a message.
"""
import re
from datetime import date, timedelta

WEEKDAYS = [  # Monday first, like date.weekday()
    ("Monday", r"monday|somvaa?r|somwaa?r|सोमवार"),
    ("Tuesday", r"tuesday|mangalvaa?r|mangalwaa?r|मंगलवार"),
    ("Wednesday", r"wednesday|budhvaa?r|budhwaa?r|बुधवार"),
    ("Thursday", r"thursday|guruvaa?r|guruwaa?r|brihaspativaa?r|veervaa?r|गुरुवार|गुरूवार|बृहस्पतिवार|वीरवार"),
    ("Friday", r"friday|shukravaa?r|shukrawaa?r|शुक्रवार"),
    ("Saturday", r"saturday|shanivaa?r|shaniwaa?r|शनिवार"),
    ("Sunday", r"sunday|ravivaa?r|raviwaa?r|itvaa?r|itwaa?r|रविवार|इतवार"),
]
# Longer phrases first, so "day after tomorrow" is not read as "tomorrow"
RELATIVE = [
    (r"day\s+after\s+tomorrow|parson|parso|parsoon|परसों|परसो", 2),
    (r"tomorrow|kal|कल", 1),
    (r"today|aaj|आज", 0),
]
_EDGE_L, _EDGE_R = r"(?<![A-Za-zऀ-ॿ])", r"(?![A-Za-zऀ-ॿ])"
_WEEKDAY_RE = [(name, i, re.compile(_EDGE_L + f"(?:{p})" + _EDGE_R, re.I))
               for i, (name, p) in enumerate(WEEKDAYS)]
_RELATIVE_RE = [(re.compile(_EDGE_L + f"(?:{p})" + _EDGE_R, re.I), days) for p, days in RELATIVE]

MONTHS = {m.lower(): i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
MONTHS.update({"जनवरी": 1, "फ़रवरी": 2, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6,
               "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "नवंबर": 11,
               "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12})
DAY_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")" + _EDGE_R, re.I)


def next_weekday(today: date, weekday: int) -> date:
    """The next date with this weekday after today; today's own weekday is next week."""
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def days_named(text: str, today: date) -> list[tuple[str, date]]:
    """The days the caller named, in the order said, as (word they used, date)."""
    found = []
    for name, i, pattern in _WEEKDAY_RE:
        for m in pattern.finditer(text):
            found.append((m.start(), m.group(), next_weekday(today, i)))
    taken = []
    for pattern, days in _RELATIVE_RE:
        for m in pattern.finditer(text):
            if any(s <= m.start() < e for s, e in taken):
                continue  # "kal" inside an already-matched phrase
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.group(), today + timedelta(days=days)))
    return [(word, d) for _, word, d in sorted(found)]


def say(d: date) -> str:
    """Like "Friday 6 November", the way the call note writes dates."""
    return f"{d:%A} {d.day} {d:%B}"


def note_for(text: str, today: date) -> str | None:
    """The line the agent adds to this turn, like "Friday = Friday 6 November", or None."""
    named = days_named(text, today)
    if not named:
        return None
    return "; ".join(f"{word} = {say(d)}" for word, d in named)


def dates_said(text: str, today: date) -> list[date]:
    """Dates written like "6 November" or "6 नवंबर", in the year that puts them nearest to today."""
    out = []
    for m in DAY_MONTH.finditer(text):
        day, month = int(m.group(1)), MONTHS[m.group(2).lower()]
        candidates = []
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                pass
        if candidates:
            out.append(min(candidates, key=lambda d: abs((d - today).days)))
    return out
