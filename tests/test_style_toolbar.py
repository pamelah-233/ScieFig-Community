from PyQt6.QtWidgets import QApplication
from ui.widgets.style_lab_workspace import StyleLabWorkspace


def test_toolbar_compacts_collapses_and_restores_controls_without_losing_state(tmp_path):
    app = QApplication.instance() or QApplication([])
    workspace = StyleLabWorkspace(preset_registry_path=tmp_path / "styles.json")
    workspace.resize(1280, 800)
    workspace.show()
    app.processEvents()
    editor = workspace.local_extraction
    try:
        assert editor.action_msg.isHidden()
        assert editor.mismatch.isHidden()
        expanded_height = editor.top.height()
        assert expanded_height <= 135
        editor.name_edit.setText("保留名称")
        editor.toolbar_toggle.click()
        app.processEvents()
        assert editor.toolbar_body.isHidden()
        assert editor.top.height() <= 45
        assert editor.top.height() < expanded_height
        editor.toolbar_toggle.click()
        app.processEvents()
        assert editor.btn_open.isVisible()
        assert editor.btn_saved.isVisible()
        assert editor.name_edit.text() == "保留名称"
        editor._set_action_note("已保存")
        assert not editor.action_msg.isHidden()
        editor._set_action_note("")
        assert editor.action_msg.isHidden()
    finally:
        workspace.close()
