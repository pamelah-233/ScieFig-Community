from __future__ import annotations

from typing import Any, Callable

import numpy as np
from PyQt6.QtGui import QImage
from core.plotting.fluoro_display import apply_display_optimization, display_points_active, display_processing_active

RGB_COMPOSITE_MODE = "rgb_composite"
FORCED_RGB_SPLIT_MODE = "forced_rgb_split"
AUTO_SINGLE_SIGNAL_MODE = "auto_single_signal"
COMBINED_INTENSITY_NAME = "Combined intensity"
RGB_COMPONENT_NAMES = ("Red", "Green", "Blue")
RGB_COMPONENT_COLORS = ("#FF0000", "#00FF00", "#0000FF")


def rgb_interpretation_mode(image_record: dict[str, Any] | None) -> str:
    return str(dict((image_record or {}).get("channel_interpretation") or {}).get("mode") or "")


def should_render_original_rgb(
    image_record: dict[str, Any] | None,
    view_mode: str | None = None,
    channel: dict[str, Any] | None = None,
) -> bool:
    if channel is not None:
        return False
    mode = rgb_interpretation_mode(image_record)
    view = str(view_mode if view_mode is not None else (image_record or {}).get("view_mode") or "merge")
    if mode == RGB_COMPOSITE_MODE:
        return view != "split"
    return False


def _rgb_components_are_identity(data, channels) -> bool:
    maximum = float(np.iinfo(data.dtype).max) if np.issubdtype(data.dtype,np.integer) else float(np.nanmax(data[:3]) or 1.0)
    return len(channels) == 3 and all(
        int(channel.get("index",0)) == index
        and channel.get("visible",True)
        and str(channel.get("pseudocolor","")).upper() == RGB_COMPONENT_COLORS[index]
        and float(channel.get("display_min",0)) == 0
        and float(channel.get("display_max",1)) == maximum
        and not display_processing_active(channel.get('display_processing'))
        for index,channel in enumerate(channels)
    )


def render_original_rgb_qimage(data: np.ndarray) -> QImage:
    rgb = np.stack([_identity_u8(data[0]), _identity_u8(data[1]), _identity_u8(data[2])], axis=-1)
    if int(data.shape[0]) >= 4:
        alpha = _identity_u8(data[3])
        rgba = np.ascontiguousarray(np.concatenate([rgb, alpha[:, :, None]], axis=2))
        height, width, _ = rgba.shape
        return QImage(rgba.data, width, height, rgba.strides[0], QImage.Format.Format_RGBA8888).copy()
    return _rgb_array_to_qimage(rgb)


def render_fluorescence_qimage(
    data: np.ndarray,
    image_record: dict[str, Any] | None,
    channels: list[dict[str, Any]],
    *,
    view_mode: str | None = None,
    channel: dict[str, Any] | None = None,
    merge_fn: Callable[..., QImage] | None = None,
    single_fn: Callable[..., QImage] | None = None,
) -> QImage:
    view = str(view_mode if view_mode is not None else (image_record or {}).get("view_mode") or "merge")
    forced = rgb_interpretation_mode(image_record) == FORCED_RGB_SPLIT_MODE
    identity_merge = forced and view == "merge" and channel is None and _rgb_components_are_identity(data,channels)
    if should_render_original_rgb(image_record, view, channel) or identity_merge:
        return render_original_rgb_qimage(data)
    merge = merge_fn or render_merge_qimage
    single = single_fn or render_single_qimage
    if channel is not None or view == "single":
        target = channel
        if target is None:
            visible = [item for item in channels if item.get("visible", True)]
            target = visible[0] if visible else (channels[0] if channels else {"index": 0})
        rendered = single(data, target)
        if rgb_interpretation_mode(image_record) == FORCED_RGB_SPLIT_MODE:
            return apply_rgb_sample_coverage(rendered, data)
        return rendered
    rendered = merge(data, channels)
    alpha_index = (image_record or {}).get('browser_preview_alpha')
    if alpha_index is not None and view == 'merge':
        from core.image_analysis.io_engine import preview_display_max
        alpha = _normalize_channel(data[int(alpha_index)], 0.0, preview_display_max(data))[:, :, None]
        converted = rendered.convertToFormat(QImage.Format.Format_RGB888)
        pointer = converted.constBits()
        pointer.setsize(converted.sizeInBytes())
        rgb = np.frombuffer(pointer, np.uint8).reshape(converted.height(), converted.bytesPerLine())
        rgb = rgb[:, :converted.width() * 3].reshape(converted.height(), converted.width(), 3)
        rendered = _rgb_array_to_qimage(np.rint(rgb * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8))
    return apply_rgb_sample_coverage(rendered,data) if forced else rendered


def apply_rgb_sample_coverage(image: QImage, data: np.ndarray) -> QImage:
    if data.ndim != 3 or int(data.shape[0]) < 4:
        return image
    alpha = _identity_u8(data[3])
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = converted.bits()
    ptr.setsize(converted.sizeInBytes())
    rgba = np.frombuffer(ptr, dtype=np.uint8).reshape(converted.height(), converted.width(), 4).copy()
    if rgba.shape[:2] != alpha.shape:
        return image
    rgba[:, :, 3] = alpha
    rgba[alpha == 0, :3] = 0
    packed = np.ascontiguousarray(rgba)
    height, width, _ = packed.shape
    return QImage(packed.data, width, height, packed.strides[0], QImage.Format.Format_RGBA8888).copy()


def _identity_u8(plane: np.ndarray) -> np.ndarray:
    if plane.dtype == np.uint8:
        return np.ascontiguousarray(plane)
    if np.issubdtype(plane.dtype, np.integer):
        max_value = float(np.iinfo(plane.dtype).max) or 1.0
    else:
        max_value = float(np.nanmax(plane) or 1.0)
        if max_value <= 0:
            max_value = 1.0
    scaled = np.clip(plane.astype(np.float32, copy=False) * (255.0 / max_value), 0, 255)
    return np.ascontiguousarray(scaled.astype(np.uint8))


def render_merge_qimage(data: np.ndarray, channels: list[dict[str, Any]]) -> QImage:
    # Contiguous component planes avoid a full interleaved float RGB temporary
    # for every source channel. Operations and float32 accumulation order stay
    # identical for every output pixel; the source array is never modified.
    planes = [np.zeros(data.shape[1:], dtype=np.float32) for _ in range(3)]
    for channel in channels:
        if not channel.get("visible", True):
            continue
        components = _merged_rgb_components(data, channel)
        if components is not None:
            rgb, alpha = components
            for component in range(3):
                planes[component] += rgb[:, :, component] * alpha
            continue
        index = int(channel.get("index", 0))
        if not (0 <= index < data.shape[0]):
            continue
        normalized = _normalize_channel(_display_plane(data, channel), float(channel.get("display_min", 0)), float(channel.get("display_max", 1)), clip=not display_points_active(channel.get('display_processing')))
        normalized = apply_display_optimization(normalized, channel.get('display_processing'))
        color = np.array(_hex_to_rgb(str(channel.get("pseudocolor", "#FFFFFF"))), dtype=np.float32)
        for component in range(3):
            planes[component] += normalized * color[component]
    rgb = np.empty((*data.shape[1:], 3), dtype=np.uint8)
    browser_rounding = any(channel.get('rgb_components') for channel in channels) or (bool(channels) and all(channel.get('color_source') == 'browser_preview' for channel in channels))
    for component, plane in enumerate(planes):
        np.clip(plane, 0, 255, out=plane)
        if browser_rounding:
            np.rint(plane, out=plane)
        rgb[:, :, component] = plane
    return _rgb_array_to_qimage(rgb)


def render_single_qimage(data: np.ndarray, channel: dict[str, Any], pseudocolor: bool = True) -> QImage:
    components = _merged_rgb_components(data, channel) if pseudocolor else None
    if components is not None:
        rgb, alpha = components
        packed = np.rint(rgb).clip(0, 255).astype(np.uint8)
        if len(channel['rgb_components']) == 4:
            packed = np.ascontiguousarray(np.concatenate([packed, np.rint(alpha[:, :, None]*255).astype(np.uint8)], axis=2))
            height, width, _ = packed.shape
            return QImage(packed.data, width, height, packed.strides[0], QImage.Format.Format_RGBA8888).copy()
        return _rgb_array_to_qimage(packed)
    normalized = _normalize_channel(_display_plane(data, channel), float(channel.get("display_min", 0)), float(channel.get("display_max", 1)), clip=not display_points_active(channel.get('display_processing')))
    normalized = apply_display_optimization(normalized, channel.get('display_processing'))
    if pseudocolor:
        color = np.array(_hex_to_rgb(str(channel.get("pseudocolor", "#FFFFFF"))), dtype=np.float32)
    else:
        color = np.array([255, 255, 255], dtype=np.float32)
    rgb = normalized[:, :, None] * color[None, None, :]
    np.clip(rgb, 0, 255, out=rgb)
    if channel.get('color_source') == 'browser_preview':
        np.rint(rgb, out=rgb)
    return _rgb_array_to_qimage(rgb.astype(np.uint8))


def _display_plane(data, channel):
    indices = channel.get('rgb_components')
    return data[list(indices[:3])].max(axis=0) if indices else data[int(channel.get('index', 0))]


def _merged_rgb_components(data: np.ndarray, channel: dict[str, Any]):
    indices = channel.get('rgb_components')
    if not indices or channel.get('color_source') == 'user':
        return None
    rgb = np.moveaxis(data[list(indices[:3])], 0, -1).astype(np.float32)
    peak = rgb.max(axis=2)
    if (float(channel.get('display_min', 0)) != 0 or float(channel.get('display_max', 255)) != 255
            or display_processing_active(channel.get('display_processing'))):
        normalized = _normalize_channel(peak, float(channel.get('display_min', 0)), float(channel.get('display_max', 255)),
            clip=not display_points_active(channel.get('display_processing')))
        adjusted = apply_display_optimization(normalized, channel.get('display_processing')) * 255
        factor = np.divide(adjusted, peak, out=np.zeros_like(peak), where=peak > 0)
        rgb *= factor[:, :, None]
        np.clip(rgb, 0, 255, out=rgb)
    alpha = data[int(indices[3])].astype(np.float32)/255 if len(indices) == 4 else 1.0
    return rgb, alpha


def _normalize_channel(channel_data: np.ndarray, display_min: float, display_max: float, *, clip: bool = True) -> np.ndarray:
    if display_max <= display_min:
        display_max = display_min + 1.0
    normalized = channel_data.astype(np.float32, copy=True)
    normalized -= display_min
    normalized /= display_max - display_min
    if clip:
        np.clip(normalized, 0.0, 1.0, out=normalized)
    return normalized


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return (255, 255, 255)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _rgb_array_to_qimage(rgb: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(rgb)
    height, width, _ = rgb.shape
    return QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
