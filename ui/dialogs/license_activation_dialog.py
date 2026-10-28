from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.constants import SOFTWARE_VERSION
from core.licensing import LICENSE_CHANNEL, LicenseError, LicenseManager
from core.utils.i18n import bind_i18n, tr, trf


class LicenseActivationDialog(QDialog):
    def __init__(self, *, manager: LicenseManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle(f"{tr('activate_sciefig')} {SOFTWARE_VERSION}")
        self.setModal(True)
        self.resize(560, 220)

        machine_code_label = bind_i18n(QLabel(), "machine_code")
        self.machine_code_field = QLineEdit(self.manager.current_machine_code())
        self.machine_code_field.setReadOnly(True)
        self.machine_code_field.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        copy_button = bind_i18n(QPushButton(), "copy_machine_code")
        copy_button.clicked.connect(self._copy_machine_code)

        machine_code_row = QHBoxLayout()
        machine_code_row.addWidget(self.machine_code_field, stretch=1)
        machine_code_row.addWidget(copy_button)

        instructions = QLabel(
            f"Send this machine code to the issuer and import the signed {LICENSE_CHANNEL} license JSON returned for this computer."
        )
        instructions.setWordWrap(True)

        self.status_label = bind_i18n(QLabel(), "no_valid_license")
        self._status_key: str | None = "no_valid_license"
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("LicenseActivationStatusLabel")

        import_button = bind_i18n(QPushButton(), "import_license")
        import_button.clicked.connect(self._import_license)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(machine_code_label)
        layout.addLayout(machine_code_row)
        layout.addWidget(instructions)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(import_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(buttons)
        self._copy_button = copy_button
        self._import_button = import_button

    def retranslate(self) -> None:
        self._copy_button.setText(tr("copy_machine_code"))
        self._import_button.setText(tr("import_license"))
        self.setWindowTitle(f"{tr('activate_sciefig')} {SOFTWARE_VERSION}")
        if self._status_key is not None:
            self.status_label.setText(tr(self._status_key))

    def _copy_machine_code(self) -> None:
        self.machine_code_field.selectAll()
        self.machine_code_field.copy()
        self._status_key = "machine_code_copied"
        self.status_label.setText(tr(self._status_key))

    def _import_license(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(self, tr("import_sciefig_license"), "", "JSON (*.json)")
        if not path:
            return
        try:
            self.manager.install_license_file(path)
        except LicenseError as exc:
            self._status_key = None
            self.status_label.setText(trf("invalid_license", error=exc))
            return
        self._status_key = "license_activated"
        self.status_label.setText(tr(self._status_key))
        self.accept()
