from __future__ import annotations
import sys
from PyQt6 import sip

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from core.utils.i18n import bind_i18n, retranslate_bound_widgets


MIN_ZOOM = 0.10
MAX_ZOOM = 8.0
ZOOM_STEP = 1.15
from ui.widgets.canvas_presentation import FIT_VIEWPORT_MARGIN_PX


def clamp_zoom(factor: float) -> float:
    return max(MIN_ZOOM, min(MAX_ZOOM, float(factor)))


def actual_zoom_factor(factor: float) -> float:
    value = abs(float(factor))
    return value if value else 1.0


def graphics_view_zoom_factor(view) -> float:
    return actual_zoom_factor(view.transform().m11())


def next_manual_zoom(current: float, factor: float) -> float:
    current = actual_zoom_factor(current)
    step = float(factor)
    if step == 1.0:
        return current
    target = current * step
    if step > 1.0:
        if current >= MAX_ZOOM:
            return current
        return min(MAX_ZOOM, target)
    if current <= MIN_ZOOM:
        return current
    return max(MIN_ZOOM, target)


def format_zoom_percent(factor: float) -> str:
    return f"{round(actual_zoom_factor(factor) * 100):d}%"


def _viewport_point(anchor) -> QPoint:
    if isinstance(anchor, QPoint):
        return QPoint(anchor)
    if isinstance(anchor, QPointF):
        return anchor.toPoint()
    if hasattr(anchor, "x") and hasattr(anchor, "y"):
        return QPoint(int(anchor.x()), int(anchor.y()))
    x, y = anchor
    return QPoint(int(x), int(y))


def zoom_graphics_view_by(view, factor: float, anchor=None) -> float:
    current = graphics_view_zoom_factor(view)
    target = next_manual_zoom(current, factor)
    if current <= 0 or abs(target - current) < 1e-12:
        return current
    scale = target / current
    if anchor is None:
        view.scale(scale, scale)
        return graphics_view_zoom_factor(view)
    point = _viewport_point(anchor)
    before = view.mapToScene(point)
    view.scale(scale, scale)
    after = view.mapToScene(point)
    view.translate(after.x() - before.x(), after.y() - before.y())
    return graphics_view_zoom_factor(view)


def set_graphics_view_zoom(view, factor: float) -> float:
    target = actual_zoom_factor(factor)
    view.resetTransform()
    view.scale(target, target)
    return graphics_view_zoom_factor(view)


def fit_graphics_view_to_rect(view, rect: QRectF, margin_px: int = FIT_VIEWPORT_MARGIN_PX) -> float:
    if rect is None or rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return graphics_view_zoom_factor(view)
    viewport = view.viewport()
    visible = viewport.rect() if viewport is not None else view.rect()
    if visible.width() <= 0 or visible.height() <= 0:
        view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        return graphics_view_zoom_factor(view)
    margin = max(0, int(margin_px))
    available_width = max(1.0, float(visible.width() - 2 * margin))
    available_height = max(1.0, float(visible.height() - 2 * margin))
    zoom = min(available_width / float(rect.width()), available_height / float(rect.height()))
    if zoom <= 0:
        return graphics_view_zoom_factor(view)
    view.resetTransform()
    view.scale(zoom, zoom)
    view.centerOn(rect.center())
    return graphics_view_zoom_factor(view)


class CanvasZoomControls(QWidget):
    fit_requested = pyqtSignal()
    zoom_out_requested = pyqtSignal()
    zoom_in_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasZoomControls")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(6)

        self.fit_button = bind_i18n(QPushButton(), "zoom_fit")
        bind_i18n(self.fit_button, "zoom_fit_tooltip", "toolTip")
        self.zoom_out_button = QPushButton("-")
        bind_i18n(self.zoom_out_button, "zoom_out", "toolTip")
        self.zoom_in_button = QPushButton("+")
        bind_i18n(self.zoom_in_button, "zoom_in", "toolTip")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("DimLabel")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setMinimumWidth(42)
        if sys.platform in {'darwin', 'win32'}:
            from ui.widgets.studio_icons import set_studio_icon
            for button, name in ((self.fit_button, 'fit'), (self.zoom_out_button, 'minus'),
                                 (self.zoom_in_button, 'plus')):
                button.setProperty('studioZoom', True)
                set_studio_icon(button, name, icon_only=True)

        self.fit_button.setFixedSize(38, 26)
        self.zoom_out_button.setFixedSize(28, 26)
        self.zoom_in_button.setFixedSize(28, 26)
        for button in (self.fit_button, self.zoom_out_button, self.zoom_in_button):
            button.setStyleSheet("min-width: 0px; padding: 0px 3px;")
            button.setFixedHeight(26)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            if sys.platform in {'darwin', 'win32'}:
                button.setFixedSize(26, 26)
                button.setStyleSheet('min-width:0px; padding:0px;')

        self.fit_button.clicked.connect(self.fit_requested)
        self.zoom_out_button.clicked.connect(self.zoom_out_requested)
        self.zoom_in_button.clicked.connect(self.zoom_in_requested)

        layout.addStretch(1)
        layout.addWidget(self.fit_button)
        layout.addWidget(self.zoom_out_button)
        layout.addWidget(self.zoom_label)
        layout.addWidget(self.zoom_in_button)

    def retranslate(self) -> None:
        retranslate_bound_widgets(self)
        if sys.platform in {'darwin', 'win32'}:
            self.fit_button.setText('')
            self.fit_button.setAccessibleName(self.fit_button.toolTip())
            self.zoom_out_button.setAccessibleName(self.zoom_out_button.toolTip())
            self.zoom_in_button.setAccessibleName(self.zoom_in_button.toolTip())
        bar=getattr(self,'layout_tools_bar',None)
        if bar is not None and not sip.isdeleted(bar):bar.retranslate()

    def enable_object_controls(self, interaction):
        self.object_controls = QWidget(self)
        row = QHBoxLayout(self.object_controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.marquee_button = bind_i18n(QPushButton(), 'canvas_force_marquee')
        bind_i18n(self.marquee_button, 'canvas_force_marquee_tip', 'toolTip')
        self.marquee_button.setCheckable(True)
        self.marquee_button.setFixedHeight(26)
        self.contains_button = bind_i18n(QCheckBox(), 'canvas_contains_only')
        bind_i18n(self.contains_button, 'canvas_contains_only_tip', 'toolTip')
        self.snap_button = bind_i18n(QCheckBox(), 'canvas_light_snap')
        bind_i18n(self.snap_button, 'canvas_light_snap_tip', 'toolTip')
        for control in (self.marquee_button, self.contains_button, self.snap_button):
            row.addWidget(control)
        self.marquee_button.toggled.connect(lambda value: self._set_edit_option(interaction, 'force_marquee', value))
        self.contains_button.toggled.connect(lambda value: self._set_edit_option(interaction, 'contains_only', value))
        self.snap_button.toggled.connect(lambda value: self._set_edit_option(interaction, 'snap_enabled', value))
        self.layout().insertWidget(0, self.object_controls)

    def _set_edit_option(self, interaction, name, value):
        interaction.cancel()
        editing = interaction.editing()
        if editing is not None:
            editing.finish_edit()
        setattr(interaction, name, value)
        interaction.view.setFocus()

    def set_zoom_factor(self, factor: float) -> None:
        self.zoom_label.setText(format_zoom_percent(factor))

