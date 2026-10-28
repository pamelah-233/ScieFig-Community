from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import importlib
import math
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np

from core.utils.hashing import sha256_file
from core.utils.image_formats import FLUORESCENCE_IMAGE_SUFFIXES, IHC_IMAGE_SUFFIXES, compound_suffix


SUPPORTED_IO_SUFFIXES = FLUORESCENCE_IMAGE_SUFFIXES
SUPPORTED_MICROSCOPY_SUFFIXES = IHC_IMAGE_SUFFIXES | {".czi", ".nd2"}
_REDUCIBLE_CZI_AXES = {"B", "M", "P", "S", "V"}


@dataclass
class ImageImportResult:
    analysis_data: np.ndarray
    metadata: dict[str, Any]
    source_hash: str
    channel_names: list[str]


def read_image(path: str | Path) -> ImageImportResult:
    source = Path(path)
    suffix = compound_suffix(source)
    if suffix not in SUPPORTED_IO_SUFFIXES:
        raise ValueError(f"Unsupported fluorescence image format: {source.suffix.lower()}")
    if suffix == ".svg":
        return _read_svg(source)
    return read_microscopy_image(source)


def read_microscopy_image(path: str | Path) -> ImageImportResult:
    source = Path(path)
    suffix = compound_suffix(source)
    if suffix not in SUPPORTED_MICROSCOPY_SUFFIXES:
        raise ValueError(f"Unsupported microscopy image format: {source.suffix.lower()}")
    if suffix in {".tif", ".tiff", ".ome.tif", ".ome.tiff"}:
        return _read_tiff(source)
    if suffix in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        return _read_common_raster(source)
    if suffix == ".czi":
        return _read_czi(source)
    if suffix == ".nd2":
        return _read_nd2(source)
    raise ValueError(f"Unsupported microscopy image format: {suffix}")


def read_rgb_image(path: str | Path) -> tuple[np.ndarray, dict[str, Any]]:
    result = read_microscopy_image(path)
    colors = result.metadata.get("channel_colors")
    return cyx_to_rgb_preview(result.analysis_data, channel_colors=colors), dict(result.metadata)


def cyx_to_rgb_preview(data: np.ndarray, channel_colors: Any = None) -> np.ndarray:
    array = np.asarray(data)
    if array.ndim != 3:
        raise ValueError(f"Expected CYX image data, got shape {array.shape}")

    unit = _cyx_to_unit(array)
    channel_count = int(unit.shape[0])
    resolved_colors = resolve_channel_display_colors([], channel_colors, channel_count=channel_count) if channel_colors else []
    if resolved_colors:
        rgb = np.zeros((3, unit.shape[1], unit.shape[2]), dtype=np.float32)
        for index, color in enumerate(resolved_colors[:channel_count]):
            red, green, blue = (int(color[offset : offset + 2], 16) / 255.0 for offset in (1, 3, 5))
            rgb += unit[index : index + 1] * np.asarray([red, green, blue], dtype=np.float32)[:, None, None]
        return np.clip(np.rint(np.moveaxis(np.clip(rgb, 0.0, 1.0), 0, -1) * 255.0), 0, 255).astype(np.uint8)
    if channel_count == 1:
        rgb = np.repeat(unit[:1], 3, axis=0)
    elif channel_count == 2:
        rgb = np.concatenate([unit, np.zeros_like(unit[:1])], axis=0)
    else:
        rgb = unit[:3]
        if channel_count >= 4:
            alpha = unit[3:4]
            rgb = rgb * alpha + (1.0 - alpha)
    return np.clip(np.rint(np.moveaxis(rgb, 0, -1) * 255.0), 0, 255).astype(np.uint8)


def resolve_channel_display_colors(
    channel_names: list[str] | tuple[str, ...],
    embedded_colors: Any = None,
    *,
    channel_count: int | None = None,
) -> list[str]:
    names = [str(name or "") for name in channel_names]
    supplied = list(embedded_colors) if isinstance(embedded_colors, (list, tuple)) else []
    count = max(0, int(channel_count if channel_count is not None else max(len(names), len(supplied))))
    defaults = ["#FF3333", "#00D26A", "#0066FF", "#FF40FF", "#FFFFFF"]
    colors: list[str] = []
    for index in range(count):
        embedded = _normalize_display_color(supplied[index]) if index < len(supplied) else None
        name = names[index] if index < len(names) else ""
        colors.append(embedded or _display_color_from_channel_name(name) or defaults[index % len(defaults)])
    return colors


def _base_metadata(source: Path, data: np.ndarray, source_format: str, axes: str = "CYX") -> dict[str, Any]:
    bit_depth = int(data.dtype.itemsize * 8) if np.issubdtype(data.dtype, np.integer) else 32
    return {
        "pixel_size_um": None,
        "pixel_size_unit": "um",
        "objective": None,
        "bit_depth": bit_depth,
        "width_px": int(data.shape[2]),
        "height_px": int(data.shape[1]),
        "n_channels": int(data.shape[0]),
        "source_format": source_format,
        "acquisition_software": None,
        "axes": axes,
        "source_name": source.name,
        "io_warnings": [],
    }


def _read_tiff(source: Path) -> ImageImportResult:
    import tifffile

    with tifffile.TiffFile(source) as tif:
        series = tif.series[0]
        try:
            raw = tifffile.memmap(source, series=0)
        except Exception:
            raw = series.asarray()
        axes = getattr(series, "axes", "") or ""
        data = normalize_to_cyx(raw, axes=axes)
        metadata = _base_metadata(source, data, "OME-TIFF" if tif.ome_metadata else "TIFF", "CYX")
        metadata.update(_full_resolution_metadata(data))
        channel_names = _parse_ome_channel_names(tif.ome_metadata)
        metadata.update(_parse_ome_metadata(tif.ome_metadata))
        metadata.update(_parse_tiff_xy_resolution(tif, metadata))
        metadata.update(_parse_imagej_metadata(tif.imagej_metadata or {}))
    return ImageImportResult(data, metadata, sha256_file(source), channel_names)


def _read_common_raster(source: Path) -> ImageImportResult:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = None
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGBA" if image.mode in {"RGBA", "LA"} else "RGB" if image.mode not in {"L", "I;16", "I"} else image.mode)
        raw = np.asarray(image)
    data = normalize_to_cyx(raw, axes="YX" if raw.ndim == 2 else "YXC")
    metadata = _base_metadata(source, data, source.suffix.lower().lstrip(".").upper(), "CYX")
    metadata.update(_full_resolution_metadata(data))
    names = _channel_names_for_data(data, raw.ndim)
    return ImageImportResult(data, metadata, sha256_file(source), names)


def _read_svg(source: Path) -> ImageImportResult:
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f"Could not read SVG image: {source}")
    default_size = renderer.defaultSize()
    if default_size.width() > 0 and default_size.height() > 0:
        size = QSize(default_size.width(), default_size.height())
    else:
        size = QSize(1024, 1024)
    image = QImage(size, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    raw = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), image.width(), 4).copy()
    data = normalize_to_cyx(raw, axes="YXC")
    metadata = _base_metadata(source, data, "SVG", "CYX")
    metadata.update(_full_resolution_metadata(data))
    return ImageImportResult(data, metadata, sha256_file(source), ["Red", "Green", "Blue", "Alpha"])


def _read_czi(source: Path) -> ImageImportResult:
    import czifile

    try:
        with czifile.CziFile(str(source)) as czi:
            metadata_xml = czi.metadata()
            scenes = getattr(czi, "scenes", None)
            scene = _first_czi_scene(scenes)
            if scene is not None:
                raw = scene.asarray()
                axes = (getattr(scene, "axes", "") or "").upper()
                scene_metadata = _parse_czi_scene_metadata(scene)
                scene_channel_names = _parse_czi_scene_channel_names(getattr(scene, "channels", None))
                scene_channel_colors = _parse_czi_scene_channel_colors(getattr(scene, "channels", None))
                scene_count = _safe_len(scenes)
            else:
                raw = czi.asarray()
                axes = (getattr(czi, "axes", "") or "").upper()
                scene_metadata = {}
                scene_channel_names = []
                scene_channel_colors = []
                scene_count = 1
    except Exception as exc:
        raise ValueError(
            f"Could not read CZI image '{source.name}'. Only basic single-scene/single-position CZI is supported; export OME-TIFF first."
        ) from exc
    if _czi_has_complex_layout(raw, axes):
        raise ValueError(
            f"Complex CZI layouts are not supported for '{source.name}'. Export this file to OME-TIFF first."
        )
    try:
        data = normalize_to_cyx(raw, axes=axes)
    except Exception as exc:
        raise ValueError(
            f"Complex CZI layouts are not supported for '{source.name}'. Export this file to OME-TIFF first."
        ) from exc
    metadata = _base_metadata(source, data, "CZI", "CYX")
    metadata.update(_full_resolution_metadata(data))
    metadata.update(_parse_czi_metadata(metadata_xml))
    metadata.update(scene_metadata)
    if scene_count > 1:
        metadata["io_warnings"].append(f"CZI contains {scene_count} scenes; displaying first scene.")
    channel_names = scene_channel_names[: data.shape[0]] or _parse_czi_channel_names(metadata_xml, data.shape[0])
    embedded_colors = scene_channel_colors[: data.shape[0]] or _parse_czi_channel_colors(metadata_xml, data.shape[0])
    metadata["channel_colors"] = resolve_channel_display_colors(
        channel_names,
        embedded_colors,
        channel_count=int(data.shape[0]),
    )
    return ImageImportResult(data, metadata, sha256_file(source), channel_names)


def _read_nd2(source: Path) -> ImageImportResult:
    try:
        nd2 = importlib.import_module("nd2")
    except Exception as exc:
        raise ValueError(
            f"Could not open ND2 image '{source.name}'. Export the file to TIFF/OME-TIFF and import that instead."
        ) from exc

    try:
        with nd2.ND2File(source) as handle:
            metadata_obj = getattr(handle, "metadata", None)
            sizes = dict(getattr(handle, "sizes", {}) or {})
            read_frame = getattr(handle, "read_frame", None)
            if callable(read_frame):
                raw = read_frame(0)
                axes = _nd2_read_frame_axes(np.asarray(raw), sizes)
            else:
                raw = handle.asarray()
                axes_payload = getattr(handle, "axes", None)
                if axes_payload:
                    if isinstance(axes_payload, str):
                        axes = axes_payload.upper()
                    else:
                        axes = "".join(str(axis).upper() for axis in axes_payload)
                else:
                    axes = "".join(str(axis).upper() for axis in sizes.keys())
            channel_names = _parse_nd2_channel_names(metadata_obj)
            voxel_sizes = _read_nd2_voxel_size_um(handle)
    except Exception as exc:
        raise ValueError(
            f"Could not open ND2 image '{source.name}'. Export the file to TIFF/OME-TIFF and import that instead."
        ) from exc

    data = normalize_to_cyx(raw, axes=axes)
    metadata = _base_metadata(source, data, "ND2", "CYX")
    metadata.update(_full_resolution_metadata(data))
    metadata.update(voxel_sizes)
    metadata["acquisition_software"] = "Nikon ND2"
    return ImageImportResult(data, metadata, sha256_file(source), channel_names)


def _nd2_read_frame_axes(raw: np.ndarray, sizes: Mapping[str, Any]) -> str:
    if raw.ndim == 2:
        return "YX"
    if raw.ndim != 3:
        return "CYX"
    reported_c = sizes.get("C")
    try:
        channel_count = int(reported_c) if reported_c is not None else None
    except (TypeError, ValueError):
        channel_count = None
    if channel_count is not None and channel_count == raw.shape[0]:
        return "CYX"
    if raw.shape[-1] in (3, 4):
        return "YXC"
    return "CYX"


def normalize_to_cyx(raw: np.ndarray, axes: str = "") -> np.ndarray:
    array = np.asarray(raw)
    array = np.squeeze(array)
    if array.ndim == 2:
        array = array[np.newaxis, :, :]
    elif array.ndim == 3:
        array = _normalize_3d(array, axes)
    elif array.ndim > 3:
        array = _normalize_nd(array, axes)
    else:
        raise ValueError(f"Unsupported fluorescence image dimensions: {array.shape}")
    return np.ascontiguousarray(array)


def _channel_names_for_data(data: np.ndarray, raw_ndim: int) -> list[str]:
    if raw_ndim == 2 or data.shape[0] == 1:
        return ["Gray"]
    names = ["Red", "Green", "Blue", "Alpha"]
    return names[: data.shape[0]]


def _full_resolution_metadata(data: np.ndarray) -> dict[str, int]:
    return {
        "original_width_px": int(data.shape[2]),
        "original_height_px": int(data.shape[1]),
        "work_data_downsample": 1,
    }


def _normalize_3d(array: np.ndarray, axes: str = "") -> np.ndarray:
    axes = axes.upper()
    if "C" in axes and len(axes) == 3:
        channel_axis = axes.index("C")
    elif array.shape[0] <= 12:
        channel_axis = 0
    elif array.shape[-1] <= 12:
        channel_axis = 2
    else:
        channel_axis = 0
    if channel_axis != 0:
        array = np.moveaxis(array, channel_axis, 0)
    return array


def preview_display_max(array: np.ndarray) -> float:
    """Source display range shared by browser previews and their transfers."""
    if np.issubdtype(array.dtype, np.integer):
        return max(1.0, float(np.iinfo(array.dtype).max))
    data = np.asarray(array, dtype=np.float32)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return 1.0
    max_value = float(finite.max())
    if max_value <= 1.0:
        return 1.0
    if max_value <= 255.0:
        return 255.0
    return max_value


def _cyx_to_unit(array: np.ndarray) -> np.ndarray:
    data = np.asarray(array, dtype=np.float32)
    if not np.any(np.isfinite(data)):
        return np.zeros(data.shape, dtype=np.float32)
    return np.clip(data / preview_display_max(array), 0.0, 1.0)


def _normalize_nd(array: np.ndarray, axes: str = "") -> np.ndarray:
    axes = axes.upper()
    if axes and len(axes) == array.ndim and "Y" in axes and "X" in axes:
        y_axis = axes.index("Y")
        x_axis = axes.index("X")
        c_axis = axes.index("C") if "C" in axes else None
        keep_axes = {y_axis, x_axis}
        if c_axis is not None:
            keep_axes.add(c_axis)
        slicer = [slice(None) if index in keep_axes else 0 for index in range(array.ndim)]
        sliced = array[tuple(slicer)]
        if c_axis is None:
            return np.asarray(sliced)[np.newaxis, :, :]
        reduced_axes = "".join(axis for index, axis in enumerate(axes) if index in keep_axes)
        return _normalize_3d(np.asarray(sliced), reduced_axes)
    while array.ndim > 3:
        array = array[0]
    return _normalize_3d(array, "")


def _czi_has_complex_layout(raw: np.ndarray, axes: str) -> bool:
    normalized_axes = str(axes or "").upper()
    if not normalized_axes:
        return False
    shape = tuple(int(size) for size in np.asarray(raw).shape)
    if len(normalized_axes) != len(shape):
        return any(axis in normalized_axes for axis in _REDUCIBLE_CZI_AXES)
    for axis, size in zip(normalized_axes, shape):
        if axis in _REDUCIBLE_CZI_AXES and int(size) > 1:
            return True
    return False


def _positive_finite_um(value: Any) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(resolved) or resolved <= 0:
        return None
    return resolved


def _apply_axis_pixel_size(payload: dict[str, Any], axis: str, um: Any) -> None:
    value = _positive_finite_um(um)
    if value is None:
        return
    if axis == "x":
        payload["pixel_size_um"] = value
        payload["pixel_size_x_um"] = value
    elif axis == "y":
        payload["pixel_size_y_um"] = value


def _apply_ome_physical_size(payload: dict[str, Any], raw_value: Any, unit: Any, axis: str) -> None:
    if raw_value in (None, ""):
        return
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return
    if not unit:
        _apply_axis_pixel_size(payload, axis, value)
        return
    converted = _ome_physical_size_to_um(value, unit)
    if converted is not None:
        _apply_axis_pixel_size(payload, axis, converted)


def _parse_ome_metadata(xml_text: str | None) -> dict[str, Any]:
    if not xml_text:
        return {}
    payload: dict[str, Any] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return payload
    pixels = next((node for node in root.iter() if _strip_ns(node.tag) == "Pixels"), None)
    if pixels is not None:
        _apply_ome_physical_size(payload, pixels.attrib.get("PhysicalSizeX"), pixels.attrib.get("PhysicalSizeXUnit"), "x")
        _apply_ome_physical_size(payload, pixels.attrib.get("PhysicalSizeY"), pixels.attrib.get("PhysicalSizeYUnit"), "y")
        unit = pixels.attrib.get("PhysicalSizeXUnit")
        if unit:
            payload["pixel_size_unit"] = _normalize_um_unit(unit)
    objective = next((node for node in root.iter() if _strip_ns(node.tag) == "Objective"), None)
    if objective is not None:
        nominal = objective.attrib.get("NominalMagnification")
        if nominal:
            payload["objective"] = f"{nominal}x"
    return payload


def _parse_ome_channel_names(xml_text: str | None) -> list[str]:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    names = []
    for node in root.iter():
        if _strip_ns(node.tag) == "Channel":
            names.append(node.attrib.get("Name") or node.attrib.get("ID") or "")
    return names


def _length_unit_to_um(value: float, unit: Any) -> float | None:
    token = _canonical_um_token(str(unit or "")).strip()
    if token in {"micron", "microns", "micrometer", "micrometers", "micrometre", "micrometres"}:
        token = "um"
    return _ome_physical_size_to_um(value, token)


def _tiff_page_xy_resolution(page: Any) -> tuple[float | None, float | None]:
    resolution = getattr(page, "resolution", None)
    if not isinstance(resolution, (tuple, list)) or not resolution:
        return None, None
    x_res = _positive_finite_um(resolution[0])
    y_res = _positive_finite_um(resolution[1]) if len(resolution) > 1 else None
    return x_res, y_res


def _tiff_resolution_unit_code(page: Any) -> int | None:
    tags = getattr(page, "tags", None)
    if tags is None:
        return None
    try:
        tag = tags.get("ResolutionUnit")
    except Exception:
        return None
    if tag is None:
        return None
    try:
        return int(getattr(tag, "value", tag))
    except (TypeError, ValueError):
        return None


def _parse_tiff_xy_resolution(tif: Any, existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    pages = getattr(tif, "pages", None)
    if pages is None:
        return {}
    try:
        page = pages[0]
    except Exception:
        return {}
    x_res, y_res = _tiff_page_xy_resolution(page)
    ij_metadata = getattr(tif, "imagej_metadata", None)
    ij_unit = ij_metadata.get("unit") if isinstance(ij_metadata, Mapping) else None
    um_per_unit = _length_unit_to_um(1.0, ij_unit) if ij_unit else None
    if um_per_unit is not None:
        if x_res == 1.0 and (y_res is None or y_res == 1.0):
            return {}
    elif _tiff_resolution_unit_code(page) == 3:
        um_per_unit = 10_000.0
    else:
        return {}
    payload: dict[str, Any] = {}
    x_um = _positive_finite_um(um_per_unit / x_res) if x_res else None
    y_um = _positive_finite_um(um_per_unit / y_res) if y_res else None
    if x_um is not None and _positive_finite_um(existing.get("pixel_size_x_um")) is None and _positive_finite_um(existing.get("pixel_size_um")) is None:
        _apply_axis_pixel_size(payload, "x", x_um)
    if y_um is not None and _positive_finite_um(existing.get("pixel_size_y_um")) is None:
        _apply_axis_pixel_size(payload, "y", y_um)
    if payload:
        payload["pixel_size_unit"] = "um"
    return payload


def _parse_imagej_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {}


def _czi_distance_um(xml_text: str, axis: str) -> float | None:
    match = re.search(
        rf'<Distance[^>]*Id="{re.escape(axis)}"[^>]*>.*?<Value>([^<]+)</Value>',
        xml_text,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        meters = float(match.group(1))
    except (TypeError, ValueError):
        return None
    return _positive_finite_um(meters * 1_000_000.0)


def _parse_czi_metadata(xml_text: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"acquisition_software": "Zeiss CZI"}
    if not xml_text:
        return payload
    _apply_axis_pixel_size(payload, "x", _czi_distance_um(xml_text, "X"))
    _apply_axis_pixel_size(payload, "y", _czi_distance_um(xml_text, "Y"))
    if "pixel_size_x_um" in payload or "pixel_size_y_um" in payload:
        payload["pixel_size_unit"] = "um"
    objective = re.search(r"<Objective[^>]*>.*?<Magnification>([^<]+)</Magnification>", xml_text, re.DOTALL)
    if objective:
        payload["objective"] = f"{objective.group(1)}x"
    return payload


def _first_czi_scene(scenes: Any) -> Any | None:
    if scenes is None:
        return None
    try:
        if len(scenes) == 0:
            return None
    except Exception:
        pass
    try:
        return scenes[0]
    except Exception:
        try:
            return next(iter(scenes.values()))
        except Exception:
            try:
                return next(iter(scenes))
            except Exception:
                return None


def _safe_len(value: Any, default: int = 1) -> int:
    try:
        return max(0, int(len(value)))
    except Exception:
        return default


def _first_positive_mapping_value(attrs: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _positive_finite_um(attrs.get(key))
        if value is not None:
            return value
    return None


def _czi_scene_axis_scale_um(scene: Any, axis: str) -> float | None:
    scales = getattr(scene, "coord_scales", None)
    if not isinstance(scales, Mapping):
        return None
    try:
        value = float(scales.get(axis))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    units = getattr(scene, "coord_units", None)
    unit = units.get(axis) if isinstance(units, Mapping) else None
    return _positive_finite_um(_czi_scale_to_um(value, unit))


def _parse_czi_scene_metadata(scene: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    attrs = getattr(scene, "attrs", None)
    if isinstance(attrs, Mapping):
        isotropic = _positive_finite_um(attrs.get("pixel_size_um"))
        if isotropic is not None:
            payload["pixel_size_um"] = isotropic
            payload["pixel_size_x_um"] = isotropic
            payload["pixel_size_y_um"] = isotropic
            payload["pixel_size_unit"] = "um"
        x_attr = _first_positive_mapping_value(attrs, ("PhysicalSizeX", "physical_size_x", "pixel_size_x"))
        y_attr = _first_positive_mapping_value(attrs, ("PhysicalSizeY", "physical_size_y", "pixel_size_y"))
        if x_attr is not None:
            payload["pixel_size_um"] = x_attr
            payload["pixel_size_x_um"] = x_attr
            payload["pixel_size_unit"] = "um"
        if y_attr is not None:
            payload["pixel_size_y_um"] = y_attr
            payload["pixel_size_unit"] = "um"

    x_scale = _czi_scene_axis_scale_um(scene, "X")
    y_scale = _czi_scene_axis_scale_um(scene, "Y")
    if x_scale is not None:
        payload["pixel_size_um"] = x_scale
        payload["pixel_size_x_um"] = x_scale
        payload["pixel_size_unit"] = "um"
    if y_scale is not None:
        payload["pixel_size_y_um"] = y_scale
        payload["pixel_size_unit"] = "um"
    return payload


def _parse_czi_scene_channel_names(channels: Any) -> list[str]:
    if isinstance(channels, Mapping):
        names: list[str] = []
        for key, value in channels.items():
            candidate = None
            if isinstance(value, Mapping):
                candidate = value.get("Name") or value.get("name") or value.get("DyeName")
            else:
                candidate = getattr(value, "name", None) or getattr(value, "channel", None)
            names.append(str(candidate or key))
        return names
    if not channels:
        return []
    names = []
    for channel in channels:
        if isinstance(channel, Mapping):
            candidate = channel.get("Name") or channel.get("name") or channel.get("DyeName")
        else:
            candidate = getattr(channel, "name", None) or getattr(channel, "channel", None)
        names.append(str(candidate or channel))
    return names


def _parse_czi_scene_channel_colors(channels: Any) -> list[str]:
    if isinstance(channels, Mapping):
        values = channels.values()
    elif channels:
        values = channels
    else:
        return []
    colors: list[str] = []
    for channel in values:
        if isinstance(channel, Mapping):
            candidate = channel.get("Color") or channel.get("color")
        else:
            candidate = getattr(channel, "color", None) or getattr(channel, "Color", None)
        colors.append(str(candidate or ""))
    return colors


def _ome_physical_size_to_um(value: float, unit: Any) -> float | None:
    normalized = _canonical_um_token(str(unit or "")).strip()
    if normalized not in {
        "m",
        "meter",
        "meters",
        "metre",
        "metres",
        "mm",
        "millimeter",
        "millimeters",
        "millimetre",
        "millimetres",
        "nm",
        "nanometer",
        "nanometers",
        "nanometre",
        "nanometres",
        "um",
    }:
        return None
    return _czi_scale_to_um(value, unit)


def _czi_scale_to_um(value: float, unit: Any) -> float:
    normalized = str(unit or "meter").strip().lower().replace("µ", "u").replace("μ", "u")
    if normalized in {"m", "meter", "meters", "metre", "metres"}:
        return value * 1_000_000.0
    if normalized in {"mm", "millimeter", "millimeters", "millimetre", "millimetres"}:
        return value * 1_000.0
    if normalized in {"nm", "nanometer", "nanometers", "nanometre", "nanometres"}:
        return value / 1_000.0
    return value


def _parse_czi_channel_names(xml_text: str | None, channel_count: int) -> list[str]:
    if not xml_text:
        return []
    names = re.findall(r"<Channel[^>]*Name=\"([^\"]+)\"", xml_text)
    if not names:
        names = re.findall(r"<DyeName>([^<]+)</DyeName>", xml_text)
    return names[:channel_count]


def _parse_czi_channel_colors(xml_text: str | None, channel_count: int) -> list[str]:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    indexed: dict[int, str] = {}
    active: list[str] = []
    for node in root.iter():
        if _strip_ns(node.tag) != "Channel":
            continue
        color = node.attrib.get("Color") or ""
        if not color:
            color_node = next((child for child in node if _strip_ns(child.tag) == "Color"), None)
            color = (color_node.text or "").strip() if color_node is not None else ""
        if not color:
            continue
        match = re.fullmatch(r"Channel:(\d+)", str(node.attrib.get("Id", "")))
        if match:
            indexed[int(match.group(1))] = color
        elif str(node.attrib.get("IsActivated", "")).lower() == "true":
            active.append(color)
    if indexed:
        return [indexed.get(index, "") for index in range(channel_count)]
    return active[:channel_count]


def _normalize_display_color(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if re.fullmatch(r"#[0-9A-F]{8}", text):
        return f"#{text[-6:]}"
    if re.fullmatch(r"#[0-9A-F]{6}", text):
        return text
    return None


def _display_color_from_channel_name(name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "", str(name).lower())
    if any(token in normalized for token in ("dapi", "hoechst", "405")):
        return "#0066FF"
    if any(token in normalized for token in ("alexafluor488", "af488", "fitc", "gfp", "488")):
        return "#00D26A"
    if any(token in normalized for token in ("alexafluor647", "af647", "cy5", "farred", "647", "633")):
        return "#FF40FF"
    if any(token in normalized for token in ("alexafluor594", "af594", "mcherry", "rfp", "tritc", "cy3", "594", "568", "555")):
        return "#FF3333"
    if normalized in {"red", "r"}:
        return "#FF0000"
    if normalized in {"green", "g"}:
        return "#00FF00"
    if normalized in {"blue", "b"}:
        return "#0000FF"
    return None


def _parse_nd2_channel_names(metadata_obj: Any) -> list[str]:
    channels = getattr(metadata_obj, "channels", None)
    if not channels:
        return []
    names: list[str] = []
    for channel in channels:
        channel_name = getattr(channel, "channel", None) or getattr(channel, "name", None)
        if channel_name is None:
            channel_name = str(channel)
        names.append(str(channel_name))
    return names


def _read_nd2_voxel_size_um(handle: Any) -> dict[str, float]:
    voxel_size = getattr(handle, "voxel_size", None)
    if voxel_size is None:
        return {}
    try:
        value = voxel_size() if callable(voxel_size) else voxel_size
    except Exception:
        return {}
    x_value = getattr(value, "x", None)
    y_value = getattr(value, "y", None)
    if isinstance(value, (tuple, list)) and value:
        if x_value is None:
            x_value = value[0]
        if y_value is None and len(value) > 1:
            y_value = value[1]
    payload: dict[str, float] = {}
    x_um = _positive_finite_um(x_value) if x_value else None
    y_um = _positive_finite_um(y_value) if y_value else None
    if x_um is not None:
        payload["pixel_size_um"] = x_um
        payload["pixel_size_x_um"] = x_um
    if y_um is not None:
        payload["pixel_size_y_um"] = y_um
    return payload


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _normalize_um_unit(unit: str) -> str:
    normalized = _canonical_um_token(unit)
    return "um" if normalized in {"um", "micrometer", "micrometre"} else unit


def _canonical_um_token(unit: str) -> str:
    unit = re.sub(r"\\+u(?:00b5|03bc)", "u", unit, flags=re.IGNORECASE)
    return unit.replace("\u00b5", "u").replace("\u03bc", "u").replace("碌", "u").lower()
