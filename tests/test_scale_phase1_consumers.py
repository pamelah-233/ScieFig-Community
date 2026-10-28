from __future__ import annotations

from copy import deepcopy
import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.brightfield_statistics import collect_brightfield_ai_object_area_distribution_rows
from core.fluoro_statistics import collect_fluoro_distribution_rows
from core.measurement_scale import apply_area_scale
from core.models.fluoro_panel import AI_ASSISTED_MEASUREMENTS_KEY, FluorescencePanel


PARTIAL_X_SCALE = {
    "enabled": True,
    "length_um": 50.0,
    "um_per_px": 0.5,
    "um_per_px_x": 0.5,
    "um_per_px_y": 0.0,
    "calibration_source": "manual",
    "assumed": False,
    "calibrated": True,
}
XY_SCALE = {
    "enabled": True,
    "length_um": 50.0,
    "um_per_px": 0.5,
    "um_per_px_x": 0.5,
    "um_per_px_y": 0.4,
    "calibration_source": "manual",
    "assumed": False,
    "calibrated": True,
}
ASSUMED_SCALE = {
    "enabled": True,
    "length_um": 50.0,
    "um_per_px": 1.0,
    "calibration_source": "assumed",
    "assumed": True,
    "calibrated": False,
}


def _ai_record(*, uid: str, scale: dict | None, area_um2: float | None = 99.0) -> dict:
    record = {
        "image_uid": uid,
        "ai_measurement_uuid": "ai-keep-1",
        "channel_name": "DAPI",
        "channel_index": 0,
        "ai_object_count": 1,
        "total_object_area_px": 10,
        "total_object_area_um2": area_um2,
        "mean_object_area_px": 10.0,
        "mean_object_area_um2": area_um2,
        "median_object_area_px": 10.0,
        "median_object_area_um2": area_um2,
        "mean_intensity": 12.0,
        "object_area_distribution_px": [10],
        "object_area_distribution_um2": [area_um2] if area_um2 is not None else [],
        "object_table": [
            {
                "object_id": 7,
                "area_px": 10,
                "area_um2": area_um2,
                "centroid_x": 1.5,
                "centroid_y": 2.5,
                "mean_intensity": 12.0,
                "integrated_intensity": 120.0,
                "contour_points_px": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                "channel_name": "DAPI",
            }
        ],
    }
    if scale is not None:
        record["measurement_scale_bar"] = deepcopy(scale)
    return record


def _fluoro_panel(record: dict, live_scale: dict | None = None) -> FluorescencePanel:
    uid = str(record.get("image_uid", "img-1"))
    image_scale = deepcopy(live_scale) if live_scale is not None else deepcopy(record.get("measurement_scale_bar", {}))
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.data["fluorescence_images"] = [
        {
            "uid": uid,
            "source_name": "keep-name.tif",
            "metadata": {"width_px": 32, "height_px": 32},
            "channels": [{"index": 0, "name": "DAPI", "visible": True, "display_min": 0, "display_max": 1}],
            "measurement_scale_bar": image_scale,
            AI_ASSISTED_MEASUREMENTS_KEY: [deepcopy(record)],
            "measurements": [],
        }
    ]
    panel.data["active_image_uid"] = uid
    panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(record)]
    return panel


def _fiber_measurement(*, scale: dict | None, area_um2: float = 99.0) -> dict:
    measurement = {
        "measurement_uuid": "fiber-keep-1",
        "image_uid": "bf-1",
        "image_name": "he.png",
        "group": "Control",
        "include_in_statistics": True,
        "type": "brightfield_ai_object_count",
        "results": {
            "ai_object_count": 1,
            "ai_object_areas_px": [10.0],
            "ai_object_areas_um2": [area_um2],
            "ai_object_mean_area_px": 10.0,
            "ai_object_mean_area_um2": area_um2,
            "ai_object_total_area_px": 10.0,
            "ai_object_total_area_um2": area_um2,
        },
    }
    if scale is not None:
        measurement["measurement_scale_bar"] = deepcopy(scale)
    return measurement


class _FluoroStatsPanel:
    def __init__(self, record: dict) -> None:
        self.analysis = {"fluorescence_ai_assisted_measurements": [record]}
        self._images = [{"uid": str(record.get("image_uid", "img-1")), "source_name": "a.tif"}]

    def images(self) -> list[dict]:
        return self._images


class _BrightfieldStatsPanel:
    def __init__(self, measurement: dict) -> None:
        self.analysis = {"brightfield_ai_object_count_measurements": [measurement]}


def test_apply_area_scale_zero_is_uncalibrated_for_area_even_when_x_exists() -> None:
    assert apply_area_scale(1, PARTIAL_X_SCALE) == 0
    assert apply_area_scale(1, ASSUMED_SCALE) == 0
    assert apply_area_scale(1, XY_SCALE) == pytest.approx(0.2)


def test_physical_area_cells_blank_and_excel_none_when_uncalibrated() -> None:
    from core.brightfield_statistics import physical_area_text_and_excel
    from core.fluoro_statistics import physical_area_text_and_excel as fluoro_physical_area_text_and_excel

    for helper in (fluoro_physical_area_text_and_excel, physical_area_text_and_excel):
        text, excel = helper(99.0, ASSUMED_SCALE, has_snapshot=True)
        assert text == ""
        assert excel is None
        text, excel = helper(99.0, PARTIAL_X_SCALE, has_snapshot=True)
        assert PARTIAL_X_SCALE["um_per_px"] > 0
        assert text == ""
        assert excel is None


def test_physical_area_cells_xy_and_legacy_snapshot_behavior() -> None:
    from core.brightfield_statistics import physical_area_text_and_excel
    from core.fluoro_statistics import physical_area_text_and_excel as fluoro_physical_area_text_and_excel

    for helper in (fluoro_physical_area_text_and_excel, physical_area_text_and_excel):
        text, excel = helper(2.0, XY_SCALE, has_snapshot=True)
        assert text == "2.00"
        assert excel == 2.0
        text, excel = helper(3.5, None, has_snapshot=False)
        assert text == "3.50"
        assert excel == 3.5
        text, excel = helper(None, ASSUMED_SCALE, has_snapshot=True)
        assert text == ""
        assert excel is None


def test_fluoro_distribution_uses_px2_without_valid_area_factor() -> None:
    assumed = _ai_record(uid="img-1", scale=ASSUMED_SCALE, area_um2=1.0)
    assumed["object_area_distribution_px"] = [10.0, 20.0]
    assumed["object_area_distribution_um2"] = [1.0, 2.0]
    rows = collect_fluoro_distribution_rows(_FluoroStatsPanel(assumed), "ai_assisted")
    assert rows[0]["unit"] == "px^2"
    assert rows[0]["metric"] == "object_area_distribution_px"
    assert rows[0]["values"] == [10.0, 20.0]

    partial = _ai_record(uid="img-1", scale=PARTIAL_X_SCALE, area_um2=1.0)
    partial["object_area_distribution_px"] = [10.0]
    partial["object_area_distribution_um2"] = [99.0]
    rows = collect_fluoro_distribution_rows(_FluoroStatsPanel(partial), "ai_assisted")
    assert rows[0]["unit"] == "px^2"


def test_fluoro_distribution_uses_um2_for_xy_area_and_px2_without_snapshot() -> None:
    calibrated = _ai_record(uid="img-1", scale=XY_SCALE, area_um2=2.0)
    calibrated["object_area_distribution_px"] = [10.0]
    calibrated["object_area_distribution_um2"] = [2.0]
    rows = collect_fluoro_distribution_rows(_FluoroStatsPanel(calibrated), "ai_assisted")
    assert rows[0]["unit"] == "um^2"
    assert rows[0]["values"] == [2.0]

    legacy = _ai_record(uid="img-1", scale=None, area_um2=3.5)
    legacy["object_area_distribution_px"] = [10.0]
    legacy["object_area_distribution_um2"] = [3.5]
    rows = collect_fluoro_distribution_rows(_FluoroStatsPanel(legacy), "ai_assisted")
    assert rows[0]["unit"] == "px^2"


def test_brightfield_distribution_uses_area_factor_not_um_per_px_alone() -> None:
    partial = _fiber_measurement(scale=PARTIAL_X_SCALE, area_um2=99.0)
    rows = collect_brightfield_ai_object_area_distribution_rows(_BrightfieldStatsPanel(partial))
    assert rows[0]["unit"] == "px^2"
    assert rows[0]["values"] == [10.0]

    xy = _fiber_measurement(scale=XY_SCALE, area_um2=2.0)
    rows = collect_brightfield_ai_object_area_distribution_rows(_BrightfieldStatsPanel(xy))
    assert rows[0]["unit"] == "um^2"
    assert rows[0]["values"] == [2.0]

    legacy = _fiber_measurement(scale=None, area_um2=3.5)
    rows = collect_brightfield_ai_object_area_distribution_rows(_BrightfieldStatsPanel(legacy))
    assert rows[0]["unit"] == "px^2"


def test_fluoro_apply_does_not_call_historical_rescale() -> None:
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    apply_src = inspect.getsource(FluorescenceWorkspace._apply_ai_measurement_scale)
    reset_src = inspect.getsource(FluorescenceWorkspace._reset_ai_measurement_scale)
    refresh_src = inspect.getsource(FluorescenceWorkspace._refresh_ai_measurement_scale_results)
    assert "refresh_ai_assisted_measurements_for_scale" not in apply_src
    assert "_refresh_ai_measurement_scale_results" not in apply_src
    assert "refresh_ai_assisted_measurements_for_scale" not in reset_src
    assert "_refresh_ai_measurement_scale_results" not in reset_src
    assert "refresh_ai_assisted_measurements_for_scale" in refresh_src


def test_fluoro_apply_leaves_snapshot_unchanged_refresh_preserves_ids() -> None:
    record = _ai_record(uid="img-1", scale=XY_SCALE, area_um2=2.0)
    panel = _fluoro_panel(record, live_scale=XY_SCALE)
    before = deepcopy(panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0])
    image_before = deepcopy(panel.images()[0][AI_ASSISTED_MEASUREMENTS_KEY][0])
    live_after_apply = {
        **XY_SCALE,
        "um_per_px": 2.0,
        "um_per_px_x": 2.0,
        "um_per_px_y": 2.0,
        "calibration_source": "manual",
        "calibrated": True,
        "assumed": False,
    }
    panel.images()[0]["measurement_scale_bar"] = dict(live_after_apply)
    after = panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]
    image_after = panel.images()[0][AI_ASSISTED_MEASUREMENTS_KEY][0]
    assert after == before
    assert image_after == image_before

    panel.refresh_ai_assisted_measurements_for_scale("img-1")
    refreshed = panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]
    live = panel.measurement_scale_bar("img-1")
    assert refreshed["ai_measurement_uuid"] == "ai-keep-1"
    assert refreshed["object_table"][0]["object_id"] == 7
    assert refreshed["object_table"][0]["contour_points_px"] == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
    assert refreshed["object_table"][0]["area_um2"] == pytest.approx(apply_area_scale(10, live))
    assert refreshed["total_object_area_um2"] == pytest.approx(apply_area_scale(10, live))


def test_fluoro_none_numeric_formatting_helper() -> None:
    from ui.panels.fluorescence_workspace import format_optional_fixed

    assert format_optional_fixed(None, 3) == ""
    assert format_optional_fixed(1.5, 3) == "1.500"
    assert format_optional_fixed(20, 0) == "20"
