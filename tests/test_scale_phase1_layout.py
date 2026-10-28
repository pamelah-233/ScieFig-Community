from __future__ import annotations

from copy import deepcopy

import pytest

from core.layout_scale_bar import resolve_scale_bar_calibration, scale_bar_layout_object
from core.models import BrightfieldPanel, FluorescencePanel, IHCPanel
from core.models.layout_editor import LayoutObject
from ui.layout_adapters.brightfield_layout_adapter import (
    apply_brightfield_layout_objects,
    brightfield_layout_objects,
)
from ui.layout_adapters.fluorescence_layout_adapter import (
    apply_fluorescence_layout_objects,
    fluorescence_layout_objects,
)
from ui.layout_adapters.ihc_layout_adapter import apply_ihc_layout_objects, ihc_layout_objects


def _resolved(**overrides):
    payload = {
        "um_per_px": 0.5,
        "um_per_px_x": 0.5,
        "um_per_px_y": 0.5,
        "calibration_source": "embedded",
        "assumed": False,
        "calibrated": True,
    }
    payload.update(overrides)
    return payload


def _patch_resolve(monkeypatch, result=None, calls=None):
    calls = calls if calls is not None else []
    result = _resolved() if result is None else result

    def fake(metadata, saved_scale=None):
        calls.append({"metadata": metadata, "saved_scale": saved_scale})
        return dict(result)

    monkeypatch.setattr("core.measurement_scale.resolve_calibration", fake, raising=False)
    return calls


def test_resolve_scale_bar_calibration_delegates_saved_scale(monkeypatch) -> None:
    saved = {
        "calibration_source": "manual_line",
        "line_px": {"x1": 0.0, "y1": 10.0, "x2": 80.0, "y2": 10.0},
        "length_um": 40.0,
        "um_per_px": 0.5,
    }
    metadata = {"pixel_size_um": 0.25, "pixel_size_x_um": 0.4, "pixel_size_y_um": 0.8}
    calls = _patch_resolve(
        monkeypatch,
        _resolved(um_per_px=0.5, um_per_px_x=0.5, um_per_px_y=0.5, calibration_source="manual_line"),
    )

    calibration = resolve_scale_bar_calibration(metadata, saved_scale=saved)

    assert calibration.source == "manual_line"
    assert calibration.um_per_px == pytest.approx(0.5)
    assert calibration.calibrated is True
    assert calibration.assumed is False
    assert calls[0]["saved_scale"] is saved
    assert calls[0]["metadata"]["pixel_size_x_um"] == 0.4


def test_explicit_layout_manual_override_wins_over_saved_scale(monkeypatch) -> None:
    calls = []

    def fake(metadata, saved_scale=None):
        calls.append({"metadata": metadata, "saved_scale": dict(saved_scale or {})})
        return _resolved(um_per_px=2.0, um_per_px_x=2.0, um_per_px_y=2.0, calibration_source="manual")

    monkeypatch.setattr("core.measurement_scale.resolve_calibration", fake, raising=False)
    saved = {"um_per_px": 9.0, "calibration_source": "manual_line"}
    calibration = resolve_scale_bar_calibration(
        {"pixel_size_um": 0.5},
        manual_um_per_px=2.0,
        saved_scale=saved,
    )

    assert calibration.source == "manual"
    assert calibration.um_per_px == pytest.approx(2.0)
    assert calibration.calibrated is True
    assert calibration.assumed is False
    assert calls[0]["saved_scale"]["calibration_source"] == "manual"
    assert calls[0]["saved_scale"]["um_per_px"] == 2.0
    assert calls[0]["saved_scale"].get("calibration_source") != saved["calibration_source"]


def test_non_finite_manual_um_per_px_rejected_through_shared_resolver() -> None:
    with pytest.raises(ValueError, match="manual_um_per_px"):
        resolve_scale_bar_calibration({}, manual_um_per_px=float("inf"))
    with pytest.raises(ValueError, match="manual_um_per_px"):
        resolve_scale_bar_calibration({}, manual_um_per_px=float("nan"))


def test_assumed_display_uses_px_label_and_does_not_use_cached_um(monkeypatch) -> None:
    _patch_resolve(
        monkeypatch,
        _resolved(
            um_per_px=0.37,
            um_per_px_x=0.37,
            um_per_px_y=0.37,
            calibration_source="assumed",
            assumed=True,
            calibrated=False,
        ),
    )
    obj = scale_bar_layout_object(
        panel_uuid="panel-a",
        module_kind="fluorescence",
        target_id="merge",
        target_rect={"x": 0, "y": 0, "width": 400, "height": 300},
        source_metadata={"width_px": 800, "height_px": 600},
        template={"label_length_um": 50.0, "fallback_bar_fraction": 0.25},
        measurement_scale={"um_per_px": 0.37, "calibration_source": "assumed", "assumed": True},
    )

    assert obj.width == pytest.approx(100.0)
    assert obj.metadata["calibration_source"] == "assumed"
    assert obj.metadata["assumed"] is True
    assert obj.metadata["um_per_px"] is None
    assert obj.metadata["label_text"] == "200 px"
    assert obj.text == "200 px"
    assert "um" not in obj.metadata["label_text"]


def test_uncalibrated_label_uses_source_pixels_not_scene_pixels(monkeypatch) -> None:
    _patch_resolve(
        monkeypatch,
        _resolved(um_per_px=0, um_per_px_x=0, um_per_px_y=0, calibration_source="assumed", assumed=True, calibrated=False),
    )
    obj = scale_bar_layout_object(
        panel_uuid="panel-a",
        module_kind="ihc",
        target_id="image-a",
        target_rect={"x": 12.0, "y": 8.0, "width": 500.0, "height": 250.0},
        source_metadata={"width_px": 1000, "height_px": 500},
        template={
            "label_length_um": 50.0,
            "fallback_bar_fraction": 0.18,
            "anchor": "bottom_right",
            "offset_x_fraction": 0.1,
            "offset_y_fraction": 0.2,
            "line_color": "#ABCDEF",
            "font_size": 11.0,
        },
    )

    assert obj.object_id == "panel-a:ihc_scale_bar:image-a"
    assert obj.width == pytest.approx(90.0)
    assert obj.x == pytest.approx(372.0)
    assert obj.style.stroke_color == "#ABCDEF"
    assert obj.style.font_size_pt == pytest.approx(11.0)
    assert obj.metadata["label_text"] == "180 px"
    assert obj.text == "180 px"
    assert "um" not in obj.metadata["label_text"]
    assert obj.metadata["assumed"] is True
    assert obj.metadata["state"] == {}


def test_layout_does_not_invent_y_from_x_only_resolved_axes(monkeypatch) -> None:
    _patch_resolve(
        monkeypatch,
        _resolved(um_per_px=0.4, um_per_px_x=0.4, um_per_px_y=0, calibration_source="embedded"),
    )
    calibration = resolve_scale_bar_calibration({"pixel_size_x_um": 0.4})

    assert calibration.source == "embedded"
    assert calibration.um_per_px == pytest.approx(0.4)
    assert calibration.um_per_px_x == pytest.approx(0.4)
    assert calibration.um_per_px_y == 0
    assert calibration.calibrated is True


def test_horizontal_bar_uses_x_axis_and_ignores_display_line_geometry(monkeypatch) -> None:
    saved = {
        "calibration_source": "embedded",
        "line_px": {"x1": 1.0, "y1": 2.0, "x2": 999.0, "y2": 2.0},
        "length_um": 50.0,
    }
    _patch_resolve(
        monkeypatch,
        _resolved(um_per_px=0.4, um_per_px_x=0.4, um_per_px_y=0.8, calibration_source="embedded"),
    )
    obj = scale_bar_layout_object(
        panel_uuid="panel-a",
        module_kind="ihc",
        target_id="image-a",
        target_rect={"x": 10.0, "y": 20.0, "width": 500.0, "height": 250.0},
        source_metadata={"width_px": 1000, "height_px": 500, "pixel_size_x_um": 0.4, "pixel_size_y_um": 0.8},
        template={"label_length_um": 40.0, "anchor": "bottom_right", "offset_x_fraction": 0.1, "offset_y_fraction": 0.2},
        measurement_scale=saved,
    )

    assert obj.object_id == "panel-a:ihc_scale_bar:image-a"
    assert obj.width == pytest.approx(50.0)
    assert obj.x == pytest.approx(410.0)
    assert obj.metadata["calibration_source"] == "embedded"
    assert obj.metadata["label_text"] == "40 um"
    assert obj.x != pytest.approx(1.0)
    assert obj.y != pytest.approx(2.0)


def test_missing_x_axis_does_not_fabricate_um_label(monkeypatch) -> None:
    _patch_resolve(
        monkeypatch,
        _resolved(um_per_px=0, um_per_px_x=0, um_per_px_y=0.5, calibration_source="embedded", assumed=False, calibrated=True),
    )
    obj = scale_bar_layout_object(
        panel_uuid="panel-a",
        module_kind="brightfield",
        target_id="image-a",
        target_rect={"x": 0, "y": 0, "width": 200, "height": 100},
        source_metadata={"width_px": 200, "pixel_size_y_um": 0.5},
        template={"label_length_um": 50.0, "fallback_bar_fraction": 0.2},
        measurement_scale={"um_per_px_y": 0.5, "calibration_source": "embedded"},
    )

    assert obj.width == pytest.approx(40.0)
    assert obj.metadata["assumed"] is True
    assert "um" not in obj.metadata["label_text"]
    assert obj.metadata["label_text"].endswith(" px")


def _ihc_panel() -> IHCPanel:
    panel = IHCPanel(panel_id="Figure1I", panel_uuid="panel-ihc-1")
    panel.data = {
        "active_image_uid": "image-a",
        "ihc_images": [
            {
                "uid": "image-a",
                "source_name": "ihc.png",
                "metadata": {"width_px": 640, "height_px": 480, "pixel_size_um": 0.5},
                "measurement_scale_bar": {
                    "enabled": True,
                    "length_um": 50.0,
                    "um_per_px": 0.5,
                    "calibration_source": "embedded",
                    "assumed": False,
                    "line_px": {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 20.0},
                },
                "measurements": [{"measurement_uuid": "ihc-measurement-a", "results": {"positive_area_pct": 25.0}}],
            }
        ],
        "ihc_layout": {
            "scale_bar_template": {"label_length_um": 50.0, "anchor": "bottom_right", "offset_x_fraction": 0.05, "offset_y_fraction": 0.1},
            "scale_bars": [{"target_id": "image-a", "enabled": True}],
        },
    }
    panel.analysis = {"ihc_measurements": [{"measurement_uuid": "ihc-measurement-a"}]}
    return panel


def test_ihc_adapter_passes_measurement_scale_and_writeback_does_not_mutate_history(monkeypatch) -> None:
    calls = _patch_resolve(monkeypatch)
    panel = _ihc_panel()
    image = panel.active_image()
    saved = deepcopy(image["measurement_scale_bar"])
    measurements = deepcopy(image["measurements"])
    analysis = deepcopy(panel.analysis)
    measurement = {"measurement_uuid": "ihc-measurement-a"}

    objects = ihc_layout_objects(panel, measurement=measurement)
    scale_bar = next(obj for obj in objects if obj.metadata.get("ihc_kind") == "layout_scale_bar")

    assert calls[0]["saved_scale"] == saved
    assert scale_bar.object_id == "panel-ihc-1:ihc_scale_bar:image-a"
    assert scale_bar.metadata["target_id"] == "image-a"

    edited = []
    for obj in objects:
        payload = obj.to_dict()
        if obj.metadata.get("ihc_kind") == "layout_scale_bar":
            payload.update({"x": 50.0, "y": 60.0})
            payload["style"]["stroke_color"] = "#111111"
        edited.append(LayoutObject.from_dict(payload))
    apply_ihc_layout_objects(panel, edited)

    assert panel.active_image()["measurement_scale_bar"] == saved
    assert panel.active_image()["measurements"] == measurements
    assert panel.analysis == analysis
    assert "um_per_px" not in panel.data["ihc_layout"]["scale_bars"][0]
    assert panel.data["ihc_layout"]["scale_bars"][0]["template"]["line_color"] == "#111111"


def _bf_panel() -> BrightfieldPanel:
    panel = BrightfieldPanel(panel_id="Figure1B", panel_uuid="panel-brightfield-1")
    panel.data = {
        "active_image_uid": "image-a",
        "brightfield_images": [
            {
                "uid": "image-a",
                "source_name": "brightfield.png",
                "metadata": {"width_px": 640, "height_px": 480},
                "measurement_scale_bar": {
                    "enabled": True,
                    "length_um": 50.0,
                    "um_per_px": 0.22,
                    "calibration_source": "assumed",
                    "assumed": True,
                },
                "measurements": [{"measurement_uuid": "bf-a", "results": {"positive_area_pct": 18.0}}],
            }
        ],
        "brightfield_layout": {
            "scale_bar_template": {"label_length_um": 50.0, "fallback_bar_fraction": 0.25},
            "scale_bars": [{"target_id": "image-a", "enabled": True, "manual_um_per_px": 2.0}],
        },
    }
    panel.analysis = {"brightfield_measurements": [{"measurement_uuid": "bf-a"}]}
    return panel


def test_brightfield_manual_override_wins_and_assumed_history_is_untouched() -> None:
    panel = _bf_panel()
    saved = deepcopy(panel.active_image()["measurement_scale_bar"])
    measurements = deepcopy(panel.active_image()["measurements"])

    objects = brightfield_layout_objects(panel, measurement={"measurement_uuid": "bf-a"})
    scale_bar = next(obj for obj in objects if obj.metadata.get("brightfield_kind") == "layout_scale_bar")

    assert scale_bar.metadata["calibration_source"] == "manual"
    assert scale_bar.metadata["um_per_px"] == pytest.approx(2.0)
    assert "um" in scale_bar.metadata["label_text"]

    apply_brightfield_layout_objects(panel, objects)

    assert panel.active_image()["measurement_scale_bar"] == saved
    assert panel.active_image()["measurements"] == measurements
    assert panel.data["brightfield_layout"]["scale_bars"][0]["manual_um_per_px"] == 2.0


def test_fluorescence_adapter_passes_measurement_scale_keeps_unprefixed_target_id(monkeypatch) -> None:
    saved = {
        "enabled": True,
        "length_um": 50.0,
        "um_per_px": 1.0,
        "um_per_px_x": 1.0,
        "um_per_px_y": 1.0,
        "calibration_source": "embedded",
        "assumed": False,
        "line_px": {"x1": 0.0, "y1": 8.0, "x2": 50.0, "y2": 8.0},
    }
    calls = _patch_resolve(monkeypatch, _resolved(um_per_px=1.0, um_per_px_x=1.0, um_per_px_y=1.0))
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-1")
    panel.data = {
        "active_image_uid": "image-a",
        "fluorescence_images": [
            {
                "uid": "image-a",
                "metadata": {"width_px": 640, "height_px": 480, "pixel_size_um": 1.0},
                "measurement_scale_bar": deepcopy(saved),
                "layout_item_geometries": {
                    "merge_split:merge": {"x": 310.0, "y": 22.0, "width": 260.0, "height": 190.0},
                },
                "layout_scale_bar_template": {"label_length_um": 50.0, "anchor": "bottom_right", "offset_x_fraction": 0.1, "offset_y_fraction": 0.2},
                "layout_scale_bars": [{"target_id": "merge_split:merge", "enabled": True}],
            }
        ],
    }
    panel.analysis = {}
    image = panel.active_image()
    history = deepcopy(image["measurement_scale_bar"])

    objects = fluorescence_layout_objects(panel)
    scale_bar = next(obj for obj in objects if obj.metadata.get("fluoro_kind") == "layout_scale_bar")

    assert calls[0]["saved_scale"] == saved
    assert scale_bar.metadata["target_id"] == "merge_split:merge"
    assert scale_bar.object_id == "panel-fluoro-1:fluorescence_scale_bar:image-a:merge_split:merge"
    assert scale_bar.x != pytest.approx(0.0)

    edited = []
    for obj in objects:
        payload = obj.to_dict()
        if obj.metadata.get("fluoro_kind") == "layout_scale_bar":
            payload["style"]["metadata"]["text_position"] = "below"
        edited.append(LayoutObject.from_dict(payload))
    apply_fluorescence_layout_objects(panel, edited)

    assert panel.active_image()["measurement_scale_bar"] == history
    assert "measurement_scale_bar" not in panel.active_image().get("layout_scale_bars", [{}])[0]
    assert panel.active_image()["layout_scale_bars"][0]["template"]["text_position"] == "below"
    assert panel.active_image()["layout_scale_bars"][0]["target_id"] == "merge_split:merge"
