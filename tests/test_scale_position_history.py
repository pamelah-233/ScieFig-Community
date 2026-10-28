"""Scale placement is one persisted, undoable gesture for line and label."""
from copy import deepcopy
from pathlib import Path
import numpy as np
import pytest
import tifffile
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtTest import QTest


@pytest.fixture
def intake(qt_application, desktop_screen, tmp_path):
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources')
    w.project_recovery.timer.stop()
    panel=w._active_fluoro_panel()
    for name in ('first','second'):
        path=tmp_path/(name+'.ome.tif')
        tifffile.imwrite(path,np.full((240,320),80,np.uint8),ome=True,
            metadata={'axes':'YX','PhysicalSizeX':.5,'PhysicalSizeXUnit':'µm',
                      'PhysicalSizeY':.5,'PhysicalSizeYUnit':'µm'})
        panel.import_image(path)
    panel.set_active_image(panel.images()[0]['uid'])
    w.set_module('fluoro');ws=w.fluoro_workspace;ws.set_panel(panel)
    w.workflow_ui.buttons['fluoro'][0].click()
    ws.scale_bar_length_spin.setValue(40);ws.scale_bar_apply_current_button.click()
    w.resize(1500,960);w.show();w.activateWindow()
    for _ in range(8):qt_application.processEvents()
    yield w,ws,panel
    w._capture_clean_project_baseline();w.close();qt_application.processEvents()


def parts(ws):
    items=ws.canvas.scene.items()
    line=next(i for i in items if i.data(0)=='fluoro_scale_bar' and i.data(3)=='line')
    text=next(i for i in items if i.data(0)=='fluoro_scale_bar' and i.data(3)=='text')
    return line,text


def drag(ws,item,delta=QPoint(-40,-24)):
    view=ws.canvas.view
    start=view.mapFromScene(item.mapToScene(item.boundingRect().center()))
    view.setFocus()
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(view.viewport(),start+delta)
    QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=start+delta)
    QTest.qWait(40)


@pytest.mark.parametrize('part',['text','line'])
def test_import_drag_moves_line_and_number_together(intake,part):
    w,ws,panel=intake;line,text=parts(ws)
    before_line=line.mapToScene(line.line().p1());before_text=text.scenePos()
    calibration=deepcopy(panel.measurement_scale_bar())
    drag(ws,text if part=='text' else line)
    line,text=parts(ws)
    moved=line.mapToScene(line.line().p1())-before_line
    assert moved.manhattanLength()>5
    assert (text.scenePos()-before_text-moved).manhattanLength()<1
    assert panel.measurement_scale_bar()==calibration


def test_import_drag_survives_image_switch_and_ctrl_z(intake):
    w,ws,panel=intake;line,text=parts(ws)
    uid=panel.active_image()['uid'];before=line.mapToScene(line.line().p1())
    drag(ws,text)
    moved=parts(ws)[0].mapToScene(parts(ws)[0].line().p1())
    ws.active_image_combo.setCurrentIndex(1);QTest.qWait(25)
    ws.active_image_combo.setCurrentIndex(0);QTest.qWait(25)
    line,text=parts(ws)
    assert (line.mapToScene(line.line().p1())-moved).manhattanLength()<1
    assert (moved-before).manhattanLength()>5
    ws.canvas.view.setFocus();QTest.keyClick(ws.canvas.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(25)
    line,text=parts(ws)
    assert (line.mapToScene(line.line().p1())-before).manhattanLength()<1
    QTest.keyClick(ws.canvas.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier);QTest.qWait(25)
    line,text=parts(ws)
    assert (line.mapToScene(line.line().p1())-moved).manhattanLength()<1
    assert panel.active_image()['uid']==uid


def test_drawn_reference_scale_label_drag_stays_with_line(intake):
    w,ws,panel=intake
    panel.active_image().pop('layout_scale_bars',None)
    ws._add_drawn_roi('scale_line',dict(x1=130,y1=170,x2=210,y2=170))
    # Also cover older projects whose reference line has no layout template.
    panel.active_image().pop('layout_scale_bars',None);ws.canvas.refresh()
    QTest.qWait(25)
    line=next(i for i in ws.canvas.scene.items() if i.data(0)=='fluoro_measurement_scale_line')
    text=next(i for i in ws.canvas.scene.items() if getattr(i,'_payload',None)==('fluoro_measurement_scale_bar',0))
    before=line.mapToScene(line.line().p1());calibration=panel.measurement_scale_bar()['um_per_px']
    drag(ws,text)
    assert (line.mapToScene(line.line().p1())-before).manhattanLength()>5
    assert panel.measurement_scale_bar()['um_per_px']==calibration
    QTest.keyClick(ws.canvas.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(25)
    line=next(i for i in ws.canvas.scene.items() if i.data(0)=='fluoro_measurement_scale_line')
    assert (line.mapToScene(line.line().p1())-before).manhattanLength()<1


def test_layout_drag_and_undo_keep_scale_together(intake):
    w,ws,panel=intake
    w.workflow_ui.buttons['fluoro'][4].click();QTest.qWait(40)
    line,text=parts(ws);before=line.mapToScene(line.line().p1());text_before=text.scenePos()
    drag(ws,text)
    line,text=parts(ws);moved=line.mapToScene(line.line().p1())
    assert (moved-before).manhattanLength()>5
    assert (text.scenePos()-text_before-(moved-before)).manhattanLength()<1
    QTest.keyClick(ws.canvas.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(40)
    line,text=parts(ws)
    assert (line.mapToScene(line.line().p1())-before).manhattanLength()<1


def test_import_scale_history_is_per_image_and_saved_in_project(intake,tmp_path):
    from core.models import Project
    w,ws,panel=intake;line,text=parts(ws)
    drag(ws,text);first=deepcopy(panel.active_image()['layout_scale_bars'])
    ws.active_image_combo.setCurrentIndex(1);QTest.qWait(25)
    before=deepcopy(panel.active_image())
    w._undo_active_editor()
    assert panel.active_image()==before
    ws.scale_bar_apply_current_button.click();QTest.qWait(25)
    second=deepcopy(panel.active_image()['layout_scale_bars']);drag(ws,parts(ws)[0])
    assert panel.images()[0]['layout_scale_bars']==first
    w._undo_active_editor()
    assert panel.active_image()['layout_scale_bars']==second
    path=tmp_path/'placements.sfig';w.project.save_sfig(path);loaded=Project.load_sfig(path)
    try:
        restored=next(p for p in loaded.panels if p.panel_uuid==panel.panel_uuid)
        assert restored.images()[0]['layout_scale_bars']==first
        assert restored.images()[1]['layout_scale_bars']==second
    finally:loaded.cleanup_archive_extract_root()


def test_undo_placement_does_not_reset_later_color_or_calibration_edits(intake):
    w,ws,panel=intake;line,text=parts(ws);drag(ws,text)
    image=panel.active_image();state=image['layout_scale_bars'][0]
    state['template']['line_color']='#FFD13B'
    panel.update_measurement_scale_bar(image['uid'],um_per_px=.75,calibration_source='manual')
    w._undo_active_editor()
    assert image['layout_scale_bars'][0]['template']['line_color']=='#FFD13B'
    assert panel.measurement_scale_bar()['um_per_px']==.75


@pytest.mark.parametrize('part',['text','line'])
def test_browser_drag_switch_and_undo(intake,part,tmp_path):
    from core.image_browser import scale_bar_layout
    from PyQt6.QtCore import QRectF
    w,_,_=intake;w.set_module('viewer');ws=w.image_browser_workspace
    ws.open_folder(tmp_path);ws.thumbnail_list.setCurrentRow(0)
    for _ in range(150):
        if ws._cached_image is not None:break
        QTest.qWait(20)
    assert ws._cached_image is not None
    ws.length_spin.setValue(40);ws.scale_bar_checkbox.setChecked(True);QTest.qWait(40)
    label=ws.canvas.image_label
    geometry=scale_bar_layout(QRectF(label._pixmap_rect()),label._source_size,label.scale_bar_state)
    before=deepcopy(ws.canvas.scale_bar_state)
    start=(geometry.text_rect if part=='text' else geometry.line_rect).center().toPoint()
    QTest.mousePress(label,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(label,start+QPoint(-40,-25));QTest.mouseRelease(label,Qt.MouseButton.LeftButton,pos=start+QPoint(-40,-25));QTest.qWait(30)
    moved=deepcopy(ws.canvas.scale_bar_state)
    assert moved.center_x_px!=before.center_x_px
    ws.thumbnail_list.setCurrentRow(1)
    for _ in range(150):
        if ws._cached_path==Path(ws.thumbnail_list.item(1).data(Qt.ItemDataRole.UserRole)):break
        QTest.qWait(20)
    ws.thumbnail_list.setCurrentRow(0)
    for _ in range(150):
        if ws._cached_path==Path(ws.thumbnail_list.item(0).data(Qt.ItemDataRole.UserRole)):break
        QTest.qWait(20)
    assert ws.canvas.scale_bar_state==moved
    label=ws.canvas.image_label;label.setFocus()
    QTest.keyClick(label,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(25)
    assert ws.canvas.scale_bar_state==before


@pytest.mark.parametrize('module',['brightfield','ihc'])
@pytest.mark.parametrize('part',['text','line'])
def test_histology_import_scale_drag_switch_and_undo(intake,tmp_path,module,part):
    from core.layout_scale_bar import resolve_native_layout_scale_bar
    w,_,_=intake;panel=getattr(w,'_active_'+module+'_panel')()
    for name in ('first','second'):panel.import_image(tmp_path/(name+'.ome.tif'))
    uid=panel.images()[0]['uid'];panel.data['active_image_uid']=uid
    w.set_module(module);ws=getattr(w,module+'_workspace');ws.set_panel(panel)
    w.workflow_ui.buttons[module][0].click()
    getattr(w,'_refresh_'+module+'_preview')(panel)
    ws.scale_bar_length_spin.setValue(40);ws.scale_bar_apply_current_button.click();QTest.qWait(30)
    layout=panel.data[module+'_layout'];before=deepcopy(layout['scale_bars'])
    spec=resolve_native_layout_scale_bar(target_id=uid,native_width_px=320,native_height_px=240,
        source_metadata=panel.active_image()['metadata'],measurement_scale=panel.measurement_scale_bar(uid),states=before)
    label=ws.canvas.image_label
    point=label._map_source_point_to_label(spec['x']+spec['width']/2,
        spec['y']+(3 if part=='text' else spec['height']-2))
    QTest.mousePress(label,Qt.MouseButton.LeftButton,pos=point)
    QTest.mouseMove(label,point+QPoint(-30,-20));QTest.mouseRelease(label,Qt.MouseButton.LeftButton,pos=point+QPoint(-30,-20));QTest.qWait(30)
    after=deepcopy(layout['scale_bars'])
    assert after!=before
    calibration=deepcopy(panel.measurement_scale_bar(uid))
    panel.data['active_image_uid']=panel.images()[1]['uid'];ws.set_panel(panel);getattr(w,'_refresh_'+module+'_preview')(panel)
    panel.data['active_image_uid']=uid;ws.set_panel(panel);getattr(w,'_refresh_'+module+'_preview')(panel);QTest.qWait(30)
    assert layout['scale_bars']==after
    label=ws.canvas.image_label;label.setFocus()
    QTest.keyClick(label,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(25)
    assert layout['scale_bars']==before
    assert panel.measurement_scale_bar(uid)==calibration
