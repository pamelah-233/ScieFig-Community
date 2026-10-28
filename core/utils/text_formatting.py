from __future__ import annotations

import re


_SUPERSCRIPT_DIGITS = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
_SUBSCRIPT_DIGITS = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def superscript_digits(text: str) -> str:
    return str(text).translate(_SUPERSCRIPT_DIGITS)


def subscript_digits(text: str) -> str:
    return str(text).translate(_SUBSCRIPT_DIGITS)


def display_unit(unit: str) -> str:
    normalized = str(unit or "").strip()
    return re.sub(
        r"\^([0-9+\-=()]+)",
        lambda match: superscript_digits(match.group(1)),
        normalized,
    )
