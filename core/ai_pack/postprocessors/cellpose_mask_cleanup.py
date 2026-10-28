"""Minimal cleanup: drop small instances, fill holes, renumber 1..N."""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


def apply_minimal_cleanup(label_mask: np.ndarray, *, min_size_px: int = 15) -> tuple[np.ndarray, int, list[int]]:
    """Drop small instances, fill holes, contiguously renumber 1..N."""
    label_mask = np.asarray(label_mask, dtype=np.int32)
    if label_mask.max() == 0:
        return label_mask, 0, []

    out = np.zeros_like(label_mask, dtype=np.int32)
    new_id = 0
    instance_areas: list[int] = []
    for old_id in range(1, int(label_mask.max()) + 1):
        instance_bool = label_mask == old_id
        if int(instance_bool.sum()) < int(min_size_px):
            continue
        filled = ndi.binary_fill_holes(instance_bool)
        area_px = int(filled.sum())
        if area_px < int(min_size_px):
            continue
        new_id += 1
        out[filled] = new_id
        instance_areas.append(area_px)
    return out, new_id, instance_areas
