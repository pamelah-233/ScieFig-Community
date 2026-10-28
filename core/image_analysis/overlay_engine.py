from __future__ import annotations

from typing import Any

import numpy as np


OVERLAY_FAILURE_MESSAGE = "Overlay generation failed or has not been confirmed."


def validate_overlay_result(
    rgb: np.ndarray,
    mask: np.ndarray,
    overlay: np.ndarray | None = None,
    *,
    error: Exception | None = None,
) -> dict[str, Any]:
    messages: list[str] = []
    fail = False
    warn = False

    rgb_array = np.asarray(rgb)
    if rgb_array.ndim != 3 or rgb_array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")

    try:
        mask_array = _as_bool_mask(mask, expected_shape=rgb_array.shape[:2])
    except ValueError as exc:
        fail = True
        mask_array = np.zeros(rgb_array.shape[:2], dtype=bool)
        messages.append(str(exc))

    if error is not None:
        fail = True
        messages.append(OVERLAY_FAILURE_MESSAGE)
        messages.append(str(error))

    if not fail and overlay is None:
        fail = True
        messages.append(OVERLAY_FAILURE_MESSAGE)

    if overlay is not None:
        overlay_array = np.asarray(overlay)
        if overlay_array.shape != rgb_array.shape:
            fail = True
            messages.append("Overlay output shape does not match the source image.")
        elif overlay_array.ndim != 3 or overlay_array.shape[2] != 3:
            fail = True
            messages.append("Overlay output must be an RGB image.")

    if not fail and not np.any(mask_array):
        warn = True
        messages.append("Overlay mask is empty. Review the segmentation result.")

    status = "FAIL" if fail else ("WARN" if warn else "PASS")
    return {
        "status": status,
        "overlay_generated": not fail,
        "messages": _dedupe(messages),
    }


def _as_bool_mask(mask: np.ndarray, expected_shape: tuple[int, int]) -> np.ndarray:
    result = np.asarray(mask, dtype=bool)
    if result.ndim != 2 or result.shape != expected_shape:
        raise ValueError(f"mask shape must be {expected_shape}, got {result.shape}.")
    return result


def _dedupe(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for message in messages:
        text = str(message).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
