from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.ai_pack.availability import AIPackAvailability, check_backend_availability, check_model_availability
from core.ai_pack.cache import ai_pack_cache_root, ensure_cache_dir, is_model_cached, model_cache_path
from core.ai_pack.manifest import ManifestError, ModelManifest, ModelManifestEntry, empty_manifest, validate_cache_subdir
from core.ai_pack.postprocess import postprocess
from core.ai_pack.runtime import (
    AIBackendUnavailableError,
    ModelNotCachedError,
    is_ort_available,
    load_session,
    run_inference,
)
from core.utils.hashing import sha256_file


def _synthetic_file_path(tmp_path: Path, name: str = "model.onnx") -> Path:
    path = tmp_path / name
    path.write_bytes(b"synthetic model bytes for cache and manifest tests")
    return path


def _synthetic_onnx_path(tmp_path: Path, name: str = "identity.onnx") -> Path:
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    path = tmp_path / name
    graph = helper.make_graph(
        nodes=[helper.make_node("Identity", ["input"], ["output"])],
        name="identity_graph",
        inputs=[helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3])],
        outputs=[helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3])],
    )
    model = helper.make_model(
        graph,
        producer_name="pytest",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def _entry_payload() -> dict[str, object]:
    return {
        "model_id": "test_identity",
        "display_name": "Test Identity",
        "version": "0.0.1",
        "source_url": "https://example.com/models/identity.onnx",
        "sha256": "sha256:" + ("a" * 64),
        "file_size_bytes": 123,
        "license": "MIT",
        "commercial_use_status": "allowed",
        "input_schema": {"input": {"dtype": "float32", "shape": [1, 3]}},
        "output_schema": {"output": {"dtype": "float32", "shape": [1, 3]}},
        "postprocess": "noop",
        "cache_subdir": "test_identity/0.0.1",
    }


def _entry_for_cached_file(model_path: Path, *, source_url: str | None = None) -> ModelManifestEntry:
    payload = _entry_payload()
    payload["source_url"] = source_url or f"https://example.com/models/{model_path.name}"
    payload["sha256"] = sha256_file(model_path)
    payload["file_size_bytes"] = model_path.stat().st_size
    return ModelManifestEntry.from_dict(payload)


def test_empty_manifest_serializes_to_expected_version() -> None:
    manifest = empty_manifest()

    assert manifest.manifest_version == "1.0"
    assert manifest.entries == []
    assert manifest.to_dict() == {"manifest_version": "1.0", "entries": []}


@pytest.mark.parametrize(
    "field_name",
    [
        "model_id",
        "display_name",
        "version",
        "source_url",
        "sha256",
        "file_size_bytes",
        "license",
        "input_schema",
        "output_schema",
        "postprocess",
        "cache_subdir",
    ],
)
def test_manifest_entry_rejects_missing_required_fields(field_name: str) -> None:
    payload = _entry_payload()
    payload.pop(field_name)

    with pytest.raises(ManifestError, match=field_name):
        ModelManifestEntry.from_dict(payload)


def test_manifest_entry_rejects_missing_commercial_use_status() -> None:
    payload = _entry_payload()
    payload.pop("commercial_use_status")

    with pytest.raises(ManifestError, match="commercial_use_status"):
        ModelManifestEntry.from_dict(payload)


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../escape",
        "..",
        ".",
        "a/b",
        r"a\b",
        "C:foo",
        "name with space",
        "",
        ".hidden",
        "-leadingdash",
        "_leadingunderscore",
    ],
)
def test_manifest_entry_rejects_unsafe_model_id(bad_id: str) -> None:
    payload = _entry_payload()
    payload["model_id"] = bad_id

    with pytest.raises(ManifestError, match="model_id"):
        ModelManifestEntry.from_dict(payload)


@pytest.mark.parametrize(
    "good_id",
    [
        "cyto3",
        "cellpose_nuclei",
        "tissuenet-cp3",
        "model.v2",
        "M123",
    ],
)
def test_manifest_entry_accepts_safe_model_id(good_id: str) -> None:
    payload = _entry_payload()
    payload["model_id"] = good_id

    entry = ModelManifestEntry.from_dict(payload)

    assert entry.model_id == good_id


def test_manifest_entry_rejects_zero_file_size() -> None:
    payload = _entry_payload()
    payload["file_size_bytes"] = 0

    with pytest.raises(ManifestError, match="file_size_bytes"):
        ModelManifestEntry.from_dict(payload)


def test_manifest_entry_rejects_non_integer_file_size() -> None:
    payload = _entry_payload()
    payload["file_size_bytes"] = "abc"

    with pytest.raises(ManifestError, match="file_size_bytes"):
        ModelManifestEntry.from_dict(payload)


@pytest.mark.parametrize("value", ["123", 1.9, True])
def test_manifest_entry_rejects_string_float_and_bool_file_size_bytes(value: object) -> None:
    payload = _entry_payload()
    payload["file_size_bytes"] = value

    with pytest.raises(ManifestError, match="file_size_bytes"):
        ModelManifestEntry.from_dict(payload)


def test_manifest_entry_rejects_sha256_without_prefix() -> None:
    payload = _entry_payload()
    payload["sha256"] = "a" * 64

    with pytest.raises(ManifestError, match="sha256"):
        ModelManifestEntry.from_dict(payload)


@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + ("g" * 64),
        "sha256:" + ("a" * 63),
    ],
)
def test_manifest_entry_rejects_sha256_with_non_hex_or_wrong_length(value: str) -> None:
    payload = _entry_payload()
    payload["sha256"] = value

    with pytest.raises(ManifestError, match="sha256"):
        ModelManifestEntry.from_dict(payload)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("input_schema", []),
        ("input_schema", {}),
        ("output_schema", []),
        ("output_schema", {}),
    ],
)
def test_manifest_entry_rejects_non_dict_or_empty_input_output_schema(
    field_name: str,
    value: object,
) -> None:
    payload = _entry_payload()
    payload[field_name] = value

    with pytest.raises(ManifestError, match=field_name):
        ModelManifestEntry.from_dict(payload)


def test_manifest_entry_unknown_commercial_use_status_falls_back_to_restricted() -> None:
    payload = _entry_payload()
    payload["commercial_use_status"] = "partner-review"

    entry = ModelManifestEntry.from_dict(payload)

    assert entry.commercial_use_status == "restricted"
    assert entry.allows_commercial_use() is False


def test_manifest_entry_rejects_unexpected_keys() -> None:
    payload = _entry_payload()
    payload["unexpected"] = "value"

    with pytest.raises(ManifestError, match="unexpected"):
        ModelManifestEntry.from_dict(payload)


@pytest.mark.parametrize(
    "value",
    [
        "/absolute/path",
        "../escape",
        "nested/../escape",
        "nested//double",
        r"C:\models\test",
        r"folder\..\escape",
    ],
)
def test_manifest_entry_rejects_unsafe_cache_subdir(value: str) -> None:
    payload = _entry_payload()
    payload["cache_subdir"] = value

    with pytest.raises(ManifestError, match="cache_subdir"):
        ModelManifestEntry.from_dict(payload)


def test_validate_cache_subdir_normalizes_backslashes() -> None:
    assert validate_cache_subdir(r"candidate\0.1") == "candidate/0.1"


def test_manifest_rejects_unsupported_version() -> None:
    with pytest.raises(ManifestError, match="Unsupported manifest_version"):
        ModelManifest.from_dict({"manifest_version": "9.9", "entries": []})


def test_manifest_rejects_duplicate_model_id() -> None:
    payload = _entry_payload()
    duplicate_id = "duplicate-id"
    manifest_payload = {
        "manifest_version": "1.0",
        "entries": [
            {**payload, "model_id": duplicate_id, "version": "0.0.1"},
            {**payload, "model_id": duplicate_id, "version": "0.0.2"},
        ],
    }

    with pytest.raises(ManifestError, match="duplicate") as exc_info:
        ModelManifest.from_dict(manifest_payload)

    assert duplicate_id in str(exc_info.value)


def test_manifest_load_from_file_round_trip(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    payload = {
        "manifest_version": "1.0",
        "entries": [
            {
                **_entry_payload(),
                "description": "Synthetic test entry",
                "tags": ["synthetic", "identity"],
            }
        ],
    }
    manifest_path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    manifest = ModelManifest.load_from_file(manifest_path)

    assert manifest.find("test_identity") is not None
    assert manifest.find("missing") is None
    assert manifest.to_dict() == {
        "manifest_version": "1.0",
        "entries": [
            {
                **payload["entries"][0],
                "license_url": "",
            }
        ],
    }


def test_ai_pack_cache_root_ends_with_expected_subdirs() -> None:
    root = ai_pack_cache_root()

    assert root.is_absolute()
    assert root.parts[-3:] == ("ScieFig", "ai_pack", "models")


@pytest.mark.parametrize(
    ("source_url", "expected_name"),
    [
        ("https://example.com/models/identity.onnx", "identity.onnx"),
        ("https://example.com/?token=abc", "test_identity.onnx"),
    ],
)
def test_model_cache_path_uses_source_url_basename_or_model_id_fallback(
    tmp_path: Path,
    source_url: str,
    expected_name: str,
) -> None:
    file_path = _synthetic_file_path(tmp_path, "source.bin")
    entry = _entry_for_cached_file(file_path, source_url=source_url)

    target = model_cache_path(entry, cache_root=tmp_path / "cache")

    assert target.name == expected_name
    assert target.parent == (tmp_path / "cache" / "test_identity" / "0.0.1")


def test_model_cache_path_rejects_unsafe_fallback_filename(tmp_path: Path) -> None:
    entry = ModelManifestEntry(
        model_id="../escape",
        display_name="x",
        version="0.0.1",
        source_url="https://example.com/?token=abc",
        sha256="sha256:" + ("a" * 64),
        file_size_bytes=123,
        license="MIT",
        commercial_use_status="restricted",
        input_schema={"x": {"dtype": "float32", "shape": [1]}},
        output_schema={"y": {"dtype": "float32", "shape": [1]}},
        postprocess="noop",
        cache_subdir="safe/subdir",
    )

    with pytest.raises(ValueError, match="filename"):
        model_cache_path(entry, cache_root=tmp_path / "cache")


def test_ensure_cache_dir_creates_parent_directory(tmp_path: Path) -> None:
    file_path = _synthetic_file_path(tmp_path)
    entry = _entry_for_cached_file(file_path)
    cache_root = tmp_path / "cache"

    target = ensure_cache_dir(entry, cache_root=cache_root)

    assert target == model_cache_path(entry, cache_root=cache_root)
    assert target.parent.is_dir()


def test_is_model_cached_false_when_file_absent(tmp_path: Path) -> None:
    file_path = _synthetic_file_path(tmp_path)
    entry = _entry_for_cached_file(file_path)

    assert is_model_cached(entry, cache_root=tmp_path / "cache") is False


def test_is_model_cached_true_when_present_and_hash_matches(tmp_path: Path) -> None:
    file_path = _synthetic_file_path(tmp_path)
    entry = _entry_for_cached_file(file_path)
    cached_path = ensure_cache_dir(entry, cache_root=tmp_path / "cache")
    cached_path.write_bytes(file_path.read_bytes())

    assert is_model_cached(entry, cache_root=tmp_path / "cache") is True


def test_is_model_cached_false_when_hash_mismatches(tmp_path: Path) -> None:
    file_path = _synthetic_file_path(tmp_path)
    entry = _entry_for_cached_file(file_path)
    cached_path = ensure_cache_dir(entry, cache_root=tmp_path / "cache")
    cached_path.write_bytes(b"mismatched bytes")

    assert is_model_cached(entry, cache_root=tmp_path / "cache") is False


def test_runtime_raises_when_model_not_cached_or_backend_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = _synthetic_file_path(tmp_path)
    entry = _entry_for_cached_file(file_path)

    monkeypatch.setattr("core.ai_pack.runtime.ort", None)
    with pytest.raises(
        AIBackendUnavailableError,
        match="pip install -r requirements.txt -r requirements-ai.txt",
    ):
        load_session(entry, cache_root=tmp_path / "cache")

    class _FakeOrt:
        class InferenceSession:  # pragma: no cover - should never be reached in this test
            def __init__(self, *_args, **_kwargs) -> None:
                raise AssertionError("InferenceSession should not be called for a missing cache file")

    monkeypatch.setattr("core.ai_pack.runtime.ort", _FakeOrt)
    with pytest.raises(ModelNotCachedError):
        load_session(entry, cache_root=tmp_path / "cache")


def test_runtime_loads_and_runs_synthetic_identity_model_when_ai_dev_deps_available(tmp_path: Path) -> None:
    if not is_ort_available():
        pytest.skip("onnxruntime is not installed")

    model_path = _synthetic_onnx_path(tmp_path)
    entry = _entry_for_cached_file(model_path)
    cached_path = ensure_cache_dir(entry, cache_root=tmp_path / "cache")
    cached_path.write_bytes(model_path.read_bytes())

    session = load_session(entry, cache_root=tmp_path / "cache")
    payload = np.asarray([[1.5, 2.5, 3.5]], dtype=np.float32)
    outputs = run_inference(session, payload)

    assert len(outputs) == 1
    np.testing.assert_allclose(outputs[0], payload)


def test_availability_reports_backend_status_correctly() -> None:
    availability = check_backend_availability()

    assert availability.backend_available is is_ort_available()
    assert availability.fully_available is False
    if availability.backend_available:
        assert availability.runtime_version
        assert availability.install_hint == ""
    else:
        expected_hint = "pip install -r requirements.txt -r requirements-ai.txt"
        assert expected_hint in availability.backend_reason
        assert availability.install_hint == expected_hint


def test_availability_reports_model_not_cached_clearly(tmp_path: Path) -> None:
    file_path = _synthetic_file_path(tmp_path)
    entry = _entry_for_cached_file(file_path)

    availability = check_model_availability(entry, cache_root=tmp_path / "cache")

    assert availability.model_available is False
    assert "cached" in availability.model_reason.lower() or "hash" in availability.model_reason.lower()
    assert availability.fully_available is False


def test_availability_reports_fully_available_when_backend_and_model_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = _synthetic_file_path(tmp_path)
    entry = _entry_for_cached_file(file_path)
    cache_path = tmp_path / "cache" / "test_identity" / "0.0.1" / file_path.name

    monkeypatch.setattr(
        "core.ai_pack.availability.check_backend_availability",
        lambda: AIPackAvailability(
            backend_available=True,
            backend_reason="onnxruntime backend available",
            runtime_version="1.18.0",
        ),
    )
    monkeypatch.setattr("core.ai_pack.availability.is_model_cached", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("core.ai_pack.availability.model_cache_path", lambda *_args, **_kwargs: cache_path)

    availability = check_model_availability(entry, cache_root=tmp_path / "cache")

    assert availability.backend_available is True
    assert availability.model_available is True
    assert availability.fully_available is True
    assert availability.runtime_version == "1.18.0"
    assert str(cache_path) in availability.model_reason


def test_postprocess_noop_returns_success_false_with_explicit_reason() -> None:
    entry = ModelManifestEntry.from_dict(_entry_payload())

    result = postprocess([np.zeros((2, 3), dtype=np.float32)], entry)

    assert result.success is False
    assert result.label_mask is None
    assert result.object_count == 0
    assert "noop" in result.reason.lower()
    assert result.metadata["model_id"] == entry.model_id
    assert result.metadata["model_version"] == entry.version
    assert result.metadata["raw_output_shapes"] == [(2, 3)]


def test_postprocess_unknown_pipeline_returns_failure_with_name() -> None:
    payload = _entry_payload()
    payload["postprocess"] = "future-flow-dynamics"
    entry = ModelManifestEntry.from_dict(payload)

    result = postprocess({"mask": np.zeros((1, 1), dtype=np.uint8)}, entry)

    assert result.success is False
    assert result.object_count == 0
    assert "future-flow-dynamics" in result.reason
    assert result.metadata["raw_output_shapes"] == {"mask": (1, 1)}


def test_ai_pack_availability_fully_available_property() -> None:
    unavailable = AIPackAvailability(False, "missing backend")
    available = AIPackAvailability(True, "ok", model_available=True, model_reason="cached")

    assert unavailable.fully_available is False
    assert available.fully_available is True
