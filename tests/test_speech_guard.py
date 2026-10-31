import pytest

from tools.speech_guard import (
    FALLBACK, LOCKED_FALLBACK, MAX_SENTENCES, ReplyGuard, clean_sentence, drop_this_years,
    words_to_digits,
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
    assert clean_sentence("क्या आप 30 अक्टूबर तक pay कर पाएंगे?") == (
        "क्या आप 30 अक्टूबर तक pay कर पाएंगे?", None)


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


# ---- step 8: simpler words on a phone line ----

@pytest.mark.parametrize("text, expected", [
    ("आपकी EMI 10 September को देय थी।", "आपकी EMI 10 September को due थी।"),
    ("आप कब तक भर पाएँगी?", "आप कब तक pay कर पाएँगे?"),
    ("क्या आप भुगतान कर पाएंगी?", "क्या आप pay कर पाएंगे?"),
])
def test_simpler_words(text, expected):
    assert clean_sentence(text)[0] == expected


@pytest.mark.parametrize("text, expected", [
    ("₹2,200 30 October 2026 को", "₹2,200 30 October को"),
    ("due on 10 September, 2026.", "due on 10 September."),
    ("30 अक्टूबर 2026 तक", "30 अक्टूबर तक"),
    ("10-09-2026 को", "10 September को"),
    ("5 January 2027", "5 January"),
    ("born on 2 November 1985", "born on 2 November 1985"),   # a birth year stays
    ("02-11-1985", "02-11-1985"),
])
def test_dates_lose_this_years_year(text, expected):
    assert drop_this_years(text, this_year=2026) == expected


# ---- step 4b: a third party never hears about a colleague ----

def test_third_party_hears_try_again_not_colleague():
    g = ReplyGuard("hi", locked=True, third_party=True)
    out = stream(g, ["माफ़ कीजिए। हमारी एक सहयोगी उन्हें वापस कॉल करेंगी। हमारी टीम भी संपर्क करेगी।"])
    assert out.strip() == "माफ़ कीजिए। " + LOCKED_FALLBACK["hi"]
    assert [e[0] for e in g.events] == ["colleague_for_third_party"] * 2


def test_customer_can_still_hear_about_a_colleague():
    g = ReplyGuard("en")
    assert stream(g, ["A colleague will call you back."]).strip() == "A colleague will call you back."


# ---- Hindi in Roman letters becomes Devanagari in a Hindi call ----

@pytest.mark.parametrize("text, expected", [
    ("Theek hai, maine note kar liya hai.", "ठीक है, मैंने note कर लिया hai."),
    ("Thik hai ji.", "ठीक है जी."),
    ("Sunita ji, aapka payment link WhatsApp pe gaya.", "Sunita जी, आपका payment link WhatsApp pe gaya."),
    ("aapki EMI pending hai. Dhanyavaad!", "आपकी EMI pending hai. धन्यवाद!"),
    ("2,200 rupaye 30 October ko.", "₹2,200 30 October ko."),
])
def test_roman_hindi_becomes_devanagari_in_a_hindi_call(text, expected):
    assert clean_sentence(text, language="hi")[0] == expected


@pytest.mark.parametrize("text", [
    "Payment link sent on WhatsApp for your EMI.",
    "I'll send the link by SMS.",
    "Did the EMI payment go through?",
])
def test_english_words_are_left_alone(text):
    assert clean_sentence(text, language="hi")[0] == text


def test_english_call_keeps_roman_hindi():
    assert clean_sentence("Theek hai, maine note kar liya.", language="en")[0] == \
        "Theek hai, maine note kar liya."


def test_guard_converts_roman_hindi():
    g = ReplyGuard("hi")
    assert g.feed("Theek hai ji. ") == "ठीक है जी. "


# ---- a customer who still owes a DOB hears the DOB question ----

def test_locked_fallback_line_replaces_try_later():
    g = ReplyGuard("hi", locked=True, locked_fallback="DOB?")
    assert g.feed("आपकी EMI ₹2,200 है। ") == ""
    assert g.finish() == "DOB?"
    assert ("locked_fallback", "DOB?") in g.events


def test_no_locked_fallback_keeps_try_later():
    g = ReplyGuard("hi", locked=True)
    g.feed("आपकी EMI ₹2,200 है। ")
    assert g.finish() == LOCKED_FALLBACK["hi"]


# ---- the customer gets gender-neutral Hindi ----

@pytest.mark.parametrize("text, expected", [
    ("आप EMI pay कर देंगी और हो जाए तो बताइए?", "आप EMI pay कर देंगे और हो जाए तो बताइए?"),
    ("क्या आप 30 October को pay कर पाएँगी?", "क्या आप 30 October को pay कर पाएँगे?"),
    ("आप एक बार फिर try कर लेंगी?", "आप एक बार फिर try कर लेंगे?"),
    ("आप 6 November को बोल रही हैं, सही?", "आप 6 November को बोल रहे हैं, सही?"),
    ("क्या आप अभी बात कर सकती हैं?", "क्या आप अभी बात कर सकते हैं?"),
    ("क्या आप भर पाएँगी?", "क्या आप pay कर पाएँगे?"),
    ("ठीक है, आप 6 November को ₹2,200 pay करने वाली हैं।", "ठीक है, आप 6 November को ₹2,200 pay करने वाले हैं।"),
])
def test_sentences_to_the_customer_use_neutral_forms(text, expected):
    assert clean_sentence(text, language="hi")[0] == expected


@pytest.mark.parametrize("text", [
    "मैं आपकी मदद कर सकती हूँ।",                       # the agent herself
    "मैं link भेजूँगी।",
    "हमारी एक सहयोगी आपको वापस कॉल करेंगी।",            # the colleague, not "आप"
    "आपकी EMI 10 September को due थी।",                # the EMI, not the customer
])
def test_agent_and_others_keep_their_forms(text):
    assert clean_sentence(text, language="hi")[0] == text


# ---- batch 3: the greeting is not repeated ----

@pytest.mark.parametrize("sentence", [
    "नमस्ते, मैं Tam Infosoft की AI असिस्टेंट बोल रही हूँ।",
    "नमस्ते Sunita, मैं Tam Infosoft की तरफ से AI assistant बोल रही हूँ।",
    "नमस्ते Sunita जी, मैं Tam Infosoft की AI voice assistant बोल रही हूँ।",
    "मैं Tam Infosoft की AI सहायक बोल रही हूँ।",
    "Hello, this is the AI assistant from Tam Infosoft.",
    "नमस्ते Sunita जी।",
    "Hello Sunita.",
])
def test_repeated_greeting_is_dropped(sentence):
    g = ReplyGuard("hi", greeted=True)
    assert g.feed(sentence + " ") == ""
    assert g.events[0][0] == "repeat_greeting"


@pytest.mark.parametrize("sentence", [
    "जी, मैं एक AI असिस्टेंट हूँ।",                  # answering "are you a robot?"
    "Yes, I'm an AI assistant from Tam Infosoft.",
    "Hi, how are you doing today?",
])
def test_answers_about_being_an_ai_are_kept(sentence):
    assert ReplyGuard("hi", greeted=True).feed(sentence + " ") == sentence + " "


def test_greeting_kept_before_the_code_greeted():
    s = "नमस्ते, मैं Tam Infosoft की AI असिस्टेंट बोल रही हूँ। "
    assert ReplyGuard("hi").feed(s) == s


def test_rest_of_the_reply_survives_a_dropped_greeting():
    g = ReplyGuard("hi", greeted=True, step_fallback="STEP")
    out = g.feed("नमस्ते Sunita जी, मैं Tam Infosoft की AI असिस्टेंट बोल रही हूँ। "
                 "आप इसे कब pay कर पाएंगे? ")
    assert out == "आप इसे कब pay कर पाएंगे? " and g.finish() == ""


def test_only_a_greeting_falls_back_to_the_current_step():
    g = ReplyGuard("hi", greeted=True, step_fallback="STEP")
    g.feed("नमस्ते Sunita जी। ")
    assert g.finish() == "STEP"
    assert ("step_fallback", "STEP") in g.events


def test_without_a_step_line_the_usual_fallback_is_used():
    g = ReplyGuard("hi", greeted=True)
    g.feed("नमस्ते Sunita जी। ")
    assert g.finish() == FALLBACK["hi"]


# ---- batch 4: no payment asks after hardship or a dispute ----

@pytest.mark.parametrize("sentence", [
    "आप इसे कब pay कर पाएंगे?", "आप कब तक pay कर सकेंगे?", "क्या आप अगले हफ्ते pay कर पाएंगे?",
    "क्या आप भर पाएँगी?", "क्या मैं payment link भेज दूँ?", "When can you pay the EMI?",
    "Will you be able to pay by Friday?", "Could you make the payment this week?",
    "Can I send you the payment link?", "aap kab tak pay kar paoge?",
])
def test_payment_asks_dropped_after_hardship(sentence):
    g = ReplyGuard("hi", no_push=True)
    assert g.feed(sentence + " ") == ""
    assert g.events[0][0] == "pay_push"


@pytest.mark.parametrize("sentence", [
    "आपकी EMI ₹2,200 है, जो 10 September को due थी।",
    "हमारी सहयोगी आपसे बात करेंगी।",
    "I understand, that sounds hard.",
    "Your EMI is ₹2,200.",
])
def test_amount_and_kind_words_are_kept_after_hardship(sentence):
    assert ReplyGuard("hi", no_push=True).feed(sentence + " ") == sentence + " "


def test_payment_asks_kept_before_hardship():
    assert ReplyGuard("hi").feed("आप इसे कब pay कर पाएंगे? ") == "आप इसे कब pay कर पाएंगे? "


def test_only_a_payment_ask_falls_back_to_the_step():
    g = ReplyGuard("hi", no_push=True, step_fallback="WHEN CAN A COLLEAGUE CALL?")
    g.feed("आप इसे कब pay कर पाएंगे? ")
    assert g.finish() == "WHEN CAN A COLLEAGUE CALL?"
