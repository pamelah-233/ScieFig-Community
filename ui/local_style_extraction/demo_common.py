from __future__ import annotations

import copy
import os
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = "sciefig.local-style-demo.v1"
DEFAULT_NAME = "Untitled local style"

MIN_KEYS = (
    "series_colors",
    "background_color",
    "label_color",
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

# Optional additions keep existing v1 drafts readable.
EXTRA_KEYS = ("font_weight", "bar_fill_color", "bar_edge_color", "bar_line_width",
              "bar_use_series_colors", "bar_width_fraction", "point_line_width",
              "sem_line_width", "sem_cap_size", "sem_cap_thick", "bracket_line_width",
              "tick_width", "tick_length", "x_tick_length", "axes_box_aspect", "x_tick_rotation")
STYLE_KEYS = MIN_KEYS + EXTRA_KEYS

TOKEN_UNITS = {
    "series_colors": "hex_list",
    "background_color": "hex",
    "label_color": "hex",
    "font_family": "family",
    "font_size_pt": "pt",
    "axis_label_size_pt": "pt",
    "tick_label_size_pt": "pt",
    "legend_size_pt": "pt",
    "line_width": "pt",
    "axis_line_width": "pt",
    "point_size": "pt",
    "point_shape": "name",
    "show_top_spine": "bool",
    "show_right_spine": "bool",
    "gridline_visible": "bool",
    "gridline_color": "hex",
}
TOKEN_UNITS.update({key: "pt" for key in EXTRA_KEYS})
TOKEN_UNITS.update(font_weight="weight", bar_fill_color="hex", bar_edge_color="hex", axes_box_aspect="ratio", x_tick_rotation="degrees",
                   bar_use_series_colors="bool", bar_width_fraction="fraction")

NEUTRAL_RESOLVED = {
    "series_colors": ["#2F6DB3", "#D97B2A", "#3A9A6A", "#C44B4B", "#7A5C9E", "#6B6B6B"],
    "background_color": "#FFFFFF",
    "label_color": "#222222",
    "font_family": "DejaVu Sans",
    "font_size_pt": 10.0,
    "axis_label_size_pt": 9.0,
    "tick_label_size_pt": 8.0,
    "legend_size_pt": 8.0,
    "line_width": 1.25,
    "axis_line_width": 0.8,
    "point_size": 8.0,
    "point_shape": "circle",
    "show_top_spine": False,
    "show_right_spine": False,
    "gridline_visible": False,
    "gridline_color": "#D0D0D0",
}
NEUTRAL_RESOLVED.update(font_weight="normal", bar_fill_color="#FFFFFF", bar_edge_color="#222222",
                        bar_line_width=.8, bar_use_series_colors=True, bar_width_fraction=.58,
                        point_line_width=.35, sem_line_width=.8, sem_cap_size=3., sem_cap_thick=.8,
                        bracket_line_width=.7, tick_width=.8, tick_length=3., x_tick_length=0., axes_box_aspect=None, x_tick_rotation=0.)

RASTER_BASE_FONT_PT = 10.0
RASTER_WIDTH_REF_PT = 100.0
PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PRODUCTION_UNSUPPORTED_KEYS = ()


def abs_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def source_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    raise ValueError(f"Unsupported source type: {suffix or 'unknown'}")


def ensure_rect(rect: Any) -> list[float]:
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise ValueError("Invalid rect")
    try:
        x, y, w, h = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid rect") from exc
    if w <= 0 or h <= 0:
        raise ValueError("Invalid rect")
    return [x, y, w, h]


def clamp_rect(rect: list[float]) -> list[float]:
    x, y, w, h = ensure_rect(rect)
    x = min(max(x, 0.0), 0.999)
    y = min(max(y, 0.0), 0.999)
    w = min(max(w, 1e-4), 1.0 - x)
    h = min(max(h, 1e-4), 1.0 - y)
    if w <= 0 or h <= 0:
        raise ValueError("Invalid rect")
    return [x, y, w, h]


def rgb_to_hex(rgb: tuple[int, int, int] | list[int]) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value).strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) != 6:
        raise ValueError(f"Invalid hex color: {value}")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _channel8(value: float) -> int:
    v = float(value)
    if v < 0:
        v = 0.0
    if v <= 1.0:
        return int(round(v * 255.0))
    return max(0, min(255, int(round(v))))


def color_to_rgb(color: Any) -> tuple[int, int, int] | None:
    if color is None:
        return None
    if isinstance(color, str):
        try:
            return hex_to_rgb(color)
        except ValueError:
            return None
    if isinstance(color, (int, float)) and not isinstance(color, bool):
        if isinstance(color, int) and color > 1:
            return (color >> 16) & 255, (color >> 8) & 255, color & 255
        v = _channel8(float(color))
        return v, v, v
    if isinstance(color, (list, tuple)):
        if len(color) == 1:
            v = _channel8(float(color[0]))
            return v, v, v
        if len(color) == 3:
            return _channel8(color[0]), _channel8(color[1]), _channel8(color[2])
        if len(color) == 4:
            c, m, y, k = (float(color[0]), float(color[1]), float(color[2]), float(color[3]))
            r = 255.0 * (1.0 - c) * (1.0 - k)
            g = 255.0 * (1.0 - m) * (1.0 - k)
            b = 255.0 * (1.0 - y) * (1.0 - k)
            return _channel8(r / 255.0 if r <= 1 else r), _channel8(g / 255.0 if g <= 1 else g), _channel8(
                b / 255.0 if b <= 1 else b
            )
    return None


def color_to_hex(color: Any) -> str | None:
    rgb = color_to_rgb(color)
    if rgb is None:
        return None
    return rgb_to_hex(rgb)


def color_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5)


def hex_dist(a: str, b: str) -> float:
    return color_dist(hex_to_rgb(a), hex_to_rgb(b))


def near_hex(a: str, b: str, tol: float = 18.0) -> bool:
    try:
        return hex_dist(a, b) <= tol
    except ValueError:
        return False


def saturation(rgb: tuple[int, int, int]) -> float:
    r, g, b = (x / 255.0 for x in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx <= 1e-9:
        return 0.0
    return (mx - mn) / mx


def is_near_white(rgb: tuple[int, int, int], tol: int = 18) -> bool:
    return rgb[0] >= 255 - tol and rgb[1] >= 255 - tol and rgb[2] >= 255 - tol


def is_dark(rgb: tuple[int, int, int], cap: int = 85) -> bool:
    return rgb[0] <= cap and rgb[1] <= cap and rgb[2] <= cap


def is_gray(rgb: tuple[int, int, int], spread: int = 22) -> bool:
    return max(rgb) - min(rgb) <= spread


def unique_hexes(values: list[str], tol: float = 16.0, limit: int | None = None) -> list[str]:
    out: list[str] = []
    for item in values:
        if not item:
            continue
        hex_value = item.upper() if item.startswith("#") else f"#{item.upper()}"
        if not any(near_hex(hex_value, existing, tol) for existing in out):
            out.append(rgb_to_hex(hex_to_rgb(hex_value)))
        if limit is not None and len(out) >= limit:
            break
    return out


def page_norm_bbox(page_rect: Any, bbox: Any) -> list[float]:
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    pw = float(page_rect.width) if hasattr(page_rect, "width") else float(page_rect[2] - page_rect[0])
    ph = float(page_rect.height) if hasattr(page_rect, "height") else float(page_rect[3] - page_rect[1])
    px0 = float(page_rect.x0) if hasattr(page_rect, "x0") else float(page_rect[0])
    py0 = float(page_rect.y0) if hasattr(page_rect, "y0") else float(page_rect[1])
    if pw <= 0 or ph <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    nx = (x0 - px0) / pw
    ny = (y0 - py0) / ph
    nw = (x1 - x0) / pw
    nh = (y1 - y0) / ph
    return [nx, ny, nw, nh]


def clip_from_norm(page_rect: Any, rect: list[float]):
    import fitz

    x, y, w, h = clamp_rect(rect)
    x0 = float(page_rect.x0) + x * float(page_rect.width)
    y0 = float(page_rect.y0) + y * float(page_rect.height)
    x1 = x0 + w * float(page_rect.width)
    y1 = y0 + h * float(page_rect.height)
    clip = fitz.Rect(x0, y0, x1, y1)
    return clip & page_rect


def image_crop_box(width: int, height: int, rect: list[float]) -> tuple[int, int, int, int]:
    x, y, w, h = clamp_rect(rect)
    x0 = int(round(x * width))
    y0 = int(round(y * height))
    x1 = int(round((x + w) * width))
    y1 = int(round((y + h) * height))
    x0 = min(max(x0, 0), max(width - 1, 0))
    y0 = min(max(y0, 0), max(height - 1, 0))
    x1 = min(max(x1, x0 + 1), width)
    y1 = min(max(y1, y0 + 1), height)
    return x0, y0, x1, y1


def evidence_item(kind: str, bbox: list[float], detail: str) -> dict[str, Any]:
    return {"kind": kind, "bbox": [float(v) for v in bbox], "detail": str(detail)}


def make_token(
    value: Any,
    unit: str,
    status: str,
    method: str,
    evidence: list[dict[str, Any]] | None = None,
    reason: str = "",
    original_value: Any = ...,
) -> dict[str, Any]:
    stored = copy.deepcopy(value)
    original = stored if original_value is ... else copy.deepcopy(original_value)
    return {
        "value": stored,
        "unit": unit,
        "status": status,
        "method": method,
        "evidence": copy.deepcopy(evidence or []),
        "reason": reason or "",
        "original_value": original,
    }


def unknown_token(key: str, reason: str, method: str = "none") -> dict[str, Any]:
    return make_token(None, TOKEN_UNITS[key], "unknown", method, [], reason, None)


def unique_run_dir(output_dir: str) -> Path:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path = root / name
    path.mkdir(parents=True, exist_ok=False)
    return path


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "item"):
        try:
            return jsonable(value.item())
        except Exception:
            return str(value)
    return value


def strip_font_name(name: str | None) -> str | None:
    if not name:
        return None
    text = str(name).strip()
    if not text:
        return None
    while True:
        plus = text.find("+")
        if plus != 6:
            break
        prefix = text[:6]
        if prefix.isalnum() and prefix == prefix.upper():
            text = text[7:]
            continue
        break
    plus = text.rfind("+")
    if plus > 0 and text[plus + 1 :].isdigit() and 1 <= len(text[plus + 1 :]) <= 3:
        text = text[:plus]
    lowered = text.replace(" ", "").lower()
    if lowered.endswith("mt") and any(word in lowered for word in ("bold", "italic")):
        text = text[:-2]
        lowered = lowered[:-2]
    for suffix in (
        "-bolditalic",
        "-boldoblique",
        "bolditalic",
        "boldoblique",
        "-italic",
        "-oblique",
        "-bold",
        "-roman",
        "-regular",
        "-medium",
        "-light",
        "italic",
        "oblique",
        "bold",
    ):
        if lowered.endswith(suffix):
            text = text[: len(text) - len(suffix)]
            if text.endswith("-"):
                text = text[:-1]
            break
    text = text.strip()
    return text or None


def empty_signals() -> dict[str, Any]:
    return {
        "native_text": False,
        "native_vectors": False,
        "native_images": False,
        "font_family": None,
        "font_size_pt": None,
        "axis_label_size_pt": None,
        "tick_label_size_pt": None,
        "legend_size_pt": None,
        "label_color": None,
        "background_color": None,
        "series_colors": None,
        "series_order_reason": "",
        "line_width": None,
        "axis_line_width": None,
        "point_size": None,
        "point_shape": None,
        "show_top_spine": None,
        "show_right_spine": None,
        "gridline_visible": None,
        "gridline_color": None,
        "plot_like": False,
        "text_like": False,
        "photo_like": False,
        "axis_frame": None,
        "text_height_px": None,
        "line_width_frac": None,
        "axis_line_width_frac": None,
        "point_size_frac": None,
        "evidence": {key: [] for key in STYLE_KEYS},
        "warnings": [],
        "methods": {},
    }

