from __future__ import annotations

import pytest

from core.layout_scale_bar import (
    DEFAULT_SCALE_BAR_TEMPLATE,
    normalize_scale_bar_template,
    resolve_scale_bar_calibration,
    scale_bar_layout_object,
)


def test_scale_bar_embedded_calibration_uses_source_pixels_scaled_to_layout_target() -> None:
    target = {"x": 10.0, "y": 20.0, "width": 500.0, "height": 250.0}
    metadata = {"width_px": 1000, "height_px": 500, "pixel_size_um": 0.5}

    obj = scale_bar_layout_object(
        panel_uuid="panel-a",
        module_kind="ihc",
        target_id="image-a",
        target_rect=target,
        source_metadata=metadata,
        template={"label_length_um": 50.0, "anchor": "bottom_right", "offset_x_fraction": 0.1, "offset_y_fraction": 0.2},
    )

    assert obj.object_type == "scale_bar"
    assert obj.width == pytest.approx(50.0)
    assert obj.x == pytest.approx(410.0)
    assert obj.metadata["calibration_source"] == "embedded"
    assert obj.metadata["label_text"] == "50 um"


def test_scale_bar_manual_calibration_is_used_when_embedded_metadata_is_absent() -> None:
    calibration = resolve_scale_bar_calibration({}, manual_um_per_px=2.0)

    assert calibration.source == "manual"
    assert calibration.um_per_px == pytest.approx(2.0)

    obj = scale_bar_layout_object(
        panel_uuid="panel-a",
        module_kind="brightfield",
        target_id="image-a",
        target_rect={"x": 0, "y": 0, "width": 300, "height": 200},
        source_metadata={"width_px": 600, "height_px": 400},
        template={"label_length_um": 40.0},
        state={"manual_um_per_px": 2.0},
    )

    assert obj.width == pytest.approx(10.0)
    assert obj.metadata["calibration_source"] == "manual"


def test_scale_bar_assumed_calibration_uses_visual_fraction_and_plain_label() -> None:
    obj = scale_bar_layout_object(
        panel_uuid="panel-a",
        module_kind="fluorescence",
        target_id="merge",
        target_rect={"x": 0, "y": 0, "width": 400, "height": 300},
        source_metadata={},
        template={"label_length_um": 50.0, "fallback_bar_fraction": 0.25},
    )

    assert obj.width == pytest.approx(100.0)
    assert obj.metadata["calibration_source"] == "assumed"
    assert obj.metadata["label_text"] == "100 px"
    assert obj.text == "100 px"
    assert "um" not in obj.metadata["label_text"]
    assert "(assumed)" not in obj.text


def test_scale_bar_rejects_invalid_manual_calibration() -> None:
    with pytest.raises(ValueError, match="manual_um_per_px"):
        resolve_scale_bar_calibration({}, manual_um_per_px=0)


def test_normalize_scale_bar_template_keeps_expected_defaults_and_bounds() -> None:
    template = normalize_scale_bar_template({"label_length_um": 0, "line_thickness": -3, "text_position": "sideways"})

    assert template["label_length_um"] == DEFAULT_SCALE_BAR_TEMPLATE["label_length_um"]
    assert template["line_thickness"] == DEFAULT_SCALE_BAR_TEMPLATE["line_thickness"]
    assert template["text_position"] == DEFAULT_SCALE_BAR_TEMPLATE["text_position"]
