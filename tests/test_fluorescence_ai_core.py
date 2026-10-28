from __future__ import annotations

import subprocess
import sys

import numpy as np

from core.image_analysis.fluorescence_ai import (
    AI_ASSISTED_UNAVAILABLE_WARNING,
    FluorescenceAIAssistedSettings,
    FluorescenceAIModelBoundary,
    analyze_fluorescence_ai_assisted,
)


def _blank(channels: int = 1, height: int = 64, width: int = 64) -> np.ndarray:
    return np.zeros((channels, height, width), dtype=np.float32)


def _add_disk(
    image: np.ndarray,
    *,
    channel: int,
    y: float,
    x: float,
    radius: float = 3.0,
    intensity: float = 120.0,
) -> None:
    yy, xx = np.ogrid[: image.shape[1], : image.shape[2]]
    mask = (yy - float(y)) ** 2 + (xx - float(x)) ** 2 <= float(radius) ** 2
    image[channel, mask] += float(intensity)


def _label_disk(
    *,
    height: int = 64,
    width: int = 64,
    y: float,
    x: float,
    radius: float = 3.0,
    label_value: int = 1,
) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.int32)
    yy, xx = np.ogrid[:height, :width]
    disk = (yy - float(y)) ** 2 + (xx - float(x)) ** 2 <= float(radius) ** 2
    mask[disk] = int(label_value)
    return mask


def _settings(**overrides) -> FluorescenceAIAssistedSettings:
    values = {
        "min_object_area_px": 8,
        "max_object_area_px": 200,
        "detection_sensitivity": 1.0,
    }
    values.update(overrides)
    return FluorescenceAIAssistedSettings(**values)


def test_fluorescence_ai_core_detects_single_mock_object() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=24, x=31, radius=3, intensity=150)
    boundary = FluorescenceAIModelBoundary.mock({0: _label_disk(y=24, x=31, radius=3)})

    result = analyze_fluorescence_ai_assisted(
        image,
        channels=[0],
        settings=_settings(),
        model_boundary=boundary,
    )

    channel = result.channel_results[0]
    assert result.metadata["analysis_mode"] == "ai_assisted"
    assert result.metadata["model_execution_mode"] == "mock"
    assert channel.object_count == 1
    detected = channel.objects[0]
    assert detected.channel_index == 0
    assert detected.area_px >= 20
    assert detected.mean_intensity > 100
    assert abs(detected.centroid_y - 24) <= 0.5
    assert abs(detected.centroid_x - 31) <= 0.5


def test_fluorescence_ai_core_detects_multiple_mock_objects() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=14, x=18, radius=3, intensity=130)
    _add_disk(image, channel=0, y=44, x=47, radius=4, intensity=170)
    labels = _label_disk(y=14, x=18, radius=3, label_value=1)
    labels += _label_disk(y=44, x=47, radius=4, label_value=2)

    result = analyze_fluorescence_ai_assisted(
        image,
        channels=[0],
        settings=_settings(),
        model_boundary=FluorescenceAIModelBoundary.mock({0: labels}),
    )

    objects = sorted(result.channel_results[0].objects, key=lambda item: item.centroid_y)
    assert len(objects) == 2
    assert abs(objects[0].centroid_y - 14) <= 0.75
    assert abs(objects[1].centroid_y - 44) <= 0.75


def test_fluorescence_ai_core_preserves_adjacent_instance_labels() -> None:
    image = _blank(height=16, width=16)
    image[0, 4:10, 4:7] = 100
    image[0, 4:10, 7:10] = 180
    labels = np.zeros((16, 16), dtype=np.int32)
    labels[4:10, 4:7] = 1
    labels[4:10, 7:10] = 2

    result = analyze_fluorescence_ai_assisted(
        image,
        channels=[0],
        settings=_settings(min_object_area_px=4, max_object_area_px=0),
        model_boundary=FluorescenceAIModelBoundary.mock({0: labels}),
    )

    channel = result.channel_results[0]
    objects = sorted(channel.objects, key=lambda item: item.centroid_x)
    assert channel.object_count == 2
    assert [item.object_id for item in objects] == [1, 2]
    assert [item.area_px for item in objects] == [18, 18]
    assert abs(objects[0].centroid_x - 5) <= 0.5
    assert abs(objects[1].centroid_x - 8) <= 0.5
    assert set(np.unique(channel.label_mask)) == {0, 1, 2}


def test_fluorescence_ai_core_filters_small_mock_objects() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=32, x=34, radius=3, intensity=150)
    labels = _label_disk(y=32, x=34, radius=3, label_value=1)
    labels[8:10, 9:11] = 2

    result = analyze_fluorescence_ai_assisted(
        image,
        channels=[0],
        settings=_settings(min_object_area_px=8),
        model_boundary=FluorescenceAIModelBoundary.mock({0: labels}),
    )

    assert result.channel_results[0].object_count == 1
    assert result.channel_results[0].objects[0].area_px >= 20


def test_fluorescence_ai_core_respects_roi_mask() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=18, x=18, radius=3, intensity=150)
    _add_disk(image, channel=0, y=46, x=46, radius=3, intensity=150)
    labels = _label_disk(y=18, x=18, radius=3, label_value=1)
    labels += _label_disk(y=46, x=46, radius=3, label_value=2)
    roi_mask = np.zeros(image.shape[1:], dtype=bool)
    roi_mask[:32, :32] = True

    result = analyze_fluorescence_ai_assisted(
        image,
        channels=[0],
        settings=_settings(),
        roi_mask=roi_mask,
        model_boundary=FluorescenceAIModelBoundary.mock({0: labels}),
    )

    assert result.metadata["roi_used"] is True
    assert result.channel_results[0].object_count == 1
    assert abs(result.channel_results[0].objects[0].centroid_y - 18) <= 0.5


def test_fluorescence_ai_core_separates_channels() -> None:
    image = _blank(channels=2)
    _add_disk(image, channel=0, y=16, x=17, radius=3, intensity=120)
    _add_disk(image, channel=1, y=46, x=43, radius=4, intensity=180)
    boundary = FluorescenceAIModelBoundary.mock(
        {
            0: _label_disk(y=16, x=17, radius=3),
            1: _label_disk(y=46, x=43, radius=4),
        }
    )

    result = analyze_fluorescence_ai_assisted(
        image,
        channels=None,
        settings=_settings(),
        model_boundary=boundary,
    )

    assert set(result.channel_results) == {0, 1}
    assert result.channel_results[0].object_count == 1
    assert result.channel_results[1].object_count == 1


def test_fluorescence_ai_core_blank_mock_output_returns_zero_objects() -> None:
    result = analyze_fluorescence_ai_assisted(
        _blank(channels=2),
        channels=[0, 1],
        settings=_settings(),
        model_boundary=FluorescenceAIModelBoundary.mock({0: np.zeros((64, 64), dtype=np.int32), 1: np.zeros((64, 64), dtype=np.int32)}),
    )

    assert result.object_count == 0
    assert result.channel_results[0].object_count == 0
    assert result.channel_results[1].object_count == 0
    assert result.metadata["model_execution_mode"] == "mock"


def test_fluorescence_ai_core_metadata_contains_effective_settings_hash() -> None:
    result = analyze_fluorescence_ai_assisted(
        _blank(),
        channels=[0],
        settings=_settings(model_id="cellpose_cyto3", allow_restricted_model=False),
    )

    assert result.metadata["analysis_mode"] == "ai_assisted"
    assert result.metadata["analysis_family"] == "fluorescence"
    assert result.metadata["analysis_version"] == "v8_1_ai_fluorescence"
    assert result.metadata["object_count_semantics"] == "ai_detected_fluorescent_objects"
    assert result.metadata["effective_settings_hash"].startswith("sha256:")
    assert "do not alter explicit mock label masks" in result.metadata["mock_label_mask_reserved_settings_note"]
    assert result.channel_results[0].metadata["effective_settings_hash"] == result.metadata["effective_settings_hash"]


def test_fluorescence_ai_core_records_restricted_model_boundary() -> None:
    boundary = FluorescenceAIModelBoundary.mock(
        {0: np.zeros((64, 64), dtype=np.int32)},
        model_commercial_use_status="restricted",
        model_license_summary="restricted research model",
    )

    result = analyze_fluorescence_ai_assisted(
        _blank(),
        channels=[0],
        settings=_settings(),
        model_boundary=boundary,
    )

    assert result.metadata["model_execution_mode"] == "mock"
    assert result.is_available is True
    assert result.is_mock is True
    assert result.is_runtime_available is False
    assert result.metadata["model_commercial_use_status"] == "restricted"
    assert result.metadata["model_license_summary"] == "restricted research model"


def test_restricted_model_default_does_not_runtime_execute() -> None:
    boundary = FluorescenceAIModelBoundary(
        model_execution_mode="runtime",
        backend_available=True,
        model_available=True,
        model_commercial_use_status="restricted",
    )

    result = analyze_fluorescence_ai_assisted(
        _blank(),
        channels=[0],
        settings=_settings(allow_restricted_model=False),
        model_boundary=boundary,
    )

    assert result.metadata["model_execution_mode"] == "unavailable"
    assert result.is_available is False
    assert result.is_mock is False
    assert result.is_runtime_available is False
    assert result.object_count == 0
    assert "restricted_model_execution_not_allowed" in result.warnings
    assert AI_ASSISTED_UNAVAILABLE_WARNING in result.warnings


def test_mock_boundary_must_be_explicit() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=24, x=31, radius=3, intensity=150)

    result = analyze_fluorescence_ai_assisted(
        image,
        channels=[0],
        settings=_settings(),
    )

    assert result.metadata["model_execution_mode"] == "unavailable"
    assert result.object_count == 0
    assert AI_ASSISTED_UNAVAILABLE_WARNING in result.warnings


def test_unavailable_model_returns_no_fabricated_objects() -> None:
    boundary = FluorescenceAIModelBoundary(
        model_execution_mode="runtime",
        backend_available=False,
        model_available=False,
        model_commercial_use_status="allowed",
    )

    result = analyze_fluorescence_ai_assisted(
        _blank(),
        channels=[0],
        settings=_settings(),
        model_boundary=boundary,
    )

    assert result.metadata["model_execution_mode"] == "unavailable"
    assert result.object_count == 0
    assert "ai_backend_unavailable" in result.warnings
    assert "ai_model_artifact_unavailable" in result.warnings


def test_fluorescence_ai_core_does_not_mutate_input() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=24, x=31, radius=3, intensity=150)
    before = image.copy()

    analyze_fluorescence_ai_assisted(
        image,
        channels=[0],
        settings=_settings(),
        model_boundary=FluorescenceAIModelBoundary.mock({0: _label_disk(y=24, x=31, radius=3)}),
    )

    assert np.array_equal(image, before)


def test_importing_fluorescence_ai_core_does_not_import_onnxruntime() -> None:
    code = "import sys; import core.image_analysis.fluorescence_ai; assert 'onnxruntime' not in sys.modules"
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_fluorescence_ai_core_does_not_use_smart_assisted_names() -> None:
    result = analyze_fluorescence_ai_assisted(
        _blank(),
        channels=[0],
        settings=_settings(),
    )
    payload = str(result.to_dict())

    assert "smart_assisted" not in payload
    assert "fluorescence_smart_assisted_measurements" not in payload


def test_fluorescence_ai_core_accepts_2d_image_as_single_channel() -> None:
    image = np.zeros((48, 52), dtype=np.float32)
    image_3d = image[np.newaxis, ...]
    _add_disk(image_3d, channel=0, y=20, x=25, radius=3, intensity=150)
    labels = np.zeros((48, 52), dtype=np.int32)
    labels[18:23, 23:28] = 1

    result = analyze_fluorescence_ai_assisted(
        image_3d[0],
        channels=None,
        settings=_settings(),
        model_boundary=FluorescenceAIModelBoundary.mock({0: labels}),
    )

    assert set(result.channel_results) == {0}
    assert result.channel_results[0].object_count == 1
