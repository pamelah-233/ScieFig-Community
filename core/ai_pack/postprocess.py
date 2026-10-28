from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PostprocessResult:
    success: bool
    label_mask: Any | None
    object_count: int
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _raw_output_shapes(raw_outputs) -> Any:
    if isinstance(raw_outputs, dict):
        return {key: _shape_of(value) for key, value in raw_outputs.items()}
    if isinstance(raw_outputs, (list, tuple)):
        return [_shape_of(value) for value in raw_outputs]
    return [_shape_of(raw_outputs)]


def _shape_of(value) -> Any:
    shape = getattr(value, "shape", None)
    if shape is None:
        return None
    try:
        return tuple(int(part) for part in shape)
    except TypeError:
        return None


def postprocess(raw_outputs, entry) -> PostprocessResult:
    shapes = _raw_output_shapes(raw_outputs)
    metadata = {
        "model_id": entry.model_id,
        "model_version": entry.version,
        "raw_output_shapes": shapes,
    }

    if entry.postprocess == "noop":
        return PostprocessResult(
            success=False,
            label_mask=None,
            object_count=0,
            reason="noop postprocess is a stub in Phase C-1",
            metadata=metadata,
        )

    if entry.postprocess == "cellpose_flow_v1_minimal":
        from .postprocessors.cellpose_flow_minimal import postprocess_cellpose_flow_v1_minimal

        result = postprocess_cellpose_flow_v1_minimal(raw_outputs, entry)
        merged_metadata = {**metadata, **result.metadata}
        return PostprocessResult(
            success=result.success,
            label_mask=result.label_mask,
            object_count=result.object_count,
            reason=result.reason,
            metadata=merged_metadata,
        )

    return PostprocessResult(
        success=False,
        label_mask=None,
        object_count=0,
        reason=f"Unknown postprocess pipeline: {entry.postprocess}",
        metadata=metadata,
    )
