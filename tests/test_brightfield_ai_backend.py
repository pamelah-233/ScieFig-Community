from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.ai_pack.cache import ensure_cache_dir
from core.ai_pack.manifest import ModelManifestEntry
from core.ai_pack.runtime import AIBackendUnavailableError, ModelNotCachedError, is_ort_available
from core.image_analysis.brightfield_ai_backend import BrightfieldAIBackend, get_ai_object_instances
from core.utils.hashing import sha256_file


def _constant_output_model_path(tmp_path: Path, output_tensor: np.ndarray, name: str = "constant.onnx") -> Path:
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    path = tmp_path / name
    input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 2, 256, 256])
    output_info = helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 3, 256, 256])
    constant = helper.make_tensor(
        "constant_output",
        TensorProto.FLOAT,
        output_tensor.shape,
        output_tensor.astype(np.float32).ravel(),
    )
    node = helper.make_node("Constant", inputs=[], outputs=["output0"], value=constant)
    graph = helper.make_graph([node], "constant_output_graph", [input_info], [output_info])
    model = helper.make_model(graph, producer_name="pytest", opset_imports=[helper.make_opsetid("", 13)])
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def _manifest_path(tmp_path: Path, model_path: Path, *, model_id: str = "cellpose_cyto3") -> Path:
    payload = {
        "manifest_version": "1.0",
        "entries": [
            {
                "model_id": model_id,
                "display_name": "Cellpose cyto3",
                "version": "0.1.0",
                "source_url": "file://artifacts/ai_pack/release/cellpose_cyto3_v0.1.0.onnx",
                "sha256": sha256_file(model_path),
                "file_size_bytes": model_path.stat().st_size,
                "license": "Cellpose-derived research artifact; personal/research use only pending commercial audit due to CC-BY-NC training-data flag.",
                "commercial_use_status": "restricted",
                "input_schema": {"input": {"dtype": "float32", "shape": [1, 2, 256, 256]}},
                "output_schema": {"output0": {"dtype": "float32", "shape": [1, 3, 256, 256]}},
                "postprocess": "cellpose_flow_v1_minimal",
                "cache_subdir": "cellpose_cyto3/0.1.0",
                "description": "Research-only Cellpose cyto3-derived ONNX artifact for ScieFig AI-assisted object count. Not cleared for commercial distribution.",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _entry_from_model(model_path: Path) -> ModelManifestEntry:
    return ModelManifestEntry.from_dict(
        {
            "model_id": "cellpose_cyto3",
            "display_name": "Cellpose cyto3",
            "version": "0.1.0",
            "source_url": "file://artifacts/ai_pack/release/cellpose_cyto3_v0.1.0.onnx",
            "sha256": sha256_file(model_path),
            "file_size_bytes": model_path.stat().st_size,
            "license": "Cellpose-derived research artifact; personal/research use only pending commercial audit due to CC-BY-NC training-data flag.",
            "commercial_use_status": "restricted",
            "input_schema": {"input": {"dtype": "float32", "shape": [1, 2, 256, 256]}},
            "output_schema": {"output0": {"dtype": "float32", "shape": [1, 3, 256, 256]}},
            "postprocess": "cellpose_flow_v1_minimal",
            "cache_subdir": "cellpose_cyto3/0.1.0",
        }
    )


def _single_target_output() -> np.ndarray:
    output = np.zeros((1, 3, 256, 256), dtype=np.float32)
    yy, xx = np.mgrid[:256, :256]
    center_y, center_x, radius = 128, 128, 20
    mask = (yy - center_y) ** 2 + (xx - center_x) ** 2 <= radius**2
    delta_y = center_y - yy[mask].astype(np.float32)
    delta_x = center_x - xx[mask].astype(np.float32)
    norm = np.maximum(np.sqrt(delta_y**2 + delta_x**2), 1.0)
    output[0, 0, mask] = (delta_y / norm) * 6.0
    output[0, 1, mask] = (delta_x / norm) * 6.0
    output[0, 2, mask] = 1.0
    return output


def _cache_model(model_path: Path, cache_root: Path) -> None:
    target = ensure_cache_dir(_entry_from_model(model_path), cache_root=cache_root)
    target.write_bytes(model_path.read_bytes())


def test_get_ai_object_instances_rejects_unsupported_backend() -> None:
    rgb = np.zeros((32, 32, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="Backend not implemented in C-3"):
        get_ai_object_instances(rgb, backend=BrightfieldAIBackend.TRADITIONAL)


def test_get_ai_object_instances_propagates_AIBackendUnavailableError_when_ort_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "placeholder.onnx"
    model_path.write_bytes(b"placeholder")
    manifest_path = _manifest_path(tmp_path, model_path)
    monkeypatch.setattr("core.ai_pack.runtime.ort", None)

    with pytest.raises(AIBackendUnavailableError):
        get_ai_object_instances(
            np.zeros((256, 256, 3), dtype=np.uint8),
            backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
            manifest_path=str(manifest_path),
            cache_root=tmp_path / "cache",
        )


def test_get_ai_object_instances_propagates_ModelNotCachedError_when_cache_empty(tmp_path: Path) -> None:
    if not is_ort_available():
        pytest.skip("onnxruntime is not installed")

    model_path = _constant_output_model_path(tmp_path, _single_target_output())
    manifest_path = _manifest_path(tmp_path, model_path)
    _cache_model(model_path, tmp_path / "cache")
    cached = ensure_cache_dir(_entry_from_model(model_path), cache_root=tmp_path / "cache")
    cached.unlink()

    with pytest.raises(ModelNotCachedError):
        get_ai_object_instances(
            np.zeros((256, 256, 3), dtype=np.uint8),
            backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
            manifest_path=str(manifest_path),
            cache_root=tmp_path / "cache",
        )


def test_get_ai_object_instances_resize_metadata_for_512x512_input(tmp_path: Path) -> None:
    if not is_ort_available():
        pytest.skip("onnxruntime is not installed")

    model_path = _constant_output_model_path(tmp_path, _single_target_output())
    manifest_path = _manifest_path(tmp_path, model_path)
    _cache_model(model_path, tmp_path / "cache")

    instances = get_ai_object_instances(
        np.full((512, 512, 3), 255, dtype=np.uint8),
        backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
        manifest_path=str(manifest_path),
        cache_root=tmp_path / "cache",
    )

    assert instances.backend_metadata["scale_y"] == pytest.approx(2.0)
    assert instances.backend_metadata["scale_x"] == pytest.approx(2.0)
    assert instances.backend_metadata["aspect_ratio_preserved"] is True
    assert instances.backend_metadata["resize_interpolation"] == "INTER_AREA"
    assert instances.backend_metadata["size_limitation_warning"] is True


def test_get_ai_object_instances_aspect_ratio_warning_for_nonsquare(tmp_path: Path) -> None:
    if not is_ort_available():
        pytest.skip("onnxruntime is not installed")

    model_path = _constant_output_model_path(tmp_path, _single_target_output())
    manifest_path = _manifest_path(tmp_path, model_path)
    _cache_model(model_path, tmp_path / "cache")

    instances = get_ai_object_instances(
        np.full((512, 128, 3), 255, dtype=np.uint8),
        backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
        manifest_path=str(manifest_path),
        cache_root=tmp_path / "cache",
    )

    assert instances.backend_metadata["aspect_ratio_preserved"] is False
    assert "AI count used non-square input; mask shapes were distorted by direct resize." in instances.warnings


def test_get_ai_object_instances_no_resize_for_256x256_input(tmp_path: Path) -> None:
    if not is_ort_available():
        pytest.skip("onnxruntime is not installed")

    model_path = _constant_output_model_path(tmp_path, _single_target_output())
    manifest_path = _manifest_path(tmp_path, model_path)
    _cache_model(model_path, tmp_path / "cache")

    instances = get_ai_object_instances(
        np.full((256, 256, 3), 255, dtype=np.uint8),
        backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
        manifest_path=str(manifest_path),
        cache_root=tmp_path / "cache",
    )

    assert instances.backend_metadata["size_limitation_warning"] is False
    assert instances.backend_metadata["resized_inference"] is False
    assert instances.backend_metadata["resize_interpolation"] == "INTER_LINEAR"


def test_get_ai_object_instances_records_resize_interpolation_INTER_LINEAR_on_upsample(tmp_path: Path) -> None:
    if not is_ort_available():
        pytest.skip("onnxruntime is not installed")

    model_path = _constant_output_model_path(tmp_path, _single_target_output())
    manifest_path = _manifest_path(tmp_path, model_path)
    _cache_model(model_path, tmp_path / "cache")

    instances = get_ai_object_instances(
        np.full((128, 128, 3), 255, dtype=np.uint8),
        backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
        manifest_path=str(manifest_path),
        cache_root=tmp_path / "cache",
    )

    assert instances.backend_metadata["resized_inference"] is True
    assert instances.backend_metadata["size_limitation_warning"] is False
    assert instances.backend_metadata["resize_interpolation"] == "INTER_LINEAR"


def test_get_ai_object_instances_areas_use_2d_scale(tmp_path: Path) -> None:
    if not is_ort_available():
        pytest.skip("onnxruntime is not installed")

    model_path = _constant_output_model_path(tmp_path, _single_target_output())
    manifest_path = _manifest_path(tmp_path, model_path)
    _cache_model(model_path, tmp_path / "cache")

    instances = get_ai_object_instances(
        np.full((512, 128, 3), 255, dtype=np.uint8),
        backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
        manifest_path=str(manifest_path),
        cache_root=tmp_path / "cache",
    )

    area_at_256 = instances.backend_metadata["instance_area_px_at_256"][0]
    expected_area = int(round(area_at_256 * instances.backend_metadata["scale_y"] * instances.backend_metadata["scale_x"]))
    assert instances.object_areas_px[0] == expected_area
