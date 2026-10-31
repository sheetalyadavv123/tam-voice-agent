"""Follow the caller's language. Pure functions: no AI, no audio.

The prompt alone does not switch languages reliably, so the agent asks this
tracker after every customer turn and changes its instructions and voice.

The call switches only after SWITCH_AFTER messages in a row that each count: at
least MIN_WORDS words, not made only of filler words, and mostly in the other
language's script. Time and day words ("next Monday", "twelve", "kal subah") are
taken out before counting, so "I think next Monday" in a Hindi call is still Hindi.
"""
import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")
WORDS = re.compile(r"[A-Za-z'ऀ-ॿ]+")
# Hindi written in Roman letters ("WhatsApp pe bhej do"). Only words that are not
# also English, so "main", "do" and "to" are left out.
HINGLISH = re.compile(
    r"\b(?:hai|hain|kya|kyun|kyon|nahi|nahin|haan|ji|bhej|bhejo|karo|kariye|kijiye|dijiye"
    r"|hoon|hun|theek|thik|accha|achha|acha|baje|shaam|subah|hoga|hogi|bata|batao|mera"
    r"|meri|mujhe|aap|aapka|aapki|pe|kal|aaj|bhi|wala|wali|abhi|paisa|paise|dunga|dungi"
    r"|karungi|karunga|kaam|baat|agle|agla|agli|hafte|hafta|kisi|din|ke|ka|ki|ko|se|mein"
    r"|aas|paas|kar|karna|dena|diya|liya|raha|rahi|tha|thi|tak|baad|pehle|parson|aaj)\b",
    re.I)
MIN_WORDS = 3
SWITCH_AFTER = 2  # counting messages in a row in the other language
FILLER = {"okay", "ok", "yes", "no", "haan", "hmm", "bro", "thanks", "thank", "you", "hello"}
# Words about when, in either language; they say nothing about the language spoken
TIME_WORDS = set("""
monday tuesday wednesday thursday friday saturday sunday weekend week month
today tomorrow tonight morning afternoon evening night noon midnight am pm o'clock oclock
next coming last half quarter past
one two three four five six seven eight nine ten eleven twelve twenty thirty
aaj kal parson parso subah shaam sham raat dopahar baje agle agla agli hafte hafta
somvar mangalvar budhvar guruvar shukravar shanivar ravivar itvaar
आज कल परसों सुबह शाम रात दोपहर बजे अगले अगला हफ्ते हफ़्ते
सोमवार मंगलवार बुधवार गुरुवार शुक्रवार शनिवार रविवार इतवार
""".split())


def turn_language(text: str) -> str | None:
    """"hi" if the turn has any Devanagari or Hindi words in Roman letters, "en" if it
    is only English, None if there is nothing to judge (empty, digits, another script)."""
    if DEVANAGARI.search(text) or HINGLISH.search(text):
        return "hi"
    if LATIN.search(text):
        return "en"
    return None


def switch_language(text: str) -> str | None:
    """The language this message counts towards, or None if it doesn't count.
    "en" needs 3+ words (time words left out), mostly Latin script, no Hindi in Roman
    letters; "hi" needs 3+ words, mostly Devanagari or Hindi in Roman letters."""
    words = [w for w in WORDS.findall(text) if w.lower() not in TIME_WORDS]
    if len(words) < MIN_WORDS or all(w.lower() in FILLER for w in words):
        return None
    latin = sum(1 for w in words if LATIN.search(w))
    if HINGLISH.search(" ".join(words)) or latin * 2 < len(words):
        return "hi"
    if latin * 2 > len(words):
        return "en"
    return None


class LanguageTracker:
    def __init__(self, start: str):
        self.current = start
        self.streak = 0  # counting messages in a row in the other language

    def observe(self, text: str) -> str | None:
        """Record one customer turn. Returns the new language when it changes."""
        lang = switch_language(text)
        if lang is None or lang == self.current:
            self.streak = 0  # "in a row" means no other message in between
            return None
        self.streak += 1
        if self.streak < SWITCH_AFTER:
            return None
        self.current, self.streak = lang, 0
        return lang
