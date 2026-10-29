"""Follow the caller's language. Pure functions: no AI, no audio.

The prompt alone does not switch languages reliably, so the agent asks this
tracker after every customer turn and changes its instructions and voice.
"""
import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")
# Hindi written in Roman letters ("WhatsApp pe bhej do"). Only words that are not
# also English, so "main", "do" and "to" are left out.
HINGLISH = re.compile(
    r"\b(?:hai|hain|kya|kyun|kyon|nahi|nahin|haan|ji|bhej|bhejo|karo|kariye|kijiye|dijiye"
    r"|hoon|hun|theek|thik|accha|achha|acha|baje|shaam|subah|hoga|hogi|bata|batao|mera"
    r"|meri|mujhe|aap|aapka|aapki|pe|kal|aaj|bhi|wala|wali|abhi|paisa|paise|dunga|dungi"
    r"|karungi|karunga|kaam|baat)\b",
    re.I)
SWITCH_AFTER = 2  # replies in a row in the other language


def turn_language(text: str) -> str | None:
    """"hi" if the turn has any Devanagari or Hindi words in Roman letters, "en" if it
    is only English, None if there is nothing to judge (empty, digits, another script)."""
    if DEVANAGARI.search(text) or HINGLISH.search(text):
        return "hi"
    if LATIN.search(text):
        return "en"
    return None


class LanguageTracker:
    def __init__(self, start: str):
        self.current = start
        self.streak = 0  # replies in a row in the other language

    def observe(self, text: str) -> str | None:
        """Record one customer turn. Returns the new language when it changes."""
        lang = turn_language(text)
        if lang is None:
            return None
        if lang == self.current:
            self.streak = 0
            return None
        self.streak += 1
        if self.streak < SWITCH_AFTER:
            return None
        self.current, self.streak = lang, 0
        return lang
