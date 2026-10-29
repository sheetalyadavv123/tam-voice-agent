import pytest

from tools.caller_words import (
    asks_why_calling, legal_question, named_a_time, named_channel, says_not_the_customer,
)


# ---- step 2a: a link only on a channel the caller named ----

@pytest.mark.parametrize("lines", [["okay"], ["haan"], ["हां भेज दो"], ["ठीक है"], []])
def test_no_channel_from_a_plain_yes(lines):
    assert named_channel(lines) is None


@pytest.mark.parametrize("lines, channel", [
    (["WhatsApp pe bhej do"], "whatsapp"),
    (["व्हाट्सएप पर भेज दीजिए"], "whatsapp"),
    (["SMS kar do"], "sms"),
    (["एसएमएस पर"], "sms"),
    (["SMS nahi, WhatsApp pe bhejo"], "whatsapp"),     # the latest mention wins
    (["WhatsApp pe", "haan bhej do"], "whatsapp"),     # one message back still counts
])
def test_channel_named(lines, channel):
    assert named_channel(lines) == channel


def test_channel_older_than_two_messages_does_not_count():
    assert named_channel(["WhatsApp pe", "haan", "okay"]) is None


# ---- step 2b: a callback time only from the caller ----

@pytest.mark.parametrize("lines", [["okay"], ["haan theek hai"], ["kal"], ["मेरी बहन हूँ"]])
def test_no_time_said(lines):
    assert named_a_time(lines) is False


@pytest.mark.parametrize("lines", [
    ["kal shaam 6 baje"], ["कल शाम 6 बजे"], ["4 pm"], ["subah aa jayengi"], ["10:30"],
    ["after 5 o'clock"], ["शाम को कर लेना", "ठीक है"],
])
def test_time_said(lines):
    assert named_a_time(lines) is True


# ---- step 3: legal questions ----

@pytest.mark.parametrize("text", [
    "case hoga kya?", "केस होगा क्या?", "Will you take me to court?", "पुलिस आएगी क्या?",
    "legal notice bhejoge?", "वकील से बात करूँ?",
])
def test_legal_questions(text):
    assert legal_question(text)


@pytest.mark.parametrize("text", ["in case I forget", "haan theek hai", "शुक्रवार को दे दूँगी"])
def test_not_legal(text):
    assert not legal_question(text)


# ---- step 4a: third parties asking why ----

@pytest.mark.parametrize("text", [
    "kya kaam hai?", "क्या काम है?", "किस बारे में है?", "loan ka hai kya?",
    "why are you calling?", "क्यों कॉल किया?",
])
def test_why_calling(text):
    assert asks_why_calling(text)


@pytest.mark.parametrize("text", [
    "main unki behen hoon", "मैं उनकी बहन हूँ", "this is her husband", "wrong number",
])
def test_not_the_customer(text):
    assert says_not_the_customer(text)


def test_customer_saying_yes_is_not_a_third_party():
    assert not says_not_the_customer("haan main hi bol rahi hoon")


# ---- the caller's own words for identity ----

from tools.caller_words import said_a_date, says_is_the_customer  # noqa: E402


@pytest.mark.parametrize("lines", [["okay"], ["haan"], ["ठीक है"], ["kal shaam 6 baje"]])
def test_plain_yes_is_not_i_am_the_customer(lines):
    assert not says_is_the_customer(lines, "Sunita")


@pytest.mark.parametrize("lines", [
    ["actually main hi Sunita hoon"], ["मैं ही बोल रही हूँ"], ["Sunita"], ["it's me"],
])
def test_i_am_the_customer(lines):
    assert says_is_the_customer(lines, "Sunita")


@pytest.mark.parametrize("lines, said", [
    (["15 March 1990"], True), (["पंद्रह मार्च"], True), (["actually main hi Sunita hoon"], False),
    (["okay"], False),
])
def test_said_a_date(lines, said):
    assert said_a_date(lines) is said
