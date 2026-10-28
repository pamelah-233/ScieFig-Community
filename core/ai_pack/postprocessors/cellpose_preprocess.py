"""Cellpose preprocess: percentile normalize a single 2-channel input.

Source anchors (Cellpose 3.1.1.3):
- cellpose/transforms.py:normalize99
- cellpose/transforms.py:normalize_img
"""

from __future__ import annotations

import numpy as np


def normalize_channels(image_256: np.ndarray) -> np.ndarray:
    """Normalize a (2, 256, 256) float32 array per-channel to [0, 1]."""
    if image_256.shape != (2, 256, 256):
        raise ValueError(f"normalize_channels expects (2, 256, 256), got {image_256.shape}")
    out = np.zeros_like(image_256, dtype=np.float32)
    for channel_index in range(2):
        channel = image_256[channel_index]
        p1, p99 = np.percentile(channel, [1.0, 99.0])
        if p99 - p1 > 1e-6:
            normalized = (channel - p1) / (p99 - p1)
        else:
            normalized = np.zeros_like(channel, dtype=np.float32)
        out[channel_index] = np.clip(normalized, 0.0, 1.0).astype(np.float32)
    return out
