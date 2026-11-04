from __future__ import annotations

from pathlib import Path
import sys

from PyQt6.QtCore import QRect, Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QSizePolicy, QVBoxLayout

from core.utils.i18n import tr


MODULES = (
    ("qpcr", "qPCR", "chart-column.svg"),
    ("wb", "WB", "layers.svg"),
    ("fluoro", "Fluorescence", "grid-3x3.svg"),
    ("ihc", "IHC", "pipette.svg"),
    ("brightfield", "Brightfield", "pipette.svg"),
    ("viewer", "Image Browser", "grid-3x3.svg"),
    ("composer", "Composer", "layout-grid.svg"),
    ("stylelab", "Style Lab", "settings-2.svg"),
)

COMPACT_LABELS = {
    "qpcr": "v9_module_qpcr",
    "wb": "v9_module_wb",
    "fluoro": "v9_module_fluoro_compact",
    "ihc": "v9_module_ihc",
    "brightfield": "v9_module_brightfield_compact",
    "viewer": "v9_module_viewer_compact",
    "composer": "v9_module_composer_compact",
    "stylelab": "v9_module_stylelab_compact",
}

MODULE_LABEL_KEYS = {
    "qpcr": "v9_module_qpcr",
    "wb": "v9_module_wb",
    "fluoro": "v9_module_fluoro",
    "ihc": "v9_module_ihc",
    "brightfield": "v9_module_brightfield",
    "viewer": "v9_module_viewer",
    "composer": "v9_module_composer",
    "stylelab": "v9_module_stylelab",
}

ICON_ONLY_WIDTH = 46
COMPACT_LABEL_WIDTH = 150
COMPACT_FONT_SIZE_PT = 8.8
MIN_COMPACT_FONT_SIZE_PT = 6.8
MODULE_BUTTON_HEIGHT = 50 if sys.platform in {'darwin', 'win32'} else 56


class ModuleNavButton(QAbstractButton):
    """Compact icon-over-text navigation button without QToolButton elision."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ModuleButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(QSize(42, MODULE_BUTTON_HEIGHT))
        self.setMaximumHeight(MODULE_BUTTON_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(72, MODULE_BUTTON_HEIGHT)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        if self.isChecked():
            painter.setPen(QPen(QColor('#775a46') if sys.platform in {'darwin', 'win32'} else QColor(217, 119, 87, 120), 1))
            painter.setBrush(QColor('#39312c' if sys.platform in {'darwin', 'win32'} else '#2c2620'))
            text_color = QColor('#ecd0b9' if sys.platform in {'darwin', 'win32'} else '#d97757')
        elif self.underMouse():
            painter.setPen(QPen(QColor('#45403b' if sys.platform in {'darwin', 'win32'} else '#38302a'), 1))
            painter.setBrush(QColor('#2e2c2a' if sys.platform in {'darwin', 'win32'} else '#2c2620'))
            text_color = QColor('#e9e5df' if sys.platform in {'darwin', 'win32'} else '#a39689')
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            text_color = QColor("#b6afa6" if sys.platform in {'darwin', 'win32'} else "#6e6258")
        painter.drawRoundedRect(rect, 4, 4)

        icon_size = self.iconSize()
        icon_y = 7 if self.text() else max(4, (self.height() - icon_size.height()) // 2)
        icon_x = max(0, (self.width() - icon_size.width()) // 2)
        icon = self.icon().pixmap(icon_size)
        if sys.platform in {'darwin', 'win32'}:
            icon = icon.copy()
            tint = QPainter(icon)
            tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            tint.fillRect(icon.rect(), text_color)
            tint.end()
        painter.drawPixmap(icon_x, icon_y, icon)

        if self.text():
            font = self.font()
            if sys.platform in {'darwin', 'win32'}:
                font.setWeight(QFont.Weight.Medium if self.isChecked() else QFont.Weight.Normal)
            else:
                font.setBold(self.isChecked())
            painter.setFont(font)
            painter.setPen(text_color)
            text_rect = QRect(1, icon_y + icon_size.height() + 3, self.width() - 2, self.height() - icon_y - icon_size.height() - 4)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.text())


class ModuleSelector(QFrame):
    module_changed = pyqtSignal(str)

    def __init__(self, resources_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ModuleNav")
        self.setMinimumWidth(44)
        self.setMaximumWidth(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, ModuleNavButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        for module_id, label, icon_name in MODULES:
            button = ModuleNavButton()
            button.setText(label)
            button.setIcon(QIcon(str(resources_dir / "icons" / icon_name)))
            button.setIconSize(QSize(22, 22))
            button.setToolTip(label)
            button.clicked.connect(lambda checked, value=module_id: self.module_changed.emit(value))
            self._group.addButton(button)
            self._buttons[module_id] = button
            layout.addWidget(button)
            if module_id == "qpcr":
                button.setChecked(True)

        layout.addStretch(1)
        self._apply_label_mode()

    def button_for_module(self, module_id: str) -> ModuleNavButton:
        return self._buttons[module_id]

    def set_active_module(self, module_id: str) -> None:
        button = self._buttons.get(module_id)
        if button is not None:
            button.setChecked(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_label_mode()

    def _apply_label_mode(self) -> None:
        icon_only = self.width() < ICON_ONLY_WIDTH
        compact = self.width() < COMPACT_LABEL_WIDTH
        icon_size = QSize(18, 18) if sys.platform in {'darwin', 'win32'} else (QSize(22, 22) if icon_only else (QSize(20, 20) if compact else QSize(22, 22)))
        for module_id, button in self._buttons.items():
            full_label = tr(MODULE_LABEL_KEYS[module_id])
            compact_label = tr(COMPACT_LABELS[module_id])
            button.setText("" if icon_only else (compact_label if compact else full_label))
            button.setToolTip(full_label)
            button.setIconSize(icon_size)
            self._fit_button_text(button, compact=compact and not icon_only)

    def retranslate(self) -> None:
        self._apply_label_mode()

    def _fit_button_text(self, button: ModuleNavButton, compact: bool) -> None:
        font = button.font()
        if sys.platform in {'darwin', 'win32'}:
            font.setPixelSize(12)
            button.setFont(font)
            if button.fontMetrics().horizontalAdvance(button.text()) > max(34, self.width()-4):
                font.setPixelSize(11)
                button.setFont(font)
            button.update()
            return
        if not compact or not button.text():
            font.setPointSizeF(10.0)
            button.setFont(font)
            button.update()
            return
        target_size = COMPACT_FONT_SIZE_PT
        measured_width = button.width() if button.width() > 0 else self.width()
        available_width = max(34, min(measured_width, self.width()) - 4)
        while target_size > MIN_COMPACT_FONT_SIZE_PT:
            font.setPointSizeF(target_size)
            button.setFont(font)
            if button.fontMetrics().horizontalAdvance(button.text()) <= available_width:
                button.update()
                return
            target_size -= 0.5
        font.setPointSizeF(MIN_COMPACT_FONT_SIZE_PT)
        button.setFont(font)
        button.update()

