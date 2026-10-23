"""Turn obfuscated page text into text Decider can read (taxonomy classes E, F and J).

The chunker keeps the raw text, because the obfuscation is itself an attack signal. The scanner
asks Decider about ``deobfuscate(text)`` instead:

- Invisible characters (class E): tag characters decoded, zero-width ones dropped, NFKC
  (``reveal_hidden_characters``).
- Look-alike letters (class F): inside a word that mixes Latin letters with Greek or Cyrillic
  ones, and in a one-letter word next to a Latin word, the look-alikes are mapped to Latin.
  Other words written in Greek or Cyrillic are left alone, so ordinary text in those languages
  is untouched.
- Encoded payloads (class J): a base64 run that decodes to readable text is kept and followed by
  its decoded text.
"""

from __future__ import annotations

import base64
import binascii
import re

from phish_guard.questions import reveal_hidden_characters

__all__ = [
    "decode_base64_runs",
    "deobfuscate",
    "latin_skeleton",
]

# Greek and Cyrillic letters that render like a Latin letter, mapped to that letter. A subset of
# Unicode's confusables (UTS #39), limited to unambiguous look-alikes.
_LOOKALIKES = str.maketrans(
    {
        # Greek
        "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H", "\u0399": "I",
        "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P", "\u03a4": "T",
        "\u03a5": "Y", "\u03a7": "X", "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03ba": "k",
        "\u03bd": "v",
        # Cyrillic
        "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
        "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0423": "Y", "\u0425": "X",
        "\u0406": "I", "\u0408": "J", "\u0405": "S", "\u0430": "a", "\u0435": "e", "\u043e": "o",
        "\u0440": "p", "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j",
        "\u0455": "s", "\u0501": "d", "\u04bb": "h",
    }
)  # fmt: skip
_LATIN = re.compile(r"[A-Za-z]")
_GREEK_OR_CYRILLIC = re.compile(r"[\u0370-\u03ff\u0400-\u04ff\u0500-\u052f]")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")


def latin_skeleton(text: str) -> str:
    """Map look-alike letters to Latin inside words that mix Latin with Greek or Cyrillic.

    A one-letter word next to a Latin word is mapped too (a lone Cyrillic "a" in "apply a hidden
    fee"): it can't mix scripts on its own, and a lone look-alike between Latin words isn't a
    word in another language. Text written in Greek or Cyrillic is left alone.
    """
    words = text.split(" ")

    def lone_letter_next_to_latin(i: int) -> bool:
        word = words[i]
        if sum(ch.isalpha() for ch in word) != 1 or not _GREEK_OR_CYRILLIC.search(word):
            return False
        neighbours = words[max(i - 1, 0) : i] + words[i + 1 : i + 2]
        return any(_LATIN.search(n) for n in neighbours)

    def fix(i: int, word: str) -> str:
        mixed = _LATIN.search(word) and _GREEK_OR_CYRILLIC.search(word)
        if mixed or lone_letter_next_to_latin(i):
            return word.translate(_LOOKALIKES)
        return word

    return " ".join(fix(i, word) for i, word in enumerate(words))


def decode_base64_runs(text: str) -> str:
    """Follow each base64 run that decodes to readable text with ``(decoded: ...)``."""

    def readable(run: str) -> str | None:
        try:
            decoded = base64.b64decode(run + "=" * (-len(run) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        printable = sum(c.isprintable() for c in decoded) / max(len(decoded), 1)
        return decoded if printable >= 0.95 and " " in decoded else None

    def expand(match: re.Match[str]) -> str:
        decoded = readable(match.group(0))
        return f"{match.group(0)} (decoded: {decoded})" if decoded else match.group(0)

    return _BASE64_RUN.sub(expand, text)


def deobfuscate(text: str) -> str:
    """The text the scanner asks Decider about."""
    return decode_base64_runs(latin_skeleton(reveal_hidden_characters(text)))
