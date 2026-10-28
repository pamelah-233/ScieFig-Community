from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.measurement_freshness import analysis_job_fingerprint
from core.models.project import Project
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


def _write_png(path: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 40, 40))
    assert image.save(str(path))
    return path


def _stub_ihc_images(window, source_paths=None):
    panel = window._active_ihc_panel()
    panel.data["ihc_images"] = [
        {"uid": "image-a", "source_name": "a.png", "measurements": [], "roi": {"mode": "whole_image", "items": []}},
        {"uid": "image-b", "source_name": "b.png", "measurements": [], "roi": {"mode": "whole_image", "items": []}},
    ]
    if source_paths:
        for image, path in zip(panel.data["ihc_images"], source_paths):
            image["source_path"] = str(path)
    panel.data["active_image_uid"] = "image-a"
    window.data_panel.populate_ihc_import_controls(panel.images(), "image-a")
    return panel


def _stub_bf_images(window) -> None:
    panel = window._active_brightfield_panel()
    panel.data["brightfield_images"] = [
        {"uid": "image-a", "source_name": "a.png", "stain_type": "H&E", "group": "G1", "measurements": [], "roi": {"mode": "whole_image", "items": []}},
        {"uid": "image-b", "source_name": "b.png", "stain_type": "H&E", "group": "G1", "measurements": [], "roi": {"mode": "whole_image", "items": []}},
    ]
    panel.data["active_image_uid"] = "image-a"
    window.data_panel.populate_brightfield_import_controls(panel.images(), "image-a")
    return panel


def test_ihc_image_a_b_a_and_method_switch_restore_widgets(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_images(window)
        window.select_ihc_image("image-a")
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.0, "weak_threshold": 0.11, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        window.select_ihc_image("image-b")
        window.data_panel.set_ihc_guided_mode("assisted")
        window.data_panel.set_ihc_guided_analysis_settings(
            {"sensitivity": "high", "cleanup_level": "low", "tissue_background_correction": False}
        )
        window.select_ihc_image("image-a")
        assert window.data_panel.current_ihc_guided_mode() == "expert"
        restored = window.data_panel.ihc_analysis_settings()
        assert restored["positive_threshold"] == 0.0
        assert restored["weak_threshold"] == 0.11
        assert restored["tissue_mask_enabled"] is False
        window.select_ihc_image("image-b")
        assert window.data_panel.current_ihc_guided_mode() == "assisted"
        assert window.data_panel.ihc_guided_analysis_settings()["sensitivity"] == "high"
        assert window.data_panel.ihc_guided_analysis_settings()["tissue_background_correction"] is False
        state = panel.data.get("analysis_ui_state_by_image")
        assert "image-a" in state and "image-b" in state
    finally:
        _close_window(app, window)


def test_brightfield_he_and_cells_image_method_switch(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_brightfield_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("brightfield")
        _stub_bf_images(window)
        window.select_brightfield_image("image-a")
        combo = window.data_panel.brightfield_measure_type_combo
        combo.setCurrentIndex(combo.findData("fiber"))
        window.data_panel.set_brightfield_he_fiber_analysis_settings(
            {"he_fiber_segmentation_method": "anti_oversegmentation", "he_fiber_splitting_sensitivity": 0, "he_fiber_foreground_sensitivity": 35}
        )
        window.select_brightfield_image("image-b")
        combo.setCurrentIndex(combo.findData("cells"))
        window.data_panel.set_cell_analysis_settings({"backend": "cellpose", "residual_threshold": 0.0, "min_circularity": 0.05})
        window.select_brightfield_image("image-a")
        assert combo.currentData() == "fiber"
        fiber = window.data_panel.brightfield_analysis_settings()
        assert fiber["he_fiber_segmentation_method"] == "anti_oversegmentation"
        assert fiber["he_fiber_splitting_sensitivity"] == 0
        window.select_brightfield_image("image-b")
        assert combo.currentData() == "cells"
        cells = window.data_panel.bright_round_cell_analysis_settings()
        assert cells["backend"] == "cellpose"
        assert cells["residual_threshold"] == 0.0
    finally:
        _close_window(app, window)


def test_fluoro_count_algorithm_id_survives_language_and_image_switch(monkeypatch):
    from tests.test_batch_analysis import fluorescence_panel
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    panel = fluorescence_panel()
    try:
        window.set_module("fluoro")
        workspace = window.fluoro_workspace
        workspace.set_panel(panel)
        workspace.set_step(2)
        workspace.active_image_combo.setCurrentIndex(workspace.active_image_combo.findData("image-0"))
        workspace._set_measure_mode("spot_count")
        workspace.set_count_analysis_settings({"algorithm": "log", "diameter_px": 12, "sensitivity": 0.5, "method": "otsu", "min_area_px": 3})
        workspace.active_image_combo.setCurrentIndex(workspace.active_image_combo.findData("image-1"))
        workspace._set_measure_mode("ai_assisted")
        workspace.set_ai_analysis_settings({"detection_sensitivity": 2.0, "min_object_area_px": 1,
            "allow_restricted_model": False, 'backend': 'cellpose_cyto3', 'cellpose_diameter_px': 55})
        workspace.active_image_combo.setCurrentIndex(workspace.active_image_combo.findData("image-0"))
        assert workspace.measure_mode == "spot_count"
        count = workspace.count_analysis_settings()
        assert count["algorithm"] == "log"
        set_language("zh")
        window.switch_language("zh")
        assert workspace.count_analysis_settings()["algorithm"] == "log"
        assert workspace.count_analysis_settings()["algorithm"] != tr("spot_method_log")
        workspace.active_image_combo.setCurrentIndex(workspace.active_image_combo.findData("image-1"))
        assert workspace.measure_mode == "ai_assisted"
        assert workspace.ai_analysis_settings()["allow_restricted_model"] is False
        assert workspace.ai_analysis_settings()['backend'] == 'cellpose_cyto3'
        assert workspace.ai_analysis_settings()['cellpose_diameter_px'] == 55
        set_language("en")
        window.switch_language("en")
    finally:
        panel.dispose()
        _close_window(app, window)


def test_project_roundtrip_restores_module_step_image_overlay_and_selection(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(window.ihc_workspace.canvas, "bind_preview_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_images(window)
        window.data_panel.set_ihc_step(1, emit_signal=False)
        panel.data["active_step"] = 1
        window.select_ihc_image("image-b")
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.0, "weak_threshold": 0.2, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        measurement = {"measurement_uuid": "row-b", "image_uid": "image-b", "results": {"positive_area_percent": 3}}
        panel.analysis["ihc_measurements"] = [measurement]
        window.data_panel.populate_ihc_measurements([measurement])
        window.data_panel.select_ihc_measurement("row-b")
        original = QPixmap(8, 8)
        original.fill()
        overlay = QPixmap(8, 8)
        overlay.fill()
        canvas = window.ihc_workspace.canvas
        canvas._original_pixmap = original
        canvas._overlay_pixmap = overlay
        window.ihc_workspace.show_overlay_preview()
        assert window.ihc_workspace.current_preview_mode() == "overlay"
        overlay_requests = []
        monkeypatch.setattr(window.ihc_workspace, "show_overlay_preview", lambda: overlay_requests.append("overlay"))
        window.analysis_state.capture_outgoing("ihc")
        window.analysis_state.capture_session()
        payload = window.project.to_dict()
        assert "analysis_ui_state_by_image" in payload["panels"][4]["data"] or "analysis_ui_state_by_image" in panel.data
        restored_project = Project.from_dict(deepcopy(payload))
        assert restored_project.session_ui_state["preview_mode_by_module"]["ihc"] == "overlay"
        window.project = restored_project
        window.analysis_state.restore_session()
        assert window.active_module == "ihc"
        restored_panel = window._active_ihc_panel()
        assert int(restored_panel.data.get("active_step", 0)) == 1
        assert restored_panel.data.get("active_image_uid") == "image-b"
        assert window.data_panel.current_ihc_guided_mode() == "expert"
        assert window.data_panel.ihc_analysis_settings()["positive_threshold"] == 0.0
        assert window.data_panel.selected_ihc_measurement_uuid() == "row-b"
        assert overlay_requests == ["overlay"]
    finally:
        _close_window(app, window)


def test_table_to_image_locate_still_works_after_ui_restore(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_images(window)
        window.select_ihc_image("image-a")
        row_b = {"measurement_uuid": "from-b", "image_uid": "image-b", "results": {}}
        panel.analysis["ihc_measurements"] = [row_b]
        window.data_panel.populate_ihc_measurements([row_b])
        window.data_panel.select_ihc_measurement("from-b")
        assert panel.data["active_image_uid"] == "image-b"
        assert window.data_panel.selected_ihc_measurement_uuid() == "from-b"
    finally:
        _close_window(app, window)


def test_ui_state_write_does_not_change_fingerprint_but_actual_params_do(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_images(window)
        fp = analysis_job_fingerprint(panel)
        window.analysis_state.capture_outgoing("ihc")
        assert "analysis_ui_state_by_image" in panel.data
        assert analysis_job_fingerprint(panel) == fp
        settings = dict(panel.data.get("analysis_settings") or {})
        settings["positive_threshold"] = 0.01
        panel.data["analysis_settings"] = settings
        assert analysis_job_fingerprint(panel) != fp
    finally:
        _close_window(app, window)


def test_method_switch_preserves_other_method_settings(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        _stub_ihc_images(window)
        window.select_ihc_image("image-a")
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.0, "weak_threshold": 0.11, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        window.data_panel.set_ihc_guided_mode("assisted")
        window.data_panel.set_ihc_guided_analysis_settings({"sensitivity": "high", "cleanup_level": "low", "tissue_background_correction": False})
        window.data_panel.set_ihc_guided_mode("expert")
        restored = window.data_panel.ihc_analysis_settings()
        assert restored["weak_threshold"] == 0.11
        assert restored["positive_threshold"] == 0.0
        window.data_panel.set_ihc_guided_mode("assisted")
        assert window.data_panel.ihc_guided_analysis_settings()["sensitivity"] == "high"
        assert window.data_panel.ihc_guided_analysis_settings()["tissue_background_correction"] is False
    finally:
        _close_window(app, window)


def test_leaving_module_saves_pending_edits_before_capture_session(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_refresh_brightfield_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_images(window)
        window.select_ihc_image("image-a")
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.0, "weak_threshold": 0.11, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        window.set_module("brightfield")
        window.analysis_state.capture_session()
        assert window.project.session_ui_state["active_module"] == "brightfield"
        assert panel.data["analysis_ui_state_by_image"]["image-a"]["expert"]["weak_threshold"] == 0.11
    finally:
        _close_window(app, window)


def test_open_restore_does_not_capture_old_widgets_into_loaded_project(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_images(window)
        window.select_ihc_image("image-a")
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.11, "weak_threshold": 0.2, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        window.analysis_state.capture_outgoing("ihc")
        window.analysis_state.capture_session()
        payload = deepcopy(window.project.to_dict())
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.99, "weak_threshold": 0.99, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        window.project = Project.from_dict(payload)
        window.analysis_state.restore_session()
        restored = window._active_ihc_panel()
        assert restored.data["analysis_ui_state_by_image"]["image-a"]["expert"]["positive_threshold"] == 0.11
        assert window.data_panel.ihc_analysis_settings()["positive_threshold"] == 0.11
        assert restored is not panel
    finally:
        _close_window(app, window)


def test_postanalysis_snapshot_keeps_per_image_params_across_image_switch(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("ihc")
        panel = _stub_ihc_images(window)
        window.select_ihc_image("image-a")
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.0, "weak_threshold": 0.11, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        window.analysis_state.capture_outgoing("ihc")
        stored = deepcopy(panel.data["analysis_ui_state_by_image"])
        result_panel = type(panel).from_dict(deepcopy(panel.to_dict()))
        result_panel.data["analysis_ui_state_by_image"] = {}
        window._apply_analysis_panel_snapshot(panel, result_panel)
        assert panel.data["analysis_ui_state_by_image"] == stored
        window.select_ihc_image("image-b")
        window.data_panel.set_ihc_guided_mode("assisted")
        window.select_ihc_image("image-a")
        assert window.data_panel.current_ihc_guided_mode() == "expert"
        assert window.data_panel.ihc_analysis_settings()["weak_threshold"] == 0.11
        assert window.data_panel.ihc_analysis_settings()["positive_threshold"] == 0.0
    finally:
        _close_window(app, window)


def test_brightfield_ai_restore_does_not_use_select_truthiness(monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_brightfield_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        window.set_module("brightfield")
        panel = _stub_bf_images(window)
        combo = window.data_panel.brightfield_measure_type_combo
        combo.setCurrentIndex(combo.findData("fiber"))
        fiber = {
            "measurement_uuid": "fiber-row",
            "image_uid": "image-a",
            "image_name": "a.png",
            "group": "G1",
            "stain_type": "H&E",
            "results": {"ai_object_count": 3, "ai_object_mean_area_px": 1.5},
        }
        panel.analysis["brightfield_ai_object_count_measurements"] = [fiber]
        window._refresh_brightfield_ai_views(panel)
        assert window.data_panel.select_brightfield_measurement("fiber-row") is False
        assert window.data_panel.select_brightfield_ai_measurement("fiber-row") is True
        overlay = []
        monkeypatch.setattr(window.brightfield_workspace.canvas, "show_overlay_preview", lambda: overlay.append("overlay"))
        window.analysis_state.capture_session()
        window.data_panel.brightfield_ai_results_table.clearSelection()
        window.data_panel.brightfield_ai_results_table.setCurrentItem(None)
        original = window.data_panel.select_brightfield_measurement

        def returns_none(uuid):
            original(uuid)
            return None

        monkeypatch.setattr(window.data_panel, "select_brightfield_measurement", returns_none)
        window.analysis_state.restore_session()
        assert window.data_panel.selected_brightfield_ai_measurement_uuid() == "fiber-row"
        assert "overlay" in overlay
        assert panel.data.get("active_image_uid") == "image-a"
    finally:
        _close_window(app, window)


def test_fluoro_restore_selects_count_and_shows_overlay_on_correct_image(monkeypatch):
    from tests.test_batch_analysis import fluorescence_panel
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    panel = fluorescence_panel()
    count = {
        "count_uuid": "count-0",
        "image_uid": "image-0",
        "image_name": "sample-0.tif",
        "channel_index": 0,
        "count": 2,
        "detected_spots": [{"x": 1, "y": 2, "w": 3, "h": 4}],
    }
    panel.analysis.setdefault("object_counts", []).append(count)
    panel.images()[0].setdefault("object_counts", []).append(count)
    shown = []
    try:
        for index, item in enumerate(window.project.panels):
            if getattr(item, "panel_type", "") == "fluorescence":
                window.project.panels[index] = panel
                break
        window.set_module("fluoro")
        workspace = window.fluoro_workspace
        workspace.set_step(2)
        workspace._set_measure_mode("spot_count")
        monkeypatch.setattr(workspace.canvas, "show_count_overlay", lambda result: shown.append((result.get("count_uuid"), result.get("image_uid"))))
        monkeypatch.setattr(workspace.canvas, "show_ai_assisted_overlay", lambda records: shown.append(("ai", records)))
        monkeypatch.setattr(workspace, "_set_ai_overlay_visible", lambda visible, **_kwargs: shown.append(("ai-button", visible)))
        assert workspace.select_result_identity("count_uuid", "count-0") is True
        assert workspace.selected_result_identity() == ("count_uuid", "count-0")
        assert panel.data.get("active_image_uid") == "image-0"
        window.analysis_state.capture_outgoing("fluorescence")
        window.analysis_state.capture_session()
        assert window.project.session_ui_state["selected_record_by_module"]["fluoro"] == {"kind": "count_uuid", "uuid": "count-0"}
        workspace.active_image_combo.setCurrentIndex(workspace.active_image_combo.findData("image-1"))
        shown.clear()
        window.analysis_state.restore_session()
        assert window.active_module == "fluoro"
        assert window._active_fluoro_panel().data.get("active_image_uid") == "image-0"
        assert workspace.selected_result_identity() == ("count_uuid", "count-0")
        assert ("count-0", "image-0") in shown
        assert ("ai-button", True) not in shown
    finally:
        panel.dispose()
        _close_window(app, window)


def test_sfig_save_load_restores_params_selection_ids_without_reanalysis(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(window.ihc_workspace.canvas, "bind_preview_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    runs = []
    monkeypatch.setattr(window, "run_ihc_analysis", lambda *args, **kwargs: runs.append(True))
    try:
        window.set_module("ihc")
        paths = [_write_png(tmp_path / "a.png"), _write_png(tmp_path / "b.png")]
        panel = _stub_ihc_images(window, source_paths=paths)
        window.data_panel.set_ihc_step(1, emit_signal=False)
        panel.data["active_step"] = 1
        window.select_ihc_image("image-b")
        window.data_panel.set_ihc_guided_mode("expert")
        window.data_panel.set_ihc_analysis_settings(
            {"positive_threshold": 0.0, "weak_threshold": 0.2, "moderate_threshold": 0.5, "strong_threshold": 0.8, "min_tissue_area_px": 0, "tissue_mask_enabled": False}
        )
        measurement = {"measurement_uuid": "row-b", "image_uid": "image-b", "results": {"positive_area_percent": 3}}
        panel.analysis["ihc_measurements"] = [measurement]
        window.data_panel.populate_ihc_measurements([measurement])
        window.data_panel.select_ihc_measurement("row-b")
        original = QPixmap(8, 8)
        original.fill()
        overlay = QPixmap(8, 8)
        overlay.fill()
        canvas = window.ihc_workspace.canvas
        canvas._original_pixmap = original
        canvas._overlay_pixmap = overlay
        window.ihc_workspace.show_overlay_preview()
        overlay_requests = []
        monkeypatch.setattr(window.ihc_workspace, "show_overlay_preview", lambda: overlay_requests.append("overlay"))
        window.analysis_state.capture_outgoing("ihc")
        window.analysis_state.capture_session()
        target = tmp_path / "stage2_restore.sfig"
        window.project.save_sfig(target)
        loaded = Project.load_sfig(target)
        assert loaded.session_ui_state["selected_record_by_module"]["ihc"] == "row-b"
        loaded_panel = next(item for item in loaded.panels if getattr(item, "panel_type", "") == "ihc")
        assert loaded_panel.analysis["ihc_measurements"][0]["measurement_uuid"] == "row-b"
        assert loaded_panel.analysis["ihc_measurements"][0]["results"] == {"positive_area_percent": 3}
        assert loaded_panel.data["analysis_ui_state_by_image"]["image-b"]["expert"]["positive_threshold"] == 0.0
        window.project = loaded
        window.analysis_state.restore_session()
        assert window.active_module == "ihc"
        assert window.data_panel.current_ihc_guided_mode() == "expert"
        assert window.data_panel.ihc_analysis_settings()["positive_threshold"] == 0.0
        assert window.data_panel.selected_ihc_measurement_uuid() == "row-b"
        assert overlay_requests == ["overlay"]
        assert runs == []
    finally:
        _close_window(app, window)
