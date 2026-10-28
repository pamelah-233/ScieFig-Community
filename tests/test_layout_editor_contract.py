from __future__ import annotations

import dataclasses

import pytest

from core.models.layout_editor import (
    LayoutCanvasSpec,
    LayoutObject,
    LayoutObjectStyle,
    LayoutSourceIdentity,
    layout_object_hash,
)


def test_layout_object_round_trips_through_dict_and_retains_stable_id() -> None:
    source = LayoutSourceIdentity(
        source_module="fluorescence",
        source_panel_uuid="panel-123",
        source_record_id="plot-7",
        source_record_namespace="visual_layout_asset",
        source_context_hash="sha256:" + "a" * 64,
    )
    layout_object = LayoutObject(
        object_id="layout-object-1",
        object_type="plot",
        x=12.5,
        y=30.0,
        width=420.0,
        height=260.0,
        rotation_deg=3.5,
        z_order=4,
        locked=True,
        hidden=False,
        source=source,
        style=LayoutObjectStyle(stroke_color="#C0392B", font_size_pt=12.0, bold=True),
        text="",
        asset_key="merge_split_plot",
        metadata={"layout_mode": "merge_split"},
    )

    payload = layout_object.to_dict()
    restored = LayoutObject.from_dict(payload)

    assert restored == layout_object
    assert restored.object_id == "layout-object-1"
    assert restored.to_dict() == payload
    assert restored.stable_hash() == layout_object_hash(layout_object)


def test_layout_hash_is_stable_across_metadata_key_order() -> None:
    first = LayoutObject(
        object_id="text-1",
        object_type="text",
        x=1.0,
        y=2.0,
        width=100.0,
        height=20.0,
        metadata={"b": 2, "a": 1},
    )
    second = LayoutObject.from_dict(
        {
            "object_id": "text-1",
            "object_type": "text",
            "x": 1.0,
            "y": 2.0,
            "width": 100.0,
            "height": 20.0,
            "metadata": {"a": 1, "b": 2},
        }
    )

    assert first.stable_hash() == second.stable_hash()


def test_layout_object_contract_is_frozen_and_validates_geometry() -> None:
    layout_object = LayoutObject(object_id="shape-1", object_type="shape", x=0, y=0, width=10, height=10)

    assert dataclasses.is_dataclass(layout_object)
    with pytest.raises(dataclasses.FrozenInstanceError):
        layout_object.x = 5  # type: ignore[misc]
    with pytest.raises(ValueError, match="width must be non-negative"):
        LayoutObject(object_id="bad", object_type="shape", x=0, y=0, width=-1, height=10)


def test_layout_metadata_is_immutable_and_hash_stable() -> None:
    layout_object = LayoutObject(
        object_id="text-immutable",
        object_type="text",
        x=0,
        y=0,
        width=10,
        height=10,
        style=LayoutObjectStyle(metadata={"nested": {"a": 1}}),
        metadata={"a": 1, "items": [{"b": 2}]},
    )
    old_hash = layout_object.stable_hash()

    with pytest.raises(TypeError):
        layout_object.metadata["a"] = 2  # type: ignore[index]
    with pytest.raises(TypeError):
        layout_object.metadata["items"][0]["b"] = 3  # type: ignore[index]
    with pytest.raises(TypeError):
        layout_object.style.metadata["nested"]["a"] = 2  # type: ignore[index]

    assert layout_object.stable_hash() == old_hash
    assert layout_object.to_dict()["metadata"] == {"a": 1, "items": [{"b": 2}]}


def test_layout_canvas_spec_round_trips_and_hashes() -> None:
    spec = LayoutCanvasSpec(
        canvas_id="wb-layout",
        width_px=3600,
        height_px=1200,
        dpi=1200,
        background_color="#FFFFFF",
        metadata={"module": "wb"},
    )

    restored = LayoutCanvasSpec.from_dict(spec.to_dict())

    assert restored == spec
    assert restored.stable_hash().startswith("sha256:")


def test_layout_canvas_metadata_is_immutable() -> None:
    spec = LayoutCanvasSpec(canvas_id="canvas", width_px=10, height_px=10, metadata={"a": 1})

    with pytest.raises(TypeError):
        spec.metadata["a"] = 2  # type: ignore[index]


def test_layout_source_identity_requires_explicit_namespace() -> None:
    with pytest.raises(ValueError, match="source_record_namespace is required"):
        LayoutSourceIdentity(source_module="fluorescence", source_panel_uuid="panel-123", source_record_namespace="")
