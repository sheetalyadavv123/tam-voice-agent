"""What did the caller actually say? Pure functions: no AI, no audio.

The AI sometimes acts on things nobody said: it picks SMS or WhatsApp after a
plain "okay", or invents a callback time. The tools check the caller's own words
with these helpers before doing anything real.
"""
import re

from tools import names

RECENT = 2  # how many of the caller's latest messages count

WHATSAPP = re.compile(r"whats\s?app|व्हाट्स\s?(?:एप|ऐप|अप)|वॉट्स\s?(?:एप|ऐप|अप)|वाट्स\s?(?:एप|ऐप|अप)", re.I)
SMS = re.compile(r"\bs\.?\s?m\.?\s?s\b|एसएमएस|एस\s?एम\s?एस|\btext\b|टेक्स्ट", re.I)

TIME = re.compile(
    r"\d{1,2}\s*(?::\s*\d{2}|\.\d{2}|\s*(?:baje|बजे|am|pm|a\.m\.|p\.m\.|o'?clock))"
    r"|\b(?:baje|subah|savere|dopahar|dopeher|shaam|sham|raat|morning|afternoon|evening|night"
    r"|noon|o'?clock)\b"
    r"|बजे|सुबह|सवेरे|दोपहर|शाम|रात",
    re.I)

DATE = re.compile(
    r"\d|\b(?:january|february|march|april|may|june|july|august|september|october|november"
    r"|december)\b|जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|सितम्बर|अक्टूबर"
    r"|नवंबर|नवम्बर|दिसंबर|दिसम्बर",
    re.I)

# "It's me" after first saying they were someone else
I_AM = re.compile(
    r"\b(?:main\s+hi|mai\s+hi|main\s+hoon|i\s+am|i'm|it'?s\s+me|this\s+is|speaking"
    r"|bol\s+rah[ia])\b|मैं\s+ही|मै\s+ही|बोल\s+रह[ीा]",
    re.I)

LEGAL = re.compile(
    r"\b(?:case|court|legal|lawyer|advocate|police|fir|notice|jail|arrest|lawsuit|sue"
    r"|prosecut(?:e|ed|ion)|summons?|kanoo?n[iy]?|kanun[iy]?|karwai|karvai|karyawahi)\b"
    r"|केस|कोर्ट|अदालत|कानूनी|क़ानूनी|कानून|क़ानून|कार्रवाई|कार्यवाही|वकील|पुलिस|नोटिस|जेल"
    r"|गिरफ्तार|गिरफ़्तार|मुकदमा|मुक़दमा|समन",
    re.I)
NOT_LEGAL = re.compile(r"\bin case\b|\bjust in case\b|\bin any case\b|\bcase study\b", re.I)

WHY_CALLING = re.compile(
    r"kya\s?kaam|kis\s?(?:baare|bare|liye|cheez)|kyu[n]?\s?(?:call|phone|fon)|kya\s?baat"
    r"|kaun\s?(?:bol|hai|ho)|kahan\s?se|loan|emi|paisa|paise"
    r"|why\s+(?:are\s+you|did\s+you|you)\s+call|what\s+is\s+(?:this|it)\s+about|who\s+is\s+(?:this|calling)"
    r"|kiske\s?(?:baare|bare|liye)"
    r"|क्या\s?काम|किस(?:के)?\s?(?:बारे|लिए|चीज़|चीज)|क्यों\s?(?:कॉल|फ़ोन|फोन)|क्या\s?बात|कौन\s?(?:बोल|है)"
    r"|कहाँ\s?से|कहां\s?से|लोन|ईएमआई|पैसा|पैसे",
    re.I)
NOT_THE_CUSTOMER = re.compile(
    r"\b(?:behen|behan|bhai|bhaiya|didi|patni|pati|wife|husband|sister|brother|mother|father"
    r"|son|daughter|mummy|papa|beta|beti|maa|wrong\s?number)\b"
    r"|बहन|भाई|भैया|दीदी|पत्नी|पति|माँ|मां|मम्मी|पापा|बेटा|बेटी|रॉन्ग\s?नंबर|गलत\s?नंबर",
    re.I)
# "Let me talk to a person": the only way the AI may claim the caller asked for one
FOR_A_PERSON = re.compile(
    r"\b(?:human|real\s+person|a\s+person|manager|officer|executive|customer\s+care|insaan|insan"
    r"|kisi\s+(?:se|aur\s+se)\s+baat|aadmi)\b"
    r"|इंसान|किसी\s+(?:से|और\s+से)\s+बात|मैनेजर|अधिकारी|एग्जीक्यूटिव|कस्टमर\s+केयर",
    re.I)
# "No, not needed" to the payment link
DECLINE = re.compile(
    r"\b(?:no|nope|nahi|nahin|nai|mat|rehne\s+do|rahne\s+do|no\s+need|not\s+needed|don'?t)\b"
    r"|नहीं|नही|मत(?:\s|$)|रहने\s+दो|ज़रूरत\s+नहीं|जरूरत\s+नहीं",
    re.I)
DECLINE_MAX_WORDS = 6
_EDGE_L, _EDGE_R = r"(?<![A-Za-zऀ-ॿ])", r"(?![A-Za-zऀ-ॿ])"
# The customer offering to pay in their own words, e.g. after reporting hardship:
# "salary aane pe de dungi", "link bhej do, main pay kar dungi", "I can pay something"
OFFERS_TO_PAY = re.compile(
    r"\b(?:(?:pay|payment|bhugtan)\s+kar\s+(?:d(?:u|oo)ng[ia]|deti|deta|dete|sakti|sakta|sakte)"
    r"|(?:de|bhar)\s+d(?:u|oo)ng[ia]|de\s+sak(?:ti|ta|te)|kuch\s+de\s+sak"
    r"|i'?ll\s+pay|i\s+will\s+pay|i\s+can\s+pay|let\s+me\s+pay|send\s+(?:me\s+)?the\s+link"
    r"|link\s+bhej)\b"
    r"|(?:pay|payment|भुगतान)\s+कर\s+(?:दूँग|दूंग|देत|सकत)|(?:दे|भर)\s+(?:दूँग|दूंग)|दे\s+सकत"
    r"|(?:link|लिंक)\s+भेज",
    re.I)
# "Am I speaking with Sunita?" -> "haan ji" / "yes" / "ji, boliye". A bare "okay" is not a yes.
YES_FIRST = re.compile(
    r"^\W*(?:haan|haa|han|ha|haanji|ji|jee|yes|yeah|yep|yup|हाँ|हां|हा|जी)" + _EDGE_R, re.I)
# Agreeing to something the agent read back, like "6 November, सही?"
AGREE = re.compile(
    _EDGE_L + r"(?:haan|haa|han|ha|haanji|ji|jee|yes|yeah|yep|ok|okay|sahi|correct|right|theek"
    r"|thik|done|हाँ|हां|हा|जी|सही|ठीक|ओके)" + _EDGE_R, re.I)


def said_recently(lines, pattern):
    """The match of pattern in the caller's last RECENT messages, newest first, or None."""
    for line in reversed(lines[-RECENT:]):
        found = list(pattern.finditer(line))
        if found:
            return found[-1]
    return None


def named_channel(lines):
    """"sms" or "whatsapp" if the caller named one recently; the latest mention wins."""
    best = None
    for line in lines[-RECENT:]:
        hits = [(m.start(), "whatsapp") for m in WHATSAPP.finditer(line)]
        hits += [(m.start(), "sms") for m in SMS.finditer(line)]
        if hits:
            best = max(hits)[1]
    return best


def named_a_time(lines):
    """Did the caller say a time of day recently, like "kal shaam 6 baje"?"""
    return said_recently(lines, TIME) is not None


def said_a_date(lines):
    """Did the caller say a date recently, like a date of birth?"""
    return said_recently(lines, DATE) is not None


def says_is_the_customer(lines, first_name):
    """Did the caller say they are the customer, like "actually main hi Sunita hoon"?
    A plain "okay" or "haan" is not enough."""
    return (said_recently(lines, I_AM) is not None
            or any(names.mentions(line, first_name) for line in lines[-RECENT:]))


def asks_for_person(lines):
    """Did the caller ask for a person, like "kisi insaan se baat karao"?"""
    return said_recently(lines, FOR_A_PERSON) is not None


def declines(text):
    """A short "no" with no channel in it, like "नहीं, ज़रूरत नहीं" or "no thanks"."""
    if len(text.split()) > DECLINE_MAX_WORDS or named_channel([text]):
        return False
    return bool(DECLINE.search(text.strip()))


def confirms_identity(text, first_name):
    """Does this message clearly say "yes, it's me"? "haan ji", "yes", "main hi", "speaking",
    "I am", or the customer's name (any spelling: "सुनीता", "Suneeta") count; "okay", a
    question, "I'm his sister" or another name ("main Rahul bol raha hoon") do not."""
    if not text or says_not_the_customer(text) or asks_why_calling(text):
        return False
    if other_name(text, first_name):
        return False
    if YES_FIRST.search(text) or I_AM.search(text):
        return True
    # the name alone: "Sunita", "सुनीता जी"; not "Sunita ghar pe nahi hai"
    talked_about = DECLINE.search(text) or CUSTOMER_AWAY.search(text)
    return names.mentions(text, first_name) and not talked_about


def offers_to_pay(lines):
    """Did the caller offer to pay, in their last RECENT messages?"""
    return said_recently(lines, OFFERS_TO_PAY) is not None


def agrees(text):
    """A yes to what the agent just read back ("हाँ, सही है", "okay"), not a "no"."""
    return bool(AGREE.search(text)) and not DECLINE.search(text)


def legal_question(text):
    return bool(LEGAL.search(NOT_LEGAL.sub(" ", text)))


def asks_why_calling(text):
    return bool(WHY_CALLING.search(text))


def says_not_the_customer(text):
    return bool(NOT_THE_CUSTOMER.search(text))


# ---- someone else answered: who are they, and may we talk to them? ----
RELATIONSHIPS = [  # (saved as, the words for it)
    ("brother", r"bhai|bhaiya|brother|भाई|भैया"),
    ("sister", r"behen|behan|bahan|sister|didi|बहन|दीदी"),
    ("husband", r"pati|husband|पति"),
    ("wife", r"patni|wife|biwi|पत्नी|बीवी"),
    ("mother", r"maa|mummy|mother|mom|ammi|माँ|मां|मम्मी|अम्मी"),
    ("father", r"papa|father|pita|pitaji|dad|abbu|पापा|पिता|पिताजी|अब्बू"),
    ("son", r"beta|son|बेटा"),
    ("daughter", r"beti|daughter|बेटी"),
    ("sister-in-law", r"bhabhi|bhabi|भाभी"),
    ("friend", r"dost|friend|saheli|दोस्त|सहेली"),
    ("neighbour", r"padosi|neighbou?r|पड़ोसी"),
    ("relative", r"rishtedar|relative|chacha|chachi|mama|mami|uncle|aunty|रिश्तेदार|चाचा|चाची|मामा|मामी"),
]
_RELATIONSHIPS = [(name, re.compile(_EDGE_L + f"(?:{p})" + _EDGE_R, re.I)) for name, p in RELATIONSHIPS]
WRONG_NUMBER = re.compile(
    r"wrong\s?number|galat\s?number|no\s+one\s+(?:named|by\s+that\s+name)|koi\s+\S+\s+nahi\s+reh"
    r"|रॉन्ग\s?नंबर|गलत\s?नंबर|ग़लत\s?नंबर|यहाँ\s+कोई\s+\S+\s+नहीं",
    re.I)
# The customer is talked about, not talking: "woh ghar pe nahi hain", "she's not home"
CUSTOMER_AWAY = re.compile(
    r"\b(?:wo|woh|vo|voh)\b.*\b(?:nahi|nahin)\b|\b(?:she|he)\s+is\s*n'?o?t|\b(?:she|he)'?s\s+not"
    r"|\bnot\s+(?:at\s+)?home\b|(?:वो|वह|वे)\s.*(?:नहीं)",
    re.I)
# Asking to be called later is the customer being busy, not someone else
CALL_LATER = re.compile(
    r"\b(?:busy|baad\s+m(?:ein|e|ai)|later|driving|meeting|call\s+back|abhi\s+baat\s+nahi)\b"
    r"|व्यस्त|बाद\s+में|अभी\s+बात\s+नहीं",
    re.I)
# A name the caller gives for themselves: "main Rahul bol raha hoon", "mera naam Rahul hai"
NAME_INTRO = [
    re.compile(r"(?:mera|my)\s+(?:naam|name)\s+(?:is\s+|hai\s+)?([A-Za-zऀ-ॿ]+)", re.I),
    re.compile(r"मेरा\s+नाम\s+([A-Za-zऀ-ॿ]+)"),
    re.compile(r"\b(?:this\s+is|i\s+am|i'm)\s+([A-Za-z]+)", re.I),
    re.compile(r"([A-Za-zऀ-ॿ]+)\s+(?:bol\s+rah[aie]|बोल\s+रह[ाीे]|speaking|here\b)", re.I),
    re.compile(r"(?:main|mai|मैं|मै)\s+([A-Za-zऀ-ॿ]+)\s+(?:hoon|hun|hu|हूँ|हूं)", re.I),
]
NOT_A_NAME = set("""
main mai hi ji haan han ha nahi nahin unka unki unke uska uski uske inka inki
her his their the a an not just also abhi toh to bhi yes no okay ok sorry hello wahi vahi aap ap
me myself calling from busy thik theek free fine ghar bahar office driving yahan wahan ready
here available sure good speaking bol raha rahi
मैं मै ही जी हाँ हां नहीं उनका उनकी उनके उसका उसकी इनका इनकी आप अभी तो भी वही ठीक घर बाहर
""".split())
# Yes or no to "may I talk to you about the account?"
CONSENT_YES = re.compile(
    r"\b(?:haan|haa|han|ha|haanji|ji|jee|yes|yeah|yep|sure|okay|ok|bilkul|zaroor|jaroor|jarur"
    r"|boliye|bolo|bataiye|batao|kariye|theek|thik|haan\s+ji|go\s+ahead|of\s+course)\b"
    r"|हाँ|हां|जी|बिल्कुल|बिलकुल|ज़रूर|जरूर|बोलिए|बोलो|बताइए|बताइये|ठीक|ओके",
    re.I)
NO_PROBLEM = re.compile(
    r"koi\s+(?:baat|problem|dikkat)\s+nahi|no\s+problem|कोई\s+(?:बात|problem|दिक्कत)\s+नहीं", re.I)
IT_IS_ME = re.compile(
    r"\b(?:main\s+hi|mai\s+hi|it'?s\s+me|it\s+is\s+me|i\s+am\s+the\s+customer)\b|मैं\s+ही|मै\s+ही",
    re.I)
FILLER_ONLY = {"okay", "ok", "hmm", "hm", "hmmm", "hello", "hallo", "helo", "acha", "accha",
               "achha", "alright", "right", "ओके", "हेलो", "हैलो", "हम्म", "अच्छा"}


def relationship(text):
    """"brother", "sister", ... if the caller said how they are related, else None."""
    hits = [(m.start(), name) for name, p in _RELATIONSHIPS for m in p.finditer(text or "")]
    return min(hits)[1] if hits else None


def other_name(text, first_name):
    """A name the caller gave for themselves that is not the customer's, or None.
    Roman words count only when capitalised, as speech-to-text writes names."""
    for pattern in NAME_INTRO:
        for m in pattern.finditer(text or ""):
            word = m.group(1)
            if word.lower() in NOT_A_NAME or word in NOT_A_NAME:
                continue
            if word.isascii() and not word[0].isupper():
                continue
            if not names.same_name(word, first_name):
                return word
    return None


def wrong_number(text):
    return bool(WRONG_NUMBER.search(text or ""))


def someone_else(text, first_name):
    """Did the person who answered say they are not the customer? A relation ("main unka
    bhai"), another name, a wrong number, "woh ghar pe nahi hain", or a short "no"."""
    if not text or confirms_identity(text, first_name) or CALL_LATER.search(text):
        return False
    return bool(says_not_the_customer(text) or other_name(text, first_name) or wrong_number(text)
                or CUSTOMER_AWAY.search(text) or declines(text))


def claims_to_be_customer(text, first_name):
    """"Actually main hi Sunita hoon", "it's me", "Sunita bol rahi hoon"; not "main unki behen"."""
    if not text or says_not_the_customer(text) or other_name(text, first_name):
        return False
    return bool(IT_IS_ME.search(text) or (names.mentions(text, first_name) and I_AM.search(text)))


def consent(text):
    """"yes", "no" or None (unclear) for "may I talk to you about the account?"."""
    text = NO_PROBLEM.sub(" haan ", text or "")
    no, yes = DECLINE.search(text), CONSENT_YES.search(text)
    if no and yes:
        return "no" if no.start() < yes.start() else "yes"
    return "no" if no else "yes" if yes else None


def only_filler(text):
    """"Okay", "hello", "hmm": nothing that answers "am I speaking with Sunita?"."""
    words = re.findall(r"[A-Za-zऀ-ॿ']+", text or "")
    return bool(words) and all(w.lower() in FILLER_ONLY for w in words)


def busy(text):
    return bool(CALL_LATER.search(text or ""))


# ---- hardship, in the caller's words ----
HARDSHIP = re.compile(
    r"\b(?:financial(?:ly)?\s+(?:problem|issue|crisis|difficult\w*|trouble|condition|tight)"
    r"|money\s+problem|lost\s+(?:my|his|her)\s+job|laid\s+off|no\s+job|jobless|unemployed"
    r"|no\s+money|can'?t\s+afford|cannot\s+afford|medical|hospital\w*|illness|sick|surgery"
    r"|accident|treatment|salary\s+(?:has\s*n'?t|did\s*n'?t|not|nahi|nahin|abhi\s+tak\s+nahi)"
    r"|no\s+salary|salary\s+(?:delay\w*|ruki|atki|band)"
    r"|paisa\s+nahi\w*|paise\s+nahi\w*|paiso\w*\s+ki\s+(?:dikkat|tangi|problem|kami|pareshani)"
    r"|haath\s+tang|tangi|financial|(?:job|naukri|nokri|kaam)\s+(?:chali|chhoot|chhut|chut|gayi|gai|nahi|band)"
    r"|bimar|beemar|bimari|beemari|ilaaj|ilaj|operation|business\s+(?:band|loss|thanda|nahi))\b"
    r"|पैसे\s+नहीं|पैसा\s+नहीं|पैसों\s+की\s+(?:दिक्कत|तंगी|परेशानी|कमी)|तंगी|हाथ\s+तंग"
    r"|(?:नौकरी|जॉब|काम)\s+(?:चली|छूट|छुट|नहीं|बंद)|बीमार|अस्पताल|हॉस्पिटल|इलाज|ऑपरेशन"
    r"|(?:सैलरी|salary|तनख्वाह|तनख़्वाह)\s+(?:नहीं|अभी\s+तक\s+नहीं|रुकी|अटकी)|आर्थिक",
    re.I)
# A day to pay, beyond the weekdays and "kal" that tools/dates finds: "5 tareekh ko"
PAY_DAY = re.compile(r"(?<!\d)\d{1,2}\s*(?:tareekh|tarikh|तारीख|ko\b|को)", re.I)


def hardship(text):
    """"job chali gayi", "paise nahi hain", "salary nahi aayi", "medical emergency"..."""
    return bool(HARDSHIP.search(text or ""))


# Fixed lines the code speaks itself, so the AI can't improvise them.
LEGAL_LINE = {
    "hi": "इस बारे में मैं जानकारी नहीं दे सकती। हमारी एक सहयोगी आपको वापस कॉल करेंगी। धन्यवाद।",
    "en": "I can't help with that question. A colleague will call you back. Thank you.",
}

# {name} is the customer's first name, {amount} like "2,200", {date} like "30 October".
# The customer gets gender-neutral Hindi; the agent speaks as a woman.
PROMISE_CLOSE_SENT = {
    "hi": "{name} जी, link भेज दिया गया है। ₹{amount} का payment {date} तक कर दीजिए। "
          "धन्यवाद, आपका दिन शुभ हो।",
    "en": "{name}, the link has been sent. Please pay ₹{amount} by {date}. "
          "Thank you, have a good day.",
}
PROMISE_CLOSE_DECLINED = {
    "hi": "ठीक है {name} जी। ₹{amount} का payment {date} तक कर दीजिए। धन्यवाद, आपका दिन शुभ हो।",
    "en": "All right, {name}. Please pay ₹{amount} by {date}. Thank you, have a good day.",
}
CALLBACK_TIME_QUESTION = {
    "hi": "हमारी सहयोगी आपको किस दिन और किस समय कॉल करें?",
    "en": "What day and time would suit you for a call from our colleague?",
}
# {when} like "Monday 2 November, दोपहर 12 बजे" / "Monday 2 November at 12 pm"
HANDOFF_CLOSE = {
    "hi": "ठीक है {name} जी, हमारी सहयोगी आपको {when} कॉल करेंगी। धन्यवाद, अपना ध्यान रखिए।",
    "en": "All right, {name}. Our colleague will call you on {when}. Thank you, take care.",
}
CONFIRM_IDENTITY = {
    "hi": "क्या मेरी बात {name} जी से हो रही है?",
    "en": "Am I speaking with {name}?",
}
DUE_QUESTION = {
    "hi": "आपकी ₹{amount} की EMI {date} को due थी। आप इसे कब pay कर पाएंगे?",
    "en": "Your EMI of ₹{amount} was due on {date}. When will you be able to pay it?",
}
DOB_QUESTION = {
    "hi": "{name} जी, verification के लिए कृपया अपनी date of birth बताइए, दिन, महीना और साल।",
    "en": "{name}, for verification, please tell me your date of birth, with the day, month and year.",
}
DOB_RETRY = {
    "hi": "माफ़ कीजिए, यह match नहीं हुआ। {name} जी, कृपया अपनी date of birth फिर से बताइए, "
          "दिन, महीना और साल।",
    "en": "Sorry, that didn't match. {name}, please tell me your date of birth again, "
          "with the day, month and year.",
}
DUE_QUESTION_WITH_PROMISE = {
    "hi": "आपकी ₹{amount} की EMI {date} को due थी, और आपने {promise} तक pay करने का बताया था। "
          "क्या आप उस दिन तक pay कर पाएंगे?",
    "en": "Your EMI of ₹{amount} was due on {date}, and you had planned to pay by {promise}. "
          "Will you be able to pay by then?",
}
LOAN_CLOSED = {
    "hi": "धन्यवाद। आपके loan पर अभी कोई payment due नहीं है। तकलीफ़ के लिए माफ़ी चाहती हूँ, "
          "आपका दिन शुभ हो।",
    "en": "Thank you. There is no payment due on your loan right now. Sorry for the trouble, "
          "have a good day.",
}
DOB_FULL_YEAR = {
    "hi": "कृपया साल पूरा बताइए, चारों अंकों के साथ।",
    "en": "Please tell me the full year, with all four digits.",
}
DOB_NOT_HEARD = {
    "hi": "माफ़ कीजिए, मैं date of birth समझ नहीं पाई। कृपया दिन, महीना और साल बताइए।",
    "en": "Sorry, I didn't catch the date of birth. Please tell me the day, month and year.",
}
CLAIM_VERIFIED = {
    "hi": "धन्यवाद, verification हो गया।",
    "en": "Thank you, you're verified.",
}
WRONG_NUMBER_CLOSE = {
    "hi": "माफ़ कीजिए, शायद गलत नंबर लग गया। आपका समय लेने के लिए धन्यवाद।",
    "en": "Sorry, I think I have the wrong number. Thank you for your time.",
}

# ---- someone else answered. {name} is the customer's first name. ----
TP_RELATIONSHIP = {
    "hi": "आप {name} जी के क्या लगते हैं?",
    "en": "May I know how you are related to {name}?",
}
TP_CONSENT = {
    "hi": "क्या मैं आपसे {name} जी के account के बारे में बात कर सकती हूँ?",
    "en": "May I speak with you about {name}'s account?",
}
TP_CONSENT_AGAIN = {
    "hi": "माफ़ कीजिए, कृपया हाँ या ना में बताइए। क्या मैं आपसे {name} जी के account के बारे में "
          "बात कर सकती हूँ?",
    "en": "Sorry, could you tell me yes or no: may I speak with you about {name}'s account?",
}
TP_DOB = {
    "hi": "Verification के लिए कृपया {name} जी की date of birth बताइए।",
    "en": "For verification, please tell me {name}'s date of birth.",
}
TP_DOB_RETRY = {
    "hi": "माफ़ कीजिए, यह match नहीं हुआ। कृपया {name} जी की date of birth फिर से बताइए, "
          "दिन, महीना और साल।",
    "en": "Sorry, that didn't match. Please tell me {name}'s date of birth again, with the day, "
          "month and year.",
}
TP_PERSONAL = {
    "hi": "यह {name} जी से जुड़ा एक personal मामला है।",
    "en": "It's a personal matter for {name}.",
}
TP_NO_SHARE = {
    "hi": "ठीक है। यह {name} जी से जुड़ा मामला है, इसलिए मैं इसके बारे में सिर्फ़ उन्हीं से बात कर "
          "सकती हूँ। उनसे किस दिन और किस समय बात हो सकती है?",
    "en": "All right. This is a matter for {name}, so I can only discuss it with them. "
          "What day and time would be good to reach {name}?",
}
TP_DOB_FAILED = {
    "hi": "माफ़ कीजिए, verification पूरा नहीं हो पाया, इसलिए मैं इसके बारे में बात नहीं कर सकती। "
          "{name} जी से किस दिन और किस समय बात हो सकती है?",
    "en": "Sorry, I couldn't complete the verification, so I can't discuss this. "
          "What day and time would be good to reach {name}?",
}
TIME_INCOMPLETE = {
    "hi": "कृपया दिन और समय दोनों बताइए।",
    "en": "Could you tell me both the day and the time?",
}
# {start} and {end} like "8" and "7", from the calling hours in tools/policy
TIME_OUTSIDE = {
    "hi": "हम सुबह {start} बजे से शाम {end} बजे के बीच ही कॉल करते हैं। उस बीच कौन सा दिन और समय "
          "ठीक रहेगा?",
    "en": "We only call between {start} am and {end} pm. What day and time in those hours "
          "would suit?",
}
TP_CLOSE_TIME = {
    "hi": "धन्यवाद। हम {name} जी को {when} फिर से कॉल करेंगे। आपका दिन शुभ हो।",
    "en": "Thank you. We'll call {name} again on {when}. Have a good day.",
}
TP_CLOSE = {
    "hi": "धन्यवाद। हम {name} जी से बाद में फिर से संपर्क करेंगे। आपका दिन शुभ हो।",
    "en": "Thank you. We'll get in touch with {name} again later. Have a good day.",
}
# Verified: the EMI is talked about in the third person
TP_REMINDER = {
    "hi": "धन्यवाद, verification हो गया। {name} जी की ₹{amount} की EMI {date} को due थी। "
          "वे इसे कब तक pay कर पाएंगे?",
    "en": "Thank you, that's verified. {name}'s EMI of ₹{amount} was due on {date}. "
          "When will they be able to pay it?",
}
TP_DUE_QUESTION = {
    "hi": "{name} जी की ₹{amount} की EMI {date} को due थी। वे इसे कब तक pay कर पाएंगे?",
    "en": "{name}'s EMI of ₹{amount} was due on {date}. When will they be able to pay it?",
}
TP_LINK_SENT = {
    "hi": "Payment link {name} जी के registered number, जिसके आखिरी अंक {digits} हैं, पर भेज दिया "
          "गया है। Payment हो जाए तो मुझे बता दीजिए।",
    "en": "The payment link has been sent to {name}'s registered number, ending {digits}. "
          "Please tell me once the payment is done.",
}
PROMISE_CLOSE_SENT_TP = {
    "hi": "ठीक है, link {name} जी के registered number पर भेज दिया गया है। ₹{amount} का payment "
          "{date} तक हो जाना चाहिए। धन्यवाद, आपका दिन शुभ हो।",
    "en": "All right, the link has been sent to {name}'s registered number. The payment of "
          "₹{amount} is due by {date}. Thank you, have a good day.",
}
PROMISE_CLOSE_DECLINED_TP = {
    "hi": "ठीक है। ₹{amount} का payment {date} तक हो जाना चाहिए। धन्यवाद, आपका दिन शुभ हो।",
    "en": "All right. The payment of ₹{amount} is due by {date}. Thank you, have a good day.",
}

# ---- hardship ----
HARDSHIP_DATE_ASK = {
    "hi": "मैं समझ सकती हूँ, {name} जी। क्या आप बता पाएंगे कि आप किस तारीख तक pay कर पाएंगे?",
    "en": "I understand, {name}. Could you tell me a date by which you'll be able to pay?",
}
HARDSHIP_CALLBACK = {
    "hi": "कोई बात नहीं। हमारी सहयोगी आपसे बात करेंगी। उन्हें आपको किस दिन और किस समय कॉल करना "
          "ठीक रहेगा?",
    "en": "That's all right. A colleague will talk to you about it. What day and time would "
          "suit you for their call?",
}
HANDOFF_CLOSE_NO_TIME = {
    "hi": "ठीक है {name} जी, हमारी सहयोगी आपसे जल्द संपर्क करेंगी। धन्यवाद, अपना ध्यान रखिए।",
    "en": "All right, {name}. Our colleague will be in touch soon. Thank you, take care.",
}
SILENCE_CHECK = {
    "hi": "क्या आप मुझे सुन पा रहे हैं?",
    "en": "Can you hear me?",
}
SILENCE_CLOSE = {
    "hi": "लगता है आपकी आवाज़ नहीं आ रही है। हम बाद में फिर से संपर्क करेंगे। धन्यवाद।",
    "en": "I can't seem to hear you. We'll get in touch again later. Thank you.",
}


def line(lines, language, **values):
    return lines.get(language, lines["en"]).format(**values)
