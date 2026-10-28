from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsItem
from PyQt6.QtTest import QTest

from core.models import FluorescencePanel
from ui.widgets.fluoro_canvas import FluorescenceCanvas

APP=QApplication.instance() or QApplication([])


@pytest.fixture
def fluoro(tmp_path):
    image=QImage(320,240,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkBlue)
    path=tmp_path/'image.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='scale');panel.import_image(path)
    record=panel.active_image();record['layout_mode']='merge_only'
    record['layout_scale_bars']=[{'target_id':'merge_only:merge','enabled':True,'manual_um_per_px':1,
        'template':{'label_length_um':40,'font_size':8.5,'line_thickness':3,'label_gap':4}}]
    canvas=FluorescenceCanvas();canvas.set_panel(panel);canvas.set_step(4)
    yield canvas,panel
    canvas.close();APP.processEvents()


def parts(canvas):
    picture=next(i for i in canvas.scene.items() if i.data(0)=='fluoro_image_merge')
    line=next(i for i in canvas.scene.items() if i.data(0)=='fluoro_scale_bar' and i.data(3)=='line')
    text=next(i for i in canvas.scene.items() if i.data(0)=='fluoro_scale_bar' and i.data(3)=='text')
    return picture,line,text


@pytest.mark.parametrize('sx,sy',[(2.0,2.0),(1.5,.75)])
def test_unselected_scale_bar_tracks_image_resize_and_refresh(fluoro,sx,sy):
    canvas,panel=fluoro
    picture,line,text=parts(canvas)
    picture.setSelected(True)
    line_start=picture.mapFromScene(line.mapToScene(line.line().p1()))
    length=line.line().length();font=text.font().pointSizeF();label=text.toPlainText()
    pixels=picture._visual_item.pixmap().size()
    calibration=deepcopy(panel.active_image().get('measurement_scale_bar'))
    picture.apply_geometry(picture.pos().x(),picture.pos().y(),picture.rect().width()*sx,picture.rect().height()*sy)
    picture,line,text=parts(canvas)
    assert line.line().length()==pytest.approx(length*sx)
    actual=picture.mapFromScene(line.mapToScene(line.line().p1()))
    assert (actual-QPointF(line_start.x()*sx,line_start.y()*sy)).manhattanLength()<1
    assert text.font().pointSizeF()==pytest.approx(font*sy)
    assert text.toPlainText()==label
    assert picture._visual_item.pixmap().size()==pixels
    before=line.sceneBoundingRect()
    canvas.apply_layout_objects(canvas.layout_objects())
    _,line,text=parts(canvas)
    assert line.sceneBoundingRect().width()==pytest.approx(before.width(),abs=1)
    assert text.font().pointSizeF()==pytest.approx(font*sy)
    assert panel.active_image().get('measurement_scale_bar')==calibration


def test_scale_bar_is_independently_movable_but_still_follows_image(fluoro):
    canvas,_=fluoro
    picture,line,text=parts(canvas)
    assert line.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    picture_before=QPointF(picture.pos())
    text_before=QPointF(text.pos())
    line.setSelected(True)
    line.setPos(line.pos()+QPointF(-10,-5))
    assert picture.pos()==picture_before
    assert text.pos()==text_before+QPointF(-10,-5)
    line.setSelected(False)
    before=line.mapToScene(line.line().p1())
    picture.setPos(picture.pos()+QPointF(70,30))
    assert line.mapToScene(line.line().p1())==before+QPointF(70,30)
    local=picture.mapFromScene(line.mapToScene(line.line().p1()))
    picture.apply_geometry(picture.pos().x(),picture.pos().y(),picture.rect().width()*2,picture.rect().height()*2)
    expected=picture.mapToScene(local*2)
    canvas.apply_layout_objects(canvas.layout_objects())
    _,line,_=parts(canvas)
    assert (line.mapToScene(line.line().p1())-expected).manhattanLength()<1


def test_resize_keeps_existing_scale_label_and_manual_display_calibration(fluoro):
    canvas,panel=fluoro
    record=panel.active_image();record.pop('layout_scale_bars')
    record['scale_bar']={'length_um':25,'manual_um_per_px':1,'show_text':True}
    canvas.refresh()
    picture,line,text=parts(canvas)
    label=text.toPlainText();length=line.line().length()
    picture.apply_geometry(picture.pos().x(),picture.pos().y(),picture.rect().width()*2,picture.rect().height()*2)
    canvas.apply_layout_objects(canvas.layout_objects())
    _,line,text=parts(canvas)
    assert text.toPlainText()==label
    assert line.line().length()==pytest.approx(length*2)


@pytest.mark.parametrize('module',['ihc','brightfield'])
def test_shared_layout_scale_bar_follows_image_but_can_move_independently(module):
    from test_layout_chart_adapter import _ihc_panel,_bf_panel
    from ui.widgets.layout_editor_canvas import LayoutEditorCanvas
    from ui.layout_adapters.ihc_layout_adapter import ihc_canvas_spec,ihc_layout_objects,apply_ihc_layout_objects
    from ui.layout_adapters.brightfield_layout_adapter import brightfield_canvas_spec,brightfield_layout_objects,apply_brightfield_layout_objects
    factory,spec,objects,apply=(_ihc_panel,ihc_canvas_spec,ihc_layout_objects,apply_ihc_layout_objects) if module=='ihc' else (
        _bf_panel,brightfield_canvas_spec,brightfield_layout_objects,apply_brightfield_layout_objects)
    panel=factory();layout=panel.data[f'{module}_layout']
    layout['scale_bars']=[{'target_id':'image-a','enabled':True,'manual_um_per_px':1,
        'template':{'label_length_um':10,'font_size':8.5,'line_thickness':3}}]
    canvas=LayoutEditorCanvas();pixmap=QPixmap(64,48);pixmap.fill(Qt.GlobalColor.darkBlue)
    canvas.set_layout_objects(spec(panel),objects(panel),lambda _:pixmap)
    picture=next(i for i in canvas.layout_items() if i.layout_object.object_type=='image')
    bar=next(i for i in canvas.layout_items() if i.layout_object.object_type=='scale_bar')
    start=bar.pos()-picture.pos();width=bar.rect().width();font=bar.layout_object.style.font_size_pt
    picture.update_geometry(picture.pos().x()+30,picture.pos().y()+20,picture.rect().width()*2,picture.rect().height()*2)
    assert (bar.pos()-picture.pos()-start*2).manhattanLength()<1
    assert bar.rect().width()==pytest.approx(width*2)
    assert bar.layout_object.style.font_size_pt==pytest.approx(font*2)
    picture_before=QPointF(picture.pos())
    bar.setSelected(True);bar.setPos(bar.pos()+QPointF(-10,-5))
    assert picture.pos()==picture_before
    apply(panel,canvas.layout_objects(),append_audit=False)
    restored=next(o for o in objects(panel) if o.object_type=='scale_bar')
    assert restored.style.font_size_pt==pytest.approx(font*2)
    assert restored.width==pytest.approx(width*2)
    picture.setSelected(True);bar.setSelected(True)
    before_picture=QPointF(picture.pos());before_bar=QPointF(bar.pos())
    QTest.keyClick(canvas.view,Qt.Key.Key_Right)
    assert picture.pos()==before_picture+QPointF(1,0)
    assert bar.pos()==before_bar+QPointF(1,0)
    canvas.close()


@pytest.mark.parametrize('module',['ihc','brightfield'])
def test_scale_controls_read_current_style_and_preserve_placement(module):
    from PyQt6.QtWidgets import QWidget
    from test_layout_chart_adapter import _ihc_panel,_bf_panel
    from ui.panels.ihc_workspace import IHCWorkspace
    from ui.panels.brightfield_workspace import BrightfieldWorkspace
    from ui.layout_adapters.ihc_layout_adapter import ihc_canvas_spec,ihc_layout_objects
    from ui.layout_adapters.brightfield_layout_adapter import brightfield_canvas_spec,brightfield_layout_objects
    factory,workspace_type,spec,objects=(_ihc_panel,IHCWorkspace,ihc_canvas_spec,ihc_layout_objects) if module=='ihc' else (
        _bf_panel,BrightfieldWorkspace,brightfield_canvas_spec,brightfield_layout_objects)
    panel=factory();panel.data[f'{module}_layout']['scale_bars']=[{'target_id':'image-a','enabled':True,'manual_um_per_px':1,
        'template':{'label_length_um':10,'font_size':8.5,'line_thickness':3,'offset_x_fraction':.12,'offset_y_fraction':.16}}]
    workspace=workspace_type(QWidget());workspace.panel=panel;canvas=workspace.layout_canvas
    workspace.current_step=3 if module=='ihc' else 4
    canvas.set_layout_objects(spec(panel),objects(panel),svg_text_changed_callback=workspace.persist_layout_edits)
    workspace._sync_scale_appearance_controls()
    picture=next(i for i in canvas.layout_items() if i.layout_object.object_type=='image')
    bar=next(i for i in canvas.layout_items() if i.layout_object.object_type=='scale_bar')
    picture.update_geometry(picture.pos().x(),picture.pos().y(),picture.rect().width()*2,picture.rect().height()*2)
    workspace.persist_layout_edits();bar.setSelected(True)
    assert workspace.scale_bar_font_size_spin.value()==17
    before=deepcopy(panel.data[f'{module}_layout']['scale_bars'][0]['template'])
    workspace.scale_bar_font_size_spin.setValue(20)
    workspace._apply_scale_bar(current_only=True)
    after=panel.data[f'{module}_layout']['scale_bars'][0]['template']
    assert after['offset_x_fraction']==pytest.approx(before['offset_x_fraction'])
    assert after['offset_y_fraction']==pytest.approx(before['offset_y_fraction'])
    workspace.close()
