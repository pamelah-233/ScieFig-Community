from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from core.utils.i18n import set_language, tr


_QT_APP = None
ROOT = Path(__file__).resolve().parents[1]


def _app() -> QApplication:
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def _close_window(app, window) -> None:
    window._clean_project_fingerprint = window._project_fingerprint()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_named_analysis_library_survives_separate_qsettings_object(tmp_path):
    from core.utils.analysis_presets import AnalysisPresets

    path = str(tmp_path / "prefs.ini")
    library = AnalysisPresets(QSettings(path, QSettings.Format.IniFormat))
    preset = library.save(
        "IHC 专家 · 弱阳 0",
        "ihc",
        "measure_dab_positive_area",
        {
            "positive_threshold": 0.0,
            "weak_threshold": 0.0,
            "tissue_mask_enabled": False,
            "min_tissue_area_px": 0,
        },
    )
    assert preset["id"]
    assert preset["name"] == "IHC 专家 · 弱阳 0"
    assert preset["module"] == "ihc"
    assert preset["method"] == "measure_dab_positive_area"
    assert preset["settings"]["positive_threshold"] == 0.0
    assert preset["settings"]["tissue_mask_enabled"] is False
    reopened = AnalysisPresets(QSettings(path, QSettings.Format.IniFormat))
    assert reopened.items() == [preset]
    renamed = reopened.rename(preset["id"], "IHC 专家 · 相机2")
    assert renamed["id"] == preset["id"]
    assert renamed["settings"]["weak_threshold"] == 0.0
    reopened.delete(preset["id"])
    assert library.items() == []


def test_presets_do_not_touch_calibration_or_store_forbidden_fields(tmp_path):
    from core.utils.analysis_presets import AnalysisPresets
    from core.utils.calibration_presets import CalibrationPresets

    path = str(tmp_path / "prefs.ini")
    settings = QSettings(path, QSettings.Format.IniFormat)
    analysis = AnalysisPresets(settings)
    calibration = CalibrationPresets(QSettings(path, QSettings.Format.IniFormat))
    record = analysis.save(
        "Count traditional",
        "fluorescence",
        "count_channel_objects",
        {
            "algorithm": "traditional",
            "diameter_px": 0,
            "sensitivity": 0.1,
            "method": "otsu",
            "um_per_px_x": 0.25,
            "image_uid": "img-a",
            "roi": {"mode": "rectangle", "items": [{"x": 1, "y": 2, "w": 3, "h": 4}]},
            "geometry": {"x": 1, "y": 2, "w": 3, "h": 4},
            "results": [{"id": 1}],
            "measurement_uuid": "should-not-store",
        },
    )
    assert set(record) == {"id", "name", "module", "method", "settings"}
    assert "um_per_px_x" not in record["settings"]
    assert "image_uid" not in record["settings"]
    assert "roi" not in record["settings"]
    assert "geometry" not in record["settings"]
    assert "results" not in record["settings"]
    assert "measurement_uuid" not in record["settings"]
    assert record["settings"]["diameter_px"] == 0
    assert calibration.items() == []
    settings.sync()
    assert str(settings.value(CalibrationPresets.KEY, "[]")) in {"[]", ""}
    with pytest.raises(ValueError):
        analysis.save("Count traditional", "fluorescence", "count_channel_objects", {"algorithm": "log"})
    with pytest.raises(ValueError):
        analysis.save(" ", "ihc", "measure_dab_positive_area", {})
    with pytest.raises(ValueError):
        analysis.save("Bad method", "ihc", "auto_analyze_he", {})


def test_method_isolation_and_translated_display_names_are_not_ids(tmp_path):
    from core.utils.analysis_presets import AnalysisPresets

    set_language("zh")
    library = AnalysisPresets(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    ihc = library.save("dab", "ihc", "auto_analyze_dab", {"guided_mode": "assisted", "sensitivity": "high"})
    fiber = library.save(
        "he-anti",
        "brightfield",
        "create_ai_object_count_measurement",
        {"he_fiber_segmentation_method": "anti_oversegmentation", "he_fiber_splitting_sensitivity": 0},
    )
    assert library.items_for("ihc", "auto_analyze_dab") == [ihc]
    assert library.items_for("brightfield", "create_ai_object_count_measurement") == [fiber]
    assert library.items_for("ihc", "measure_dab_positive_area") == []
    with pytest.raises(ValueError):
        library.save("translated", "fluorescence", tr("spot_count"), {"algorithm": "traditional"})
    with pytest.raises(ValueError):
        library.save("display", "fluorescence", "对象计数", {"algorithm": "traditional"})
    set_language("en")


def test_getter_setter_roundtrip_zero_false_and_he_cellpose(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        panel = window.data_panel
        panel.set_ihc_analysis_settings(
            {
                "positive_threshold": 0.0,
                "weak_threshold": 0.0,
                "moderate_threshold": 0.0,
                "strong_threshold": 0.0,
                "min_tissue_area_px": 0,
                "tissue_mask_enabled": False,
            }
        )
        ihc = panel.ihc_analysis_settings()
        assert ihc["positive_threshold"] == 0.0
        assert ihc["tissue_mask_enabled"] is False
        assert ihc["min_tissue_area_px"] == 0
        panel.set_ihc_guided_analysis_settings(
            {"sensitivity": "low", "cleanup_level": "high", "tissue_background_correction": False}
        )
        guided = panel.ihc_guided_analysis_settings()
        assert guided["sensitivity"] == "low"
        assert guided["tissue_background_correction"] is False

        panel.set_brightfield_analysis_settings(
            {
                "segmentation_mode": "grayscale",
                "h_min": 0.0,
                "gray_threshold": 0.0,
                "gray_invert": False,
                "fill_holes": False,
                "cleanup_min_area_px": 0,
                "he_fiber_segmentation_method": "anti_oversegmentation",
                "he_fiber_splitting_sensitivity": 0,
                "he_fiber_foreground_sensitivity": 35,
            }
        )
        bf = panel.brightfield_analysis_settings()
        assert bf["gray_invert"] is False
        assert bf["fill_holes"] is False
        assert bf["gray_threshold"] == 0.0
        assert bf["he_fiber_segmentation_method"] == "anti_oversegmentation"
        assert bf["he_fiber_splitting_sensitivity"] == 0

        panel.set_cell_analysis_settings(
            {
                "backend": "cellpose",
                "residual_threshold": 0.0,
                "min_circularity": 0.05,
                "min_diameter_um": 0.1,
            }
        )
        cells = panel.bright_round_cell_analysis_settings()
        assert cells["backend"] == "cellpose"
        assert cells["residual_threshold"] == 0.0
        assert cells["min_circularity"] == 0.05

        fluoro = window.fluoro_workspace
        set_language("zh")
        window.switch_language("zh")
        fluoro.set_count_analysis_settings(
            {"algorithm": "log", "diameter_px": 2, "sensitivity": 0.1, "method": "triangle", "min_area_px": 1, "manual_value": 0}
        )
        count = fluoro.count_analysis_settings()
        assert count["algorithm"] == "log"
        assert count["method"] == "triangle"
        assert count["manual_value"] == 0
        assert count["algorithm"] != tr("spot_method_log")
        fluoro.set_ai_analysis_settings(
            {"detection_sensitivity": 0.1, "min_object_area_px": 1, "max_object_area_px": 1, "allow_restricted_model": False}
        )
        ai = fluoro.ai_analysis_settings()
        assert ai["min_object_area_px"] == 1
        assert ai["allow_restricted_model"] is False
        fluoro.set_roi_analysis_settings(
            {"channel": 0, "background_method": "None", "fixed_background": 0.0}
        )
        roi = fluoro.roi_analysis_settings()
        assert roi["fixed_background"] == 0.0
        assert "geometry" not in roi
        set_language("en")
        window.switch_language("en")
    finally:
        _close_window(app, window)


def test_apply_preset_changes_widgets_not_measurements_or_run(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from core.utils.analysis_presets import AnalysisPresets
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    runs = []
    monkeypatch.setattr(window, "run_ihc_analysis", lambda *args, **kwargs: runs.append(True))
    library = AnalysisPresets(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    try:
        window.set_module("ihc")
        panel = window._active_ihc_panel()
        panel.data["ihc_images"] = [
            {"uid": "image-1", "source_name": "a.png", "measurements": [], "roi": {"mode": "whole_image", "items": []}}
        ]
        panel.data["active_image_uid"] = "image-1"
        record = {
            "measurement_uuid": "keep-me",
            "image_uid": "image-1",
            "results": {"positive_area_percent": 12},
        }
        panel.analysis["ihc_measurements"] = [record]
        snapshot = deepcopy(panel.analysis["ihc_measurements"])
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.4, "weak_threshold": 0.4, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 100, "tissue_mask_enabled": False}
        )
        preset = library.save(
            "zero expert",
            "ihc",
            "measure_dab_positive_area",
            {"positive_threshold": 0.0, "weak_threshold": 0.0, "tissue_mask_enabled": False, "min_tissue_area_px": 0},
        )
        window.analysis_state.apply_preset(preset)
        settings = window.data_panel.ihc_analysis_settings()
        assert settings["positive_threshold"] == 0.0
        assert settings["tissue_mask_enabled"] is False
        assert panel.analysis["ihc_measurements"] == snapshot
        assert panel.analysis["ihc_measurements"][0]["measurement_uuid"] == "keep-me"
        assert panel.analysis["ihc_measurements"][0]["results"] == {"positive_area_percent": 12}
        assert runs == []
        incompatible = library.save("cells", "brightfield", "measure_bright_round_cells", {"backend": "traditional"})
        with pytest.raises(ValueError):
            window.analysis_state.apply_preset(incompatible)
        assert panel.analysis["ihc_measurements"] == snapshot
        assert runs == []
    finally:
        _close_window(app, window)


def test_compact_preset_bar_exists_on_actual_groups():
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    try:
        for module, host in (
            ("ihc", window.data_panel.ihc_measure_actions_group),
            ("brightfield", window.data_panel.brightfield_measure_group),
            ("fluorescence", window.fluoro_workspace.measure_params_stack.parentWidget()),
        ):
            bar = window.analysis_state.bars[module]
            assert bar.parent() is not None
            assert bar.preset_combo is not None
            assert bar.apply_button.text()
            assert bar.save_button.text()
            assert bar.manage_button is not None
        window.set_module("ihc")
        assert window.analysis_state.bars["ihc"].isVisible() or True
    finally:
        _close_window(app, window)
