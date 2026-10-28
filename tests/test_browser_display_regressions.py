from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage, QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget, QGraphicsLineItem

from test_browser_navigation_and_calibration import ImmediatePool
from test_image_browser_workspace import FakeBrowserCore, FakeBrowserImage, _rgb, _workspace
from core.models import FluorescencePanel
from ui.widgets.fluoro_canvas import FluorescenceCanvas

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def browser_view(tmp_path):
    paths=[tmp_path/f'{i}.png' for i in range(3)]
    sizes=[(480,320),(320,480),(600,220)]
    core=FakeBrowserCore({p:FakeBrowserImage(p,_rgb(*size)) for p,size in zip(paths,sizes)})
    browser=_workspace(core,thread_pool=ImmediatePool())
    host=QWidget();host.resize(1360,880)
    layout=QHBoxLayout(host)
    layout.addWidget(browser.canvas,1);layout.addWidget(browser.side_panel)
    browser.side_panel.setFixedWidth(400)
    host.show();browser.open_folder(tmp_path)
    browser.thumbnail_list.setCurrentRow(0);QTest.qWait(150)
    yield browser,host
    host.close();browser.canvas.close();browser.close();host.deleteLater();browser.deleteLater();APP.processEvents()


def image_center(canvas):
    label=canvas.image_label
    return canvas.scroll_area.viewport().mapFromGlobal(label.mapToGlobal(label._pixmap_rect().center()))


def test_thumbnail_viewport_keeps_one_complete_preview_row(browser_view):
    browser,host=browser_view
    # A complete image plus filename must fit even with calibration controls below.
    for height in (880,680):
        host.resize(1360,height);QTest.qWait(120)
        assert browser.thumbnail_list.viewport().height()>=160


def test_next_image_stays_centered_after_fit(browser_view):
    browser,_=browser_view
    for _ in range(2):
        browser.canvas.fit_to_canvas();QTest.qWait(150)
        center=browser.canvas.scroll_area.viewport().rect().center()
        assert (image_center(browser.canvas)-center).manhattanLength()<6
        browser.canvas.next_button.click();QTest.qWait(150)
        assert (image_center(browser.canvas)-center).manhattanLength()<6


def test_thumbnail_area_can_be_dragged_larger_without_moving_canvas(browser_view):
    browser,_=browser_view
    thumbnails=browser.thumbnail_list
    before_height=thumbnails.height()
    before_center=image_center(browser.canvas)
    handle=browser.preview_splitter.handle(1)
    start=handle.rect().center()
    QTest.mousePress(handle,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(handle,start+QPoint(0,170),delay=20)
    QTest.mouseRelease(handle,Qt.MouseButton.LeftButton,pos=start+QPoint(0,170))
    QTest.qWait(100)
    assert thumbnails.height()>before_height+100
    visible=[thumbnails.visualItemRect(thumbnails.item(i)) for i in range(thumbnails.count())]
    assert sum(thumbnails.viewport().rect().contains(rect) for rect in visible)>=2
    assert browser.settings_scroll.height()>=160
    assert image_center(browser.canvas)==before_center


@pytest.mark.parametrize('calibration_source',['manual','manual_line','embedded'])
def test_browser_transfer_has_single_scale_presentation(tmp_path,calibration_source):
    path=tmp_path/'source.png'
    source=QImage(320,240,QImage.Format.Format_RGB32);source.fill(QColor('#203040'));source.save(str(path))
    panel=FluorescencePanel(panel_id='F');image=panel.import_image(path)
    if calibration_source=='embedded':
        image['metadata']['pixel_size_um']=.5
    payload={'calibration':{'enabled':True,'length_um':20,'um_per_px':.5,
        'calibration_source':calibration_source,'calibrated':True,'assumed':False,
        'line_px':{'x1':210,'y1':200,'x2':250,'y2':200}},
        'display':{'visible':True,'length_um':20,'um_per_px':.5,'center_x_px':230,'line_y_px':200,
        'line_color':'#00FF00','text_color':'#FFFF00','line_thickness_px':4,'font_size_px':14,'text_position':'below'}}
    panel.apply_browser_scale_transfer(image['uid'],payload)
    raw=panel.image_data(image['uid']).copy()
    canvas=FluorescenceCanvas();canvas.set_panel(panel);canvas.set_step(1)
    try:
        markers=[str(i.data(0)) for i in canvas.scene.items()]
        editable=sum(isinstance(i,QGraphicsLineItem) and i.data(0) in ('fluoro_scale_bar','fluoro_measurement_scale_line') for i in canvas.scene.items())
        assert editable+markers.count('fluoro_browser_scale_bar')==1
        assert canvas._browser_display_scale_bar_state(image).text_position=='below'
        canvas.set_step(4)
        # Transferred target retains an individually editable layout scale, not a baked duplicate.
        layout_lines=[i for i in canvas.scene.items() if i.data(0)=='fluoro_scale_bar' and isinstance(i,QGraphicsLineItem)]
        assert len(layout_lines)==1
        assert layout_lines[0].pen().color().name()=='#00ff00'
        assert (panel.image_data(image['uid'])==raw).all()
        # Explicitly editing the transferred target supersedes the browser bar.
        from ui.widgets.calibration_controls import apply_target_scale_style
        target=f"{image.get('layout_mode', 'merge_split')}:merge"
        apply_target_scale_style(image,'layout_scale_bars',[target],{
            'label_length_um':30,'line_color':'#FF0000','text_color':'#FF0000'},0)
        canvas.set_step(1)
        assert not any(i.data(0)=='fluoro_browser_scale_bar' for i in canvas.scene.items())
        assert len([i for i in canvas.scene.items() if i.data(0)=='fluoro_scale_bar' and isinstance(i,QGraphicsLineItem)])==1
        # Drawing calibration temporarily replaces, rather than doubles, display bars.
        canvas.set_roi_mode('scale_line');canvas.refresh()
        assert not any(i.data(0)=='fluoro_scale_bar' for i in canvas.scene.items())
        assert len([i for i in canvas.scene.items() if i.data(0)=='fluoro_measurement_scale_line'])==1
    finally:
        canvas.close();canvas.deleteLater();APP.processEvents()
