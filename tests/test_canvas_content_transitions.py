import os
import time
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from copy import deepcopy

import numpy as np
import pytest
import tifffile
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from core.models import FluorescencePanel, WesternBlotPanel
from ui.widgets.fluoro_canvas import FluorescenceCanvas
from ui.widgets.wb_canvas import WBCanvas
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas
from ui.widgets.canvas_presentation import REFERENCE_PAPER_SIZE

_APP = QApplication.instance() or QApplication([])


def shown(canvas):
    canvas.resize(1200,800)
    canvas.show()
    QTest.qWait(50)
    return canvas


def assert_fitted(canvas):
    frame = canvas._view_controller.backend.content_rect()
    mapped = canvas.view.mapFromScene(frame).boundingRect()
    viewport = canvas.view.viewport().rect()
    assert canvas._fit_mode
    assert (mapped.center()-viewport.center()).manhattanLength() <= 3
    assert viewport.adjusted(-2,-2,2,2).contains(mapped)


@pytest.mark.parametrize('canvas_type',[FluorescenceCanvas,WBCanvas,QPCRLayoutCanvas])
def test_empty_hint_is_standard_rectangle_not_a_text_strip(canvas_type):
    canvas = shown(canvas_type())
    canvas.refresh() if hasattr(canvas,'refresh') else canvas.render()
    QTest.qWait(40)
    frame = canvas._view_controller.backend.content_rect()
    assert frame.width()/frame.height() == pytest.approx(REFERENCE_PAPER_SIZE[0]/REFERENCE_PAPER_SIZE[1])
    hint = next(item for item in canvas.scene.items() if hasattr(item,'toPlainText'))
    assert hint.font().pixelSize() == 14
    mapped = hint.deviceTransform(canvas.view.viewportTransform()).mapRect(hint.boundingRect())
    assert (mapped.center()-QPointF(canvas.view.viewport().rect().center())).manhattanLength() <= 3
    assert mapped.height() < 100
    hint.setPlainText("Import or reload a fluorescence image to preview channels. 尚未导入图片，请先选择文件。")
    QTest.qWait(20)
    assert canvas._view_controller.backend.content_rect() == frame
    assert hint.font().pixelSize() == 14
    canvas.close()


def write_channels(path):
    data = np.zeros((2,240,320),dtype=np.uint16)
    data[0,40:70,50:80] = 1000
    data[1,130:170,190:230] = 2000
    tifffile.imwrite(path,data,metadata={'axes':'CYX'},photometric='minisblack')
    return data


def test_fluoro_import_and_switch_fit_but_same_image_refresh_keeps_view(tmp_path):
    panel = FluorescencePanel(panel_id='transition')
    canvas = shown(FluorescenceCanvas())
    canvas.set_panel(panel)
    QTest.qWait(40)
    canvas._view_controller.pan_by(1000,400)
    first = tmp_path/'first.tif'
    second = tmp_path/'second.tif'
    raw = write_channels(first)
    write_channels(second)
    panel.import_image(first)
    first_uid = panel.active_image()['uid']
    canvas.refresh()
    QTest.qWait(60)
    assert_fitted(canvas)
    canvas._view_controller.pan_by(1000,400)
    panel.import_image(second)
    canvas.refresh()
    QTest.qWait(60)
    assert_fitted(canvas)
    canvas.zoom_by(1.2)
    canvas._view_controller.pan_by(40,20)
    zoom = canvas._zoom_factor
    center = canvas.view.mapToScene(canvas.view.viewport().rect().center())
    before = deepcopy(panel.active_image()['measurement_scale_bar'])
    canvas.refresh()
    QTest.qWait(60)
    assert not canvas._fit_mode
    assert canvas._zoom_factor == zoom
    after = canvas.view.mapToScene(canvas.view.viewport().rect().center())
    assert (after-center).manhattanLength() <= 2/zoom
    assert panel.active_image()['measurement_scale_bar'] == before
    panel.data['active_image_uid'] = first_uid
    canvas.refresh(); QTest.qWait(60)
    assert_fitted(canvas)
    assert np.array_equal(panel.image_data(first_uid),raw)
    canvas.close()


def test_wb_new_image_does_not_inherit_old_pan(tmp_path):
    canvas = shown(WBCanvas())
    panel = WesternBlotPanel(panel_id='wb-transition')
    canvas.set_panel(panel)
    canvas._view_controller.pan_by(1000,400)
    for name in ('one.png','two.png'):
        image = QImage(320,240,QImage.Format.Format_RGB32)
        image.fill(QColor('#ababab'))
        path = tmp_path/name
        image.save(str(path))
        panel.import_full_blot(path)
        canvas.set_panel(panel)
        QTest.qWait(50)
        assert_fitted(canvas)
        canvas.zoom_by(1.1)
        canvas._view_controller.pan_by(1000,400)
    canvas.close()


@pytest.mark.parametrize('module',['ihc','brightfield'])
def test_histology_image_identity_resets_only_on_new_image(module):
    from PyQt6.QtGui import QPixmap
    from ui.panels.ihc_workspace import IHCPreviewCanvas
    from ui.panels.brightfield_workspace import BrightfieldPreviewCanvas
    canvas = shown((IHCPreviewCanvas if module=='ihc' else BrightfieldPreviewCanvas)())
    pix = QPixmap(320,240); pix.fill(QColor('#ababab'))
    canvas.bind_preview_image('first')
    canvas.set_pixmap(pix,title='Original')
    QTest.qWait(40)
    canvas.zoom_by(2)
    canvas._view_controller.pan_by(1000,400)
    canvas.bind_preview_image('second')
    canvas.set_pixmap(pix,title='Original')
    QTest.qWait(50)
    assert canvas._fit_mode
    canvas.zoom_by(1.1)
    zoom = canvas._zoom_factor
    canvas.bind_preview_image('second')
    canvas.set_pixmap(pix,title='Original')
    QTest.qWait(40)
    assert not canvas._fit_mode
    assert canvas._zoom_factor == zoom
    canvas.close()


def test_real_window_async_import_and_image_selector_center_without_fit_click(tmp_path):
    from ui.main_window import MainWindow
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window.set_module('fluoro')
    canvas = window.fluoro_workspace.canvas
    window.show(); QTest.qWait(60)
    for filename in ('first.tif','second.tif'):
        canvas._view_controller.pan_by(1000,400)
        path = tmp_path/filename
        write_channels(path)
        window.import_fluorescence_images([str(path)])
        deadline = time.monotonic()+10
        while window._analysis_job_service.is_busy('import:fluoro'):
            assert time.monotonic() < deadline
            QTest.qWait(10)
        QTest.qWait(60)
        assert_fitted(canvas)
    canvas._view_controller.pan_by(1000,400)
    window.fluoro_workspace.active_image_combo.setCurrentIndex(0)
    QTest.qWait(60)
    assert_fitted(canvas)
    window._capture_clean_project_baseline()
    window.close()
