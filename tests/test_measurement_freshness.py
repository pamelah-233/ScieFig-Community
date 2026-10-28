from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtWidgets import QApplication

from core.measurement_freshness import (
    attach_analysis_inputs,
    build_analysis_inputs,
    display_status,
    normalize_analysis_inputs,
    result_freshness,
    saved_inputs_from_record,
)
from core.utils.i18n import set_language, tr


_QT_APP = None


def _app() -> QApplication:
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def _base_inputs(**overrides):
    payload = build_analysis_inputs(
        method="ihc",
        settings={"mode": "foolproof", "sensitivity": "medium", "cleanup_level": "medium"},
        image_uid="image-1",
        roi={"mode": "whole_image", "items": []},
        calibration={"um_per_px_x": 0.5, "um_per_px_y": 0.5, "calibration_source": "manual"},
    )
    payload.update(overrides)
    return normalize_analysis_inputs(payload)


def test_result_freshness_none_is_unknown_never_current():
    current = _base_inputs()
    assert result_freshness(None, current) == "unknown"
    assert result_freshness(None, None) == "unknown"


def test_unchanged_effective_settings_are_current():
    saved = _base_inputs()
    current = deepcopy(saved)
    assert result_freshness(saved, current) == "current"


def test_changed_settings_roi_or_calibration_are_stale():
    saved = _base_inputs()
    assert result_freshness(saved, _base_inputs(settings={**saved["settings"], "sensitivity": "high"})) == "stale"
    assert result_freshness(saved, _base_inputs(roi={"mode": "rectangle", "items": [{"x": 1, "y": 2, "w": 3, "h": 4}]})) == "stale"
    assert result_freshness(saved, _base_inputs(calibration={**saved["calibration"], "um_per_px_x": 0.25})) == "stale"


def test_style_and_overlay_opacity_do_not_invalidate():
    saved = _base_inputs()
    current = deepcopy(saved)
    current["overlay_opacity"] = 0.9
    current["style"] = {"line_color": "#ff00aa", "font_size": 18}
    current["display_name"] = "pretty"
    assert result_freshness(normalize_analysis_inputs(saved), normalize_analysis_inputs(current)) == "current"


def test_normalize_keeps_analysis_enabled_and_uses_semantic_roi_calibration():
    saved = build_analysis_inputs(
        method="ihc",
        settings={"mode": "expert", "enabled": False, "tissue_mask_enabled": False},
        image_uid="image-1",
        roi={"mode": "rectangle", "items": [{"x": 1, "y": 2, "w": 3, "h": 4, "enabled": True}], "mask": np.ones((8, 8), dtype=bool)},
        calibration={"um_per_px_x": 0.5, "um_per_px_y": 0.5, "length_um": 50, "line_color": "#fff", "calibration_source": "manual"},
    )
    assert saved["settings"]["enabled"] is False
    assert saved["settings"]["tissue_mask_enabled"] is False
    assert saved["roi"]["mode"] == "rectangle"
    assert saved["roi"]["items"][0]["enabled"] is True
    assert "mask" not in saved["roi"]
    assert saved["calibration"]["um_per_px_x"] == 0.5
    assert "length_um" not in saved["calibration"]
    assert "line_color" not in saved["calibration"]


def test_legacy_record_without_captured_inputs_is_unknown():
    record = {
        "measurement_uuid": "legacy-1",
        "image_uid": "image-1",
        "params": {"sensitivity": "medium"},
        "roi": {"mode": "whole_image"},
    }
    assert saved_inputs_from_record(record) is None
    assert result_freshness(saved_inputs_from_record(record), _base_inputs()) == "unknown"


def test_freshness_belongs_to_record_identity_not_a_global_flag():
    first = {"measurement_uuid": "a"}
    second = {"measurement_uuid": "b"}
    attach_analysis_inputs(first, _base_inputs())
    attach_analysis_inputs(second, _base_inputs(settings={"mode": "expert", "positive_threshold": 0.2}))
    current = _base_inputs()
    assert result_freshness(saved_inputs_from_record(first), current) == "current"
    assert result_freshness(saved_inputs_from_record(second), current) == "stale"


def test_multi_channel_records_keep_separate_identities():
    channel_zero = {"count_uuid": "c0", "channel_index": 0}
    channel_one = {"count_uuid": "c1", "channel_index": 1}
    shared = build_analysis_inputs(
        method="fluoro_count",
        settings={"algorithm": "traditional", "min_area_px": 4},
        image_uid="image-1",
        channel_indices=[0, 1],
    )
    attach_analysis_inputs(channel_zero, shared)
    attach_analysis_inputs(channel_one, shared)
    assert channel_zero["count_uuid"] != channel_one["count_uuid"]
    assert saved_inputs_from_record(channel_zero) == saved_inputs_from_record(channel_one)
    current = build_analysis_inputs(
        method="fluoro_count",
        settings={"algorithm": "traditional", "min_area_px": 9},
        image_uid="image-1",
        channel_indices=[0, 1],
    )
    assert result_freshness(saved_inputs_from_record(channel_zero), current) == "stale"
    assert result_freshness(saved_inputs_from_record(channel_one), current) == "stale"


def test_failed_attempt_keeps_prior_result_freshness():
    saved = _base_inputs()
    current = deepcopy(saved)
    assert display_status(has_result=True, saved_inputs=saved, current_inputs=current, attempt="failed") == ("failed", "current")
    assert display_status(has_result=False, saved_inputs=None, current_inputs=current, attempt="failed") == "failed"
    assert display_status(has_result=True, saved_inputs=saved, current_inputs=current, attempt="running") == "calculating"
    assert display_status(has_result=False, saved_inputs=None, current_inputs=current, attempt=None) == "not_calculated"


def test_attach_copies_inputs_and_does_not_share_mutation():
    record = {"measurement_uuid": "new"}
    captured = _base_inputs()
    attach_analysis_inputs(record, captured)
    captured["settings"]["sensitivity"] = "mutated"
    assert saved_inputs_from_record(record)["settings"]["sensitivity"] == "medium"


def test_raw_arrays_are_not_compared():
    saved = _base_inputs()
    current = deepcopy(saved)
    current["mask"] = np.ones((4, 4), dtype=bool)
    current["roi"] = {**current["roi"], "mask": np.zeros((4, 4), dtype=bool)}
    assert result_freshness(normalize_analysis_inputs(saved), normalize_analysis_inputs(current)) == "current"


def test_status_i18n_keys_are_compact_and_not_accuracy_claims():
    from core.measurement_freshness import STATUS_I18N_KEYS

    set_language("en")
    assert tr(STATUS_I18N_KEYS["current"]) == "Matches current settings"
    assert tr(STATUS_I18N_KEYS["stale"]) == "Parameters changed, recalculate"
    assert "accurate" not in tr(STATUS_I18N_KEYS["current"]).lower()
    set_language("zh")
    assert tr(STATUS_I18N_KEYS["current"]) == "与当前设置一致"
    assert tr(STATUS_I18N_KEYS["unknown"]) == "旧结果输入未知"
    set_language("en")


def test_window_status_label_and_attempt_states():
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("ihc")
        label = window.data_panel.ihc_freshness_status_label
        assert label.objectName() == "DimLabel"
        assert label.text() == tr("result_status_not_calculated")
        panel = window._active_ihc_panel()
        panel.data["ihc_images"] = [{"uid": "image-1", "source_name": "a.png", "measurements": [], "roi": {"mode": "whole_image", "items": []}}]
        panel.data["active_image_uid"] = "image-1"
        legacy = {"measurement_uuid": "legacy-row", "image_uid": "image-1", "results": {"positive_area_percent": 1}}
        panel.analysis["ihc_measurements"] = [legacy]
        window.data_panel.ihc_results_table.blockSignals(True)
        window.data_panel.populate_ihc_measurements([legacy])
        window.data_panel.ihc_results_table.blockSignals(False)
        window._refresh_result_freshness_status()
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_unknown")
        window.data_panel.set_ihc_guided_mode("assisted")
        current = window._current_analysis_inputs("ihc")
        attach_analysis_inputs(legacy, current)
        window._refresh_result_freshness_status()
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_current")
        window.data_panel.ihc_sensitivity_combo.setCurrentIndex(
            max(0, window.data_panel.ihc_sensitivity_combo.findData("high"))
        )
        window._refresh_result_freshness_status()
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_stale")
        window.data_panel.set_ihc_overlay_opacity(0.95)
        window._refresh_result_freshness_status()
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_stale")
        window._begin_analysis_attempt("ihc", "image-1", "ihc", current)
        window._refresh_result_freshness_status()
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_calculating")
        window._finish_analysis_attempt("ihc", failed=True)
        window._refresh_result_freshness_status()
        text = window.data_panel.ihc_freshness_status_label.text()
        assert tr("result_status_failed") in text
        assert tr("result_status_stale") in text
        window._analysis_attempts.clear()
        window._analysis_attempts_by_job.clear()
        window._refresh_result_freshness_status()
        assert window.data_panel.ihc_freshness_status_label.text() != tr("result_status_calculating")
    finally:
        window._clean_project_fingerprint = window._project_fingerprint()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_success_attaches_invocation_inputs_not_later_widget_values(monkeypatch):
    from core.models.ihc_panel import IHCPanel
    from ui.controllers.analysis_job_service import AnalysisJobService
    from ui.controllers.ihc_analysis_controller import IHCAnalysisController
    from tests.test_analysis_async import _apply_snapshot, _app as async_app, _wait_until

    app = async_app()
    service = AnalysisJobService()
    panel = IHCPanel(panel_id="IHC")
    panel.data["ihc_images"] = [{"uid": "image-1", "source_name": "image.png", "measurements": [], "roi": {"mode": "whole_image", "items": []}}]
    panel.data["active_image_uid"] = "image-1"
    applied = []
    captures = []

    def fake_ihc(self, settings=None, guided_mode="foolproof"):
        measurement = {"measurement_uuid": "ihc-capture", "image_uid": "image-1", "results": {"positive": 7}}
        self.analysis["ihc_measurements"] = [measurement]
        return measurement

    def capture():
        payload = build_analysis_inputs(
            method="ihc",
            settings={"mode": "foolproof", "sensitivity": "medium"},
            image_uid="image-1",
            roi={"mode": "whole_image", "items": []},
        )
        captures.append(payload)
        return payload

    monkeypatch.setattr(IHCPanel, "auto_analyze_dab", fake_ihc)
    controller = IHCAnalysisController(
        get_panel=lambda: panel,
        get_expert_settings=lambda: {},
        get_assisted_settings=lambda: {"sensitivity": "medium"},
        get_foolproof_settings=lambda: {"sensitivity": "medium"},
        get_current_mode=lambda: "foolproof",
        get_overlay_opacity=lambda: 0.35,
        get_current_task_type=lambda: "ihc_dab",
        get_examples_for_image=lambda _uid: [],
        clear_examples_for_image=lambda _uid: None,
        clear_example_click_mode=lambda: None,
        refresh_measurement_views=lambda _panel, measurement, **_kwargs: applied.append(measurement),
        notify_user=lambda *_args: None,
        report_failure=lambda *_args: None,
        run_async=service.start,
        apply_panel_snapshot=_apply_snapshot,
        capture_analysis_inputs=capture,
    )
    controller.run_analysis()
    _wait_until(app, lambda: bool(applied))
    saved = saved_inputs_from_record(applied[0])
    assert saved is not None
    assert saved_inputs_from_record(panel.analysis["ihc_measurements"][0]) == saved
    assert captures and saved == captures[0]
    assert saved["settings"]["sensitivity"] == "medium"
    assert result_freshness(saved, build_analysis_inputs(
        method="ihc",
        settings={"mode": "foolproof", "sensitivity": "high"},
        image_uid="image-1",
        roi={"mode": "whole_image", "items": []},
    )) == "stale"
    from core.models.project import Project
    project = Project(title="freshness", panels=[panel])
    restored = Project.from_dict(project.to_dict())
    restored_panel = restored.panels[0]
    assert saved_inputs_from_record(restored_panel.analysis["ihc_measurements"][0]) == saved
    service.shutdown()


def _close_window(app, window) -> None:
    window._clean_project_fingerprint = window._project_fingerprint()
    window.close()
    window.deleteLater()
    app.processEvents()


def _stub_ihc_image(window) -> None:
    panel = window._active_ihc_panel()
    panel.data["ihc_images"] = [{
        "uid": "image-1", "source_name": "a.png", "measurements": [],
        "roi": {"mode": "whole_image", "items": []},
        "measurement_scale_bar": {"um_per_px_x": 0.5, "um_per_px_y": 0.5, "calibration_source": "manual"},
    }]
    panel.data["active_image_uid"] = "image-1"
    return panel


def test_expert_weak_threshold_and_row_select_refresh_via_signals(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        window.data_panel.set_ihc_guided_mode("expert")
        panel = _stub_ihc_image(window)
        current = window._current_analysis_inputs("ihc")
        first = {"measurement_uuid": "row-a", "image_uid": "image-1", "results": {"positive_area_percent": 1}}
        second = {"measurement_uuid": "row-b", "image_uid": "image-1", "results": {"positive_area_percent": 2}}
        attach_analysis_inputs(first, current)
        attach_analysis_inputs(second, build_analysis_inputs(
            method="ihc",
            settings={**dict(current["settings"]), "weak_threshold": 0.99},
            image_uid="image-1",
            roi=current.get("roi"),
            calibration=current.get("calibration"),
        ))
        panel.analysis["ihc_measurements"] = [first, second]
        window.data_panel.populate_ihc_measurements([first, second])
        window.data_panel.select_ihc_measurement("row-a")
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_current")
        window.data_panel.select_ihc_measurement("row-b")
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_stale")
        window.data_panel.select_ihc_measurement("row-a")
        window.data_panel.ihc_weak_threshold_spin.setValue(0.11)
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_stale")
    finally:
        _close_window(app, window)


def test_he_splitting_slider_and_roi_change_refresh_via_signals(monkeypatch):
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_refresh_brightfield_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("brightfield")
        panel = window._active_brightfield_panel()
        panel.data["brightfield_images"] = [{
            "uid": "image-1", "source_name": "a.png", "stain_type": "H&E", "measurements": [],
            "roi": {"mode": "whole_image", "items": []},
            "measurement_scale_bar": {"um_per_px_x": 0.5, "um_per_px_y": 0.5, "calibration_source": "manual"},
        }]
        panel.data["active_image_uid"] = "image-1"
        window.data_panel.brightfield_measure_type_combo.setCurrentIndex(
            max(0, window.data_panel.brightfield_measure_type_combo.findData("fiber"))
        )
        fiber = {"measurement_uuid": "fiber-1", "image_uid": "image-1", "results": {"ai_object_count": 1}}
        attach_analysis_inputs(fiber, window._current_analysis_inputs("brightfield_fiber"))
        panel.analysis["brightfield_ai_object_count_measurements"] = [fiber]
        window.data_panel.populate_brightfield_ai_measurements([fiber], selected_measurement_uuid="fiber-1")
        window._refresh_result_freshness_status()
        assert window.data_panel.brightfield_ai_freshness_status_label.text() == tr("result_status_current")
        window.data_panel.brightfield_he_fiber_splitting_slider.setValue(80)
        assert window.data_panel.brightfield_ai_freshness_status_label.text() == tr("result_status_stale")

        window.set_module("ihc")
        ihc = _stub_ihc_image(window)
        record = {"measurement_uuid": "ihc-roi", "image_uid": "image-1", "results": {}}
        attach_analysis_inputs(record, window._current_analysis_inputs("ihc"))
        ihc.analysis["ihc_measurements"] = [record]
        window.data_panel.populate_ihc_measurements([record])
        window.data_panel.select_ihc_measurement("ihc-roi")
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_current")
        window.set_ihc_roi_from_canvas(QRect(2, 3, 8, 9))
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_stale")
        attach_analysis_inputs(record, window._current_analysis_inputs("ihc"))
        window.data_panel.select_ihc_measurement("ihc-roi")
        window.ihc_workspace.calibration_controls.x_spin.setValue(0.2)
        window.ihc_workspace.calibration_controls.y_spin.setValue(0.2)
        panel_scale = ihc.measurement_scale_bar("image-1")
        ihc.update_measurement_scale_bar("image-1", um_per_px_x=0.2, um_per_px_y=0.2)
        window.ihc_workspace.calibration_controls.calibration_applied.emit(dict(panel_scale, um_per_px_x=0.2, um_per_px_y=0.2))
        assert window.data_panel.ihc_freshness_status_label.text() == tr("result_status_stale")
    finally:
        _close_window(app, window)


def test_controller_failure_shows_failed_and_prior_freshness(monkeypatch):
    from core.models.ihc_panel import IHCPanel
    from PyQt6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    def boom(self, settings=None, guided_mode="foolproof"):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(IHCPanel, "auto_analyze_dab", boom)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_image(window)
        prior = {"measurement_uuid": "prior", "image_uid": "image-1", "results": {"positive_area_percent": 1}}
        attach_analysis_inputs(prior, window._current_analysis_inputs("ihc"))
        panel.analysis["ihc_measurements"] = [prior]
        window.data_panel.populate_ihc_measurements([prior])
        window.data_panel.select_ihc_measurement("prior")
        window.ihc_analysis_controller.run_analysis()
        from tests.test_analysis_async import _wait_until
        _wait_until(app, lambda: tr("result_status_failed") in window.data_panel.ihc_freshness_status_label.text())
        text = window.data_panel.ihc_freshness_status_label.text()
        assert tr("result_status_failed") in text
        assert tr("result_status_current") in text
        assert panel.analysis["ihc_measurements"] == [prior]
    finally:
        _close_window(app, window)


def test_apply_snapshot_keeps_live_overlay_opacity(monkeypatch):
    import threading
    from core.models.ihc_panel import IHCPanel
    from PyQt6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow
    from tests.test_analysis_async import _wait_until

    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    release = threading.Event()

    def slow_ihc(self, settings=None, guided_mode="foolproof"):
        release.wait(2.0)
        measurement = {"measurement_uuid": "applied", "image_uid": "image-1", "results": {"positive": 3}}
        self.analysis["ihc_measurements"] = [measurement]
        return measurement

    monkeypatch.setattr(IHCPanel, "auto_analyze_dab", slow_ihc)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_image(window)
        window.data_panel.set_ihc_overlay_opacity(0.35)
        window.ihc_analysis_controller.run_analysis()
        window.data_panel.set_ihc_overlay_opacity(0.8)
        live_opacity = window.data_panel.ihc_overlay_opacity()
        release.set()
        _wait_until(app, lambda: bool(panel.analysis.get("ihc_measurements")))
        assert panel.analysis["ihc_measurements"][0]["measurement_uuid"] == "applied"
        assert window.data_panel.ihc_overlay_opacity() == live_opacity
        guided = dict(panel.data.get("guided_state", {}))
        assert abs(float(guided.get("overlay_opacity", 0)) - live_opacity) < 1e-6
    finally:
        release.set()
        _close_window(app, window)
