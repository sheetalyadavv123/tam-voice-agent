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
