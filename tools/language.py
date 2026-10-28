"""Follow the caller's language. Pure functions: no AI, no audio.

The prompt alone does not switch languages reliably, so the agent asks this
tracker after every customer turn and changes its instructions and voice.
"""
import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")
SWITCH_AFTER = 2  # replies in a row in the other language


def turn_language(text: str) -> str | None:
    """"hi" if the turn has any Devanagari, "en" if it is only Roman letters,
    None if there is nothing to judge (empty, digits, another script)."""
    if DEVANAGARI.search(text):
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
