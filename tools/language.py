"""Follow the caller's language. Pure functions: no AI, no audio.

The prompt alone does not switch languages reliably, so the agent asks this
tracker after every customer turn and changes its instructions and voice.

Short replies never switch: "Okay" or "Thank you" in a Hindi call is still a Hindi
call. A message switches the call only when it has at least MIN_WORDS words, is not
made only of filler words, and is mostly in the other language's script.
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
FILLER = {"okay", "ok", "yes", "no", "haan", "hmm", "bro", "thanks", "thank", "you", "hello"}


def turn_language(text: str) -> str | None:
    """"hi" if the turn has any Devanagari or Hindi words in Roman letters, "en" if it
    is only English, None if there is nothing to judge (empty, digits, another script)."""
    if DEVANAGARI.search(text) or HINGLISH.search(text):
        return "hi"
    if LATIN.search(text):
        return "en"
    return None


def switch_language(text: str) -> str | None:
    """The language this message is strong enough to switch the call to, or None.
    "en" needs 3+ words, mostly Latin script, no Hindi in Roman letters;
    "hi" needs 3+ words, mostly Devanagari or Hindi in Roman letters."""
    words = WORDS.findall(text)
    if len(words) < MIN_WORDS or all(w.lower() in FILLER for w in words):
        return None
    latin = sum(1 for w in words if LATIN.search(w))
    if HINGLISH.search(text) or latin * 2 < len(words):
        return "hi"
    if latin * 2 > len(words):
        return "en"
    return None


class LanguageTracker:
    def __init__(self, start: str):
        self.current = start

    def observe(self, text: str) -> str | None:
        """Record one customer turn. Returns the new language when it changes."""
        lang = switch_language(text)
        if lang is None or lang == self.current:
            return None
        self.current = lang
        return lang
