"""Hebrew text normalization for spoken TTS output.

Azure's Hebrew TTS handles raw digits inconsistently: it sometimes falls back
to English (e.g. "06:00" spoken as "sikes") and sometimes converts digits
correctly but unnaturally (e.g. "23:45" spoken as "esrim ve'shalosh
arba'im ve'chamesh" instead of the idiomatic "quarter to midnight"). Every
numeric field sent to the voice agent is therefore pre-converted to natural
spoken Hebrew words here before being handed to Vapi.
"""

from __future__ import annotations

import re

# Feminine/counting hour names, used both for clock times and as the base
# "standalone" digit words inside normalize_number (matching the spec's
# normalize_number(6) == "שש" example).
_HOUR_NAMES: dict[int, str] = {
    1: "אחת",
    2: "שתיים",
    3: "שלוש",
    4: "ארבע",
    5: "חמש",
    6: "שש",
    7: "שבע",
    8: "שמונה",
    9: "תשע",
    10: "עשר",
    11: "אחת עשרה",
    12: "שתים עשרה",
}

_TENS: dict[int, str] = {
    1: "עשר",
    2: "עשרים",
    3: "שלושים",
    4: "ארבעים",
    5: "חמישים",
    6: "שישים",
    7: "שבעים",
    8: "שמונים",
    9: "תשעים",
}

# Construct ("smichut") forms used immediately before "אלפים" (thousands).
_THOUSAND_CONSTRUCT: dict[int, str] = {
    3: "שלושת",
    4: "ארבעת",
    5: "חמשת",
    6: "ששת",
    7: "שבעת",
    8: "שמונת",
    9: "תשעת",
}

_TIME_RE = re.compile(r"^([0-9]{1,2}):([0-9]{1,2})$")


def _part_of_day(hour: int) -> str:
    if 0 <= hour <= 4:
        return "בלילה"
    if 5 <= hour <= 11:
        return "בבוקר"
    if 12 <= hour <= 16:
        return "בצהריים"
    if 17 <= hour <= 20:
        return "בערב"
    return "בלילה"  # 21-23


def _hour_name(hour_24: int) -> str:
    h12 = hour_24 % 12 or 12
    return _HOUR_NAMES[h12]


def _cardinal(n: int) -> str:
    """Spell out an integer 0-59 (used for arbitrary clock minutes and as a
    building block for normalize_number's units/teens/tens groups)."""
    if n == 0:
        return "אפס"
    if n <= 10:
        return _HOUR_NAMES[n]
    if n < 20:
        return f"{_HOUR_NAMES[n - 10]} עשרה"
    tens, units = divmod(n, 10)
    if units == 0:
        return _TENS[tens]
    return f"{_TENS[tens]} ו{_HOUR_NAMES[units]}"


def normalize_time(time_str: str) -> str:
    """Convert a 24-hour "HH:MM" string into natural spoken Hebrew.

    Raises ValueError on empty/malformed input or out-of-range components.
    """
    if not time_str:
        raise ValueError(f"invalid time string: {time_str!r}")

    match = _TIME_RE.match(time_str.strip())
    if not match:
        raise ValueError(f"invalid time string: {time_str!r}")

    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ValueError(f"time out of range: {time_str!r}")

    part = _part_of_day(hour)
    hour_name = _hour_name(hour)

    if minute == 0:
        return f"{hour_name} {part}"
    if minute == 15:
        return f"{hour_name} ורבע {part}"
    if minute == 30:
        return f"{hour_name} וחצי {part}"
    if minute == 45:
        next_hour = (hour + 1) % 24
        if next_hour == 0:
            # Idiomatic form: "quarter to midnight", not "quarter to twelve".
            return "רבע לחצות"
        next_hour_name = _hour_name(next_hour)
        next_part = _part_of_day(next_hour)
        return f"רבע ל{next_hour_name} {next_part}"

    return f"{hour_name} ו{_cardinal(minute)} {part}"


def normalize_number(n: int) -> str:
    """Convert an integer (0-999,999) into spoken Hebrew words.

    Used for delay hours and currency amounts. Raises ValueError for
    non-integers or values outside the supported range.
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError(f"expected an int, got {n!r}")
    if not (0 <= n <= 999_999):
        raise ValueError(f"number out of supported range: {n!r}")

    if n == 0:
        return "אפס"

    tokens: list[str] = []

    thousands, remainder = divmod(n, 1000)
    if thousands == 1:
        tokens.append("אלף")
    elif thousands == 2:
        tokens.append("אלפיים")
    elif 3 <= thousands <= 9:
        tokens.append(f"{_THOUSAND_CONSTRUCT[thousands]} אלפים")
    elif thousands >= 10:
        tokens.append(f"{normalize_number(thousands)} אלף")

    hundreds, remainder = divmod(remainder, 100)
    if hundreds == 1:
        tokens.append("מאה")
    elif hundreds == 2:
        tokens.append("מאתיים")
    elif hundreds >= 3:
        tokens.append(f"{_HOUR_NAMES[hundreds]} מאות")

    # Tens/units are appended as separate atomic tokens (rather than via
    # _cardinal, which bakes its own internal "ו" for standalone use) so the
    # "single ו before the final term" rule below isn't applied twice.
    tens, units = divmod(remainder, 10)
    if 1 <= remainder <= 9:
        tokens.append(_HOUR_NAMES[remainder])
    elif 10 <= remainder <= 19:
        tokens.append("עשר" if units == 0 else f"{_HOUR_NAMES[units]} עשרה")
    elif remainder >= 20:
        tokens.append(_TENS[tens])
        if units:
            tokens.append(_HOUR_NAMES[units])

    if len(tokens) >= 2:
        tokens[-1] = f"ו{tokens[-1]}"

    return " ".join(tokens)
