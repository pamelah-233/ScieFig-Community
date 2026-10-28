from __future__ import annotations

from collections.abc import Mapping, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from core.chart_payload import chart_kind_label_key, translate_unavailable_reason
from core.utils.i18n import tr


class ChartKindControls(QWidget):
    kind_chosen = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChartKindControls")
        self._syncing = False
        self._current_kind = ""
        self._unavailable: dict[str, str] = {}

        self._label = QLabel(tr("chart_kind_label"))
        self._label.setObjectName("ChartKindLabel")
        self.combo = QComboBox()
        self.combo.setObjectName("ChartKindCombo")
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.combo.setMinimumContentsLength(12)
        self._model = QStandardItemModel(self.combo)
        self.combo.setModel(self._model)
        self.reason_label = QLabel()
        self.reason_label.setObjectName("ChartKindReasonLabel")
        self.reason_label.setWordWrap(True)
        self.reason_label.setVisible(False)

        row = QHBoxLayout()
        row.setContentsMargins(8, 4, 8, 0)
        row.setSpacing(8)
        row.addWidget(self._label)
        row.addWidget(self.combo, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(2)
        root.addLayout(row)
        root.addWidget(self.reason_label)
        self.combo.currentIndexChanged.connect(self._emit_kind)
        self.hide()

    def retranslate(self) -> None:
        self._label.setText(tr("chart_kind_label"))
        kinds: list[str] = []
        for row in range(self._model.rowCount()):
            kind = str(self._model.item(row).data(Qt.ItemDataRole.UserRole) or "")
            if kind:
                kinds.append(kind)
        if kinds:
            self.set_options(
                kinds,
                self._current_kind,
                unavailable=self._unavailable,
                enabled=self.combo.isEnabled(),
            )

    def set_options(
        self,
        kinds: Sequence[str],
        current: str,
        *,
        unavailable: Mapping[str, str] | None = None,
        enabled: bool = True,
        reason: str | None = None,
    ) -> None:
        self._unavailable = {str(key): str(value) for key, value in dict(unavailable or {}).items()}
        self._syncing = True
        self._model.clear()
        selected_row = -1
        current_token = str(current or "")
        for kind in kinds:
            token = str(kind)
            item = QStandardItem(tr(chart_kind_label_key(token)))
            item.setData(token, Qt.ItemDataRole.UserRole)
            blocked = token in self._unavailable
            if blocked:
                item.setEnabled(False)
                item.setToolTip(translate_unavailable_reason(self._unavailable[token]))
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if blocked:
                flags = Qt.ItemFlag.ItemIsSelectable
            item.setFlags(flags)
            self._model.appendRow(item)
            if token == current_token:
                selected_row = self._model.rowCount() - 1
        if selected_row < 0 and current_token:
            item = QStandardItem(tr(chart_kind_label_key(current_token)))
            item.setData(current_token, Qt.ItemDataRole.UserRole)
            if current_token in self._unavailable:
                item.setEnabled(False)
                item.setFlags(Qt.ItemFlag.ItemIsSelectable)
            self._model.appendRow(item)
            selected_row = self._model.rowCount() - 1
        if selected_row >= 0:
            self.combo.setCurrentIndex(selected_row)
        self._current_kind = current_token
        self.combo.setEnabled(bool(enabled) and self._model.rowCount() > 0)
        display_reason = reason or self._unavailable.get(current_token, "")
        text = translate_unavailable_reason(display_reason) if display_reason else ""
        self.reason_label.setText(text)
        self.reason_label.setVisible(bool(text))
        self._syncing = False
        self.setVisible(True)

    def show_disabled(self, reason: str) -> None:
        self._syncing = True
        self._model.clear()
        self._current_kind = ""
        self._unavailable = {}
        self.combo.setEnabled(False)
        self.reason_label.setText(translate_unavailable_reason(reason) or tr("chart_kind_reason_requires_source"))
        self.reason_label.setVisible(True)
        self._syncing = False
        self.setVisible(True)

    def current_kind(self) -> str:
        index = self.combo.currentIndex()
        if index < 0:
            return self._current_kind
        item = self._model.item(index)
        if item is None:
            return self._current_kind
        return str(item.data(Qt.ItemDataRole.UserRole) or self._current_kind)

    def _emit_kind(self, index: int) -> None:
        if self._syncing or index < 0:
            return
        item = self._model.item(index)
        if item is None:
            return
        kind = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not kind:
            return
        if kind in self._unavailable or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            self._syncing = True
            for row in range(self._model.rowCount()):
                candidate = self._model.item(row)
                if candidate is not None and str(candidate.data(Qt.ItemDataRole.UserRole) or "") == self._current_kind:
                    self.combo.setCurrentIndex(row)
                    break
            self._syncing = False
            blocked = translate_unavailable_reason(self._unavailable.get(kind, ""))
            if blocked:
                self.reason_label.setText(blocked)
                self.reason_label.setVisible(True)
            return
        self._current_kind = kind
        self.kind_chosen.emit(kind)
