from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np

from core.image_analysis.fluorescence_smart import (
    FluorescenceSmartSettings,
    analyze_fluorescence_smart_assisted,
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


def _settings(**overrides) -> FluorescenceSmartSettings:
    values = {
        "background_radius_px": 15,
        "min_object_area_px": 8,
        "max_object_area_px": 200,
        "detection_sensitivity": 1.0,
    }
    values.update(overrides)
    return FluorescenceSmartSettings(**values)


def test_fluorescence_smart_detects_single_dot() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=24, x=31, radius=3, intensity=150)

    result = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(),
    )

    channel = result.channel_results[0]
    assert channel.object_count == 1
    detected = channel.objects[0]
    assert detected.channel_index == 0
    assert detected.area_px >= 20
    assert detected.mean_intensity > 100
    assert detected.integrated_intensity > detected.mean_intensity
    assert abs(detected.centroid_y - 24) <= 0.5
    assert abs(detected.centroid_x - 31) <= 0.5


def test_fluorescence_smart_detects_multiple_separated_dots() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=14, x=18, radius=3, intensity=130)
    _add_disk(image, channel=0, y=44, x=47, radius=4, intensity=170)

    result = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(),
    )

    objects = sorted(result.channel_results[0].objects, key=lambda item: item.centroid_y)
    assert len(objects) == 2
    assert abs(objects[0].centroid_y - 14) <= 0.75
    assert abs(objects[0].centroid_x - 18) <= 0.75
    assert abs(objects[1].centroid_y - 44) <= 0.75
    assert abs(objects[1].centroid_x - 47) <= 0.75


def test_fluorescence_smart_rejects_noise_speckles() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=32, x=34, radius=3, intensity=150)
    for y, x in [(4, 5), (9, 52), (21, 8), (55, 17), (58, 60)]:
        image[0, y, x] = 500

    result = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(min_object_area_px=8),
    )

    assert result.channel_results[0].object_count == 1
    assert result.channel_results[0].objects[0].area_px >= 20


def test_fluorescence_smart_noise_rejection_level_tightens_small_object_filtering() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=32, x=34, radius=3, intensity=150)
    image[0, 8:10, 9:11] = 150

    permissive = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(min_object_area_px=2, noise_rejection_level=1.0),
    )
    strict = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(min_object_area_px=2, noise_rejection_level=4.0),
    )

    assert permissive.channel_results[0].metadata["effective_min_object_area_px"] == 2
    assert permissive.channel_results[0].object_count == 2
    assert strict.channel_results[0].metadata["effective_min_object_area_px"] == 8
    assert strict.channel_results[0].object_count == 1


def test_fluorescence_smart_handles_uneven_background() -> None:
    x_gradient = np.linspace(20, 220, 80, dtype=np.float32)
    image = np.repeat(x_gradient[np.newaxis, np.newaxis, :], repeats=64, axis=1)
    _add_disk(image, channel=0, y=24, x=16, radius=3, intensity=90)
    _add_disk(image, channel=0, y=40, x=62, radius=3, intensity=90)

    result = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(background_radius_px=21, min_object_area_px=8),
    )

    objects = result.channel_results[0].objects
    assert len(objects) == 2
    assert {round(item.centroid_x) for item in objects} == {16, 62}
    assert result.metadata["background_method"] == "rolling_percentile"


def test_fluorescence_smart_respects_roi_mask() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=18, x=18, radius=3, intensity=150)
    _add_disk(image, channel=0, y=46, x=46, radius=3, intensity=150)
    roi_mask = np.zeros(image.shape[1:], dtype=bool)
    roi_mask[:32, :32] = True

    result = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(),
        roi_mask=roi_mask,
    )

    assert result.metadata["roi_used"] is True
    assert result.channel_results[0].object_count == 1
    detected = result.channel_results[0].objects[0]
    assert abs(detected.centroid_y - 18) <= 0.5
    assert abs(detected.centroid_x - 18) <= 0.5


def test_fluorescence_smart_separates_channels() -> None:
    image = _blank(channels=2)
    _add_disk(image, channel=0, y=16, x=17, radius=3, intensity=120)
    _add_disk(image, channel=1, y=46, x=43, radius=4, intensity=180)

    result = analyze_fluorescence_smart_assisted(
        image,
        channels=None,
        settings=_settings(),
    )

    assert set(result.channel_results) == {0, 1}
    assert result.channel_results[0].object_count == 1
    assert result.channel_results[1].object_count == 1
    assert abs(result.channel_results[0].objects[0].centroid_y - 16) <= 0.75
    assert abs(result.channel_results[1].objects[0].centroid_y - 46) <= 0.75


def test_fluorescence_smart_blank_image_returns_zero_objects() -> None:
    result = analyze_fluorescence_smart_assisted(
        _blank(channels=2),
        channels=[0, 1],
        settings=_settings(),
    )

    assert result.object_count == 0
    assert result.channel_results[0].object_count == 0
    assert result.channel_results[1].object_count == 0
    assert result.warnings == []


def test_fluorescence_smart_metadata_contains_effective_settings_hash() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=24, x=31, radius=3, intensity=150)

    result = analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(),
    )

    assert result.metadata["analysis_mode"] == "smart_assisted"
    assert result.metadata["analysis_family"] == "fluorescence"
    assert result.metadata["analysis_version"] == "v8_0_smart_fluorescence"
    assert result.metadata["object_count_semantics"] == "detected_fluorescent_objects"
    assert result.metadata["intensity_units"] == "raw_image_units"
    assert result.metadata["effective_settings_hash"].startswith("sha256:")
    assert result.metadata["blob_method"] == "connected_components"
    assert result.channel_results[0].metadata["effective_settings_hash"] == result.metadata["effective_settings_hash"]


def test_fluorescence_smart_settings_are_frozen_after_creation() -> None:
    settings = _settings()

    try:
        settings.noise_rejection_level = 3.0
    except FrozenInstanceError:
        pass
    else:  # pragma: no cover - branch exists to produce a precise assertion failure
        raise AssertionError("FluorescenceSmartSettings should be frozen")


def test_fluorescence_smart_does_not_mutate_input() -> None:
    image = _blank()
    _add_disk(image, channel=0, y=24, x=31, radius=3, intensity=150)
    before = image.copy()

    analyze_fluorescence_smart_assisted(
        image,
        channels=[0],
        settings=_settings(),
    )

    assert np.array_equal(image, before)


def test_fluorescence_smart_accepts_2d_image_as_single_channel() -> None:
    image = np.zeros((48, 52), dtype=np.float32)
    image_3d = image[np.newaxis, ...]
    _add_disk(image_3d, channel=0, y=20, x=25, radius=3, intensity=150)

    result = analyze_fluorescence_smart_assisted(
        image_3d[0],
        channels=None,
        settings=_settings(),
    )

    assert set(result.channel_results) == {0}
    assert result.channel_results[0].object_count == 1
