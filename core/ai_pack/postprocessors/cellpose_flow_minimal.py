"""Minimal Cellpose flow-to-mask postprocess for ScieFig AI Pack v1.

Source anchors (Cellpose 3.1.1.3):
- cellpose/dynamics.py:572-602  follow_flows  (replaced with pure-numpy stepping loop)
- cellpose/dynamics.py:724-820  get_masks_torch  (replaced with numpy attractor density + skimage.label)
- cellpose/dynamics.py:874-941  compute_masks  (replaced orchestration; no torch, no numba, no fastremap)
- cellpose/utils.py:620-665      fill_holes_and_remove_small_masks  (replaced with scipy.ndimage.binary_fill_holes + np.unique renumbering, in cellpose_mask_cleanup.py)

Intentionally NOT ported in the v1 minimal slice:
- flow_error               — QC filtering deferred to cellpose_flow_v1_qc
- remove_bad_flow_masks    — QC filtering deferred to cellpose_flow_v1_qc
- tiling / stitching       — deferred to C-3.1; first slice runs single 256x256 inference only

Inputs:
  raw_outputs: list with at least one tensor of shape (1, 3, 256, 256) interpreted as
               (flow_y, flow_x, cellprob) along axis 1.
  entry:       ModelManifestEntry from core.ai_pack.manifest.

Output:
  PostprocessResult with label_mask shape (256, 256) int32 (0 = background, >=1 = instance id).
"""

from __future__ import annotations

import numpy as np
from skimage.measure import label as sk_label

from core.ai_pack.postprocess import PostprocessResult


CELLPROB_THRESHOLD = 0.0
NITER_DEFAULT = 200
MIN_SIZE_PX = 15
DIAM_MEAN_CYTO3 = 30.0


def postprocess_cellpose_flow_v1_minimal(raw_outputs, entry) -> PostprocessResult:
    metadata = {
        "model_id": entry.model_id,
        "model_version": entry.version,
        "postprocess_id": "cellpose_flow_v1_minimal",
        "qc_flow_filtering_enabled": False,
        "cellprob_threshold": CELLPROB_THRESHOLD,
        "niter": NITER_DEFAULT,
        "min_size_px": MIN_SIZE_PX,
    }
    primary = _select_primary_tensor(raw_outputs)
    if primary is None:
        return PostprocessResult(
            success=False,
            label_mask=None,
            object_count=0,
            reason="cellpose_flow_v1_minimal: no usable (1, 3, H, W) tensor in raw_outputs",
            metadata=metadata,
        )

    flow_yx = primary[0, 0:2].astype(np.float32)
    cellprob = primary[0, 2].astype(np.float32)
    height, width = cellprob.shape

    seed_mask = cellprob > CELLPROB_THRESHOLD
    if not seed_mask.any():
        return PostprocessResult(
            success=True,
            label_mask=np.zeros((height, width), dtype=np.int32),
            object_count=0,
            reason="",
            metadata={**metadata, "early_exit_empty_seeds": True},
        )

    flow_scale = 5.0 / DIAM_MEAN_CYTO3
    dy = flow_yx[0] * flow_scale
    dx = flow_yx[1] * flow_scale

    yy, xx = np.where(seed_mask)
    py = yy.astype(np.float32)
    px = xx.astype(np.float32)
    for _ in range(NITER_DEFAULT):
        iy = np.clip(np.round(py).astype(np.int32), 0, height - 1)
        ix = np.clip(np.round(px).astype(np.int32), 0, width - 1)
        py = py + dy[iy, ix]
        px = px + dx[iy, ix]
        np.clip(py, 0.0, float(height - 1), out=py)
        np.clip(px, 0.0, float(width - 1), out=px)

    attractor_iy = np.clip(np.round(py).astype(np.int32), 0, height - 1)
    attractor_ix = np.clip(np.round(px).astype(np.int32), 0, width - 1)
    attractor_density = np.zeros((height, width), dtype=np.int32)
    np.add.at(attractor_density, (attractor_iy, attractor_ix), 1)
    basin_mask = attractor_density >= 2
    basin_labels = sk_label(basin_mask, connectivity=2)
    if basin_labels.max() == 0:
        return PostprocessResult(
            success=True,
            label_mask=np.zeros((height, width), dtype=np.int32),
            object_count=0,
            reason="",
            metadata={**metadata, "early_exit_no_basins": True},
        )

    seed_labels = basin_labels[attractor_iy, attractor_ix]
    label_mask = np.zeros((height, width), dtype=np.int32)
    valid = seed_labels > 0
    label_mask[yy[valid], xx[valid]] = seed_labels[valid]

    from .cellpose_mask_cleanup import apply_minimal_cleanup

    label_mask, object_count, instance_areas_px = apply_minimal_cleanup(label_mask, min_size_px=MIN_SIZE_PX)
    metadata["instance_area_px_at_256"] = list(instance_areas_px)

    return PostprocessResult(
        success=True,
        label_mask=label_mask,
        object_count=int(object_count),
        reason="",
        metadata=metadata,
    )


def _select_primary_tensor(raw_outputs):
    candidates = raw_outputs if isinstance(raw_outputs, (list, tuple)) else [raw_outputs]
    for tensor in candidates:
        if hasattr(tensor, "shape") and len(getattr(tensor, "shape", ())) == 4:
            shape = tuple(int(x) for x in tensor.shape)
            if shape == (1, 3, 256, 256):
                return np.asarray(tensor)
    return None
