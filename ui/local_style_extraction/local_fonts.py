from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .fonthelpers import extra_font_fields
from .local_ocr import ocr_words, tesseract_status

from .runtime import RUNTIME_DIR
DEP_PYTHON = RUNTIME_DIR / "python"
if DEP_PYTHON.is_dir() and str(DEP_PYTHON) not in sys.path:
    sys.path.insert(0, str(DEP_PYTHON))

WIN_FONTS = Path(r"C:\Windows\Fonts")
ALLOW = {
    "arial": "Arial",
    "times new roman": "Times New Roman",
    "times": "Times New Roman",
    "calibri": "Calibri",
    "cambria": "Cambria",
    "georgia": "Georgia",
    "verdana": "Verdana",
    "tahoma": "Tahoma",
    "trebuchet ms": "Trebuchet MS",
    "courier new": "Courier New",
    "consolas": "Consolas",
    "segoe ui": "Segoe UI",
    "dejavu sans": "DejaVu Sans",
    "liberation sans": "Liberation Sans",
    "nimbus sans l": "Nimbus Sans L",
    "helvetica": "Helvetica",
    "minion": "Minion",
    "myriad pro": "Myriad Pro",
    "univers": "Univers",
    "avenir": "Avenir",
    "avenirltstd": "AvenirLTStd",
}
MAX_CANDIDATES = 12
MAX_MATCH_WORDS = 8
MIN_LETTERS = 10
MIN_WORD_LEN = 3
NCC_ABSTAIN = 0.42
RENDER_CACHE: dict[tuple[str, str, int], np.ndarray] = {}
_FONT_LIST: list[dict[str, str]] | None = None


def _norm_family(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _allow_family(name: str | None) -> str | None:
    if not name:
        return None
    key = " ".join(str(name).split()).lower()
    if key in ALLOW:
        return ALLOW[key]
    compact = _norm_family(name)
    for raw, canon in ALLOW.items():
        if _norm_family(raw) == compact:
            return canon
    return None


def _name_from_tt(path: Path) -> list[str]:
    try:
        from fontTools.ttLib import TTCollection, TTFont
    except Exception:
        return [path.stem]
    names: list[str] = []
    try:
        if path.suffix.lower() in {".ttc", ".otc"}:
            coll = TTCollection(str(path))
            fonts = list(coll.fonts)
        else:
            fonts = [TTFont(str(path), fontNumber=0, lazy=True)]
        for font in fonts:
            table = font.get("name")
            if table is None:
                continue
            for nid in (16, 1):
                for rec in table.names:
                    if int(rec.nameID) != nid:
                        continue
                    try:
                        val = rec.toUnicode().strip()
                    except Exception:
                        continue
                    if val and val not in names:
                        names.append(val)
            try:
                font.close()
            except Exception:
                pass
    except Exception:
        names.append(path.stem)
    return names or [path.stem]


_STEM_HINTS = (
    "arial",
    "times",
    "calibri",
    "cambria",
    "georgia",
    "verdana",
    "tahoma",
    "trebuc",
    "cour",
    "consola",
    "segoeui",
    "DejaVuSans",
)


def installed_fonts() -> list[dict[str, str]]:
    global _FONT_LIST
    if _FONT_LIST is not None:
        return _FONT_LIST
    found: dict[str, dict[str, str]] = {}
    if WIN_FONTS.is_dir():
        preferred = [WIN_FONTS / name for name in ("arial.ttf", "times.ttf", "calibri.ttf", "cambria.ttf", "georgia.ttf", "verdana.ttf", "tahoma.ttf", "cour.ttf", "consola.ttf", "segoeui.ttf", "trebuc.ttf")]
        paths = [p for p in preferred if p.is_file()]
        for path in WIN_FONTS.iterdir():
            if path.suffix.lower() not in {".ttf", ".otf", ".ttc", ".otc"}:
                continue
            stem = path.stem.lower()
            if any(h.lower() in stem for h in _STEM_HINTS) and path not in paths:
                paths.append(path)
        for path in paths:
            for raw in _name_from_tt(path):
                canon = _allow_family(raw)
                if not canon or canon in found:
                    continue
                found[canon] = {"family": canon, "path": str(path), "raw": raw}
                if len(found) >= MAX_CANDIDATES:
                    break
            if len(found) >= MAX_CANDIDATES:
                break
    _FONT_LIST = [found[k] for k in sorted(found)]
    return _FONT_LIST


def candidate_fonts() -> list[dict[str, str]]:
    prefer = ["Arial", "Times New Roman", "Calibri", "Cambria", "Georgia", "Verdana", "Tahoma", "Segoe UI", "Consolas", "Courier New"]
    have = {item["family"]: item for item in installed_fonts()}
    out = [have[name] for name in prefer if name in have]
    for item in have.values():
        if item not in out:
            out.append(item)
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _render_word(font_path: str, text: str, px: int) -> np.ndarray | None:
    px = max(10, min(int(px), 96))
    key = (font_path, text, px)
    cached = RENDER_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        font = ImageFont.truetype(font_path, px)
    except OSError:
        return None
    bbox = font.getbbox(text)
    if not bbox:
        return None
    w = max(1, bbox[2] - bbox[0] + 4)
    h = max(1, bbox[3] - bbox[1] + 4)
    image = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(image)
    draw.text((2 - bbox[0], 2 - bbox[1]), text, font=font, fill=0)
    arr = np.asarray(image, dtype=np.uint8)
    RENDER_CACHE[key] = arr
    if len(RENDER_CACHE) > 256:
        RENDER_CACHE.clear()
        RENDER_CACHE[key] = arr
    return arr


def _binarize(gray: np.ndarray) -> np.ndarray:
    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)
    if float(np.mean(gray)) < 127:
        gray = 255 - gray
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def _ncc(crop: np.ndarray, templ: np.ndarray) -> float:
    if crop.size == 0 or templ.size == 0:
        return 0.0
    th, tw = templ.shape[:2]
    ch, cw = crop.shape[:2]
    if th < 4 or tw < 4 or ch < 4 or cw < 4:
        return 0.0
    scale = ch / float(th)
    nw = max(4, int(round(tw * scale)))
    nh = max(4, ch)
    resized = cv2.resize(templ, (nw, nh), interpolation=cv2.INTER_AREA)
    img, needle = crop, resized
    if needle.shape[0] > img.shape[0] or needle.shape[1] > img.shape[1]:
        img, needle = resized, crop
    if needle.shape[0] > img.shape[0] or needle.shape[1] > img.shape[1]:
        return 0.0
    try:
        res = cv2.matchTemplate(img, needle, cv2.TM_CCOEFF_NORMED)
        return float(np.max(res)) if res.size else 0.0
    except cv2.error:
        return 0.0


def _usable_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = []
    for word in words:
        text = "".join(ch for ch in str(word.get("text") or "") if ch.isalnum() or ch in "-")
        if len(text) < MIN_WORD_LEN:
            continue
        usable.append({**word, "match_text": text})
        if len(usable) >= MAX_MATCH_WORDS:
            break
    return usable


def estimate_font(image_path: str | Path, rgb=None) -> dict[str, Any]:
    ocr = ocr_words(image_path, rgb=rgb)
    if not ocr.get("ok"):
        return {
            "status": "unknown",
            "value": None,
            "candidates": [],
            "method": "none",
            "reason": str(ocr.get("reason") or "local OCR unavailable"),
            "warnings": [str(ocr.get("reason") or "local OCR unavailable")],
            "ocr": ocr,
        }
    usable = _usable_words(list(ocr.get("words") or []))
    letters = sum(len(str(w.get("match_text") or "")) for w in usable)
    if letters < MIN_LETTERS:
        reason = f"Insufficient glyphs for font matching (letters={letters}, min={MIN_LETTERS}); OCR is not font identity"
        return {
            "status": "unknown",
            "value": None,
            "candidates": [],
            "method": "local_ocr_match",
            "reason": reason,
            "warnings": [reason],
            "ocr": ocr,
        }
    with Image.open(ocr.get("image") or image_path) as im:
        gray = np.asarray(ImageOps.exif_transpose(im).convert("L"), dtype=np.uint8)
    fonts = candidate_fonts()
    scores: dict[str, list[float]] = {item["family"]: [] for item in fonts}
    for word in usable:
        left, top, width, height = (int(v) for v in word["bbox"])
        pad = max(1, int(round(0.08 * height)))
        x0 = max(0, left - pad)
        y0 = max(0, top - pad)
        x1 = min(gray.shape[1], left + width + pad)
        y1 = min(gray.shape[0], top + height + pad)
        crop = _binarize(gray[y0:y1, x0:x1])
        text = str(word["match_text"])
        px = max(12, min(64, int(height)))
        for item in fonts:
            rendered = _render_word(item["path"], text, px)
            if rendered is None:
                continue
            score = _ncc(crop, _binarize(rendered))
            scores[item["family"]].append(max(0.0, score))
    ranked = []
    for family, vals in scores.items():
        if not vals:
            continue
        ranked.append({"family": family, "score": round(float(sum(vals) / len(vals)), 4), "method": "glyph_ncc"})
    ranked.sort(key=lambda row: row["score"], reverse=True)
    top = ranked[:5]
    if not top or float(top[0]["score"]) < NCC_ABSTAIN:
        reason = "OCR text matched installed glyphs with insufficient similarity; estimated font abstained"
        return {
            "status": "unknown",
            "value": None,
            "candidates": top,
            "method": "local_ocr_match",
            "reason": reason,
            "warnings": [reason],
            "ocr": ocr,
        }
    best = top[0]
    return {
        "status": "estimated",
        "value": best["family"],
        "candidates": top,
        "method": "local_ocr_match",
        "reason": "Local OCR boxes matched to installed glyph renders; score is similarity, not probability",
        "warnings": [],
        "ocr": ocr,
    }


def apply_font_token(tokens: dict[str, dict[str, Any]], native: dict[str, Any] | None, source_class: str, crop_png: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    tok = tokens["font_family"]
    warnings: list[str] = []
    raw = None if not native else native.get("raw_font_name")
    extra_font_fields(tok, raw_font_name=raw)
    if source_class == "photo":
        if tok.get("status") != "unknown":
            pass
        tok.pop("candidates", None)
        return tokens, warnings
    if tok.get("status") == "detected" and tok.get("value"):
        return tokens, warnings
    est = estimate_font(crop_png)
    warnings.extend(est.get("warnings") or [])
    cands = list(est.get("candidates") or [])
    if cands:
        extra_font_fields(tok, candidates=cands)
    if est.get("status") == "estimated" and est.get("value"):
        tok["value"] = est["value"]
        tok["status"] = "estimated"
        tok["method"] = "local_ocr_match"
        tok["reason"] = est["reason"]
        tok["original_value"] = est["value"]
        extra_font_fields(tok, candidates=cands)
    elif tok.get("status") == "unknown" and est.get("reason"):
        tok["reason"] = est["reason"]
    return tokens, warnings


def tesseract_ready() -> dict[str, Any]:
    return tesseract_status()
