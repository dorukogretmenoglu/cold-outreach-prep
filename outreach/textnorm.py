import re
import unicodedata

_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿­]")
_WS = re.compile(r"\s+")
_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
})


def turkish_casefold(text: str) -> str:
    # str.casefold() maps "I" to "i" and "İ" to "i̇", which breaks Turkish matching.
    return text.replace("I", "ı").replace("İ", "i").lower()


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text).translate(_TRANSLATE)
    return _WS.sub(" ", turkish_casefold(text)).strip()


def contains_quote(haystack: str, quote: str) -> bool:
    return normalize(quote) in normalize(haystack)


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> set[str]:
    """Numbers normalised so that 1.500, 1,500 and 1500 compare equal."""
    return {re.sub(r"[.,]", "", n) for n in _NUMBER.findall(text)}


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", normalize(text))
