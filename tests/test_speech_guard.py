import pytest

from tools.speech_guard import (
    FALLBACK, LOCKED_FALLBACK, MAX_SENTENCES, ReplyGuard, clean_sentence, words_to_digits,
)


@pytest.mark.parametrize("text, expected", [
    ("Your EMI of two thousand two hundred rupees is due.", "Your EMI of 2,200 rupees is due."),
    ("Please pay by thirty October.", "Please pay by 30 October."),
    ("It is twenty-two hundred rupees.", "It is 2,200 rupees."),
    ("One lakh twenty five thousand.", "125,000."),
    ("two thousand and fifty rupees", "2,050 rupees"),
    ("आपकी EMI दो हज़ार दो सौ रुपये है।", "आपकी EMI 2,200 रुपये है।"),
    ("बाईस सौ रुपये", "2,200 रुपये"),
    ("तीस अक्टूबर को", "30 अक्टूबर को"),
    ("पांच हजार", "5,000"),
])
def test_number_words_become_digits(text, expected):
    assert words_to_digits(text) == expected


@pytest.mark.parametrize("text", [
    "एक बार फिर से बताइए?",
    "One moment please.",
    "मुझे दो मिनट दीजिए।",
    "₹2,200 on 30 October.",
    "I can call you at four.",
])
def test_ordinary_words_and_digits_left_alone(text):
    assert words_to_digits(text) == text


@pytest.mark.parametrize("text, expected", [
    ("Your EMI is overdue.", "Your EMI is due."),
    ("Your account is in default.", "Your account is in pending."),
    ("Please clear your dues.", "Please clear your EMI."),
    ("आपकी EMI ओवरड्यू है।", "आपकी EMI ड्यू है।"),
])
def test_banned_words_replaced(text, expected):
    assert clean_sentence(text) == (expected, "rewritten")


@pytest.mark.parametrize("text, why", [
    ("Otherwise we may take legal action.", "threat"),
    ("We will inform your employer.", "threat"),
    ("नहीं तो पुलिस आ सकती है।", "threat"),
    ("Please share the OTP you received.", "secret"),
    ("Can you tell me your Aadhaar number?", "secret"),
    ("अपना पिन बताइए।", "secret"),
    ("Pay at https://pay.example.com/abc now.", "link"),
    ("Go to www.tamfinance.in to pay.", "link"),
])
def test_unsafe_sentences_dropped(text, why):
    assert clean_sentence(text) == ("", why)


def test_clean_sentence_untouched():
    assert clean_sentence("क्या आप 30 अक्टूबर तक भुगतान कर पाएंगे?") == (
        "क्या आप 30 अक्टूबर तक भुगतान कर पाएंगे?", None)


def stream(guard, pieces):
    return "".join(guard.feed(p) for p in pieces) + guard.finish()


def test_guard_streams_sentences_across_chunks():
    g = ReplyGuard("en")
    out = stream(g, ["Your EMI of two thou", "sand rupees is over", "due. When can ", "you pay?"])
    assert out.strip() == "Your EMI of ₹2,000 is due. When can you pay?"


def test_guard_holds_text_until_sentence_ends():
    g = ReplyGuard("en")
    assert g.feed("Your EMI is") == ""
    assert g.feed(" due. When") == "Your EMI is due. "


def test_guard_drops_only_the_bad_sentence():
    g = ReplyGuard("hi")
    out = stream(g, ["कृपया भुगतान करें। वरना कोर्ट जाना पड़ेगा। आप कब भुगतान करेंगे?"])
    assert out.strip() == "कृपया भुगतान करें। आप कब भुगतान करेंगे?"
    assert [e[0] for e in g.events] == ["threat"]


def test_guard_fallback_when_everything_dropped():
    g = ReplyGuard("hi")
    assert stream(g, ["Share your OTP. Or police will come."]) == FALLBACK["hi"]


def test_guard_no_fallback_for_tool_only_reply():
    assert ReplyGuard("en").finish() == ""


def test_guard_caps_sentences():
    g = ReplyGuard("en")
    out = stream(g, ["One. Two. Three. Four. Five."])
    assert out.strip() == "One. Two. Three."
    assert MAX_SENTENCES == 3 and [e[0] for e in g.events] == ["too_long", "too_long"]


@pytest.mark.parametrize("text, expected", [
    ("Aapki EMI do hazaar do sau rupees hai.", "Aapki EMI 2,200 rupees hai."),
    ("Aapki EMI do thousand two hundred rupees hai.", "Aapki EMI 2,200 rupees hai."),
    ("tees October tak", "30 October tak"),
])
def test_hinglish_number_words(text, expected):
    assert words_to_digits(text) == expected


def test_unknown_multiplier_is_never_guessed():
    # "dhai" (2.5) is not known; turning "thousand" into 1,000 would change the amount
    assert words_to_digits("dhai thousand rupees") == "dhai thousand rupees"


def test_rupee_amounts_get_commas():
    assert clean_sentence("आपकी EMI ₹2200 है।") == ("आपकी EMI ₹2,200 है।", "rewritten")
    assert clean_sentence("आपकी EMI ₹2,200 है।") == ("आपकी EMI ₹2,200 है।", None)


@pytest.mark.parametrize("text", [
    "मैं उनकी EMI के बारे में कॉल कर रही थी।",
    "It is about their loan payment.",
    "उनकी किस्त बाकी है।",
    "₹2,200 is pending.",
    "Main unki kist ke baare mein call kar rahi hoon.",
])
def test_loan_talk_dropped_before_customer_confirmed(text):
    assert clean_sentence(text, locked=True) == ("", "loan_before_verified")
    assert clean_sentence(text, locked=False)[0] != ""


def test_locked_guard_keeps_harmless_sentences():
    g = ReplyGuard("hi", locked=True)
    out = stream(g, ["मैं Tam Infosoft से कॉल कर रही हूँ। उनकी EMI बाकी है। कब कॉल करूँ?"])
    assert out.strip() == "मैं Tam Infosoft से कॉल कर रही हूँ। कब कॉल करूँ?"


def test_locked_fallback_says_nothing_about_money():
    g = ReplyGuard("hi", locked=True)
    assert stream(g, ["यह उनकी EMI के बारे में है।"]) == LOCKED_FALLBACK["hi"]


def test_gender_slash_keeps_female_form():
    assert clean_sentence("मैं कॉल कर रहा/रही हूँ।")[0] == "मैं कॉल कर रही हूँ।"
    assert clean_sentence("SMS/WhatsApp")[0] == "SMS/WhatsApp"


@pytest.mark.parametrize("text, expected", [
    ("मैं कॉल कर रहा हूँ।", "मैं कॉल कर रही हूँ।"),
    ("मैं मदद कर सकता हूं।", "मैं मदद कर सकती हूं।"),
    ("मैं बाद में कॉल करूँगा।", "मैं बाद में कॉल करूँगी।"),
    ("मैं दे दूंगा।", "मैं दे दूंगी।"),
    ("मैं Sunita जी से बात कर रहा था।", "मैं Sunita जी से बात कर रही थी।"),
    ("वह कल आ रहा था।", "वह कल आ रहा था।"),   # "he was", not the AI
    ("वह कल आएगा।", "वह कल आएगा।"),
])
def test_ai_speaks_as_a_woman(text, expected):
    assert clean_sentence(text)[0] == expected


@pytest.mark.parametrize("text, expected", [
    ("2200 रुपये 30 October को", "₹2,200 30 October को"),
    ("2,200 rupees", "₹2,200"),
    ("₹2,200 रुपये", "₹2,200 रुपये"),
])
def test_amounts_with_rupees_word_get_symbol(text, expected):
    assert clean_sentence(text)[0] == expected


def test_money_words_dropped_for_third_party():
    assert clean_sentence("मैं अभी बाकी का पैसा बताने में सक्षम नहीं हूँ।", locked=True)[0] == ""
