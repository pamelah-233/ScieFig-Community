from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from .demo_common import (
    RASTER_BASE_FONT_PT,
    RASTER_WIDTH_REF_PT,
    color_dist,
    empty_signals,
    evidence_item,
    hex_to_rgb,
    image_crop_box,
    is_dark,
    is_gray,
    is_near_white,
    rgb_to_hex,
    saturation,
    unique_hexes,
)
from .palette_roles import (
    PALETTE_SAFETY_CEILING,
    RASTER_QUANT_STEP,
    assemble_series_colors,
    raster_bar_geom,
    raster_legend_geom,
    raster_legend_group_ok,
    raster_legend_on_margin,
)


def load_rgb(path: str) -> np.ndarray:
    with Image.open(path) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGB")
        return np.asarray(rgb, dtype=np.uint8)


def image_size(path: str) -> tuple[int, int]:
    with Image.open(path) as image:
        rgb = ImageOps.exif_transpose(image)
        return int(rgb.size[0]), int(rgb.size[1])


def crop_rgb(rgb: np.ndarray, rect: list[float]) -> np.ndarray:
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    x0, y0, x1, y1 = image_crop_box(width, height, rect)
    crop = rgb[y0:y1, x0:x1]
    if crop.size == 0:
        raise ValueError("Crop is too small")
    return crop


def save_png(rgb: np.ndarray, path: str) -> tuple[int, int]:
    image = Image.fromarray(rgb, mode="RGB")
    image.save(path, format="PNG")
    return int(image.size[0]), int(image.size[1])


def render_image(path: str, output_path: str, max_side: int = 1600) -> tuple[int, int]:
    with Image.open(path) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGB")
        width, height = rgb.size
        longest = max(width, height, 1)
        if longest > max_side:
            scale = max_side / float(longest)
            size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
            rgb = rgb.resize(size, Image.Resampling.LANCZOS)
        rgb.save(output_path, format="PNG")
        return int(rgb.size[0]), int(rgb.size[1])


def _border_bg(rgb: np.ndarray) -> tuple[int, int, int]:
    h, w = rgb.shape[:2]
    band = max(1, min(8, h // 20, w // 20))
    parts = [rgb[:band, :, :], rgb[-band:, :, :], rgb[:, :band, :], rgb[:, -band:, :]]
    border = np.concatenate([p.reshape(-1, 3) for p in parts], axis=0)
    med = np.median(border, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def _is_blend(color: tuple[int, int, int], bg: tuple[int, int, int], inks: list[tuple[int, int, int]]) -> bool:
    c = np.array(color, dtype=np.float64)
    b = np.array(bg, dtype=np.float64)
    for ink in inks:
        i = np.array(ink, dtype=np.float64)
        delta = i - b
        denom = float(np.dot(delta, delta))
        if denom < 25.0:
            continue
        t = float(np.dot(c - b, delta) / denom)
        if t < 0.12 or t > 0.88:
            continue
        proj = b + t * delta
        if float(np.linalg.norm(c - proj)) <= 14.0:
            return True
    return False


def _kmeans_colors(rgb: np.ndarray, k: int = 8) -> list[tuple[tuple[int, int, int], int]]:
    h, w = rgb.shape[:2]
    pixels = rgb.reshape(-1, 3).astype(np.float32)
    if pixels.shape[0] > 40000:
        rng = np.random.RandomState(0)
        idx = rng.choice(pixels.shape[0], 40000, replace=False)
        sample = pixels[idx]
    else:
        sample = pixels
    k_use = int(min(k, max(2, sample.shape[0] // 20)))
    cv2.setRNGSeed(0)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 25, 0.5)
    _compact, labels, centers = cv2.kmeans(sample, k_use, None, criteria, 4, cv2.KMEANS_PP_CENTERS)
    centers_i = np.clip(np.rint(centers), 0, 255).astype(np.int32)
    counts = np.bincount(labels.flatten(), minlength=k_use)
    pairs = []
    for i, center in enumerate(centers_i):
        pairs.append(((int(center[0]), int(center[1]), int(center[2])), int(counts[i])))
    pairs.sort(key=lambda item: item[1], reverse=True)
    return pairs


def _hough_lines(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    h, w = gray.shape[:2]
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 40, 120)
    min_len = int(0.32 * min(h, w))
    raw = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=max(30, min(h, w) // 8), minLineLength=min_len, maxLineGap=6)
    if raw is None:
        return []
    lines = []
    for item in raw.reshape(-1, 4):
        x1, y1, x2, y2 = (int(item[0]), int(item[1]), int(item[2]), int(item[3]))
        lines.append((x1, y1, x2, y2))
    return lines


def _aligned(line: tuple[int, int, int, int], tol: int = 4) -> str | None:
    x1, y1, x2, y2 = line
    if abs(y2 - y1) <= tol and abs(x2 - x1) >= 12:
        return "h"
    if abs(x2 - x1) <= tol and abs(y2 - y1) >= 12:
        return "v"
    return None


def _line_len(line: tuple[int, int, int, int]) -> float:
    return float(((line[2] - line[0]) ** 2 + (line[3] - line[1]) ** 2) ** 0.5)


def _text_heights(gray: np.ndarray, bg: tuple[int, int, int]) -> list[int]:
    h, w = gray.shape[:2]
    if is_dark(bg, 80):
        mask = (gray > 180).astype(np.uint8) * 255
    else:
        mask = (gray < 90).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    heights: list[int] = []
    for i in range(1, n_labels):
        x, y, bw, bh, area = stats[i]
        if area < 8 or area > 0.05 * h * w:
            continue
        if bh < 4 or bh > 0.18 * h:
            continue
        aspect = bw / float(max(bh, 1))
        if aspect < 0.12 or aspect > 8.0:
            continue
        if bw > 0.45 * w and bh < 0.04 * h:
            continue
        heights.append(int(bh))
    return heights


def _stroke_width_px(mask: np.ndarray) -> float | None:
    if int(mask.sum()) < 20:
        return None
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    vals = dist[mask > 0]
    if vals.size == 0:
        return None
    radius = float(np.percentile(vals, 70))
    width = radius * 2.0
    if width < 0.4:
        return None
    return width


def _blob_kind(bw: int, bh: int, area: int) -> str:
    mx = max(int(bw), int(bh))
    mn = min(int(bw), int(bh))
    if mx <= 0 or mn <= 0 or int(area) < 8:
        return "noise"
    aspect = mx / float(mn)
    solidity = int(area) / float(max(bw * bh, 1))
    if mn >= 8 and aspect <= 6.5 and solidity >= 0.70:
        return "fill_blob"
    if mx >= 18 and (aspect >= 5.0 or (solidity <= 0.45 and mx >= 24)):
        return "line"
    if mx >= 18 and mn <= 14:
        return "line"
    return "other"


def _line_like_mask(series_mask: np.ndarray) -> np.ndarray | None:
    n_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(series_mask, connectivity=8)
    out = np.zeros_like(series_mask)
    found = False
    for i in range(1, n_labels):
        _x, _y, bw, bh, area = stats[i]
        if _blob_kind(int(bw), int(bh), int(area)) == "line":
            out[labels == i] = 255
            found = True
    if not found:
        return None
    return out


def _sample_line_rgb(work: np.ndarray, line: tuple[int, int, int, int]) -> tuple[int, int, int] | None:
    h, w = work.shape[:2]
    x1, y1, x2, y2 = line
    n = max(int(_line_len(line)), 2)
    xs = np.clip(np.linspace(x1, x2, n).astype(int), 0, w - 1)
    ys = np.clip(np.linspace(y1, y2, n).astype(int), 0, h - 1)
    cols = work[ys, xs]
    if cols.size == 0:
        return None
    med = np.median(cols, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def _interior_gray_grid(
    work: np.ndarray,
    h_lines: list[tuple[int, int, int, int]],
    v_lines: list[tuple[int, int, int, int]],
    frame: tuple[int, int, int, int] | None,
    h: int,
    w: int,
) -> bool:
    if frame is None:
        return False
    fx0, fy0, fx1, fy1 = frame
    fw = max(fx1 - fx0, 1)
    fh = max(fy1 - fy0, 1)

    def _regular(vals: list[float]) -> bool:
        if len(vals) < 3:
            return False
        vals = sorted(vals)
        uniq = [vals[0]]
        for value in vals[1:]:
            if abs(value - uniq[-1]) > 4:
                uniq.append(value)
        if len(uniq) < 3:
            return False
        gaps = [uniq[i + 1] - uniq[i] for i in range(len(uniq) - 1)]
        med = float(np.median(gaps))
        if med < 8:
            return False
        return sum(1 for gap in gaps if abs(gap - med) <= 0.45 * med) >= 2

    hi: list[float] = []
    for ln in h_lines:
        y = 0.5 * (ln[1] + ln[3])
        if abs(y - fy0) <= max(4, 0.04 * fh) or abs(y - fy1) <= max(4, 0.04 * fh):
            continue
        if y <= fy0 or y >= fy1:
            continue
        rgb = _sample_line_rgb(work, ln)
        if rgb is None or not is_gray(rgb, 26):
            continue
        if not (130 <= int(np.mean(rgb)) <= 230):
            continue
        hi.append(float(y))
    vi: list[float] = []
    for ln in v_lines:
        x = 0.5 * (ln[0] + ln[2])
        if abs(x - fx0) <= max(4, 0.04 * fw) or abs(x - fx1) <= max(4, 0.04 * fw):
            continue
        if x <= fx0 or x >= fx1:
            continue
        rgb = _sample_line_rgb(work, ln)
        if rgb is None or not is_gray(rgb, 26):
            continue
        if not (130 <= int(np.mean(rgb)) <= 230):
            continue
        vi.append(float(x))
    return _regular(hi) or _regular(vi)


def _norm_bbox(h: int, w: int, x0: float, y0: float, x1: float, y1: float, page_rect: list[float] | None) -> list[float]:
    if page_rect is None:
        return [x0 / max(w, 1), y0 / max(h, 1), max(x1 - x0, 1) / max(w, 1), max(y1 - y0, 1) / max(h, 1)]
    px, py, pw, ph = page_rect
    return [
        px + (x0 / max(w, 1)) * pw,
        py + (y0 / max(h, 1)) * ph,
        (max(x1 - x0, 1) / max(w, 1)) * pw,
        (max(y1 - y0, 1) / max(h, 1)) * ph,
    ]


def _ink_mask(work: np.ndarray, color: tuple[int, int, int], spread: int = 18) -> np.ndarray:
    lower = np.array([max(0, int(c) - spread) for c in color], dtype=np.uint8)
    upper = np.array([min(255, int(c) + spread) for c in color], dtype=np.uint8)
    return cv2.inRange(work, lower, upper)


def _cluster_keep(
    work: np.ndarray,
    color: tuple[int, int, int],
    bg: tuple[int, int, int],
    inks: list[tuple[int, int, int]],
    axis_mask: np.ndarray,
    plot_axes: bool,
) -> str:
    if color_dist(color, bg) < 22:
        return "drop"
    if is_near_white(color):
        return "drop"
    mask = _ink_mask(work, color, 18)
    if axis_mask is not None and int(axis_mask.max()) > 0:
        axis_dilated = cv2.dilate(axis_mask, np.ones((5, 5), np.uint8), iterations=1)
        remain = cv2.bitwise_and(mask, cv2.bitwise_not(axis_dilated))
    else:
        remain = mask
    pix = int(np.count_nonzero(remain))
    line_like = False
    fill_like = False
    if pix >= 8:
        line_like = _line_like_mask(remain) is not None
        _n_labels, _labels, stats, _c = cv2.connectedComponentsWithStats(remain, connectivity=8)
        for i in range(1, _n_labels):
            if _blob_kind(int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4])) == "fill_blob":
                fill_like = True
                break
    mean_v = int(np.mean(color))
    light_gray = is_gray(color, 26) and saturation(color) < 0.12 and 130 <= mean_v <= 230
    if light_gray and not fill_like:
        return "grid"
    dark_low = is_dark(color, 70) and saturation(color) < 0.18
    gray_low = is_gray(color, 26) and saturation(color) < 0.12
    blend = _is_blend(color, bg, inks[:6])
    if blend and not ((dark_low or gray_low) and line_like):
        return "drop"
    if dark_low or gray_low:
        if line_like:
            return "series"
        if fill_like and plot_axes:
            return "series"
        return "drop"
    if pix >= 40 or line_like or fill_like:
        return "series"
    return "drop"


def _core_median(work: np.ndarray, mask: np.ndarray) -> tuple[int, int, int] | None:
    if int(np.count_nonzero(mask)) < 6:
        return None
    eroded = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=1)
    use = eroded if int(np.count_nonzero(eroded)) >= 6 else mask
    pix = work[use > 0]
    if pix.size == 0:
        return None
    med = np.median(pix.reshape(-1, 3), axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def _not_bg_mask(work: np.ndarray, bg: tuple[int, int, int], dist_min: float = 22.0) -> np.ndarray:
    delta = work.astype(np.float32) - np.array(bg, dtype=np.float32)
    dist = np.sqrt(np.sum(delta * delta, axis=2))
    return (dist >= float(dist_min)).astype(np.uint8) * 255


def _iter_quant_masks(work: np.ndarray, bg: tuple[int, int, int], step: int = RASTER_QUANT_STEP):
    h, w = work.shape[:2]
    quant = (work.astype(np.int32) // step) * step
    keys, inverse = np.unique(quant.reshape(-1, 3), axis=0, return_inverse=True)
    inverse = inverse.reshape(h, w)
    for index, key in enumerate(keys):
        rgb = (int(key[0]), int(key[1]), int(key[2]))
        if is_near_white(rgb) or color_dist(rgb, bg) < 22:
            continue
        mask = (inverse == index).astype(np.uint8) * 255
        if int(np.count_nonzero(mask)) < 8:
            continue
        yield rgb, mask


def _component_recs(work: np.ndarray, mask: np.ndarray, h: int, w: int) -> list[dict[str, Any]]:
    n_labels, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    recs: list[dict[str, Any]] = []
    img_area = float(max(h * w, 1))
    for i in range(1, n_labels):
        x, y, bw, bh, area = (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4]))
        if area < 8:
            continue
        solidity = area / float(max(bw * bh, 1))
        area_frac = area / img_area
        kind = _blob_kind(bw, bh, area)
        comp = (labels == i).astype(np.uint8) * 255
        core = _core_median(work, comp)
        if core is None:
            continue
        recs.append(
            {
                "x": x,
                "y": y,
                "w": bw,
                "h": bh,
                "area": area,
                "area_frac": area_frac,
                "solidity": solidity,
                "kind": kind,
                "rgb": core,
                "hex": rgb_to_hex(core),
                "cx": float(cents[i][0]),
                "cy": float(cents[i][1]),
                "mask": comp,
            }
        )
    return recs


def _legend_group(recs: list[dict[str, Any]], h: int, w: int) -> list[dict[str, Any]]:
    patches = [
        rec
        for rec in recs
        if rec["kind"] == "fill_blob" and raster_legend_geom(rec["w"], rec["h"], rec["area_frac"], rec["solidity"])
    ]
    if len(patches) < 2:
        return []

    # Pick the dominant repeated swatch geometry. Text glyph blobs can be
    # aligned with the swatches and pass the broad fill-blob test, but their
    # smaller geometry and lower solidity form a separate group. No color
    # threshold belongs here: a genuine black/gray/navy swatch must compete
    # on the same geometry/solidity evidence as every other swatch.
    def compatible(anchor: dict[str, Any], item: dict[str, Any]) -> bool:
        anchor_area = max(float(anchor["w"] * anchor["h"]), 1.0)
        item_area = max(float(item["w"] * item["h"]), 1.0)
        area_ratio = item_area / anchor_area
        if area_ratio < 0.70 or area_ratio > (1.0 / 0.70):
            return False
        for key in ("w", "h"):
            base = max(float(anchor[key]), 1.0)
            if abs(float(item[key]) - base) > max(2.0, 0.18 * base):
                return False
        return abs(float(item["solidity"]) - float(anchor["solidity"])) <= 0.15

    # Sort for deterministic processing, then use bounded geometry buckets to
    # keep several active support groups. A single contiguous run treated
    # compatibility as transitive: an incompatible interior blob could split
    # two otherwise compatible edge swatches. The bucket sweep checks the
    # actual anchor compatibility for only neighboring geometry bins, so
    # dense noise is not an anchor-vs-all O(p²) scan and no candidate is
    # truncated before the evidence checks.
    ordered = sorted(
        patches,
        key=lambda item: (
            float(item["w"] * item["h"]),
            float(item["w"]),
            float(item["h"]),
            float(item["solidity"]),
            float(item["cy"]),
            float(item["cx"]),
            int(item["x"]),
            int(item["y"]),
            str(item.get("hex", "")),
        ),
    )

    bucket_w = 2.0
    bucket_h = 2.0
    bucket_solidity = 0.05

    def bucket_key(item: dict[str, Any]) -> tuple[int, int, int]:
        return (
            int(np.floor(float(item["w"]) / bucket_w)),
            int(np.floor(float(item["h"]) / bucket_h)),
            int(np.floor(float(item["solidity"]) / bucket_solidity)),
        )

    def bucket_span(value: float, step: float, tolerance: float) -> range:
        # Add one bucket on both sides for boundary-rounding, then let the
        # exact compatible() check decide membership.
        margin = float(tolerance) + step
        low = max(0.0, float(value) - margin)
        high = float(value) + margin
        return range(int(np.floor(low / step)), int(np.floor(high / step)) + 1)

    groups: list[dict[str, Any]] = []
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for item in ordered:
        item_w = float(item["w"])
        item_h = float(item["h"])
        item_solidity = float(item["solidity"])
        nearby_ids: set[int] = set()
        for wb in bucket_span(item_w, bucket_w, max(2.0, 0.18 * item_w)):
            for hb in bucket_span(item_h, bucket_h, max(2.0, 0.18 * item_h)):
                for sb in bucket_span(item_solidity, bucket_solidity, 0.15):
                    nearby_ids.update(buckets.get((wb, hb, sb), ()))
        matches = [
            group_id
            for group_id in sorted(nearby_ids)
            if compatible(groups[group_id]["anchor"], item)
        ]
        if matches:
            # A bridge candidate may support more than one non-transitive
            # geometry group. Keep those supports separate so margin/rank
            # selection can choose the spatially valid one later.
            for group_id in matches:
                groups[group_id]["members"].append(item)
            continue
        group_id = len(groups)
        groups.append({"anchor": item, "members": [item]})
        buckets.setdefault(bucket_key(item), []).append(group_id)

    def valid_group(members: list[dict[str, Any]]) -> tuple[dict[str, Any], ...] | None:
        if len(members) < 2:
            return None
        xs = [float(item["cx"]) for item in members]
        ys = [float(item["cy"]) for item in members]
        stdx = float(np.std(xs)) if len(members) > 1 else 0.0
        stdy = float(np.std(ys)) if len(members) > 1 else 0.0
        if not raster_legend_group_ok(len(members), stdx, stdy, w, h):
            return None
        med_cx = float(np.median(xs))
        med_cy = float(np.median(ys))
        if not raster_legend_on_margin(med_cx, med_cy, w, h):
            return None
        return tuple(members)

    # Ranking occurs after the margin gate, so an interior tie cannot veto a
    # valid edge legend. Position is a deterministic final tie-break and is
    # independent of input order.
    valid_groups: list[tuple[dict[str, Any], ...]] = []
    for group in groups:
        valid = valid_group(group["members"])
        if valid is not None:
            valid_groups.append(valid)
    if not valid_groups:
        return []

    def rank(group: tuple[dict[str, Any], ...]) -> tuple[float, ...]:
        median_area = float(np.median([float(item["w"] * item["h"]) for item in group]))
        median_solidity = float(np.median([float(item["solidity"]) for item in group]))
        median_cx = float(np.median([float(item["cx"]) for item in group]))
        median_cy = float(np.median([float(item["cy"]) for item in group]))
        return (float(len(group)), median_area, median_solidity, -median_cy, -median_cx)

    similar = list(max(valid_groups, key=rank))
    similar.sort(key=lambda item: (item["cy"], item["cx"]))
    return similar


def _bar_group(recs: list[dict[str, Any]], legend: list[dict[str, Any]], h: int, w: int) -> list[dict[str, Any]]:
    legend_ids = {id(item) for item in legend}
    bars = [
        rec
        for rec in recs
        if id(rec) not in legend_ids
        and rec["kind"] == "fill_blob"
        and raster_bar_geom(rec["w"], rec["h"], rec["area_frac"], rec["solidity"])
    ]
    if len(bars) < 2:
        return []
    bottoms = [float(b["y"] + b["h"]) for b in bars]
    med_b = float(np.median(bottoms))
    aligned = [b for b in bars if abs(float(b["y"] + b["h"]) - med_b) <= max(10.0, 0.06 * h)]
    if len(aligned) < 2 or len(aligned) > 16:
        return []
    widths = [float(b["w"]) for b in aligned]
    med_w = float(np.median(widths))
    similar = [b for b in aligned if abs(float(b["w"]) - med_w) <= 0.55 * max(med_w, 1.0)]
    if len(similar) < 2:
        return []
    similar.sort(key=lambda item: item["cx"])
    return similar


def _stroke_recs(recs: list[dict[str, Any]], axis_mask: np.ndarray, bg: tuple[int, int, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in recs:
        if rec["kind"] != "line":
            continue
        rgb = rec["rgb"]
        if is_near_white(rgb):
            continue
        mean_v = int(np.mean(rgb))
        if is_gray(rgb, 26) and saturation(rgb) < 0.12 and 130 <= mean_v <= 230:
            continue
        overlap = 0
        if axis_mask is not None and int(axis_mask.max()) > 0:
            overlap = int(np.count_nonzero(cv2.bitwise_and(rec["mask"], axis_mask)))
            if overlap > 0.55 * max(rec["area"], 1):
                continue
        out.append(rec)
    return out


def _spatial_palette(work: np.ndarray, bg: tuple[int, int, int], axis_mask: np.ndarray) -> dict[str, Any]:
    h, w = work.shape[:2]
    recs: list[dict[str, Any]] = []
    saturated: list[tuple[int, int, int]] = []
    ink_mask = _not_bg_mask(work, bg)
    ink_px = int(np.count_nonzero(ink_mask))
    spatial_recs = _component_recs(work, ink_mask, h, w)
    for _qrgb, mask in _iter_quant_masks(work, bg):
        recs.extend(_component_recs(work, mask, h, w))
        if saturation(_qrgb) > 0.18:
            saturated.append(_qrgb)
    ink_frac = ink_px / float(max(h * w, 1))
    legend = _legend_group(spatial_recs, h, w)
    if legend and ink_frac >= 0.18:
        if all(is_gray(item["rgb"], 28) and saturation(item["rgb"]) < 0.15 for item in legend):
            legend = []
    strokes = _stroke_recs(recs, axis_mask, bg)
    bars = _bar_group(recs, legend, h, w)
    legend_hex = [item["hex"] for item in legend]
    mark_hex: list[str] = []
    if not legend:
        cores = [item["rgb"] for item in strokes + bars]
        kept_marks: list[dict[str, Any]] = []
        for item in strokes + bars:
            rgb = item["rgb"]
            others = [c for c in cores if c != rgb]
            if others and _is_blend(rgb, bg, others):
                continue
            if is_near_white(rgb, 28):
                continue
            mark_hex.append(item["hex"])
            kept_marks.append(item)
        strokes = [item for item in kept_marks if item in strokes]
        bars = [item for item in kept_marks if item in bars]
    else:
        strokes = []
        bars = []
    structured = bool(legend) or bool(strokes) or bool(bars)
    sat_n = sum(1 for rec in legend + strokes + bars if saturation(rec["rgb"]) > 0.18)
    gray_only = structured and sat_n == 0
    return {
        "legend": legend,
        "strokes": strokes,
        "bars": bars,
        "legend_hex": legend_hex,
        "mark_hex": mark_hex,
        "structured": structured,
        "ink_frac": ink_frac,
        "sat_n": sat_n,
        "gray_only": gray_only,
        "n_recs": len(recs),
        "saturated_inks": saturated,
    }


def analyze_raster(rgb: np.ndarray, page_rect: list[float] | None = None) -> dict[str, Any]:
    signals = empty_signals()
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("Raster crop must be RGB")
    h, w = int(rgb.shape[0]), int(rgb.shape[1])
    if h < 8 or w < 8:
        raise ValueError("Crop is too small")
    work = rgb[:, :, :3]
    if max(h, w) > 1400:
        scale = 1400.0 / float(max(h, w))
        work = cv2.resize(work, (max(8, int(w * scale)), max(8, int(h * scale))), interpolation=cv2.INTER_AREA)
        h, w = int(work.shape[0]), int(work.shape[1])
    bg = _border_bg(work)
    signals["background_color"] = rgb_to_hex(bg)
    signals["methods"]["background_color"] = "raster_estimate"
    signals["evidence"]["background_color"].append(
        evidence_item("raster_blob", _norm_bbox(h, w, 0, 0, w, min(6, h), page_rect), "border median color")
    )

    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY)
    lines = _hough_lines(gray)
    aligned = []
    for line in lines:
        kind = _aligned(line)
        if kind:
            aligned.append((kind, line))
    h_lines = [ln for kind, ln in aligned if kind == "h"]
    v_lines = [ln for kind, ln in aligned if kind == "v"]
    plot_axes = len(h_lines) >= 1 and len(v_lines) >= 1
    frame: tuple[int, int, int, int] | None = None
    axis_mask = np.zeros((h, w), dtype=np.uint8)
    if aligned:
        xs: list[int] = []
        ys: list[int] = []
        for _kind, ln in aligned:
            xs.extend([ln[0], ln[2]])
            ys.extend([ln[1], ln[3]])
        frame = (min(xs), min(ys), max(xs), max(ys))
        fw = max(frame[2] - frame[0], 1)
        fh = max(frame[3] - frame[1], 1)
        top = any(abs(((ln[1] + ln[3]) / 2) - frame[1]) <= max(4, 0.08 * fh) and _line_len(ln) >= 0.45 * fw for ln in h_lines)
        right = any(abs(((ln[0] + ln[2]) / 2) - frame[2]) <= max(4, 0.08 * fw) and _line_len(ln) >= 0.45 * fh for ln in v_lines)
        signals["show_top_spine"] = bool(top)
        signals["show_right_spine"] = bool(right)
        signals["methods"]["show_top_spine"] = "raster_estimate"
        signals["methods"]["show_right_spine"] = "raster_estimate"
        fb = _norm_bbox(h, w, frame[0], frame[1], frame[2], frame[3], page_rect)
        signals["evidence"]["show_top_spine"].append(evidence_item("raster_blob", fb, "hough top edge"))
        signals["evidence"]["show_right_spine"].append(evidence_item("raster_blob", fb, "hough right edge"))
        for _kind, ln in aligned:
            cv2.line(axis_mask, (ln[0], ln[1]), (ln[2], ln[3]), 255, 2)
        axis_w = _stroke_width_px(axis_mask)
        if axis_w is not None:
            signals["axis_line_width_frac"] = axis_w / float(w)
            signals["axis_line_width"] = (axis_w / float(w)) * RASTER_WIDTH_REF_PT
            signals["methods"]["axis_line_width"] = "raster_estimate"
            signals["evidence"]["axis_line_width"].append(
                evidence_item("raster_blob", fb, f"raster axis width {axis_w:.2f}px; resolved vs {RASTER_WIDTH_REF_PT:.0f}pt crop reference")
            )
            signals["warnings"].append(
                f"Raster axis/line widths are crop-relative, resolved with a {RASTER_WIDTH_REF_PT:.0f} pt width reference, not assumed DPI."
            )

    spatial = _spatial_palette(work, bg, axis_mask)
    clusters = _kmeans_colors(work, k=min(16, max(8, spatial.get("sat_n", 0) + 6)))
    heights = _text_heights(gray, bg)
    unique_hues = 0
    for color, _count in clusters:
        if saturation(color) > 0.18 and color_dist(color, bg) > 30:
            unique_hues += 1
    photo_like = (not plot_axes) and unique_hues >= 5 and len(heights) < 12 and not spatial["legend"]
    if (not spatial["legend"]) and (not spatial["bars"]) and spatial["ink_frac"] >= 0.18 and spatial["sat_n"] <= 1:
        photo_like = True
    if spatial.get("gray_only") and spatial["ink_frac"] >= 0.18 and not spatial["legend"]:
        photo_like = True
    if unique_hues >= 8 and spatial["ink_frac"] >= 0.22 and not spatial["legend"]:
        photo_like = True
    if photo_like:
        signals["photo_like"] = True
        signals["plot_like"] = False
        signals["text_like"] = False
        signals["warnings"].append("Raster looks photographic; series/axes/fonts not taken from photo hues")
        return signals

    inks = [c for c, n in clusters if color_dist(c, bg) > 28]
    series: list[str] = []
    series_rgb: list[tuple[int, int, int]] = []
    gray_grid: list[tuple[int, int, int]] = []
    if spatial["structured"]:
        ordered, truncated = assemble_series_colors(spatial["legend_hex"], spatial["mark_hex"], tol=18.0)
        series = list(ordered)
        if not spatial["legend"]:
            cores = [hex_to_rgb(h) for h in series]
            extra: list[str] = []
            for color, _count in clusters:
                if cores and any(color_dist(color, core) <= 18.0 for core in cores):
                    continue
                role = _cluster_keep(work, color, bg, inks, axis_mask, plot_axes)
                if role == "grid":
                    gray_grid.append(color)
                    continue
                if role == "series":
                    extra.append(rgb_to_hex(color))
            if extra:
                series, more_trunc = assemble_series_colors(series, extra, tol=18.0)
                truncated = truncated or more_trunc
        if truncated:
            signals["warnings"].append(
                f"Series palette truncated at documented safety ceiling {PALETTE_SAFETY_CEILING}"
            )
        lookup = {item["hex"]: item["rgb"] for item in spatial["legend"] + spatial["strokes"] + spatial["bars"]}
        for hex_value in series:
            if hex_value not in lookup:
                lookup[hex_value] = hex_to_rgb(hex_value)
        series_rgb = [lookup[h] for h in series if h in lookup]
    else:
        for color, _count in clusters:
            role = _cluster_keep(work, color, bg, inks, axis_mask, plot_axes)
            if role == "grid":
                gray_grid.append(color)
                continue
            if role != "series":
                continue
            series.append(rgb_to_hex(color))
            series_rgb.append(color)
        series = unique_hexes(series, tol=18.0, limit=None)

    text_like = len(heights) >= 18 and len(series) <= 1 and not plot_axes
    plot_like = bool(plot_axes or (len(series) >= 2 and not photo_like and len(heights) < 80))

    if text_like and not plot_like:
        signals["text_like"] = True
        signals["plot_like"] = False
        if heights:
            signals["text_height_px"] = float(np.median(heights))
        signals["warnings"].append("Raster looks text-like; chart tokens left unknown")
        return signals

    signals["plot_like"] = bool(plot_like)
    signals["photo_like"] = bool(photo_like)
    signals["text_like"] = bool(text_like)

    if series and not photo_like:
        if spatial["legend"]:
            ordered = list(series)
        else:
            ys = []
            for color in series_rgb:
                lower = np.array([max(0, c - 18) for c in color], dtype=np.uint8)
                upper = np.array([min(255, c + 18) for c in color], dtype=np.uint8)
                mask = cv2.inRange(work, lower, upper)
                ys.append((int(np.argmax(mask.sum(axis=1)) if mask.sum() else 0), rgb_to_hex(color)))
            ys.sort()
            ordered = unique_hexes([hex_value for _, hex_value in ys], tol=18.0, limit=None)
        if len(ordered) > PALETTE_SAFETY_CEILING:
            ordered = ordered[:PALETTE_SAFETY_CEILING]
            signals["warnings"].append(
                f"Series palette truncated at documented safety ceiling {PALETTE_SAFETY_CEILING}"
            )
        signals["series_colors"] = ordered
        if spatial["legend"]:
            signals["series_order_reason"] = "Raster legend patches (core samples), top-to-bottom then leftover series marks"
        else:
            signals["series_order_reason"] = "No legend patches found; raster series ordered by vertical mass (top-to-bottom)"
        signals["methods"]["series_colors"] = "raster_estimate"
        evidence_detail = (
            "legend-patch core samples, anti-alias excluded"
            if spatial["legend"]
            else "clustered non-background inks, anti-alias excluded"
        )
        signals["evidence"]["series_colors"].append(
            evidence_item("raster_blob", _norm_bbox(h, w, 0, 0, w, h, page_rect), evidence_detail)
        )
        for hex_value in ordered:
            signals["evidence"]["series_colors"].append(
                evidence_item("raster_blob", _norm_bbox(h, w, 0, 0, w, h, page_rect), f"series color {hex_value}")
            )
        signals["warnings"].append(signals["series_order_reason"])
        series_mask = np.zeros((h, w), dtype=np.uint8)
        for color in series_rgb[:4]:
            lower = np.array([max(0, c - 20) for c in color], dtype=np.uint8)
            upper = np.array([min(255, c + 20) for c in color], dtype=np.uint8)
            color_mask = cv2.inRange(work, lower, upper)
            if axis_mask is not None and int(axis_mask.max()) > 0:
                overlap = cv2.bitwise_and(color_mask, axis_mask)
                if int(np.count_nonzero(overlap)) > 0.5 * max(int(np.count_nonzero(color_mask)), 1):
                    continue
            series_mask = cv2.bitwise_or(series_mask, color_mask)
        line_mask = _line_like_mask(series_mask)
        if line_mask is not None:
            line_mask = cv2.bitwise_and(line_mask, cv2.bitwise_not(axis_mask))
            lw = _stroke_width_px(line_mask)
            if lw is not None:
                signals["line_width_frac"] = lw / float(w)
                signals["line_width"] = (lw / float(w)) * RASTER_WIDTH_REF_PT
                signals["methods"]["line_width"] = "raster_estimate"
                signals["evidence"]["line_width"].append(
                    evidence_item("raster_blob", _norm_bbox(h, w, 0, 0, w, h, page_rect), f"raster series width {lw:.2f}px vs {RASTER_WIDTH_REF_PT:.0f}pt reference")
                )

    if plot_axes and _interior_gray_grid(work, h_lines, v_lines, frame, h, w):
        signals["gridline_visible"] = True
        if gray_grid:
            signals["gridline_color"] = rgb_to_hex(gray_grid[0])
            signals["methods"]["gridline_color"] = "raster_estimate"
        signals["methods"]["gridline_visible"] = "raster_estimate"
        signals["evidence"]["gridline_visible"].append(
            evidence_item("raster_blob", _norm_bbox(h, w, 0, 0, w, h, page_rect), "interior light-gray aligned strokes")
        )
    elif plot_axes:
        signals["gridline_visible"] = False
        signals["methods"]["gridline_visible"] = "raster_estimate"

    if heights and plot_like:
        heights_sorted = sorted(heights)
        small = float(np.median(heights_sorted[: max(1, len(heights_sorted) // 3)]))
        large = float(np.median(heights_sorted[-max(1, len(heights_sorted) // 3) :]))
        mid = float(np.median(heights_sorted))
        base = RASTER_BASE_FONT_PT
        ratio_large = large / max(small, 1.0)
        ratio_mid = mid / max(small, 1.0)
        signals["tick_label_size_pt"] = base
        signals["axis_label_size_pt"] = round(base * min(max(ratio_mid, 1.0), 1.8), 2)
        signals["font_size_pt"] = round(base * min(max(ratio_large, 1.05), 2.2), 2)
        signals["legend_size_pt"] = round(base * min(max(ratio_mid, 1.0), 1.5), 2)
        signals["text_height_px"] = small
        for key in ("font_size_pt", "axis_label_size_pt", "tick_label_size_pt", "legend_size_pt"):
            signals["methods"][key] = "raster_estimate"
            signals["evidence"][key].append(
                evidence_item(
                    "raster_blob",
                    _norm_bbox(h, w, 0, 0, w, h, page_rect),
                    f"glyph-like box ratios vs {RASTER_BASE_FONT_PT:.0f} pt reference; not native PDF points",
                )
            )
        signals["warnings"].append(
            f"Raster typography is relative box height only; resolved against {RASTER_BASE_FONT_PT:.0f} pt reference. Font identity unknown."
        )
        ink = (gray < 80).astype(np.uint8)
        if ink.mean() > 0.002:
            ys, xs = np.where(ink > 0)
            if len(xs):
                signals["label_color"] = rgb_to_hex((int(np.median(work[ys, xs, 0])), int(np.median(work[ys, xs, 1])), int(np.median(work[ys, xs, 2]))))
                signals["methods"]["label_color"] = "raster_estimate"
                signals["evidence"]["label_color"].append(
                    evidence_item("raster_blob", _norm_bbox(h, w, 0, 0, w, h, page_rect), "dark glyph-like pixels")
                )

    nlabels, labels, stats, _c = cv2.connectedComponentsWithStats((gray < 70).astype(np.uint8) * 255)
    compact = 0
    for i in range(1, nlabels):
        _x, _y, bw, bh, area = stats[i]
        mx = max(bw, bh)
        mn = min(bw, bh)
        if 3 <= mx <= 18 and mn > 0 and mx / float(mn) <= 1.35 and 4 <= area <= 220:
            compact += 1
    if compact >= 6 and plot_like:
        signals["point_shape"] = "circle"
        signals["methods"]["point_shape"] = "raster_estimate"
        signals["evidence"]["point_shape"].append(
            evidence_item("raster_blob", _norm_bbox(h, w, 0, 0, w, h, page_rect), "compact roundish blobs")
        )

    return signals

