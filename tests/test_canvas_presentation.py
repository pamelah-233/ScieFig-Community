import os
import sys
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame
from ui.widgets.wb_canvas import WBCanvas
from ui.widgets.fluoro_canvas import FluorescenceCanvas
from ui.widgets.preview_canvas import PreviewCanvas
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas

_APP = QApplication.instance() or QApplication([])


def scene_image(scene):
    image = QImage(720,520,QImage.Format.Format_ARGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    scene.render(painter)
    painter.end()
    return image


def test_wb_zoom_uses_shared_workflow_row_not_domain_sidebar_and_returns_on_switch():
    from ui.main_window import MainWindow
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    window.set_module('wb')
    canvas = window.wb_canvas
    # The compact header intentionally hosts live zoom controls beside workflow steps.
    assert window.v9_shell.workflow_row.isAncestorOf(canvas.zoom_controls)
    assert not window.wb_canvas_toolbar_panel.isAncestorOf(canvas.zoom_controls)
    window.set_module('viewer')
    assert canvas.isAncestorOf(canvas.zoom_controls)
    window._capture_clean_project_baseline()
    window.close()


@pytest.mark.parametrize('height', [600,800])
def test_equal_content_has_equal_fit_in_wb_fluoro_and_preview(height):
    from ui.panels.ihc_workspace import IHCPreviewCanvas
    from ui.panels.brightfield_workspace import BrightfieldPreviewCanvas
    pix = QPixmap(640,420)
    pix.fill(QColor('#537f91'))
    wb = WBCanvas()
    tools = wb.take_toolbar_widget()
    fluoro = FluorescenceCanvas()
    qpcr = QPCRLayoutCanvas()
    preview = PreviewCanvas()
    ihc, brightfield = IHCPreviewCanvas(), BrightfieldPreviewCanvas()
    widths, heights = [], []
    for canvas in (wb,fluoro,qpcr,preview,ihc,brightfield):
        canvas.resize(800,height)
        if isinstance(canvas, PreviewCanvas):
            canvas.set_pixmap(pix)
        else:
            canvas.scene.clear()
            canvas.scene.addPixmap(pix)
            if canvas is wb:
                canvas.scene.setSceneRect(QRectF(-80,-80,840,620))
            elif canvas is fluoro:
                canvas._ensure_white_canvas()
            else:
                canvas.scene.setSceneRect(QRectF(-40,-40,720,500))
        canvas.show()
        QTest.qWait(60)
        canvas.fit_to_canvas()
        QTest.qWait(30)
        if isinstance(canvas, PreviewCanvas):
            size = canvas.image_label.pixmap().size()
            widths.append(size.width()); heights.append(size.height())
        else:
            widths.append(640*canvas.view.transform().m11())
            heights.append(420*canvas.view.transform().m22())
    assert max(widths)-min(widths) <= 3, widths
    assert max(heights)-min(heights) <= 3, heights
    for canvas in (wb,fluoro,qpcr,preview,ihc,brightfield):
        canvas.close()
    tools.close()


def test_scene_surround_is_not_export_background():
    canvas = FluorescenceCanvas()
    pix = QPixmap(640,420);pix.fill(QColor('#537f91'))
    canvas.scene.addPixmap(pix)
    canvas._ensure_white_canvas()
    export_before = scene_image(canvas.scene)
    document_before = QRectF(canvas.scene.sceneRect())
    canvas.resize(800,600);canvas.show();QTest.qWait(50)
    canvas.fit_to_canvas();QTest.qWait(30)
    viewport = canvas.view.viewport().grab().toImage()
    assert viewport.pixelColor(3,3).name() == ('#171717' if sys.platform in {'darwin', 'win32'} else '#211c17')
    assert canvas.scene.sceneRect() == document_before
    assert scene_image(canvas.scene) == export_before
    canvas.close()


def test_content_frame_stays_fixed_after_item_move_without_refitting():
    canvas = FluorescenceCanvas()
    pix = QPixmap(100,100); pix.fill(QColor('#537f91'))
    item = canvas.scene.addPixmap(pix)
    canvas._ensure_white_canvas()
    canvas.resize(800,600); canvas.show(); QTest.qWait(50)
    canvas.fit_to_canvas(); canvas._fit_mode = False
    paper_before = canvas._view_controller.backend.content_rect()
    position = canvas.view.mapFromScene(QPointF(-3,50))
    before = canvas.view.viewport().grab().toImage().pixelColor(position).name()
    item.setPos(100,0); QTest.qWait(30)
    after = canvas.view.viewport().grab().toImage().pixelColor(position).name()
    assert before == '#ffffff'
    assert after == '#ffffff'
    assert canvas._view_controller.backend.content_rect() == paper_before
    canvas.close()


@pytest.mark.parametrize('canvas_type', [WBCanvas, FluorescenceCanvas, QPCRLayoutCanvas])
def test_object_move_resize_and_same_document_refresh_keep_paper_rect(canvas_type):
    canvas = canvas_type()
    canvas.resize(800,600)
    canvas._view_controller.set_content_identity('fixed-paper-test')
    pix = QPixmap(160,100);pix.fill(QColor('#537f91'))
    item = canvas.scene.addPixmap(pix)
    canvas.scene.setSceneRect(QRectF(-20,-20,200,140))
    canvas.show();QTest.qWait(30)
    canvas.fit_to_canvas()
    backend = canvas._view_controller.backend
    paper = backend.content_rect()
    paper_on_screen = canvas.view.mapFromScene(paper).boundingRect()
    item.setPos(130,80)
    item.setScale(2)
    QTest.qWait(20)
    assert backend.content_rect() == paper
    assert canvas.view.mapFromScene(paper).boundingRect() == paper_on_screen
    # Source layouts rebuild scene items when changing style, undoing or pasting.
    canvas.scene.clear()
    replacement = canvas.scene.addPixmap(pix)
    replacement.setPos(300,240)
    canvas.scene.setSceneRect(QRectF(280,220,200,140))
    canvas._view_controller.set_content_identity('fixed-paper-test')
    canvas._apply_canvas_zoom()
    QTest.qWait(20)
    assert backend.content_rect() == paper
    # A different document/image may establish its own initial paper.
    canvas._view_controller.set_content_identity('another-document')
    canvas._apply_canvas_zoom()
    QTest.qWait(20)
    assert backend.content_rect() != paper
    canvas.close()


def test_image_letterbox_uses_shared_paper_not_a_dark_label_background():
    from ui.widgets.preview_canvas import SelectableImageLabel
    previous = _APP.styleSheet()
    _APP.setStyleSheet('QWidget { background: #16130f; }')
    paper = QFrame()
    paper.setObjectName('PaperCanvas')
    from ui.widgets.canvas_presentation import configure_canvas_paper
    configure_canvas_paper(paper)
    paper.resize(120,100)
    label = SelectableImageLabel(paper)
    label.setGeometry(10,10,100,80)
    pix = QPixmap(50,40); pix.fill(QColor('#537f91'))
    label.set_source_pixmap(pix,pix)
    paper.show(); QTest.qWait(20)
    color = paper.grab().toImage().pixelColor(12,12).name()
    paper.close()
    _APP.setStyleSheet(previous)
    assert color == '#ffffff'
