from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core.models.project import Project
from core.project_recovery import ProjectRecoveryStore, RecoveryDraftInfo
from core.utils.i18n import set_language, tr
from ui.controllers.project_recovery_controller import ProjectRecoveryController, ProjectRecoveryDialog


_QT_APP = None
ROOT = Path(__file__).resolve().parents[1]


def _app() -> QApplication:
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def _close_window(app, window) -> None:
    recovery = getattr(window, "project_recovery", None)
    if recovery is not None:
        recovery._alive = False
        recovery.timer.stop()
    window._clean_project_fingerprint = window._project_fingerprint()
    window.close()
    window.deleteLater()
    app.processEvents()


def _attach_recovery(window, tmp_path: Path, interval_ms: int = 60_000) -> ProjectRecoveryController:
    previous = getattr(window, "project_recovery", None)
    if previous is not None:
        previous._alive = False
        previous.timer.stop()
    store = ProjectRecoveryStore(tmp_path / "recovery", str(uuid4()), os.getpid())
    controller = ProjectRecoveryController(window, store=store, interval_ms=interval_ms)
    window.project_recovery = controller
    return controller


def _wait_until(app, predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _wait_idle(app, controller: ProjectRecoveryController, extra=None, timeout: float = 8.0) -> bool:
    def ready():
        if controller._write_active:
            return False
        return extra() if extra is not None else True

    return _wait_until(app, ready, timeout)


def _dirty(window, title: str = "Dirty recovery") -> None:
    window.project.title = title


def _write_png(path: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 40, 40))
    assert image.save(str(path))
    return path


def _draft_info(title: str, suffix: str) -> RecoveryDraftInfo:
    return RecoveryDraftInfo(
        path=Path(f"{suffix}.sfig"),
        session_id=f"session-{suffix}",
        project_uuid=f"project-{suffix}",
        owner_pid=1,
        title=title,
        modified_at="2026-09-09 12:00",
        panel_ids=["Figure1C"],
        panel_uuids=[f"uuid-{suffix}"],
        recoverable=True,
        is_own_session=False,
        owner_alive=False,
    )


def test_recovery_dialog_requires_explicit_choice_when_multiple():
    app = _app()
    set_language("zh")
    dialog = ProjectRecoveryDialog([_draft_info("荧光草稿", "a"), _draft_info("IHC 草稿", "b")])
    assert dialog.draft_list.isHidden() is False
    assert dialog.draft_list.count() == 2
    assert dialog.draft_list.currentRow() == 0
    dialog.draft_list.setCurrentRow(1)
    dialog._finish("restore")
    assert dialog.choice == "restore"
    assert dialog.selected.title == "IHC 草稿"
    single = ProjectRecoveryDialog([_draft_info("仅一份", "c")])
    assert single.draft_list.isHidden() is True
    assert single.selected.title == "仅一份"


def test_timer_and_background_job_write_dirty_project(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path, interval_ms=30)
    try:
        _dirty(window, "Timer Draft")
        assert window.is_project_dirty()
        assert _wait_until(app, lambda: bool(controller.store.list_drafts()))
        assert _wait_idle(app, controller)
        drafts = controller.store.list_drafts()
        assert len(drafts) == 1
        assert drafts[0].title == "Timer Draft"
        assert window._clean_project_fingerprint != window._project_fingerprint()
    finally:
        _close_window(app, window)


def test_deferred_busy_then_writes_when_idle(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    try:
        _dirty(window, "Busy Draft")
        window._analysis_job_service._busy_keys.add("ihc")
        controller.maybe_autosave()
        app.processEvents()
        assert controller.store.list_drafts() == []
        window._analysis_job_service._busy_keys.clear()
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        assert controller.store.list_drafts()[0].title == "Busy Draft"
    finally:
        _close_window(app, window)


def test_clean_project_is_noop(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    try:
        assert window.is_project_dirty() is False
        controller.maybe_autosave()
        app.processEvents()
        assert controller.store.list_drafts() == []
        assert window.is_project_dirty() is False
    finally:
        _close_window(app, window)


def test_failed_write_keeps_previous_draft_and_shows_status(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    try:
        _dirty(window, "First Draft")
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        draft_path = controller.store.list_drafts()[0].path
        original = draft_path.read_bytes()
        baseline = window._clean_project_fingerprint

        def boom(_snapshot):
            raise OSError("injected recovery write failure")

        monkeypatch.setattr(controller.store, "write_snapshot", boom)
        window.project.title = "Second Draft"
        controller._last_written_fingerprint = None
        controller.maybe_autosave()
        assert _wait_idle(app, controller)
        assert draft_path.read_bytes() == original
        assert window.statusBar().currentMessage() == tr("recovery_write_failed")
        assert window._clean_project_fingerprint == baseline
    finally:
        _close_window(app, window)


def test_startup_restore_discard_later(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    try:
        _dirty(window, "Recover Me")
        window.project.session_ui_state = {
            "selected_record_by_module": {"fluoro": {"kind": "count_uuid", "uuid": "count-1"}}
        }
        window.project.browser_workspace_state = {"zoom": 2.5, "length_um": 10}
        controller._capture_live_session = lambda: None
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        draft = controller.store.list_drafts()[0]
        empty = window._create_default_project()
        window._dispose_project_transient_caches(window.project)
        window.project = empty
        window._capture_clean_project_baseline()
        controller._startup_handled = False
        controller._choose_recovery = lambda drafts: ("later", drafts[0])
        controller.prompt_startup_recovery()
        assert draft.path.is_file()
        assert window.project.title != "Recover Me"
        calls = []
        controller._choose_recovery = lambda drafts: calls.append(drafts) or ("restore", drafts[0])
        controller.prompt_startup_recovery()
        assert calls == []
        controller._startup_handled = False
        controller._choose_recovery = lambda drafts: ("restore", drafts[0])
        controller.prompt_startup_recovery()
        assert window.project.title == "Recover Me"
        assert window.is_project_dirty()
        assert window.project.session_ui_state["selected_record_by_module"]["fluoro"]["uuid"] == "count-1"
        assert window.project.browser_workspace_state["zoom"] == 2.5
        assert draft.path.is_file()
        controller._startup_handled = False
        controller._choose_recovery = lambda drafts: ("discard", drafts[0])
        controller.prompt_startup_recovery()
        assert not draft.path.is_file()
    finally:
        _close_window(app, window)


def test_cancel_close_preserves_work_and_draft(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    try:
        _dirty(window, "Stay Open")
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        path = controller.store.list_drafts()[0].path
        monkeypatch.setattr(window, "_confirm_destructive_action", lambda action: False)
        window.close()
        app.processEvents()
        assert path.is_file()
        assert window.project.title == "Stay Open"
        assert controller._alive is True
    finally:
        monkeypatch.setattr(window, "_confirm_destructive_action", lambda action: True)
        _close_window(app, window)


def test_new_and_open_success_vs_cancel(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    try:
        _dirty(window, "Keep On Cancel")
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        path = controller.store.list_drafts()[0].path
        old_uuid = window.project.project_uuid
        monkeypatch.setattr(window, "_confirm_destructive_action", lambda action: False)
        window.new_project()
        assert window.project.project_uuid == old_uuid
        assert path.is_file()
        monkeypatch.setattr(window, "_confirm_destructive_action", lambda action: True)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("", ""))
        window.open_project()
        assert window.project.title == "Keep On Cancel"
        assert path.is_file()
        opened = tmp_path / "opened.sfig"
        Project(title="Opened File").save_sfig(opened)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(opened), ""))
        window.open_project()
        assert _wait_idle(app, controller)
        assert window.project.title == "Opened File"
        assert not path.is_file()
        _dirty(window, "Before New")
        controller._last_written_fingerprint = None
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: any(item.title == "Before New" for item in controller.store.list_drafts()))
        new_path = next(item.path for item in controller.store.list_drafts() if item.title == "Before New")
        window.new_project()
        assert _wait_idle(app, controller)
        assert not new_path.is_file()
        assert window.project.title != "Before New"
    finally:
        _close_window(app, window)


def test_inflight_save_and_close_do_not_keep_stale_draft(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = controller.store.write_snapshot

    def blocked(snapshot):
        started.set()
        if not release.wait(5):
            raise TimeoutError("write was not released")
        return original(snapshot)

    monkeypatch.setattr(controller.store, "write_snapshot", blocked)
    try:
        _dirty(window, "In Flight")
        old_uuid = window.project.project_uuid
        controller.maybe_autosave()
        assert started.wait(3)
        monkeypatch.setattr(window, "_confirm_destructive_action", lambda action: True)
        window.new_project()
        release.set()
        assert _wait_idle(app, controller)
        assert all(item.project_uuid != old_uuid for item in controller.store.list_drafts())
    finally:
        release.set()
        _close_window(app, window)


def test_native_array_roundtrip_and_selection_scale_state(tmp_path, monkeypatch):
    from tests.test_project_recovery import _sourceless_fluoro_project
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    project, pixels, image_uid = _sourceless_fluoro_project()
    project.session_ui_state = {
        "active_module": "fluoro",
        "selected_record_by_module": {"fluoro": {"kind": "count_uuid", "uuid": "count-1"}},
    }
    project.browser_workspace_state = {"zoom": 1.25, "scale": {"length_um": 20}}
    try:
        window._dispose_project_transient_caches(window.project)
        window.project = project
        controller._capture_live_session = lambda: None
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        restored = controller.store.load_draft(controller.store.list_drafts()[0])
        try:
            restored_pixels = restored.panels[0].image_data(image_uid)
            assert restored_pixels is not None
            assert np.array_equal(np.asarray(restored_pixels), pixels)
            assert restored.session_ui_state["selected_record_by_module"]["fluoro"]["uuid"] == "count-1"
            assert restored.browser_workspace_state["scale"]["length_um"] == 20
        finally:
            restored.dispose()
    finally:
        _close_window(app, window)


def test_recovery_status_strings_are_chinese():
    set_language("zh")
    keys = (
        "recovery_title",
        "recovery_message",
        "recovery_restore",
        "recovery_discard",
        "recovery_later",
        "recovery_write_failed",
        "recovery_capture_failed",
        "recovery_restore_failed",
        "recovery_discard_failed",
        "recovery_restore_blocked",
        "recovery_restored_status",
        "recovery_discarded_status",
    )
    for key in keys:
        text = tr(key)
        assert text != key
        assert any("\u4e00" <= character <= "\u9fff" for character in text)


def test_pending_widget_edit_is_captured_without_dirtying_untouched(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(window, "_refresh_ihc_preview", lambda *args, **kwargs: None)
    controller = _attach_recovery(window, tmp_path)
    set_language("zh")
    try:
        assert window.is_project_dirty() is False
        controller.maybe_autosave()
        app.processEvents()
        assert controller.store.list_drafts() == []
        assert window.is_project_dirty() is False

        window.set_module("ihc")
        panel = window._active_ihc_panel()
        source = _write_png(tmp_path / "a.png")
        panel.data["ihc_images"] = [
            {
                "uid": "image-a",
                "source_name": "a.png",
                "source_path": str(source),
                "measurements": [],
                "roi": {"mode": "whole_image", "items": []},
            }
        ]
        panel.data["active_image_uid"] = "image-a"
        window.data_panel.populate_ihc_import_controls(panel.images(), "image-a")
        window.select_ihc_image("image-a")
        window.data_panel.set_ihc_guided_mode("expert")
        window.analysis_state.capture_session()
        window._capture_clean_project_baseline()
        assert window.is_project_dirty() is False
        current = window.data_panel.ihc_analysis_settings()
        expected = 0.11 if float(current["weak_threshold"]) != 0.11 else 0.22
        window.data_panel.set_ihc_analysis_settings({**current, "weak_threshold": expected})
        assert window.data_panel.ihc_analysis_settings()["weak_threshold"] == expected
        assert window.is_project_dirty() is False
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        restored = controller.store.load_draft(controller.store.list_drafts()[0])
        try:
            restored_panel = next(item for item in restored.panels if getattr(item, "panel_type", "") == "ihc")
            assert restored_panel.data["analysis_ui_state_by_image"]["image-a"]["expert"]["weak_threshold"] == expected
        finally:
            restored.dispose()
    finally:
        _close_window(app, window)


def test_corrupt_restore_keeps_project_and_draft(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    set_language("zh")
    try:
        _dirty(window, "Live Work")
        live_uuid = window.project.project_uuid
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        draft = controller.store.list_drafts()[0]
        draft.path.write_bytes(b"not-a-valid-sfig")
        empty = window._create_default_project()
        window._dispose_project_transient_caches(window.project)
        window.project = empty
        window._capture_clean_project_baseline()
        current_title = window.project.title
        current_uuid = window.project.project_uuid
        controller._startup_handled = False
        controller._choose_recovery = lambda drafts: ("restore", drafts[0])
        controller.prompt_startup_recovery()
        app.processEvents()
        assert window.project.project_uuid == current_uuid
        assert window.project.title == current_title
        assert window.project.project_uuid != live_uuid
        assert draft.path.is_file()
        assert window.statusBar().currentMessage() == tr("recovery_restore_failed")
        assert any("\u4e00" <= character <= "\u9fff" for character in window.statusBar().currentMessage())
    finally:
        _close_window(app, window)


def test_capture_failure_shows_status_and_keeps_work(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    set_language("zh")
    try:
        _dirty(window, "Capture Fail")
        baseline = window._clean_project_fingerprint

        def boom(_project):
            raise ValueError("missing recovery asset")

        monkeypatch.setattr(controller.store, "capture_snapshot", boom)
        controller.maybe_autosave()
        app.processEvents()
        assert controller.store.list_drafts() == []
        assert window.project.title == "Capture Fail"
        assert window._clean_project_fingerprint == baseline
        assert window.is_project_dirty()
        assert window.statusBar().currentMessage() == tr("recovery_capture_failed")
        assert any("\u4e00" <= character <= "\u9fff" for character in window.statusBar().currentMessage())
    finally:
        _close_window(app, window)


def test_failed_discard_does_not_claim_success(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    set_language("zh")
    try:
        _dirty(window, "Keep Draft")
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        draft = controller.store.list_drafts()[0]

        def boom(_draft):
            raise OSError("injected discard failure")

        monkeypatch.setattr(controller.store, "discard_draft", boom)
        controller._startup_handled = False
        controller._choose_recovery = lambda drafts: ("discard", drafts[0])
        controller.prompt_startup_recovery()
        app.processEvents()
        assert draft.path.is_file()
        assert window.statusBar().currentMessage() == tr("recovery_discard_failed")
        assert window.statusBar().currentMessage() != tr("recovery_discarded_status")
        assert any("\u4e00" <= character <= "\u9fff" for character in window.statusBar().currentMessage())
    finally:
        _close_window(app, window)


def test_delayed_startup_does_not_replace_dirty_work(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    set_language("zh")
    try:
        _dirty(window, "Recover Me")
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        draft = controller.store.list_drafts()[0]
        empty = window._create_default_project()
        window._dispose_project_transient_caches(window.project)
        window.project = empty
        window._capture_clean_project_baseline()
        controller._startup_handled = False
        restored = []
        controller._choose_recovery = lambda drafts: restored.append(drafts) or ("restore", drafts[0])
        _dirty(window, "User Work")
        controller.schedule_startup_recovery()
        assert _wait_until(app, lambda: controller._startup_handled)
        assert restored == []
        assert window.project.title == "User Work"
        assert draft.path.is_file()
        assert window.statusBar().currentMessage() == tr("recovery_restore_blocked")
        assert any("\u4e00" <= character <= "\u9fff" for character in window.statusBar().currentMessage())
    finally:
        _close_window(app, window)


def test_restored_source_draft_kept_until_durable_write(tmp_path, monkeypatch):
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    set_language("zh")
    other = ProjectRecoveryStore(controller.store.root, str(uuid4()), 999999)
    source_project = Project(title="Crash Draft")
    snapshot = other.capture_snapshot(source_project)
    try:
        other.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
        source_project.dispose()
    try:
        source = next(item for item in controller.store.list_drafts() if item.session_id == other.session_id)
        assert source.path.is_file()
        controller._startup_handled = False
        controller._choose_recovery = lambda drafts: ("restore", next(item for item in drafts if item.session_id == other.session_id))
        controller.prompt_startup_recovery()
        assert window.project.title == "Crash Draft"
        assert source.path.is_file()
        assert window.is_project_dirty()
        original_write = controller.store.write_snapshot

        def boom(_snapshot):
            raise OSError("injected recovery write failure")

        monkeypatch.setattr(controller.store, "write_snapshot", boom)
        controller._last_written_fingerprint = None
        controller.maybe_autosave()
        assert _wait_idle(app, controller)
        assert source.path.is_file()
        assert window.statusBar().currentMessage() == tr("recovery_write_failed")
        monkeypatch.setattr(controller.store, "write_snapshot", original_write)
        controller._last_written_fingerprint = None
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: not source.path.is_file())
        assert not source.path.is_file()
        owned = [item for item in controller.store.list_drafts() if item.session_id == controller.store.session_id]
        assert len(owned) == 1
        assert owned[0].title == "Crash Draft"
    finally:
        _close_window(app, window)


def test_batch_is_running_defers_autosave(tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings

    from core.utils.calibration_presets import CalibrationPresets
    from tests.test_batch_analysis import fluorescence_panel
    from ui.dialogs.batch_analysis_dialog import BatchAnalysisDialog
    from ui.main_window import MainWindow

    app = _app()
    window = MainWindow(resources_dir=ROOT / "resources")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    controller = _attach_recovery(window, tmp_path)
    panel = fluorescence_panel()
    entered = threading.Event()
    release = threading.Event()

    def runner(snapshot, uid, method, options):
        entered.set()
        assert release.wait(5)
        return getattr(snapshot, method)(uid, **options)

    library = CalibrationPresets(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    dialog = BatchAnalysisDialog(
        panel,
        "count_channel_objects",
        {"channel_indices": [0]},
        parent=window,
        runner=runner,
        library=library,
    )
    try:
        _dirty(window, "Batch Busy")
        dialog.start_button.click()
        assert _wait_until(app, entered.is_set)
        assert dialog.is_running is True
        controller.maybe_autosave()
        app.processEvents()
        assert controller.store.list_drafts() == []
        release.set()
        assert _wait_until(app, lambda: not dialog.is_running)
        assert dialog.is_running is False
        controller.maybe_autosave()
        assert _wait_idle(app, controller, lambda: bool(controller.store.list_drafts()))
        assert controller.store.list_drafts()[0].title == "Batch Busy"
    finally:
        release.set()
        dialog.close()
        panel.dispose()
        _close_window(app, window)
