from pathlib import Path
from PyQt6.QtWidgets import QApplication, QTabWidget
from ui.widgets.style_lab_workspace import StyleLabWorkspace


def test_only_new_page_is_created_and_saved_styles_remain_available(tmp_path):
    app = QApplication.instance() or QApplication([])
    workspace = StyleLabWorkspace(preset_registry_path=tmp_path / "styles.json", render_initial_preview=False)
    try:
        workspace.open_local_extraction()
        assert not workspace.findChildren(QTabWidget)
        assert not hasattr(workspace, "board")
        assert not hasattr(workspace, "extract_style_button")
        assert workspace.local_extraction.api.can_extract
        draft = {"name": "Saved palette", "source": {}, "tokens": {"series_colors": {"value": ["#123456"], "status": "user_edited"}}}
        workspace._save_local_extraction(draft, False)
        records = workspace._preset_registry.list_presets()
        assert len(records) == 1
        workspace._refresh_saved_menu()
        assert workspace.saved_menu.actions()[0].text() == "Saved palette"
        workspace.local_extraction._confirm_replace = lambda message: True
        workspace._load_saved_style(records[0].preset_id)
        assert workspace.local_extraction.draft["name"] == "Saved palette"
        assert workspace.activate_saved_style_preset(records[0].preset_id) is not None
    finally:
        from core.style_preset_resolver import clear_runtime_active_style_preset
        clear_runtime_active_style_preset()
        workspace.shutdown_local_extraction()
        workspace.close()


def test_entering_style_module_opens_new_design_directly():
    from ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window._confirm_destructive_action = lambda *args: True
    try:
        window.set_module("stylelab")
        assert window.stylelab_workspace.local_extraction is not None
        assert not window.stylelab_workspace.findChildren(QTabWidget)
    finally:
        window.close()
