from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class ExampleGuidedResult:
    threshold: float
    positive_sample_count: int
    negative_sample_count: int
    examples: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    threshold_shift_fraction: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": float(self.threshold),
            "positive_sample_count": int(self.positive_sample_count),
            "negative_sample_count": int(self.negative_sample_count),
            "examples": [dict(item) for item in self.examples],
            "warnings": list(self.warnings),
            "threshold_shift_fraction": self.threshold_shift_fraction,
        }


def recalibrate_dab_threshold_from_examples(
    dab_channel: np.ndarray,
    examples: list[dict[str, Any]],
    *,
    current_threshold: float,
    tissue_mask: np.ndarray | None = None,
    default_patch_radius: int = 6,
) -> ExampleGuidedResult:
    dab = np.asarray(dab_channel, dtype=np.float64)
    tissue = np.asarray(tissue_mask, dtype=bool) if tissue_mask is not None else np.ones(dab.shape, dtype=bool)
    positive_values: list[np.ndarray] = []
    negative_values: list[np.ndarray] = []
    normalized_examples: list[dict[str, Any]] = []
    warnings: list[str] = []

    for raw in examples:
        kind = str(raw.get("kind", "")).strip().lower()
        if kind not in {"positive", "negative"}:
            continue
        x = int(round(float(raw.get("x", 0))))
        y = int(round(float(raw.get("y", 0))))
        radius = max(1, int(round(float(raw.get("patch_radius", default_patch_radius)))))
        patch = _sample_patch(dab, tissue, x=x, y=y, radius=radius)
        if patch.size == 0:
            warnings.append(f"{kind} example at x={x}, y={y} did not contain usable tissue pixels.")
            continue
        normalized_examples.append({"kind": kind, "x": x, "y": y, "patch_radius": radius, "n": int(patch.size)})
        if kind == "positive":
            positive_values.append(patch)
        else:
            negative_values.append(patch)

    pos = np.concatenate(positive_values) if positive_values else np.asarray([], dtype=np.float64)
    neg = np.concatenate(negative_values) if negative_values else np.asarray([], dtype=np.float64)
    current = float(current_threshold)

    if pos.size and neg.size:
        pos_low = float(np.quantile(pos, 0.25))
        neg_high = float(np.quantile(neg, 0.75))
        pos_median = float(np.median(pos))
        neg_median = float(np.median(neg))
        if pos_median <= neg_median:
            warnings.append("Positive and negative examples conflict; review selected examples before using this result.")
        threshold = (pos_low + neg_high) / 2.0
    elif pos.size:
        threshold = float(np.quantile(pos, 0.20))
    elif neg.size:
        threshold = float(np.quantile(neg, 0.95) + 0.03)
    else:
        raise ValueError("Example-guided recalibration needs at least one usable positive or negative example.")

    threshold = float(min(max(threshold, 0.02), 3.0))
    shift = abs(threshold - current) / max(abs(current), 0.02)
    if shift > 0.40:
        warnings.append("Example-guided recalibration shifted the threshold substantially; confirm the overlay.")

    return ExampleGuidedResult(
        threshold=threshold,
        positive_sample_count=len(positive_values),
        negative_sample_count=len(negative_values),
        examples=normalized_examples,
        warnings=warnings,
        threshold_shift_fraction=float(shift),
    )


def _sample_patch(dab: np.ndarray, tissue: np.ndarray, *, x: int, y: int, radius: int) -> np.ndarray:
    height, width = dab.shape
    x0 = max(0, x - radius)
    x1 = min(width, x + radius + 1)
    y0 = max(0, y - radius)
    y1 = min(height, y + radius + 1)
    if x1 <= x0 or y1 <= y0:
        return np.asarray([], dtype=np.float64)
    patch = dab[y0:y1, x0:x1]
    patch_mask = tissue[y0:y1, x0:x1]
    return patch[patch_mask]
