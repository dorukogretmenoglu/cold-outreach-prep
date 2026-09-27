"""Parse a date quote into the date it states, so a claimed content_date can be checked by code."""
import re
from datetime import date, timedelta

from .textnorm import normalize

MONTHS = {
    # Turkish
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_NUM = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_DMY_TEXT = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_ALT})\.?,?\s+(\d{{4}})\b")
_MDY_TEXT = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b")
_REL = re.compile(
    r"\b(\d+|bir|an|a)\s+(gün|gun|hafta|ay|yıl|day|days|week|weeks|month|months|year|years)\s+(önce|once|ago)\b"
)
_REL_WORDS = {"dün": 1, "yesterday": 1, "bugün": 0, "today": 0}
_UNIT_DAYS = {
    "gün": 1, "gun": 1, "day": 1, "days": 1,
    "hafta": 7, "week": 7, "weeks": 7,
    "ay": 30, "month": 30, "months": 30,
    "yıl": 365, "year": 365, "years": 365,
}


def _absolute(q: str) -> date | None:
    try:
        if m := _ISO.search(q):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _DMY_TEXT.search(q):
            return date(int(m[3]), MONTHS[m[2]], int(m[1]))
        if m := _MDY_TEXT.search(q):
            return date(int(m[3]), MONTHS[m[1]], int(m[2]))
        if m := _DMY_NUM.search(q):
            return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    return None


def parse_date_quote(quote: str, reference: date) -> tuple[date, int] | None:
    """Return (stated_date, tolerance_days), or None when the quote holds no recognisable date.

    Relative dates ("3 ay önce") are measured from `reference` (the fetch date) and carry a
    tolerance of one unit, because sites round them.
    """
    for q in (normalize(quote), quote.lower()):
        if (d := _absolute(q)) is not None:
            return d, 0
        if m := _REL.search(q):
            n = 1 if m[1] in ("bir", "an", "a") else int(m[1])
            unit = _UNIT_DAYS[m[2]]
            return reference - timedelta(days=n * unit), max(1, unit)
        for word, days in _REL_WORDS.items():
            if re.search(rf"\b{word}\b", q):
                return reference - timedelta(days=days), 1
    return None


def parse_iso(value: str | None) -> date | None:
    m = _ISO.search(value or "")
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
