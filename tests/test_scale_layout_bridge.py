from __future__ import annotations

from copy import deepcopy
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.layout_scale_bar import (
    apply_native_image_scale_overlay,
    ensure_layout_scale_bar_states,
    measurement_scale_enters_layout,
    native_layout_scale_action,
    paint_layout_scale_bar_on_native_image,
    seed_layout_scale_presentation,
)
from core.models import BrightfieldPanel, FluorescencePanel, IHCPanel
from core.models.layout_editor import LayoutObject
from ui.layout_adapters.brightfield_layout_adapter import apply_brightfield_layout_objects, brightfield_layout_objects
from ui.layout_adapters.fluorescence_layout_adapter import apply_fluorescence_layout_objects, fluorescence_layout_objects
from ui.layout_adapters.ihc_layout_adapter import apply_ihc_layout_objects, ihc_layout_objects
from ui.widgets.calibration_controls import sync_scale_appearance_controls


DRAWN_SCALE = {
    "enabled": True,
    "length_um": 40.0,
    "um_per_px": 0.5,
    "um_per_px_x": 0.5,
    "um_per_px_y": 0.5,
    "calibration_source": "manual_line",
    "assumed": False,
    "calibrated": True,
    "line_px": {"x1": 10.0, "y1": 80.0, "x2": 90.0, "y2": 80.0},
}


def _drawn_scale(**overrides):
    payload = deepcopy(DRAWN_SCALE)
    payload.update(overrides)
    return payload


def test_drawn_calibration_enters_layout_without_copying_um_px() -> None:
    states = ensure_layout_scale_bar_states(
        measurement_scale=_drawn_scale(),
        existing_states=[],
        target_ids=["merge_split:merge"],
    )

    assert measurement_scale_enters_layout(_drawn_scale()) is True
    assert states == [{"target_id": "merge_split:merge", "enabled": True}]
    assert "um_per_px" not in states[0]
    assert "line_px" not in states[0]
    assert "length_um" not in states[0]


def test_assumed_or_disabled_measurement_does_not_seed_layout() -> None:
    assumed = _drawn_scale(calibration_source="assumed", assumed=True, calibrated=False)
    disabled = _drawn_scale(enabled=False)
    assert measurement_scale_enters_layout(assumed) is False
    assert measurement_scale_enters_layout(disabled) is False
    assert ensure_layout_scale_bar_states(measurement_scale=assumed, existing_states=[], target_ids=["image-a"]) == []


def test_existing_disabled_layout_bar_is_not_reenabled() -> None:
    states = ensure_layout_scale_bar_states(
        measurement_scale=_drawn_scale(),
        existing_states=[{"target_id": "image-a", "enabled": False, "um_per_px": 9.0}],
        target_ids=["image-a"],
    )

    assert len(states) == 1
    assert states[0]["enabled"] is False
    assert "um_per_px" not in states[0]


def test_fluorescence_drawn_scale_seeds_layout_editable_state() -> None:
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-bridge-fluoro")
    panel.data = {
        "active_image_uid": "image-a",
        "fluorescence_images": [
            {
                "uid": "image-a",
                "metadata": {"width_px": 640, "height_px": 480, "pixel_size_um": 0.25},
                "layout_mode": "merge_split",
                "layout_item_geometries": {
                    "merge_split:merge": {"x": 10.0, "y": 8.0, "width": 260.0, "height": 190.0},
                },
            }
        ],
    }
    panel.analysis = {}

    resolved = panel.update_measurement_scale_bar(
        "image-a",
        length_um=40.0,
        line_px={"x1": 10.0, "y1": 80.0, "x2": 90.0, "y2": 80.0},
        calibration_source="manual_line",
    )
    image = panel.active_image()
    calibration = deepcopy(image["measurement_scale_bar"])

    assert resolved["um_per_px"] == pytest.approx(0.5)
    assert image["layout_scale_bars"] == [{"target_id": "merge_split:merge", "enabled": True}]
    assert image["layout_scale_bar_template"]["label_length_um"] == pytest.approx(40.0)
    assert "um_per_px" not in image["layout_scale_bars"][0]
    assert "line_px" not in image["layout_scale_bars"][0]
    assert native_layout_scale_action(image["layout_scale_bars"], "merge_split:merge") == "paint"

    objects = fluorescence_layout_objects(panel)
    scale_bars = [obj for obj in objects if obj.metadata.get("fluoro_kind") == "layout_scale_bar"]
    assert len(scale_bars) == 1
    assert scale_bars[0].object_type == "scale_bar"

    edited = []
    for obj in objects:
        payload = obj.to_dict()
        if obj.metadata.get("fluoro_kind") == "layout_scale_bar":
            payload["style"]["stroke_color"] = "#111111"
            payload["style"]["text_color"] = "#222222"
            payload["style"]["stroke_width"] = 6.0
            payload["style"]["font_size_pt"] = 14.0
            payload["style"]["font_family"] = "Times New Roman"
            payload["style"]["metadata"]["text_position"] = "below"
        edited.append(LayoutObject.from_dict(payload))
    apply_fluorescence_layout_objects(panel, edited)

    image = panel.active_image()
    assert image["measurement_scale_bar"] == calibration
    assert image["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
    assert image["measurement_scale_bar"]["line_px"]["x2"] == pytest.approx(90.0)
    template = image["layout_scale_bars"][0]["template"]
    assert image["layout_scale_bar_template"]["line_color"] == "#FFFFFF"
    assert template["line_color"] == "#111111"
    assert template["text_color"] == "#222222"
    assert template["line_thickness"] == pytest.approx(6.0)
    assert template["font_size"] == pytest.approx(14.0)
    assert template["font_family"] == "Times New Roman"
    assert template["text_position"] == "below"
    assert "um_per_px" not in image["layout_scale_bars"][0]
    assert len(image["layout_scale_bars"]) == 1


def test_histology_drawn_scale_enters_layout_and_style_roundtrip() -> None:
    for module, panel_cls, layout_key, objects_fn, apply_fn, kind in (
        (
            "ihc",
            IHCPanel,
            "ihc_layout",
            lambda panel: ihc_layout_objects(panel, measurement={"measurement_uuid": "m-a"}),
            apply_ihc_layout_objects,
            "ihc_kind",
        ),
        (
            "brightfield",
            BrightfieldPanel,
            "brightfield_layout",
            lambda panel: brightfield_layout_objects(panel, measurement={"measurement_uuid": "m-a"}),
            apply_brightfield_layout_objects,
            "brightfield_kind",
        ),
    ):
        panel = panel_cls(panel_id="Figure1H", panel_uuid=f"panel-bridge-{module}")
        image_key = "ihc_images" if module == "ihc" else "brightfield_images"
        panel.data = {
            "active_image_uid": "image-a",
            image_key: [
                {
                    "uid": "image-a",
                    "metadata": {"width_px": 640, "height_px": 480},
                    "measurements": [{"measurement_uuid": "m-a"}],
                }
            ],
        }
        panel.analysis = {}
        resolved = panel.update_measurement_scale_bar(
            "image-a",
            length_um=40.0,
            line_px={"x1": 10.0, "y1": 80.0, "x2": 90.0, "y2": 80.0},
            calibration_source="manual_line",
        )
        layout = panel.data[layout_key]
        calibration = deepcopy(panel.active_image()["measurement_scale_bar"])
        assert resolved["um_per_px"] == pytest.approx(0.5)
        assert layout["scale_bars"] == [{"target_id": "image-a", "enabled": True}]
        assert "um_per_px" not in layout["scale_bars"][0]
        assert layout["scale_bar_template"]["label_length_um"] == pytest.approx(40.0)

        objects = objects_fn(panel)
        scale_bars = [obj for obj in objects if obj.metadata.get(kind) == "layout_scale_bar"]
        assert len(scale_bars) == 1
        edited = []
        for obj in objects:
            payload = obj.to_dict()
            if obj.metadata.get(kind) == "layout_scale_bar":
                payload["style"]["stroke_color"] = "#FFD13B"
                payload["style"]["font_size_pt"] = 12.0
                payload["style"]["stroke_width"] = 5.0
            edited.append(LayoutObject.from_dict(payload))
        apply_fn(panel, edited)
        assert panel.active_image()["measurement_scale_bar"] == calibration
        assert panel.active_image()["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
        assert panel.data[layout_key]["scale_bars"][0]["template"]["line_color"] == "#FFD13B"
        assert panel.data[layout_key]["scale_bars"][0]["template"]["font_size"] == pytest.approx(12.0)
        assert panel.data[layout_key]["scale_bars"][0]["template"]["line_thickness"] == pytest.approx(5.0)
        assert "um_per_px" not in panel.data[layout_key]["scale_bars"][0]
        assert len(panel.data[layout_key]["scale_bars"]) == 1


def test_histology_drawn_scale_attaches_to_source_image_without_overlay() -> None:
    panel = IHCPanel(panel_id="Figure1I", panel_uuid="panel-bridge-ihc-source")
    panel.data = {
        "active_image_uid": "image-a",
        "ihc_images": [
            {
                "uid": "image-a",
                "metadata": {"width_px": 640, "height_px": 480},
                "measurement_scale_bar": _drawn_scale(),
            }
        ],
        "ihc_layout": {},
    }
    panel.analysis = {}
    objects = ihc_layout_objects(panel)
    scale_bars = [obj for obj in objects if obj.metadata.get("ihc_kind") == "layout_scale_bar"]
    assert len(scale_bars) == 1
    assert scale_bars[0].metadata["target_output_key"] == "source_image"


def test_seed_is_idempotent_and_does_not_duplicate_bars() -> None:
    container = {}
    seed_layout_scale_presentation(
        container,
        measurement_scale=_drawn_scale(),
        target_ids=["image-a"],
        states_key="scale_bars",
        template_key="scale_bar_template",
    )
    seed_layout_scale_presentation(
        container,
        measurement_scale=_drawn_scale(length_um=80.0),
        target_ids=["image-a"],
        states_key="scale_bars",
        template_key="scale_bar_template",
    )
    assert container["scale_bars"] == [{"target_id": "image-a", "enabled": True}]
    assert container["scale_bar_template"]["label_length_um"] == pytest.approx(40.0)


def test_sync_scale_appearance_controls_restores_saved_style() -> None:
    owner = SimpleNamespace(
        scale_bar_length_spin=SimpleNamespace(value=lambda: 50.0, setValue=lambda value: setattr(owner, "_length", value), blockSignals=lambda _flag: None),
        scale_bar_manual_um_per_px_spin=SimpleNamespace(setValue=lambda value: setattr(owner, "_manual", value), blockSignals=lambda _flag: None, setSpecialValueText=lambda value: setattr(owner, "_manual_hint", value)),
        scale_bar_font_combo=SimpleNamespace(setCurrentFont=lambda font: setattr(owner, "_font", font.family()), blockSignals=lambda _flag: None),
        scale_bar_font_size_spin=SimpleNamespace(setValue=lambda value: setattr(owner, "_font_size", value), blockSignals=lambda _flag: None),
        scale_bar_line_width_spin=SimpleNamespace(setValue=lambda value: setattr(owner, "_width", value), blockSignals=lambda _flag: None),
        scale_bar_line_color_combo=SimpleNamespace(count=lambda: 1, itemData=lambda _index: "#111111", itemText=lambda _index: "Black", setCurrentIndex=lambda index: setattr(owner, "_line_index", index), blockSignals=lambda _flag: None),
        scale_bar_text_color_combo=SimpleNamespace(count=lambda: 1, itemData=lambda _index: "#222222", itemText=lambda _index: "Dark", setCurrentIndex=lambda index: setattr(owner, "_text_index", index), blockSignals=lambda _flag: None),
        scale_bar_text_position_combo=SimpleNamespace(count=lambda: 1, itemData=lambda _index: "below", itemText=lambda _index: "Below", setCurrentIndex=lambda index: setattr(owner, "_pos_index", index), blockSignals=lambda _flag: None),
        scale_show_text=SimpleNamespace(setChecked=lambda value: setattr(owner, "_show", value), blockSignals=lambda _flag: None),
    )
    sync_scale_appearance_controls(
        owner,
        {
            "label_length_um": 25.0,
            "font_family": "Arial",
            "font_size": 11.0,
            "line_thickness": 3.0,
            "line_color": "#111111",
            "text_color": "#222222",
            "text_position": "below",
            "show_text": False,
        },
        manual_um_per_px=0.0,
    )
    assert owner._length == pytest.approx(25.0)
    assert owner._font_size == pytest.approx(11.0)
    assert owner._width == pytest.approx(3.0)
    assert owner._show is False
    assert owner._line_index == 0
    assert owner._pos_index == 0


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _blank_native(width: int = 200, height: int = 120) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#000000"))
    return image


def _red_count(image: QImage) -> int:
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = QColor(image.pixel(x, y))
            if color.red() > 200 and color.green() < 40 and color.blue() < 40:
                count += 1
    return count


def test_native_layout_painter_draws_one_styled_bar_without_resize() -> None:
    _app()
    source = _blank_native()
    calibration = _drawn_scale()
    painted, action = paint_layout_scale_bar_on_native_image(
        source,
        target_id="image-a",
        native_width_px=200,
        native_height_px=120,
        source_metadata={"width_px": 200, "height_px": 120},
        measurement_scale=calibration,
        template={
            "label_length_um": 40.0,
            "line_color": "#FF0000",
            "text_color": "#FF0000",
            "line_thickness": 8.0,
            "show_text": False,
            "anchor": "bottom_right",
            "offset_x_fraction": 0.06,
            "offset_y_fraction": 0.08,
        },
        states=[{"target_id": "image-a", "enabled": True}],
    )

    assert action == "paint"
    assert painted.width() == 200
    assert painted.height() == 120
    assert _red_count(painted) > 200
    assert _red_count(source) == 0
    assert calibration["um_per_px"] == pytest.approx(0.5)
    assert calibration["line_px"]["x2"] == pytest.approx(90.0)


def test_native_layout_painter_hidden_does_not_resurrect_fallback() -> None:
    _app()
    source = _blank_native()
    calls: list[int] = []

    def fallback(image: QImage) -> QImage:
        calls.append(1)
        clone = QImage(image)
        clone.fill(QColor("#FF0000"))
        return clone

    result = apply_native_image_scale_overlay(
        source,
        target_id="image-a",
        native_width_px=200,
        native_height_px=120,
        source_metadata={"width_px": 200, "height_px": 120},
        measurement_scale=_drawn_scale(),
        template={"line_color": "#FF0000", "line_thickness": 8.0, "show_text": False},
        states=[{"target_id": "image-a", "enabled": False}],
        fallback_painter=fallback,
    )
    assert calls == []
    assert result.width() == 200
    assert result.height() == 120
    assert _red_count(result) == 0


def test_native_layout_painter_absent_uses_caller_fallback() -> None:
    _app()
    source = _blank_native()
    calls: list[int] = []

    def fallback(image: QImage) -> QImage:
        calls.append(1)
        clone = QImage(image)
        clone.fill(QColor("#00FF00"))
        return clone

    result = apply_native_image_scale_overlay(
        source,
        target_id="image-a",
        states=[],
        fallback_painter=fallback,
    )
    assert calls == [1]
    green = 0
    for y in range(result.height()):
        for x in range(result.width()):
            color = QColor(result.pixel(x, y))
            if color.green() > 200 and color.red() < 40:
                green += 1
    assert green == 200 * 120
