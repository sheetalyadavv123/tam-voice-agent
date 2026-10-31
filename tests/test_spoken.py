from datetime import datetime

import pytest

from tools.rules import IST
from tools.spoken import callback_time, parse_dob

NOW = datetime(2026, 10, 31, 11, 0, tzinfo=IST)  # a Saturday


@pytest.mark.parametrize("text", [
    "2 November 1985", "2nd November 1985", "November 2, 1985", "02/11/1985", "1985-11-02",
    "2 नवंबर 1985", "दो नवंबर उन्नीस सौ पचासी", "do november unnees sau pachaasi",
    "second November nineteen eighty five", "two november nineteen eighty-five", "2 11 1985",
    "do gyarah unnees sau pachaasi", "unki date of birth 2 nov 1985 hai",
    "उन्नीस सौ पचासी, दो नवंबर",
])
def test_dob_in_any_form(text):
    assert parse_dob(text) == (2, 11, 1985)


@pytest.mark.parametrize("text,want", [
    ("2 November 85", "two_digit_year"), ("02/11/85", "two_digit_year"),
    ("pata nahi", "no_date"), ("nau janvari", "no_date"),
    ("teen march unnis sau athattar", "no_date"),  # a number word we don't know: not 1900
    ("चौदह मई 1990", (14, 5, 1990)), ("twenty fifth december 1988", (25, 12, 1988)),
])
def test_dob_edge_cases(text, want):
    assert parse_dob(text) == want


@pytest.mark.parametrize("text,want", [
    ("Monday 12 baje", (2026, 11, 2, 12, 0)),
    ("kal subah 11 baje", (2026, 11, 1, 11, 0)),
    ("kal shaam 6 baje", (2026, 11, 1, 18, 0)),
    ("कल शाम छह बजे", (2026, 11, 1, 18, 0)),
    ("सोमवार दोपहर 2 बजे", (2026, 11, 2, 14, 0)),
    ("tomorrow at 4 pm", (2026, 11, 1, 16, 0)),
    ("Tuesday 4:30 pm", (2026, 11, 3, 16, 30)),
    ("saade chaar baje", (2026, 10, 31, 16, 30)),
    ("4 November ko 3 baje", (2026, 11, 4, 15, 0)),
    ("6 baje", (2026, 10, 31, 18, 0)),
    ("kal 4 baje", (2026, 11, 1, 16, 0)),
])
def test_callback_times(text, want):
    assert callback_time(text, NOW)["when"] == datetime(*want, tzinfo=IST)


@pytest.mark.parametrize("text,missing", [
    ("Monday", "time"), ("kal shaam ko", "time"), ("pata nahi", "both"), ("5 November", "time"),
])
def test_a_day_without_a_time_is_not_enough(text, missing):
    assert callback_time(text, NOW) == {"missing": missing}
