import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QPixmap, QWheelEvent, QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from ui.widgets.preview_canvas import PreviewCanvas

_APP = QApplication.instance() or QApplication([])


def show(widget, width=700, height=550):
    widget.resize(width, height)
    widget.show()
    QTest.qWait(100)
    return widget


def wheel(widget, pos, delta=120):
    event = QWheelEvent(QPointF(pos), QPointF(widget.mapToGlobal(pos)), QPoint(),
                        QPoint(0, delta), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(widget, event)
    QTest.qWait(10)


def test_preview_cursor_keeps_same_source_pixel_over_repeated_zoom():
    canvas = show(PreviewCanvas())
    pix = QPixmap(1600, 1200)
    pix.fill(Qt.GlobalColor.white)
    canvas.set_pixmap(pix, enable_roi_selection=True)
    QTest.qWait(100)
    label = canvas.image_label
    cursor = label.mapToGlobal(label._pixmap_rect().topLeft() + QPoint(180,100))
    before = label._map_label_point_to_source(label.mapFromGlobal(cursor))
    for delta in [120]*5 + [-120]*5:
        wheel(label, label.mapFromGlobal(cursor), delta)
    after = label._map_label_point_to_source(label.mapFromGlobal(cursor))
    assert (after - before).manhattanLength() <= 8
    assert canvas._source_pixmap.size() == pix.size()
    canvas.close()


def test_preview_space_pan_works_at_fit_without_selecting_roi():
    canvas = show(PreviewCanvas())
    pix = QPixmap(800,600)
    pix.fill(Qt.GlobalColor.white)
    canvas.set_pixmap(pix, enable_roi_selection=True)
    canvas.fit_to_canvas()
    QTest.qWait(100)
    label = canvas.image_label
    received = []
    canvas.roi_selected.connect(received.append)
    before = label.mapToGlobal(QPoint())
    point = label.rect().center()
    QTest.keyPress(label, Qt.Key.Key_Space)
    QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(label, point + QPoint(40,25))
    QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=point + QPoint(40,25))
    QTest.keyRelease(label, Qt.Key.Key_Space)
    assert not received
    assert not canvas._fit_mode
    assert (label.mapToGlobal(QPoint()) - before).manhattanLength() >= 40
    zoom = canvas._zoom_factor
    canvas.resize(680,520)
    QTest.qWait(100)
    assert canvas._zoom_factor == zoom
    canvas.close()


def test_matplotlib_page_and_export_dimensions_do_not_change_with_view(tmp_path):
    from matplotlib.figure import Figure
    from PyQt6.QtGui import QImage
    figure = Figure(figsize=(6,4), dpi=100)
    axes = figure.add_subplot()
    axes.plot([1,2],[3,4])
    size = tuple(figure.get_size_inches())
    bounds = axes.get_position().bounds
    figure.savefig(tmp_path / "before.png", dpi=100)
    canvas = show(PreviewCanvas())
    canvas.set_figure(figure)
    QTest.qWait(100)
    for _ in range(4):
        canvas.zoom_by(1.15)
    QTest.qWait(50)
    assert tuple(figure.get_size_inches()) == size
    assert axes.get_position().bounds == bounds
    figure.savefig(tmp_path / "after.png", dpi=100)
    assert QImage(str(tmp_path / "after.png")) == QImage(str(tmp_path / "before.png"))
    canvas.close()


def test_style_source_can_pan_at_fit_without_changing_crop():
    from ui.local_style_extraction.source_canvas import SourceCanvas
    canvas = show(SourceCanvas(), 400,300)
    canvas._pixmap = QPixmap(800,600)
    canvas._pixmap.fill(Qt.GlobalColor.white)
    canvas.set_crop([.2,.2,.4,.4])
    canvas.fit_to_canvas()
    QTest.qWait(20)
    before = canvas._norm_from_pos(QPointF(200,150))
    canvas._view_controller.begin_pan()
    canvas._view_controller.pan_by(40,20)
    QTest.qWait(20)
    assert canvas._norm_from_pos(QPointF(200,150)) != before
    assert canvas.crop() == [.2,.2,.4,.4]
    assert canvas._zoom_factor == pytest.approx(canvas._page_rect.width() / (800*canvas._view_window[2]))
    canvas.close()


def test_style_preview_wheel_updates_toolbar_and_keeps_cursor():
    from ui.local_style_extraction.app import PreviewBoard
    board = show(PreviewBoard(), 520,650)
    pix = QPixmap(800,600)
    pix.fill(Qt.GlobalColor.white)
    view = board.styled
    view._pix = pix
    view.fit_to_canvas()
    QTest.qWait(30)
    wheel(view, view.rect().center())
    assert board.zoom_controls.zoom_label.text() == f"{round(view._zoom_factor * 100)}%"
    board.close()


def test_style_source_same_page_refresh_keeps_manual_view(tmp_path):
    from ui.local_style_extraction.source_canvas import SourceCanvas
    pix = QPixmap(800,600)
    pix.fill(Qt.GlobalColor.white)
    path = tmp_path / 'page.png'
    pix.save(str(path))
    canvas = show(SourceCanvas(), 400,300)
    canvas.set_page_image(str(path))
    canvas.zoom_by(2)
    canvas._view_controller.pan_by(30,15)
    view = list(canvas._view_window)
    canvas.set_page_image(str(path))
    assert not canvas._fit_mode
    assert canvas._view_window == pytest.approx(view, abs=1e-12)
    canvas.close()


def test_style_both_renderers_keep_offcenter_cursor():
    from ui.local_style_extraction.source_canvas import SourceCanvas
    from ui.local_style_extraction.app import ScaledView
    for canvas in (SourceCanvas(), ScaledView('preview')):
        show(canvas, 400,300)
        pix = QPixmap(800,600)
        pix.fill(Qt.GlobalColor.white)
        if isinstance(canvas, SourceCanvas):
            canvas._pixmap = pix
            def point():
                return canvas._page_point_at(QPointF(150,110))
        else:
            canvas._pix = pix
            def point():
                r = canvas.image_rect()
                return (150-r.x())/r.width(), (110-r.y())/r.height()
        canvas.fit_to_canvas()
        before = point()
        for _ in range(5):
            wheel(canvas, QPoint(150,110))
        assert point() == pytest.approx(before, abs=1e-8)
        canvas.close()


def test_browser_shell_refresh_preserves_manual_zoom():
    from ui.main_window import MainWindow
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    window.set_module('viewer')
    canvas = window.image_browser_workspace.canvas
    canvas.zoom_by(2)
    zoom = canvas._zoom_factor
    window._refresh_active_canvas_layout()
    QTest.qWait(100)
    assert canvas._zoom_factor == zoom
    assert not canvas._fit_mode
    window._capture_clean_project_baseline()
    window.close()


def test_style_hidden_preview_cannot_replace_active_zoom_label():
    from ui.local_style_extraction.app import PreviewBoard
    board = show(PreviewBoard(), 520,650)
    board.baseline._pix = QPixmap(400,300)
    board.styled._pix = QPixmap(800,600)
    board._set_view(1)
    board.baseline.fit_to_canvas()
    label = board.zoom_controls.zoom_label.text()
    board.styled.fit_to_canvas()
    assert board.zoom_controls.zoom_label.text() == label
    board.close()


def test_wb_font_signals_stop_when_canvas_is_destroyed():
    from ui.main_window import MainWindow
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    window.set_module('wb')
    canvas = window.wb_canvas
    canvas.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # Qt exceptions abort a normal process; capture and assert the actual failure.
    import sys
    errors = []
    old_hook = sys.excepthook
    sys.excepthook = lambda *args: errors.append(args[1])
    try:
        with pytest.raises(RuntimeError, match="deleted"):
            window.preview_wb_stats_plot_style_live()
        window.data_panel.wb_text_font_combo.currentFontChanged.emit(QFont("Arial"))
        window.data_panel.wb_stats_font_combo.currentFontChanged.emit(QFont("Arial"))
    finally:
        sys.excepthook = old_hook
    window.active_module = 'qpcr'
    window._capture_clean_project_baseline()
    window.close()
    assert not errors, errors


@pytest.mark.parametrize('kind', ['text','line'])
def test_wb_refresh_drops_deleted_cached_selection(kind):
    from ui.main_window import MainWindow
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    scene = window.wb_canvas.scene
    item = scene.addText('A') if kind == 'text' else scene.addLine(0,0,20,0)
    setattr(window, f'_active_wb_{kind}_item', item)
    scene.clear()
    target = getattr(window, f'_current_wb_{kind}_target')()
    assert target is None
    window._capture_clean_project_baseline()
    window.close()


@pytest.mark.parametrize('kind', ['source','style_preview','wb'])
def test_focus_loss_cancels_temporary_pan(kind):
    from PyQt6.QtGui import QFocusEvent
    from ui.local_style_extraction.source_canvas import SourceCanvas
    from ui.local_style_extraction.app import ScaledView
    from ui.widgets.wb_canvas import WBCanvas
    canvas = show({'source': SourceCanvas, 'style_preview': lambda: ScaledView('preview'), 'wb': WBCanvas}[kind]())
    target = canvas.view if kind == 'wb' else canvas
    QTest.keyPress(target, Qt.Key.Key_Space)
    QApplication.sendEvent(target, QFocusEvent(QEvent.Type.FocusOut))
    nav = getattr(canvas, '_navigation', None)
    assert not (nav.space if nav else target._space_pressed)
    canvas.close()


def test_plain_wheel_scrolls_style_without_changing_zoom():
    from ui.local_style_extraction.app import ScaledView
    view = show(ScaledView('preview'), 400,300)
    view._pix = QPixmap(800,600)
    view.fit_to_canvas()
    before = view.image_rect()
    zoom = view._zoom_factor
    pos = view.rect().center()
    event = QWheelEvent(QPointF(pos),QPointF(view.mapToGlobal(pos)),QPoint(),QPoint(0,-120),
                        Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
    QApplication.sendEvent(view,event)
    assert view._zoom_factor == zoom
    assert view.image_rect().y() < before.y()
    assert not view._fit_mode
    view.close()


def test_preview_focus_into_image_does_not_drop_held_space():
    canvas = show(PreviewCanvas())
    pix = QPixmap(800,600)
    pix.fill(Qt.GlobalColor.white)
    canvas.set_pixmap(pix)
    canvas.setFocus()
    _APP.processEvents()
    QTest.keyPress(canvas,Qt.Key.Key_Space)
    canvas.image_label.setFocus()
    _APP.processEvents()
    assert canvas._navigation.space
    QTest.keyRelease(canvas.image_label,Qt.Key.Key_Space)
    canvas.close()


def test_browser_same_image_refresh_keeps_view_but_next_image_fits(tmp_path):
    from test_browser_navigation_and_calibration import browser
    workspace, paths = browser(tmp_path)
    canvas = workspace.canvas
    canvas.zoom_by(2)
    canvas._view_controller.pan_by(20,10)
    zoom = canvas._zoom_factor
    workspace.select_image(paths[0])
    QTest.qWait(20)
    assert canvas._zoom_factor == zoom
    assert not canvas._fit_mode
    workspace._navigate_image(1)
    assert canvas._fit_mode
    workspace.close()
