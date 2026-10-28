from __future__ import annotations

import os

from PyQt6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from core.models import Project
from core.models.layout_editor import LayoutCanvasSpec, LayoutObject
from ui.widgets.canvas_zoom import MIN_ZOOM, ZOOM_STEP, graphics_view_zoom_factor
from ui.widgets.composer_canvas import ComposerCanvas
from ui.widgets.fluoro_canvas import FluorescenceCanvas
from ui.widgets.layout_editor_canvas import LayoutEditorCanvas
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.wb_canvas import WBCanvas


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _ctrl_wheel(view: QGraphicsView, pos: QPoint, angle_y: int) -> None:
    viewport = view.viewport()
    event = QWheelEvent(
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        QPoint(0, 0),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(viewport, event)
    _APP.processEvents()


def _plain_wheel(view: QGraphicsView, pos: QPoint, angle_y: int) -> None:
    viewport = view.viewport()
    event = QWheelEvent(
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        QPoint(0, 0),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(viewport, event)
    _APP.processEvents()


def _show(canvas, width: int = 420, height: int = 320):
    canvas.resize(width, height)
    canvas.show()
    _APP.processEvents()
    QTest.qWait(30)
    _APP.processEvents()
    return canvas


def _large_scene(canvas, width: float = 12000.0, height: float = 9000.0) -> QGraphicsRectItem:
    item = QGraphicsRectItem(QRectF(0, 0, width, height))
    item.setPos(24, 32)
    canvas.scene.addItem(item)
    canvas.scene.setSceneRect(QRectF(0, 0, width, height))
    _APP.processEvents()
    return item


def _scene_canvases():
    composer = ComposerCanvas()
    composer.set_project(Project(title="foundation"))
    layout = LayoutEditorCanvas()
    layout.set_layout_objects(LayoutCanvasSpec("layout", 1600, 1200), [])
    return [
        ("wb", WBCanvas()),
        ("fluoro", FluorescenceCanvas()),
        ("qpcr", QPCRLayoutCanvas()),
        ("layout", layout),
        ("composer", composer),
    ]


def test_graphics_view_zoom_factor_reports_unclamped_fit_scale():
    view = QGraphicsView()
    view.setScene(QGraphicsScene())
    view.resize(200, 160)
    view.show()
    _APP.processEvents()
    view.scene().setSceneRect(QRectF(0, 0, 8000, 6000))
    view.fitInView(view.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
    factor = graphics_view_zoom_factor(view)
    actual = abs(float(view.transform().m11()))
    assert actual < MIN_ZOOM
    assert abs(factor - actual) < 1e-9
    view.close()


def test_zoom_controls_display_actual_percent_below_minimum():
    from ui.widgets.canvas_zoom import CanvasZoomControls

    controls = CanvasZoomControls()
    controls.set_zoom_factor(0.04)
    assert controls.zoom_label.text() == "4%"
    controls.close()


def test_zero_delta_ctrl_wheel_does_not_zoom():
    canvas = _show(WBCanvas())
    _large_scene(canvas)
    canvas.fit_to_canvas()
    before = float(canvas._zoom_factor)
    _ctrl_wheel(canvas.view, canvas.view.viewport().rect().center(), 0)
    assert canvas._zoom_factor == before
    canvas.close()


def test_plain_wheel_does_not_zoom():
    canvas = _show(WBCanvas())
    _large_scene(canvas)
    canvas.fit_to_canvas()
    before = float(canvas._zoom_factor)
    _plain_wheel(canvas.view, canvas.view.viewport().rect().center(), 120)
    assert abs(canvas._zoom_factor - before) < 1e-9
    canvas.close()


def test_large_content_fit_below_minimum_and_zoom_out_does_not_enlarge():
    canvas = _show(WBCanvas(), 240, 180)
    item = _large_scene(canvas)
    pos = QPointF(item.pos())
    canvas.fit_to_canvas()
    assert canvas._fit_mode
    assert canvas._zoom_factor < MIN_ZOOM
    assert canvas.zoom_controls.zoom_label.text().endswith("%")
    assert int(canvas.zoom_controls.zoom_label.text()[:-1]) < 10
    before = float(canvas._zoom_factor)
    canvas.zoom_by(1 / ZOOM_STEP)
    assert canvas._zoom_factor <= before + 1e-9
    assert canvas._zoom_factor < MIN_ZOOM
    assert item.pos() == pos
    canvas.close()


def test_ctrl_wheel_keeps_cursor_scene_point():
    canvas = _show(WBCanvas(), 480, 360)
    _large_scene(canvas)
    canvas.fit_to_canvas()
    canvas.zoom_by(ZOOM_STEP)
    point = QPoint(90, 70)
    before = canvas.view.mapToScene(point)
    before_zoom = float(canvas._zoom_factor)
    _ctrl_wheel(canvas.view, point, 120)
    after = canvas.view.mapToScene(point)
    assert canvas._zoom_factor > before_zoom
    assert QLineF(before, after).length() * float(canvas._zoom_factor) < 3
    canvas.close()


def test_queued_fit_after_manual_zoom_is_ignored():
    canvas = _show(WBCanvas(), 360, 280)
    _large_scene(canvas)
    canvas.fit_to_canvas()
    canvas._apply_canvas_zoom()
    canvas.zoom_by(ZOOM_STEP)
    manual = float(canvas._zoom_factor)
    _APP.processEvents()
    QTest.qWait(30)
    _APP.processEvents()
    assert not canvas._fit_mode
    assert abs(canvas._zoom_factor - manual) < 1e-6
    canvas.close()


def test_view_ops_do_not_change_scene_item_geometry():
    canvases = []
    try:
        for name, canvas in _scene_canvases():
            _show(canvas)
            if name == "composer":
                item = next(item for item in canvas.scene.items() if isinstance(item, QGraphicsRectItem))
                paper = QRectF(canvas.paper_rect)
            else:
                item = _large_scene(canvas)
                paper = None
            pos = QPointF(item.pos())
            rect = QRectF(item.rect()) if hasattr(item, "rect") else None
            canvas.fit_to_canvas()
            canvas.zoom_by(ZOOM_STEP)
            _ctrl_wheel(canvas.view, QPoint(40, 40), 120)
            assert item.pos() == pos
            if rect is not None:
                assert item.rect() == rect
            if paper is not None:
                assert canvas.paper_rect == paper
            canvases.append(canvas)
    finally:
        for canvas in canvases:
            canvas.close()
            _APP.processEvents()


def test_all_scene_canvases_share_one_live_controller_and_zoom_controls():
    from ui.widgets.canvas_view_controller import CanvasViewController

    canvases = []
    try:
        for name, canvas in _scene_canvases():
            _show(canvas)
            controller = getattr(canvas, "_view_controller", None)
            assert isinstance(controller, CanvasViewController), name
            assert canvas.zoom_controls is controller.zoom_controls
            assert canvas.view.canvas_owner is canvas
            canvas.fit_to_canvas()
            canvas.zoom_by(ZOOM_STEP)
            assert not canvas._fit_mode
            canvases.append(canvas)
    finally:
        for canvas in canvases:
            canvas.close()
            _APP.processEvents()


def test_wb_crop_event_still_emits_after_view_integration():
    canvas = _show(WBCanvas(), 400, 320)
    canvas.set_mode("roi")
    received = []
    canvas.roi_selected.connect(received.append)
    viewport = canvas.view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
    QTest.mouseMove(viewport, QPoint(120, 110))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(120, 110))
    _APP.processEvents()
    assert received
    assert received[0].width() > 3
    assert received[0].height() > 3
    canvas.close()


def test_fluorescence_roi_event_still_emits_after_view_integration():
    canvas = _show(FluorescenceCanvas(), 400, 320)
    canvas.set_roi_mode("rectangle")
    received = []
    canvas.view.scene_roi_drawn.connect(lambda mode, payload: received.append((mode, payload)))
    viewport = canvas.view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(24, 24))
    QTest.mouseMove(viewport, QPoint(110, 90))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(110, 90))
    _APP.processEvents()
    assert received and received[0][0] == "rectangle"
    canvas.close()


def test_backspace_edits_text_on_scene_canvases():
    from PyQt6.QtGui import QTextCursor

    canvases = []
    try:
        for name, canvas in _scene_canvases():
            if name == "fluoro":
                canvas.current_step = 4
            item = SharedTextItem(LayoutObject(f"{name}-text", "text", 0, 0, 150, 40, text="ABC"))
            canvas.scene.addItem(item)
            _show(canvas)
            canvas.view.setFocus()
            item.setSelected(True)
            item.begin_edit()
            cursor = item.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            item.setTextCursor(cursor)
            QTest.keyClick(canvas.view, Qt.Key.Key_Backspace)
            _APP.processEvents()
            assert item.toPlainText() == "AB", name
            assert item.scene() is canvas.scene
            canvases.append(canvas)
    finally:
        for canvas in canvases:
            canvas.close()
            _APP.processEvents()


def test_space_while_editing_inserts_text_and_is_not_canvas_pan():
    from PyQt6.QtGui import QTextCursor

    canvas = _show(QPCRLayoutCanvas(), 480, 360)
    item = SharedTextItem(LayoutObject("space-text", "text", 12, 12, 160, 40, text="A"))
    canvas.scene.addItem(item)
    canvas.view.setFocus()
    item.setSelected(True)
    assert item.begin_edit()
    cursor = item.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    item.setTextCursor(cursor)
    _APP.processEvents()
    QTest.keyClick(canvas.view, Qt.Key.Key_Space)
    _APP.processEvents()
    assert item.is_editing
    assert item.toPlainText() == "A "
    assert not getattr(canvas.view, "_panning", False)
    assert not getattr(canvas.view, "_space_pressed", False)
    canvas.close()


def test_space_pan_at_fit_leaves_manual_mode_after_resize():
    canvas = _show(WBCanvas(), 400, 300)
    canvas.scene.addRect(QRectF(0, 0, 40, 30))
    canvas.scene.setSceneRect(QRectF(0, 0, 40, 30))
    canvas.fit_to_canvas()
    assert canvas._fit_mode
    canvas.view.setFocus()
    QTest.keyPress(canvas.view, Qt.Key.Key_Space)
    viewport = canvas.view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    QTest.mouseMove(viewport, QPoint(48, 36))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(48, 36))
    QTest.keyRelease(canvas.view, Qt.Key.Key_Space)
    _APP.processEvents()
    assert not canvas._fit_mode
    zoom = float(canvas._zoom_factor)
    canvas.resize(520, 380)
    _APP.processEvents()
    QTest.qWait(40)
    _APP.processEvents()
    assert not canvas._fit_mode
    assert abs(canvas._zoom_factor - zoom) < 1e-6
    canvas.close()


def test_space_release_restores_cursor_without_roi_mode_setter():
    canvas = _show(FluorescenceCanvas(), 360, 280)
    canvas.set_roi_mode("scale_line")
    calls: list[str] = []
    original = canvas.view.set_roi_mode

    def wrapped(mode: str) -> None:
        calls.append(mode)
        original(mode)

    canvas.view.set_roi_mode = wrapped  # type: ignore[method-assign]
    canvas.view.setFocus()
    QTest.keyPress(canvas.view, Qt.Key.Key_Space)
    QTest.keyRelease(canvas.view, Qt.Key.Key_Space)
    _APP.processEvents()
    assert calls == []
    assert canvas.view.roi_mode == "scale_line"
    canvas.close()


def test_scene_canvas_property_does_not_rename_module_views():
    from ui.widgets.canvas_graphics_adapter import SCENE_CANVAS_PROPERTY

    fluoro = FluorescenceCanvas()
    qpcr = QPCRLayoutCanvas()
    layout = LayoutEditorCanvas()
    wb = WBCanvas()
    composer = ComposerCanvas()
    try:
        assert fluoro.view.property(SCENE_CANVAS_PROPERTY) is True
        assert qpcr.view.property(SCENE_CANVAS_PROPERTY) is True
        assert layout.view.property(SCENE_CANVAS_PROPERTY) is True
        assert fluoro.view.objectName() != "ComposerView"
        assert qpcr.view.objectName() != "ComposerView"
        assert layout.view.objectName() != "ComposerView"
        assert wb.view.objectName() == "WBCanvasView"
        assert composer.view.objectName() == "ComposerView"
    finally:
        for canvas in (fluoro, qpcr, layout, wb, composer):
            canvas.close()


def test_layout_editor_export_bounds_and_pixels_survive_view_navigation():
    canvas = _show(LayoutEditorCanvas(), 420, 320)
    spec = LayoutCanvasSpec("export-nav", 320, 240, background_color="#FFFFFF")
    canvas.set_layout_objects(spec, [LayoutObject("label", "text", 16, 20, 120, 36, text="Nav")])
    source = QRectF(canvas.scene.sceneRect())
    before = canvas.render_full_layout()
    canvas.fit_to_canvas()
    canvas.zoom_by(ZOOM_STEP)
    canvas.view.horizontalScrollBar().setValue(canvas.view.horizontalScrollBar().value() + 12)
    canvas.view.verticalScrollBar().setValue(canvas.view.verticalScrollBar().value() + 8)
    _APP.processEvents()
    assert canvas.scene.sceneRect() == source
    after = canvas.render_full_layout()
    assert after.size() == before.size()
    assert after == before
    canvas.close()


def test_owned_fit_timer_dies_with_view():
    from PyQt6 import sip

    canvas = _show(WBCanvas(), 320, 240)
    _large_scene(canvas)
    canvas.fit_to_canvas()
    timer = canvas._view_controller._fit_timer
    assert timer.parent() is canvas.view
    canvas.close()
    canvas.deleteLater()
    _APP.processEvents()
    QTest.qWait(20)
    _APP.processEvents()
    assert sip.isdeleted(timer)
