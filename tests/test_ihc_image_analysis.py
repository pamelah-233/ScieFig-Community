from __future__ import annotations

import importlib

import numpy as np
import pytest

from core.image_analysis import (
    AutoParameterResult,
    BatchThresholdState,
    BRIGHTFIELD_HE_PROFILE,
    DAB_IHC_PROFILE,
    classify_dab_gmm,
    clean_mask,
    compute_hscore,
    deconvolve_h_dab,
    estimate_dab_auto_parameters,
    estimate_stain_vectors_macenko,
    measure_area_fraction,
    measure_channel_intensity,
    render_mask_overlay,
    rgb_to_dab_channel,
    rgb_to_hematoxylin_channel,
    rgb_to_od,
    threshold_channel,
)
from core.image_analysis.stain_engine import (
    _ordered_intensity_labels,
    rgb_to_dab_channel as stain_rgb_to_dab_channel,
)


def _weak_dab_rgb() -> np.ndarray:
    rgb = np.full((10, 10, 3), 245, dtype=np.uint8)
    rgb[2:5, 2:5] = [205, 175, 150]
    rgb[0, 0] = [120, 70, 30]
    return rgb


def _large_two_stain_rgb() -> np.ndarray:
    rgb = np.full((64, 64, 3), 245, dtype=np.uint8)
    rgb[:, :32] = [150, 92, 40]
    rgb[:, 32:] = [95, 125, 190]
    return rgb


def test_hed_deconvolution_separates_dab_from_hematoxylin() -> None:
    rgb = np.zeros((4, 6, 3), dtype=np.uint8)
    rgb[:, 0:2] = [245, 245, 245]
    rgb[:, 2:4] = [135, 75, 28]
    rgb[:, 4:6] = [80, 120, 190]

    dab = rgb_to_dab_channel(rgb)
    hematoxylin = rgb_to_hematoxylin_channel(rgb)

    assert dab.shape == (4, 6)
    assert hematoxylin.shape == (4, 6)
    assert float(dab[:, 2:4].mean()) > float(dab[:, 4:6].mean()) + 0.45
    assert float(dab[:, 2:4].mean()) > float(dab[:, 0:2].mean()) + 0.45
    assert float(hematoxylin[:, 4:6].mean()) > float(hematoxylin[:, 2:4].mean()) + 0.45


def test_public_dab_channel_import_resolves_to_stain_engine_implementation() -> None:
    assert rgb_to_dab_channel is stain_rgb_to_dab_channel


def test_threshold_and_clean_mask_remove_noise_and_fill_holes() -> None:
    channel = np.array(
        [
            [0.1, 0.2, 0.7],
            [0.6, 0.8, 0.3],
        ],
        dtype=float,
    )

    high_mask = threshold_channel(channel, 0.5)
    low_mask = threshold_channel(channel, 0.5, polarity="low_positive")

    assert high_mask.tolist() == [[False, False, True], [True, True, False]]
    assert low_mask.tolist() == [[True, True, False], [False, False, True]]

    noisy = np.zeros((7, 7), dtype=bool)
    noisy[1:6, 1:6] = True
    noisy[3, 3] = False
    noisy[0, 0] = True

    cleaned = clean_mask(noisy, min_area_px=3, fill_holes=True)

    assert cleaned[3, 3]
    assert not cleaned[0, 0]
    assert int(cleaned.sum()) == 25


def test_measure_area_fraction_and_channel_intensity_respect_roi_and_mask() -> None:
    mask = np.array(
        [
            [True, False, True],
            [True, False, False],
        ],
        dtype=bool,
    )
    roi = np.array(
        [
            [True, True, False],
            [True, True, False],
        ],
        dtype=bool,
    )
    channel = np.array(
        [
            [0.9, 0.1, 0.6],
            [0.3, 0.2, 0.8],
        ],
        dtype=float,
    )

    fraction = measure_area_fraction(mask, roi)
    intensity = measure_channel_intensity(channel, mask=mask, roi_mask=roi)

    assert fraction["positive_area_px"] == 2
    assert fraction["total_area_px"] == 4
    assert fraction["positive_area_fraction"] == pytest.approx(0.5)
    assert fraction["positive_area_percent"] == pytest.approx(50.0)
    assert intensity["area_px"] == 2
    assert intensity["mean_intensity"] == pytest.approx(0.6)
    assert intensity["integrated_density"] == pytest.approx(1.2)


def test_hscore_reports_negative_weak_moderate_and_strong_percentages() -> None:
    channel = np.array([[0.1, 0.25, 0.55, 0.85, 0.95]], dtype=float)
    roi = np.array([[True, True, True, True, True]], dtype=bool)

    result = compute_hscore(
        channel,
        roi,
        weak_threshold=0.20,
        moderate_threshold=0.50,
        strong_threshold=0.80,
    )

    assert result["total_area_px"] == 5
    assert result["negative_percent"] == pytest.approx(20.0)
    assert result["weak_percent"] == pytest.approx(20.0)
    assert result["moderate_percent"] == pytest.approx(20.0)
    assert result["strong_percent"] == pytest.approx(40.0)
    assert result["positive_percent"] == pytest.approx(80.0)
    assert result["h_score"] == pytest.approx(180.0)


def test_render_mask_overlay_blends_only_positive_pixels() -> None:
    rgb = np.full((2, 2, 3), 100, dtype=np.uint8)
    mask = np.array([[True, False], [False, False]], dtype=bool)

    overlay = render_mask_overlay(rgb, mask, color=(200, 0, 0), alpha=0.5)

    assert overlay.dtype == np.uint8
    assert overlay.shape == rgb.shape
    assert overlay[0, 0].tolist() == [150, 50, 50]
    assert overlay[0, 1].tolist() == [100, 100, 100]


def test_guided_profiles_and_auto_parameter_state_round_trip_as_dicts() -> None:
    auto_result = AutoParameterResult(
        task_id=DAB_IHC_PROFILE.task_id,
        recommended_thresholds={
            "estimated_positive_threshold": 0.22,
            "effective_positive_threshold": 0.22,
        },
        recommended_tissue_mask_settings={"enabled": True, "mode": "value_and_saturation"},
        recommended_min_area=4,
        recommended_max_area=None,
        recommended_watershed=False,
        confidence_label="medium",
        confidence_score=0.58,
        qc_warnings=["DAB signal appears weak."],
        explanation="Weak DAB signal was detected and background was excluded before measurement.",
        threshold_method="robust_candidate_quantile",
        metadata={"sensitivity": "medium"},
    )
    batch_state = BatchThresholdState(
        task_id=DAB_IHC_PROFILE.task_id,
        batch_threshold=0.22,
        batch_threshold_source_image="image-1",
        batch_threshold_method="robust_candidate_quantile",
        locked=True,
        per_image_overrides={"image-2": 0.19},
        override_warnings=["image-2 used an override threshold"],
    )

    restored_auto = AutoParameterResult.from_dict(auto_result.to_dict())
    restored_batch = BatchThresholdState.from_dict(batch_state.to_dict())

    assert DAB_IHC_PROFILE.to_dict()["task_id"] == "ihc_dab_positive_area"
    assert BRIGHTFIELD_HE_PROFILE.to_dict()["task_id"] == "brightfield_he_eosin_area"
    assert restored_auto.recommended_thresholds["estimated_positive_threshold"] == pytest.approx(0.22)
    assert restored_auto.recommended_min_area == 4
    assert restored_auto.qc_warnings == ["DAB signal appears weak."]
    assert restored_batch.batch_threshold == pytest.approx(0.22)
    assert restored_batch.locked is True
    assert restored_batch.per_image_overrides["image-2"] == pytest.approx(0.19)


def test_estimate_dab_auto_parameters_recommends_threshold_below_manual_default_for_weak_signal() -> None:
    result = estimate_dab_auto_parameters(_weak_dab_rgb())

    assert result.recommended_thresholds["estimated_positive_threshold"] < 0.30
    assert result.recommended_thresholds["effective_positive_threshold"] < 0.30
    assert result.recommended_tissue_mask_settings["enabled"] is True
    assert result.recommended_min_area >= 2
    assert result.threshold_method
    assert result.explanation
    assert result.metadata["dab_channel_source"] == "dab_od"
    assert isinstance(result.qc_warnings, list)


def test_rgb_to_od_and_macenko_estimation_return_hdab_metadata() -> None:
    rgb = _large_two_stain_rgb()

    od = rgb_to_od(rgb)
    estimate = estimate_stain_vectors_macenko(rgb)
    deconvolved = deconvolve_h_dab(rgb)

    assert od.shape == rgb.shape
    assert np.all(od >= 0.0)
    assert estimate["method"] in {"macenko", "default_hdab_fallback"}
    assert np.asarray(estimate["stain_matrix"]).shape == (2, 3)
    assert deconvolved["hematoxylin_od"].shape == rgb.shape[:2]
    assert deconvolved["dab_od"].shape == rgb.shape[:2]
    assert deconvolved["stain_separation"]["method"] == estimate["method"]


def test_macenko_falls_back_for_small_images_with_warning() -> None:
    estimate = estimate_stain_vectors_macenko(_weak_dab_rgb())

    assert estimate["method"] == "default_hdab_fallback"
    assert np.asarray(estimate["stain_matrix"]).shape == (2, 3)
    assert any("valid tissue pixels < 1000" in warning.lower() for warning in estimate["warnings"])


def test_classify_dab_gmm_returns_percentages_and_clean_sklearn_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    dab_od = np.linspace(0.02, 0.9, 256, dtype=np.float64).reshape(16, 16)

    result = classify_dab_gmm(dab_od)

    assert result["classification_method"] in {"gmm_bic", "fixed_bins_fallback"}
    assert result["h_score_method"]
    assert result["gmm_n_components"] in {None, 2, 3, 4}
    assert isinstance(result["class_mapping"], dict)
    assert result["positive_percent"] == pytest.approx(
        result["weak_percent"] + result["moderate_percent"] + result["strong_percent"]
    )
    assert sum(result["percentages"][key] for key in ("negative", "weak", "moderate", "strong")) == pytest.approx(100.0)

    original_import_module = importlib.import_module

    def _blocked_import(name: str, package: str | None = None) -> object:
        if name == "sklearn.mixture":
            raise ModuleNotFoundError("sklearn.mixture")
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _blocked_import)
    fallback = classify_dab_gmm(dab_od)

    assert fallback["classification_method"] == "fixed_bins_fallback"
    assert fallback["h_score_method"] == "fixed_bins_fallback"
    assert fallback["gmm_n_components"] is None
    assert any("scikit-learn is unavailable" in warning.lower() for warning in fallback["warnings"])


def test_classify_dab_gmm_uses_conservative_positive_semantics_for_two_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeGaussianMixture:
        def __init__(self, *, n_components: int, **kwargs) -> None:
            self.n_components = n_components

        def fit(self, samples: np.ndarray) -> "_FakeGaussianMixture":
            if self.n_components == 2:
                self.means_ = np.array([[0.08], [0.62]], dtype=np.float64)
                self.weights_ = np.array([0.5, 0.5], dtype=np.float64)
                self.covariances_ = np.array([[[0.01]], [[0.01]]], dtype=np.float64)
            else:
                self.means_ = np.linspace(0.05, 0.8, self.n_components, dtype=np.float64).reshape(-1, 1)
                self.weights_ = np.full(self.n_components, 1.0 / self.n_components, dtype=np.float64)
                self.covariances_ = np.full((self.n_components, 1, 1), 0.02, dtype=np.float64)
            return self

        def bic(self, samples: np.ndarray) -> float:
            return 0.0 if self.n_components == 2 else float(50 + self.n_components)

        def predict(self, samples: np.ndarray) -> np.ndarray:
            if self.n_components != 2:
                return np.zeros(samples.shape[0], dtype=int)
            return (samples[:, 0] >= 0.40).astype(int)

    class _FakeMixtureModule:
        GaussianMixture = _FakeGaussianMixture

    monkeypatch.setattr(
        "core.image_analysis.stain_engine.importlib.import_module",
        lambda name: _FakeMixtureModule(),
    )

    dab_od = np.linspace(0.05, 0.75, 200, dtype=np.float64).reshape(20, 10)
    result = classify_dab_gmm(dab_od)

    assert _ordered_intensity_labels(2) == ["negative", "positive"]
    assert result["classification_method"] == "gmm_bic"
    assert result["gmm_n_components"] == 2
    assert result["class_mapping"] == {"0": "negative", "1": "positive"}
    assert result["negative_percent"] == pytest.approx(50.0)
    assert result["positive_percent"] == pytest.approx(50.0)
    assert result["strong_percent"] == pytest.approx(0.0)
    assert result["moderate_percent"] == pytest.approx(0.0)
    assert result["weak_percent"] == pytest.approx(result["positive_percent"])
    assert result["negative_percent"] + result["positive_percent"] == pytest.approx(100.0)
    assert result["h_score_method"] == "gmm_2_component_positive_conservative"
    assert result["h_score"] == pytest.approx(result["positive_percent"])
    assert result["h_score"] != pytest.approx(3.0 * result["positive_percent"])


def test_estimate_dab_auto_parameters_handles_od_space_strong_staining() -> None:
    rgb = np.full((128, 128, 3), 240, dtype=np.uint8)
    rgb[32:96, 32:96, 0] = 60
    rgb[32:96, 32:96, 1] = 35
    rgb[32:96, 32:96, 2] = 25
    rng = np.random.default_rng(0)
    rgb = np.clip(
        rgb.astype(np.int16) + rng.integers(-3, 4, rgb.shape, dtype=np.int16),
        0,
        255,
    ).astype(np.uint8)

    result = estimate_dab_auto_parameters(rgb)
    estimated = float(result.recommended_thresholds["estimated_positive_threshold"])
    effective = float(result.recommended_thresholds["effective_positive_threshold"])

    assert estimated > 0.5, (
        "Strong OD-space DAB staining should not be capped by legacy normalized 0-1 threshold bounds."
    )
    assert effective > 0.5


def test_classify_dab_gmm_maps_three_components_to_negative_weak_strong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeGaussianMixture:
        def __init__(self, *, n_components: int, **kwargs) -> None:
            self.n_components = n_components

        def fit(self, samples: np.ndarray) -> "_FakeGaussianMixture":
            self.means_ = np.array([[0.06], [0.28], [0.78]], dtype=np.float64)
            self.weights_ = np.array([0.4, 0.3, 0.3], dtype=np.float64)
            self.covariances_ = np.array([[[0.01]], [[0.01]], [[0.01]]], dtype=np.float64)
            return self

        def bic(self, samples: np.ndarray) -> float:
            return 0.0 if self.n_components == 3 else float(100 + self.n_components)

        def predict(self, samples: np.ndarray) -> np.ndarray:
            return np.clip((samples[:, 0] * 3.0).astype(int), 0, 2)

    class _FakeMixtureModule:
        GaussianMixture = _FakeGaussianMixture

    monkeypatch.setattr(
        "core.image_analysis.stain_engine.importlib.import_module",
        lambda name: _FakeMixtureModule(),
    )

    dab_od = np.linspace(0.05, 0.95, 240, dtype=np.float64).reshape(24, 10)
    result = classify_dab_gmm(dab_od)

    assert _ordered_intensity_labels(3) == ["negative", "weak", "strong"]
    assert result["classification_method"] == "gmm_bic"
    assert result["gmm_n_components"] == 3
    assert result["class_mapping"] == {"0": "negative", "1": "weak", "2": "strong"}
    assert result["moderate_percent"] == pytest.approx(0.0)
    assert result["weak_percent"] > 0.0
    assert result["strong_percent"] > 0.0
