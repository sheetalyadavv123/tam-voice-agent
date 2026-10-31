from datetime import date, timedelta

import pytest

from tools.dates import dates_said, days_named, next_weekday, note_for

TODAY = date(2026, 10, 28)  # a Wednesday


# Every weekday, in Hindi, Hinglish and English. Wednesday is today, so it means next week.
@pytest.mark.parametrize("words, expected", [
    (["Monday", "somvar", "somwar", "सोमवार"], date(2026, 11, 2)),
    (["Tuesday", "mangalvar", "mangalwar", "मंगलवार"], date(2026, 11, 3)),
    (["Wednesday", "budhvar", "budhwar", "बुधवार"], date(2026, 11, 4)),
    (["Thursday", "guruvar", "guruwar", "गुरुवार", "बृहस्पतिवार", "veervar"], date(2026, 10, 29)),
    (["Friday", "shukravar", "shukrawar", "शुक्रवार"], date(2026, 10, 30)),
    (["Saturday", "shanivar", "shaniwar", "शनिवार"], date(2026, 10, 31)),
    (["Sunday", "ravivar", "raviwar", "itvaar", "रविवार", "इतवार"], date(2026, 11, 1)),
])
def test_every_weekday_means_its_next_date(words, expected):
    for word in words:
        assert days_named(f"main {word} ko de dungi", TODAY) == [(word, expected)], word


def test_todays_own_weekday_means_next_week():
    for today in (date(2026, 10, 26) + timedelta(days=i) for i in range(7)):
        assert next_weekday(today, today.weekday()) == today + timedelta(days=7)


@pytest.mark.parametrize("text, expected", [
    ("is Friday", date(2026, 10, 30)),
    ("agle Monday ko", date(2026, 11, 2)),
    ("next Monday", date(2026, 11, 2)),
    ("I'll pay it on Friday", date(2026, 10, 30)),
    ("शुक्रवार तक कर दूंगी", date(2026, 10, 30)),
])
def test_how_people_say_a_weekday(text, expected):
    assert [d for _, d in days_named(text, TODAY)] == [expected]


@pytest.mark.parametrize("text, expected", [
    ("aaj kar dungi", date(2026, 10, 28)),
    ("आज ही कर दूँगी", date(2026, 10, 28)),
    ("today", date(2026, 10, 28)),
    ("kal pakka", date(2026, 10, 29)),
    ("कल शाम तक", date(2026, 10, 29)),
    ("tomorrow", date(2026, 10, 29)),
    ("parson", date(2026, 10, 30)),
    ("parso de dungi", date(2026, 10, 30)),
    ("परसों", date(2026, 10, 30)),
    ("day after tomorrow", date(2026, 10, 30)),  # not read as "tomorrow"
])
def test_aaj_kal_parson(text, expected):
    assert [d for _, d in days_named(text, TODAY)] == [expected]


def test_several_days_in_order():
    assert [d for _, d in days_named("kal nahi, Friday ko", TODAY)] == [date(2026, 10, 29), date(2026, 10, 30)]


@pytest.mark.parametrize("text", ["haan ji", "WhatsApp pe bhej do", "Sunita speaking", "kalpana", "30 October"])
def test_no_day_named(text):
    assert days_named(text, TODAY) == [] and note_for(text, TODAY) is None


def test_note_for_the_turn():
    assert note_for("is Friday", TODAY) == "Friday = Friday 30 October"
    assert note_for("शुक्रवार को", TODAY) == "शुक्रवार = Friday 30 October"
    assert note_for("kal", TODAY) == "kal = Thursday 29 October"


@pytest.mark.parametrize("text, expected", [
    ("Friday आप 6 November को बोल रहे हैं, सही?", [date(2026, 11, 6)]),
    ("आपकी EMI 10 September को due थी।", [date(2026, 9, 10)]),
    ("तो 2 जनवरी?", [date(2027, 1, 2)]),  # nearest to today, so next year
    ("₹2,200 का payment", []),
])
def test_dates_said(text, expected):
    assert dates_said(text, TODAY) == expected
