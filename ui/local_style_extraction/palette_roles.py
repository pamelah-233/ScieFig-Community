from __future__ import annotations

from typing import Any

from .demo_common import (
    color_to_hex,
    hex_to_rgb,
    is_near_white,
    unique_hexes,
)

PALETTE_SAFETY_CEILING = 32
RASTER_QUANT_STEP = 12
RASTER_LEGEND_MAX = 16
RASTER_LEGEND_AREA_BAND = 0.40


def is_legend_patch(item: dict[str, Any]) -> bool:
    try:
        w = float(item["w"])
        h = float(item["h"])
        area_frac = float(item["area_frac"])
    except (KeyError, TypeError, ValueError):
        return False
    rgb = item.get("rgb")
    if rgb is None:
        try:
            rgb = hex_to_rgb(item["hex"])
        except (KeyError, ValueError):
            return False
    return 3.0 <= w <= 42.0 and 3.0 <= h <= 26.0 and area_frac < 0.03 and not is_near_white(rgb)


def fill_is_series_mark(rec: dict[str, Any]) -> bool:
    try:
        area_frac = float(rec["area_frac"])
        w = float(rec["w"])
        h = float(rec["h"])
    except (KeyError, TypeError, ValueError):
        return False
    rgb = rec.get("rgb")
    if rgb is None:
        try:
            rgb = hex_to_rgb(rec["hex"])
        except (KeyError, ValueError):
            return False
    if area_frac >= 0.35 or is_near_white(rgb):
        return False
    if is_legend_patch(rec):
        return True
    mx = max(w, h)
    mn = min(w, h)
    if mn <= 0:
        return False
    if 2.0 <= mx <= 16.0 and mx / mn <= 1.35 and area_frac <= 0.02:
        return True
    return area_frac < 0.35 and mn >= 6.0


def _seg_len(seg: tuple[float, float, float, float]) -> float:
    return float(((seg[2] - seg[0]) ** 2 + (seg[3] - seg[1]) ** 2) ** 0.5)


def _axis_aligned(seg: tuple[float, float, float, float], tol: float = 2.5) -> str | None:
    dx = abs(seg[2] - seg[0])
    dy = abs(seg[3] - seg[1])
    if dy <= tol and dx >= 8:
        return "h"
    if dx <= tol and dy >= 8:
        return "v"
    return None


def _near_clip_edge(seg: tuple[float, float, float, float], clip: Any, kind: str) -> bool:
    width = float(getattr(clip, "width", 0.0) or 0.0)
    height = float(getattr(clip, "height", 0.0) or 0.0)
    x0 = float(getattr(clip, "x0", 0.0) or 0.0)
    y0 = float(getattr(clip, "y0", 0.0) or 0.0)
    x1 = float(getattr(clip, "x1", x0 + width) or (x0 + width))
    y1 = float(getattr(clip, "y1", y0 + height) or (y0 + height))
    edge = max(6.0, 0.08 * min(max(width, 1.0), max(height, 1.0)))
    if kind == "h":
        y = 0.5 * (seg[1] + seg[3])
        return abs(y - y0) <= edge or abs(y - y1) <= edge
    x = 0.5 * (seg[0] + seg[2])
    return abs(x - x0) <= edge or abs(x - x1) <= edge


def _stroke_is_rect_like(rec: dict[str, Any]) -> bool:
    segs = rec.get("segs") or []
    if len(segs) < 4:
        return False
    kinds = [_axis_aligned(seg, 2.5) for seg in segs]
    if any(kind is None for kind in kinds):
        return False
    n_h = sum(1 for kind in kinds if kind == "h")
    n_v = sum(1 for kind in kinds if kind == "v")
    return n_h >= 2 and n_v >= 2


def stroke_is_grid_geometry(
    rec: dict[str, Any],
    clip: Any,
    grid_recs: list[dict[str, Any]] | None,
    grid_color: str | None,
) -> bool:
    rgb = rec.get("rgb")
    if rgb is None:
        try:
            rgb = hex_to_rgb(rec["hex"])
        except (KeyError, ValueError):
            return False
    try:
        width = float(rec.get("width") or 0.0)
    except (TypeError, ValueError):
        width = 0.0
    if width > 0.85:
        return False
    if max(rgb) - min(rgb) > 28:
        return False
    if min(rgb) < 150 or max(rgb) > 245:
        return False
    hex_value = rec.get("hex")
    if grid_color and hex_value == grid_color:
        return True
    if grid_recs:
        for item in grid_recs:
            if item.get("hex") == hex_value:
                return True
    clip_w = float(getattr(clip, "width", 1.0) or 1.0)
    clip_h = float(getattr(clip, "height", 1.0) or 1.0)
    min_len = 0.22 * min(clip_w, clip_h)
    for seg in rec.get("segs") or []:
        kind = _axis_aligned(seg, 2.5)
        if kind is not None and _seg_len(seg) >= min_len:
            return True
    return False


def stroke_is_axis_geometry(rec: dict[str, Any], clip: Any, axis_recs: list[dict[str, Any]] | None) -> bool:
    segs = rec.get("segs") or []
    if not segs:
        return False
    clip_w = float(getattr(clip, "width", 1.0) or 1.0)
    clip_h = float(getattr(clip, "height", 1.0) or 1.0)
    min_len = 0.28 * min(clip_w, clip_h)
    has_sloped = False
    has_frame_axis = False
    axis_segs = [item.get("seg") for item in (axis_recs or []) if item.get("seg")]
    for seg in segs:
        kind = _axis_aligned(seg, 2.5)
        length = _seg_len(seg)
        if kind is None and length >= 4.0:
            has_sloped = True
            continue
        if kind and length >= min_len and _near_clip_edge(seg, clip, kind):
            has_frame_axis = True
        for axis_seg in axis_segs:
            if (
                kind
                and length >= min_len
                and abs(seg[0] - axis_seg[0]) <= 3.0
                and abs(seg[1] - axis_seg[1]) <= 3.0
                and abs(seg[2] - axis_seg[2]) <= 3.0
                and abs(seg[3] - axis_seg[3]) <= 3.0
            ):
                has_frame_axis = True
    if has_sloped:
        return False
    return has_frame_axis


def stroke_is_series_mark(
    rec: dict[str, Any],
    clip: Any,
    axis_recs: list[dict[str, Any]] | None,
    grid_recs: list[dict[str, Any]] | None,
    grid_color: str | None,
) -> bool:
    try:
        width = float(rec.get("width") or 0.0)
    except (TypeError, ValueError):
        width = 0.0
    if width <= 0:
        return False
    rgb = rec.get("rgb")
    if rgb is None:
        try:
            rgb = hex_to_rgb(rec["hex"])
        except (KeyError, ValueError):
            return False
    if is_near_white(rgb):
        return False
    if not rec.get("segs"):
        return False
    if _stroke_is_rect_like(rec):
        return False
    if stroke_is_grid_geometry(rec, clip, grid_recs, grid_color):
        return False
    if stroke_is_axis_geometry(rec, clip, axis_recs):
        return False
    bbox = rec.get("bbox") or ()
    if len(bbox) >= 4:
        span = max(abs(float(bbox[2]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[1])))
    else:
        span = 0.0
    clip_w = float(getattr(clip, "width", 1.0) or 1.0)
    clip_h = float(getattr(clip, "height", 1.0) or 1.0)
    min_span = max(36.0, 0.12 * min(clip_w, clip_h))
    if span < min_span:
        return False
    return True


def text_hexes(spans: list[dict[str, Any]] | None) -> set[str]:
    out: set[str] = set()
    for span in spans or []:
        hex_value = color_to_hex(span.get("color"))
        if hex_value:
            out.add(hex_value)
    return out


def assemble_series_colors(
    legend: list[str] | None,
    marks: list[str] | None,
    *,
    tol: float = 14.0,
    ceiling: int = PALETTE_SAFETY_CEILING,
) -> tuple[list[str], bool]:
    ordered: list[str] = []
    for src in list(legend or []) + list(marks or []):
        if src:
            ordered.append(src)
    uniq = unique_hexes(ordered, tol=tol, limit=None)
    truncated = False
    if ceiling is not None and ceiling > 0 and len(uniq) > ceiling:
        uniq = uniq[:ceiling]
        truncated = True
    return uniq, truncated


def raster_legend_geom(w: float, h: float, area_frac: float, solidity: float) -> bool:
    try:
        ww = float(w)
        hh = float(h)
        af = float(area_frac)
        sol = float(solidity)
    except (TypeError, ValueError):
        return False
    mn = min(ww, hh)
    mx = max(ww, hh)
    if mn < 3.0 or mx > 90.0:
        return False
    if af >= 0.035 or sol < 0.70:
        return False
    if mx / max(mn, 1.0) > 6.5:
        return False
    return True


def raster_legend_group_ok(n: int, std_x: float, std_y: float, width: float, height: float) -> bool:
    if n < 2 or n > RASTER_LEGEND_MAX:
        return False
    return float(std_x) <= 0.22 * max(float(width), 1.0) or float(std_y) <= 0.22 * max(float(height), 1.0)


def raster_legend_on_margin(med_cx: float, med_cy: float, width: float, height: float) -> bool:
    w = max(float(width), 1.0)
    h = max(float(height), 1.0)
    x = float(med_cx)
    y = float(med_cy)
    return x <= 0.28 * w or x >= 0.72 * w or y <= 0.22 * h or y >= 0.78 * h


def raster_bar_geom(w: float, h: float, area_frac: float, solidity: float) -> bool:
    try:
        ww = float(w)
        hh = float(h)
        af = float(area_frac)
        sol = float(solidity)
    except (TypeError, ValueError):
        return False
    if af >= 0.35 or af < 0.002:
        return False
    if sol < 0.62:
        return False
    mn = min(ww, hh)
    mx = max(ww, hh)
    if mn < 8.0 or mx < 16.0:
        return False
    return True
