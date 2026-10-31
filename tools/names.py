"""Does a word the caller said match the customer's name? Pure functions: no AI, no audio.

Speech-to-text writes the same name many ways: "Sunita", "सुनीता", "सुनिता", "सनीता".
Every word is turned into a rough Roman spelling (Devanagari is transliterated), then
long vowels, doubled letters and "h" after consonants are folded together, so all of
these give the same key or keys one letter apart. Names of 4+ letters may differ by one
letter, 8+ letters by two; shorter names must match exactly.
"""
import re

VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu", "ऋ": "ri",
          "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
MATRAS = {"ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu", "ृ": "ri", "े": "e",
          "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e"}
CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j",
    "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t",
    "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
    "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s",
    "ह": "h", "क़": "k", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}
HALANT, NUKTA = "्", "़"
NASAL = {"ं": "n", "ँ": "n", "ः": "h"}
WORD = re.compile(r"[A-Za-z]+|[ऀ-ॿ]+")
# Folded in this order, so "chh" and "sh" go before the single letters
FOLD = [("chh", "c"), ("ch", "c"), ("sh", "s"), ("ph", "f"), ("kh", "k"), ("gh", "g"),
        ("th", "t"), ("dh", "d"), ("bh", "b"), ("jh", "j"), ("ck", "k"), ("q", "k"),
        ("w", "v"), ("z", "j"), ("ee", "i"), ("oo", "u"), ("ii", "i"), ("uu", "u")]


def transliterate(word: str) -> str:
    """A rough Roman spelling of a Devanagari word: "सुनीता" -> "suniitaa"."""
    out, i = [], 0
    while i < len(word):
        ch = word[i]
        if i + 1 < len(word) and word[i + 1] == NUKTA and ch + NUKTA in CONSONANTS:
            ch, i = ch + NUKTA, i + 1
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if ch in CONSONANTS:
            out.append(CONSONANTS[ch])
            # the inherent "a", except before a vowel sign, a halant or at the end
            if nxt and nxt not in MATRAS and nxt != HALANT and nxt not in NASAL:
                out.append("a")
            elif nxt in NASAL:
                out.append("a")
        elif ch in MATRAS:
            out.append(MATRAS[ch])
        elif ch in VOWELS:
            out.append(VOWELS[ch])
        elif ch in NASAL:
            out.append(NASAL[ch])
        i += 1
    return "".join(out)


def key(word: str) -> str:
    """The folded spelling used to compare names: "Sunita", "सुनीता", "सुनिता" -> "sunita"."""
    w = transliterate(word) if re.search(r"[ऀ-ॿ]", word) else word
    w = re.sub(r"[^a-z]", "", w.lower())
    for old, new in FOLD:
        w = w.replace(old, new)
    w = re.sub(r"(.)\1+", r"\1", w)  # "aa" -> "a", "mm" -> "m"
    return w


def distance(a: str, b: str) -> int:
    """Edit distance (insert, delete or change one letter)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def allowed(name_key: str) -> int:
    return 2 if len(name_key) >= 8 else 1 if len(name_key) >= 4 else 0


def same_name(word: str, name: str) -> bool:
    a, b = key(word), key(name)
    return bool(a and b) and distance(a, b) <= allowed(b)


def mentions(text: str, name: str) -> bool:
    """Does the text contain the name, spelled any of the ways above?"""
    return any(same_name(w, name) for w in WORD.findall(text or ""))
