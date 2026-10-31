"""Dates and times in the caller's own words, read by the code. Pure functions: no AI.

- parse_dob(): a date of birth said in Hindi, Hinglish or English, in digits or words:
  "2 November 1985", "02/11/1985", "दो नवंबर उन्नीस सौ पचासी",
  "do november unnees sau pachaasi", "second November nineteen eighty five".
- callback_time(): a day and time to call back, like "kal shaam 6 baje" or
  "Monday 12 baje". A day without a time is not enough: the code never picks the hour.

Numbers in Roman-letter Hindi are matched on the folded spelling from tools/names, so
"unnees", "unnis" and "उन्नीस" are all 19.
"""
import re
from datetime import datetime, timedelta

from tools import names
from tools.dates import MONTHS, dates_said, days_named
from tools.speech_guard import NUMBER, SCALE

ORDINALS = {w: i for i, w in enumerate(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth".split())}
ORDINALS.update({"twentieth": 20, "thirtieth": 30})
_ROMAN_HINDI = {names.key(w): n for w, n in NUMBER.items() if re.search(r"[ऀ-ॿ]", w)}
_ROMAN_HINDI.update({names.key(w): n for w, n in NUMBER.items() if w.isascii()})
_ROMAN_SCALE = {names.key(w): v for w, v in (("sau", 100), ("hazaar", 1000), ("hajar", 1000))}
MONTH_WORDS = {m: i for m, i in MONTHS.items()}
MONTH_WORDS.update({m[:3]: i for m, i in MONTHS.items() if m.isascii()})
MONTH_WORDS.update({"sept": 9})
TOKEN = re.compile(r"\d+(?:st|nd|rd|th)?|[A-Za-z]+(?:-[A-Za-z]+)*|[ऀ-ॿ]+")


def _number(word):
    """(value, kind) for one word: kind is "n" (a number), "scale" (hundred, हज़ार) or None."""
    w = word.lower()
    if re.fullmatch(r"\d+(?:st|nd|rd|th)?", w):
        return int(re.sub(r"\D", "", w)), "n"
    if w in ORDINALS:
        return ORDINALS[w], "n"
    if "-" in w:  # "twenty-five", "twenty-second"
        parts = [_number(p) for p in w.split("-")]
        if all(k == "n" for _, k in parts):
            return sum(v for v, _ in parts), "n"
        return None, None
    if w in SCALE:
        return SCALE[w], "scale"
    if w in NUMBER:
        return NUMBER[w], "n"
    k = names.key(w)
    if k in _ROMAN_SCALE:
        return _ROMAN_SCALE[k], "scale"
    if w.isascii() and len(k) >= 3 and k in _ROMAN_HINDI:
        return _ROMAN_HINDI[k], "n"
    return None, None


def spoken_numbers(text):
    """The numbers and months in the text, in order, like [("n", 2), ("month", 11),
    ("n", 1985)]. "nineteen eighty five" and "उन्नीस सौ पचासी" both give 1985."""
    words = TOKEN.findall(text)
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w.lower() in MONTH_WORDS:
            out.append(("month", MONTH_WORDS[w.lower()]))
            i += 1
            continue
        value, kind = _number(w)
        if kind is None:
            i += 1
            continue
        # words that join into one number: "eighty five", "unnees sau pachaasi", "do hazaar ek"
        j = i + 1
        while j < len(words):
            v2, k2 = _number(words[j])
            spelled = not words[j][0].isdigit()
            if k2 == "scale" and value < 100:
                value *= v2
            elif k2 == "n" and spelled and value >= 100 and value % 100 == 0 and v2 < 100:
                value += v2
            elif (k2 == "n" and spelled and not w[0].isdigit() and 20 <= value < 100
                  and value % 10 == 0 and 0 < v2 < 10):
                value += v2
            else:
                break
            j += 1
        unknown_next = (j < len(words) and _number(words[j])[1] is None
                        and words[j].lower() not in MONTH_WORDS and len(words[j]) >= 4)
        if value >= 100 and value % 100 == 0 and unknown_next:
            out.append(("unclear", value))  # "unnis sau athattar": a number word we don't know
        else:
            out.append(("n", value))
        i = j
    # "nineteen eighty five": a 19 or 20 followed by 0-99, after a day and month, is a year
    joined = []
    for kind, v in out:
        if (joined and kind == "n" and joined[-1] in (("n", 19), ("n", 20)) and v < 100
                and len(joined) >= 3):
            joined[-1] = ("n", joined[-1][1] * 100 + v)
        else:
            joined.append((kind, v))
    return joined


NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{2,4})(?!\d)")


def parse_dob(text):
    """A date of birth from the caller's words, as (day, month, year), or "two_digit_year"
    or "no_date". Whether the date exists is checked by the caller of this."""
    m = NUMERIC_DATE.search(text)
    if m:
        a, b, c = (int(g) for g in m.groups())
        day, month, year = (c, b, a) if a > 31 else (a, b, c)
        return (day, month, year) if year >= 100 else "two_digit_year"
    found = spoken_numbers(text)
    if any(k == "unclear" for k, _ in found):
        return "no_date"
    at = next((i for i, (k, _) in enumerate(found) if k == "month"), None)
    if at is not None:
        month = found[at][1]
        before = [v for k, v in found[:at] if k == "n"]
        after = [v for k, v in found[at + 1:] if k == "n"]
        if before and 1 <= before[-1] <= 31 and after:
            day, year = before[-1], after[0]
        elif len(after) >= 2 and 1 <= after[0] <= 31:  # "November 2, 1985"
            day, year = after[0], after[1]
        elif before and after == [] and len(before) >= 2 and before[0] >= 1000:
            year, day = before[0], before[-1]  # "1985, 2 November"
        else:
            return "no_date"
    else:
        nums = [v for k, v in found if k == "n"]
        if len(nums) < 3:
            return "no_date"
        if nums[0] >= 1000:
            year, month, day = nums[:3]
        else:
            day, month, year = nums[:3]
    return (day, month, year) if year >= 100 else "two_digit_year"


# ---- a time to call back ----
PART_OF_DAY = [
    ("morning", re.compile(r"\b(?:subah|subha|savere|morning)\b|सुबह|सवेरे", re.I)),
    ("afternoon", re.compile(r"\b(?:dopahar|dopehar|dopeher|afternoon|noon)\b|दोपहर", re.I)),
    ("evening", re.compile(r"\b(?:shaam|sham|evening)\b|शाम", re.I)),
    ("night", re.compile(r"\b(?:raat|night)\b|रात", re.I)),
]
HALF = re.compile(r"\b(?:saade|sade|saadhe)\b|साढ़े|साढे", re.I)
QUARTER_PAST = re.compile(r"\b(?:sava|savaa|sawa)\b|सवा", re.I)
QUARTER_TO = re.compile(r"\b(?:paune|pone)\b|पौने", re.I)
SPECIAL_HOURS = [(re.compile(r"\bdedh\b|डेढ़|डेढ", re.I), 1, 30),
                 (re.compile(r"\b(?:dhai|dhaai)\b|ढाई", re.I), 2, 30)]
AM = re.compile(r"\b(?:am|a\.m\.?)(?![A-Za-z])", re.I)
PM = re.compile(r"\b(?:pm|p\.m\.?)(?![A-Za-z])", re.I)
CLOCK_WORD = re.compile(r"\b(?:baje|o'?clock|am|pm|a\.m|p\.m)(?![A-Za-z])|बजे", re.I)
DIGIT_TIME = re.compile(r"(?<![\d/.-])(\d{1,2})(?:\s*[:.]\s*(\d{2}))?(?![\d/-])")


def _hour_said(text):
    """(hour, minute) as said (1-12 or 0-23), or None. Needs a clock word ("baje", "pm",
    "4:30") or a part of the day next to the number; "4 November" is not a time."""
    has_part = any(p.search(text) for _, p in PART_OF_DAY)
    for pattern, hour, minute in SPECIAL_HOURS:
        if pattern.search(text) and (CLOCK_WORD.search(text) or has_part):
            return hour, minute
    for m in DIGIT_TIME.finditer(text):
        after = text[m.end():m.end() + 12].lower()
        if re.match(r"\s*(?:st|nd|rd|th|tareekh|tarikh|तारीख)", after) or any(
                re.match(r"\s*" + re.escape(mo), after) for mo in MONTH_WORDS):
            continue  # "4 November", "5 tareekh"
        if m.group(2) is None and not (CLOCK_WORD.match(text, m.end()) or re.match(
                r"\s*(?:baje|बजे|am|pm|a\.m|p\.m|o'?clock)", text[m.end():], re.I) or has_part):
            continue  # a bare "5" with nothing around it is not a time
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if hour <= 23 and minute < 60:
            return _adjust(text, hour, minute)
    clock = CLOCK_WORD.search(text)
    if clock or has_part:  # an hour in words: "chhe baje", "छह बजे", "six pm"
        before = text[:clock.start()] if clock else text
        for kind, v in reversed(spoken_numbers(before)):
            if kind == "n" and 1 <= v <= 12:
                return _adjust(before, v, 0)
    return None


def _adjust(text, hour, minute):
    """"saade chaar" is 4:30, "sava chaar" 4:15, "paune chaar" 3:45."""
    if minute == 0 and HALF.search(text):
        minute = 30
    elif minute == 0 and QUARTER_PAST.search(text):
        minute = 15
    elif minute == 0 and QUARTER_TO.search(text):
        hour, minute = (hour - 1) or 12, 45
    return hour, minute


def _to_24(hour, text):
    if hour > 12:
        return hour
    part = next((name for name, p in PART_OF_DAY if p.search(text)), None)
    if AM.search(text) or part == "morning":
        return hour % 12
    if PM.search(text) or part in ("evening", "night"):
        return hour % 12 + 12
    if part == "afternoon":
        return 12 if hour == 12 else hour + 12 if hour <= 6 else hour
    # no am or pm: calls are made in the day, so 1 to 7 means the afternoon or evening
    return hour + 12 if 1 <= hour <= 7 else hour


def callback_time(text, now):
    """{"when": datetime} for the time the caller gave, or {"missing": "time"} when only a
    day or a part of the day was said, or {"missing": "both"}."""
    today = now.date()
    days = days_named(text, today)
    day = days[-1][1] if days else None
    said = dates_said(text, today)
    if said:
        day = said[-1]
    hour = _hour_said(text)
    if hour is None:
        named_something = day or any(p.search(text) for _, p in PART_OF_DAY)
        return {"missing": "time" if named_something else "both"}
    h, minute = hour
    h = _to_24(h, text)
    if day is None:  # "6 baje": today if that is still ahead, else tomorrow
        day = today if (h, minute) > (now.hour, now.minute) else today + timedelta(days=1)
    try:
        return {"when": datetime(day.year, day.month, day.day, h, minute, tzinfo=now.tzinfo)}
    except ValueError:
        return {"missing": "time"}
