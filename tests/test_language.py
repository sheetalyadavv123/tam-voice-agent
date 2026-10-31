import pytest

from tools.language import LanguageTracker, switch_language, turn_language


def test_turn_language():
    assert turn_language("I'll try to pay it by Friday.") == "en"
    assert turn_language("हां जी.") == "hi"
    assert turn_language("मैं Friday तक कर दूंगी") == "hi"
    assert turn_language("30") is None
    assert turn_language("తీసుకో") is None


@pytest.mark.parametrize("text", ["Okay", "Thank you", "Okay, thank you.", "yes", "Hello hello hello",
                                  "Hmm okay bro", "No thanks", "By Friday."])
def test_short_or_filler_replies_keep_hindi(text):
    t = LanguageTracker("hi")
    assert t.observe(text) is None
    assert t.current == "hi"


def test_a_full_english_sentence_switches_to_english():
    t = LanguageTracker("hi")
    assert t.observe("I'll pay it on Friday") == "en"
    assert t.current == "en"


def test_short_hindi_reply_stays_hindi():
    t = LanguageTracker("hi")
    assert t.observe("हां ठीक है") is None
    assert t.current == "hi"


def test_short_hindi_reply_does_not_switch_english_back():
    t = LanguageTracker("en")
    assert t.observe("हां जी") is None
    assert t.current == "en"


def test_full_hindi_sentence_switches_back():
    t = LanguageTracker("en")
    assert t.observe("शुक्रवार तक कर दूंगी") == "hi"


def test_mostly_latin_means_more_than_half_the_words():
    assert switch_language("मैं Friday तक कर दूंगी") == "hi"
    assert switch_language("I will pay Friday ठीक") == "en"
    assert switch_language("EMI link भेज दो") is None  # two Latin, two Devanagari words


def test_hinglish_in_roman_letters_counts_as_hindi():
    assert turn_language("WhatsApp pe bhej do") == "hi"
    assert turn_language("case hoga kya?") == "hi"
    assert turn_language("kal shaam 6 baje") == "hi"
    assert turn_language("I'll pay on Friday, main reason is salary") == "en"


@pytest.mark.parametrize("text", ["agle hafte kisi din", "6 November ke aas paas", "kal tak kar dungi",
                                  "Friday ko de dungi", "salary aane ke baad"])
def test_more_hinglish_stays_hindi(text):
    # "agle hafte kisi din" switched a real nano call to English
    t = LanguageTracker("hi")
    assert t.observe(text) is None and t.current == "hi"


def test_english_with_common_words_still_switches():
    t = LanguageTracker("hi")
    assert t.observe("I will pay it next week") == "en"


def test_hinglish_does_not_switch_a_hindi_call_to_english():
    t = LanguageTracker("hi")
    assert t.observe("Okay") is None
    assert t.observe("WhatsApp pe bhej do") is None
    assert t.observe("case hoga kya?") is None
    assert t.current == "hi"
