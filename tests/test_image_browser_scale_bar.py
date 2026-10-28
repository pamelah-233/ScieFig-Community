from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication

from core.image_browser import ScaleBarState, scale_bar_layout
from ui.widgets.image_browser_canvas import ImageBrowserCanvas, ImageBrowserScaleBarLabel
from ui.widgets.preview_canvas import PreviewCanvas, SelectableImageLabel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _mouse_event(event_type: QEvent.Type, point: QPoint) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _source_and_display() -> tuple[QPixmap, QPixmap]:
    source = QPixmap(160, 120)
    source.fill(QColor("#182028"))
    return source, source.scaled(240, 180, Qt.AspectRatioMode.KeepAspectRatio)


def test_preview_canvas_factory_defaults_to_shared_selectable_label() -> None:
    app = _app()
    canvas = PreviewCanvas()
    label = canvas._make_image_label()

    assert app is not None
    assert type(label) is SelectableImageLabel
    canvas.close()
    canvas.deleteLater()
    app.processEvents()


def test_image_browser_canvas_uses_browser_label_and_paints_overlay() -> None:
    app = _app()
    canvas = ImageBrowserCanvas()
    source = QPixmap(160, 120)
    source.fill(QColor("#182028"))
    canvas.resize(760, 560)
    canvas.set_pixmap(source, title="sample.png")
    state = ScaleBarState(
        visible=True,
        length_um=20.0,
        um_per_px=0.5,
        center_x_px=80.0,
        line_y_px=60.0,
        line_color="#12AB34",
        text_color="#AB12CD",
        font_family="DejaVu Sans",
        font_size_px=16.0,
    )
    canvas.set_scale_bar_state(state)
    canvas.show()
    app.processEvents()

    assert isinstance(canvas.image_label, ImageBrowserScaleBarLabel)
    assert canvas.image_label is not None
    pixmap_rect = canvas.image_label._pixmap_rect()
    layout = scale_bar_layout(QRectF(pixmap_rect), source.size(), state)
    grabbed = canvas.image_label.grab().toImage()
    line_point = layout.line_rect.center().toPoint()

    assert grabbed.pixelColor(line_point) == QColor("#12AB34")
    canvas.close()
    canvas.deleteLater()
    app.processEvents()


def test_default_scale_bar_is_not_clipped_when_square_image_is_fitted() -> None:
    app = _app()
    canvas = ImageBrowserCanvas()
    source = QPixmap(240, 240)
    source.fill(QColor("#182028"))
    state = ScaleBarState(visible=True, length_um=50.0, um_per_px=1.0)
    canvas.resize(450, 320)
    canvas.show()
    canvas.set_pixmap(source)
    canvas.set_scale_bar_state(state)
    canvas.fit_to_canvas()
    app.processEvents()

    assert canvas.image_label is not None
    pixmap_rect = canvas.image_label._pixmap_rect()
    assert canvas.image_label.rect().contains(pixmap_rect)
    layout = scale_bar_layout(QRectF(pixmap_rect), source.size(), state)
    grabbed = canvas.image_label.grab().toImage()
    assert grabbed.pixelColor(layout.line_rect.center().toPoint()) == QColor("#FFFFFF")
    canvas.close()
    canvas.deleteLater()
    app.processEvents()


def test_image_browser_scale_bar_drag_emits_source_pixel_position_and_stays_in_bounds() -> None:
    app = _app()
    label = ImageBrowserScaleBarLabel()
    source, display = _source_and_display()
    label.resize(display.size())
    label.set_source_pixmap(source, display)
    state = ScaleBarState(
        visible=True,
        length_um=20.0,
        um_per_px=0.5,
        center_x_px=80.0,
        line_y_px=60.0,
        line_color="#FFFFFF",
        text_color="#FFFFFF",
    )
    label.set_scale_bar_state(state)
    moved: list[ScaleBarState] = []
    label.scale_bar_moved.connect(moved.append)
    label.show()
    app.processEvents()

    layout = scale_bar_layout(QRectF(label._pixmap_rect()), source.size(), state)
    start = layout.bounds.center().toPoint()
    end = start + QPoint(24, 12)
    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end))
    label.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, end))

    assert moved
    assert moved[-1].center_x_px == 96.0
    assert moved[-1].line_y_px == 68.0
    final_layout = scale_bar_layout(QRectF(label._pixmap_rect()), source.size(), moved[-1])
    assert QRectF(label._pixmap_rect()).contains(final_layout.bounds)
    label.close()
    label.deleteLater()
    app.processEvents()


def test_browser_scale_bar_source_position_survives_fit_and_zoom() -> None:
    app = _app()
    canvas = ImageBrowserCanvas()
    source = QPixmap(160, 120)
    source.fill(QColor("#182028"))
    state = ScaleBarState(visible=True, length_um=20.0, um_per_px=0.5, center_x_px=80.0, line_y_px=60.0)
    canvas.resize(760, 560)
    canvas.set_pixmap(source)
    canvas.set_scale_bar_state(state)
    canvas.show()
    app.processEvents()
    canvas.fit_to_canvas()
    canvas.zoom_by(2.0)
    app.processEvents()

    assert canvas.scale_bar_state.center_x_px == 80.0
    assert canvas.scale_bar_state.line_y_px == 60.0
    assert canvas.image_label is not None
    assert canvas.image_label.scale_bar_state.center_x_px == 80.0
    assert canvas.image_label.scale_bar_state.line_y_px == 60.0
    canvas.close()
    canvas.deleteLater()
    app.processEvents()
