from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel,
                            QTableWidget, QTableWidgetItem, QVBoxLayout, QPushButton)
from core.statistics_pairing import sample_identity, save_pair_ids
from core.utils.i18n import bind_i18n, tr


class StatisticsPairingDialog(QDialog):
    def __init__(self, rows, assignments, saved, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('pairing_settings'))
        self.resize(680, 440)
        layout = QVBoxLayout(self)
        hint = bind_i18n(QLabel(), 'pairing_hint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr('sample'), tr('group'), tr('pair_id')])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row in rows:
            group_key = next((str(row.get(k, '')) for k in ('sample_group_key','sample_key','lane_label')
                              if str(row.get(k, '')) in assignments), None)
            if group_key is None:
                continue
            index = self.table.rowCount()
            self.table.insertRow(index)
            key = sample_identity(row)
            label = str(row.get('sample_label') or row.get('image_name') or row.get('lane_label') or key)
            for col, value in enumerate((label, assignments[group_key], saved.get(key, ''))):
                item = QTableWidgetItem(str(value))
                if col < 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    item.setToolTip(key)
                self.table.setItem(index, col, item)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def pair_ids(self):
        return {str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)): self.table.item(row, 2).text().strip()
                for row in range(self.table.rowCount())}


def pairing_button(parent, context):
    """context returns (panel, source, rows, current group assignments)."""
    button = bind_i18n(QPushButton(parent), 'pairing_settings')
    def edit():
        panel, source, rows, assignments = context()
        if panel is None:
            return
        dialog = StatisticsPairingDialog(rows, assignments, panel.analysis.get('statistics_pair_ids', {}).get(source, {}), parent.window())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            save_pair_ids(panel, source, dialog.pair_ids())
    button.clicked.connect(edit)
    return button
