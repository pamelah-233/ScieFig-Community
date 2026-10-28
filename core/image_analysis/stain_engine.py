from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
from typing import Any

import numpy as np

DEFAULT_H_DAB_MATRIX = np.asarray(
    [
        [0.650, 0.704, 0.286],
        [0.268, 0.570, 0.776],
    ],
    dtype=np.float64,
)
DEFAULT_H_DAB_MATRIX /= np.linalg.norm(DEFAULT_H_DAB_MATRIX, axis=1, keepdims=True)

_MIN_MACENKO_TISSUE_PIXELS = 1000
_MIN_SIGNAL_P95 = 0.05
_MIN_DYNAMIC_RANGE = 0.08
_MIN_SINGULAR_RATIO = 0.05
_MIN_VECTOR_ANGLE_DEG = 15.0
_MIN_GMM_PIXELS = 64
_MIN_GMM_COMPONENT_WEIGHT = 0.03
_MIN_GMM_MEAN_SEPARATION = 0.05
_FIXED_BIN_WEAK_THRESHOLD = 0.15
_FIXED_BIN_MODERATE_THRESHOLD = 0.50
_FIXED_BIN_STRONG_THRESHOLD = 1.00
_BINARY_GMM_HSCORE_METHOD = "gmm_2_component_positive_conservative"


@dataclass(frozen=True)
class StainEstimate:
    stain_matrix: np.ndarray
    method: str
    warnings: list[str]
    tissue_pixel_count: int
    signal_p95: float
    dynamic_range: float
    singular_values: list[float]
    vector_angle_deg: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stain_matrix": self.stain_matrix.tolist(),
            "method": self.method,
            "warnings": list(self.warnings),
            "tissue_pixel_count": int(self.tissue_pixel_count),
            "signal_p95": float(self.signal_p95),
            "dynamic_range": float(self.dynamic_range),
            "singular_values": [float(value) for value in self.singular_values],
            "vector_angle_deg": None if self.vector_angle_deg is None else float(self.vector_angle_deg),
        }


def rgb_to_dab_channel(rgb: np.ndarray) -> np.ndarray:
    return _extract_hed_channel(rgb, channel_index=2)


def rgb_to_hematoxylin_channel(rgb: np.ndarray) -> np.ndarray:
    return _extract_hed_channel(rgb, channel_index=0)


def rgb_to_od(rgb: np.ndarray) -> np.ndarray:
    rgb_unit = _as_rgb_array(rgb, scale="unit")
    clipped = np.clip(rgb_unit, 1.0 / 255.0, 1.0)
    return -np.log(clipped)


def estimate_stain_vectors_macenko(
    rgb: np.ndarray,
    tissue_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    od = rgb_to_od(rgb)
    selected_mask = _resolve_tissue_mask(od, tissue_mask=tissue_mask)
    selected_od = od[selected_mask]
    tissue_pixel_count = int(selected_od.shape[0])

    if tissue_pixel_count < _MIN_MACENKO_TISSUE_PIXELS:
        return _default_fallback_estimate(
            warning=(
                f"Macenko fallback: valid tissue pixels < {_MIN_MACENKO_TISSUE_PIXELS} "
                f"(got {tissue_pixel_count})."
            ),
            tissue_pixel_count=tissue_pixel_count,
        ).to_dict()

    signal = np.linalg.norm(selected_od, axis=1)
    signal_p95 = float(np.percentile(signal, 95))
    dynamic_range = float(np.percentile(signal, 99) - np.percentile(signal, 1))

    if signal_p95 < _MIN_SIGNAL_P95:
        return _default_fallback_estimate(
            warning="Macenko fallback: weak or near-zero optical density signal.",
            tissue_pixel_count=tissue_pixel_count,
            signal_p95=signal_p95,
            dynamic_range=dynamic_range,
        ).to_dict()

    if dynamic_range < _MIN_DYNAMIC_RANGE:
        return _default_fallback_estimate(
            warning="Macenko fallback: overexposed or compressed optical density dynamic range.",
            tissue_pixel_count=tissue_pixel_count,
            signal_p95=signal_p95,
            dynamic_range=dynamic_range,
        ).to_dict()

    centered = selected_od - np.mean(selected_od, axis=0, keepdims=True)
    try:
        _, singular_values, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return _default_fallback_estimate(
            warning="Macenko fallback: poor SVD/PCA separation.",
            tissue_pixel_count=tissue_pixel_count,
            signal_p95=signal_p95,
            dynamic_range=dynamic_range,
        ).to_dict()

    if (
        singular_values.size < 2
        or not np.isfinite(singular_values[:2]).all()
        or float(singular_values[0]) <= 0.0
        or float(singular_values[1] / singular_values[0]) < _MIN_SINGULAR_RATIO
    ):
        return _default_fallback_estimate(
            warning="Macenko fallback: poor SVD/PCA separation.",
            tissue_pixel_count=tissue_pixel_count,
            signal_p95=signal_p95,
            dynamic_range=dynamic_range,
            singular_values=singular_values,
        ).to_dict()

    principal = vh[:2].T
    projected = centered @ principal
    angles = np.arctan2(projected[:, 1], projected[:, 0])
    min_angle = float(np.percentile(angles, 1))
    max_angle = float(np.percentile(angles, 99))

    first = _normalize_vector(principal @ np.array([math.cos(min_angle), math.sin(min_angle)], dtype=np.float64))
    second = _normalize_vector(principal @ np.array([math.cos(max_angle), math.sin(max_angle)], dtype=np.float64))

    if first is None or second is None:
        return _default_fallback_estimate(
            warning="Macenko fallback: poor SVD/PCA separation.",
            tissue_pixel_count=tissue_pixel_count,
            signal_p95=signal_p95,
            dynamic_range=dynamic_range,
            singular_values=singular_values,
        ).to_dict()

    stain_matrix = _order_hdab_vectors(np.vstack([first, second]))
    vector_angle_deg = _vector_angle_deg(stain_matrix[0], stain_matrix[1])
    if vector_angle_deg < _MIN_VECTOR_ANGLE_DEG:
        return _default_fallback_estimate(
            warning=f"Macenko fallback: stain vector angle < {_MIN_VECTOR_ANGLE_DEG:g} deg.",
            tissue_pixel_count=tissue_pixel_count,
            signal_p95=signal_p95,
            dynamic_range=dynamic_range,
            singular_values=singular_values,
            vector_angle_deg=vector_angle_deg,
        ).to_dict()

    estimate = StainEstimate(
        stain_matrix=stain_matrix,
        method="macenko",
        warnings=[],
        tissue_pixel_count=tissue_pixel_count,
        signal_p95=signal_p95,
        dynamic_range=dynamic_range,
        singular_values=[float(value) for value in singular_values[:3]],
        vector_angle_deg=vector_angle_deg,
    )
    return estimate.to_dict()


def deconvolve_h_dab(
    rgb: np.ndarray,
    stain_matrix: np.ndarray | dict[str, Any] | None = None,
    tissue_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    estimate_payload: dict[str, Any]
    if stain_matrix is None:
        estimate_payload = estimate_stain_vectors_macenko(rgb, tissue_mask=tissue_mask)
        matrix = np.asarray(estimate_payload["stain_matrix"], dtype=np.float64)
    elif isinstance(stain_matrix, dict):
        estimate_payload = dict(stain_matrix)
        matrix = np.asarray(estimate_payload["stain_matrix"], dtype=np.float64)
    else:
        matrix = _normalize_stain_matrix(stain_matrix)
        estimate_payload = {
            "stain_matrix": matrix.tolist(),
            "method": "provided_stain_matrix",
            "warnings": [],
            "tissue_pixel_count": int(np.prod(np.asarray(rgb).shape[:2])),
            "signal_p95": None,
            "dynamic_range": None,
            "singular_values": [],
            "vector_angle_deg": _vector_angle_deg(matrix[0], matrix[1]),
        }

    od = rgb_to_od(rgb)
    coefficients = od.reshape(-1, 3) @ np.linalg.pinv(matrix)
    channels = np.clip(coefficients.reshape((*od.shape[:2], 2)), 0.0, None)

    return {
        "hematoxylin_od": channels[..., 0],
        "dab_od": channels[..., 1],
        "stain_matrix": matrix,
        "method": str(estimate_payload.get("method", "provided_stain_matrix")),
        "warnings": list(estimate_payload.get("warnings", [])),
        "stain_separation": {
            "method": str(estimate_payload.get("method", "provided_stain_matrix")),
            "warnings": list(estimate_payload.get("warnings", [])),
            "tissue_pixel_count": int(estimate_payload.get("tissue_pixel_count", 0)),
            "signal_p95": estimate_payload.get("signal_p95"),
            "dynamic_range": estimate_payload.get("dynamic_range"),
            "singular_values": list(estimate_payload.get("singular_values", [])),
            "vector_angle_deg": estimate_payload.get("vector_angle_deg"),
        },
    }


def classify_dab_gmm(
    dab_od: np.ndarray,
    tissue_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    dab_array = _as_2d_array(dab_od, name="dab_od")
    selected_mask = np.isfinite(dab_array)
    if tissue_mask is not None:
        selected_mask &= _as_bool_mask(tissue_mask, name="tissue_mask", expected_shape=dab_array.shape)

    values = dab_array[selected_mask]
    if values.size == 0:
        return _classification_payload(
            percentages={"negative": 100.0, "weak": 0.0, "moderate": 0.0, "strong": 0.0},
            classification_method="fixed_bins_fallback",
            h_score_method="fixed_bins_fallback",
            warnings=["GMM fallback: no finite tissue pixels available for DAB classification."],
        )

    if values.size < _MIN_GMM_PIXELS:
        return _fixed_bin_classification(
            values,
            warning=f"GMM fallback: fewer than {_MIN_GMM_PIXELS} pixels available for stable clustering.",
        )

    try:
        mixture_module = importlib.import_module("sklearn.mixture")
        GaussianMixture = getattr(mixture_module, "GaussianMixture")
    except ModuleNotFoundError:
        return _fixed_bin_classification(
            values,
            warning="GMM fallback: scikit-learn is unavailable in this environment.",
        )

    bic_by_n: dict[int, float] = {}
    best_model = None
    best_n = None
    best_bic = None
    samples = values.reshape(-1, 1)

    for n_components in (2, 3, 4):
        if samples.shape[0] <= n_components:
            continue
        try:
            model = GaussianMixture(
                n_components=n_components,
                covariance_type="full",
                random_state=0,
                n_init=3,
                reg_covar=1e-6,
                max_iter=200,
            )
            model.fit(samples)
            bic = float(model.bic(samples))
        except Exception:
            continue
        bic_by_n[n_components] = bic
        if best_bic is None or bic < best_bic:
            best_bic = bic
            best_model = model
            best_n = n_components

    if best_model is None or best_n is None or best_bic is None:
        return _fixed_bin_classification(
            values,
            warning="GMM fallback: GaussianMixture fitting was unstable.",
        )

    labels = best_model.predict(samples)
    means = np.asarray(best_model.means_, dtype=np.float64).reshape(-1)
    component_order = np.argsort(means)
    ordered_weights = np.asarray(best_model.weights_, dtype=np.float64).reshape(-1)[component_order]
    covariances = np.asarray(best_model.covariances_, dtype=np.float64).reshape(best_n, -1)
    ordered_covariances = covariances[component_order]
    ordered_means = means[component_order]

    if np.any(~np.isfinite(ordered_covariances)) or np.any(ordered_covariances <= 0.0):
        return _fixed_bin_classification(
            values,
            warning="GMM fallback: component covariance was unstable.",
        )
    if np.any(ordered_weights < _MIN_GMM_COMPONENT_WEIGHT):
        return _fixed_bin_classification(
            values,
            warning="GMM fallback: component weight was too small for stable classification.",
        )
    if best_n > 1 and np.any(np.diff(ordered_means) < _MIN_GMM_MEAN_SEPARATION):
        return _fixed_bin_classification(
            values,
            warning="GMM fallback: component means were too close for stable classification.",
        )

    ordered_labels = _ordered_intensity_labels(best_n)
    counts = {"negative": 0, "positive": 0, "weak": 0, "moderate": 0, "strong": 0}
    for rank, component_index in enumerate(component_order):
        label_name = ordered_labels[rank]
        counts[label_name] += int(np.count_nonzero(labels == component_index))
    if best_n == 2:
        counts["weak"] = counts["positive"]

    total = float(samples.shape[0])
    percentages = {
        key: (100.0 * value / total) if total else 0.0
        for key, value in counts.items()
    }
    ordered_mapping = {str(rank): label for rank, label in enumerate(ordered_labels)}
    h_score_method = _BINARY_GMM_HSCORE_METHOD if best_n == 2 else "gmm_component_ranked_hscore"
    return _classification_payload(
        percentages=percentages,
        classification_method="gmm_bic",
        h_score_method=h_score_method,
        gmm_n_components=best_n,
        gmm_bic=best_bic,
        gmm_bic_candidates={str(key): float(value) for key, value in bic_by_n.items()},
        component_means=[float(value) for value in ordered_means],
        class_mapping=ordered_mapping,
        warnings=[],
    )


def _fixed_bin_classification(values: np.ndarray, warning: str) -> dict[str, Any]:
    finite_values = np.asarray(values, dtype=np.float64)
    # Default OD bins based on Rizzardi et al. (2012) and common QuPath /
    # IHC Profiler conventions. These are starting defaults, not biological
    # absolutes. Expert Mode allows user override.
    negative_count = int(np.count_nonzero(finite_values < _FIXED_BIN_WEAK_THRESHOLD))
    weak_count = int(
        np.count_nonzero(
            (finite_values >= _FIXED_BIN_WEAK_THRESHOLD) & (finite_values < _FIXED_BIN_MODERATE_THRESHOLD)
        )
    )
    moderate_count = int(
        np.count_nonzero(
            (finite_values >= _FIXED_BIN_MODERATE_THRESHOLD) & (finite_values < _FIXED_BIN_STRONG_THRESHOLD)
        )
    )
    strong_count = int(np.count_nonzero(finite_values >= _FIXED_BIN_STRONG_THRESHOLD))
    total = float(finite_values.size)
    percentages = {
        "negative": 100.0 * negative_count / total,
        "weak": 100.0 * weak_count / total,
        "moderate": 100.0 * moderate_count / total,
        "strong": 100.0 * strong_count / total,
    }
    return _classification_payload(
        percentages=percentages,
        classification_method="fixed_bins_fallback",
        h_score_method="fixed_bins_fallback",
        warnings=[warning],
    )


def _classification_payload(
    *,
    percentages: dict[str, float],
    classification_method: str,
    h_score_method: str,
    gmm_n_components: int | None = None,
    gmm_bic: float | None = None,
    gmm_bic_candidates: dict[str, float] | None = None,
    component_means: list[float] | None = None,
    class_mapping: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    negative = float(percentages.get("negative", 0.0))
    positive = percentages.get("positive")
    weak = float(percentages.get("weak", 0.0))
    moderate = float(percentages.get("moderate", 0.0))
    strong = float(percentages.get("strong", 0.0))
    positive = float(weak + moderate + strong if positive is None else positive)
    if h_score_method == "binary_positive_only":
        h_score = positive
    elif h_score_method == _BINARY_GMM_HSCORE_METHOD:
        h_score = positive
    else:
        h_score = weak + (2.0 * moderate) + (3.0 * strong)

    return {
        "classification_method": classification_method,
        "h_score_method": h_score_method,
        "gmm_n_components": gmm_n_components,
        "gmm_bic": None if gmm_bic is None else float(gmm_bic),
        "gmm_bic_candidates": dict(gmm_bic_candidates or {}),
        "component_means": list(component_means or []),
        "class_mapping": dict(class_mapping or {}),
        "negative_percent": negative,
        "weak_percent": weak,
        "moderate_percent": moderate,
        "strong_percent": strong,
        "positive_percent": positive,
        "h_score": float(h_score),
        "percentages": {
            "negative": negative,
            "weak": weak,
            "moderate": moderate,
            "strong": strong,
            "positive": positive,
        },
        "warnings": list(warnings or []),
    }


def _default_fallback_estimate(
    *,
    warning: str,
    tissue_pixel_count: int,
    signal_p95: float = 0.0,
    dynamic_range: float = 0.0,
    singular_values: np.ndarray | None = None,
    vector_angle_deg: float | None = None,
) -> StainEstimate:
    return StainEstimate(
        stain_matrix=DEFAULT_H_DAB_MATRIX.copy(),
        method="default_hdab_fallback",
        warnings=[warning],
        tissue_pixel_count=int(tissue_pixel_count),
        signal_p95=float(signal_p95),
        dynamic_range=float(dynamic_range),
        singular_values=[] if singular_values is None else [float(value) for value in singular_values[:3]],
        vector_angle_deg=vector_angle_deg,
    )


def _ordered_intensity_labels(n_components: int) -> list[str]:
    if n_components == 2:
        return ["negative", "positive"]
    if n_components == 3:
        return ["negative", "weak", "strong"]
    return ["negative", "weak", "moderate", "strong"]


def _order_hdab_vectors(stain_matrix: np.ndarray) -> np.ndarray:
    matrix = _normalize_stain_matrix(stain_matrix)
    # Hematoxylin is expected to be more blue-weighted than DAB.
    blue_weight = matrix[:, 2] - matrix[:, 0]
    order = np.argsort(blue_weight)[::-1]
    return matrix[order]


def _normalize_stain_matrix(stain_matrix: np.ndarray | list[list[float]]) -> np.ndarray:
    matrix = np.asarray(stain_matrix, dtype=np.float64)
    if matrix.shape != (2, 3):
        raise ValueError("stain_matrix must have shape (2, 3).")
    row_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(row_norms <= 0.0):
        raise ValueError("stain_matrix rows must be non-zero.")
    normalized = matrix / row_norms
    normalized[normalized.sum(axis=1) < 0.0] *= -1.0
    return normalized


def _normalize_vector(vector: np.ndarray) -> np.ndarray | None:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0.0:
        return None
    result = np.asarray(vector, dtype=np.float64) / norm
    if float(np.sum(result)) < 0.0:
        result *= -1.0
    return result


def _vector_angle_deg(first: np.ndarray, second: np.ndarray) -> float:
    cosine = float(np.clip(np.dot(first, second), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))


def _extract_hed_channel(rgb: np.ndarray, channel_index: int) -> np.ndarray:
    from skimage.color import rgb2hed
    hed = rgb2hed(_as_rgb_array(rgb, scale="unit"))
    stain_channel = np.clip(hed[..., channel_index], 0.0, None)
    channel_min = float(np.min(stain_channel))
    channel_max = float(np.max(stain_channel))
    if channel_max <= channel_min:
        return np.zeros(stain_channel.shape, dtype=np.float64)
    return (stain_channel - channel_min) / (channel_max - channel_min)


def _resolve_tissue_mask(
    od: np.ndarray,
    tissue_mask: np.ndarray | None = None,
) -> np.ndarray:
    signal = np.linalg.norm(od, axis=2)
    selected = np.isfinite(signal) & (signal > 0.15)
    if tissue_mask is not None:
        selected &= _as_bool_mask(tissue_mask, name="tissue_mask", expected_shape=od.shape[:2])
    return selected


def _as_rgb_array(rgb: np.ndarray, scale: str = "byte") -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if scale not in {"byte", "unit"}:
        raise ValueError(f"Unsupported RGB scale: {scale}")

    if np.max(array) <= 1.0:
        unit_array = np.clip(array, 0.0, 1.0)
    else:
        unit_array = np.clip(array, 0.0, 255.0) / 255.0

    if scale == "unit":
        return unit_array
    return unit_array * 255.0


def _as_2d_array(array: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(array, dtype=np.float64)
    if result.ndim != 2:
        raise ValueError(f"{name} must be a 2D array.")
    return result


def _as_bool_mask(
    mask: np.ndarray,
    name: str,
    expected_shape: tuple[int, int] | None = None,
) -> np.ndarray:
    bool_mask = np.asarray(mask, dtype=bool)
    if bool_mask.ndim != 2:
        raise ValueError(f"{name} must be a 2D mask.")
    if expected_shape is not None and bool_mask.shape != expected_shape:
        raise ValueError(f"{name} shape must be {expected_shape}, got {bool_mask.shape}.")
    return bool_mask
