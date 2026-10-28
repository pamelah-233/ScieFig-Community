from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QMenu, QPushButton, QVBoxLayout, QWidget, QInputDialog
from core.style_preset_registry import StylePresetRegistry, default_style_preset_registry_path
from core.style_preset_resolver import set_runtime_active_style_preset
from core.utils.i18n import bind_i18n, retranslate_bound_widgets, get_language


class StyleLabWorkspace(QWidget):
    """Current local extraction UI; the retired preset board is removed."""
    runtime_saved_preset_activated = pyqtSignal()
    defaults_requested = pyqtSignal()

    def __init__(self, *, preset_registry_path=None, render_initial_preview=True, parent=None):
        super().__init__(parent)
        self.setObjectName("StyleLabWorkspace")
        self._preset_registry_path = Path(preset_registry_path or default_style_preset_registry_path())
        self._preset_registry = StylePresetRegistry(self._preset_registry_path)
        self.local_extraction = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0) if sys.platform in {'darwin', 'win32'} else root.setContentsMargins(8, 8, 8, 8)
        if render_initial_preview:
            self.open_local_extraction()

    def open_local_extraction(self):
        if self.local_extraction is not None:
            return
        from ui.local_style_extraction.app import LocalStyleExtractionWindow
        self.local_extraction = LocalStyleExtractionWindow()
        self.local_extraction.setParent(self)
        self.local_extraction.setWindowFlags(Qt.WindowType.Widget)
        self.layout().addWidget(self.local_extraction)
        self.local_extraction.saveToLibraryRequested.connect(self._save_local_extraction)
        self.saved_menu = QMenu(self.local_extraction.btn_saved)
        self.saved_menu.aboutToShow.connect(self._refresh_saved_menu)
        self.local_extraction.btn_saved.setMenu(self.saved_menu)
        self.reset_defaults_button=bind_i18n(QPushButton(),'module_restore_defaults')
        bind_i18n(self.reset_defaults_button,'module_restore_defaults_hint','toolTip')
        studio=self.local_extraction.studio_ui
        if studio is not None:
            from ui.widgets.studio_icons import set_studio_icon
            set_studio_icon(self.reset_defaults_button,'reset')
            studio.action_row.layout().insertWidget(2,self.reset_defaults_button)
            studio.action_buttons.append((self.reset_defaults_button,self.reset_defaults_button.text()))
        else:
            self.local_extraction.top.layout().itemAt(0).layout().addWidget(self.reset_defaults_button)
        self.reset_defaults_button.clicked.connect(self.defaults_requested.emit)
        self.retranslate()

    def retranslate(self):
        retranslate_bound_widgets(self)
        if self.local_extraction is not None:
            self.reset_defaults_button.setAccessibleName(self.reset_defaults_button.text())
            self.local_extraction.btn_saved.setText("已保存样式" if get_language() == "zh" else "Saved styles")
            studio=self.local_extraction.studio_ui
            if studio is not None:
                studio.action_buttons=[(button,self.reset_defaults_button.text() if button is self.reset_defaults_button else label)
                                       for button,label in studio.action_buttons]
                studio._update_density()

    def restore_defaults(self):
        editor=self.local_extraction
        if editor is None:return
        for key in list((editor.draft or {}).get('tokens',{})):
            editor._token_reset(key)
        editor.toolbar_toggle.setChecked(True)

    def _refresh_saved_menu(self):
        self.saved_menu.clear()
        try:
            records = self._preset_registry.list_presets()
        except (ValueError, OSError) as exc:
            self.local_extraction._set_action_error(str(exc))
            return
        if not records:
            self.saved_menu.addAction("尚无已保存样式").setEnabled(False)
        for record in records:
            menu = self.saved_menu.addMenu(record.display_name)
            menu.addAction("应用", lambda checked=False, uid=record.preset_id: self.activate_saved_style_preset(uid))
            menu.addAction("重命名…", lambda checked=False, uid=record.preset_id: self._rename_saved_style(uid))
            if record.extraction_profile.get("local_extraction"):
                menu.addAction("载入编辑", lambda checked=False, uid=record.preset_id: self._load_saved_style(uid))

    def _load_saved_style(self, preset_id):
        record = self._preset_registry.get_preset(preset_id)
        if record is None or not self.local_extraction._confirm_replace("载入已保存样式"):
            return
        draft = deepcopy(record.extraction_profile.get("local_extraction"))
        if not draft:
            return
        draft['name'] = record.display_name
        editor = self.local_extraction
        editor.draft = draft
        editor._remember_origin(draft)
        editor.name_edit.setText(record.display_name)
        editor.token_editor.set_draft(draft, editor.unsupported_keys, editor.font_substitutions)
        editor._schedule_preview()
        editor._refresh_enabled()
        editor._set_stage(1)
        source = draft.get('source', {})
        path = str(source.get('path', ''))
        if path and Path(path).is_file():
            editor._reload_source_visual(path, int(source.get('page_index', 0)), list(source.get('rect', [0,0,1,1])))
        elif path:
            editor._set_action_note('样式已载入；原始来源文件不可用，无法显示原文对照。')

    def _rename_saved_style(self, preset_id):
        try:
            record = self._preset_registry.get_preset(preset_id)
            if record is None:
                return
            name, accepted = QInputDialog.getText(self, '重命名样式', '名称', text=record.display_name)
            if accepted and name.strip():
                self._preset_registry.rename_preset(preset_id, name)
                self.local_extraction._set_action_note(f'已重命名：{name.strip()}')
        except (ValueError, OSError) as exc:
            self.local_extraction._set_action_error(str(exc))

    def activate_saved_style_preset(self, preset_id):
        try:
            record = self._preset_registry.get_preset(preset_id)
            if record is None:
                raise ValueError("样式不存在。")
            status = set_runtime_active_style_preset(preset_id, self._preset_registry_path)
            if not status.ok:
                raise ValueError(status.message)
            self.runtime_saved_preset_activated.emit()
            self.local_extraction._set_action_note(f"已应用：{record.display_name}")
            return record
        except (ValueError, OSError) as exc:
            self.local_extraction._set_action_error(str(exc))
            return None

    def _save_local_extraction(self, draft, activate):
        from ui.local_style_extraction.integration import save_extracted_style
        try:
            saved = save_extracted_style(draft, self._preset_registry)
            if activate:
                self.activate_saved_style_preset(saved.preset_id)
            else:
                self.local_extraction._set_action_note(f"已保存：{saved.display_name}；可从“已保存样式”复用。")
        except (ValueError, OSError) as exc:
            self.local_extraction._set_action_error(str(exc))

    def shutdown_local_extraction(self):
        if self.local_extraction is None:
            return True
        self.local_extraction._preview_timer.stop()
        self.local_extraction.worker.shutdown()
        return self.local_extraction.worker.wait(30000)

    def closeEvent(self, event):
        if self.shutdown_local_extraction():
            super().closeEvent(event)
        else:
            event.ignore()

