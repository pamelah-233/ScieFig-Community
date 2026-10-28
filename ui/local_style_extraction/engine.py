from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .demo_common import (
    DEFAULT_NAME,
    MIN_KEYS,
    STYLE_KEYS,
    EXTRA_KEYS,
    NEUTRAL_RESOLVED,
    SCHEMA,
    TOKEN_UNITS,
    abs_path,
    clamp_rect,
    clip_from_norm,
    ensure_rect,
    jsonable,
    make_token,
    source_kind,
    unique_run_dir,
    unknown_token,
)
from .pdf_native import extract_pdf_signals, open_pdf, page_info, render_pdf_page, suggest_pdf_regions
from .raster_style import analyze_raster, crop_rgb, image_size, load_rgb, render_image, save_png


def inspect_source(path: str) -> dict:
    source = abs_path(path)
    if not Path(source).is_file():
        raise FileNotFoundError(f"Source not found: {source}")
    kind = source_kind(source)
    if kind == "pdf":
        doc = open_pdf(source)
        try:
            pages = [page_info(doc[i], i) for i in range(doc.page_count)]
            return {"path": source, "kind": "pdf", "page_count": int(doc.page_count), "pages": pages}
        finally:
            doc.close()
    width, height = image_size(source)
    return {
        "path": source,
        "kind": "image",
        "page_count": 1,
        "pages": [{"index": 0, "width": float(width), "height": float(height), "unit": "px"}],
    }


def render_page(path: str, page_index: int, output_path: str, max_side: int = 1600) -> dict:
    source = abs_path(path)
    if not Path(source).is_file():
        raise FileNotFoundError(f"Source not found: {source}")
    if max_side < 16:
        raise ValueError("max_side is too small")
    dest = abs_path(output_path)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    info = inspect_source(source)
    if page_index < 0 or page_index >= int(info["page_count"]):
        raise ValueError("Invalid page_index")
    if info["kind"] == "pdf":
        doc = open_pdf(source)
        try:
            width, height = render_pdf_page(doc[page_index], dest, max_side=max_side)
        finally:
            doc.close()
    else:
        if page_index != 0:
            raise ValueError("Invalid page_index")
        width, height = render_image(source, dest, max_side=max_side)
    return {"path": dest, "width": int(width), "height": int(height), "page_index": int(page_index)}


def suggest_regions(path: str, page_index: int) -> list[dict]:
    source = abs_path(path)
    info = inspect_source(source)
    if page_index < 0 or page_index >= int(info["page_count"]):
        raise ValueError("Invalid page_index")
    if info["kind"] == "pdf":
        doc = open_pdf(source)
        try:
            return suggest_pdf_regions(doc[page_index])
        finally:
            doc.close()
    rgb = load_rgb(source)
    signals = analyze_raster(rgb)
    if signals["text_like"] or signals["photo_like"]:
        return []
    if signals["plot_like"]:
        return [{"id": "r0", "rect": [0.04, 0.04, 0.92, 0.92], "label": "raster-plot", "reason": "Axis-like raster geometry"}]
    return [{"id": "r0", "rect": [0.0, 0.0, 1.0, 1.0], "label": "full-image", "reason": "No native PDF regions; full image crop"}]


def _blank_tokens() -> dict[str, dict[str, Any]]:
    tokens = {}
    reasons = {
        "series_colors": "No series palette in the selected crop",
        "background_color": "No background color in the selected crop",
        "label_color": "No label color in the selected crop",
        "font_family": "No reliable native font information",
        "font_size_pt": "No native font size in the selected crop",
        "axis_label_size_pt": "No axis label size in the selected crop",
        "tick_label_size_pt": "No tick label size in the selected crop",
        "legend_size_pt": "No legend size in the selected crop",
        "line_width": "No series line width in the selected crop",
        "axis_line_width": "No axis line width in the selected crop",
        "point_size": "No identifiable markers in the selected crop",
        "point_shape": "Marker shape not identifiable",
        "show_top_spine": "Top spine not observed",
        "show_right_spine": "Right spine not observed",
        "gridline_visible": "Grid presence not observed",
        "gridline_color": "Grid color not observed",
    }
    for key in STYLE_KEYS:
        tokens[key] = unknown_token(key, reasons.get(key, "No reliable native geometry for this property"))
    return tokens


def _status_for(method: str, key: str) -> str:
    if method in {"pdf_native", "pdf_rendered"}:
        return "detected"
    if method in {"raster_estimate", "local_ocr_match", "pdf_geometry"}:
        return "estimated"
    return "unknown"


def _apply_signal(tokens: dict[str, dict[str, Any]], key: str, signals: dict[str, Any]) -> None:
    value = signals.get(key)
    if value is None:
        return
    method = signals.get("methods", {}).get(key)
    if not method:
        return
    evidence = list(signals.get("evidence", {}).get(key) or [])
    reason = ""
    if method == "raster_estimate" and key in {
        "font_size_pt",
        "axis_label_size_pt",
        "tick_label_size_pt",
        "legend_size_pt",
    }:
        reason = "Raster glyph-like box ratios; resolved against 10 pt reference. Not native PDF points."
    elif method == "raster_estimate" and key in {"line_width", "axis_line_width", "point_size"}:
        reason = "Raster stroke is crop-relative; resolved with a 100 pt width reference. Not assumed DPI."
    elif key == "series_colors":
        reason = str(signals.get("series_order_reason") or "")
    elif method == "pdf_native":
        reason = "Native PDF primitives inside the selected crop"
    elif method == "pdf_geometry":
        reason = "参数来自 PDF 几何关系推定；请与所选图区核对。"
    elif method == "pdf_rendered":
        reason = "已按 PDF 页面实际显示颜色校准；保留矢量位置证据。"
    tokens[key] = make_token(value, TOKEN_UNITS[key], _status_for(method, key), method, evidence, reason)


def _classify(native: dict[str, Any] | None, raster: dict[str, Any] | None) -> str:
    n_plot = bool(native and native.get("plot_like"))
    n_text = bool(native and native.get("text_like"))
    n_photo = bool(native and native.get("photo_like"))
    r_plot = bool(raster and raster.get("plot_like"))
    r_text = bool(raster and raster.get("text_like"))
    r_photo = bool(raster and raster.get("photo_like"))
    if n_plot and (n_photo or r_photo):
        return "mixed"
    if n_plot or r_plot:
        return "plot"
    if n_photo or r_photo:
        return "photo"
    if n_text or r_text:
        return "text"
    return "unknown"


def _restricted(source_class: str) -> bool:
    return source_class in {"photo", "text"}


CHART_KEYS = (
    "series_colors",
    "font_family",
    "font_size_pt",
    "axis_label_size_pt",
    "tick_label_size_pt",
    "legend_size_pt",
    "line_width",
    "axis_line_width",
    "point_size",
    "point_shape",
    "show_top_spine",
    "show_right_spine",
    "gridline_visible",
    "gridline_color",
)

CHART_GEOMETRY_KEYS = (
    "series_colors",
    "line_width",
    "axis_line_width",
    "point_size",
    "point_shape",
    "show_top_spine",
    "show_right_spine",
    "gridline_visible",
    "gridline_color",
)


def _build_tokens(native: dict[str, Any] | None, raster: dict[str, Any] | None, source_class: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    tokens = _blank_tokens()
    warnings: list[str] = []
    if native:
        warnings.extend(native.get("warnings") or [])
        for key in STYLE_KEYS:
            _apply_signal(tokens, key, native)
    if raster:
        for key in STYLE_KEYS:
            current = tokens[key]
            if current["status"] in {"detected"}:
                continue
            if native and native.get("native_text") and key == "legend_size_pt":
                continue  # Font size alone does not establish the presence of a legend.
            if current["status"] == "estimated" and current.get("method") == "pdf_geometry":
                continue
            if native and key == "point_shape" and native.get("point_size") is not None and native.get("point_shape") is None:
                continue
            if _restricted(source_class) and key in CHART_KEYS:
                continue
            if source_class == "mixed" and key == "series_colors" and raster.get("photo_like"):
                continue
            if (
                key == "line_width"
                and native
                and native.get("native_vectors")
                and native.get("line_width") is None
            ):
                continue
            if current["status"] == "unknown":
                _apply_signal(tokens, key, raster)
        raster_keys = {key for key, token in tokens.items() if token.get('method') == 'raster_estimate'}
        for warning in raster.get('warnings') or []:
            low = warning.lower()
            if ('typography' in low or 'font identity' in low) and not raster_keys.intersection({'font_family','font_size_pt','axis_label_size_pt','tick_label_size_pt'}):
                continue
            if ('legend patches' in low or 'series ordered' in low) and 'series_colors' not in raster_keys:
                continue
            if ('width' in low or 'axis/line' in low) and not raster_keys.intersection({'line_width','axis_line_width'}):
                continue
            warnings.append(warning)
    if native and native.get("native_vectors") and native.get("line_width") is None and tokens["line_width"]["status"] == "unknown":
        warnings.append("No native series-line evidence; line width not inferred from bar/text/axis morphology")
    if source_class == "photo":
        for key in CHART_KEYS + EXTRA_KEYS:
            tokens[key] = unknown_token(key, "Text-only/photo crop cannot emit a full chart style", "none")
        warnings.append("Photo/micrograph crop: axes, fonts, and series palette left unknown")
    elif source_class == "text":
        for key in CHART_GEOMETRY_KEYS + EXTRA_KEYS:
            tokens[key] = unknown_token(key, "Text-only/photo crop cannot emit a full chart style", "none")
        warnings.append("Text-only crop: chart style not fabricated from article text")
    return tokens, warnings


def _resolved_and_fallback(tokens: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    resolved = {}
    fallback_keys = []
    for key in STYLE_KEYS:
        token = tokens.get(key) or unknown_token(key, "Property not present in this saved draft")
        if token["status"] in {"detected", "estimated", "user_edited"} and token["value"] is not None:
            resolved[key] = copy.deepcopy(token["value"])
        else:
            resolved[key] = copy.deepcopy(NEUTRAL_RESOLVED[key])
            fallback_keys.append(key)
    return resolved, fallback_keys


def extract_style(path: str, page_index: int, rect: list[float], output_dir: str) -> dict:
    source = abs_path(path)
    info = inspect_source(source)
    if page_index < 0 or page_index >= int(info["page_count"]):
        raise ValueError("Invalid page_index")
    crop = clamp_rect(ensure_rect(rect))
    run_dir = unique_run_dir(output_dir)
    crop_png = abs_path(run_dir / "crop.png")
    native = None
    raster = None
    if info["kind"] == "pdf":
        doc = open_pdf(source)
        try:
            page = doc[page_index]
            clip = clip_from_norm(page.rect, crop)
            if clip.is_empty or clip.width < 2 or clip.height < 2:
                raise ValueError("Invalid rect")
            render_pdf_page(page, crop_png, max_side=1600, clip=clip)
            native = extract_pdf_signals(page, clip)
        finally:
            doc.close()
        rgb = load_rgb(crop_png)
        raster = analyze_raster(rgb, page_rect=crop)
    else:
        if page_index != 0:
            raise ValueError("Invalid page_index")
        rgb = crop_rgb(load_rgb(source), crop)
        save_png(rgb, crop_png)
        raster = analyze_raster(rgb, page_rect=crop)
    source_class = _classify(native, raster)
    tokens, warnings = _build_tokens(native, raster, source_class)
    from .local_fonts import apply_font_token

    tokens, font_warnings = apply_font_token(tokens, native, source_class, crop_png)
    warnings.extend(font_warnings)
    resolved, fallback_keys = _resolved_and_fallback(tokens)
    warnings = list(dict.fromkeys([w for w in warnings if w]))
    if fallback_keys:
        warnings.append("Preview defaults are not detected evidence")
    coverage = {
        "source_class": source_class,
        "native_text": bool(native and native.get("native_text")),
        "native_vectors": bool(native and native.get("native_vectors")),
    }
    return {
        "schema": SCHEMA,
        "name": DEFAULT_NAME,
        "source": {
            "path": source,
            "kind": info["kind"],
            "page_index": int(page_index),
            "page_count": int(info["page_count"]),
            "page_size": dict(info["pages"][page_index]),
            "rect": crop,
            "crop_png": crop_png,
        },
        "tokens": tokens,
        "resolved_style": resolved,
        "fallback_keys": fallback_keys,
        "warnings": warnings,
        "coverage": coverage,
    }


def edit_token(draft: dict, key: str, value) -> dict:
    if not isinstance(draft, dict) or draft.get("schema") != SCHEMA:
        raise ValueError("Style JSON is not a local-style-demo draft")
    if key not in STYLE_KEYS:
        raise ValueError(f"Unknown token key: {key}")
    updated = copy.deepcopy(draft)
    token = updated["tokens"].setdefault(key, _blank_tokens()[key])
    token["value"] = copy.deepcopy(value)
    token["status"] = "user_edited"
    token["reason"] = "User override; original evidence preserved"
    updated["tokens"][key] = token
    resolved, fallback_keys = _resolved_and_fallback(updated["tokens"])
    updated["resolved_style"] = resolved
    updated["fallback_keys"] = fallback_keys
    return updated


def save_style(draft: dict, name: str, path: str) -> None:
    if not isinstance(draft, dict) or draft.get("schema") != SCHEMA:
        raise ValueError("Style JSON is not a local-style-demo draft")
    payload = copy.deepcopy(draft)
    payload["name"] = str(name or DEFAULT_NAME)
    dest = abs_path(path)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    with Path(dest).open("w", encoding="utf-8") as handle:
        json.dump(jsonable(payload), handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_style(path: str) -> dict:
    source = abs_path(path)
    if not Path(source).is_file():
        raise FileNotFoundError(f"Style not found: {source}")
    with Path(source).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError("Style JSON is not a local-style-demo draft")
    for key in MIN_KEYS:
        if key not in payload.get("tokens", {}):
            raise ValueError(f"Style JSON missing token: {key}")
    for key in EXTRA_KEYS:
        payload["tokens"].setdefault(key, _blank_tokens()[key])
    payload["resolved_style"], payload["fallback_keys"] = _resolved_and_fallback(payload["tokens"])
    return payload

