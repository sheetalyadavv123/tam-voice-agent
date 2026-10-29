"""Checks every sentence the AI is about to speak. Pure functions: no AI, no audio.

The prompt asks for these rules, but a small model forgets them, so the agent
runs each reply through clean_sentence before it reaches the voice.

- Words we never use ("overdue", "default", "dues") are swapped for neutral ones.
- A sentence that threatens (police, court, legal, employer, neighbours), asks for
  a secret (OTP, PIN, password, CVV, card number, Aadhaar, bank details) or
  reads out a web link is dropped.
- Until the customer is confirmed, a sentence that mentions the loan, EMI,
  payment or an amount is dropped, so nothing leaks to someone else.
- "रहा/रही" style slashes keep the second, female form.
- Amounts and dates written as words become digits, like "दो हज़ार दो सौ" -> "2,200".
  Only numbers next to hundred/thousand/lakh, a month or "rupees" are changed, so
  "एक बार" and "one moment" stay as they are.
- Words that are hard to catch on a phone line get simpler ones ("देय" -> "due",
  "भर पाएँगी" -> "pay कर पाएँगी"), and this year's dates lose the year.
- When someone else answered, a promise that "a colleague will call" becomes
  "I'll try them again later": colleagues only call the customer.
- A reply is cut to MAX_SENTENCES.
"""
import re
from datetime import date

MAX_SENTENCES = 3

SENTENCE_END = re.compile(r"(?<=[.?!।])\s+")

REPLACE = [
    (re.compile(r"\boverdue\b", re.I), "due"),
    (re.compile(r"\bdefault(?:ed|er|s)?\b", re.I), "pending"),
    (re.compile(r"\bdues\b", re.I), "EMI"),
    (re.compile(r"ओवरड्यू"), "ड्यू"),
    (re.compile(r"डिफ़ॉल्ट|डिफॉल्ट"), "पेंडिंग"),
    # simpler words on a phone line
    (re.compile(r"(?<![ऀ-ॿ])देय(?![ऀ-ॣ])"), "due"),
    (re.compile(r"(?<![ऀ-ॿ])(?:भर|भुगतान कर) (पा[ऀ-ॣ]+)"), r"pay कर \1"),
]

DROP = {
    "threat": re.compile(
        r"\b(?:police|court|legal|lawyer|lawsuit|jail|arrest|employer|neighbou?rs?)\b"
        r"|पुलिस|कोर्ट|अदालत|कानूनी|क़ानूनी|वकील|जेल|गिरफ्तार|गिरफ़्तार|पड़ोसी",
        re.I),
    "secret": re.compile(
        r"\b(?:otp|pin|password|cvv|aadhaa?r|card number|bank details|account number)\b"
        r"|ओटीपी|पिन|पासवर्ड|आधार कार्ड|आधार नंबर|सीवीवी",
        re.I),
    "link": re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|in|co|me|ly|link)/", re.I),
}

# Until the customer is confirmed, nothing about the loan may be said at all.
LOAN = re.compile(
    r"\b(?:emi|loan|payment|pay|due|outstanding|rupees?|instal+ments?|kist|amount|money"
    r"|paisa|paise)\b|₹"
    r"|ईएमआई|लोन|किस्त|क़िस्त|भुगतान|पेमेंट|रुपये|रुपए|बकाया|कर्ज़|कर्ज|ड्यू|पैसा|पैसे|पैसों"
    r"|रकम|राशि",
    re.I)
# "रहा/रही", "करूँगा/करूँगी": the AI is a woman, keep the second form
GENDER_SLASH = re.compile(r"([ऀ-ॿ]+)/([ऀ-ॿ]+)")
# Verb forms that can only mean "I" (with हूँ, or the ूँगा future), made female
FEMALE_FIRST_PERSON = [
    (re.compile(r"(रह|[ऀ-ॿ]+त)ा (हूँ|हूं)"), r"\1ी \2"),
    (re.compile(r"([ूऊ][ँं])गा(?![ऀ-ॣ])"), r"\1गी"),
]
# "रहा था" is only "I was" when the sentence says मैं
FEMALE_WITH_MAIN = (re.compile(r"(रह|[ऀ-ॿ]+त)ा था(?![ऀ-ॣ])"), r"\1ी थी")

FALLBACK = {
    "hi": "क्या मैं आपकी किसी और तरह से मदद कर सकती हूँ?",
    "en": "Is there anything else I can help you with?",
}
COLLEAGUE = re.compile(
    r"\b(?:colleague|team member|executive|agent)s?\b|सहयोगी|सहकर्मी|एग्जीक्यूटिव|टीम", re.I)

LOCKED_FALLBACK = {
    "hi": "मैं उनसे बाद में फिर से बात करने की कोशिश करूँगी।",
    "en": "I will try to reach them again later.",
}

# ---- numbers in words ----
EN_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
            "fourteen fifteen sixteen seventeen eighteen nineteen").split()
EN_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
HI_1_99 = (
    "एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह "
    "उन्नीस बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस "
    "बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस "
    "तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरपन "
    "चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ "
    "सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर "
    "अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी "
    "नवासी नब्बे इक्यानबे बानबे तिरानबे चौरानबे पचानबे छियानबे सत्तानबे अट्ठानबे निन्यानबे"
).split()

NUMBER = {w: i for i, w in enumerate(EN_UNITS)}
NUMBER.update({w: 20 + 10 * i for i, w in enumerate(EN_TENS)})
NUMBER.update({w: i + 1 for i, w in enumerate(HI_1_99)})
NUMBER.update({"पांच": 5, "छः": 6, "छे": 6})
# Hindi numbers in Roman letters, as the AI sometimes writes Hinglish
NUMBER.update({
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "chhah": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12,
    "pandrah": 15, "bees": 20, "pachchees": 25, "pachees": 25, "tees": 30, "chalees": 40,
    "pachaas": 50, "pachas": 50,
})
SCALE = {"hundred": 100, "सौ": 100, "sau": 100, "thousand": 1000, "हज़ार": 1000, "हजार": 1000,
         "hazaar": 1000, "hazar": 1000, "lakh": 100000, "lakhs": 100000, "लाख": 100000}
MONTHS = ("january february march april may june july august september october november "
          "december जनवरी फ़रवरी फरवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर सितम्बर "
          "अक्टूबर नवंबर नवम्बर दिसंबर दिसम्बर").split()
MONEY = ("rupees", "rupee", "रुपये", "रुपए", "रुपया")

RUPEE_AMOUNT = re.compile(r"₹\s?(\d{4,})(?![\d,.])")  # ₹2200 -> ₹2,200, like the call note
# "2200 रुपये" or "2,200 rupees" -> "₹2,200"
AMOUNT_THEN_RUPEES = re.compile(
    r"(?<![\d,₹])(\d{1,3}(?:,\d{3})+|\d+)\s?(?:रुपये|रुपए|rupees)", re.I)
EN_MONTHS = ("January February March April May June July August September October "
             "November December").split()
_MONTH_WORD = "|".join(MONTHS)
DATE_WITH_YEAR = re.compile(rf"(\d{{1,2}})\s+({_MONTH_WORD})\s*,?\s+(\d{{4}})", re.I)
NUMERIC_DATE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")


def drop_this_years(text: str, this_year: int | None = None) -> str:
    """"30 October 2026" -> "30 October" and "10-09-2026" -> "10 September", for this
    year and next only, so a birth year is never touched."""
    this_year = this_year or date.today().year
    near = (this_year, this_year + 1)

    def named(m):
        return f"{m.group(1)} {m.group(2)}" if int(m.group(3)) in near else m.group(0)

    def numeric(m):
        day, month, year = (int(g) for g in m.groups())
        if year not in near or not 1 <= month <= 12 or not 1 <= day <= 31:
            return m.group(0)
        return f"{day} {EN_MONTHS[month - 1]}"

    return NUMERIC_DATE.sub(numeric, DATE_WITH_YEAR.sub(named, text))


WORD = re.compile(r"[\wऀ-ॿ]+(?:-[\wऀ-ॿ]+)*")


def _is_number_word(w):
    w = w.lower()
    return w in NUMBER or w in SCALE or (
        "-" in w and all(p in NUMBER for p in w.split("-")))


def _value(words):
    """The number a run of number words stands for, like ["two", "thousand"] -> 2000."""
    total = current = 0
    for w in words:
        w = w.lower()
        if w in SCALE:
            if SCALE[w] == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * SCALE[w]
                current = 0
        else:
            current += sum(NUMBER[p] for p in w.split("-"))
    return total + current


def words_to_digits(text: str) -> str:
    """Turn amounts and dates written as words into digits; leave other words alone."""
    tokens = list(WORD.finditer(text))
    out, last, i = [], 0, 0
    while i < len(tokens):
        if not _is_number_word(tokens[i].group()):
            i += 1
            continue
        run, j = [tokens[i]], i + 1
        while j < len(tokens):
            gap = text[run[-1].end():tokens[j].start()]
            if gap.strip().lower() in ("", ",", "and") and _is_number_word(tokens[j].group()):
                run.append(tokens[j])
                j += 1
            elif (tokens[j].group().lower() == "and" and j + 1 < len(tokens)
                  and _is_number_word(tokens[j + 1].group())):
                j += 1  # "two thousand and fifty"
            else:
                break
        words = [t.group() for t in run]
        after = tokens[j].group().lower() if j < len(tokens) else ""
        has_scale = any(w.lower() in SCALE for w in words)
        # A run that starts with "thousand" may follow a number word we don't know
        # ("dhai thousand"); guessing would change the amount, so leave it alone.
        starts_with_scale = words[0].lower() in SCALE
        if not starts_with_scale and (has_scale or after in MONTHS or after in MONEY):
            n = _value(words)
            out.append(text[last:run[0].start()])
            out.append(f"{n:,}")
            last = run[-1].end()
        i = j
    out.append(text[last:])
    return "".join(out)


def clean_sentence(sentence: str, locked: bool = False) -> tuple[str, str | None]:
    """Returns (text to speak, why it was changed or None). Empty text means drop it.
    locked: the customer is not confirmed yet, so any loan talk is dropped."""
    for why, pattern in DROP.items():
        if pattern.search(sentence):
            return "", why
    if locked and LOAN.search(sentence):
        return "", "loan_before_verified"
    cleaned = GENDER_SLASH.sub(r"\2", sentence)
    for pattern, new in FEMALE_FIRST_PERSON:
        cleaned = pattern.sub(new, cleaned)
    if "मैं" in cleaned:
        cleaned = FEMALE_WITH_MAIN[0].sub(FEMALE_WITH_MAIN[1], cleaned)
    for pattern, new in REPLACE:
        cleaned = pattern.sub(new, cleaned)
    cleaned = words_to_digits(cleaned)
    cleaned = RUPEE_AMOUNT.sub(lambda m: f"₹{int(m.group(1)):,}", cleaned)
    cleaned = AMOUNT_THEN_RUPEES.sub(
        lambda m: f"₹{int(m.group(1).replace(',', '')):,}", cleaned)
    cleaned = drop_this_years(cleaned)
    return cleaned, ("rewritten" if cleaned != sentence else None)


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Complete sentences in buffer, and the unfinished rest."""
    parts = SENTENCE_END.split(buffer)
    return parts[:-1], parts[-1]


class ReplyGuard:
    """Cleans one streamed reply. Feed it text as it arrives; it hands back only whole,
    checked sentences. `events` lists what was changed, for the logs."""

    def __init__(self, language: str, locked: bool = False, third_party: bool = False):
        self.language = language
        self.locked = locked
        self.third_party = third_party
        self.said_try_again = False
        self.buffer = ""
        self.spoken = 0
        self.events = []

    def feed(self, text: str) -> str:
        self.buffer += text
        done, self.buffer = split_sentences(self.buffer)
        return "".join(self._check(s) for s in done)

    def finish(self) -> str:
        """The rest of the reply. If every sentence was dropped, a safe line instead."""
        rest, self.buffer = self.buffer, ""
        out = self._check(rest) if rest.strip() else ""
        if self.spoken == 0 and any(e[0] != "rewritten" for e in self.events):
            self.spoken += 1
            lines = LOCKED_FALLBACK if self.locked else FALLBACK
            out = lines.get(self.language, lines["en"])
        return out

    def _check(self, sentence: str) -> str:
        if self.spoken >= MAX_SENTENCES:
            self.events.append(("too_long", sentence))
            return ""
        if self.third_party and COLLEAGUE.search(sentence):
            self.events.append(("colleague_for_third_party", sentence))
            if self.said_try_again:
                return ""
            self.said_try_again = True
            self.spoken += 1
            return LOCKED_FALLBACK.get(self.language, LOCKED_FALLBACK["en"]) + " "
        cleaned, why = clean_sentence(sentence, self.locked)
        if why:
            self.events.append((why, sentence))
        if not cleaned.strip():
            return ""
        self.spoken += 1
        return cleaned.strip() + " "
