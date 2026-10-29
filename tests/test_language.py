from tools.language import LanguageTracker, turn_language


def test_turn_language():
    assert turn_language("I'll try to pay it by Friday.") == "en"
    assert turn_language("हां जी.") == "hi"
    assert turn_language("मैं Friday तक कर दूंगी") == "hi"
    assert turn_language("30") is None
    assert turn_language("తీసుకో") is None


def test_switches_after_two_english_replies():
    t = LanguageTracker("hi")
    assert t.observe("Yes. Sunita speaking.") is None
    assert t.observe("I'll try to pay it by Friday.") == "en"
    assert t.current == "en"


def test_one_hindi_reply_does_not_switch_back():
    t = LanguageTracker("hi")
    t.observe("Yes.")
    t.observe("By Friday.")
    assert t.observe("हां जी.") is None
    assert t.observe("Okay, thank you.") is None
    assert t.current == "en"


def test_switches_back_after_two_hindi_replies():
    t = LanguageTracker("en")
    t.observe("हां जी")
    assert t.observe("शुक्रवार तक कर दूंगी") == "hi"


def test_streak_resets_on_same_language():
    t = LanguageTracker("hi")
    t.observe("Yes.")
    t.observe("हां")
    assert t.observe("Friday.") is None
    assert t.current == "hi"


def test_hinglish_in_roman_letters_counts_as_hindi():
    assert turn_language("WhatsApp pe bhej do") == "hi"
    assert turn_language("case hoga kya?") == "hi"
    assert turn_language("kal shaam 6 baje") == "hi"
    assert turn_language("I'll pay on Friday, main reason is salary") == "en"


def test_hinglish_does_not_switch_a_hindi_call_to_english():
    t = LanguageTracker("hi")
    assert t.observe("Okay") is None
    assert t.observe("WhatsApp pe bhej do") is None
    assert t.observe("case hoga kya?") is None
    assert t.current == "hi"
