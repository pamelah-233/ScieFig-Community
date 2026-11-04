"""Western Blot densitometry: integrated density measurement and normalization."""

from __future__ import annotations

from math import isnan
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QTransform


SATURATION_PIXEL_VALUE = 255
SATURATION_FRACTION_THRESHOLD = 0.35
LOW_SIGNAL_DENSITY_THRESHOLD = 20.0
NEGATIVE_DENSITY_TOLERANCE = -100.0


def qimage_to_gray_numpy(image: QImage) -> np.ndarray:
    """Convert a QImage to a 2D uint8 grayscale array."""
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    height = gray.height()
    width = gray.width()
    ptr = gray.bits()
    ptr.setsize(gray.bytesPerLine() * height)
    array = np.frombuffer(ptr, dtype=np.uint8).reshape((height, gray.bytesPerLine()))
    return array[:, :width].copy()


def _gray_numpy_to_qimage(gray: np.ndarray) -> QImage:
    contiguous = np.ascontiguousarray(gray.astype(np.uint8))
    image = QImage(contiguous.data, contiguous.shape[1], contiguous.shape[0], contiguous.strides[0], QImage.Format.Format_Grayscale8)
    return image.copy()


def apply_geometric_transforms(
    gray: np.ndarray,
    crop_roi: dict,
    rotation: float,
    flip_h: bool,
    flip_v: bool,
) -> np.ndarray:
    """Apply crop, rotation, and flips without brightness/contrast changes."""
    h, w = gray.shape
    x = max(0, min(int(crop_roi.get("x", 0)), max(w - 1, 0)))
    y = max(0, min(int(crop_roi.get("y", 0)), max(h - 1, 0)))
    width = max(1, min(int(crop_roi.get("w", w)), max(w - x, 1)))
    height = max(1, min(int(crop_roi.get("h", h)), max(h - y, 1)))
    cropped = gray[y : y + height, x : x + width].copy()

    if flip_h:
        cropped = np.fliplr(cropped)
    if flip_v:
        cropped = np.flipud(cropped)
    if float(rotation):
        transform = QTransform()
        transform.rotate(float(rotation))
        cropped = qimage_to_gray_numpy(_gray_numpy_to_qimage(cropped).transformed(transform, Qt.TransformationMode.SmoothTransformation))
    return cropped


def detect_polarity(gray: np.ndarray) -> str:
    """Return "dark_on_light" or "bright_on_dark" using center-vs-edge means."""
    if gray.size == 0:
        return "dark_on_light"
    center_h = max(1, gray.shape[0] // 4)
    center_w = max(1, gray.shape[1] // 4)
    center_region = gray[center_h:-center_h or None, center_w:-center_w or None]
    if center_region.size == 0:
        center_region = gray
    edge_h = max(1, gray.shape[0] // 10)
    edge_top = gray[:edge_h, :]
    edge_bottom = gray[-edge_h:, :]
    center_mean = float(np.mean(center_region))
    edge_mean = float(np.mean(np.concatenate([edge_top.ravel(), edge_bottom.ravel()])))
    return "bright_on_dark" if center_mean > edge_mean else "dark_on_light"


def local_edge_background(lane_roi: np.ndarray, edge_fraction: float = 0.1) -> float:
    """Use top and bottom lane ROI edges as local background."""
    if lane_roi.size == 0:
        return 0.0
    h = lane_roi.shape[0]
    edge_h = max(1, int(h * edge_fraction))
    edge_pixels = np.concatenate([lane_roi[:edge_h].ravel(), lane_roi[-edge_h:].ravel()])
    return float(np.mean(edge_pixels))


def rolling_ball_background(gray: np.ndarray, radius: int = 50) -> np.ndarray:
    """Approximate rolling-ball background with a numpy box filter."""
    radius = max(1, int(radius))
    kernel = 2 * radius + 1
    padded = np.pad(gray.astype(np.float64), ((radius, radius), (radius, radius)), mode="edge")
    integral = np.pad(np.cumsum(np.cumsum(padded, axis=0), axis=1), ((1, 0), (1, 0)), mode="constant")
    h, w = gray.shape
    y0 = np.arange(h)
    x0 = np.arange(w)
    y1 = y0 + kernel
    x1 = x0 + kernel
    background = (
        integral[y1[:, None], x1[None, :]]
        - integral[y0[:, None], x1[None, :]]
        - integral[y1[:, None], x0[None, :]]
        + integral[y0[:, None], x0[None, :]]
    ) / float(kernel * kernel)
    return background


def compute_lane_boundaries(lane_centers_x: list[float], strip_width: int) -> list[tuple[int, int]]:
    """Compute (x_start, x_end) lane boundaries from center positions."""
    if strip_width <= 0:
        return []
    centers = sorted(float(center) for center in lane_centers_x)
    if not centers:
        return [(0, int(strip_width))]
    if len(centers) == 1:
        return [(0, int(strip_width))]
    edges = [0.0]
    for left, right in zip(centers, centers[1:]):
        edges.append((left + right) / 2.0)
    edges.append(float(strip_width))
    boundaries = []
    for left, right in zip(edges, edges[1:]):
        x0 = max(0, min(int(round(left)), int(strip_width) - 1))
        x1 = max(x0 + 1, min(int(round(right)), int(strip_width)))
        boundaries.append((x0, x1))
    return boundaries


def measure_densitometry(
    panel_data: dict,
    loading_control_strip_id: str,
    reference_lane_index: int,
    background_method: str = "local_edge",
    polarity_mode: str = "auto",
    rolling_ball_radius: int = 50,
) -> dict:
    """Measure all placed WB strips and normalize to a loading-control strip."""
    strips = list(panel_data.get("band_strips", []))
    placed_ids = list(panel_data.get("placed_strip_ids", []))
    if placed_ids:
        by_id = {str(strip.get("strip_id")): strip for strip in strips}
        strips = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
    if not strips:
        raise ValueError("No Western Blot strips are available for densitometry.")

    lane_labels = list(panel_data.get("annotations", {}).get("lane_labels", []))
    lane_centers = [float(value) for value in panel_data.get("annotations", {}).get("lane_anchors", [])]
    if not lane_labels:
        lane_count = len(lane_centers) if lane_centers else 1
        lane_labels = [f"Lane {index + 1}" for index in range(lane_count)]
    measurements = []
    loading_measurement = None
    for strip in strips:
        strip_id = str(strip.get("strip_id", ""))
        image = QImage(str(strip.get("image_path", "")))
        if image.isNull():
            raise ValueError(f"Could not read Western Blot strip image: {strip.get('image_path', '')}")
        adjustments = dict(strip.get("adjustments", {}))
        gray = apply_geometric_transforms(
            qimage_to_gray_numpy(image),
            dict(strip.get("crop_roi", {})),
            float(adjustments.get("rotation", 0.0)),
            bool(adjustments.get("flip_horizontal", False)),
            bool(adjustments.get("flip_vertical", False)),
        )
        detected = detect_polarity(gray) if polarity_mode == "auto" else polarity_mode
        inverted = detected == "dark_on_light"
        pixels = (255 - gray if inverted else gray).astype(np.float64)
        effective_lane_centers = lane_centers or [
            (index + 0.5) * pixels.shape[1] / max(len(lane_labels), 1)
            for index in range(len(lane_labels))
        ]
        boundaries = compute_lane_boundaries(effective_lane_centers, pixels.shape[1])
        if len(boundaries) != len(lane_labels):
            boundaries = compute_lane_boundaries(
                [(index + 0.5) * pixels.shape[1] / max(len(lane_labels), 1) for index in range(len(lane_labels))],
                pixels.shape[1],
            )
        background_image = rolling_ball_background(pixels, rolling_ball_radius) if background_method == "rolling_ball" else None
        strip_qc_flags = []
        if len(boundaries) != len(lane_labels):
            strip_qc_flags.append("lane_count_mismatch")

        lanes = []
        for lane_index, (x0, x1) in enumerate(boundaries[: len(lane_labels)]):
            lane_roi = pixels[:, x0:x1]
            raw_intensity = float(np.sum(lane_roi))
            if background_method == "local_edge":
                background_value = local_edge_background(lane_roi)
                corrected_values = lane_roi - background_value
            elif background_method == "rolling_ball" and background_image is not None:
                background_slice = background_image[:, x0:x1]
                background_value = float(np.mean(background_slice))
                corrected_values = lane_roi - background_slice
            else:
                background_value = 0.0
                corrected_values = lane_roi
            signed_density = float(np.sum(corrected_values))
            bg_subtracted = float(np.sum(np.maximum(corrected_values, 0)))
            saturation_fraction = float(np.count_nonzero(lane_roi >= SATURATION_PIXEL_VALUE) / lane_roi.size) if lane_roi.size else 0.0
            qc_flags = []
            if lane_roi.size < 100:
                qc_flags.append("roi_too_small")
            if saturation_fraction > SATURATION_FRACTION_THRESHOLD:
                qc_flags.append("saturated")
            if bg_subtracted < LOW_SIGNAL_DENSITY_THRESHOLD:
                qc_flags.append("low_signal")
            if signed_density < NEGATIVE_DENSITY_TOLERANCE:
                qc_flags.append("negative_density")
            lanes.append(
                {
                    "lane_index": lane_index,
                    "lane_label": lane_labels[lane_index],
                    "raw_intensity": _round4(raw_intensity),
                    "background_value": _round4(background_value),
                    "bg_subtracted": _round4(bg_subtracted),
                    "saturation_fraction": _round4(saturation_fraction),
                    "normalized": None,
                    "fold": None,
                    "qc_flags": qc_flags,
                    "roi": {"x": int(x0), "y": 0, "w": int(x1 - x0), "h": int(pixels.shape[0])},
                }
            )

        measurement = {
            "strip_id": strip_id,
            "strip_label": str(strip.get("protein_name") or strip_id),
            "strip_qc_flags": strip_qc_flags,
            "polarity_detected": detected,
            "inverted": inverted,
            "lanes": lanes,
        }
        if strip_id == str(loading_control_strip_id):
            loading_measurement = measurement
        measurements.append(measurement)

    if loading_measurement is None:
        raise ValueError(f"Loading control strip not found: {loading_control_strip_id}")
    if reference_lane_index < 0 or reference_lane_index >= len(lane_labels):
        raise ValueError(f"Reference lane index out of range: {reference_lane_index}")

    loading_lanes = loading_measurement["lanes"]
    for measurement in measurements:
        for lane in measurement["lanes"]:
            lane_index = int(lane["lane_index"])
            loading_density = float(loading_lanes[lane_index]["bg_subtracted"]) if lane_index < len(loading_lanes) else 0.0
            if loading_density <= 0:
                lane["normalized"] = float("nan")
                lane["qc_flags"].append("loading_control_zero")
            else:
                lane["normalized"] = _round4(float(lane["bg_subtracted"]) / loading_density)
        reference_value = measurement["lanes"][reference_lane_index]["normalized"]
        if reference_value is None or (isinstance(reference_value, float) and (isnan(reference_value) or reference_value <= 0)):
            for lane in measurement["lanes"]:
                lane["fold"] = float("nan")
                lane["qc_flags"].append("reference_zero")
        else:
            for lane in measurement["lanes"]:
                normalized = lane["normalized"]
                lane["fold"] = _round4(float(normalized) / float(reference_value)) if normalized == normalized else float("nan")

    return {
        "loading_control_strip_id": str(loading_control_strip_id),
        "loading_control_strip_label": loading_measurement["strip_label"],
        "reference_lane_index": int(reference_lane_index),
        "reference_lane_label": lane_labels[int(reference_lane_index)],
        "background_method": background_method,
        "polarity_mode": polarity_mode,
        "measurement_basis": "raw_cropped",
        "global_qc_flags": [],
        "measurements": measurements,
    }


def _round4(value: float) -> float:
    return round(float(value), 4)
