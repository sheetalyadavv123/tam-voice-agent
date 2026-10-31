"""What did the caller actually say? Pure functions: no AI, no audio.

The AI sometimes acts on things nobody said: it picks SMS or WhatsApp after a
plain "okay", or invents a callback time. The tools check the caller's own words
with these helpers before doing anything real.
"""
import re

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
    r"\b(?:case|court|legal|lawyer|advocate|police|fir|notice|jail|arrest|lawsuit|sue)\b"
    r"|केस|कोर्ट|अदालत|कानूनी|क़ानूनी|वकील|पुलिस|नोटिस|जेल|गिरफ्तार|गिरफ़्तार|मुकदमा|मुक़दमा",
    re.I)
NOT_LEGAL = re.compile(r"\bin case\b|\bjust in case\b|\bin any case\b|\bcase study\b", re.I)

WHY_CALLING = re.compile(
    r"kya\s?kaam|kis\s?(?:baare|bare|liye|cheez)|kyu[n]?\s?(?:call|phone|fon)|kya\s?baat"
    r"|kaun\s?(?:bol|hai|ho)|kahan\s?se|loan|emi|paisa|paise"
    r"|why\s+(?:are\s+you|did\s+you|you)\s+call|what\s+is\s+(?:this|it)\s+about|who\s+is\s+(?:this|calling)"
    r"|क्या\s?काम|किस\s?(?:बारे|लिए|चीज़|चीज)|क्यों\s?(?:कॉल|फ़ोन|फोन)|क्या\s?बात|कौन\s?(?:बोल|है)"
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
    named = re.compile(re.escape(first_name), re.I)
    return said_recently(lines, I_AM) is not None or said_recently(lines, named) is not None


def asks_for_person(lines):
    """Did the caller ask for a person, like "kisi insaan se baat karao"?"""
    return said_recently(lines, FOR_A_PERSON) is not None


def declines(text):
    """A short "no" with no channel in it, like "नहीं, ज़रूरत नहीं" or "no thanks"."""
    if len(text.split()) > DECLINE_MAX_WORDS or named_channel([text]):
        return False
    return bool(DECLINE.search(text.strip()))


def legal_question(text):
    return bool(LEGAL.search(NOT_LEGAL.sub(" ", text)))


def asks_why_calling(text):
    return bool(WHY_CALLING.search(text))


def says_not_the_customer(text):
    return bool(NOT_THE_CUSTOMER.search(text))


# Fixed lines the code speaks itself, so the AI can't improvise them.
LEGAL_LINE = {
    "hi": "इस बारे में मैं जानकारी नहीं दे सकती। हमारी एक सहयोगी आपको वापस कॉल करेंगी। धन्यवाद।",
    "en": "I can't help with that question. A colleague will call you back. Thank you.",
}
THIRD_PARTY_LINE = {
    "hi": "यह {name} जी से जुड़ा एक personal मामला है, मैं इसके बारे में सिर्फ़ उन्हीं से बात कर सकती हूँ। "
          "उनसे बात करने का सही समय क्या रहेगा?",
    "en": "It's a personal matter, and I can only discuss it with {name}. "
          "When would be a good time to reach them?",
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
DOB_FAILED_CLOSE = {
    "hi": "माफ़ कीजिए, मैं verification पूरा नहीं कर पाई। हम बाद में फिर से संपर्क करेंगे। धन्यवाद।",
    "en": "Sorry, I couldn't complete the verification. We'll get in touch again later. Thank you.",
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
