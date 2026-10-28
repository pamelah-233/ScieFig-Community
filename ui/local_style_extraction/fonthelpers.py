from __future__ import annotations

import io
import re
from typing import Any, Iterable

from .demo_common import strip_font_name

_VENDOR = {
    "adobe",
    "microsoft",
    "apple",
    "linotype",
    "linotypehell",
    "monotype",
    "urw",
    "google",
    "ibm",
    "hell",
    "gmbh",
    "inc",
    "incorporated",
    "systems",
    "corporation",
    "ag",
    "ltd",
    "copyright",
}
_TM_RE = re.compile(r"([A-Z][A-Za-z0-9]+(?:[- ][A-Z][A-Za-z0-9]+)*)\s+is a trademark", re.I)
_T1_FAMILY_RE = re.compile(rb"/FamilyName\s+\(([^)]+)\)")
_T1_FULL_RE = re.compile(rb"/FullName\s+\(([^)]+)\)")
_ANON_ADV = re.compile(r"(?:^adv|[a-z]{0,8}adv(?:ot|ps|p)?)", re.I)
_HEXISH = re.compile(r"[0-9a-f]{5,}", re.I)


def is_anonymous_font_id(name: str | None) -> bool:
    text = str(name or "").strip()
    if not text:
        return True
    compact = text.replace(" ", "").replace("-", "").replace("_", "")
    if len(compact) <= 3:
        return True
    low = compact.lower()
    if low.startswith("adv"):
        return True
    if "advot" in low or "advps" in low:
        return True
    if _ANON_ADV.search(compact) and _HEXISH.search(compact):
        return True
    return False


def looks_like_family(name: str | None) -> bool:
    text = strip_font_name(name) or str(name or "").strip()
    if not text:
        return False
    if is_anonymous_font_id(text):
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    return letters >= 4


def readable_family(name: str | None) -> str | None:
    stripped = strip_font_name(name)
    if not stripped:
        return None
    if looks_like_family(stripped):
        return stripped
    return None


def family_from_notice(text: str | None) -> str | None:
    if not text:
        return None
    for match in _TM_RE.finditer(str(text)):
        cand = match.group(1).strip()
        key = cand.replace(" ", "").replace("-", "").lower()
        if key in _VENDOR:
            continue
        if looks_like_family(cand):
            return strip_font_name(cand) or cand
    return None


def _tt_family(buf: bytes) -> str | None:
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        return None
    try:
        font = TTFont(io.BytesIO(buf))
    except Exception:
        return None
    try:
        table = font.get("name")
        if table is None:
            return None
        by_id: dict[int, str] = {}
        for rec in table.names:
            try:
                val = rec.toUnicode().strip()
            except Exception:
                continue
            if val:
                by_id.setdefault(int(rec.nameID), val)
        for nid in (16, 1, 4):
            fam = readable_family(by_id.get(nid))
            if fam:
                return fam
        return None
    finally:
        try:
            font.close()
        except Exception:
            pass


def _cff_family(buf: bytes) -> str | None:
    try:
        from fontTools.cffLib import CFFFontSet
    except Exception:
        return None
    try:
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(buf), None)
    except Exception:
        return None
    names = list(getattr(cff, "fontNames", []) or [])
    for name in names:
        fam = readable_family(name)
        if fam:
            return fam
        try:
            font = cff[name]
        except Exception:
            continue
        raw = getattr(font, "rawDict", {}) or {}
        for key in ("FamilyName", "FullName", "BaseFontName"):
            val = raw.get(key) or getattr(font, key, None)
            fam = readable_family(str(val) if val not in (None, "None") else None)
            if fam:
                return fam
        notice = raw.get("Notice") or getattr(font, "Notice", None)
        fam = family_from_notice(str(notice) if notice not in (None, "None") else None)
        if fam:
            return fam
    return None


def parse_embedded_family(buf: bytes | None, basefont: str | None = None) -> str | None:
    if not buf:
        return readable_family(basefont)
    for match in _T1_FAMILY_RE.findall(buf)[:2]:
        fam = readable_family(match.decode("latin-1", "replace"))
        if fam:
            return fam
    for match in _T1_FULL_RE.findall(buf)[:2]:
        fam = readable_family(match.decode("latin-1", "replace"))
        if fam:
            return fam
    fam = _tt_family(buf)
    if fam:
        return fam
    fam = _cff_family(buf)
    if fam:
        return fam
    ascii_bits = []
    cur = []
    for b in buf:
        if 32 <= b < 127:
            cur.append(chr(b))
        else:
            if len(cur) >= 12:
                ascii_bits.append("".join(cur))
            cur = []
    if len(cur) >= 12:
        ascii_bits.append("".join(cur))
    for chunk in ascii_bits[:8]:
        fam = family_from_notice(chunk)
        if fam:
            return fam
    return readable_family(basefont)


def _keys_for(basefont: str | None, name: str | None) -> list[str]:
    out: list[str] = []
    for item in (basefont, name, strip_font_name(basefont), strip_font_name(name)):
        if item and item not in out:
            out.append(item)
    return out


def font_item_needed(basefont: str | None, name: str | None, needed: Iterable[str | None]) -> bool:
    keys = {k for k in _keys_for(basefont, name) if k}
    want = {str(n) for n in needed if n}
    want |= {strip_font_name(n) or "" for n in want}
    want.discard("")
    if not want:
        return False
    if keys & want:
        return True
    for item in keys:
        for other in want:
            if item.endswith(other) or other.endswith(item):
                return True
    return False


def build_font_lookup(records: list[tuple[str | None, str | None, bytes]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for basefont, name, buf in records:
        family = readable_family(basefont) or readable_family(name)
        if family is None and buf:
            family = parse_embedded_family(buf, basefont)
        if family is None:
            continue
        for key in _keys_for(basefont, name):
            lookup[key] = family
    return lookup


def resolve_span_family(raw: str | None, lookup: dict[str, str]) -> str | None:
    if raw and raw in lookup:
        return lookup[raw]
    stripped = strip_font_name(raw)
    if stripped and stripped in lookup:
        return lookup[stripped]
    return readable_family(raw)


def extra_font_fields(token: dict[str, Any], **fields: Any) -> dict[str, Any]:
    for key, value in fields.items():
        if value is not None:
            token[key] = value
    return token
