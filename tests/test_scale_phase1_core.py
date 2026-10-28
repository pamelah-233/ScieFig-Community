from __future__ import annotations

from copy import deepcopy
from math import hypot, inf, nan

import pytest

from core.measurement_scale import (
    apply_area_scale,
    apply_length_scale,
    line_length_px,
    measurement_scale_apply_updates,
    merge_measurement_scale_updates,
    resolve_calibration,
    resolve_measurement_scale,
)


LINE_10 = {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 0.0}
LINE_3_4 = {"x1": 0.0, "y1": 0.0, "x2": 3.0, "y2": 4.0}
RECORD = {"metadata": {"width_px": 1024, "height_px": 768}}


def _assert_axes(result: dict, *, x: float, y: float, source: str, calibrated: bool) -> None:
    assert result["um_per_px_x"] == pytest.approx(x)
    assert result["um_per_px_y"] == pytest.approx(y)
    assert result["um_per_px"] == pytest.approx(x)
    assert result["calibration_source"] == source
    assert result["assumed"] is (source == "assumed")
    assert result["calibrated"] is calibrated


def test_resolve_calibration_rejects_non_finite_and_non_positive_values() -> None:
    for bad in (inf, -inf, nan, 0, -0.2, None, "", "abc", True):
        cal = resolve_calibration({"pixel_size_um": bad, "pixel_size_x_um": bad, "pixel_size_y_um": bad})
        _assert_axes(cal, x=0.0, y=0.0, source="assumed", calibrated=False)
        saved = {
            "calibration_source": "manual",
            "um_per_px": bad,
            "um_per_px_x": bad,
            "um_per_px_y": bad,
            "length_um": bad,
            "line_px": LINE_10,
        }
        cal = resolve_calibration({}, saved)
        _assert_axes(cal, x=0.0, y=0.0, source="assumed", calibrated=False)


def test_resolve_calibration_accepts_finite_positive_numeric_strings() -> None:
    cal = resolve_calibration({"pixel_size_um": "0.25"})
    _assert_axes(cal, x=0.25, y=0.25, source="embedded", calibrated=True)


def test_manual_line_uses_length_over_hypot_and_ignores_stale_cache() -> None:
    saved = {
        "calibration_source": "manual_line",
        "line_px": dict(LINE_3_4),
        "length_um": 10.0,
        "um_per_px": 99.0,
        "um_per_px_x": 99.0,
        "um_per_px_y": 99.0,
    }
    cal = resolve_calibration({"pixel_size_um": 0.11}, saved)
    _assert_axes(cal, x=2.0, y=2.0, source="manual_line", calibrated=True)


def test_explicit_manual_um_per_px_is_isotropic() -> None:
    saved = {
        "calibration_source": "manual",
        "um_per_px": 0.5,
        "line_px": dict(LINE_10),
        "length_um": 20.0,
    }
    cal = resolve_calibration({"pixel_size_um": 0.11}, saved)
    _assert_axes(cal, x=0.5, y=0.5, source="manual", calibrated=True)


def test_explicit_saved_axes_are_retained() -> None:
    saved = {
        "calibration_source": "manual",
        "um_per_px": 0.2,
        "um_per_px_x": 0.2,
        "um_per_px_y": 0.8,
    }
    cal = resolve_calibration({"pixel_size_x_um": 9.0, "pixel_size_y_um": 9.0}, saved)
    _assert_axes(cal, x=0.2, y=0.8, source="manual", calibrated=True)


def test_missing_axis_stays_zero_unless_legacy_isotropic_scalar_present() -> None:
    cal = resolve_calibration({}, {"calibration_source": "manual", "um_per_px_x": 0.2})
    _assert_axes(cal, x=0.2, y=0.0, source="manual", calibrated=True)

    cal = resolve_calibration({}, {"calibration_source": "manual", "um_per_px_x": 0.2, "um_per_px": 0.5})
    _assert_axes(cal, x=0.2, y=0.0, source="manual", calibrated=True)

    cal = resolve_calibration({"pixel_size_y_um": 0.4})
    _assert_axes(cal, x=0.0, y=0.4, source="embedded", calibrated=True)


def test_embedded_source_rereads_metadata_and_ignores_cache() -> None:
    saved = {
        "calibration_source": "embedded",
        "um_per_px": 9.9,
        "um_per_px_x": 9.9,
        "um_per_px_y": 9.9,
    }
    cal = resolve_calibration({"pixel_size_um": 0.25}, saved)
    _assert_axes(cal, x=0.25, y=0.25, source="embedded", calibrated=True)

    cal = resolve_calibration({"pixel_size_x_um": 0.1, "pixel_size_y_um": 0.4}, saved)
    _assert_axes(cal, x=0.1, y=0.4, source="embedded", calibrated=True)

    cal = resolve_calibration({}, saved)
    _assert_axes(cal, x=0.0, y=0.0, source="assumed", calibrated=False)


def test_old_assumed_positive_cache_is_not_physical_calibration() -> None:
    saved = {
        "calibration_source": "assumed",
        "um_per_px": 0.27,
        "um_per_px_x": 0.27,
        "um_per_px_y": 0.27,
        "length_um": 50.0,
        "line_px": dict(LINE_10),
    }
    cal = resolve_calibration({}, saved)
    _assert_axes(cal, x=0.0, y=0.0, source="assumed", calibrated=False)
    assert cal["um_per_px"] != pytest.approx(0.27)


def test_legacy_positive_without_source_is_manual() -> None:
    cal = resolve_calibration({}, {"um_per_px": 0.4})
    _assert_axes(cal, x=0.4, y=0.4, source="manual", calibrated=True)

    cal = resolve_calibration({}, {"um_per_px_x": 0.2, "um_per_px_y": 0.3})
    _assert_axes(cal, x=0.2, y=0.3, source="manual", calibrated=True)


def test_legacy_unsourced_line_is_manual_line() -> None:
    cal = resolve_calibration({}, {"line_px": dict(LINE_10), "length_um": 5.0})
    _assert_axes(cal, x=0.5, y=0.5, source="manual_line", calibrated=True)


def test_resolve_calibration_does_not_fabricate_one_um_per_px() -> None:
    cal = resolve_calibration({})
    _assert_axes(cal, x=0.0, y=0.0, source="assumed", calibrated=False)
    assert cal["um_per_px"] != 1.0


def test_resolve_measurement_scale_keeps_old_keys_and_adds_axes() -> None:
    saved = {
        "enabled": False,
        "length_um": 100.0,
        "um_per_px": 0.173611,
        "line_px": dict(LINE_10),
        "calibration_source": "manual_line",
    }
    resolved = resolve_measurement_scale(RECORD, saved)
    assert resolved["enabled"] is False
    assert resolved["length_um"] == pytest.approx(100.0)
    assert resolved["line_px"] == LINE_10
    assert resolved["line_length_px"] == pytest.approx(10.0)
    assert resolved["calibration_source"] == "manual_line"
    assert resolved["assumed"] is False
    _assert_axes(resolved, x=10.0, y=10.0, source="manual_line", calibrated=True)


def test_assumed_display_empty_and_roundtrip_stays_uncalibrated() -> None:
    first = resolve_measurement_scale(RECORD, None)
    assert first["display_line_px"] == {}
    _assert_axes(first, x=0.0, y=0.0, source="assumed", calibrated=False)
    second = resolve_measurement_scale(RECORD, first)
    assert second["display_line_px"] == {}
    _assert_axes(second, x=0.0, y=0.0, source="assumed", calibrated=False)


def test_unknown_saved_fields_are_deep_copied() -> None:
    saved = {
        "calibration_source": "manual",
        "um_per_px": 0.2,
        "preset_id": "p1",
        "preset_name": "Mine",
        "preset": {"id": "p1", "name": "Mine"},
    }
    resolved = resolve_measurement_scale(RECORD, saved)
    assert resolved["preset_id"] == "p1"
    assert resolved["preset_name"] == "Mine"
    assert resolved["preset"] == {"id": "p1", "name": "Mine"}
    resolved["preset"]["id"] = "changed"
    assert saved["preset"]["id"] == "p1"


def test_resolve_functions_do_not_mutate_inputs() -> None:
    metadata = {"pixel_size_um": 0.3, "nested": {"keep": 1}}
    saved = {
        "calibration_source": "manual",
        "um_per_px": 0.2,
        "line_px": dict(LINE_10),
        "extra": {"k": "v"},
    }
    metadata_copy = deepcopy(metadata)
    saved_copy = deepcopy(saved)
    resolve_calibration(metadata, saved)
    resolve_measurement_scale({"metadata": metadata}, saved)
    apply_area_scale(4.0, saved)
    apply_length_scale(1.0, 1.0, saved)
    measurement_scale_apply_updates(saved, length_um=40.0, displayed_um_per_px=0.7)
    assert metadata == metadata_copy
    assert saved == saved_copy


def test_apply_area_scale_uses_xy_or_legacy_isotropic_and_zero_without_calibration() -> None:
    assert apply_area_scale(10.0, {"um_per_px_x": 2.0, "um_per_px_y": 3.0}) == pytest.approx(60.0)
    assert apply_area_scale(10.0, {"um_per_px": 2.0}) == pytest.approx(40.0)
    assert apply_area_scale(10.0, {"um_per_px_x": 2.0, "um_per_px_y": 0.0}) == 0.0
    assert apply_area_scale(10.0, {"um_per_px": 2.0, "assumed": True}) == 0.0
    assert apply_area_scale(10.0, {"um_per_px": 2.0, "calibration_source": "assumed"}) == 0.0
    assert apply_area_scale(10.0, {"um_per_px": 2.0, "calibrated": False}) == 0.0
    assert apply_area_scale(10.0, None) == 0.0


def test_apply_length_scale_uses_hypot_and_zero_if_needed_axis_missing() -> None:
    scale = {"um_per_px_x": 0.2, "um_per_px_y": 0.5}
    assert apply_length_scale(3.0, 4.0, scale) == pytest.approx(hypot(0.6, 2.0))
    assert apply_length_scale(3.0, 0.0, {"um_per_px_x": 0.2}) == pytest.approx(0.6)
    assert apply_length_scale(0.0, 4.0, {"um_per_px_y": 0.5}) == pytest.approx(2.0)
    assert apply_length_scale(3.0, 4.0, {"um_per_px_x": 0.2}) == 0.0
    assert apply_length_scale(3.0, 4.0, {"um_per_px": 2.0}) == pytest.approx(hypot(6.0, 8.0))
    assert apply_length_scale(3.0, 4.0, {"um_per_px": 1.0, "assumed": True}) == 0.0
    assert apply_length_scale(3.0, 4.0, None) == 0.0


def test_apply_helper_keeps_manual_line_when_spin_matches() -> None:
    saved = {"calibration_source": "manual_line", "um_per_px": 0.5, "length_um": 100.0}
    matched = measurement_scale_apply_updates(saved, length_um=80.0, displayed_um_per_px=0.5)
    assert matched == {"length_um": 80.0, "calibration_source": "manual_line"}
    typed = measurement_scale_apply_updates(saved, length_um=80.0, displayed_um_per_px=0.7)
    assert typed["calibration_source"] == "manual"
    assert typed["um_per_px"] == pytest.approx(0.7)
    assert typed["length_um"] == pytest.approx(80.0)


def test_explicit_x_metadata_alias_does_not_fill_y_or_area() -> None:
    cal = resolve_calibration({"pixel_size_um": 0.3, "pixel_size_x_um": 0.3})
    _assert_axes(cal, x=0.3, y=0.0, source="embedded", calibrated=True)
    assert apply_area_scale(10.0, cal) == 0.0


def test_merge_typed_scalar_clears_cached_axes_then_resolves_isotropic() -> None:
    current = {
        "enabled": True,
        "length_um": 50.0,
        "um_per_px": 0.3,
        "um_per_px_x": 0.3,
        "um_per_px_y": 0.6,
        "calibration_source": "manual",
        "line_px": dict(LINE_10),
        "calibrated": True,
        "assumed": False,
    }
    current_copy = deepcopy(current)
    merged = merge_measurement_scale_updates(
        current, {"um_per_px": 0.5, "calibration_source": "manual"}
    )
    assert current == current_copy
    assert "um_per_px_x" not in merged
    assert "um_per_px_y" not in merged
    resolved = resolve_measurement_scale(RECORD, merged)
    _assert_axes(resolved, x=0.5, y=0.5, source="manual", calibrated=True)


def test_merge_manual_zero_reset_rereads_embedded_metadata() -> None:
    current = {
        "calibration_source": "manual",
        "um_per_px": 0.5,
        "um_per_px_x": 0.5,
        "um_per_px_y": 0.5,
        "length_um": 50.0,
        "line_px": dict(LINE_10),
        "display_line_px": dict(LINE_10),
    }
    updates = measurement_scale_apply_updates(current, length_um=50.0, displayed_um_per_px=0.0)
    assert updates["calibration_source"] == "embedded"
    assert updates["um_per_px"] == 0.0
    merged = merge_measurement_scale_updates(current, updates)
    assert merged.get("line_px") in ({}, None)
    record = {"metadata": {"pixel_size_um": 0.25, "width_px": 1024, "height_px": 768}}
    resolved = resolve_measurement_scale(record, merged)
    _assert_axes(resolved, x=0.25, y=0.25, source="embedded", calibrated=True)


def test_generated_display_line_uses_exact_length_not_clamped_fraction() -> None:
    record = {"metadata": {"width_px": 1024, "height_px": 768}}
    saved = {"calibration_source": "manual", "um_per_px": 0.05, "length_um": 50.0}
    resolved = resolve_measurement_scale(record, saved)
    display = resolved["display_line_px"]
    span = abs(float(display["x2"]) - float(display["x1"]))
    assert span == pytest.approx(1000.0)
    assert span != pytest.approx(460.8)


def test_merge_explicit_axis_updates_remain_axes() -> None:
    current = {
        "um_per_px": 0.3,
        "um_per_px_x": 0.3,
        "um_per_px_y": 0.6,
        "calibration_source": "manual",
    }
    merged = merge_measurement_scale_updates(
        current, {"um_per_px_x": 0.4, "um_per_px_y": 0.8}
    )
    resolved = resolve_measurement_scale(RECORD, merged)
    _assert_axes(resolved, x=0.4, y=0.8, source="manual", calibrated=True)


def test_line_length_px_keeps_signed_translation_and_rejects_nonfinite() -> None:
    original = {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 0.0}
    translated = {"x1": -5.0, "y1": 0.0, "x2": 5.0, "y2": 0.0}
    assert line_length_px(original) == pytest.approx(10.0)
    assert line_length_px(translated) == pytest.approx(10.0)
    assert line_length_px({"x1": inf, "y1": 0.0, "x2": 10.0, "y2": 0.0}) == 0.0
    assert line_length_px({"x1": 0.0, "y1": nan, "x2": 10.0, "y2": 0.0}) == 0.0
    saved = {
        "calibration_source": "manual_line",
        "line_px": dict(translated),
        "length_um": 5.0,
        "um_per_px": 99.0,
    }
    cal = resolve_calibration({}, saved)
    _assert_axes(cal, x=0.5, y=0.5, source="manual_line", calibrated=True)
    resolved = resolve_measurement_scale(RECORD, saved)
    assert resolved["line_length_px"] == pytest.approx(10.0)
    assert resolved["um_per_px"] == pytest.approx(0.5)


def test_assumed_cache_yields_to_genuine_metadata() -> None:
    saved = {
        "calibration_source": "assumed",
        "um_per_px": 9.0,
        "um_per_px_x": 9.0,
        "um_per_px_y": 9.0,
        "length_um": 50.0,
        "line_px": dict(LINE_10),
    }
    cal = resolve_calibration({"pixel_size_um": 0.25}, saved)
    _assert_axes(cal, x=0.25, y=0.25, source="embedded", calibrated=True)
    cal = resolve_calibration({}, saved)
    _assert_axes(cal, x=0.0, y=0.0, source="assumed", calibrated=False)
