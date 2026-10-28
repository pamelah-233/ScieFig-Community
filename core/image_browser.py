from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QImage, QImageReader, QPainter

from core.image_analysis.io_engine import (
    ImageImportResult,
    cyx_to_rgb_preview,
    normalize_to_cyx,
    read_microscopy_image,
    resolve_channel_display_colors,
)
from core.utils.image_formats import COMMON_RASTER_SUFFIXES, compound_suffix


BROWSER_IMAGE_SUFFIXES = COMMON_RASTER_SUFFIXES | {".ome.tif", ".ome.tiff", ".czi", ".nd2"}
_TIFF_THUMBNAIL_SUFFIXES = {".tif", ".tiff", ".ome.tif", ".ome.tiff"}


@dataclass(slots=True)
class BrowserImage:
    path: Path
    rgb: np.ndarray
    metadata: dict[str, object]
    channel_names: list[str]

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[1]), int(self.rgb.shape[0])


@dataclass(slots=True)
class ScaleBarState:
    visible: bool = False
    length_um: float = 50.0
    um_per_px: float | None = None
    center_x_px: float = 0.0
    line_y_px: float = 0.0
    line_color: str = "#FFFFFF"
    text_color: str = "#FFFFFF"
    line_thickness_px: float = 4.0
    font_family: str = "Arial"
    font_size_px: float = 18.0
    bold: bool = False
    italic: bool = False
    text_position: str = "above"


@dataclass(frozen=True, slots=True)
class ScaleBarLayout:
    line_rect: QRectF
    text_rect: QRectF
    bounds: QRectF
    label: str


def discover_image_files(folder: str | Path, *, recursive: bool = False) -> list[Path]:
    root = Path(folder)
    paths = (base / name for base, _dirs, names in root.walk(follow_symlinks=False) for name in names) if recursive else root.iterdir()
    return sorted(
        (
            path
            for path in paths
            if path.is_file() and compound_suffix(path) in BROWSER_IMAGE_SUFFIXES
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )


def load_browser_image(path: str | Path) -> BrowserImage:
    source = Path(path)
    result: ImageImportResult = read_microscopy_image(source)
    channel_colors = None
    if compound_suffix(source) == ".czi":
        channel_colors = resolve_channel_display_colors(
            result.channel_names,
            result.metadata.get("channel_colors"),
            channel_count=int(result.analysis_data.shape[0]),
        )
    return BrowserImage(
        path=source,
        rgb=cyx_to_rgb_preview(result.analysis_data, channel_colors=channel_colors),
        metadata=dict(result.metadata),
        channel_names=list(result.channel_names),
    )


def load_browser_thumbnail(path: str | Path, max_size: QSize = QSize(176, 132)) -> QImage:
    source = Path(path)
    suffix = compound_suffix(source)
    if suffix in {".czi", ".nd2"}:
        return thumbnail_qimage(load_browser_image(source), max_size)

    reader = QImageReader(str(source))
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid() and source_size.width() > 0 and source_size.height() > 0:
        scale = min(
            max_size.width() / source_size.width(),
            max_size.height() / source_size.height(),
            1.0,
        )
        reader.setScaledSize(
            QSize(
                max(1, int(round(source_size.width() * scale))),
                max(1, int(round(source_size.height() * scale))),
            )
        )
        thumbnail = reader.read()
        if not thumbnail.isNull():
            return thumbnail
        reader_error = reader.errorString() or "thumbnail read failed"
        invalid_size = False
    else:
        reader_error = reader.errorString() or "thumbnail size is unavailable"
        invalid_size = True

    if suffix in _TIFF_THUMBNAIL_SUFFIXES:
        return _tiff_memmap_thumbnail(source, max_size)
    if invalid_size:
        raise ValueError(f"thumbnail read failed: {reader_error}")
    raise ValueError(reader_error)


def _tiff_memmap_thumbnail(source: Path, max_size: QSize) -> QImage:
    import tifffile

    try:
        with tifffile.TiffFile(source) as tif:
            series = tif.series[0]
            axes = str(getattr(series, "axes", "") or "")
            level = 0
            levels = getattr(series, "levels", None)
            if levels and len(levels) > 1:
                smallest = levels[-1]
                smallest_axes = str(getattr(smallest, "axes", "") or axes)
                height, width = _yx_sizes(getattr(smallest, "shape", ()), smallest_axes)
                if 0 < height <= max_size.height() and 0 < width <= max_size.width():
                    level = len(levels) - 1
                    axes = smallest_axes
            raw = tifffile.memmap(source, series=0, level=level, mode="r")
            sampled, sampled_axes = _sample_thumbnail_array(raw, axes, max_size)
    except Exception as exc:
        raise ValueError("thumbnail read failed: TIFF memmap unavailable") from exc

    thumbnail = rgb_to_qimage(cyx_to_rgb_preview(normalize_to_cyx(sampled, axes=sampled_axes)))
    if thumbnail.width() > max_size.width() or thumbnail.height() > max_size.height():
        thumbnail = thumbnail.scaled(
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    if thumbnail.isNull():
        raise ValueError("thumbnail read failed: TIFF memmap unavailable")
    return thumbnail


def _yx_sizes(shape: tuple[int, ...], axes: str) -> tuple[int, int]:
    axes = str(axes or "").upper()
    if "Y" in axes and "X" in axes and len(axes) == len(shape):
        return int(shape[axes.index("Y")]), int(shape[axes.index("X")])
    if len(shape) >= 2:
        return int(shape[-2]), int(shape[-1])
    return 0, 0


def _sample_thumbnail_array(raw: np.ndarray, axes: str, max_size: QSize) -> tuple[np.ndarray, str]:
    axes = str(axes or "").upper()
    if len(axes) != int(raw.ndim):
        if raw.ndim == 2:
            axes = "YX"
        elif raw.ndim == 3 and int(raw.shape[-1]) <= 4:
            axes = "YXS"
        elif raw.ndim == 3:
            axes = "CYX"
        else:
            axes = ("Q" * (raw.ndim - 2)) + "YX"

    max_y = max(1, int(max_size.height()))
    max_x = max(1, int(max_size.width()))
    slicer: list[int | slice] = []
    kept: list[str] = []
    for index, axis in enumerate(axes):
        size = int(raw.shape[index])
        if axis == "Y":
            slicer.append(slice(None, None, max(1, size // max_y)))
            kept.append("Y")
        elif axis == "X":
            slicer.append(slice(None, None, max(1, size // max_x)))
            kept.append("X")
        elif axis in {"C", "S"}:
            slicer.append(slice(None))
            kept.append("C")
        else:
            slicer.append(0)
    return np.asarray(raw[tuple(slicer)]), "".join(kept)


def rgb_to_qimage(rgb: np.ndarray) -> QImage:
    array = np.asarray(rgb)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"Expected HxWx3 RGB data, got shape {array.shape}")
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    array = np.ascontiguousarray(array)
    height, width, _ = array.shape
    return QImage(
        array.data,
        int(width),
        int(height),
        int(array.strides[0]),
        QImage.Format.Format_RGB888,
    ).copy()


def thumbnail_qimage(image: BrowserImage, max_size: QSize = QSize(176, 132)) -> QImage:
    source = rgb_to_qimage(image.rgb)
    if source.width() <= max_size.width() and source.height() <= max_size.height():
        return source
    return source.scaled(
        max_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def scale_bar_layout(target_rect: QRectF, source_size: QSize, state: ScaleBarState) -> ScaleBarLayout:
    """Resolve source-pixel scale-bar state into target/display rectangles.

    ``center_x_px`` and ``line_y_px`` are always expressed in native source
    pixels.  A zero pair means that the default bottom-right position should
    be used; this keeps the public state defaults compatible with the original
    fixed-position renderer.
    """

    target = QRectF(target_rect)
    source_width = int(source_size.width())
    source_height = int(source_size.height())
    if not state.visible:
        return ScaleBarLayout(QRectF(), QRectF(), QRectF(), "")
    if target.width() <= 0 or target.height() <= 0 or source_width <= 0 or source_height <= 0:
        raise ValueError("scale-bar target size must be positive")

    length_um = float(state.length_um)
    um_per_px = _optional_positive_float(state.um_per_px)
    if length_um <= 0:
        raise ValueError("scale-bar length must be positive")
    if um_per_px is None:
        raise ValueError("scale-bar calibration is unavailable")

    scale_x = target.width() / float(source_width)
    scale_y = target.height() / float(source_height)
    source_bar_width = min(float(source_width), max(1.0, length_um / um_per_px))
    source_line_height = min(float(source_height), max(0.1, float(state.line_thickness_px)))
    line_width = min(target.width(), max(1.0, source_bar_width * scale_x))
    line_height = min(target.height(), max(0.1, source_line_height * scale_y))

    font = _scale_bar_font(state, scale_y)
    metrics = QFontMetricsF(font)
    label = f"{length_um:g} µm"
    text_width = min(target.width(), max(line_width, metrics.horizontalAdvance(label) + max(2.0, 4.0 * scale_x)))
    text_height = min(target.height(), max(1.0, metrics.height()))
    label_gap = max(2.0 * scale_y, line_height * 0.75)

    margin_x_source = max(16.0, source_width * 0.04)
    margin_y_source = max(16.0, source_width * 0.04)
    default_position = float(state.center_x_px) == 0.0 and float(state.line_y_px) == 0.0
    if default_position:
        line_center_x_source = float(source_width) - margin_x_source - source_bar_width / 2.0
        line_center_y_source = float(source_height) - margin_y_source - source_line_height / 2.0
    else:
        line_center_x_source = float(state.center_x_px)
        line_center_y_source = float(state.line_y_px)

    line_center_x = target.left() + line_center_x_source * scale_x
    line_center_y = target.top() + line_center_y_source * scale_y
    line_rect = QRectF(
        line_center_x - line_width / 2.0,
        line_center_y - line_height / 2.0,
        line_width,
        line_height,
    )
    text_rect = QRectF(
        line_center_x - text_width / 2.0,
        line_rect.bottom() + label_gap if state.text_position == "below" else line_rect.top() - label_gap - text_height,
        text_width,
        text_height,
    )
    bounds = line_rect.united(text_rect)

    # Keep the complete line + label group in the image even when the caller
    # supplies a position near an edge or an unusually large font.
    shift_x = 0.0
    shift_y = 0.0
    if bounds.left() < target.left():
        shift_x = target.left() - bounds.left()
    elif bounds.right() > target.right():
        shift_x = target.right() - bounds.right()
    if bounds.top() < target.top():
        shift_y = target.top() - bounds.top()
    elif bounds.bottom() > target.bottom():
        shift_y = target.bottom() - bounds.bottom()
    if shift_x or shift_y:
        line_rect.translate(shift_x, shift_y)
        text_rect.translate(shift_x, shift_y)
        bounds = line_rect.united(text_rect)

    return ScaleBarLayout(line_rect, text_rect, bounds, label)


def draw_scale_bar(
    painter: QPainter,
    target_rect: QRectF,
    source_size: QSize,
    state: ScaleBarState,
) -> ScaleBarLayout:
    """Paint one scale bar and return the exact geometry that was painted."""

    layout = scale_bar_layout(target_rect, source_size, state)
    if not state.visible:
        return layout

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(layout.line_rect, QColor(state.line_color))
    painter.setFont(_scale_bar_font(state, target_rect.height() / max(1.0, float(source_size.height()))))
    painter.setPen(QColor(state.text_color))
    painter.drawText(
        layout.text_rect,
        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
        layout.label,
    )
    painter.restore()
    return layout


def render_snapshot(
    image: BrowserImage,
    show_scale_bar: bool,
    length_um: float,
    manual_um_per_px: float | None = None,
    *,
    scale_bar_state: ScaleBarState | None = None,
) -> QImage:
    rendered = rgb_to_qimage(image.rgb)
    if not show_scale_bar:
        return rendered

    if scale_bar_state is None:
        um_per_px = _resolve_um_per_px(image.metadata, manual_um_per_px)
        state = ScaleBarState(
            visible=True,
            length_um=float(length_um),
            um_per_px=um_per_px,
        )
    else:
        state = replace(scale_bar_state, visible=bool(scale_bar_state.visible))
        if manual_um_per_px is not None and float(manual_um_per_px) > 0:
            state.um_per_px = float(manual_um_per_px)
        elif _optional_positive_float(state.um_per_px) is None:
            state.um_per_px = _resolve_um_per_px(image.metadata, manual_um_per_px)
        if not state.visible:
            return rendered

    rendered = rendered.convertToFormat(QImage.Format.Format_ARGB32)
    painter = QPainter(rendered)
    draw_scale_bar(
        painter,
        QRectF(0.0, 0.0, float(rendered.width()), float(rendered.height())),
        rendered.size(),
        state,
    )
    painter.end()
    return rendered.convertToFormat(QImage.Format.Format_RGB888)


def _resolve_um_per_px(metadata: dict[str, object], manual_um_per_px: Any) -> float | None:
    if manual_um_per_px is not None:
        manual = float(manual_um_per_px)
        if manual > 0:
            return manual
        if manual < 0:
            raise ValueError("scale-bar calibration must be positive")
    from core.measurement_scale import resolve_calibration

    meta = dict(metadata) if isinstance(metadata, dict) else {}
    resolved = resolve_calibration(meta, None)
    if not resolved.get("calibrated"):
        return None
    value = float(resolved.get("um_per_px") or 0.0)
    return value if value > 0 else None


def _optional_positive_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    return resolved if resolved > 0 else None


def _scale_bar_font(state: ScaleBarState, scale_y: float) -> QFont:
    font = QFont(str(state.font_family or "Arial"))
    font.setPixelSize(max(1, int(round(max(1.0, float(state.font_size_px)) * max(0.01, float(scale_y))))))
    font.setBold(bool(state.bold))
    font.setItalic(bool(state.italic))
    return font
