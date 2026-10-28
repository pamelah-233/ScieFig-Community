from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QPushButton

import ui.export_menu as export_menu_module
import ui.main_window as main_window_module
from core.utils.i18n import set_language, tr
import ui.panels.image_browser_workspace as image_browser_module
from ui.export_menu import (
    ACTION_FIGURE,
    ACTION_IMAGE,
    ACTION_SUMMARY_JSON,
    ACTION_TABLE,
    ACTION_TABLE_AI_STATS_CSV,
    ACTION_TABLE_EXCEL,
    ACTION_TABLE_RESULTS_CSV,
    ACTION_TABLE_STATS_CSV,
    apply_native_png_suffix,
    apply_selected_figure_suffix,
    top_level_label_keys,
)
from ui.main_window import MainWindow


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _close(window: MainWindow, app: QApplication) -> None:
    window._capture_clean_project_baseline()
    window.close()
    window.deleteLater()
    app.processEvents()


def _export_button(window: MainWindow) -> QPushButton:
    button = window.findChild(QPushButton, "V9ExportButton")
    assert button is not None
    return button


def _open_export_menu(window: MainWindow, monkeypatch) -> QMenu:
    captured: dict[str, QMenu] = {}

    def fake_exec(self, *args, **kwargs):
        captured["menu"] = self
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    monkeypatch.setattr(QMenu, "popup", fake_exec)
    monkeypatch.setattr(export_menu_module.QMenu, "exec", fake_exec)
    monkeypatch.setattr(export_menu_module.QMenu, "popup", fake_exec)
    _export_button(window).click()
    menu = captured.get("menu")
    assert menu is not None
    return menu


def _menu_items(menu: QMenu) -> list[dict]:
    items = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        entry = {"id": action.data(), "text": action.text(), "enabled": action.isEnabled()}
        submenu = action.menu()
        if submenu is not None:
            entry["children"] = _menu_items(submenu)
        items.append(entry)
    return items


def _trigger(menu: QMenu, action_id: str) -> None:
    for action in menu.actions():
        if action.data() == action_id:
            action.trigger()
            return
        submenu = action.menu()
        if submenu is not None:
            for child in submenu.actions():
                if child.data() == action_id:
                    child.trigger()
                    return
    raise AssertionError(f"missing export action {action_id}")


def test_apply_selected_figure_suffix_uses_dialog_filter() -> None:
    assert apply_selected_figure_suffix("figure", "PNG (*.png)", ".pdf").endswith(".png")
    assert apply_selected_figure_suffix("figure", "TIFF (*.tiff)", ".pdf").endswith(".tiff")
    assert apply_selected_figure_suffix("figure.pdf", "PNG (*.png)", ".pdf").endswith(".pdf")


def test_viewer_native_png_suffix_forced_on_top_and_local_paths(tmp_path, monkeypatch) -> None:
    assert apply_native_png_suffix(str(tmp_path / "shot")) == str(tmp_path / "shot.png")
    assert apply_native_png_suffix(str(tmp_path / "shot.jpg")) == str(tmp_path / "shot.png")
    assert apply_native_png_suffix(str(tmp_path / "shot.TIFF")) == str(tmp_path / "shot.png")
    assert apply_native_png_suffix(str(tmp_path / "shot.png")) == str(tmp_path / "shot.png")

    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    written: list[str] = []
    monkeypatch.setattr(
        window.image_browser_workspace,
        "export_snapshot",
        lambda path: written.append(str(path)) or True,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    try:
        window.image_browser_workspace._cached_image = type("LoadedImage", (), {"path": tmp_path / "loaded.png"})()
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "native.jpg"), "PNG (*.png)"),
        )
        window.export_viewer_native_image()
        assert written == [str(tmp_path / "native.png")]

        written.clear()
        monkeypatch.setattr(window.image_browser_workspace, "window", lambda: window.image_browser_workspace)
        monkeypatch.setattr(
            image_browser_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "local.bmp"), "PNG (*.png)"),
        )
        window.image_browser_workspace.choose_export_path()
        assert written == [str(tmp_path / "local.png")]
    finally:
        _close(window, app)


def test_ihc_and_brightfield_top_export_not_placeholder(monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    audits: list[str] = []
    original_append = window.project.append_audit

    def capture_audit(event, detail=""):
        audits.append(str(event))
        return original_append(event, detail)

    messages: list[str] = []
    monkeypatch.setattr(window.project, "append_audit", capture_audit)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text, *args, **kwargs: messages.append(str(text)),
    )
    try:
        for module_id in ("ihc", "brightfield"):
            audits.clear()
            messages.clear()
            window.set_module(module_id)
            menu = _open_export_menu(window, monkeypatch)
            texts = [item["text"] for item in _menu_items(menu)]
            assert "export_placeholder" not in audits
            assert tr("export_pipeline_later") not in messages
            assert tr("export_image") in texts
            assert tr("export_figure") in texts
            assert tr("export_table") in texts
    finally:
        _close(window, app)
        set_language("en")


def test_top_export_menu_content_per_module(monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    expected = {
        "qpcr": [tr("export_figure")],
        "wb": [tr("export_figure")],
        "fluoro": [tr("export_image"), tr("export_figure"), tr("export_table")],
        "ihc": [tr("export_image"), tr("export_figure"), tr("export_table"), tr("export_more")],
        "brightfield": [tr("export_image"), tr("export_figure"), tr("export_table")],
        "viewer": [tr("export_image")],
        "composer": [tr("export_figure")],
    }
    try:
        for module_id, labels in expected.items():
            window.set_module(module_id)
            menu = _open_export_menu(window, monkeypatch)
            texts = [item["text"] for item in _menu_items(menu)]
            assert texts == labels, module_id
            assert top_level_label_keys(module_id)
        window.set_module("ihc")
        ihc_menu = _open_export_menu(window, monkeypatch)
        table = next(item for item in _menu_items(ihc_menu) if item["id"] == ACTION_TABLE)
        assert {child["id"] for child in table["children"]} == {
            ACTION_TABLE_EXCEL,
            ACTION_TABLE_RESULTS_CSV,
            ACTION_TABLE_STATS_CSV,
        }
        more = next(item for item in _menu_items(ihc_menu) if item["id"] == "more")
        assert more["children"][0]["id"] == ACTION_SUMMARY_JSON
        window.set_module("brightfield")
        bf_menu = _open_export_menu(window, monkeypatch)
        table = next(item for item in _menu_items(bf_menu) if item["id"] == ACTION_TABLE)
        assert {child["id"] for child in table["children"]} == {
            ACTION_TABLE_EXCEL,
            ACTION_TABLE_STATS_CSV,
            ACTION_TABLE_AI_STATS_CSV,
        }
    finally:
        _close(window, app)
        set_language("en")


def test_stylelab_export_is_disabled_empty_state(monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    audits: list[str] = []
    original_append = window.project.append_audit
    monkeypatch.setattr(
        window.project,
        "append_audit",
        lambda event, detail="": audits.append(str(event)) or original_append(event, detail),
    )
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text, *args, **kwargs: messages.append(str(text)),
    )
    writers: list[str] = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: writers.append("dialog") or ("", ""))
    try:
        window.set_module("stylelab")
        button = _export_button(window)
        assert button.isEnabled() is False
        assert button.toolTip() == tr("export_stylelab_empty")
        button.click()
        window.export_active_module()
        assert writers == []
        assert "export_placeholder" not in audits
        assert tr("export_pipeline_later") not in messages
        assert tr("export_stylelab_empty") in messages
    finally:
        _close(window, app)
        set_language("en")


def test_top_export_routes_per_module(tmp_path, monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    calls: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    try:
        window.set_module("viewer")
        window.image_browser_workspace._cached_image = type("LoadedImage", (), {"path": tmp_path / "loaded.png"})()
        monkeypatch.setattr(window.image_browser_workspace, "export_snapshot", lambda path: calls.append(f"viewer:{path}") or True)
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(tmp_path / "view.png"), "PNG (*.png)"))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_IMAGE)
        assert calls[-1].startswith("viewer:")

        window.set_module("composer")
        monkeypatch.setattr(window.composer_canvas, "export_figure", lambda path, dpi=300, include_source_data=False: calls.append(f"composer:{path}") or Path(path))
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(tmp_path / "comp.pdf"), "PDF (*.pdf)"))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert calls[-1].startswith("composer:")

        window.set_module("wb")
        window._active_wb_panel().data["image_data"] = {"ok": True}
        monkeypatch.setattr(main_window_module, "export_wb_figure_scene", lambda scene, path, dpi=1200: calls.append(f"wb:{path}") or Path(path))
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(tmp_path / "wb.pdf"), "PDF (*.pdf)"))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert calls[-1].startswith("wb:")

        window.set_module("fluoro")
        monkeypatch.setattr(window._active_fluoro_panel(), "active_image", lambda: {"uid": "f1"})
        monkeypatch.setattr(window.fluoro_workspace, "persist_layout_edits_from_scene", lambda: None)
        monkeypatch.setattr(window.fluoro_workspace.canvas, "set_step", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(window.fluoro_workspace, "include_metadata", lambda: False)
        monkeypatch.setattr(window.fluoro_workspace, "include_measurements", lambda: False)
        monkeypatch.setattr(window.fluoro_workspace, "include_source", lambda: False)
        monkeypatch.setattr(window, "_export_fluorescence_figure_scene", lambda path, dpi: calls.append(f"fluoro:{path}") or Path(path))
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(tmp_path / "fl.pdf"), "PDF (*.pdf)"))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert calls[-1].startswith("fluoro:")

        window.set_module("qpcr")
        window._active_qpcr_panel().data.setdefault("processed_data", {})["fold_change"] = [{"x": 1}]
        monkeypatch.setattr(main_window_module, "export_qpcr_panel", lambda panel, path: calls.append(f"qpcr:{path}") or Path(path))
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(tmp_path / "qpcr.pdf"), "PDF (*.pdf)"))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert calls[-1].startswith("qpcr:")

        window.set_module("ihc")
        monkeypatch.setattr(window, "export_ihc_overlay_image", lambda: calls.append("ihc-image"))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_IMAGE)
        assert calls[-1] == "ihc-image"

        window.set_module("brightfield")
        monkeypatch.setattr(window, "export_brightfield_image", lambda: calls.append("bf-image"))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_IMAGE)
        assert calls[-1] == "bf-image"
    finally:
        _close(window, app)
        set_language("en")


def test_top_export_no_results_and_cancel(tmp_path, monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    messages: list[str] = []
    writers: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text, *args, **kwargs: messages.append(str(text)),
    )
    monkeypatch.setattr(main_window_module, "export_wb_figure_scene", lambda *args, **kwargs: writers.append("wb") or Path(tmp_path / "x.pdf"))
    monkeypatch.setattr(main_window_module, "export_qpcr_panel", lambda *args, **kwargs: writers.append("qpcr") or Path(tmp_path / "x.pdf"))
    try:
        window.set_module("qpcr")
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: writers.append("dialog") or ("", ""))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert tr("import_qpcr_before_export") in messages
        assert writers == []

        window.set_module("wb")
        messages.clear()
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert tr("import_wb_before_export") in messages
        assert writers == []

        window._active_wb_panel().data["image_data"] = {"ok": True}
        messages.clear()
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert writers == []
        assert messages == []

        window.set_module("ihc")
        messages.clear()
        _trigger(_open_export_menu(window, monkeypatch), ACTION_TABLE_EXCEL)
        assert writers == []
        messages.clear()
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert tr("import_ihc_before_layout") in messages
        assert writers == []

        window.set_module("brightfield")
        messages.clear()
        _trigger(_open_export_menu(window, monkeypatch), ACTION_TABLE_STATS_CSV)
        assert tr("no_measurements_to_export") in messages
        assert writers == []
        messages.clear()
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert tr("import_brightfield_before_layout") in messages
        assert writers == []
    finally:
        _close(window, app)
        set_language("en")


def test_wb_fluoro_figure_uses_selected_filter(tmp_path, monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    written: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    try:
        window.set_module("wb")
        window.data_panel.wb_export_pdf_radio.setChecked(True)
        window._active_wb_panel().data["image_data"] = {"ok": True}
        monkeypatch.setattr(
            main_window_module,
            "export_wb_figure_scene",
            lambda scene, path, dpi=1200: written.append(str(path)) or Path(path),
        )
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "wb_export"), "PNG (*.png)"),
        )
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert written[-1].endswith(".png")

        window.set_module("fluoro")
        monkeypatch.setattr(window._active_fluoro_panel(), "active_image", lambda: {"uid": "f1"})
        monkeypatch.setattr(window.fluoro_workspace, "persist_layout_edits_from_scene", lambda: None)
        monkeypatch.setattr(window.fluoro_workspace.canvas, "set_step", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(window.fluoro_workspace, "export_format", lambda: "pdf")
        monkeypatch.setattr(window.fluoro_workspace, "include_metadata", lambda: False)
        monkeypatch.setattr(window.fluoro_workspace, "include_measurements", lambda: False)
        monkeypatch.setattr(window.fluoro_workspace, "include_source", lambda: False)
        monkeypatch.setattr(
            window,
            "_export_fluorescence_figure_scene",
            lambda path, dpi: written.append(str(path)) or Path(path),
        )
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "fluoro_export"), "TIFF (*.tiff)"),
        )
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert written[-1].endswith(".tiff")
    finally:
        _close(window, app)
        set_language("en")


def test_local_figure_buttons_use_leaf_not_menu(monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    menus: list[str] = []
    dialogs: list[str] = []
    messages: list[str] = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *args, **kwargs: menus.append("menu"))
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: dialogs.append("dialog") or ("", ""),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text, *args, **kwargs: messages.append(str(text)),
    )
    try:
        window.set_module("wb")
        window.data_panel.wb_export_button.click()
        window.set_module("fluoro")
        window.fluoro_workspace.export_button.click()
        window.set_module("composer")
        window.composer_canvas.export_requested.emit()
        assert menus == []
        assert tr("import_wb_before_export") in messages
        assert tr("import_fluorescence_before_export") in messages
        assert dialogs == ["dialog"]
        window.switch_language("zh")
        app.processEvents()
        window.set_module("wb")
        assert window.data_panel.wb_export_button.text() == tr("export_figure")
        window.set_module("fluoro")
        assert window.fluoro_workspace.export_button.text() == tr("export_figure")
    finally:
        window.switch_language("en")
        _close(window, app)


def test_ihc_and_brightfield_layout_figure_uses_renderer(tmp_path, monkeypatch) -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    rendered: list[tuple[str, int | None]] = []
    natives: list[str] = []
    layout = QImage(12, 8, QImage.Format.Format_ARGB32)
    layout.fill(QColor("#CC2030"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "export_ihc_original_image_to_path", lambda path: natives.append("ihc-orig") or Path(path))
    monkeypatch.setattr(window, "export_ihc_overlay_image_to_path", lambda path: natives.append("ihc-over") or Path(path))
    monkeypatch.setattr(window, "export_brightfield_original_image_to_path", lambda path: natives.append("bf-orig") or Path(path))
    monkeypatch.setattr(window, "export_brightfield_overlay_image_to_path", lambda path: natives.append("bf-over") or Path(path))
    try:
        window.set_module("ihc")
        monkeypatch.setattr(window, "_prepare_ihc_layout_for_export", lambda: True)
        monkeypatch.setattr(
            window.ihc_workspace.layout_canvas,
            "render_full_layout",
            lambda dpi=None: rendered.append(("ihc", dpi)) or QImage(layout),
        )
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "ihc_layout.png"), "PNG (*.png)"),
        )
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert rendered[-1] == ("ihc", 1200)
        assert natives == []
        saved = QImage(str(tmp_path / "ihc_layout.png"))
        assert (saved.width(), saved.height()) == (12, 8)

        window.set_module("brightfield")
        monkeypatch.setattr(window, "_prepare_brightfield_layout_for_export", lambda: True)
        monkeypatch.setattr(
            window.brightfield_workspace.layout_canvas,
            "render_full_layout",
            lambda dpi=None: rendered.append(("bf", dpi)) or QImage(layout),
        )
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "bf_layout.png"), "PNG (*.png)"),
        )
        _trigger(_open_export_menu(window, monkeypatch), ACTION_FIGURE)
        assert rendered[-1] == ("bf", 1200)
        assert natives == []
        saved_bf = QImage(str(tmp_path / "bf_layout.png"))
        assert (saved_bf.width(), saved_bf.height()) == (12, 8)
    finally:
        _close(window, app)
        set_language("en")
