from __future__ import annotations

import os

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from ui.widgets.canvas_zoom import ZOOM_STEP
from ui.widgets.image_browser_canvas import ImageBrowserCanvas
from ui.widgets.preview_canvas import PreviewCanvas


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _ctrl_wheel(widget, pos: QPoint, angle_y: int) -> None:
    event = QWheelEvent(
        QPointF(pos),
        QPointF(widget.mapToGlobal(pos)),
        QPoint(0, 0),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(widget, event)
    _APP.processEvents()


def _show(widget, width=480, height=360):
    widget.resize(width, height)
    widget.show()
    _APP.processEvents()
    QTest.qWait(30)
    _APP.processEvents()
    return widget


def test_preview_ctrl_wheel_on_scroll_viewport_zooms():
    canvas = _show(PreviewCanvas())
    canvas.fit_to_canvas()
    before = float(canvas._zoom_factor)
    _ctrl_wheel(canvas.scroll_area.viewport(), QPoint(40, 40), 120)
    assert canvas._zoom_factor > before
    canvas.close()


def test_preview_zero_delta_wheel_does_not_zoom():
    canvas = _show(PreviewCanvas())
    canvas.fit_to_canvas()
    before = float(canvas._zoom_factor)
    _ctrl_wheel(canvas.scroll_area.viewport(), QPoint(40, 40), 0)
    assert canvas._zoom_factor == before
    canvas.close()


def test_preview_queued_fit_does_not_override_manual_zoom():
    canvas = _show(PreviewCanvas(), 400, 300)
    canvas.fit_to_canvas()
    canvas.schedule_fit_after_layout()
    canvas.zoom_by(ZOOM_STEP)
    manual = float(canvas._zoom_factor)
    _APP.processEvents()
    QTest.qWait(90)
    _APP.processEvents()
    assert not canvas._fit_mode
    assert abs(canvas._zoom_factor - manual) < 1e-6
    canvas.close()


def test_preview_keeps_source_pixmap_and_manual_zoom_on_same_canvas_update():
    canvas = _show(PreviewCanvas(), 500, 380)
    source = QPixmap(160, 120)
    source.fill(QColor("#204060"))
    canvas.set_pixmap(source)
    canvas.zoom_by(ZOOM_STEP)
    zoom = float(canvas._zoom_factor)
    other = QPixmap(160, 120)
    other.fill(QColor("#602040"))
    canvas.set_pixmap(other)
    assert canvas._source_pixmap.size() == source.size()
    assert not canvas._fit_mode
    assert abs(canvas._zoom_factor - zoom) < 1e-6
    canvas.close()


def test_matplotlib_axes_and_data_survive_canvas_navigation():
    from matplotlib.figure import Figure

    figure = Figure()
    axes = figure.add_subplot(111)
    axes.plot([0.0, 1.0, 2.0], [1.0, 3.0, 2.0])
    xlim = axes.get_xlim()
    ylim = axes.get_ylim()
    xy = axes.lines[0].get_xydata().copy()
    canvas = _show(PreviewCanvas(), 520, 400)
    canvas.set_figure(figure)
    canvas.zoom_by(ZOOM_STEP)
    canvas.fit_to_canvas()
    assert axes.get_xlim() == xlim
    assert axes.get_ylim() == ylim
    assert (axes.lines[0].get_xydata() == xy).all()
    canvas.close()


def test_style_source_zoom_does_not_change_crop_or_evidence():
    from ui.local_style_extraction.source_canvas import SourceCanvas

    canvas = _show(SourceCanvas(), 280, 280)
    canvas._pixmap = QPixmap(120, 90)
    canvas._pixmap.fill(QColor("#eeeeee"))
    crop = [0.15, 0.2, 0.4, 0.35]
    evidence = [[0.2, 0.25, 0.1, 0.1]]
    canvas.set_crop(crop)
    canvas.set_evidence(evidence)
    canvas.zoom_by(ZOOM_STEP)
    canvas.pan_view(12, 8)
    assert canvas.crop() == crop
    assert canvas._evidence == evidence
    canvas.close()


def test_style_preview_scaled_view_keeps_source_pixels():
    from ui.local_style_extraction.app import ScaledView

    view = _show(ScaledView("preview"), 240, 180)
    pix = QPixmap(64, 48)
    pix.fill(QColor("#112233"))
    view._pix = QPixmap(pix)
    key = view.source_pixmap().cacheKey()
    size = view.source_pixmap().size()
    view.zoom_by(ZOOM_STEP)
    view.fit_to_canvas()
    assert view.source_pixmap().cacheKey() == key
    assert view.source_pixmap().size() == size
    view.close()


def test_browser_navigation_arrows_are_viewport_only():
    canvas = _show(ImageBrowserCanvas(), 500, 360)
    source = QPixmap(80, 80)
    source.fill(QColor("#182028"))
    canvas.set_pixmap(source)
    canvas.set_navigation_state(True, True, visible=True)
    _APP.processEvents()
    assert canvas.previous_button.parent() is canvas.scroll_area.viewport()
    assert canvas.next_button.parent() is canvas.scroll_area.viewport()
    assert canvas.previous_button.parent() is not canvas.paper
    canvas.close()
