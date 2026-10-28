from __future__ import annotations

import numpy as np


def assess_white_balance_color_cast(rgb: np.ndarray) -> dict[str, object]:
    array = _as_rgb(rgb)
    bright_mask = np.mean(array, axis=2) >= np.quantile(np.mean(array, axis=2), 0.75)
    sample = array[bright_mask] if np.any(bright_mask) else array.reshape(-1, 3)
    channel_means = np.mean(sample, axis=0)
    mean_level = float(np.mean(channel_means))
    if mean_level <= 0:
        cast_score = 0.0
    else:
        cast_score = float((np.max(channel_means) - np.min(channel_means)) / mean_level)
    warning = ""
    if cast_score > 0.18:
        warning = "White balance or color cast may be shifted. Review the overlay before using this result."
    return {
        "cast_score": cast_score,
        "channel_means": [float(value) for value in channel_means],
        "warning": warning,
    }


def assess_dab_stain_compatibility(dab_channel: np.ndarray, tissue_mask: np.ndarray | None = None) -> dict[str, object]:
    dab = np.asarray(dab_channel, dtype=np.float64)
    selected = np.asarray(tissue_mask, dtype=bool) if tissue_mask is not None else np.ones(dab.shape, dtype=bool)
    values = dab[selected] if np.any(selected) else dab.reshape(-1)
    if values.size == 0:
        return {"spread": 0.0, "warning": "DAB stain compatibility could not be assessed because no tissue pixels were available."}
    spread = float(np.quantile(values, 0.95) - np.quantile(values, 0.05))
    warning = ""
    if spread < 0.04:
        warning = "DAB stain compatibility may be weak because the signal distribution is very flat. Stain separation or thresholding may be unstable."
    return {"spread": spread, "warning": warning}


def _as_rgb(rgb: np.ndarray) -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float64)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if np.max(array) <= 1.0:
        return np.clip(array * 255.0, 0.0, 255.0)
    return np.clip(array, 0.0, 255.0)
