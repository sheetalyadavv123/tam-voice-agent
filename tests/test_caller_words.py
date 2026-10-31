import pytest

from tools import caller_words
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


# ---- batch 3: only a clear yes confirms identity ----

@pytest.mark.parametrize("text", [
    "haan ji", "हाँ, मैं ही बोल रही हूँ", "जी, बोलिए", "ji haan", "yes", "Yes, speaking",
    "main hi hoon", "I am", "It's me", "Sunita here", "हां जी",
])
def test_clear_yes_confirms_identity(text):
    assert caller_words.confirms_identity(text, "Sunita") is True


@pytest.mark.parametrize("text", [
    "okay", "Okay", "ok", "hmm", "", "kaun bol raha hai?", "kya kaam hai ji?",
    "haan, main unki behen hoon", "wrong number", "theek hai",
])
def test_okay_or_a_question_does_not_confirm(text):
    assert caller_words.confirms_identity(text, "Sunita") is False


@pytest.mark.parametrize("text, expected", [
    ("हाँ, 6 November सही है", True), ("haan, WhatsApp pe bhej do", True), ("okay", True),
    ("ji", True), ("नहीं, 7 November", False), ("WhatsApp pe bhej do", False),
])
def test_agrees_to_a_read_back(text, expected):
    assert caller_words.agrees(text) is expected


# ---- batch 4 combined ----

@pytest.mark.parametrize("text", ["किसके बारे में है?", "किस बारे में?", "क्या काम है?", "किस लिए फोन किया?",
                                  "kiske baare mein hai"])
def test_why_calling_in_devanagari(text):
    assert caller_words.asks_why_calling(text)


@pytest.mark.parametrize("text", ["kya legal action hoga?", "prosecution hoga kya", "legal notice aayega?",
                                  "कानूनी कार्रवाई होगी?", "kanooni karwai hogi kya", "summons bhejoge?"])
def test_new_legal_words(text):
    assert caller_words.legal_question(text)


@pytest.mark.parametrize("text", ["in case I forget", "just in case, send it on WhatsApp"])
def test_in_case_is_still_not_legal(text):
    assert not caller_words.legal_question(text)


@pytest.mark.parametrize("text", [
    "abhi meri job chali gayi hai", "paise nahi hain abhi", "salary nahi aayi", "medical emergency hai",
    "financial problem chal rahi hai", "मेरी नौकरी चली गई", "पैसे नहीं हैं", "I lost my job",
    "beemar hoon, hospital mein hoon", "salary delayed hai", "I can't afford it this month",
])
def test_hardship_words(text):
    assert caller_words.hardship(text)


@pytest.mark.parametrize("text", ["haan bataiye", "salary aane pe de dungi", "kya kaam hai", "Friday ko kar dungi"])
def test_not_hardship(text):
    assert not caller_words.hardship(text)


@pytest.mark.parametrize("text,rel,name", [
    ("nahi, main unka bhai Rahul bol raha hoon", "brother", "Rahul"),
    ("main unki behen hoon", "sister", None),
    ("mera naam Rahul hai", None, "Rahul"),
    ("I am her husband", "husband", None),
    ("This is Rahul", None, "Rahul"),
])
def test_who_answered(text, rel, name):
    assert caller_words.someone_else(text, "Sunita")
    assert caller_words.relationship(text) == rel
    assert caller_words.other_name(text, "Sunita") == name


@pytest.mark.parametrize("text", ["haan ji", "Okay", "abhi nahi, baad mein call karo", "main busy hoon",
                                  "kaun bol raha hai?", "main thik hoon"])
def test_not_someone_else(text):
    assert not caller_words.someone_else(text, "Sunita")


@pytest.mark.parametrize("text,want", [
    ("haan bataiye", "yes"), ("ji boliye", "yes"), ("koi baat nahi, bataiye", "yes"), ("theek hai", "yes"),
    ("nahi", "no"), ("nahi, unse hi baat kariye", "no"), ("hmm", None),
])
def test_consent(text, want):
    assert caller_words.consent(text) == want


@pytest.mark.parametrize("text", ["actually main hi Sunita hoon", "it's me", "Sunita bol rahi hoon"])
def test_claims_to_be_the_customer(text):
    assert caller_words.claims_to_be_customer(text, "Sunita")


def test_relative_is_not_a_claim():
    assert not caller_words.claims_to_be_customer("main unki behen bol rahi hoon", "Sunita")
