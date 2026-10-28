from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.utils.i18n import bind_i18n, tr


class WBExportDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("export_wb_figure"))
        self.setStyleSheet(
            """
            QDialog {
                background-color: #1E1A16;
            }
            QRadioButton {
                color: #E8E8E8;
                spacing: 8px;
                font-size: 13px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #AAAAAA;
                border-radius: 9px;
                background-color: transparent;
            }
            QRadioButton::indicator:checked {
                border: 2px solid #4F8CFF;
                background-color: #4F8CFF;
            }
            QRadioButton::indicator:hover {
                border: 2px solid #CCCCCC;
            }
            QCheckBox {
                color: #E8E8E8;
                spacing: 8px;
                font-size: 13px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #AAAAAA;
                border-radius: 3px;
                background-color: transparent;
            }
            QCheckBox::indicator:checked {
                border: 2px solid #4F8CFF;
                background-color: #4F8CFF;
            }
            QComboBox {
                background-color: #2A2E3A;
                color: #E8E8E8;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QLabel {
                color: #E8E8E8;
            }
            QPushButton {
                background-color: #363B4A;
                color: #E8E8E8;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 6px 16px;
            }
            QPushButton:hover {
                background-color: #4A5060;
            }
            """
        )
        self.pdf_radio = bind_i18n(QRadioButton(), "pdf_vector_recommended")
        self.svg_radio = bind_i18n(QRadioButton(), "svg_vector_editable")
        self.tiff_radio = bind_i18n(QRadioButton(), "tiff_raster")
        self.png_radio = bind_i18n(QRadioButton(), "png_raster")
        self.pdf_radio.setChecked(True)
        self.dpi_combo = QComboBox()
        self.dpi_combo.addItems(["300", "600", "1200"])
        self.dpi_combo.setCurrentText("1200")
        self.include_source_checkbox = bind_i18n(QCheckBox(), "include_source_full_blot_log")

        format_box = QVBoxLayout()
        format_box.addWidget(self.pdf_radio)
        format_box.addWidget(self.svg_radio)
        format_box.addWidget(self.tiff_radio)
        format_box.addWidget(self.png_radio)

        form = QFormLayout()
        self.format_label = bind_i18n(QLabel(), "format")
        self.dpi_label = bind_i18n(QLabel(), "dpi")
        form.addRow(self.format_label, self._format_widget(format_box))
        form.addRow(self.dpi_label, self.dpi_combo)
        form.addRow("", self.include_source_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("ok"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.pdf_radio.toggled.connect(self._update_dpi_enabled)
        self.svg_radio.toggled.connect(self._update_dpi_enabled)
        self.tiff_radio.toggled.connect(self._update_dpi_enabled)
        self.png_radio.toggled.connect(self._update_dpi_enabled)
        self._update_dpi_enabled()
        self.button_box = buttons

    def selected_format(self) -> str:
        if self.svg_radio.isChecked():
            return "svg"
        if self.tiff_radio.isChecked():
            return "tiff"
        if self.png_radio.isChecked():
            return "png"
        return "pdf"

    def selected_dpi(self) -> int:
        return int(self.dpi_combo.currentText())

    def include_source_data(self) -> bool:
        return self.include_source_checkbox.isChecked()

    def _update_dpi_enabled(self) -> None:
        self.dpi_combo.setEnabled(self.tiff_radio.isChecked() or self.png_radio.isChecked())

    def _format_widget(self, layout: QVBoxLayout) -> QWidget:
        box = QWidget(self)
        box.setLayout(layout)
        return box
