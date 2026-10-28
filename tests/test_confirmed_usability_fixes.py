"""Regression coverage for the four confirmed save/ROI/navigation defects."""
import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from core.models import Project
from ui.controllers.project_open_controller import open_project_path
from ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def window(monkeypatch):
    def unexpected_warning(*args):
        raise AssertionError(f"Unexpected warning: {args[1:]}")
    monkeypatch.setattr(QMessageBox, "warning", unexpected_warning)
    widget = MainWindow(resources_dir=ROOT / "resources")
    widget.project_recovery.timer.stop()
    widget.project_recovery._alive = False
    yield widget
    widget._capture_clean_project_baseline()
    widget.close()
    APP.processEvents()


def choose_path(monkeypatch, path):
    calls = []
    def choose(*args, **kwargs):
        calls.append(args)
        return str(path), "ScieFig Project (*.sfig)"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    return calls


def test_empty_external_placeholder_can_be_saved_again_after_load(window, tmp_path):
    path = tmp_path / "empty.sfig"
    window.project.save_sfig(path)
    restored = Project.load_sfig(path)
    try:
        restored.save_sfig(tmp_path / "resaved.sfig")
    finally:
        restored.dispose()


def test_existing_empty_placeholder_archive_can_be_resaved(window, tmp_path, monkeypatch):
    import core.sfig_io as sfig_io
    path = tmp_path / "legacy-empty.sfig"
    original_payload = sfig_io._archive_ready_project_payload
    def legacy_payload(project):
        payload = original_payload(project)
        placeholder = next(panel for panel in payload["panels"] if panel["panel_type"] == "external_image")
        generated = f"data/external/{placeholder['panel_uuid']}/external_asset"
        placeholder["data"].update(raw_copy_path=generated, embedded_copy_path=generated)
        return payload
    with monkeypatch.context() as patch:
        patch.setattr(sfig_io, "_archive_ready_project_payload", legacy_payload)
        window.project.save_sfig(path)
    restored = Project.load_sfig(path)
    try:
        restored.save_sfig(tmp_path / "resaved.sfig")
    finally:
        restored.dispose()


def test_save_reuses_current_path(window, tmp_path, monkeypatch):
    path = tmp_path / "实验 项目.sfig"
    calls = choose_path(monkeypatch, path)
    assert window.save_project()
    window.project.title = "Updated"
    assert window.save_project()
    restored = Project.load_sfig(path)
    try:
        assert len(calls) == 1
        assert restored.title == "Updated"
        assert not window.is_project_dirty()
    finally:
        restored.dispose()


@pytest.mark.parametrize("module", ["qpcr", "composer"])
def test_ctrl_s_saves_from_a_focused_child(window, tmp_path, monkeypatch, module):
    path = tmp_path / "shortcut.sfig"
    choose_path(monkeypatch, path)
    window.set_module(module)
    window.show()
    window.activateWindow()
    button = window.findChild(QPushButton, "V9SaveButton")
    button.setFocus()
    QTest.qWait(50)
    QTest.keyClick(button, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    APP.processEvents()
    assert path.is_file()


def test_save_as_switches_the_subsequent_save_target(window, tmp_path, monkeypatch):
    original, copied = tmp_path / "original.sfig", tmp_path / "copy.sfig"
    choose_path(monkeypatch, original)
    assert window.save_project()
    calls = choose_path(monkeypatch, copied)
    window.findChild(QPushButton, "V9SaveAsButton").click()
    window.project.title = "Only Copy Changes"
    assert window.save_project()
    first, second = Project.load_sfig(original), Project.load_sfig(copied)
    try:
        assert len(calls) == 1
        assert first.title != second.title
        assert second.title == "Only Copy Changes"
    finally:
        first.dispose()
        second.dispose()


def test_opened_project_saves_in_place(window, tmp_path, monkeypatch):
    path = tmp_path / "opened.sfig"
    assert window.project.save_sfig(path) is None
    assert open_project_path(window, path, confirm_unsaved=False)
    calls = choose_path(monkeypatch, tmp_path / "wrong.sfig")
    window.project.title = "Opened And Edited"
    assert window.save_project()
    restored = Project.load_sfig(path)
    try:
        assert calls == []
        assert restored.title == "Opened And Edited"
    finally:
        restored.dispose()


@pytest.mark.parametrize("failure", [False, True])
def test_canceled_or_failed_save_as_keeps_the_previous_target(window, tmp_path, monkeypatch, failure):
    original = tmp_path / "original.sfig"
    choose_path(monkeypatch, original)
    assert window.save_project()
    window.project.title = "Unsaved Edit"
    if failure:
        choose_path(monkeypatch, tmp_path / "missing" / "fail.sfig")
        def fail(*args, **kwargs):
            raise OSError("injected write failure")
        with monkeypatch.context() as patch:
            patch.setattr(window.project, "save_sfig", fail)
            patch.setattr(QMessageBox, "warning", lambda *a: None)
            window.findChild(QPushButton, "V9SaveAsButton").click()
    else:
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: ("", ""))
        window.findChild(QPushButton, "V9SaveAsButton").click()
    assert window.is_project_dirty()
    calls = choose_path(monkeypatch, tmp_path / "wrong.sfig")
    assert window.save_project()
    assert calls == []
    restored = Project.load_sfig(original)
    try:
        assert restored.title == "Unsaved Edit"
    finally:
        restored.dispose()


def test_new_project_does_not_overwrite_the_old_file(window, tmp_path, monkeypatch):
    choose_path(monkeypatch, tmp_path / "old.sfig")
    assert window.save_project()
    window.new_project()
    calls = choose_path(monkeypatch, tmp_path / "new.sfig")
    assert window.save_project()
    assert len(calls) == 1


def test_failed_open_preserves_the_save_target(window, tmp_path, monkeypatch):
    original = tmp_path / "original.sfig"
    choose_path(monkeypatch, original)
    assert window.save_project()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    assert not open_project_path(window, tmp_path / "missing.sfig", confirm_unsaved=False)
    calls = choose_path(monkeypatch, tmp_path / "wrong.sfig")
    window.project.title = "Still Original"
    assert window.save_project()
    assert calls == []


@pytest.mark.parametrize("saved", [False, True])
def test_visiting_empty_wb_does_not_prompt_to_save(window, tmp_path, monkeypatch, saved):
    if saved:
        choose_path(monkeypatch, tmp_path / "empty.sfig")
        assert window.save_project()
    before = deepcopy(window.project.to_dict())
    window.set_module("wb")
    APP.processEvents()
    window.project_recovery._capture_live_session()
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.StandardButton.Cancel)
    assert window.project.to_dict() == before
    assert not window.is_project_dirty()
    assert window._confirm_destructive_action("Close")
    assert questions == []
    window._active_wb_panel().data["test_edit"] = True
    assert window.is_project_dirty()
    assert not window._confirm_destructive_action("Close")
    assert len(questions) == 1


def roi_panel(window, tmp_path):
    path = tmp_path / "signal.png"
    image = QImage(24, 24, QImage.Format.Format_RGB32)
    image.fill(QColor("#ff3344"))
    assert image.save(str(path))
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    records = [panel.import_image(path), panel.import_image(path)]
    for record in records:
        panel.create_roi_intensity_measurement(record["uid"], "rectangle", {"x": 2, "y": 2, "w": 6, "h": 6}, [0])
    panel.data["active_image_uid"] = records[0]["uid"]
    window.fluoro_workspace.set_panel(panel, refresh=True)
    window.fluoro_workspace.set_step(2)
    return panel, records


def test_roi_clear_requires_confirmation(window, tmp_path, monkeypatch):
    panel, records = roi_panel(window, tmp_path)
    before = deepcopy(records[0]["measurements"])
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.StandardButton.No)
    window.fluoro_workspace.clear_roi_button.click()
    assert questions
    assert records[0]["measurements"] == before
    assert len(panel.analysis["measurements"]) == 2


def test_roi_clear_undo_redo_preserves_other_and_new_measurements(window, tmp_path, monkeypatch):
    panel, records = roi_panel(window, tmp_path)
    before = deepcopy(records[0]["measurements"])
    other = deepcopy(records[1]["measurements"])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    window.fluoro_workspace.clear_roi_button.click()
    assert records[0]["measurements"] == []
    new = panel.create_roi_intensity_measurement(records[0]["uid"], "rectangle", {"x": 12, "y": 12, "w": 4, "h": 4}, [0])
    window._undo_active_editor(False)
    assert records[0]["measurements"] == before + [new]
    assert records[1]["measurements"] == other
    assert len(panel.analysis["measurements"]) == 3
    window._undo_active_editor(True)
    assert records[0]["measurements"] == [new]
    assert records[1]["measurements"] == other
    assert len(panel.analysis["measurements"]) == 2


def test_roi_undo_history_does_not_cross_projects(window, tmp_path, monkeypatch):
    panel, records = roi_panel(window, tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    window.fluoro_workspace.clear_roi_button.click()
    monkeypatch.setattr(window, "_confirm_destructive_action", lambda *a: True)
    window.new_project()
    window._undo_active_editor(False)
    assert records[0]["measurements"] == []
    assert window._active_fluoro_panel().analysis.get("measurements", []) == []


def test_roi_clear_responds_to_ctrl_z_and_ctrl_y(window, tmp_path, monkeypatch):
    panel, records = roi_panel(window, tmp_path)
    before = deepcopy(records[0]["measurements"])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    window.fluoro_workspace.clear_roi_button.click()
    window.show()
    window.activateWindow()
    target = window.fluoro_workspace.canvas.view
    target.setFocus()
    QTest.qWait(50)
    QTest.keyClick(target, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    APP.processEvents()
    assert records[0]["measurements"] == before
    QTest.keyClick(target, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    APP.processEvents()
    assert records[0]["measurements"] == []
