from __future__ import annotations

from collections import Counter
import re
import math
from statistics import median
from typing import Any

import fitz
from .pdf_chart_marks import chart_mark_signals, clip_segment, visible_drawings, marker_shape, visible_axis_strokes

from .demo_common import (
    color_to_hex,
    color_to_rgb,
    empty_signals,
    evidence_item,
    hex_to_rgb,
    is_dark,
    is_gray,
    is_near_white,
    page_norm_bbox,
    saturation,
    unique_hexes,
)
from .fonthelpers import (
    build_font_lookup,
    font_item_needed,
    resolve_span_family,
)
from .palette_roles import (
    PALETTE_SAFETY_CEILING,
    assemble_series_colors,
    fill_is_series_mark,
    stroke_is_series_mark,
)


def open_pdf(path: str) -> fitz.Document:
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc
    if doc.is_encrypted:
        doc.close()
        raise ValueError("Encrypted PDF is not supported")
    return doc


def page_info(page: fitz.Page, index: int) -> dict[str, Any]:
    rect = page.rect
    return {
        "index": int(index),
        "width": float(rect.width),
        "height": float(rect.height),
        "unit": "pt",
    }


def render_pdf_page(page: fitz.Page, output_path: str, max_side: int = 1600, clip=None) -> tuple[int, int]:
    rect = clip if clip is not None else page.rect
    longest = max(float(rect.width), float(rect.height), 1.0)
    scale = float(max_side) / longest
    if scale <= 0:
        scale = 1.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    pix.save(output_path)
    return int(pix.width), int(pix.height)


def _drawing_rect(drawing: dict[str, Any]) -> fitz.Rect:
    return fitz.Rect(drawing.get("rect"))


def _inflate_rect(rect: fitz.Rect, pad: float = 1.0) -> fitz.Rect:
    r = fitz.Rect(rect)
    if r.width < pad:
        r.x0 -= pad
        r.x1 += pad
    if r.height < pad:
        r.y0 -= pad
        r.y1 += pad
    return r


def _opacity_ok(drawing: dict[str, Any], key: str) -> bool:
    try:
        value = float(drawing.get(key, 1.0))
    except (TypeError, ValueError):
        return True
    return value >= 0.2


def _line_segments(drawing: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    lines: list[tuple[float, float, float, float]] = []
    for item in drawing.get("items") or []:
        if not item:
            continue
        op = item[0]
        if op == "l" and len(item) >= 3:
            p1, p2 = item[1], item[2]
            lines.append((float(p1.x), float(p1.y), float(p2.x), float(p2.y)))
        elif op == "re" and len(item) >= 2:
            rect = fitz.Rect(item[1])
            x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
            lines.extend(
                [
                    (x0, y0, x1, y0),
                    (x1, y0, x1, y1),
                    (x1, y1, x0, y1),
                    (x0, y1, x0, y0),
                ]
            )
    return lines


def _seg_len(seg: tuple[float, float, float, float]) -> float:
    return float(((seg[2] - seg[0]) ** 2 + (seg[3] - seg[1]) ** 2) ** 0.5)


def _axis_aligned(seg: tuple[float, float, float, float], tol: float = 2.0) -> str | None:
    dx = abs(seg[2] - seg[0])
    dy = abs(seg[3] - seg[1])
    if dy <= tol and dx >= 8:
        return "h"
    if dx <= tol and dy >= 8:
        return "v"
    return None


def _collect_spans(page: fitz.Page, clip: fitz.Rect) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    payload = page.get_text("dict", clip=clip)
    for block in payload.get("blocks", []):
        if int(block.get("type", 0)) != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text") or "").strip()
                if not text:
                    continue
                bbox = span.get("bbox")
                if not bbox:
                    continue
                inter = fitz.Rect(bbox) & clip
                if inter.is_empty or inter.width < 0.4 or inter.height < 0.4:
                    continue
                spans.append(span)
                span['line_direction'] = line.get('dir', (1, 0))
    needed = {span.get("font") for span in spans}
    records: list[tuple[str | None, str | None, bytes]] = []
    try:
        font_items = list(page.get_fonts())
    except Exception:
        font_items = []
    doc = page.parent
    for item in font_items:
        basefont = item[3] if len(item) > 3 else None
        name = item[4] if len(item) > 4 else None
        buf = b""
        if font_item_needed(basefont, name, needed):
            try:
                extracted = doc.extract_font(item[0])
            except Exception:
                extracted = None
            if isinstance(extracted, tuple):
                for part in extracted:
                    if isinstance(part, (bytes, bytearray)) and len(part) > len(buf):
                        buf = bytes(part)
        records.append((str(basefont) if basefont else None, str(name) if name else None, buf))
    lookup = build_font_lookup(records)
    out: list[dict[str, Any]] = []
    for span in spans:
        rec = dict(span)
        raw = span.get("font")
        rec["raw_font"] = raw
        rec["readable_family"] = resolve_span_family(raw, lookup)
        out.append(rec)
    return out


def _collect_images(page: fitz.Page, clip: fitz.Rect) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    try:
        infos = page.get_image_info()
    except Exception:
        infos = []
    for info in infos:
        bbox = info.get("bbox")
        if not bbox:
            continue
        inter = fitz.Rect(bbox) & clip
        if inter.is_empty:
            continue
        images.append(info)
    return images


def _merge_rects(rects: list[fitz.Rect], gap: float) -> list[fitz.Rect]:
    if not rects:
        return []
    boxes = [fitz.Rect(r) for r in rects if r.width > 1 and r.height > 1]
    changed = True
    while changed:
        changed = False
        out: list[fitz.Rect] = []
        used = [False] * len(boxes)
        for i, a in enumerate(boxes):
            if used[i]:
                continue
            acc = fitz.Rect(a)
            grow = fitz.Rect(acc.x0 - gap, acc.y0 - gap, acc.x1 + gap, acc.y1 + gap)
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                b = boxes[j]
                other = fitz.Rect(b.x0 - gap, b.y0 - gap, b.x1 + gap, b.y1 + gap)
                if grow.intersects(other):
                    acc |= b
                    grow = fitz.Rect(acc.x0 - gap, acc.y0 - gap, acc.x1 + gap, acc.y1 + gap)
                    used[j] = True
                    changed = True
            used[i] = True
            out.append(acc)
        boxes = out
    return boxes


def suggest_pdf_regions(page: fitz.Page) -> list[dict[str, Any]]:
    page_rect = page.rect
    page_area = max(float(page_rect.width * page_rect.height), 1.0)
    drawing_rects: list[fitz.Rect] = []
    image_rects: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        rect = _inflate_rect(_drawing_rect(drawing), pad=1.2)
        if rect.width * rect.height > 0.92 * page_area:
            continue
        drawing_rects.append(rect)
    for info in _collect_images(page, page_rect):
        bbox = info.get("bbox")
        if bbox:
            image_rects.append(fitz.Rect(bbox))
    gap = max(8.0, 0.012 * max(page_rect.width, page_rect.height))
    merged = _merge_rects(drawing_rects + image_rects, gap)
    regions: list[dict[str, Any]] = []
    for idx, rect in enumerate(merged):
        area = float(rect.width * rect.height)
        if area < 0.02 * page_area:
            continue
        inter_draw = sum(1 for r in drawing_rects if (r & rect).width > 0)
        inter_img = sum(1 for r in image_rects if (r & rect).width > 0)
        if inter_draw >= 3 and inter_img == 0:
            label = "vector-plot"
            reason = "Clustered native strokes/fills"
        elif inter_img and inter_draw < 3:
            label = "embedded-image"
            reason = "Embedded raster image region"
        elif inter_img and inter_draw:
            label = "mixed-region"
            reason = "Image plus vector marks"
        else:
            label = "graphic-cluster"
            reason = "Grouped page graphics"
        nx, ny, nw, nh = page_norm_bbox(page_rect, (rect.x0, rect.y0, rect.x1, rect.y1))
        nx = min(max(nx, 0.0), 1.0)
        ny = min(max(ny, 0.0), 1.0)
        nw = min(max(nw, 0.0), 1.0 - nx)
        nh = min(max(nh, 0.0), 1.0 - ny)
        if nw < 0.03 or nh < 0.03:
            continue
        regions.append(
            {
                "id": f"r{idx}",
                "rect": [nx, ny, nw, nh],
                "label": label,
                "reason": reason,
            }
        )
    regions.sort(key=lambda item: item["rect"][2] * item["rect"][3], reverse=True)
    return regions[:10]


def _fill_records(drawings: list[dict[str, Any]], clip: fitz.Rect) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    clip_area = max(float(clip.width * clip.height), 1.0)
    for drawing in drawings:
        if not _opacity_ok(drawing, "fill_opacity"):
            continue
        fill = color_to_hex(drawing.get("fill"))
        if fill is None:
            continue
        rect = _inflate_rect(_drawing_rect(drawing), pad=0.6) & clip
        if rect.is_empty:
            continue
        area = float(rect.width * rect.height)
        if area < 0.4:
            continue
        records.append(
            {
                "hex": fill,
                "rgb": hex_to_rgb(fill),
                "bbox": (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                "w": float(rect.width),
                "h": float(rect.height),
                "area": area,
                "area_frac": area / clip_area,
                "seq": int(drawing.get("seqno") or 0),
                "shape": marker_shape(drawing.get("items", [])),
            }
        )
    return records


def _stroke_records(drawings: list[dict[str, Any]], clip: fitz.Rect) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for drawing in drawings:
        if not _opacity_ok(drawing, "stroke_opacity"):
            continue
        color = color_to_hex(drawing.get("color"))
        if color is None:
            continue
        width = float(drawing.get("width") or 0.0)
        rect = _inflate_rect(_drawing_rect(drawing), pad=1.2) & clip
        if rect.is_empty:
            continue
        segs = []
        for seg in _line_segments(drawing):
            clipped = clip_segment(seg, drawing.get('visible_clip', clip))
            if clipped and _seg_len(clipped) >= .5:
                segs.append(clipped)
        records.append(
            {
                "hex": color,
                "rgb": hex_to_rgb(color),
                "width": width,
                "bbox": (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                "segs": segs,
                "seq": int(drawing.get("seqno") or 0),
            }
        )
    return records


def _legend_order(fills: list[dict[str, Any]], spans: list[dict[str, Any]], clip: fitz.Rect) -> list[str] | None:
    patches = [
        item
        for item in fills
        if 3.0 <= item["w"] <= 42.0
        and 3.0 <= item["h"] <= 26.0
        and item["area_frac"] < 0.03
        and not is_near_white(item["rgb"])
    ]
    if len(patches) < 2:
        return None
    xs = [0.5 * (p["bbox"][0] + p["bbox"][2]) for p in patches]
    ys = [0.5 * (p["bbox"][1] + p["bbox"][3]) for p in patches]
    x_spread = max(xs) - min(xs)
    y_spread = max(ys) - min(ys)
    if y_spread >= x_spread:
        patches.sort(key=lambda p: (p["bbox"][1], p["bbox"][0]))
    else:
        patches.sort(key=lambda p: (p["bbox"][0], p["bbox"][1]))
    colors = unique_hexes([p["hex"] for p in patches], tol=14.0, limit=None)
    if len(colors) < 2:
        return None
    return colors


def _first_paint_order(fills: list[dict[str, Any]], strokes: list[dict[str, Any]]) -> list[str]:
    items: list[tuple[float, float, int, str]] = []
    for rec in fills:
        if rec["area_frac"] >= 0.35 or is_near_white(rec["rgb"]):
            continue
        items.append((rec["bbox"][1], rec["bbox"][0], rec["seq"], rec["hex"]))
    for rec in strokes:
        if is_near_white(rec["rgb"]):
            continue
        items.append((rec["bbox"][1], rec["bbox"][0], rec["seq"], rec["hex"]))
    items.sort()
    return unique_hexes([hex_value for _, _, _, hex_value in items], tol=14.0, limit=None)


def _axis_strokes(strokes: list[dict[str, Any]], clip: fitz.Rect, fills=()) -> list[dict[str, Any]]:
    min_len = 0.28 * min(float(clip.width), float(clip.height))
    found: list[dict[str, Any]] = []
    for rec in strokes:
        if any(f.get('shape') == 'square' and f['h'] > f['w']*1.35 and
               max(abs(a-b) for a,b in zip(rec['bbox'], f['bbox'])) < 1.3 for f in fills):
            continue
        if rec["width"] > 4.5:
            continue
        if not is_dark(rec["rgb"], 110):
            continue
        for seg in rec["segs"]:
            kind = _axis_aligned(seg, tol=2.5)
            if kind is None:
                continue
            length = _seg_len(seg)
            if length < min_len:
                continue
            found.append(
                {
                    "kind": kind,
                    "seg": seg,
                    "width": rec["width"],
                    "hex": rec["hex"],
                    "rgb": rec["rgb"],
                    "length": length,
                }
            )
    return found


def _regular_parallel(gridish: list[dict[str, Any]], kind: str) -> bool:
    items = [g for g in gridish if g["kind"] == kind]
    if len(items) < 3:
        return False
    if kind == "h":
        coords = sorted(0.5 * (g["seg"][1] + g["seg"][3]) for g in items)
    else:
        coords = sorted(0.5 * (g["seg"][0] + g["seg"][2]) for g in items)
    uniq = [coords[0]]
    for value in coords[1:]:
        if abs(value - uniq[-1]) > 2.0:
            uniq.append(value)
    if len(uniq) < 3:
        return False
    gaps = [uniq[i + 1] - uniq[i] for i in range(len(uniq) - 1)]
    med = float(sorted(gaps)[len(gaps) // 2])
    if med < 6.0:
        return False
    return sum(1 for gap in gaps if abs(gap - med) <= 0.4 * med) >= 2


def _grid_from_strokes(strokes: list[dict[str, Any]], clip: fitz.Rect, axis: list[dict[str, Any]]) -> tuple[bool | None, str | None, list[dict[str, Any]]]:
    min_len = 0.22 * min(float(clip.width), float(clip.height))
    gridish: list[dict[str, Any]] = []
    for rec in strokes:
        rgb = rec["rgb"]
        if rec["width"] > 0.85:
            continue
        if not is_gray(rgb, 28):
            continue
        if min(rgb) < 150 or max(rgb) > 245:
            continue
        for seg in rec["segs"]:
            kind = _axis_aligned(seg, 2.5)
            if kind is None or _seg_len(seg) < min_len:
                continue
            gridish.append({"kind": kind, "hex": rec["hex"], "seg": seg, "bbox": rec["bbox"]})
    if not (_regular_parallel(gridish, "h") or _regular_parallel(gridish, "v")):
        return None, None, []
    hexes = unique_hexes([item["hex"] for item in gridish], tol=12.0, limit=2)
    return True, (hexes[0] if hexes else None), gridish[:8]


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


def _point_candidates(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for rec in fills:
        w, h = rec["w"], rec["h"]
        mx = max(w, h)
        mn = min(w, h)
        if mx < 1.0 or mx > 10.0 or not rec.get('shape'):
            continue
        if mn <= 0:
            continue
        aspect = mx / mn
        if aspect > 1.35:
            continue
        if rec["area_frac"] > 0.02:
            continue
        if is_near_white(rec["rgb"]):
            continue
        points.append(rec)
    return points


def _font_hierarchy(spans: list[dict[str, Any]]) -> dict[str, Any]:
    sized: list[tuple[float, str | None, str, int, tuple, Any]] = []
    for span in spans:
        try:
            size = float(span.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
        if size <= 0.5:
            continue
        raw = str(span.get("raw_font") or span.get("font") or "")
        family = span.get("readable_family")
        if not family:
            family = None
        nchars = max(len(str(span.get("text") or "")), 1)
        sized.append((size, family, raw, nchars, tuple(span.get("bbox") or (0, 0, 0, 0)), span.get("color")))
    if not sized:
        return {}
    total_chars = sum(item[3] for item in sized)
    resolved_chars = sum(item[3] for item in sized if item[1])
    family = None
    if total_chars and resolved_chars * 2 >= total_chars:
        fam_counts: Counter[str] = Counter()
        for _, fam, _, nchars, _, _ in sized:
            if fam:
                fam_counts[str(fam)] += int(nchars)
        if fam_counts:
            family = fam_counts.most_common(1)[0][0]
    raw_counts: Counter[str] = Counter()
    for _, _, raw, nchars, _, _ in sized:
        if raw:
            raw_counts[raw] += int(nchars)
    raw_name = raw_counts.most_common(1)[0][0] if raw_counts else None
    sizes = [size for size, _, _, _, _, _ in sized]
    counts = Counter(round(size, 4) for size in sizes)
    levels = [size for size, _ in counts.most_common()]
    levels_sorted = sorted(set(sizes), reverse=True)
    base = counts.most_common(1)[0][0]
    numeric = [s for s in spans if re.fullmatch(r'[-+−]?[\d.,]+(?:[eE][-+]?\d+)?', str(s.get('text', '')).strip())]
    rotated = [s for s in spans if abs(s.get('line_direction', (1,0))[1]) > .95
               and len(str(s.get('text', ''))) > 3]
    tick = median(float(s['size']) for s in numeric) if numeric else base
    axis = median(float(s['size']) for s in rotated) if rotated else None
    weights = ['bold' if int(s.get('flags', 0)) & 16 or 'bold' in str(s.get('raw_font', '')).lower()
               else 'normal' for s in spans]
    colors = [color_to_hex(color) for _, _, _, _, _, color in sized]
    colors = [c for c in colors if c]
    label = Counter(colors).most_common(1)[0][0] if colors else None
    return {
        "font_family": family,
        "raw_font_name": raw_name,
        "font_size_pt": float(base),
        "font_weight": Counter(weights).most_common(1)[0][0],
        "axis_label_size_pt": float(axis) if axis is not None else None,
        "tick_label_size_pt": float(tick),
        "legend_size_pt": None,
        "label_color": label,
        "levels": levels_sorted,
    }


def extract_pdf_signals(page: fitz.Page, clip: fitz.Rect) -> dict[str, Any]:
    signals = empty_signals()
    page_rect = page.rect
    drawings = visible_drawings(page, clip)
    spans = _collect_spans(page, clip)
    images = _collect_images(page, clip)
    signals["native_text"] = bool(spans)
    signals["native_vectors"] = bool(drawings)
    signals["native_images"] = bool(images)
    fills = _fill_records(drawings, clip)
    strokes = _stroke_records(drawings, clip)

    clip_area = max(float(clip.width * clip.height), 1.0)
    bg_candidates = [f for f in fills if f["area_frac"] >= 0.35]
    if bg_candidates:
        bg_candidates.sort(key=lambda item: item["area"], reverse=True)
        signals["background_color"] = bg_candidates[0]["hex"]
        signals["evidence"]["background_color"].append(
            evidence_item("fill", page_norm_bbox(page_rect, bg_candidates[0]["bbox"]), "large vector fill")
        )
        signals["methods"]["background_color"] = "pdf_native"

    fonts = _font_hierarchy(spans)
    if fonts:
        signals["font_family"] = fonts.get("font_family")
        signals["raw_font_name"] = fonts.get("raw_font_name")
        signals["font_size_pt"] = fonts.get("font_size_pt")
        signals["font_weight"] = fonts.get("font_weight")
        signals["methods"]["font_weight"] = "pdf_geometry"
        signals["evidence"]["font_weight"] = [evidence_item("text", page_norm_bbox(page_rect, spans[0]['bbox']), "Dominant native font weight")]
        signals["axis_label_size_pt"] = fonts.get("axis_label_size_pt")
        signals["tick_label_size_pt"] = fonts.get("tick_label_size_pt")
        signals["legend_size_pt"] = fonts.get("legend_size_pt")
        signals["label_color"] = fonts.get("label_color")
        if spans:
            bbox = page_norm_bbox(page_rect, spans[0].get("bbox"))
            if signals["font_family"]:
                signals["evidence"]["font_family"].append(
                    evidence_item("text", bbox, f"native font {signals['font_family']}")
                )
                signals["methods"]["font_family"] = "pdf_native"
            elif fonts.get("raw_font_name"):
                signals["warnings"].append(
                    f"Anonymous font id {fonts.get('raw_font_name')} is not a family identity"
                )
            if signals["font_size_pt"] is not None:
                signals["evidence"]["font_size_pt"].append(
                    evidence_item("text", bbox, f"native size {signals['font_size_pt']} pt")
                )
                signals["methods"]["font_size_pt"] = "pdf_native"
                for key in ("axis_label_size_pt", "tick_label_size_pt"):
                    if signals.get(key) is not None:
                        signals["methods"][key] = "pdf_geometry"
                        signals["evidence"][key] = [evidence_item("text", bbox, "Native size; text role inferred from orientation or numeric content")]
            if signals["label_color"]:
                signals["evidence"]["label_color"].append(evidence_item("text", bbox, "native text color"))
                signals["methods"]["label_color"] = "pdf_native"

    axis = visible_axis_strokes(page, clip, _axis_strokes(strokes, clip, fills))
    if axis:
        widths = [item["width"] for item in axis if item["width"] > 0]
        if widths:
            widths.sort()
            signals["axis_line_width"] = float(widths[len(widths) // 2])
            signals["methods"]["axis_line_width"] = "pdf_native"
            bbox = page_norm_bbox(page_rect, axis[0]["seg"])
            signals["evidence"]["axis_line_width"].append(
                evidence_item("stroke", bbox, f"native axis width {signals['axis_line_width']} pt")
            )
        xs: list[float] = []
        ys: list[float] = []
        for item in axis:
            xs.extend([item["seg"][0], item["seg"][2]])
            ys.extend([item["seg"][1], item["seg"][3]])
        frame = (min(xs), min(ys), max(xs), max(ys))
        signals["axis_frame"] = frame
        fw = max(frame[2] - frame[0], 1.0)
        fh = max(frame[3] - frame[1], 1.0)
        signals['axes_box_aspect'] = fh/fw
        signals['methods']['axes_box_aspect'] = 'pdf_geometry'
        signals['evidence']['axes_box_aspect'] = [evidence_item('stroke', page_norm_bbox(page_rect, frame),
            'Visible axis box height/width; data ranges are not transferred')]
        below = [s for s in spans if s['bbox'][1] > frame[3]-.2
                 and s['bbox'][0] < frame[2] and s['bbox'][2] > frame[0]]
        angles = [math.degrees(math.atan2(-s.get('line_direction',(1,0))[1],s.get('line_direction',(1,0))[0])) for s in below]
        if len(angles)>=2 and max(angles)-min(angles)<2:
            signals['x_tick_rotation'] = median(angles)
            signals['methods']['x_tick_rotation'] = 'pdf_geometry'
            signals['evidence']['x_tick_rotation'] = [evidence_item('text',page_norm_bbox(page_rect,below[0]['bbox']),
                'Consistent native text orientation below the visible x axis')]
        ticks = {'x':[], 'y':[]}
        for rec in strokes:
            for seg in rec['segs']:
                length = _seg_len(seg)
                if not .4 <= length <= min(8, .1*min(fw,fh)):
                    continue
                if (abs(seg[1]-seg[3])<.1 and abs(max(seg[0],seg[2])-frame[0])<.2
                        and frame[1] <= seg[1] <= frame[3]):
                    ticks['y'].append((length,rec['width'],seg))
                if (abs(seg[0]-seg[2])<.1 and abs(min(seg[1],seg[3])-frame[3])<.2
                        and frame[0] <= seg[0] <= frame[2]):
                    ticks['x'].append((length,rec['width'],seg))
        for orientation, key in (('y','tick_length'), ('x','x_tick_length')):
            if len(ticks[orientation])>=2:
                signals[key] = median(t[0] for t in ticks[orientation])
                signals['methods'][key] = 'pdf_geometry'
                signals['evidence'][key] = [evidence_item('stroke',page_norm_bbox(page_rect,frame),'Short perpendicular native ticks at the visible axis')]
        all_ticks = ticks['x']+ticks['y']
        if len(all_ticks)>=2:
            signals['tick_width'] = median(t[1] for t in all_ticks)
            signals['methods']['tick_width'] = 'pdf_geometry'
            signals['evidence']['tick_width'] = [evidence_item('stroke',page_norm_bbox(page_rect,frame),'Native tick stroke width')]
        top = False
        right = False
        for item in axis:
            seg = item["seg"]
            if item["kind"] == "h" and abs(((seg[1] + seg[3]) / 2.0) - frame[1]) <= max(3.0, 0.08 * fh):
                if _seg_len(seg) >= 0.45 * fw:
                    top = True
            if item["kind"] == "v" and abs(((seg[0] + seg[2]) / 2.0) - frame[2]) <= max(3.0, 0.08 * fw):
                if _seg_len(seg) >= 0.45 * fh:
                    right = True
        signals["show_top_spine"] = top
        signals["show_right_spine"] = right
        signals["methods"]["show_top_spine"] = "pdf_native"
        signals["methods"]["show_right_spine"] = "pdf_native"
        signals["evidence"]["show_top_spine"].append(
            evidence_item("stroke", page_norm_bbox(page_rect, frame), "top spine present" if top else "no top spine")
        )
        signals["evidence"]["show_right_spine"].append(
            evidence_item("stroke", page_norm_bbox(page_rect, frame), "right spine present" if right else "no right spine")
        )

    grid_vis, grid_color, grid_ev = _grid_from_strokes(strokes, clip, axis)
    if grid_vis:
        signals["gridline_visible"] = True
        signals["gridline_color"] = grid_color
        signals["methods"]["gridline_visible"] = "pdf_native"
        signals["methods"]["gridline_color"] = "pdf_native"
        gbbox = page_norm_bbox(page_rect, grid_ev[0]["bbox"])
        signals["evidence"]["gridline_visible"].append(evidence_item("stroke", gbbox, "repeated light aligned strokes"))
        if grid_color:
            signals["evidence"]["gridline_color"].append(evidence_item("stroke", gbbox, "grid stroke color"))
    elif axis:
        signals["gridline_visible"] = False
        signals["methods"]["gridline_visible"] = "pdf_native"
        signals["evidence"]["gridline_visible"].append(
            evidence_item("stroke", page_norm_bbox(page_rect, (clip.x0, clip.y0, clip.x1, clip.y1)), "no grid strokes")
        )

    axis_hexes = unique_hexes([item["hex"] for item in axis], tol=12.0, limit=3)
    legend = _legend_order(fills, spans, clip)
    mark_items: list[tuple[float, float, int, str]] = []
    for rec in fills:
        if fill_is_series_mark(rec):
            mark_items.append((rec["bbox"][1], rec["bbox"][0], rec["seq"], rec["hex"]))
    for rec in strokes:
        if stroke_is_series_mark(rec, clip, axis, grid_ev, signals.get("gridline_color")):
            mark_items.append((rec["bbox"][1], rec["bbox"][0], rec["seq"], rec["hex"]))
    mark_items.sort()
    mark_colors = unique_hexes([hex_value for _, _, _, hex_value in mark_items], tol=14.0, limit=None)
    series_source, truncated = assemble_series_colors(legend, mark_colors, tol=14.0, ceiling=PALETTE_SAFETY_CEILING)
    if legend:
        order_reason = "Legend color patches, top-to-bottom then left-to-right; additional series marks appended when absent from legend"
        if series_source:
            signals["methods"]["series_colors"] = "pdf_native"
    else:
        order_reason = "No legend patches found; series colors ordered by first vector paint (top-to-bottom, then left-to-right)"
        if series_source:
            signals["methods"]["series_colors"] = "pdf_native"
            signals["warnings"].append(order_reason)
    if truncated:
        signals["warnings"].append(
            f"Series palette truncated at documented safety ceiling {PALETTE_SAFETY_CEILING}"
        )
    if series_source:
        signals["series_colors"] = series_source
        signals["series_order_reason"] = order_reason
        for hex_value in series_source:
            ev_fill = next((f for f in fills if f["hex"] == hex_value), None)
            ev_stroke = next((s for s in strokes if s["hex"] == hex_value), None)
            rec = ev_fill or ev_stroke
            if rec is None:
                continue
            kind = "fill" if ev_fill is not None else "stroke"
            signals["evidence"]["series_colors"].append(
                evidence_item(kind, page_norm_bbox(page_rect, rec["bbox"]), f"series color {hex_value}")
            )

    points = _point_candidates(fills)
    if points:
        sizes = [0.5 * (p["w"] + p["h"]) for p in points]
        sizes.sort()
        signals["point_size"] = float(sizes[len(sizes) // 2])
        shapes = {p['shape'] for p in points}
        signals["point_shape"] = shapes.pop() if len(shapes) == 1 else None
        signals["methods"]["point_size"] = "pdf_native"
        signals["methods"]["point_shape"] = "pdf_geometry"
        signals["evidence"]["point_size"].append(
            evidence_item("fill", page_norm_bbox(page_rect, points[0]["bbox"]), "compact filled marker")
        )
        signals["evidence"]["point_shape"].append(
            evidence_item("fill", page_norm_bbox(page_rect, points[0]["bbox"]), "native marker path geometry")
        )

    series_strokes = []
    for rec in strokes:
        if rec["width"] <= 0:
            continue
        if rec["hex"] in axis_hexes and is_dark(rec["rgb"]) and saturation(rec["rgb"]) < 0.2:
            continue
        if signals["gridline_color"] and rec["hex"] == signals["gridline_color"]:
            continue
        if rec["width"] < 0.35:
            continue
        if _stroke_is_rect_like(rec):
            continue
        if not rec.get("segs"):
            continue
        long_color = rec["hex"] in (signals["series_colors"] or [])
        if long_color or (saturation(rec["rgb"]) > 0.2 and rec["width"] >= 0.6):
            series_strokes.append(rec["width"])
    if series_strokes:
        series_strokes.sort()
        signals["line_width"] = float(series_strokes[len(series_strokes) // 2])
        signals["methods"]["line_width"] = "pdf_native"
        signals["evidence"]["line_width"].append(
            evidence_item("stroke", page_norm_bbox(page_rect, (clip.x0, clip.y0, clip.x1, clip.y1)), "native series stroke width")
        )

    chart_mark_signals(page, clip, drawings, signals)
    image_area = 0.0
    for info in images:
        inter = fitz.Rect(info.get("bbox")) & clip
        image_area += float(inter.width * inter.height)
    image_frac = image_area / clip_area
    n_axis = len(axis)
    n_color_fills = len([f for f in fills if f["area_frac"] < 0.35 and not is_near_white(f["rgb"]) and saturation(f["rgb"]) > 0.15])
    plot_like = n_axis >= 2 or n_color_fills >= 2 or bool(signals["series_colors"]) or len(strokes) >= 8
    text_like = len(spans) >= 2 and n_axis < 2 and n_color_fills == 0 and len(strokes) <= 2 and not plot_like
    photo_like = image_frac >= 0.55 and n_axis < 2 and n_color_fills < 2
    signals["plot_like"] = bool(plot_like)
    signals["text_like"] = bool(text_like and not plot_like)
    signals["photo_like"] = bool(photo_like and not plot_like)
    if photo_like and plot_like:
        signals["warnings"].append("Crop mixes an embedded image with vector marks")
    return signals

