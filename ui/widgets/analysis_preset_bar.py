"""Compact named analysis preset selector. Storage is analysis/named_presets."""
from PyQt6.QtWidgets import (
    QComboBox,
    QVBoxLayout,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from core.utils.analysis_presets import AnalysisPresets, library_events
from core.utils.i18n import bind_i18n, tr


class AnalysisPresetBar(QWidget):
    def __init__(self, module: str, parent=None, *, library=None, on_apply=None, on_save=None):
        super().__init__(parent)
        self.setObjectName("AnalysisPresetBar")
        self.module = module
        self.library = library if library is not None else AnalysisPresets()
        self._on_apply = on_apply
        self._on_save = on_save
        self._method = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumContentsLength(8)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preset_combo.setToolTip(tr("analysis_preset_hint"))
        self.apply_button = bind_i18n(QPushButton(), "preset_apply")
        self.save_button = bind_i18n(QPushButton(), "preset_save")
        self.manage_button = bind_i18n(QToolButton(), "analysis_preset_manage")
        self.manage_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.manage_button)
        self.rename_action = menu.addAction(tr("preset_rename"))
        self.delete_action = menu.addAction(tr("preset_delete"))
        self.manage_button.setMenu(menu)
        self.apply_button.clicked.connect(self._apply)
        self.save_button.clicked.connect(self._save)
        self.rename_action.triggered.connect(self._rename)
        self.delete_action.triggered.connect(self._delete)
        for button in (self.apply_button, self.save_button, self.manage_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.preset_combo)
        from ui.widgets.calibration_controls import action_grid
        layout.addWidget(action_grid((self.apply_button,self.save_button,self.manage_button)))
        events = library_events()
        if events is not None:
            events.changed.connect(self.reload)
        self.reload()

    def retranslate(self):
        from core.utils.i18n import retranslate_bound_widgets

        retranslate_bound_widgets(self)
        self.preset_combo.setToolTip(tr("analysis_preset_hint"))
        self.rename_action.setText(tr("preset_rename"))
        self.delete_action.setText(tr("preset_delete"))
        self.reload()

    def set_method(self, method: str) -> None:
        method = str(method or "")
        if method == self._method:
            self.reload()
            return
        self._method = method
        self.reload()

    def reload(self):
        selected = self.preset_combo.currentData()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem(tr("analysis_preset_choose"), None)
        for record in self.library.items_for(self.module, self._method):
            self.preset_combo.addItem(record["name"], record["id"])
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(selected)))
        self.preset_combo.blockSignals(False)

    def current_record(self):
        preset_id = self.preset_combo.currentData()
        if not preset_id:
            return None
        return next((record for record in self.library.items() if record.get("id") == preset_id), None)

    def _apply(self):
        record = self.current_record()
        if record is None or self._on_apply is None:
            return
        try:
            self._on_apply(record)
        except ValueError as exc:
            QMessageBox.warning(self, tr("presets"), tr(str(exc)))

    def _save(self):
        if self._on_save is None:
            return
        name, ok = QInputDialog.getText(self, tr("preset_save"), tr("analysis_preset_name"))
        if not ok:
            return
        try:
            record = self._on_save(name)
            if record:
                self.reload()
                self.preset_combo.setCurrentIndex(self.preset_combo.findData(record["id"]))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("presets"), tr(str(exc)))

    def _rename(self):
        record = self.current_record()
        if record is None:
            return
        name, ok = QInputDialog.getText(self, tr("preset_rename"), tr("analysis_preset_name"), text=record["name"])
        if not ok:
            return
        try:
            renamed = self.library.rename(record["id"], name)
            self.reload()
            self.preset_combo.setCurrentIndex(self.preset_combo.findData(renamed["id"]))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("presets"), tr(str(exc)))

    def _delete(self):
        record = self.current_record()
        if record is None:
            return
        if QMessageBox.question(self, tr("preset_delete"), tr("analysis_preset_delete_confirm")) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.delete(record["id"])
            self.reload()
        except OSError as exc:
            QMessageBox.warning(self, tr("presets"), tr(str(exc)))
