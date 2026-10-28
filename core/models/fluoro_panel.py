from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
import os
from pathlib import Path
import re
import tempfile
from typing import Any, ClassVar, Mapping
from uuid import uuid4

import numpy as np
from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter

from core.image_analysis.fluorescence_ai import (
    FluorescenceAIAssistedSettings,
    FluorescenceAIModelBoundary,
    analyze_fluorescence_ai_assisted,
    analyze_fluorescence_ai_assisted_local_heuristic,
)
from core.image_analysis.fluorescence_smart import FluorescenceSmartSettings, analyze_fluorescence_smart_assisted
from core.image_analysis.io_engine import preview_display_max, resolve_channel_display_colors
from core.image_browser import ScaleBarState, draw_scale_bar
from core.measurement_scale import (
    apply_area_scale,
    apply_length_scale,
    merge_measurement_scale_updates,
    resolve_calibration,
    resolve_measurement_scale,
)
from core.models.panel import BasePanel, register_panel_class
from core.plotting.fluoro_render import (
    AUTO_SINGLE_SIGNAL_MODE,
    COMBINED_INTENSITY_NAME,
    FORCED_RGB_SPLIT_MODE,
    RGB_COMPONENT_COLORS,
    RGB_COMPONENT_NAMES,
    RGB_COMPOSITE_MODE,
    render_fluorescence_qimage,
    rgb_interpretation_mode,
)
from core.plotting.style_presets import get_journal_preset, image_plate_style_from_preset
from core.style_preset_resolver import resolve_runtime_image_plate_style_if_active
from core.utils.fluoro_image_io import import_fluorescence_image
from core.utils.hashing import sha256_json
from core.utils.rendering import fit_qimage_to_size, qimage_to_bytes, target_pixel_size
from core.utils.time import utc_now_iso


def _optional_pixel_size(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    return resolved if isfinite(resolved) and resolved > 0 else None


def _scale_is_calibrated(scale: Mapping[str, Any] | None) -> bool:
    if not isinstance(scale, Mapping):
        return False
    if "calibrated" in scale:
        return bool(scale.get("calibrated"))
    source = str(scale.get("calibration_source") or "")
    return not bool(scale.get("assumed")) and source not in {"", "assumed"}


def _resolved_image_scale(image: Mapping[str, Any] | None) -> dict[str, Any]:
    record = image if isinstance(image, Mapping) else {}
    saved = record.get("measurement_scale_bar")
    return resolve_measurement_scale(record, saved if isinstance(saved, Mapping) else None)


def _physical_area_um2(area_px: float, scale: Mapping[str, Any] | None) -> float | None:
    if not _scale_is_calibrated(scale):
        return None
    scaled = float(apply_area_scale(area_px, scale))
    if scaled == 0.0 and float(area_px) != 0.0:
        return None
    return scaled


def _shared_calibration(image: Mapping[str, Any] | None) -> dict[str, Any]:
    record = image if isinstance(image, Mapping) else {}
    saved = record.get("measurement_scale_bar")
    return resolve_calibration(
        dict(record.get("metadata", {})) if isinstance(record.get("metadata"), Mapping) else {},
        saved if isinstance(saved, Mapping) else None,
    )


def _shared_um_per_px_x(image: Mapping[str, Any] | None) -> float:
    calibration = _shared_calibration(image)
    if not calibration.get("calibrated"):
        return 0.0
    return float(calibration.get("um_per_px_x") or 0.0)


def _display_um_per_px_x(image: Mapping[str, Any] | None, scale_bar: Mapping[str, Any] | None = None) -> float:
    record = image if isinstance(image, Mapping) else {}
    config = scale_bar if isinstance(scale_bar, Mapping) else record.get("scale_bar")
    bar = config if isinstance(config, Mapping) else {}
    override = _optional_pixel_size(bar.get("manual_um_per_px"))
    if override is not None:
        return override
    return _shared_um_per_px_x(record)


def _legacy_scale_bar_spec(image: Mapping[str, Any] | None, scale_bar: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    record = image if isinstance(image, Mapping) else {}
    config = dict(scale_bar) if isinstance(scale_bar, Mapping) else dict(record.get("scale_bar", {}) or {})
    um_x = _display_um_per_px_x(record, config)
    try:
        length_um = float(config.get("length_um", 50.0) or 50.0)
    except (TypeError, ValueError):
        length_um = 50.0
    if um_x > 0:
        if length_um <= 0:
            return None
        length_px = float(length_um / um_x)
        return {
            "length_px": length_px,
            "length_um": length_um,
            "label": f"{length_um:g} um",
            "unit": "um",
            "um_per_px": um_x,
        }
    try:
        cached_px = float(config.get("length_px", 0.0) or 0.0)
    except (TypeError, ValueError):
        cached_px = 0.0
    if cached_px <= 0:
        return None
    return {
        "length_px": cached_px,
        "length_um": None,
        "label": f"{cached_px:g} px",
        "unit": "px",
        "um_per_px": 0.0,
    }


def _incoming_calibration_is_assumed(saved: Mapping[str, Any] | None) -> bool:
    if not isinstance(saved, Mapping) or not saved:
        return True
    source = str(saved.get("calibration_source") or "")
    return bool(saved.get("assumed")) or source in {"", "assumed"}


def _browser_scale_bar_state(payload: Mapping[str, Any]) -> ScaleBarState:
    values: dict[str, Any] = {}
    for name in ScaleBarState.__dataclass_fields__:
        if name in payload:
            values[name] = payload[name]
    return ScaleBarState(**values)


@dataclass
class ChannelInfo:
    index: int
    name: str
    pseudocolor: str
    display_min: float
    display_max: float
    visible: bool = True
    auto_contrast_min: float = 0.0
    auto_contrast_max: float = 65535.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "name": self.name,
            "pseudocolor": self.pseudocolor,
            "display_min": float(self.display_min),
            "display_max": float(self.display_max),
            "visible": bool(self.visible),
            "auto_contrast_min": float(self.auto_contrast_min),
            "auto_contrast_max": float(self.auto_contrast_max),
        }


@dataclass
class ImageMetadata:
    pixel_size_um: float | None = None
    pixel_size_x_um: float | None = None
    pixel_size_y_um: float | None = None
    pixel_size_unit: str = "um"
    objective: str | None = None
    bit_depth: int = 16
    width_px: int = 0
    height_px: int = 0
    n_channels: int = 0
    source_format: str = ""
    acquisition_software: str | None = None
    axes: str = "CYX"
    _axis_fields: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._axis_fields:
            return
        present: list[str] = []
        if self.pixel_size_x_um is not None:
            present.append("pixel_size_x_um")
        if self.pixel_size_y_um is not None:
            present.append("pixel_size_y_um")
        object.__setattr__(self, "_axis_fields", tuple(present))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pixel_size_um": self.pixel_size_um,
            "pixel_size_unit": self.pixel_size_unit,
            "objective": self.objective,
            "bit_depth": int(self.bit_depth),
            "width_px": int(self.width_px),
            "height_px": int(self.height_px),
            "n_channels": int(self.n_channels),
            "source_format": self.source_format,
            "acquisition_software": self.acquisition_software,
            "axes": self.axes,
        }
        if "pixel_size_x_um" in self._axis_fields:
            payload["pixel_size_x_um"] = self.pixel_size_x_um
        if "pixel_size_y_um" in self._axis_fields:
            payload["pixel_size_y_um"] = self.pixel_size_y_um
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ImageMetadata":
        source = payload if isinstance(payload, Mapping) else {}
        axis_fields = tuple(key for key in ("pixel_size_x_um", "pixel_size_y_um") if key in source)
        return cls(
            pixel_size_um=_optional_pixel_size(source.get("pixel_size_um")),
            pixel_size_x_um=_optional_pixel_size(source.get("pixel_size_x_um")),
            pixel_size_y_um=_optional_pixel_size(source.get("pixel_size_y_um")),
            pixel_size_unit=str(source.get("pixel_size_unit", "um")),
            objective=source.get("objective"),
            bit_depth=int(source.get("bit_depth", 16)),
            width_px=int(source.get("width_px", 0)),
            height_px=int(source.get("height_px", 0)),
            n_channels=int(source.get("n_channels", 0)),
            source_format=str(source.get("source_format", "")),
            acquisition_software=source.get("acquisition_software"),
            axes=str(source.get("axes", "CYX")),
            _axis_fields=axis_fields,
        )


@dataclass
class ScaleBarConfig:
    length_um: float = 50.0
    position: str = "bottom-right"
    color: str = "#FFFFFF"
    height_px: int = 6
    font_size: float = 7.0
    show_text: bool = True
    length_px: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "length_um": float(self.length_um),
            "position": self.position,
            "color": self.color,
            "height_px": int(self.height_px),
            "font_size": float(self.font_size),
            "show_text": bool(self.show_text),
            "length_px": float(self.length_px),
        }


@dataclass
class ThresholdConfig:
    channel_index: int
    method: str = "manual"
    value: float = 0.0
    auto_value: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_index": int(self.channel_index),
            "method": self.method,
            "value": float(self.value),
            "auto_value": float(self.auto_value),
        }


DEFAULT_CHANNEL_NAMES = ["DAPI", "GFP", "RFP", "mCherry", "Cy5", "Ch6"]
DEFAULT_PSEUDOCOLORS = image_plate_style_from_preset(get_journal_preset("Nature"))["channel_colors"]
SMART_ASSISTED_MEASUREMENTS_KEY = "fluorescence_smart_assisted_measurements"
AI_ASSISTED_MEASUREMENTS_KEY = "fluorescence_ai_assisted_measurements"


class ROIIntensityAnalyzer:
    """ROI intensity measurements against the analysis data copy."""

    def measure(
        self,
        image: dict[str, Any],
        analysis_data: np.ndarray,
        roi_mask: np.ndarray,
        channel_indices: list[int],
        background_mean_by_channel: dict[int, float] | None = None,
    ) -> list[dict[str, Any]]:
        background_mean_by_channel = background_mean_by_channel or {}
        scale = _resolved_image_scale(image)
        channels = {
            int(channel.get("index", 0)): str(channel.get("name", f"Ch{int(channel.get('index', 0)) + 1}"))
            for channel in image.get("channels", [])
        }
        area_px = int(roi_mask.sum())
        area_um2 = _physical_area_um2(area_px, scale)
        results = []
        for ch_idx in channel_indices:
            channel = analysis_data[int(ch_idx)]
            pixels = channel[roi_mask]
            if pixels.size:
                mean_intensity = float(pixels.mean())
                median = float(np.median(pixels))
                std = float(pixels.std())
                min_v = float(pixels.min())
                max_v = float(pixels.max())
                int_den = float(pixels.sum())
            else:
                mean_intensity = median = std = min_v = max_v = int_den = 0.0
            bg = float(background_mean_by_channel.get(int(ch_idx), 0.0))
            results.append(
                {
                    "channel_index": int(ch_idx),
                    "channel": channels.get(int(ch_idx), f"Ch{int(ch_idx) + 1}"),
                    "area_px": area_px,
                    "area_um2": None if area_um2 is None else round(area_um2, 3),
                    "mean": round(mean_intensity, 3),
                    "median": round(median, 3),
                    "std": round(std, 3),
                    "min": round(min_v, 3),
                    "max": round(max_v, 3),
                    "integrated_density": round(int_den, 3),
                    "background_mean": round(bg, 3),
                    "corrected_integrated_density": round(int_den - bg * area_px, 3),
                }
            )
        return results


def _nice_scale_bar(metadata: ImageMetadata) -> ScaleBarConfig:
    config = ScaleBarConfig()
    config.color = str(image_plate_style_from_preset(get_journal_preset("Nature"))["scale_bar_color"])
    calibration = resolve_calibration(metadata.to_dict())
    um_x = float(calibration.get("um_per_px_x") or 0.0)
    if not calibration.get("calibrated") or um_x <= 0:
        return config
    image_width_um = metadata.width_px * um_x
    target_um = image_width_um * 0.15
    nice_values = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
    config.length_um = float(min(nice_values, key=lambda value: abs(value - target_um)))
    config.length_px = float(config.length_um / um_x)
    return config


@register_panel_class
@dataclass
class FluorescencePanel(BasePanel):
    PANEL_TYPE: ClassVar[str] = "fluorescence"
    SOURCE_MODULE: ClassVar[str] = "fluorescence"
    IMAGE_CACHE_BYTE_BUDGET: ClassVar[int] = 256 * 1024 * 1024
    panel_type: str = field(default=PANEL_TYPE)
    _image_cache: OrderedDict[str, np.ndarray] = field(default_factory=OrderedDict, init=False, repr=False, compare=False)
    _spilled_image_paths: dict[str, Path] = field(default_factory=dict, init=False, repr=False, compare=False)
    _mapped_image_data: dict[str, np.memmap] = field(default_factory=dict, init=False, repr=False, compare=False)
    _borrowed_readonly_cache: set[str] = field(default_factory=set, init=False, repr=False, compare=False)
    _borrowed_readonly_spills: set[str] = field(default_factory=set, init=False, repr=False, compare=False)
    _spill_directory: Any = field(default=None, init=False, repr=False, compare=False)

    def import_image(self, path: str | Path) -> dict[str, Any]:
        result = import_fluorescence_image(path)
        image_uid = str(uuid4())
        metadata = ImageMetadata.from_dict(result.metadata)
        analysis_data, channel_names, channel_interpretation = self._interpret_imported_rgb_samples(
            Path(path), result.analysis_data, result.channel_names
        )
        interpretation_mode = str(channel_interpretation.get("mode") or "")
        rgb_composite = interpretation_mode == RGB_COMPOSITE_MODE
        auto_single_signal = interpretation_mode == AUTO_SINGLE_SIGNAL_MODE
        if rgb_composite:
            channels = self._combined_intensity_channels(analysis_data)
            metadata.n_channels = 1
        else:
            metadata.n_channels = int(analysis_data.shape[0])
            embedded_colors = result.metadata.get("channel_colors")
            imported_colors = (
                resolve_channel_display_colors(channel_names, embedded_colors, channel_count=int(analysis_data.shape[0]))
                if embedded_colors
                else None
            )
            channels = self._default_channels(analysis_data, channel_names, pseudocolors=imported_colors)
            if imported_colors:
                for channel in channels:
                    channel["color_source"] = "file_metadata"
        scale_bar = _nice_scale_bar(metadata).to_dict()
        record = {
            "uid": image_uid,
            "source_path": str(Path(path)),
            "source_name": Path(path).name,
            "source_hash": result.source_hash,
            "metadata": metadata.to_dict(),
            "channels": channels,
            "import_display_channels": deepcopy(channels),
            "scale_bar": scale_bar,
            "measurement_scale_bar": resolve_measurement_scale({"metadata": metadata.to_dict()}),
            "thresholds": [],
            "rois": [],
            "measurements": [],
            "view_mode": "single" if auto_single_signal else "merge",
            "layout_mode": "merge_only" if auto_single_signal or rgb_composite else "merge_split",
            "imported_at": utc_now_iso(),
            "analysis_data_shape": list(analysis_data.shape),
            "analysis_dtype": str(analysis_data.dtype),
            "channel_interpretation": channel_interpretation,
            "non_destructive": True,
        }
        self._cache_image_array(image_uid, analysis_data)
        images = list(self.data.get("fluorescence_images", []))
        images.append(record)
        self.data["fluorescence_images"] = images
        self.data["active_image_uid"] = image_uid
        self.data["raw_source"] = Path(path).name
        self.data["raw_hash"] = result.source_hash
        self.data["raw_copy_path"] = f"data/fluorescence/{self.panel_uuid}/{Path(path).name}"
        self.analysis.setdefault("measurements", [])
        self.style.setdefault("fluorescence", {})
        self.append_audit("import_fluorescence_image", f"{Path(path).name}; {metadata.n_channels} channel(s); {metadata.source_format}")
        return record

    def _interpret_imported_rgb_samples(
        self,
        source_path: Path,
        data: np.ndarray,
        names: list[str] | None,
    ) -> tuple[np.ndarray, list[str], dict[str, Any]]:
        source_names = list(names or [])
        if data.ndim != 3 or int(data.shape[0]) not in {3, 4}:
            return data, source_names, {"mode": "native_channels"}
        if not self._source_path_is_rgb_sample_image(source_path):
            return data, source_names, {"mode": "native_channels"}
        return np.ascontiguousarray(data), [COMBINED_INTENSITY_NAME], {
            "mode": RGB_COMPOSITE_MODE,
            "source_sample_count": int(data.shape[0]),
            "has_alpha": int(data.shape[0]) == 4,
        }

    def force_split_rgb_samples(self, image_uid: str) -> dict[str, Any]:
        images = self.images()
        image = next((item for item in images if str(item.get("uid", "")) == str(image_uid)), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")
        interpretation = dict(image.get("channel_interpretation", {}))
        if interpretation.get("mode") not in {RGB_COMPOSITE_MODE, AUTO_SINGLE_SIGNAL_MODE}:
            raise ValueError("The active image is not an ordinary RGB image that can be split into R/G/B components.")
        data = self._rgb_source_array(image, interpretation)
        if data.ndim != 3 or int(data.shape[0]) not in {3, 4}:
            raise ValueError("The original image does not contain splittable RGB samples.")

        image["channels"] = self._rgb_component_channels(data)
        metadata = dict(image.get("metadata", {}))
        metadata["n_channels"] = 3
        image["metadata"] = metadata
        image["analysis_data_shape"] = list(data.shape)
        image["analysis_dtype"] = str(data.dtype)
        image["channel_interpretation"] = {
            "mode": FORCED_RGB_SPLIT_MODE,
            "source_sample_count": int(data.shape[0]),
            "has_alpha": int(data.shape[0]) > 3,
        }
        image["view_mode"] = "split"
        image["layout_mode"] = "merge_split"
        self._cache_image_array(str(image_uid), np.ascontiguousarray(data))
        self.data["fluorescence_images"] = images
        self.append_audit("force_split_fluorescence_rgb_samples", str(image.get("source_name", image_uid)))
        return image

    def restore_auto_detected_rgb_signal(self, image_uid: str) -> dict[str, Any]:
        images = self.images()
        image = next((item for item in images if str(item.get("uid", "")) == str(image_uid)), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")
        interpretation = dict(image.get("channel_interpretation", {}))
        if interpretation.get("mode") != FORCED_RGB_SPLIT_MODE:
            raise ValueError("The active image is not using forced RGB sample splitting.")
        cached = self.image_data(str(image_uid))
        if cached is not None and cached.ndim == 3 and int(cached.shape[0]) in {3, 4}:
            data = np.asarray(cached)
        else:
            data = self._rgb_source_array(image, {**interpretation, "mode": RGB_COMPOSITE_MODE})
        restored_interpretation = {
            "mode": RGB_COMPOSITE_MODE,
            "source_sample_count": int(data.shape[0]),
            "has_alpha": int(data.shape[0]) == 4,
        }
        image["channels"] = self._combined_intensity_channels(data)
        metadata = dict(image.get("metadata", {}))
        metadata["n_channels"] = 1
        image["metadata"] = metadata
        image["analysis_data_shape"] = list(data.shape)
        image["analysis_dtype"] = str(data.dtype)
        image["channel_interpretation"] = restored_interpretation
        image["view_mode"] = "merge"
        image["layout_mode"] = "merge_only"
        self._cache_image_array(str(image_uid), np.ascontiguousarray(data))
        self.data["fluorescence_images"] = images
        self.append_audit("restore_fluorescence_auto_single_signal", str(image.get("source_name", image_uid)))
        return image

    def merge_images_as_channels(self, image_uids: list[str], *, source_name: str | None = None) -> dict[str, Any]:
        selected_uids = [str(uid) for uid in image_uids if str(uid).strip()]
        if len(selected_uids) < 2:
            raise ValueError("Select at least two fluorescence images to merge as channels.")
        images_by_uid = {str(image.get("uid", "")): dict(image) for image in self.images()}
        missing = [uid for uid in selected_uids if uid not in images_by_uid]
        if missing:
            raise KeyError(f"Fluorescence image not found: {missing[0]}")

        channel_arrays: list[np.ndarray] = []
        channel_names: list[str] = []
        channel_displays: list[dict[str, Any]] = []
        display_components: list[np.ndarray | None] = []
        source_images: list[dict[str, Any]] = []
        height = width = None
        for uid in selected_uids:
            image = images_by_uid[uid]
            data = self.image_data(uid)
            if data is None:
                raise ValueError(f"Source image data is unavailable: {image.get('source_name', uid)}")
            if data.ndim != 3 or int(data.shape[0]) < 1:
                raise ValueError(f"Source image has no channel data: {image.get('source_name', uid)}")
            channel_array, channel_source = self._merge_source_channel_array(image, data)
            if height is None or width is None:
                height, width = int(channel_array.shape[0]), int(channel_array.shape[1])
            elif int(channel_array.shape[0]) != height or int(channel_array.shape[1]) != width:
                raise ValueError("Selected fluorescence images must have the same width and height.")
            channel_arrays.append(np.asarray(channel_array))
            if rgb_interpretation_mode(image) in {RGB_COMPOSITE_MODE, FORCED_RGB_SPLIT_MODE}:
                # An RGB single-signal image already has its display color.
                # Do not assign a journal palette or auto-stretch it again.
                sums = np.sum(self._rgb_planes_with_coverage(data), axis=(1, 2), dtype=np.float64)
                peak = float(np.max(sums)) or 1.0
                color = '#' + ''.join(f'{int(round(value/peak*255)):02X}' for value in sums)
                from core.plotting.fluoro_render import render_fluorescence_qimage
                rendered = render_fluorescence_qimage(data, image,
                    self._styled_channels_for_render(image.get('channels', []), image), view_mode='merge')
                rgba = rendered.convertToFormat(QImage.Format.Format_RGBA8888)
                pointer = rgba.constBits()
                pointer.setsize(rgba.sizeInBytes())
                pixels = np.frombuffer(pointer, np.uint8).reshape(rgba.height(), rgba.width(), 4).copy()
                count = 4 if rendered.hasAlphaChannel() else 3
                display_components.append(np.moveaxis(pixels[:, :, :count], -1, 0))
                channel_displays.append(dict(pseudocolor=color, display_min=0.0, display_max=255.0,
                    auto_contrast_min=0.0, auto_contrast_max=255.0, display_processing={}))
            else:
                display_components.append(None)
                channel_displays.append(deepcopy(self._styled_channels_for_render(image.get('channels', []), image)[0]))
            source_entry = {
                "uid": uid,
                "source_name": str(image.get("source_name", "")),
                "source_hash": str(image.get("source_hash", "")),
                "channel_index": 0,
            }
            source_entry.update(channel_source)
            source_images.append(source_entry)
            channel_names.append(self._merged_channel_name(image))

        merged_data = np.stack(channel_arrays, axis=0)
        image_uid = str(uuid4())
        first_metadata = ImageMetadata.from_dict(dict(images_by_uid[selected_uids[0]].get("metadata", {})))
        metadata = ImageMetadata(
            pixel_size_um=first_metadata.pixel_size_um,
            pixel_size_x_um=first_metadata.pixel_size_x_um,
            pixel_size_y_um=first_metadata.pixel_size_y_um,
            pixel_size_unit=first_metadata.pixel_size_unit,
            objective=first_metadata.objective,
            bit_depth=int(merged_data.dtype.itemsize*8),
            width_px=int(width or first_metadata.width_px),
            height_px=int(height or first_metadata.height_px),
            n_channels=int(merged_data.shape[0]),
            source_format="merged_channels",
            acquisition_software=first_metadata.acquisition_software,
            axes="CYX",
            _axis_fields=first_metadata._axis_fields,
        )
        channels = self._default_channels(merged_data, channel_names)
        for index, channel in enumerate(channels):
            channel.update(channel_displays[index])
            channel.update(index=index, name=channel_names[index], color_source='merged_source')
        # Keep scientific channels first. Display components are independent,
        # lossless RGB snapshots, never additional measurement channels.
        signal_count = len(channel_arrays)
        extra_arrays = []
        component_offset = signal_count
        for channel, components in zip(channels, display_components):
            if components is not None:
                channel['rgb_components'] = list(range(component_offset, component_offset + len(components)))
                component_offset += len(components)
                extra_arrays.extend(components)
        if extra_arrays:
            merged_data = np.concatenate([merged_data, np.stack(extra_arrays)], axis=0)
        scale_bar = _nice_scale_bar(metadata).to_dict()
        names = [str(item.get("source_name", "")) for item in source_images if str(item.get("source_name", ""))]
        merged_name = (source_name or self._default_merged_source_name(names)).strip()
        record = {
            "uid": image_uid,
            "source_path": "",
            "source_name": merged_name,
            "source_hash": sha256_json({"merged_channels": source_images}),
            "metadata": metadata.to_dict(),
            "channels": channels,
            "import_display_channels": deepcopy(channels),
            "scale_bar": scale_bar,
            "measurement_scale_bar": resolve_measurement_scale({"metadata": metadata.to_dict()}),
            "thresholds": [],
            "rois": [],
            "measurements": [],
            "view_mode": "merge",
            "layout_mode": "merge_split",
            "imported_at": utc_now_iso(),
            "analysis_data_shape": list(merged_data.shape),
            "analysis_dtype": str(merged_data.dtype),
            "non_destructive": True,
            "merged_from_images": source_images,
            "merged_signal_channels": signal_count,
        }
        images = self.images()
        images.append(record)
        self.data["fluorescence_images"] = images
        self._cache_image_array(image_uid, merged_data)
        self.data["active_image_uid"] = image_uid
        self.data["raw_source"] = merged_name
        self.data["raw_hash"] = record["source_hash"]
        self.data["raw_copy_path"] = ""
        self.analysis.setdefault("measurements", [])
        self.style.setdefault("fluorescence", {})
        self.append_audit("merge_fluorescence_images_as_channels", f"{merged_name}; {len(selected_uids)} source image(s)")
        return record

    def _merge_source_channel_array(self, image: dict[str, Any], data: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        if int(data.shape[0]) == 1:
            interpretation = dict(image.get("channel_interpretation", {}))
            if interpretation.get("mode") == AUTO_SINGLE_SIGNAL_MODE:
                return np.asarray(data[0]), {
                    "source_channel_mode": "rgb_max_projection",
                    "source_channels": list(interpretation.get("source_sample_indices", [0])),
                }
            return np.asarray(data[0]), {"source_channel_mode": "single_channel", "source_channels": [0]}
        if int(data.shape[0]) in {3, 4} and (
            rgb_interpretation_mode(image) in {RGB_COMPOSITE_MODE, FORCED_RGB_SPLIT_MODE}
            or self._image_record_is_rgb_sample_source(image)
        ):
            visible_samples = np.asarray(data[:3])
            return np.max(visible_samples, axis=0).astype(data.dtype, copy=False), {
                "source_channel_mode": "rgb_max_projection",
                "source_channels": [0, 1, 2],
            }
        raise ValueError(
            "Merge-as-channels expects single-channel source images or RGB single-signal images: "
            f"{image.get('source_name', image.get('uid', 'image'))}"
        )

    def _image_record_is_rgb_sample_source(self, image: dict[str, Any]) -> bool:
        source_path = Path(str(image.get("source_path", "")))
        return self._source_path_is_rgb_sample_image(source_path)

    def _source_path_is_rgb_sample_image(self, source_path: Path) -> bool:
        suffix = source_path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            return True
        if suffix not in {".tif", ".tiff"} or not source_path.exists():
            return False
        try:
            import tifffile

            with tifffile.TiffFile(source_path) as tif:
                page = tif.pages[0]
                samples_per_pixel = int(getattr(page, "samplesperpixel", 1) or 1)
                photometric = getattr(page, "photometric", None)
                photometric_name = str(getattr(photometric, "name", photometric)).upper()
                axes = str(getattr(tif.series[0], "axes", "") or "").upper()
        except Exception:
            return False
        return samples_per_pixel in {3, 4} and ("RGB" in photometric_name or "S" in axes)

    def _rgb_source_array(self, image: dict[str, Any], interpretation: dict[str, Any]) -> np.ndarray:
        uid = str(image.get("uid", ""))
        cached = None
        if uid:
            try:
                cached = self.image_data(uid)
            except (OSError, ValueError):
                cached = None
        if (
            interpretation.get("mode") == RGB_COMPOSITE_MODE
            and cached is not None
            and cached.ndim == 3
            and int(cached.shape[0]) in {3, 4}
        ):
            return np.asarray(cached)
        source_path = Path(str(image.get("source_path", "")))
        if not source_path.exists():
            raise ValueError("The original image file is unavailable, so its RGB samples cannot be split.")
        result = import_fluorescence_image(source_path)
        data = np.asarray(result.analysis_data)
        if data.ndim != 3 or int(data.shape[0]) not in {3, 4} or not self._source_path_is_rgb_sample_image(source_path):
            raise ValueError("The original image does not contain splittable RGB samples.")
        return data

    def _combined_intensity_channels(self, data: np.ndarray) -> list[dict[str, Any]]:
        combined = np.max(np.asarray(data[:3]), axis=0)
        max_value = float(np.iinfo(data.dtype).max) if np.issubdtype(data.dtype, np.integer) else float(np.nanmax(combined) or 1.0)
        non_zero = combined[combined > 0]
        if non_zero.size:
            display_min, display_max = np.percentile(non_zero, [1, 99.5])
        else:
            display_min, display_max = 0.0, max_value
        if display_max <= display_min:
            display_max = display_min + 1.0
        channel = ChannelInfo(
            index=0,
            name=COMBINED_INTENSITY_NAME,
            pseudocolor="#FFFFFF",
            display_min=float(display_min),
            display_max=float(display_max),
            auto_contrast_min=float(display_min),
            auto_contrast_max=float(display_max),
        ).to_dict()
        channel["color_source"] = RGB_COMPOSITE_MODE
        return [channel]

    def _rgb_component_channels(self, data: np.ndarray) -> list[dict[str, Any]]:
        max_value = float(np.iinfo(data.dtype).max) if np.issubdtype(data.dtype, np.integer) else float(np.nanmax(data[:3]) or 1.0)
        channels = []
        for index in range(3):
            channel = ChannelInfo(
                index=index,
                name=RGB_COMPONENT_NAMES[index],
                pseudocolor=RGB_COMPONENT_COLORS[index],
                display_min=0.0,
                display_max=float(max_value),
                auto_contrast_min=0.0,
                auto_contrast_max=float(max_value),
            ).to_dict()
            channel["color_source"] = "rgb_component"
            channels.append(channel)
        return channels

    def _rgb_planes_with_coverage(self, data: np.ndarray) -> np.ndarray:
        rgb = np.array(data[:3], copy=True)
        if data.ndim == 3 and int(data.shape[0]) >= 4:
            rgb[:, np.asarray(data[3]) == 0] = 0
        return rgb

    def _analysis_stack(self, image: dict[str, Any], data: np.ndarray) -> np.ndarray:
        if image.get('merged_signal_channels'):
            return data[:int(image['merged_signal_channels'])]
        mode = rgb_interpretation_mode(image)
        if mode == RGB_COMPOSITE_MODE:
            return np.ascontiguousarray(np.max(self._rgb_planes_with_coverage(data), axis=0, keepdims=True))
        if mode == FORCED_RGB_SPLIT_MODE:
            return np.ascontiguousarray(self._rgb_planes_with_coverage(data))
        return data

    def _merged_channel_name(self, image: dict[str, Any]) -> str:
        stem = Path(str(image.get("source_name", "channel"))).stem.strip()
        stem = re.sub(r"[_ -]?(channel|ch)\s*\d+$", "", stem, flags=re.IGNORECASE).strip(" _-")
        return stem or str(image.get("source_name", "channel"))

    def segmentation_input_context(self,image_uid: str) -> dict[str,Any]:
        image=next((item for item in self.images() if item.get('uid')==image_uid),{})
        mode=image.get('segmentation_input','raw')
        context={'mode':'adjusted' if mode=='adjusted' else 'raw'}
        if mode=='adjusted':
            context['channels']=[{key:deepcopy(ch.get(key)) for key in
                ('index','display_min','display_max','display_processing')} for ch in image.get('channels',[])]
            context['intensity_range']='0-255; per-channel grayscale, no annotations or pseudocolor'
        return context

    def segmentation_input_data(self,image_uid: str,*,raw_data=None) -> np.ndarray:
        """Explicit detection input at source resolution; quantitative data stays raw."""
        from core.plotting.fluoro_render import _normalize_channel, _display_plane
        from core.plotting.fluoro_display import apply_display_optimization,display_points_active
        image=next((item for item in self.images() if item.get('uid')==image_uid),{})
        source=self.image_data(image_uid) if raw_data is None else raw_data
        if source is None:raise FileNotFoundError('Fluorescence source image data is not available.')
        data=self._analysis_stack(image,source)
        if image.get('segmentation_input','raw')!='adjusted':return data
        channels={int(ch.get('index',0)):ch for ch in image.get('channels',[])}
        result=np.empty(data.shape,np.uint8)
        for index,plane in enumerate(data):
            ch=channels.get(index,{})
            if ch.get('rgb_components'):
                plane = _display_plane(source, ch)
            normalized=_normalize_channel(plane,float(ch.get('display_min',0)),float(ch.get('display_max',preview_display_max(data))),
                clip=not display_points_active(ch.get('display_processing')))
            result[index]=np.rint(apply_display_optimization(normalized,ch.get('display_processing'))*255).clip(0,255).astype(np.uint8)
        return result

    def _default_merged_source_name(self, names: list[str]) -> str:
        if not names:
            return "Merged channels"
        first = Path(names[0]).stem or "Merged"
        return f"{first} merged channels"

    def save_image_data_as_ome_tiff(self, image_uid: str, path: str | Path) -> Path:
        data = self.image_data(image_uid)
        if data is None:
            raise ValueError("Fluorescence image data is unavailable.")
        image = next((item for item in self.images() if str(item.get("uid", "")) == str(image_uid)), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")
        if image.get('merged_signal_channels'):
            data = data[:int(image['merged_signal_channels'])]
        target = Path(path)
        if target.suffix.lower() not in {".tif", ".tiff"}:
            target = target.with_suffix(".ome.tif")
        channel_names = [
            str(channel.get("name", f"Ch{index + 1}"))
            for index, channel in enumerate(image.get("channels", []))
        ]
        if len(channel_names) < int(data.shape[0]):
            channel_names.extend(f"Ch{index + 1}" for index in range(len(channel_names), int(data.shape[0])))
        metadata: dict[str, Any] = {
            "axes": "CYX",
            "Channel": {"Name": channel_names[: int(data.shape[0])]},
        }
        image_metadata = dict(image.get("metadata", {}))
        has_axis_keys = "pixel_size_x_um" in image_metadata or "pixel_size_y_um" in image_metadata
        size_x = _optional_pixel_size(image_metadata.get("pixel_size_x_um"))
        size_y = _optional_pixel_size(image_metadata.get("pixel_size_y_um"))
        size_iso = _optional_pixel_size(image_metadata.get("pixel_size_um"))
        if has_axis_keys:
            if size_x is None:
                size_x = size_iso
        elif size_iso is not None:
            size_x = size_iso
            size_y = size_iso
        if size_x is not None:
            metadata["PhysicalSizeX"] = float(size_x)
            metadata["PhysicalSizeXUnit"] = "µm"
        if size_y is not None:
            metadata["PhysicalSizeY"] = float(size_y)
            metadata["PhysicalSizeYUnit"] = "µm"
        import tifffile

        array = np.asarray(data)
        if rgb_interpretation_mode(image) in {RGB_COMPOSITE_MODE, FORCED_RGB_SPLIT_MODE} and array.ndim == 3 and int(array.shape[0]) in {3, 4}:
            yxs = np.ascontiguousarray(np.moveaxis(array, 0, -1))
            rgb_metadata = {"axes": "YXS"}
            if "PhysicalSizeX" in metadata:
                rgb_metadata["PhysicalSizeX"] = metadata["PhysicalSizeX"]
                rgb_metadata["PhysicalSizeXUnit"] = metadata["PhysicalSizeXUnit"]
            if "PhysicalSizeY" in metadata:
                rgb_metadata["PhysicalSizeY"] = metadata["PhysicalSizeY"]
                rgb_metadata["PhysicalSizeYUnit"] = metadata["PhysicalSizeYUnit"]
            write_kwargs: dict[str, Any] = {"photometric": "rgb", "metadata": rgb_metadata, "ome": True}
            if int(yxs.shape[-1]) == 4:
                write_kwargs["extrasamples"] = "unassalpha"
            tifffile.imwrite(target, yxs, **write_kwargs)
        else:
            tifffile.imwrite(target, array, ome=True, metadata=metadata)
        self.append_audit("save_fluorescence_image_data", f"{image.get('source_name', image_uid)} -> {target.name}")
        return target

    def _default_channels(
        self,
        data: np.ndarray,
        names: list[str] | None = None,
        *,
        pseudocolors: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        channels = []
        names = names or []
        try:
            plate_style = image_plate_style_from_preset(get_journal_preset(str(self.style.get("preset", "Nature"))))
        except KeyError:
            plate_style = image_plate_style_from_preset(get_journal_preset("Nature"))
        pseudocolors = list(pseudocolors or plate_style.get("channel_colors", [])) or list(DEFAULT_PSEUDOCOLORS)
        max_value = float(np.iinfo(data.dtype).max) if np.issubdtype(data.dtype, np.integer) else float(np.nanmax(data) or 1.0)
        for index in range(int(data.shape[0])):
            channel = data[index]
            non_zero = channel[channel > 0]
            if non_zero.size:
                display_min, display_max = np.percentile(non_zero, [1, 99.5])
            else:
                display_min, display_max = 0.0, max_value
            if display_max <= display_min:
                display_max = display_min + 1.0
            channels.append(
                ChannelInfo(
                    index=index,
                    name=names[index] if index < len(names) and names[index] else (DEFAULT_CHANNEL_NAMES[index] if index < len(DEFAULT_CHANNEL_NAMES) else f"Ch{index + 1}"),
                    pseudocolor=pseudocolors[index] if index < len(pseudocolors) else "#FFFFFF",
                    display_min=float(display_min),
                    display_max=float(display_max),
                    auto_contrast_min=float(display_min),
                    auto_contrast_max=float(display_max),
                ).to_dict()
            )
        return channels

    def images(self) -> list[dict[str, Any]]:
        return list(self.data.get("fluorescence_images", []))

    def active_image(self) -> dict[str, Any] | None:
        uid = self.data.get("active_image_uid")
        for image in self.images():
            if image.get("uid") == uid:
                return image
        images = self.images()
        return images[0] if images else None

    def delete_measurement(self, measurement_uuid: str) -> bool:
        target = str(measurement_uuid)
        removed = False
        measurements = []
        for measurement in self.analysis.get("measurements", []):
            if str(measurement.get("measurement_uuid")) == target:
                removed = True
                continue
            measurements.append(measurement)
        self.analysis["measurements"] = measurements
        images = self.images()
        for image in images:
            image_rows = []
            for measurement in image.get("measurements", []):
                if str(measurement.get("measurement_uuid")) == target:
                    removed = True
                    continue
                image_rows.append(measurement)
            image["measurements"] = image_rows
        self.data["fluorescence_images"] = images
        if removed:
            self._invalidate_fluoro_statistics()
            self.append_audit("delete_fluorescence_measurement", target)
        return removed

    def clear_measurements(self, image_uid: str | None = None, measurement_type: str | None = None) -> int:
        removed_uuids: set[str] = set()

        def should_remove(measurement: dict[str, Any]) -> bool:
            row_image_uid = str(measurement.get("image_uuid", measurement.get("image_uid", "")))
            if image_uid is not None and row_image_uid != str(image_uid):
                return False
            if measurement_type is not None and str(measurement.get("type", "")) != str(measurement_type):
                return False
            return True

        measurements = []
        for measurement in self.analysis.get("measurements", []):
            if should_remove(measurement):
                removed_uuids.add(str(measurement.get("measurement_uuid")))
                continue
            measurements.append(measurement)
        self.analysis["measurements"] = measurements

        images = self.images()
        for image in images:
            if image_uid is not None and str(image.get("uid")) != str(image_uid):
                continue
            image_rows = []
            for measurement in image.get("measurements", []):
                if should_remove(measurement):
                    removed_uuids.add(str(measurement.get("measurement_uuid")))
                    continue
                image_rows.append(measurement)
            image["measurements"] = image_rows
        self.data["fluorescence_images"] = images
        if removed_uuids:
            self._invalidate_fluoro_statistics()
            self.append_audit("clear_fluorescence_measurements", f"image={image_uid}; type={measurement_type}; count={len(removed_uuids)}")
        return len(removed_uuids)

    def delete_object_count(self, count_uuid: str) -> bool:
        target = str(count_uuid)
        removed = False
        results = []
        for result in self.analysis.get("object_counts", []):
            if str(result.get("count_uuid")) == target:
                removed = True
                continue
            results.append(result)
        self.analysis["object_counts"] = results
        images = self.images()
        for image in images:
            image_rows = []
            for result in image.get("object_counts", []):
                if str(result.get("count_uuid")) == target:
                    removed = True
                    continue
                image_rows.append(result)
            image["object_counts"] = image_rows
        self.data["fluorescence_images"] = images
        if removed:
            self._invalidate_fluoro_statistics()
            self.append_audit("delete_fluorescence_object_count", target)
        return removed

    def clear_object_counts(self, image_uid: str | None = None) -> int:
        removed_uuids: set[str] = set()
        results = []
        for result in self.analysis.get("object_counts", []):
            if image_uid is not None and str(result.get("image_uid", "")) != str(image_uid):
                results.append(result)
                continue
            removed_uuids.add(str(result.get("count_uuid")))
        self.analysis["object_counts"] = results
        images = self.images()
        for image in images:
            if image_uid is not None and str(image.get("uid")) != str(image_uid):
                continue
            image_rows = []
            for result in image.get("object_counts", []):
                if image_uid is None or str(result.get("image_uid", image.get("uid"))) == str(image_uid):
                    removed_uuids.add(str(result.get("count_uuid")))
                    continue
                image_rows.append(result)
            image["object_counts"] = image_rows
        self.data["fluorescence_images"] = images
        if removed_uuids:
            self._invalidate_fluoro_statistics()
            self.append_audit("clear_fluorescence_object_counts", f"image={image_uid}; count={len(removed_uuids)}")
        return len(removed_uuids)

    def delete_smart_assisted_measurement(self, smart_measurement_uuid: str) -> bool:
        target = str(smart_measurement_uuid)
        removed = False
        rows = []
        for row in self.analysis.get(SMART_ASSISTED_MEASUREMENTS_KEY, []):
            if str(row.get("smart_measurement_uuid")) == target:
                removed = True
                continue
            rows.append(row)
        self.analysis[SMART_ASSISTED_MEASUREMENTS_KEY] = rows

        images = self.images()
        for image in images:
            image_rows = []
            for row in image.get(SMART_ASSISTED_MEASUREMENTS_KEY, []):
                if str(row.get("smart_measurement_uuid")) == target:
                    removed = True
                    continue
                image_rows.append(row)
            image[SMART_ASSISTED_MEASUREMENTS_KEY] = image_rows
        self.data["fluorescence_images"] = images
        if removed:
            self._invalidate_fluoro_statistics()
            self.append_audit("delete_fluorescence_smart_assisted_measurement", target)
        return removed

    def clear_smart_assisted_measurements(self, image_uid: str | None = None) -> int:
        removed_count = 0

        def should_remove(row: dict[str, Any]) -> bool:
            if image_uid is None:
                return True
            return str(row.get("image_uid", row.get("image_uuid", ""))) == str(image_uid)

        rows = []
        for row in self.analysis.get(SMART_ASSISTED_MEASUREMENTS_KEY, []):
            if should_remove(row):
                removed_count += 1
                continue
            rows.append(row)
        self.analysis[SMART_ASSISTED_MEASUREMENTS_KEY] = rows

        images = self.images()
        for image in images:
            if image_uid is not None and str(image.get("uid")) != str(image_uid):
                continue
            image_rows = []
            for row in image.get(SMART_ASSISTED_MEASUREMENTS_KEY, []):
                if should_remove(row):
                    continue
                image_rows.append(row)
            image[SMART_ASSISTED_MEASUREMENTS_KEY] = image_rows
        self.data["fluorescence_images"] = images
        if removed_count:
            self._invalidate_fluoro_statistics()
            self.append_audit("clear_fluorescence_smart_assisted_measurements", f"image={image_uid}; count={removed_count}")
        return removed_count

    def delete_ai_assisted_measurement(self, ai_measurement_uuid: str) -> bool:
        target = str(ai_measurement_uuid)
        removed = False
        rows = []
        for row in self.analysis.get(AI_ASSISTED_MEASUREMENTS_KEY, []):
            if str(row.get("ai_measurement_uuid")) == target:
                removed = True
                continue
            rows.append(row)
        self.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = rows

        images = self.images()
        for image in images:
            image_rows = []
            for row in image.get(AI_ASSISTED_MEASUREMENTS_KEY, []):
                if str(row.get("ai_measurement_uuid")) == target:
                    removed = True
                    continue
                image_rows.append(row)
            image[AI_ASSISTED_MEASUREMENTS_KEY] = image_rows
        self.data["fluorescence_images"] = images
        if removed:
            self._invalidate_fluoro_statistics()
            self.append_audit("delete_fluorescence_ai_assisted_measurement", target)
        return removed

    def clear_ai_assisted_measurements(self, image_uid: str | None = None) -> int:
        removed_count = 0

        def should_remove(row: dict[str, Any]) -> bool:
            if image_uid is None:
                return True
            return str(row.get("image_uid", row.get("image_uuid", ""))) == str(image_uid)

        rows = []
        for row in self.analysis.get(AI_ASSISTED_MEASUREMENTS_KEY, []):
            if should_remove(row):
                removed_count += 1
                continue
            rows.append(row)
        self.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = rows

        images = self.images()
        for image in images:
            if image_uid is not None and str(image.get("uid")) != str(image_uid):
                continue
            image_rows = []
            for row in image.get(AI_ASSISTED_MEASUREMENTS_KEY, []):
                if should_remove(row):
                    continue
                image_rows.append(row)
            image[AI_ASSISTED_MEASUREMENTS_KEY] = image_rows
        self.data["fluorescence_images"] = images
        if removed_count:
            self._invalidate_fluoro_statistics()
            self.append_audit("clear_fluorescence_ai_assisted_measurements", f"image={image_uid}; count={removed_count}")
        return removed_count

    def clear_line_profile(self, image_uid: str | None = None) -> None:
        self.analysis['line_profile_runs']=[r for r in self.analysis.get('line_profile_runs',[]) if image_uid is not None and r.get('image_uid')!=image_uid]
        if image_uid is None:
            self.analysis.pop("line_profile", None)
        else:
            rows = [row for row in self.analysis.get("line_profile", []) if str(row.get("image_uid", "")) != str(image_uid)]
            if rows:
                self.analysis["line_profile"] = rows
            else:
                self.analysis.pop("line_profile", None)
        images = self.images()
        for image in images:
            if image_uid is None or str(image.get("uid")) == str(image_uid):
                image.pop("line_profile", None)
        self.data["fluorescence_images"] = images
        self._invalidate_fluoro_statistics()
        self.append_audit("clear_fluorescence_line_profile", f"image={image_uid}")

    def clear_channel_overlap(self, image_uid: str | None = None) -> None:
        self.analysis['channel_overlap_runs']=[r for r in self.analysis.get('channel_overlap_runs',[]) if image_uid is not None and r.get('image_uid')!=image_uid]
        if image_uid is None:
            self.analysis.pop("channel_overlap", None)
        else:
            rows = [row for row in self.analysis.get("channel_overlap", []) if str(row.get("image_uid", "")) != str(image_uid)]
            if rows:
                self.analysis["channel_overlap"] = rows
            else:
                self.analysis.pop("channel_overlap", None)
        images = self.images()
        for image in images:
            if image_uid is None or str(image.get("uid")) == str(image_uid):
                image.pop("channel_overlap", None)
        self.data["fluorescence_images"] = images
        self._invalidate_fluoro_statistics()
        self.append_audit("clear_fluorescence_channel_overlap", f"image={image_uid}")

    def set_active_image(self, image_uid: str) -> None:
        if not any(image.get("uid") == image_uid for image in self.images()):
            raise KeyError(f"Fluorescence image not found: {image_uid}")
        self.data["active_image_uid"] = image_uid
        self.append_audit("select_fluorescence_image", image_uid)

    def _image_record_for_uid(self, image_uid: str) -> dict[str, Any] | None:
        uid = str(image_uid)
        return next((image for image in self.images() if str(image.get("uid", "")) == uid), None)

    def _record_is_cache_only(self, image_uid: str) -> bool:
        record = self._image_record_for_uid(image_uid)
        return record is not None and not str(record.get("source_path", "")).strip()

    def _cache_image_array(self, image_uid: str, data: np.ndarray) -> None:
        uid = str(image_uid).strip()
        if not uid:
            return
        source = np.asarray(data)
        budget = max(0, int(self.IMAGE_CACHE_BYTE_BUDGET))
        if int(source.nbytes) > budget:
            self._image_cache.pop(uid, None)
            self._spill_image_array(uid, source)
            return
        array = source.copy()
        self._image_cache.pop(uid, None)
        self._remove_spill(uid)
        self._image_cache[uid] = array
        self._trim_image_cache()

    def _trim_image_cache(self) -> None:
        budget = max(0, int(self.IMAGE_CACHE_BYTE_BUDGET))
        while sum(int(array.nbytes) for array in self._image_cache.values()) > budget:
            uid, array = self._image_cache.popitem(last=False)
            if self._record_is_cache_only(uid):
                self._spill_image_array(uid, array)

    def _spill_image_array(self, image_uid: str, data: np.ndarray) -> None:
        uid = str(image_uid).strip()
        if not uid:
            return
        target = self._new_spill_path()
        np.save(target, np.asarray(data), allow_pickle=False)
        self._close_mapped_image_data(uid)
        previous = self._spilled_image_paths.pop(uid, None)
        if previous is not None and previous != target:
            previous.unlink(missing_ok=True)
        self._spilled_image_paths[uid] = target

    def _new_spill_path(self) -> Path:
        if self._spill_directory is None:
            self._spill_directory = tempfile.TemporaryDirectory(
                prefix=f"sciefig-fluorescence-{self.panel_uuid}-",
                ignore_cleanup_errors=True,
            )
        return Path(str(self._spill_directory.name)) / f"{uuid4().hex}.npy"

    def _remove_spill(self, image_uid: str) -> None:
        uid = str(image_uid).strip()
        self._close_mapped_image_data(uid)
        target = self._spilled_image_paths.pop(uid, None)
        if target is not None:
            target.unlink(missing_ok=True)

    def _close_mapped_image_data(self, image_uid: str) -> None:
        mapped = self._mapped_image_data.pop(str(image_uid).strip(), None)
        if mapped is not None:
            mapped._mmap.close()

    def remove_cached_image_data(self, image_uid: str) -> None:
        uid = str(image_uid).strip()
        self._image_cache.pop(uid, None)
        self._remove_spill(uid)

    def remove_image(self, image_uid: str) -> bool:
        uid = str(image_uid).strip()
        images = self.images()
        remaining = [image for image in images if str(image.get("uid", "")) != uid]
        if len(remaining) == len(images):
            return False
        self.remove_cached_image_data(uid)
        self.data["fluorescence_images"] = remaining
        if str(self.data.get("active_image_uid", "")) == uid:
            self.data["active_image_uid"] = str(remaining[0].get("uid", "")) if remaining else None
        return True

    def dispose(self) -> None:
        self._image_cache.clear()
        self._borrowed_readonly_cache.clear()
        for image_uid in list(self._spilled_image_paths):
            self._remove_spill(image_uid)
        for image_uid in list(self._mapped_image_data):
            self._close_mapped_image_data(image_uid)
        if self._spill_directory is not None:
            self._spill_directory.cleanup()
            self._spill_directory = None
        self._borrowed_readonly_spills.clear()

    def __del__(self) -> None:
        try:
            self.dispose()
        except Exception:
            pass

    def cache_image_data(self, image_uid: str, data: np.ndarray) -> None:
        self._cache_image_array(image_uid, data)

    def borrow_readonly_image_data(self, source_panel: "FluorescencePanel") -> None:
        """Give an isolated snapshot lossless read-only access to live transient pixels."""
        source_records = {str(image.get("uid", "")) for image in source_panel.images()}
        snapshot_records = {str(image.get("uid", "")) for image in self.images()}
        for uid in source_records & snapshot_records:
            cached = source_panel._image_cache.get(uid)
            if cached is not None:
                borrowed = cached.view()
                borrowed.setflags(write=False)
                self._image_cache[uid] = borrowed
                self._borrowed_readonly_cache.add(uid)
                continue

            source_spill = source_panel._spilled_image_paths.get(uid)
            if source_spill is None or not source_spill.is_file():
                continue
            target = self._new_spill_path()
            try:
                os.link(source_spill, target)
            except OSError as exc:
                raise RuntimeError(
                    f"Could not create a read-only fluorescence analysis snapshot for {uid}."
                ) from exc
            self._spilled_image_paths[uid] = target
            self._borrowed_readonly_spills.add(uid)

    def worker_owned_cached_image_uids(self) -> list[str]:
        borrowed = self._borrowed_readonly_cache | self._borrowed_readonly_spills
        return [
            uid
            for uid in set(self._image_cache) | set(self._spilled_image_paths)
            if uid not in borrowed
        ]

    def adopt_imported_cache(self, source_panel: "FluorescencePanel", image_uids: list[str]) -> None:
        """Adopt worker-owned arrays or spills without copying them on the GUI thread."""
        budget = max(0, int(self.IMAGE_CACHE_BYTE_BUDGET))
        for image_uid in image_uids:
            uid = str(image_uid).strip()
            if not uid:
                continue
            array = source_panel._image_cache.pop(uid, None)
            if array is not None and int(array.nbytes) <= budget:
                self._image_cache.pop(uid, None)
                self._remove_spill(uid)
                self._image_cache[uid] = array
                continue

            source_spill = source_panel._spilled_image_paths.pop(uid, None)
            if source_spill is None or not source_spill.is_file():
                continue
            self._image_cache.pop(uid, None)
            self._remove_spill(uid)
            target = self._new_spill_path()
            source_spill.replace(target)
            self._spilled_image_paths[uid] = target
        self._trim_image_cache()

    def image_data(self, image_uid: str | None = None) -> np.ndarray | None:
        uid = str(image_uid or self.data.get("active_image_uid") or "").strip()
        if not uid:
            return None
        cached = self._image_cache.pop(uid, None)
        if cached is not None:
            self._image_cache[uid] = cached
            return cached

        spilled = self._spilled_image_paths.get(uid)
        if spilled is not None and spilled.is_file():
            mapped = self._mapped_image_data.get(uid)
            if mapped is not None:
                return mapped
            try:
                mmap_mode = "r" if uid in self._borrowed_readonly_spills else None if self._record_is_cache_only(uid) else "r"
                data = np.load(spilled, allow_pickle=False, mmap_mode=mmap_mode)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Spilled fluorescence image data is invalid: {spilled.name}") from exc
            if int(np.asarray(data).nbytes) <= max(0, int(self.IMAGE_CACHE_BYTE_BUDGET)):
                array = np.asarray(data).copy()
                if isinstance(data, np.memmap):
                    data._mmap.close()
                self._image_cache.pop(uid, None)
                self._remove_spill(uid)
                self._image_cache[uid] = array
                self._trim_image_cache()
                return self._image_cache.get(uid, array)
            if isinstance(data, np.memmap):
                self._mapped_image_data[uid] = data
            return data

        record = self._image_record_for_uid(uid)
        if not record:
            return None
        source_path = Path(str(record.get("source_path", "")))
        if source_path.exists():
            result = import_fluorescence_image(source_path)
            data = np.asarray(result.analysis_data)
            interpretation = dict(record.get("channel_interpretation", {}))
            if interpretation.get("mode") == AUTO_SINGLE_SIGNAL_MODE and data.ndim == 3:
                source_indices = [
                    int(index)
                    for index in interpretation.get("source_sample_indices", [])
                    if 0 <= int(index) < int(data.shape[0])
                ]
                if source_indices:
                    data = np.max(data[source_indices], axis=0, keepdims=True)
            self._cache_image_array(uid, np.ascontiguousarray(data))
            return self._image_cache.get(uid, np.ascontiguousarray(data))
        return None

    def update_channel(self, image_uid: str, channel_index: int, **updates: Any) -> None:
        images = self.images()
        for image in images:
            if image.get("uid") != image_uid:
                continue
            channels = list(image.get("channels", []))
            if not (0 <= channel_index < len(channels)):
                raise IndexError(f"Channel index out of range: {channel_index}")
            channel = dict(channels[channel_index])
            if 'pseudocolor' in updates and updates['pseudocolor'] != channel.get('pseudocolor'):
                channel['color_source'] = 'user'
            if 'display_processing' in updates:
                from core.plotting.fluoro_display import display_settings
                channel['display_processing'] = display_settings(updates['display_processing'])
            for key in {"name", "pseudocolor", "display_min", "display_max", "visible"}:
                if key in updates:
                    channel[key] = updates[key]
            channels[channel_index] = channel
            image["channels"] = channels
            self.data["fluorescence_images"] = images
            self.append_audit("update_fluorescence_channel", f"{image_uid} ch{channel_index}: {updates}")
            return
        raise KeyError(f"Fluorescence image not found: {image_uid}")

    def set_view_mode(self, image_uid: str, view_mode: str) -> None:
        if view_mode not in {"merge", "split", "single"}:
            raise ValueError(f"Unsupported fluorescence view mode: {view_mode}")
        image = next((item for item in self.images() if str(item.get("uid", "")) == str(image_uid)), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")
        if view_mode == "split" and rgb_interpretation_mode(image) in {AUTO_SINGLE_SIGNAL_MODE, RGB_COMPOSITE_MODE}:
            raise ValueError("Ordinary RGB composites cannot be split unless RGB sample splitting is forced.")
        self._update_image_record(image_uid, {"view_mode": view_mode}, "set_fluorescence_view_mode")

    def set_layout_mode(self, image_uid: str, layout_mode: str) -> None:
        if layout_mode not in {"merge_only", "merge_split", "batch_grid", "custom"}:
            raise ValueError(f"Unsupported fluorescence layout mode: {layout_mode}")
        image = next((item for item in self.images() if str(item.get("uid", "")) == str(image_uid)), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")
        if layout_mode == "merge_split" and rgb_interpretation_mode(image) == RGB_COMPOSITE_MODE:
            raise ValueError("Ordinary RGB composites cannot use split layout unless RGB sample splitting is forced.")
        self._update_image_record(image_uid, {"layout_mode": layout_mode}, "set_fluorescence_layout_mode")

    def update_scale_bar(self, image_uid: str, **updates: Any) -> None:
        images = self.images()
        for image in images:
            if image.get("uid") != image_uid:
                continue
            scale_bar = dict(image.get("scale_bar", {}))
            scale_bar.update(updates)
            spec = _legacy_scale_bar_spec(image, scale_bar)
            if spec is not None:
                scale_bar["length_px"] = float(spec["length_px"])
            image["scale_bar"] = scale_bar
            self.data["fluorescence_images"] = images
            self.append_audit("update_fluorescence_scale_bar", f"{image_uid}: {updates}")
            return
        raise KeyError(f"Fluorescence image not found: {image_uid}")

    def measurement_scale_bar(self, image_uid: str | None = None) -> dict[str, Any]:
        uid = str(image_uid or self.data.get("active_image_uid") or "")
        image = next((item for item in self.images() if str(item.get("uid")) == uid), None)
        if image is None:
            active = self.active_image()
            if active is None:
                raise KeyError("Fluorescence image not found.")
            image = active
        return resolve_measurement_scale(image, dict(image.get("measurement_scale_bar", {})))

    def update_measurement_scale_bar(self, image_uid: str, **updates: Any) -> dict[str, Any]:
        images = self.images()
        for image in images:
            if str(image.get("uid")) != str(image_uid):
                continue
            current = dict(image.get("measurement_scale_bar", {}))
            merged = merge_measurement_scale_updates(current, updates)
            resolved = resolve_measurement_scale(image, merged)
            image["measurement_scale_bar"] = resolved
            from core.layout_scale_bar import fluorescence_layout_scale_target_ids, seed_layout_scale_presentation

            seed_layout_scale_presentation(
                image,
                measurement_scale=resolved,
                target_ids=fluorescence_layout_scale_target_ids(image),
                states_key="layout_scale_bars",
                template_key="layout_scale_bar_template",
            )
            self.data["fluorescence_images"] = images
            self._invalidate_fluoro_statistics()
            self.append_audit("update_fluorescence_measurement_scale_bar", f"{image_uid}: {resolved.get('calibration_source')}")
            return dict(resolved)
        raise KeyError(f"Fluorescence image not found: {image_uid}")

    def apply_browser_scale_transfer(self, image_uid: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        incoming = dict(payload or {})
        images = self.images()
        for image in images:
            if str(image.get("uid")) != str(image_uid):
                continue
            self._inherit_browser_image_display(image)
            calibration = incoming.get("calibration")
            display = incoming.get("display")
            saved = dict(calibration) if isinstance(calibration, Mapping) else None
            resolved = resolve_measurement_scale(image, saved)
            if _incoming_calibration_is_assumed(saved) and not resolved.get("calibrated"):
                file_resolved = resolve_measurement_scale({"metadata": dict(image.get("metadata", {}))}, None)
                if file_resolved.get("calibrated"):
                    resolved = file_resolved
                else:
                    current = image.get("measurement_scale_bar")
                    if isinstance(current, Mapping):
                        current_resolved = resolve_measurement_scale(image, dict(current))
                        if current_resolved.get("calibrated"):
                            resolved = current_resolved
            image["measurement_scale_bar"] = dict(resolved)
            from core.layout_scale_bar import fluorescence_layout_scale_target_ids, seed_layout_scale_presentation

            seed_layout_scale_presentation(
                image,
                measurement_scale=resolved,
                target_ids=fluorescence_layout_scale_target_ids(image),
                states_key="layout_scale_bars",
                template_key="layout_scale_bar_template",
            )
            if isinstance(display, Mapping) or isinstance(image.get("browser_scale_bar"), Mapping):
                display_out = deepcopy(dict(display)) if isinstance(display, Mapping) else deepcopy(dict(image.get("browser_scale_bar", {})))
                um_x = _shared_um_per_px_x(image)
                if um_x > 0:
                    display_out["um_per_px"] = um_x
                elif "um_per_px" in display_out:
                    display_out["um_per_px"] = None
                image["browser_scale_bar"] = display_out
            self.data["fluorescence_images"] = images
            self.append_audit("apply_fluorescence_browser_scale_transfer", str(image_uid))
            return {
                "measurement_scale_bar": deepcopy(dict(image.get("measurement_scale_bar", {}))),
                "browser_scale_bar": deepcopy(dict(image.get("browser_scale_bar", {}))),
            }
        raise KeyError(f"Fluorescence image not found: {image_uid}")

    def _inherit_browser_image_display(self, image: dict[str, Any]) -> None:
        # Browser imports are source-display transfers, not requests to run
        # fluorescence auto-contrast. Keep its suggested range available for
        # later explicit adjustment, but do not apply it on transfer.
        if rgb_interpretation_mode(image) == RGB_COMPOSITE_MODE:
            return
        data = self.image_data(str(image['uid']))
        if data is None:
            return
        channels = image.get('channels', [])
        is_czi = Path(str(image.get('source_path', ''))).suffix.lower() == '.czi'
        colors = (resolve_channel_display_colors(
            [str(channel.get('name', '')) for channel in channels],
            [channel.get('pseudocolor') if channel.get('color_source') == 'file_metadata' else None for channel in channels],
            channel_count=int(data.shape[0]),
        ) if is_czi else ['#FFFFFF'] if data.shape[0] == 1 else list(RGB_COMPONENT_COLORS))
        maximum = preview_display_max(data)
        for index, channel in enumerate(channels):
            channel.update(display_min=0.0, display_max=maximum,
                           pseudocolor=colors[index] if index < len(colors) else '#FFFFFF',
                           visible=index < len(colors), color_source='browser_preview')
        # Match the browser's non-CZI RGB/coverage preview, without changing
        # the source channels used for quantitative measurements.
        if not is_czi and data.shape[0] >= 4:
            image['browser_preview_alpha'] = 3

    def refresh_ai_assisted_measurements_for_scale(self, image_uid: str) -> None:
        """Recalculate existing AI area results after a scale calibration change."""
        measurement_scale = self.measurement_scale_bar(image_uid)
        self._rescale_ai_assisted_measurements(str(image_uid), measurement_scale)
        self._invalidate_fluoro_statistics()

    def _rescale_ai_assisted_measurements(self, image_uid: str, measurement_scale: dict[str, Any]) -> None:
        changed = False
        for record in self.analysis.get("fluorescence_ai_assisted_measurements", []):
            if str(record.get("image_uid", record.get("image_uuid", ""))) != str(image_uid):
                continue
            object_areas_px = [int(area) for area in record.get("object_area_distribution_px", []) if isinstance(area, (int, float))]
            object_areas_um2 = [float(apply_area_scale(area, measurement_scale)) for area in object_areas_px]
            total_area_px = int(record.get("total_object_area_px", sum(object_areas_px)) or 0)
            record["measurement_scale_bar"] = dict(measurement_scale)
            record["object_area_distribution_um2"] = object_areas_um2
            record["total_object_area_um2"] = float(apply_area_scale(total_area_px, measurement_scale))
            record["mean_object_area_um2"] = float(np.mean(object_areas_um2)) if object_areas_um2 else 0.0
            record["median_object_area_um2"] = float(np.median(object_areas_um2)) if object_areas_um2 else 0.0
            object_table = list(record.get("object_table", []))
            if object_table:
                for detected_object in object_table:
                    if isinstance(detected_object, dict):
                        detected_object["area_um2"] = float(apply_area_scale(float(detected_object.get("area_px", 0.0) or 0.0), measurement_scale))
                record["object_table"] = object_table
            changed = True
        if changed:
            images = self.images()
            for image in images:
                if str(image.get("uid")) != str(image_uid):
                    continue
                image_records = list(image.get(AI_ASSISTED_MEASUREMENTS_KEY, []))
                for record in image_records:
                    if str(record.get("image_uid", record.get("image_uuid", ""))) == str(image_uid):
                        self._rescale_single_ai_record(record, measurement_scale)
                image[AI_ASSISTED_MEASUREMENTS_KEY] = image_records
                break
            self.data["fluorescence_images"] = images

    def _rescale_single_ai_record(self, record: dict[str, Any], measurement_scale: dict[str, Any]) -> None:
        object_areas_px = [int(area) for area in record.get("object_area_distribution_px", []) if isinstance(area, (int, float))]
        object_areas_um2 = [float(apply_area_scale(area, measurement_scale)) for area in object_areas_px]
        total_area_px = int(record.get("total_object_area_px", sum(object_areas_px)) or 0)
        record["measurement_scale_bar"] = dict(measurement_scale)
        record["object_area_distribution_um2"] = object_areas_um2
        record["total_object_area_um2"] = float(apply_area_scale(total_area_px, measurement_scale))
        record["mean_object_area_um2"] = float(np.mean(object_areas_um2)) if object_areas_um2 else 0.0
        record["median_object_area_um2"] = float(np.median(object_areas_um2)) if object_areas_um2 else 0.0
        object_table = list(record.get("object_table", []))
        for detected_object in object_table:
            if isinstance(detected_object, dict):
                detected_object["area_um2"] = float(apply_area_scale(float(detected_object.get("area_px", 0.0) or 0.0), measurement_scale))
        if object_table:
            record["object_table"] = object_table

    def apply_threshold(self, image_uid: str, channel_index: int, method: str = "manual", manual_value: float = 0.0) -> np.ndarray:
        data = self.image_data(image_uid)
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        data = self.segmentation_input_data(image_uid)
        channel = data[int(channel_index)]
        threshold_value = self._threshold_for_channel(channel, method, manual_value)
        mask = channel > threshold_value
        config = ThresholdConfig(channel_index=channel_index, method=method, value=threshold_value, auto_value=threshold_value).to_dict()
        images = self.images()
        for image in images:
            if image.get("uid") == image_uid:
                thresholds = [item for item in image.get("thresholds", []) if int(item.get("channel_index", -1)) != int(channel_index)]
                thresholds.append(config)
                image["thresholds"] = thresholds
                break
        self.data["fluorescence_images"] = images
        self.analysis.setdefault("threshold_masks", {})[f"{image_uid}:{channel_index}"] = {
            "method": method,
            "value": threshold_value,
            "positive_pixels": int(mask.sum()),
        }
        self.append_audit("apply_fluorescence_threshold", f"{image_uid} ch{channel_index} {method}={threshold_value:.3f}")
        return mask

    def set_black_balance_roi(self, image_uid: str, geometry: dict[str, Any], roi_type: str = "rectangle", scope: str = "all_channels") -> dict[str, Any]:
        data = self.image_data(image_uid)
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        mask = self._roi_mask(data.shape[1:], roi_type, geometry)
        if int(mask.sum()) <= 0:
            raise ValueError("Black balance ROI must not be empty.")
        if scope not in {"all_channels", "selected_channel"}:
            raise ValueError(f"Unsupported black balance scope: {scope}")
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        if scope == "selected_channel":
            source_channel = geometry.get("source_channel_index")
            if source_channel is None:
                source_channel = 0
            channel_indices = [int(source_channel)]
        elif rgb_interpretation_mode(image) in {RGB_COMPOSITE_MODE, FORCED_RGB_SPLIT_MODE}:
            channel_indices = list(range(min(3, int(data.shape[0]))))
        else:
            channel_indices = list(range(int(image.get('merged_signal_channels', data.shape[0]))))
        backgrounds = {
            int(index): float(data[int(index)][mask].mean())
            for index in channel_indices
        }
        clean_geometry = dict(geometry)
        clean_geometry.pop("source_channel_index", None)
        correction = {
            "mode": "black_balance",
            "scope": scope,
            "roi_type": roi_type,
            "geometry": clean_geometry,
            "channel_backgrounds": backgrounds,
        }
        images = self.images()
        for image in images:
            if image.get("uid") == image_uid:
                image["background_correction"] = correction
                break
        self.data["fluorescence_images"] = images
        self.append_audit("set_fluorescence_black_balance", f"{image_uid}: {geometry}")
        return correction

    def black_balanced_image_data(self, image_uid: str | None = None) -> np.ndarray:
        uid = image_uid or self.data.get("active_image_uid")
        data = self.image_data(str(uid)) if uid else None
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        image = next((item for item in self.images() if item.get("uid") == uid), {})
        correction = dict(image.get("background_correction", {}))
        if correction.get("mode") != "black_balance":
            return data.copy()
        backgrounds = {int(key): float(value) for key, value in dict(correction.get("channel_backgrounds", {})).items()}
        corrected = data.astype(np.float32, copy=True)
        for channel_index, background in backgrounds.items():
            if 0 <= channel_index < corrected.shape[0]:
                corrected[channel_index] = np.maximum(corrected[channel_index] - background, 0.0)
        return corrected

    def run_smart_assisted_analysis(
        self,
        image_uid: str,
        channel_indices: list[int] | None = None,
        *,
        settings: FluorescenceSmartSettings | dict[str, Any] | None = None,
        roi_type: str | None = None,
        geometry: dict[str, Any] | None = None,
        roi_mask: np.ndarray | None = None,
        use_black_balance: bool = False,
    ) -> list[dict[str, Any]]:
        image = next((item for item in self.images() if str(item.get("uid")) == str(image_uid)), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")

        settings_obj = self._normalize_smart_settings(settings)
        use_existing_black_balance = bool(use_black_balance) or self._smart_settings_request_black_balance(settings)
        data = self.black_balanced_image_data(image_uid) if use_existing_black_balance else self.image_data(image_uid)
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        raw_data = self._analysis_stack(image, data)
        data = self.segmentation_input_data(image_uid,raw_data=data)
        segmentation_input = self.segmentation_input_context(image_uid)

        if channel_indices is None:
            channel_indices = [
                int(channel.get("index", 0))
                for channel in image.get("channels", [])
                if channel.get("visible", True)
            ]
            if not channel_indices:
                channel_indices = list(range(int(data.shape[0])))
        seen_channel_indices: set[int] = set()
        deduped_channel_indices: list[int] = []
        for value in channel_indices:
            channel_index = int(value)
            if channel_index in seen_channel_indices:
                continue
            seen_channel_indices.add(channel_index)
            deduped_channel_indices.append(channel_index)
        channel_indices = deduped_channel_indices

        resolved_roi_mask = roi_mask
        roi_context = self._smart_roi_context(data.shape[1:], roi_type=roi_type, geometry=geometry, roi_mask=roi_mask)
        if resolved_roi_mask is None and roi_type is not None and geometry is not None:
            resolved_roi_mask = self._roi_mask(data.shape[1:], roi_type, geometry)
            if int(resolved_roi_mask.sum()) <= 0:
                raise ValueError("Smart-assisted analysis ROI must not be empty.")

        result = analyze_fluorescence_smart_assisted(
            data,
            channels=channel_indices,
            settings=settings_obj,
            roi_mask=resolved_roi_mask,
        )
        channel_names = {
            int(channel.get("index", 0)): str(channel.get("name", f"Ch{int(channel.get('index', 0)) + 1}"))
            for channel in image.get("channels", [])
        }
        now = utc_now_iso()
        records: list[dict[str, Any]] = []
        image_background_correction = (
            dict(image.get("background_correction", {"mode": "none"}))
            if use_existing_black_balance
            else {"mode": "none"}
        )
        for channel_index in channel_indices:
            channel_result = result.channel_results[int(channel_index)]
            objects = list(channel_result.objects)
            if segmentation_input['mode']=='adjusted' and channel_result.label_mask is not None:
                from skimage.measure import regionprops
                regions={int(region.label):region for region in regionprops(channel_result.label_mask,intensity_image=raw_data[int(channel_index)])}
                for obj in objects:
                    region=regions.get(int(obj.object_id))
                    if region is not None:
                        obj.mean_intensity=float(region.intensity_mean)
                        obj.integrated_intensity=float(region.image_intensity[region.image].sum())
            total_area_px = int(sum(item.area_px for item in objects))
            mean_object_area_px = float(total_area_px / len(objects)) if objects else 0.0
            mean_intensity = float(np.mean([item.mean_intensity for item in objects])) if objects else 0.0
            integrated_intensity = float(sum(item.integrated_intensity for item in objects))
            channel_metadata = dict(channel_result.metadata)
            metadata = {**dict(result.metadata), **channel_metadata}
            input_preprocessing_context = self._smart_input_preprocessing_context(
                image=image,
                channel_index=int(channel_index),
                roi_context=roi_context,
                use_black_balance=use_existing_black_balance,
                image_background_correction=image_background_correction,
                core_effective_settings_hash=str(metadata.get("effective_settings_hash", "")),
            )
            input_preprocessing_context['segmentation_input']=deepcopy(segmentation_input)
            smart_panel_context_hash = sha256_json(input_preprocessing_context)
            metadata["input_preprocessing_context"] = input_preprocessing_context
            metadata["smart_panel_context_hash"] = smart_panel_context_hash
            metadata["roi_context"] = dict(roi_context)
            roi_persistence_warning = roi_context.get("persistence_warning")
            if roi_persistence_warning:
                metadata["roi_persistence_warning"] = str(roi_persistence_warning)
            warnings = list(channel_result.warnings) + list(result.warnings)
            if roi_persistence_warning and str(roi_persistence_warning) not in warnings:
                warnings.append(str(roi_persistence_warning))
            record = {
                "smart_measurement_uuid": str(uuid4()),
                "type": "fluorescence_smart_assisted_measurement",
                "segmentation_input":deepcopy(segmentation_input),
                "image_uid": str(image_uid),
                "created_at": now,
                "analysis_mode": "smart_assisted",
                "analysis_family": "fluorescence",
                "analysis_version": str(metadata.get("analysis_version", settings_obj.analysis_version)),
                "channel_index": int(channel_index),
                "channel_name": channel_names.get(int(channel_index), f"Ch{int(channel_index) + 1}"),
                "smart_object_count": int(channel_result.object_count),
                "total_object_area_px": total_area_px,
                "mean_object_area_px": mean_object_area_px,
                "mean_intensity": mean_intensity,
                "integrated_intensity": integrated_intensity,
                "smart_panel_context_hash": smart_panel_context_hash,
                "background_estimate": {
                    "method": channel_metadata.get("background_method", metadata.get("background_method")),
                    "radius_px": channel_metadata.get("background_radius_px", metadata.get("background_radius_px")),
                    "image_background_correction": image_background_correction,
                },
                "object_area_distribution_px": [int(item.area_px) for item in objects],
                "object_table": [item.to_dict() for item in objects],
                "roi_or_geometry": dict(roi_context),
                "input_preprocessing_context": input_preprocessing_context,
                "metadata": metadata,
                "warnings": warnings,
            }
            records.append(record)

        self._append_smart_assisted_measurements(image_uid, records)
        self.append_audit("run_fluorescence_smart_assisted_analysis", f"{image_uid}; channels={channel_indices}; count={len(records)}")
        return records

    def run_ai_assisted_analysis(
        self,
        image_uid: str,
        channel_indices: list[int] | None = None,
        *,
        settings: FluorescenceAIAssistedSettings | dict[str, Any] | None = None,
        roi_type: str | None = None,
        geometry: dict[str, Any] | None = None,
        roi_mask: np.ndarray | None = None,
        model_boundary: FluorescenceAIModelBoundary | None = None,
    ) -> list[dict[str, Any]]:
        image = next((item for item in self.images() if str(item.get("uid")) == str(image_uid)), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")

        settings_obj = self._normalize_ai_settings(settings)
        from core.image_analysis.fluorescence_ai import CELLPOSE_FLUORESCENCE_MODELS
        if settings_obj.backend != 'local_heuristic' and settings_obj.backend not in CELLPOSE_FLUORESCENCE_MODELS:
            raise ValueError('Unknown fluorescence area backend: ' + str(settings_obj.backend))
        data = self.image_data(image_uid)
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        raw_data = self._analysis_stack(image, data)
        data = self.segmentation_input_data(image_uid)
        segmentation_input = self.segmentation_input_context(image_uid)

        if channel_indices is None:
            channel_indices = [
                int(channel.get("index", 0))
                for channel in image.get("channels", [])
                if channel.get("visible", True)
            ]
            if not channel_indices:
                channel_indices = list(range(int(data.shape[0])))
        seen_channel_indices: set[int] = set()
        deduped_channel_indices: list[int] = []
        for value in channel_indices:
            channel_index = int(value)
            if channel_index in seen_channel_indices:
                continue
            seen_channel_indices.add(channel_index)
            deduped_channel_indices.append(channel_index)
        channel_indices = deduped_channel_indices

        resolved_roi_mask = roi_mask
        roi_context = self._smart_roi_context(data.shape[1:], roi_type=roi_type, geometry=geometry, roi_mask=roi_mask)
        if resolved_roi_mask is None and roi_type is not None and geometry is not None:
            resolved_roi_mask = self._roi_mask(data.shape[1:], roi_type, geometry)
            if int(resolved_roi_mask.sum()) <= 0:
                raise ValueError("AI-assisted analysis ROI must not be empty.")

        if settings_obj.backend in CELLPOSE_FLUORESCENCE_MODELS:
            from core.image_analysis.fluorescence_ai import analyze_fluorescence_cellpose
            result = analyze_fluorescence_cellpose(data, channels=channel_indices,
                settings=settings_obj, roi_mask=resolved_roi_mask)
        else:
            result = analyze_fluorescence_ai_assisted(
                data, channels=channel_indices, settings=settings_obj,
                roi_mask=resolved_roi_mask, model_boundary=model_boundary)
        if not result.is_available:
            if model_boundary is None:
                result = analyze_fluorescence_ai_assisted_local_heuristic(
                    data,
                    channels=channel_indices,
                    settings=settings_obj,
                    roi_mask=resolved_roi_mask,
                )
            else:
                self.append_audit("run_fluorescence_ai_assisted_analysis_unavailable", f"{image_uid}; channels={channel_indices}; warnings={result.warnings}")
                return []

        channel_names = {
            int(channel.get("index", 0)): str(channel.get("name", f"Ch{int(channel.get('index', 0)) + 1}"))
            for channel in image.get("channels", [])
        }
        now = utc_now_iso()
        records: list[dict[str, Any]] = []
        image_background_correction = {"mode": "none"}
        measurement_scale = self.measurement_scale_bar(image_uid)
        for channel_index in channel_indices:
            channel_result = result.channel_results[int(channel_index)]
            objects = list(channel_result.objects)
            if segmentation_input['mode']=='adjusted' and channel_result.label_mask is not None:
                from skimage.measure import regionprops
                regions={int(region.label):region for region in regionprops(channel_result.label_mask,intensity_image=raw_data[int(channel_index)])}
                for obj in objects:
                    region=regions.get(int(obj.object_id))
                    if region is not None:
                        obj.mean_intensity=float(region.intensity_mean)
                        obj.integrated_intensity=float(region.image_intensity[region.image].sum())
            total_area_px = int(sum(item.area_px for item in objects))
            object_areas_px = [int(item.area_px) for item in objects]
            object_areas_um2 = [float(apply_area_scale(area, measurement_scale)) for area in object_areas_px]
            mean_object_area_px = float(total_area_px / len(objects)) if objects else 0.0
            median_object_area_px = float(np.median(object_areas_px)) if object_areas_px else 0.0
            total_area_um2 = float(apply_area_scale(total_area_px, measurement_scale))
            mean_object_area_um2 = float(np.mean(object_areas_um2)) if object_areas_um2 else 0.0
            median_object_area_um2 = float(np.median(object_areas_um2)) if object_areas_um2 else 0.0
            mean_intensity = float(np.mean([item.mean_intensity for item in objects])) if objects else 0.0
            integrated_intensity = float(sum(item.integrated_intensity for item in objects))
            metadata = {**dict(result.metadata), **dict(channel_result.metadata)}
            input_preprocessing_context = self._ai_input_preprocessing_context(
                image=image,
                channel_index=int(channel_index),
                roi_context=roi_context,
                image_background_correction=image_background_correction,
                core_effective_settings_hash=str(metadata.get("effective_settings_hash", "")),
                model_execution_mode=str(metadata.get("model_execution_mode", "")),
                model_id=str(metadata.get("model_id", "")),
                model_version=str(metadata.get("model_version", "")),
                ai_pack_postprocess=str(metadata.get("ai_pack_postprocess", "")),
                model_commercial_use_status=str(metadata.get("model_commercial_use_status", "")),
            )
            input_preprocessing_context['segmentation_input']=deepcopy(segmentation_input)
            ai_panel_context_hash = sha256_json(input_preprocessing_context)
            metadata["input_preprocessing_context"] = input_preprocessing_context
            metadata["ai_panel_context_hash"] = ai_panel_context_hash
            metadata["roi_context"] = dict(roi_context)
            roi_persistence_warning = roi_context.get("persistence_warning")
            if roi_persistence_warning:
                metadata["roi_persistence_warning"] = str(roi_persistence_warning)
            warnings = list(channel_result.warnings) + list(result.warnings)
            if roi_persistence_warning and str(roi_persistence_warning) not in warnings:
                warnings.append(str(roi_persistence_warning))
            record = {
                "ai_measurement_uuid": str(uuid4()),
                "type": "fluorescence_ai_assisted_measurement",
                "segmentation_input":deepcopy(segmentation_input),
                "image_uid": str(image_uid),
                "created_at": now,
                "analysis_mode": "ai_assisted",
                "analysis_family": "fluorescence",
                "analysis_version": str(metadata.get("analysis_version", settings_obj.analysis_version)),
                "channel_index": int(channel_index),
                "channel_name": channel_names.get(int(channel_index), f"Ch{int(channel_index) + 1}"),
                "ai_object_count": int(channel_result.object_count),
                "total_object_area_px": total_area_px,
                "total_object_area_um2": total_area_um2,
                "mean_object_area_px": mean_object_area_px,
                "mean_object_area_um2": mean_object_area_um2,
                "median_object_area_px": median_object_area_px,
                "median_object_area_um2": median_object_area_um2,
                "mean_intensity": mean_intensity,
                "integrated_intensity": integrated_intensity,
                "ai_panel_context_hash": ai_panel_context_hash,
                "object_area_distribution_px": object_areas_px,
                "object_area_distribution_um2": object_areas_um2,
                "object_table": self._ai_objects_with_contours(objects, channel_result.label_mask, measurement_scale=measurement_scale),
                "roi_or_geometry": dict(roi_context),
                "input_preprocessing_context": input_preprocessing_context,
                "measurement_scale_bar": dict(measurement_scale),
                "metadata": metadata,
                "warnings": warnings,
            }
            records.append(record)

        self._append_ai_assisted_measurements(image_uid, records)
        self.append_audit("run_fluorescence_ai_assisted_analysis", f"{image_uid}; channels={channel_indices}; count={len(records)}")
        return records

    def _ai_objects_with_contours(
        self,
        objects: list[Any],
        label_mask: np.ndarray | None,
        *,
        measurement_scale: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        contours_by_object_id = self._contours_by_object_id(label_mask)
        rows: list[dict[str, Any]] = []
        for item in objects:
            payload = item.to_dict()
            payload["area_um2"] = float(apply_area_scale(float(payload.get("area_px", 0.0) or 0.0), measurement_scale))
            contour = contours_by_object_id.get(int(payload.get("object_id", 0)), [])
            if contour:
                payload["contour_points_px"] = contour
            else:
                payload["contour_points_px"] = self._bbox_contour_points(payload)
            rows.append(payload)
        return rows

    def _contours_by_object_id(self, label_mask: np.ndarray | None) -> dict[int, list[dict[str, float]]]:
        from skimage.measure import find_contours
        if label_mask is None:
            return {}
        labels = np.asarray(label_mask, dtype=np.int32)
        contours_by_id: dict[int, list[dict[str, float]]] = {}
        for object_id in [int(value) for value in np.unique(labels) if int(value) > 0]:
            object_mask = labels == object_id
            contours = find_contours(object_mask.astype(float), 0.5)
            if not contours:
                continue
            contour = max(contours, key=len)
            if len(contour) < 3:
                continue
            contours_by_id[object_id] = [
                {"x": float(round(point[1], 2)), "y": float(round(point[0], 2))}
                for point in contour
            ]
        return contours_by_id

    def _bbox_contour_points(self, payload: dict[str, Any]) -> list[dict[str, float]]:
        x0 = float(payload.get("bbox_x_min", 0.0))
        y0 = float(payload.get("bbox_y_min", 0.0))
        x1 = float(payload.get("bbox_x_max", x0 + 1.0))
        y1 = float(payload.get("bbox_y_max", y0 + 1.0))
        return [
            {"x": x0, "y": y0},
            {"x": x1, "y": y0},
            {"x": x1, "y": y1},
            {"x": x0, "y": y1},
        ]

    def count_channel_objects(
        self,
        image_uid: str,
        channel_indices: list[int],
        method: str = "otsu",
        manual_value: float = 0.0,
        min_area_px: int = 5,
        max_area_px: int | None = None,
        enhance: bool = False,
        algorithm: str = 'traditional',
        diameter_px: float = 40.,
        sensitivity: float = 1.,
    ) -> list[dict[str, Any]]:
        from skimage.measure import label
        from skimage.measure import regionprops
        data = self.black_balanced_image_data(image_uid)
        results = []
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        raw_data = self._analysis_stack(image, data)
        data = self.segmentation_input_data(image_uid,raw_data=data)
        segmentation_input = self.segmentation_input_context(image_uid)
        channel_names = {int(ch.get("index", 0)): str(ch.get("name", f"Ch{int(ch.get('index', 0)) + 1}")) for ch in image.get("channels", [])}
        for channel_index in channel_indices:
            channel = data[int(channel_index)]
            if algorithm != 'traditional':
                from core.image_analysis.fluorescence_counting import count_with_algorithm
                result=count_with_algorithm(channel,algorithm,diameter_px,sensitivity,min_area_px,max_area_px,intensity_image=raw_data[int(channel_index)])
                result.update(count_uuid=str(uuid4()),type='spot_count',image_uid=image_uid,
                    image_name=str(image.get('source_name','')),channel_index=int(channel_index),
                    channel_name=channel_names.get(int(channel_index),f'Ch{int(channel_index)+1}'),
                    method=algorithm,min_area_px=int(min_area_px),max_area_px=max_area_px,segmentation_input=deepcopy(segmentation_input),
                    roi_or_geometry={'type':'whole_image','coordinates_px':{}},
                    background_correction=dict(image.get('background_correction',{'mode':'none'})))
                results.append(result)
                continue
            threshold_value = self._threshold_for_channel(channel, method, manual_value)
            mask = channel > threshold_value
            if enhance:
                from core.image_analysis.fluorescence_spots import segment_spots
                labeled, threshold_value = segment_spots(channel, threshold_value, min_area_px, automatic=method != 'manual', threshold_method=method)
                mask = labeled > 0
            else:
                labeled = label(mask)
            if not enhance or method == 'manual':
                self._store_threshold_mask(image_uid, int(channel_index), method, threshold_value, mask)
            regions = [
                region
                for region in regionprops(labeled, intensity_image=raw_data[int(channel_index)])
                if region.area >= max(1, int(min_area_px)) and (max_area_px is None or region.area <= max_area_px)
            ]
            result = {
                "count_uuid": str(uuid4()),
                "type": "spot_count",
                "image_uid": image_uid,
                "channel_index": int(channel_index),
                "channel_name": channel_names.get(int(channel_index), f"Ch{int(channel_index) + 1}"),
                "method": method,
                "spot_enhancement": bool(enhance),
                "algorithm": "traditional",
                "segmentation_input":deepcopy(segmentation_input),
                "image_name": str(image.get('source_name','')),
                "algorithm_parameters": {'threshold_method':method,'threshold':threshold_value,
                    'min_area_px':int(min_area_px),'max_area_px':max_area_px,'legacy_enhance':bool(enhance)},
                "threshold_space": "local_background_corrected" if enhance and method != 'manual' else "raw_channel",
                "threshold": threshold_value,
                "min_area_px": int(min_area_px),
                "max_area_px": int(max_area_px) if max_area_px is not None else None,
                "count": int(len(regions)),
                "total_positive_area_px": int(sum(region.area for region in regions)),
                "mean_object_area_px": float(np.mean([region.area for region in regions])) if regions else 0.0,
                "mean_intensity": float(np.mean([region.intensity_mean if hasattr(region, "intensity_mean") else region.mean_intensity for region in regions])) if regions else 0.0,
                "detected_spots": self._detected_spots_from_regions(regions),
                "roi_or_geometry": {"type": "threshold", "coordinates_px": {}},
                "background_correction": dict(image.get("background_correction", {"mode": "none"})),
            }
            results.append(result)
        self._append_object_count_results(image_uid, results)
        self.append_audit("count_fluorescence_objects", f"{image_uid}; channels={channel_indices}; method={method}; min_area={min_area_px}")
        return results

    def count_like_sample(
        self,
        image_uid: str,
        channel_indices: list[int],
        sample_geometry: dict[str, Any],
        sample_roi_type: str = "rectangle",
        sensitivity: float = 0.5,
        enhance: bool = False,
    ) -> list[dict[str, Any]]:
        from skimage.measure import label
        from skimage.measure import regionprops
        data = self.black_balanced_image_data(image_uid)
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        raw_data = self._analysis_stack(image, data)
        data = self.segmentation_input_data(image_uid,raw_data=data)
        segmentation_input = self.segmentation_input_context(image_uid)
        sample_mask = self._roi_mask(data.shape[1:], sample_roi_type, sample_geometry)
        sample_area = int(sample_mask.sum())
        if sample_area <= 0:
            raise ValueError("Sample spot ROI is empty.")
        results = []
        channel_names = {int(ch.get("index", 0)): str(ch.get("name", f"Ch{int(ch.get('index', 0)) + 1}")) for ch in image.get("channels", [])}
        for channel_index in channel_indices:
            channel = data[int(channel_index)]
            sample_pixels = channel[sample_mask]
            sample_mean = float(np.mean(sample_pixels)) if sample_pixels.size else 0.0
            object_area = sample_area
            if enhance:
                from core.image_analysis.fluorescence_spots import sample_spot_profile
                object_area, sample_mean = sample_spot_profile(channel, sample_mask)
            background = float(np.percentile(channel, 25))
            threshold_value = background + (sample_mean - background) * float(sensitivity)
            mask = channel > threshold_value
            min_area = max(1, int(object_area * 0.35))
            max_area = max(min_area, int(object_area * 2.75))
            if enhance:
                from core.image_analysis.fluorescence_spots import segment_spots
                labeled, threshold_value = segment_spots(channel, threshold_value, min_area)
            else:
                labeled = label(mask)
            regions = [
                region
                for region in regionprops(labeled, intensity_image=raw_data[int(channel_index)])
                if min_area <= region.area <= max_area
            ]
            result = {
                "count_uuid": str(uuid4()),
                "type": "spot_count",
                "image_uid": image_uid,
                "channel_index": int(channel_index),
                "channel_name": channel_names.get(int(channel_index), f"Ch{int(channel_index) + 1}"),
                "method": "sample_like",
                "algorithm": "sample",
                "segmentation_input":deepcopy(segmentation_input),
                "image_name": str(image.get('source_name','')),
                "algorithm_parameters": {'sensitivity':float(sensitivity),'threshold':threshold_value,
                    'sample_area_px':sample_area,'sample_object_area_px':object_area,'sample_mean':sample_mean,
                    'min_area_px':min_area,'max_area_px':max_area,
                    'sample_roi':{'type':sample_roi_type,'geometry':dict(sample_geometry)},'legacy_enhance':bool(enhance)},
                "spot_enhancement": bool(enhance),
                "sample_object_area_px": object_area,
                "threshold": threshold_value,
                "sample_area_px": sample_area,
                "sample_mean": sample_mean,
                "min_area_px": min_area,
                "max_area_px": max_area,
                "count": int(len(regions)),
                "total_positive_area_px": int(sum(region.area for region in regions)),
                "mean_object_area_px": float(np.mean([region.area for region in regions])) if regions else 0.0,
                "mean_intensity": float(np.mean([region.intensity_mean if hasattr(region, "intensity_mean") else region.mean_intensity for region in regions])) if regions else 0.0,
                "detected_spots": self._detected_spots_from_regions(regions),
                "roi_or_geometry": {
                    "type": sample_roi_type,
                    "coordinates_px": dict(sample_geometry),
                },
                "background_correction": dict(image.get("background_correction", {"mode": "none"})),
            }
            results.append(result)
        self._append_object_count_results(image_uid, results)
        self._update_image_record(
            image_uid,
            {"sample_spot": {"roi_type": sample_roi_type, "geometry": sample_geometry, "sensitivity": float(sensitivity)}},
            "set_fluorescence_sample_spot",
        )
        self.append_audit("count_fluorescence_sample_like", f"{image_uid}; channels={channel_indices}; sample_area={sample_area}")
        return results

    def line_profile(
        self,
        image_uid: str,
        channel_indices: list[int],
        line_geometry: dict[str, Any] | None = None,
        samples: int = 256,
    ) -> list[dict[str, Any]]:
        data = self.image_data(image_uid)
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        data = self._analysis_stack(image, data)
        width = data.shape[2]
        height = data.shape[1]
        if not line_geometry:
            line_geometry = {"x1": 0.0, "y1": height / 2.0, "x2": float(width - 1), "y2": height / 2.0}
        x1 = float(line_geometry.get("x1", 0.0))
        y1 = float(line_geometry.get("y1", height / 2.0))
        x2 = float(line_geometry.get("x2", width - 1))
        y2 = float(line_geometry.get("y2", height / 2.0))
        length_px = float(np.hypot(x2 - x1, y2 - y1))
        n_samples = max(2, int(samples))
        xs = np.linspace(x1, x2, n_samples)
        ys = np.linspace(y1, y2, n_samples)
        xi = np.clip(np.rint(xs).astype(int), 0, width - 1)
        yi = np.clip(np.rint(ys).astype(int), 0, height - 1)
        scale = _resolved_image_scale(image)
        distances_px = np.linspace(0.0, length_px, n_samples)
        physical_length = float(apply_length_scale(x2 - x1, y2 - y1, scale))
        use_um = _scale_is_calibrated(scale) and (physical_length > 0.0 or length_px == 0.0)
        distances_um = np.linspace(0.0, physical_length, n_samples) if use_um else None
        channel_names = {int(ch.get("index", 0)): str(ch.get("name", f"Ch{int(ch.get('index', 0)) + 1}")) for ch in image.get("channels", [])}
        rows = []
        for channel_index in channel_indices:
            values = data[int(channel_index), yi, xi].astype(float)
            rows.append(
                {
                    "image_uid": image_uid,
                    "channel_index": int(channel_index),
                    "channel_name": channel_names.get(int(channel_index), f"Ch{int(channel_index) + 1}"),
                    "distance_px": [float(value) for value in distances_px],
                    "distance_um": None if distances_um is None else [float(value) for value in distances_um],
                    "distance_unit": "um" if use_um else "px",
                    "intensity": [float(value) for value in values],
                    "line": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    "measurement_scale_bar": dict(scale),
                }
            )
        self.analysis["line_profile"] = rows
        record=dict(measurement_uuid=str(uuid4()),image_uid=image_uid,type='line_profile',
                    image_name=image.get('source_name',''),channel_index=int(channel_indices[0]) if channel_indices else 0,
                    rows=deepcopy(rows),line=dict(line_geometry))
        self.analysis.setdefault('line_profile_runs',[]).append(record)
        active = self.active_image()
        if active and active.get("uid") == image_uid:
            active["line_profile"] = rows
        self.append_audit("compute_fluorescence_line_profile", f"{image_uid}; channels={channel_indices}")
        return rows

    def compute_channel_overlap(
        self,
        image_uid: str,
        channel_indices: list[int],
        method: str = "otsu",
        manual_value: float = 0.0,
    ) -> list[dict[str, Any]]:
        if len(channel_indices) < 2:
            raise ValueError("At least two channels are required for overlap statistics.")
        masks = {int(index): self.apply_threshold(image_uid, int(index), method, manual_value) for index in channel_indices}
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        names = {int(ch.get("index", 0)): str(ch.get("name", f"Ch{int(ch.get('index', 0)) + 1}")) for ch in image.get("channels", [])}
        rows = []
        for base_index in channel_indices:
            base_mask = masks[int(base_index)]
            base_area = int(base_mask.sum())
            for compare_index in channel_indices:
                compare_mask = masks[int(compare_index)]
                overlap = int(np.logical_and(base_mask, compare_mask).sum())
                compare_area = int(compare_mask.sum())
                rows.append(
                    {
                        "image_uid": image_uid,
                        "base_channel_index": int(base_index),
                        "base_channel_name": names.get(int(base_index), f"Ch{int(base_index) + 1}"),
                        "compare_channel_index": int(compare_index),
                        "compare_channel_name": names.get(int(compare_index), f"Ch{int(compare_index) + 1}"),
                        "overlap_px": overlap,
                        "base_area_px": base_area,
                        "compare_area_px": compare_area,
                        "overlap_percent_of_base": float(overlap / base_area * 100.0) if base_area else 0.0,
                        "overlap_percent_of_compare": float(overlap / compare_area * 100.0) if compare_area else 0.0,
                    }
                )
        self.analysis["channel_overlap"] = rows
        import cv2
        outlines=[]
        for index,mask in masks.items():
            contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            outlines.append(dict(channel_index=index,object_table=[dict(object_id=n+1,
                contour_points_px=cv2.approxPolyDP(contour,1.,True).reshape(-1,2).tolist())
                for n,contour in enumerate(contours) if len(contour)>=3]))
        self.analysis.setdefault('channel_overlap_runs',[]).append(dict(measurement_uuid=str(uuid4()),
            image_uid=image_uid,type='channel_overlap',image_name=image.get('source_name',''),rows=deepcopy(rows),
            parameters=dict(method=method,manual_value=manual_value),outlines=outlines))
        active = self.active_image()
        if active and active.get("uid") == image_uid:
            active["channel_overlap"] = rows
        self.append_audit("compute_fluorescence_overlap", f"{image_uid}; channels={channel_indices}; method={method}")
        return rows

    def render_preview(self, target_width_mm: float, target_height_mm: float, dpi: int = 150) -> QImage:
        image = self._render_active_image()
        return fit_qimage_to_size(image, target_pixel_size(target_width_mm, target_height_mm, dpi), QColor("black"))

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> bytes:
        raster_format = format.lower().lstrip(".")
        if raster_format in {"pdf", "svg"}:
            raster_format = "png"
        image = self.render_preview(target_width_mm, target_height_mm, dpi=dpi)
        return qimage_to_bytes(image, raster_format)

    def render_image_with_scale_bar(self, image_uid: str | None = None) -> QImage:
        uid = str(image_uid or self.data.get("active_image_uid") or "")
        image_record = next((item for item in self.images() if str(item.get("uid")) == uid), None)
        if image_record is None:
            image_record = self.active_image()
        if not image_record:
            raise ValueError("Fluorescence panel has no active image to render.")
        rendered = self._render_image_pixels(image_record)
        browser = image_record.get("browser_scale_bar")
        browser_visible = isinstance(browser, Mapping) and bool(browser.get("visible"))
        if not browser_visible:
            rendered = self._draw_scale_bar_on_image(rendered, image_record)
        return self._draw_browser_scale_bar_on_image(rendered, image_record)

    def _render_active_image(self) -> QImage:
        return self.render_image_with_scale_bar()

    def _render_image_pixels(self, image_record: dict[str, Any]) -> QImage:
        data = self.image_data(str(image_record.get("uid")))
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        channels = self._styled_channels_for_render(list(image_record.get("channels", [])), image_record)
        view_mode = str(image_record.get("view_mode", "merge"))
        return render_fluorescence_qimage(data, image_record, channels, view_mode=view_mode)

    def _render_image_plate_style(self) -> dict[str, Any]:
        return resolve_runtime_image_plate_style_if_active(str(self.style.get("preset", "Nature"))) or {}

    def _styled_channels_for_render(self, channels: list[dict[str, Any]], image_record: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if rgb_interpretation_mode(image_record) in {RGB_COMPOSITE_MODE, FORCED_RGB_SPLIT_MODE}:
            return [dict(channel) for channel in channels]
        plate_style = self._render_image_plate_style()
        if not plate_style:
            return [dict(channel) for channel in channels]
        colors = [str(color).strip() for color in plate_style.get("channel_colors", []) if str(color).strip()]
        styled: list[dict[str, Any]] = []
        for index, channel in enumerate(channels):
            channel_copy = dict(channel)
            if index < len(colors) and channel_copy.get("color_source") not in {"file_metadata", "browser_preview", "merged_source", "user"}:
                channel_copy["pseudocolor"] = colors[index]
            styled.append(channel_copy)
        return styled

    def _draw_scale_bar_on_image(self, image: QImage, image_record: dict[str, Any]) -> QImage:
        config = dict(image_record.get("scale_bar", {}))
        spec = _legacy_scale_bar_spec(image_record, config)
        if spec is None:
            return image
        length_px = float(spec["length_px"])
        if length_px <= 0:
            return image
        plate_style = self._render_image_plate_style()
        rendered = image.convertToFormat(QImage.Format.Format_ARGB32)
        margin = max(8, int(rendered.width() * 0.04))
        bar_width = max(1, min(int(round(length_px)), max(1, rendered.width() - margin * 2)))
        bar_height = max(1, int(config.get("height_px", 6)), int(round(float(plate_style.get("scale_bar_line_width", 0.0) or 0.0))))
        x = max(margin, rendered.width() - margin - bar_width)
        y = max(margin, rendered.height() - margin)
        color = QColor(str(plate_style.get("scale_bar_color", config.get("color", "#FFFFFF"))))
        painter = QPainter(rendered)
        painter.setPen(color)
        painter.setBrush(color)
        painter.drawRect(x, y - bar_height, bar_width, bar_height)
        if config.get("show_text", True):
            painter.setFont(
                QFont(
                    str(plate_style.get("label_font_family", "Arial")),
                    int(float(plate_style.get("label_font_size_pt", config.get("font_size", 7)))),
                )
            )
            label = str(spec["label"])
            if spec.get("unit") == "px":
                label = f"{float(bar_width):g} px"
            painter.drawText(x, max(0, y - bar_height - 4), label)
        painter.end()
        return rendered

    def _draw_browser_scale_bar_on_image(self, image: QImage, image_record: dict[str, Any]) -> QImage:
        payload = image_record.get("browser_scale_bar")
        if not isinstance(payload, Mapping):
            return image
        state = _browser_scale_bar_state(payload)
        if not state.visible:
            return image
        um_x = _shared_um_per_px_x(image_record)
        if um_x <= 0:
            return image
        state = ScaleBarState(
            visible=state.visible,
            length_um=state.length_um,
            um_per_px=um_x,
            center_x_px=state.center_x_px,
            line_y_px=state.line_y_px,
            line_color=state.line_color,
            text_color=state.text_color,
            line_thickness_px=state.line_thickness_px,
            font_family=state.font_family,
            font_size_px=state.font_size_px,
            bold=state.bold,
            italic=state.italic,
            text_position=state.text_position,
        )
        rendered = image.convertToFormat(QImage.Format.Format_ARGB32)
        painter = QPainter(rendered)
        draw_scale_bar(
            painter,
            QRectF(0.0, 0.0, float(rendered.width()), float(rendered.height())),
            QSize(rendered.width(), rendered.height()),
            state,
        )
        painter.end()
        return rendered

    def add_rectangle_roi(self, image_uid: str, x: int, y: int, w: int, h: int, channel_index: int = 0) -> dict[str, Any]:
        return self.add_roi(image_uid, "rectangle", {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}, channel_index)

    def add_roi(self, image_uid: str, roi_type: str, geometry: dict[str, Any], channel_index: int = 0) -> dict[str, Any]:
        measurement = self.measure_roi(image_uid, roi_type, geometry, channel_index)
        roi = {
            "roi_id": str(uuid4()),
            "type": roi_type,
            "geometry": geometry,
            "channel_index": int(channel_index),
            "measurement": measurement,
        }
        roi.update(geometry)
        images = self.images()
        for image in images:
            if image.get("uid") == image_uid:
                image.setdefault("rois", []).append(roi)
                image.setdefault("measurements", []).append(measurement)
                break
        self.data["fluorescence_images"] = images
        self.analysis.setdefault("legacy_measurements", {})[roi["roi_id"]] = measurement
        self.append_audit("add_fluorescence_roi", f"{image_uid} {roi_type} {geometry} ch{channel_index}")
        return roi

    def create_roi_intensity_measurement(
        self,
        image_uid: str,
        roi_type: str,
        geometry: dict[str, Any],
        channel_indices: list[int] | None = None,
        background_roi: dict[str, Any] | None = None,
        background_mean_by_channel: dict[int, float] | None = None,
    ) -> dict[str, Any]:
        image = next((item for item in self.images() if item.get("uid") == image_uid), None)
        if image is None:
            raise KeyError(f"Fluorescence image not found: {image_uid}")
        data = self.black_balanced_image_data(image_uid)
        data = self._analysis_stack(image, data)
        if channel_indices is None:
            channel_indices = [
                int(channel.get("index", 0))
                for channel in image.get("channels", [])
                if channel.get("visible", True)
            ]
        channel_indices = [int(index) for index in channel_indices]
        roi_mask = self._roi_mask(data.shape[1:], roi_type, geometry)
        if int(roi_mask.sum()) <= 0:
            raise ValueError("ROI intensity measurement requires a non-empty ROI.")

        background_values = {int(key): float(value) for key, value in (background_mean_by_channel or {}).items()}
        background_correction = "none"
        if background_roi is not None:
            bg_type = str(background_roi.get("type", "rectangle"))
            bg_geometry = dict(background_roi.get("geometry", {}))
            bg_mask = self._roi_mask(data.shape[1:], bg_type, bg_geometry)
            if int(bg_mask.sum()) <= 0:
                raise ValueError("Background ROI must not be empty.")
            background_values = {
                int(index): float(data[int(index)][bg_mask].mean()) if bg_mask.any() else 0.0
                for index in channel_indices
            }
            background_correction = "local_background_roi"
        elif background_values:
            background_correction = "fixed_value_per_channel"

        rows = ROIIntensityAnalyzer().measure(image, data, roi_mask, channel_indices, background_values)
        channel_names = {
            int(channel.get("index", 0)): str(channel.get("name", f"Ch{int(channel.get('index', 0)) + 1}"))
            for channel in image.get("channels", [])
        }
        scale = _resolved_image_scale(image)
        now = utc_now_iso()
        measurement = {
            "measurement_uuid": str(uuid4()),
            "type": "roi_intensity",
            "image_uuid": image_uid,
            "created_at": now,
            "modified_at": now,
            "mode": "assisted",
            "source_channels": [channel_names.get(index, f"Ch{index + 1}") for index in channel_indices],
            "roi_or_geometry": {
                "type": roi_type,
                "coordinates_px": geometry,
                "mask_path": None,
            },
            "params": {
                "background_correction": background_correction,
                "image_background_correction": dict(image.get("background_correction", {"mode": "none"})),
                "background_roi": background_roi,
                "background_mean_by_channel": background_values,
                "threshold_policy": None,
                "normalization": "raw",
                "pixel_size_um": float(scale["um_per_px"]) if _scale_is_calibrated(scale) and float(scale.get("um_per_px") or 0.0) > 0 else None,
            },
            "measurement_scale_bar": dict(scale),
            "results_summary": {
                "n_rows": len(rows),
                "primary_result": "1 ROI measured",
                "channels": [channel_names.get(index, f"Ch{index + 1}") for index in channel_indices],
            },
            "result_files": {
                "csv": None,
                "plot": None,
                "overlay": None,
            },
            "results_table": rows,
            "derived_panel_uuid": None,
            "audit_log": [],
        }

        images = self.images()
        for item in images:
            if item.get("uid") == image_uid:
                item.setdefault("measurements", []).append(measurement)
                break
        self.data["fluorescence_images"] = images
        measurements = list(self.analysis.get("measurements", []))
        measurements.append(measurement)
        self.analysis["measurements"] = measurements
        self._invalidate_fluoro_statistics()
        self.append_audit("create_roi_intensity_measurement", f"{image_uid} {roi_type} {geometry}; channels={channel_indices}")
        return measurement

    def measure_rectangle_roi(self, image_uid: str, x: int, y: int, w: int, h: int, channel_index: int = 0) -> dict[str, Any]:
        return self.measure_roi(image_uid, "rectangle", {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}, channel_index)

    def measure_roi(self, image_uid: str, roi_type: str, geometry: dict[str, Any], channel_index: int = 0) -> dict[str, Any]:
        data = self.image_data(image_uid)
        if data is None:
            raise FileNotFoundError("Fluorescence source image data is not available.")
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        data = self._analysis_stack(image, data)
        channel = data[int(channel_index)]
        mask = self._roi_mask(channel.shape, roi_type, geometry)
        pixels = channel[mask]
        scale = _resolved_image_scale(image)
        area_px = int(mask.sum())
        return {
            "channel_index": int(channel_index),
            "roi_type": roi_type,
            "area_px": area_px,
            "area_um2": _physical_area_um2(area_px, scale),
            "mean": float(np.mean(pixels)) if pixels.size else 0.0,
            "integrated_density": float(np.sum(pixels)) if pixels.size else 0.0,
            "measurement_scale_bar": dict(scale),
        }

    def _roi_mask(self, shape: tuple[int, int], roi_type: str, geometry: dict[str, Any]) -> np.ndarray:
        from skimage.draw import ellipse as draw_ellipse
        from skimage.draw import polygon as draw_polygon
        height, width = shape
        mask = np.zeros(shape, dtype=bool)
        if roi_type in {"rectangle", "square"}:
            x = int(round(float(geometry.get("x", 0))))
            y = int(round(float(geometry.get("y", 0))))
            w = int(round(float(geometry.get("w", 0))))
            h = int(round(float(geometry.get("h", 0))))
            x0 = max(0, min(x, width))
            y0 = max(0, min(y, height))
            x1 = max(x0, min(x0 + max(w, 0), width))
            y1 = max(y0, min(y0 + max(h, 0), height))
            mask[y0:y1, x0:x1] = True
            return mask
        if roi_type in {"ellipse", "circle"}:
            cx = float(geometry.get("cx", 0))
            cy = float(geometry.get("cy", 0))
            rx = max(0.0, float(geometry.get("rx", 0)))
            ry = max(0.0, float(geometry.get("ry", 0)))
            rr, cc = draw_ellipse(cy, cx, ry, rx, shape=shape)
            mask[rr, cc] = True
            return mask
        if roi_type in {"polygon", "freehand"}:
            points = geometry.get("points", [])
            if len(points) < 3:
                return mask
            xs = np.array([float(point[0]) for point in points], dtype=float)
            ys = np.array([float(point[1]) for point in points], dtype=float)
            rr, cc = draw_polygon(ys, xs, shape=shape)
            mask[rr, cc] = True
            return mask
        raise ValueError(f"Unsupported fluorescence ROI type: {roi_type}")

    def _threshold_value(self, image_uid: str, channel_index: int) -> float:
        image = next((item for item in self.images() if item.get("uid") == image_uid), {})
        for threshold in reversed(image.get("thresholds", [])):
            if int(threshold.get("channel_index", -1)) == int(channel_index):
                return float(threshold.get("value", 0.0))
        return 0.0

    def _threshold_for_channel(self, channel: np.ndarray, method: str, manual_value: float) -> float:
        from skimage.filters import threshold_li
        from skimage.filters import threshold_otsu
        from skimage.filters import threshold_triangle
        if method == "manual":
            return float(manual_value)
        if method == "otsu":
            return float(threshold_otsu(channel))
        if method == "triangle":
            return float(threshold_triangle(channel))
        if method == "li":
            return float(threshold_li(channel))
        raise ValueError(f"Unsupported threshold method: {method}")

    def _store_threshold_mask(self, image_uid: str, channel_index: int, method: str, threshold_value: float, mask: np.ndarray) -> None:
        config = ThresholdConfig(channel_index=channel_index, method=method, value=threshold_value, auto_value=threshold_value).to_dict()
        images = self.images()
        for image in images:
            if image.get("uid") == image_uid:
                thresholds = [item for item in image.get("thresholds", []) if int(item.get("channel_index", -1)) != int(channel_index)]
                thresholds.append(config)
                image["thresholds"] = thresholds
                break
        self.data["fluorescence_images"] = images
        self.analysis.setdefault("threshold_masks", {})[f"{image_uid}:{channel_index}"] = {
            "method": method,
            "value": threshold_value,
            "positive_pixels": int(mask.sum()),
        }

    def _append_object_count_results(self, image_uid: str, results: list[dict[str, Any]]) -> None:
        if not results:
            return
        for result in results:
            result.setdefault('measurement_scale_bar', deepcopy(self.measurement_scale_bar(image_uid)))
        all_results = list(self.analysis.get("object_counts", []))
        all_results.extend(results)
        self.analysis["object_counts"] = all_results
        self._invalidate_fluoro_statistics()
        images = self.images()
        for image in images:
            if image.get("uid") == image_uid:
                rows = list(image.get("object_counts", []))
                rows.extend(results)
                image["object_counts"] = rows
                break
        self.data["fluorescence_images"] = images

    def _append_smart_assisted_measurements(self, image_uid: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        rows = list(self.analysis.get(SMART_ASSISTED_MEASUREMENTS_KEY, []))
        rows.extend(deepcopy(records))
        self.analysis[SMART_ASSISTED_MEASUREMENTS_KEY] = rows
        self._invalidate_fluoro_statistics()
        images = self.images()
        for image in images:
            if str(image.get("uid")) == str(image_uid):
                image_rows = list(image.get(SMART_ASSISTED_MEASUREMENTS_KEY, []))
                image_rows.extend(deepcopy(records))
                image[SMART_ASSISTED_MEASUREMENTS_KEY] = image_rows
                break
        self.data["fluorescence_images"] = images

    def _append_ai_assisted_measurements(self, image_uid: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        rows = list(self.analysis.get(AI_ASSISTED_MEASUREMENTS_KEY, []))
        rows.extend(deepcopy(records))
        self.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = rows
        self._invalidate_fluoro_statistics()
        images = self.images()
        for image in images:
            if str(image.get("uid")) == str(image_uid):
                image_rows = list(image.get(AI_ASSISTED_MEASUREMENTS_KEY, []))
                image_rows.extend(deepcopy(records))
                image[AI_ASSISTED_MEASUREMENTS_KEY] = image_rows
                break
        self.data["fluorescence_images"] = images

    def _invalidate_fluoro_statistics(self) -> None:
        self.analysis.pop("fluoro_statistics", None)

    def _normalize_smart_settings(self, settings: FluorescenceSmartSettings | dict[str, Any] | None) -> FluorescenceSmartSettings:
        if settings is None:
            return FluorescenceSmartSettings()
        if isinstance(settings, FluorescenceSmartSettings):
            return settings
        if isinstance(settings, dict):
            allowed = set(FluorescenceSmartSettings.__dataclass_fields__)
            payload = {key: value for key, value in settings.items() if key in allowed}
            return FluorescenceSmartSettings(**payload)
        raise TypeError("settings must be FluorescenceSmartSettings, dict, or None.")

    def _normalize_ai_settings(self, settings: FluorescenceAIAssistedSettings | dict[str, Any] | None) -> FluorescenceAIAssistedSettings:
        if settings is None:
            return FluorescenceAIAssistedSettings()
        if isinstance(settings, FluorescenceAIAssistedSettings):
            return settings
        if isinstance(settings, dict):
            allowed = {field.name for field in FluorescenceAIAssistedSettings.__dataclass_fields__.values()}
            payload = {key: value for key, value in settings.items() if key in allowed}
            return FluorescenceAIAssistedSettings(**payload)
        raise TypeError("settings must be FluorescenceAIAssistedSettings, dict, or None.")

    def _smart_settings_request_black_balance(self, settings: FluorescenceSmartSettings | dict[str, Any] | None) -> bool:
        if isinstance(settings, dict):
            return bool(settings.get("use_existing_black_balance", settings.get("use_black_balance", False)))
        return False

    def _smart_input_preprocessing_context(
        self,
        *,
        image: dict[str, Any],
        channel_index: int,
        roi_context: dict[str, Any],
        use_black_balance: bool,
        image_background_correction: dict[str, Any],
        core_effective_settings_hash: str,
    ) -> dict[str, Any]:
        return {
            "use_black_balance": bool(use_black_balance),
            "image_background_correction": dict(image_background_correction),
            "image_uid": str(image.get("uid", "")),
            "source_hash": image.get("source_hash"),
            "source_name": image.get("source_name"),
            "channel_index": int(channel_index),
            "roi_or_geometry": dict(roi_context),
            "core_effective_settings_hash": str(core_effective_settings_hash),
        }

    def _ai_input_preprocessing_context(
        self,
        *,
        image: dict[str, Any],
        channel_index: int,
        roi_context: dict[str, Any],
        image_background_correction: dict[str, Any],
        core_effective_settings_hash: str,
        model_execution_mode: str,
        model_id: str,
        model_version: str,
        ai_pack_postprocess: str,
        model_commercial_use_status: str,
    ) -> dict[str, Any]:
        return {
            "image_uid": str(image.get("uid", "")),
            "image_source_hash": str(image.get("source_hash", image.get("raw_hash", ""))),
            "channel_index": int(channel_index),
            "roi_or_geometry": dict(roi_context),
            "input_preprocessing_context": {
                "image_background_correction": dict(image_background_correction),
                "normalization": "raw_image_units",
            },
            "image_background_correction": dict(image_background_correction),
            "core_effective_settings_hash": str(core_effective_settings_hash),
            "model_execution_mode": str(model_execution_mode),
            "model_id": str(model_id),
            "model_version": str(model_version),
            "ai_pack_postprocess": str(ai_pack_postprocess),
            "model_commercial_use_status": str(model_commercial_use_status),
        }

    def _smart_roi_context(
        self,
        shape: tuple[int, int],
        *,
        roi_type: str | None,
        geometry: dict[str, Any] | None,
        roi_mask: np.ndarray | None,
    ) -> dict[str, Any]:
        if roi_mask is not None:
            mask = np.asarray(roi_mask, dtype=bool)
            if mask.ndim != 2 or mask.shape != shape:
                raise ValueError(f"roi_mask shape must be {shape}, got {mask.shape}.")
            reconstructable = roi_type is not None and bool(geometry)
            context = {
                "type": roi_type or "mask",
                "coordinates_px": dict(geometry or {}),
                "mask_path": None,
                "area_px": int(mask.sum()),
                "reconstructable": bool(reconstructable),
                "transient": not bool(reconstructable),
            }
            if not reconstructable:
                context["persistence_warning"] = "external_roi_mask_not_persisted"
            return context
        if roi_type is not None and geometry is not None:
            mask = self._roi_mask(shape, roi_type, geometry)
            return {
                "type": roi_type,
                "coordinates_px": dict(geometry),
                "mask_path": None,
                "area_px": int(mask.sum()),
                "reconstructable": True,
                "transient": False,
            }
        return {
            "type": "full_image",
            "coordinates_px": {},
            "mask_path": None,
            "area_px": int(shape[0] * shape[1]),
            "reconstructable": True,
            "transient": False,
        }

    def _detected_spots_from_regions(self, regions) -> list[dict[str, Any]]:
        import cv2
        spots = []
        for region in regions:
            min_row, min_col, max_row, max_col = region.bbox
            centroid_y, centroid_x = region.centroid
            contours, _ = cv2.findContours(np.asarray(region.image, dtype=np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            contour = max(contours, key=cv2.contourArea) if contours else np.empty((0, 1, 2))
            points = [[float(p[0][0] + min_col), float(p[0][1] + min_row)] for p in contour]
            spots.append(
                {
                    "object_id": int(region.label),
                    "contour_points_px": points,
                    "x": int(min_col),
                    "y": int(min_row),
                    "w": int(max_col - min_col),
                    "h": int(max_row - min_row),
                    "centroid_x": float(centroid_x),
                    "centroid_y": float(centroid_y),
                    "area_px": int(region.area),
                }
            )
        return spots

    def _update_image_record(self, image_uid: str, updates: dict[str, Any], audit_action: str) -> None:
        images = self.images()
        for image in images:
            if image.get("uid") == image_uid:
                image.update(updates)
                self.data["fluorescence_images"] = images
                self.append_audit(audit_action, f"{image_uid}: {updates}")
                return
        raise KeyError(f"Fluorescence image not found: {image_uid}")
