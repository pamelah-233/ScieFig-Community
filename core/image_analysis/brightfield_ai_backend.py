"""Generic Brightfield AI backend layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - depends on optional runtime environment
    cv2 = None


class BrightfieldAIBackend(str, Enum):
    TRADITIONAL = "traditional"
    CELLPOSE_CYTO3 = "cellpose_cyto3"


@dataclass(slots=True)
class AIObjectInstances:
    label_mask_orig: np.ndarray
    object_count: int
    object_areas_px: list[int]
    object_centroids: list[tuple[float, float]]
    backend: BrightfieldAIBackend
    backend_metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _base_metadata_and_warnings(height_orig: int, width_orig: int, entry) -> tuple[dict, list[str]]:
    scale_y = float(height_orig) / 256.0
    scale_x = float(width_orig) / 256.0
    aspect_ratio_preserved = height_orig == width_orig
    size_limitation_warning = max(height_orig, width_orig) > 256
    metadata = {
        "inference_shape": [256, 256],
        "scale_y": scale_y,
        "scale_x": scale_x,
        "aspect_ratio_preserved": aspect_ratio_preserved,
        "postprocess_id": "cellpose_flow_v1_minimal",
        "qc_flow_filtering_enabled": False,
        "original_shape": [int(height_orig), int(width_orig)],
        "resized_inference": (height_orig != 256) or (width_orig != 256),
        "size_limitation_warning": size_limitation_warning,
        "model_id": entry.model_id,
        "model_version": entry.version,
        "model_sha256": entry.sha256,
    }
    warnings = ["Research model: restricted commercial-use status"]
    if size_limitation_warning:
        warnings.append("AI count used resized 256×256 inference; small objects may be undercounted.")
    if not aspect_ratio_preserved:
        warnings.append("AI count used non-square input; mask shapes were distorted by direct resize.")
    return metadata, warnings


def get_ai_object_instances(
    rgb: np.ndarray,
    *,
    backend: BrightfieldAIBackend,
    manifest_path: str = "config/ai_pack/manifest.json",
    cache_root=None,
) -> AIObjectInstances:
    """Run the AI backend on an RGB image and return instances in original coordinates."""
    if backend != BrightfieldAIBackend.CELLPOSE_CYTO3:
        raise ValueError(f"Backend not implemented in C-3: {backend!r}")
    if cv2 is None:
        raise ImportError("opencv-python-headless is required for AI backend resize. Install Core deps.")

    from core.ai_pack import load_from_file, load_session, postprocess, run_inference
    from core.ai_pack.postprocessors.cellpose_preprocess import normalize_channels

    manifest = load_from_file(manifest_path)
    entry = manifest.find("cellpose_cyto3")
    if entry is None:
        raise ValueError("Manifest does not contain 'cellpose_cyto3' entry")

    rgb = np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"rgb must be (H, W, 3), got {rgb.shape}")
    height_orig, width_orig = rgb.shape[:2]
    base_metadata, warnings = _base_metadata_and_warnings(height_orig, width_orig, entry)
    scale_y = float(height_orig) / 256.0
    scale_x = float(width_orig) / 256.0

    is_downsample = max(height_orig, width_orig) > 256
    interpolation = cv2.INTER_AREA if is_downsample else cv2.INTER_LINEAR
    interpolation_name = "INTER_AREA" if is_downsample else "INTER_LINEAR"
    base_metadata["resize_interpolation"] = interpolation_name
    rgb_256 = cv2.resize(rgb, (256, 256), interpolation=interpolation)

    if rgb_256.dtype == np.uint8:
        rgb_256f = rgb_256.astype(np.float32) / 255.0
    else:
        rgb_256f = rgb_256.astype(np.float32)
        if float(rgb_256f.max(initial=0.0)) > 1.0:
            rgb_256f = rgb_256f / 255.0

    gray = 0.299 * rgb_256f[..., 0] + 0.587 * rgb_256f[..., 1] + 0.114 * rgb_256f[..., 2]
    channels_2 = np.stack([gray, np.zeros_like(gray)], axis=0).astype(np.float32)
    channels_2 = normalize_channels(channels_2)
    onnx_input = channels_2[None, ...]

    session = load_session(entry, cache_root=cache_root)
    raw_outputs = run_inference(session, onnx_input)

    result = postprocess(raw_outputs, entry)
    if not result.success or result.label_mask is None:
        return AIObjectInstances(
            label_mask_orig=np.zeros((height_orig, width_orig), dtype=np.int32),
            object_count=0,
            object_areas_px=[],
            object_centroids=[],
            backend=backend,
            backend_metadata={
                **base_metadata,
                **(result.metadata or {}),
                "postprocess_failed": True,
                "postprocess_reason": result.reason,
            },
            warnings=warnings,
        )

    label_mask_256 = np.asarray(result.label_mask, dtype=np.int32)
    label_mask_orig = np.asarray(
        cv2.resize(label_mask_256, (width_orig, height_orig), interpolation=cv2.INTER_NEAREST),
        dtype=np.int32,
    )

    areas_at_256 = list(result.metadata.get("instance_area_px_at_256", []))
    if len(areas_at_256) == int(result.object_count):
        object_areas_px = [int(round(area * scale_y * scale_x)) for area in areas_at_256]
    else:
        object_areas_px = [int((label_mask_orig == index).sum()) for index in range(1, int(result.object_count) + 1)]

    centroids: list[tuple[float, float]] = []
    for index in range(1, int(result.object_count) + 1):
        mask = label_mask_256 == index
        if not mask.any():
            centroids.append((0.0, 0.0))
            continue
        ys, xs = np.where(mask)
        centroids.append((float(ys.mean()) * scale_y, float(xs.mean()) * scale_x))

    return AIObjectInstances(
        label_mask_orig=label_mask_orig,
        object_count=int(result.object_count),
        object_areas_px=object_areas_px,
        object_centroids=centroids,
        backend=backend,
        backend_metadata={**base_metadata, **(result.metadata or {})},
        warnings=warnings,
    )
