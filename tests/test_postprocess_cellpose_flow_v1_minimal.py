from __future__ import annotations

import math

import numpy as np
import pytest

from core.ai_pack.manifest import ModelManifestEntry
from core.ai_pack.postprocess import postprocess
from core.ai_pack.postprocessors.cellpose_mask_cleanup import apply_minimal_cleanup
from core.ai_pack.postprocessors.cellpose_preprocess import normalize_channels
from core.ai_pack.postprocessors.cellpose_flow_minimal import postprocess_cellpose_flow_v1_minimal


def _entry(postprocess_id: str = "cellpose_flow_v1_minimal") -> ModelManifestEntry:
    return ModelManifestEntry.from_dict(
        {
            "model_id": "cellpose_cyto3",
            "display_name": "Cellpose cyto3",
            "version": "0.1.0",
            "source_url": "file://artifacts/ai_pack/release/cellpose_cyto3_v0.1.0.onnx",
            "sha256": "sha256:" + ("7" * 64),
            "file_size_bytes": 26513469,
            "license": "Cellpose-derived research artifact; personal/research use only pending commercial audit due to CC-BY-NC training-data flag.",
            "commercial_use_status": "restricted",
            "input_schema": {"input": {"dtype": "float32", "shape": [1, 2, 256, 256]}},
            "output_schema": {"output0": {"dtype": "float32", "shape": [1, 3, 256, 256]}},
            "postprocess": postprocess_id,
            "cache_subdir": "cellpose_cyto3/0.1.0",
            "description": "Research-only Cellpose cyto3-derived ONNX artifact for ScieFig AI-assisted object count. Not cleared for commercial distribution.",
        }
    )


def _disk_mask(center_y: int, center_x: int, radius: int) -> np.ndarray:
    yy, xx = np.ogrid[:256, :256]
    return (yy - center_y) ** 2 + (xx - center_x) ** 2 <= radius ** 2


def _single_target_flow(center_y: int, center_x: int, radius: int) -> np.ndarray:
    flow = np.zeros((1, 3, 256, 256), dtype=np.float32)
    mask = _disk_mask(center_y, center_x, radius)
    ys, xs = np.where(mask)
    delta_y = center_y - ys.astype(np.float32)
    delta_x = center_x - xs.astype(np.float32)
    norm = np.maximum(np.sqrt(delta_y**2 + delta_x**2), 1.0)
    flow[0, 0, ys, xs] = (delta_y / norm) * 6.0
    flow[0, 1, ys, xs] = (delta_x / norm) * 6.0
    flow[0, 2, ys, xs] = 1.0
    return flow


def _two_target_flow() -> tuple[np.ndarray, float]:
    flow = np.zeros((1, 3, 256, 256), dtype=np.float32)
    centers = ((64, 64), (192, 192))
    radius = 18
    expected_area = math.pi * radius * radius
    yy, xx = np.mgrid[:256, :256]
    for center_y, center_x in centers:
        mask = _disk_mask(center_y, center_x, radius)
        delta_y = center_y - yy[mask].astype(np.float32)
        delta_x = center_x - xx[mask].astype(np.float32)
        norm = np.maximum(np.sqrt(delta_y**2 + delta_x**2), 1.0)
        flow[0, 0, mask] = (delta_y / norm) * 6.0
        flow[0, 1, mask] = (delta_x / norm) * 6.0
        flow[0, 2, mask] = 1.0
    return flow, expected_area


def test_synthetic_single_attractor_yields_one_instance() -> None:
    result = postprocess_cellpose_flow_v1_minimal([_single_target_flow(128, 128, 20)], _entry())

    assert result.success is True
    assert result.object_count == 1
    assert result.label_mask.shape == (256, 256)
    assert result.metadata["postprocess_id"] == "cellpose_flow_v1_minimal"
    assert result.metadata["qc_flow_filtering_enabled"] is False


def test_synthetic_two_well_separated_attractors_yields_two_instances() -> None:
    raw_outputs, expected_area = _two_target_flow()

    result = postprocess_cellpose_flow_v1_minimal([raw_outputs], _entry())

    assert result.success is True
    assert result.object_count == 2
    areas = sorted(result.metadata["instance_area_px_at_256"])
    assert len(areas) == 2
    for area in areas:
        assert area == pytest.approx(expected_area, rel=0.30)


def test_empty_cellprob_yields_empty_mask() -> None:
    raw_outputs = np.zeros((1, 3, 256, 256), dtype=np.float32)

    result = postprocess_cellpose_flow_v1_minimal([raw_outputs], _entry())

    assert result.success is True
    assert result.object_count == 0
    assert result.metadata["early_exit_empty_seeds"] is True


def test_no_basin_yields_empty_mask() -> None:
    raw_outputs = np.zeros((1, 3, 256, 256), dtype=np.float32)
    mask = _disk_mask(128, 128, 20)
    raw_outputs[0, 2, mask] = 1.0

    result = postprocess_cellpose_flow_v1_minimal([raw_outputs], _entry())

    assert result.success is True
    assert result.object_count == 0
    assert result.metadata.get("early_exit_no_basins") is True
    assert result.metadata.get("early_exit_empty_seeds") is not True


def test_min_size_filter_drops_tiny_instances() -> None:
    raw_outputs = _single_target_flow(128, 128, 20)
    tiny = _single_target_flow(32, 32, 1)
    raw_outputs[0, 0] += tiny[0, 0]
    raw_outputs[0, 1] += tiny[0, 1]
    raw_outputs[0, 2] = np.maximum(raw_outputs[0, 2], tiny[0, 2])

    result = postprocess_cellpose_flow_v1_minimal([raw_outputs], _entry())

    assert result.success is True
    assert result.object_count == 1


def test_unsupported_postprocess_id_returns_failure_no_throw() -> None:
    result = postprocess([np.zeros((1, 3, 256, 256), dtype=np.float32)], _entry("unknown_pipeline_xyz"))

    assert result.success is False
    assert "Unknown postprocess pipeline" in result.reason


def test_wrong_shape_raw_output_returns_failure() -> None:
    raw_outputs = np.zeros((1, 3, 128, 128), dtype=np.float32)

    result = postprocess_cellpose_flow_v1_minimal([raw_outputs], _entry())

    assert result.success is False
    assert "no usable" in result.reason or "shape" in result.reason


def test_normalize_channels_clips_to_unit_range() -> None:
    image = np.zeros((2, 256, 256), dtype=np.float32)
    image[0, :, :128] = -50.0
    image[0, :, 128:] = 500.0
    image[1, :, :] = np.linspace(-10.0, 10.0, 256, dtype=np.float32)[None, :]

    normalized = normalize_channels(image)

    assert normalized.dtype == np.float32
    assert np.all(normalized >= 0.0)
    assert np.all(normalized <= 1.0)


def test_apply_minimal_cleanup_renumbers_contiguously() -> None:
    label_mask = np.array(
        [
            [0, 1, 0, 0],
            [0, 3, 3, 0],
            [0, 0, 0, 7],
        ],
        dtype=np.int32,
    )

    cleaned, object_count, _areas = apply_minimal_cleanup(label_mask, min_size_px=1)

    assert object_count == 3
    assert set(np.unique(cleaned)) == {0, 1, 2, 3}
