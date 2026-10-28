"""Measurement overlays must remain in the coordinates of their owning image."""
from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsPixmapItem, QGraphicsRectItem

from core.constants import WB_STEP_DENSITY, WB_STEP_LAYOUT
from core.models import FluorescencePanel
from ui.main_window import MainWindow
from ui.widgets.fluoro_canvas import FluorescenceCanvas

APP=QApplication.instance() or QApplication([])


@pytest.fixture
def measured_wb(tmp_path):
    from test_wb_phase4 import _write_wb_lane_strip
    first=_write_wb_lane_strip(tmp_path/'target.png',[100,160,210],width=360,height=40)
    second=_write_wb_lane_strip(tmp_path/'loading.png',[150,150,150],width=360,height=40)
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window.set_module('wb')
    window.import_wb_images([str(first),str(second)])
    panel=window._active_wb_panel()
    panel.data['placed_strip_ids']=['strip_1','strip_2']
    panel.set_annotations(['A','B','C'],[],[],lane_anchors=[60,180,300])
    window.data_panel.set_wb_step(WB_STEP_DENSITY)
    window.data_panel.wb_density_loading_ctrl_combo.setCurrentIndex(1)
    window.data_panel.wb_density_bg_method_combo.setCurrentText('None')
    window.data_panel.wb_density_measure_button.click()
    window.show();APP.processEvents()
    yield window,panel,window.wb_canvas
    window.close();APP.processEvents()


def _first_wb_box(canvas):
    return next(i for i in canvas._density_overlay_items if isinstance(i,QGraphicsRectItem))


def test_wb_measurement_box_follows_group_and_remeasurement(measured_wb):
    _,panel,canvas=measured_wb
    result=deepcopy(panel.data['densitometry'])
    box=_first_wb_box(canvas)
    before=box.sceneBoundingRect()
    start=QPointF(canvas._group.pos())
    canvas._group.setPos(start+QPointF(180,130))
    assert box.sceneBoundingRect().topLeft()==before.topLeft()+QPointF(180,130)
    expected=box.sceneBoundingRect()
    # Measuring again / re-showing a table result must not reset the box to world origin.
    canvas.set_density_overlay(result)
    assert _first_wb_box(canvas).sceneBoundingRect()==expected
    canvas.render()
    assert canvas._group.pos()==start+QPointF(180,130)
    assert _first_wb_box(canvas).sceneBoundingRect()==expected
    assert panel.data['densitometry']==result


def test_wb_pointer_drag_keeps_image_and_measurement_boxes_together(measured_wb):
    _,_,canvas=measured_wb
    image=next(i for i in canvas._group.childItems() if isinstance(i,QGraphicsPixmapItem))
    box=_first_wb_box(canvas)
    image_before=image.sceneBoundingRect().topLeft();box_before=box.sceneBoundingRect().topLeft()
    start=canvas.view.mapFromScene(image.sceneBoundingRect().center())
    end=start+QPoint(80,60)
    QTest.mousePress(canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=start)
    APP.sendEvent(canvas.view.viewport(),QMouseEvent(QEvent.Type.MouseMove,QPointF(end),
        QPointF(canvas.view.viewport().mapToGlobal(end)),Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=end)
    APP.processEvents()
    delta=image.sceneBoundingRect().topLeft()-image_before
    assert delta.manhattanLength()>5
    assert (box.sceneBoundingRect().topLeft()-box_before-delta).manhattanLength()<.01


def test_wb_density_entry_centers_current_strips_not_cached_import_page(measured_wb):
    _,_,canvas=measured_wb
    # Numeric results appear above strips after measurement. They must not
    # redefine/recenter the fixed paper or enter the strip-content bounds.
    bounds=QRectF()
    for item in canvas._group.childItems():
        if item not in canvas._density_overlay_items:bounds=bounds.united(item.sceneBoundingRect())
    center=canvas.view.mapFromScene(bounds.center())
    assert (center-canvas.view.viewport().rect().center()).manhattanLength()<20


def test_wb_returning_from_layout_restores_raw_measurement_strips_and_overlays(measured_wb):
    window,panel,canvas=measured_wb
    result=deepcopy(panel.data['densitometry'])
    panel.set_band_configuration([{'protein':'A'},{'protein':'B'}],['A','B','C'])
    window.data_panel.set_wb_step(WB_STEP_LAYOUT)
    window.data_panel.set_wb_step(WB_STEP_DENSITY)
    APP.processEvents()
    assert len(canvas._strip_display_rects)==2
    assert len([i for i in canvas._density_overlay_items if isinstance(i,QGraphicsRectItem)])==6
    assert not canvas.view.object_interaction.enabled()
    assert panel.data['densitometry']==result


def test_wb_layout_and_incoming_statistics_initialize_complete_fixed_page(measured_wb):
    window,panel,canvas=measured_wb
    panel.set_layout_position(180,130)
    panel.set_band_configuration([{'protein':'A'},{'protein':'B'}],['A','B','C'])
    window.data_panel.set_wb_step(WB_STEP_LAYOUT)
    APP.processEvents()
    first=canvas._view_controller.backend.content_rect()
    assert first.width()/first.height()==pytest.approx(620/420)
    panel.data['wb_statistics']={'plot_data':[{'group':'A','value':1.0},{'group':'B','value':2.0}], 'comparisons':[]}
    panel.data['wb_layout']['stats_plot']={'enabled':True,'width_px':520,'height_px':360}
    canvas.render();APP.processEvents()
    paper=canvas._view_controller.backend.content_rect()
    assert paper.contains(canvas._stats_group.sceneBoundingRect())
    assert paper.contains(canvas._group.sceneBoundingRect())
    assert not canvas._stats_group.sceneBoundingRect().intersects(canvas._group.sceneBoundingRect())
    canvas._stats_group.setPos(canvas._stats_group.pos()+QPointF(70,20))
    assert canvas._view_controller.backend.content_rect()==paper


@pytest.mark.parametrize('kind',['measurement','ellipse','polygon','count','ai','ai_contour','ai_review'])
def test_fluoro_overlays_follow_source_move_and_scale(tmp_path,kind):
    image=QImage(240,160,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
    path=tmp_path/'image.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='overlay');panel.import_image(path)
    canvas=FluorescenceCanvas();canvas.set_panel(panel);canvas.set_step(1)
    source=canvas._image_items[0]
    source.setPos(75,45)
    if kind=='measurement':
        canvas.show_measurement_overlay({'measurement_uuid':'r','roi_or_geometry':{'type':'rectangle','coordinates_px':{'x':20,'y':25,'w':40,'h':30}}})
    elif kind=='ellipse':
        canvas.show_measurement_overlay({'measurement_uuid':'r','roi_or_geometry':{'type':'ellipse','coordinates_px':{'cx':40,'cy':40,'rx':20,'ry':15}}})
    elif kind=='polygon':
        canvas.show_measurement_overlay({'measurement_uuid':'r','roi_or_geometry':{'type':'polygon','coordinates_px':{'points':[(20,25),(60,25),(60,55)]}}})
    elif kind=='count':
        canvas.show_count_overlay({'count_uuid':'c','roi_or_geometry':{'coordinates_px':{'x':20,'y':25,'w':40,'h':30}},'detected_spots':[{'x':30,'y':30,'w':5,'h':6}]})
    else:
        obj={'object_id':'o','bbox_x_min':20,'bbox_y_min':25,'bbox_x_max':60,'bbox_y_max':55}
        if kind=='ai_contour':obj['contour_points_px']=[{'x':20,'y':25},{'x':60,'y':25},{'x':60,'y':55}]
        if kind=='ai_review':obj['review_contours_px']=[[(20,25),(60,25),(60,55)]]
        canvas.show_ai_assisted_overlay([{'ai_measurement_uuid':'a','object_table':[obj]}])
    before=[source.mapFromScene(i.sceneBoundingRect()).boundingRect().center() for i in canvas._measurement_overlay_items]
    source.setPos(190,120)
    source.setScale(source.scale()*1.5)
    after=[source.mapFromScene(i.sceneBoundingRect()).boundingRect().center() for i in canvas._measurement_overlay_items]
    for old,new in zip(before,after):
        assert (new-old).manhattanLength()<.01
    canvas.clear_measurement_overlay()
    assert source.scene() is canvas.scene
    canvas.close()


@pytest.mark.parametrize('module',['qpcr','fluoro'])
def test_new_layout_chart_is_included_before_page_is_frozen(tmp_path,module):
    from test_layout_chart_adapter import _qpcr_panel,_fluoro_panel
    from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas
    if module=='qpcr':
        panel=_qpcr_panel();canvas=QPCRLayoutCanvas();canvas.set_panel(panel)
        records=panel.data['qpcr_layout']['plots'];record=deepcopy(records[0])
    else:
        image=QImage(240,160,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
        path=tmp_path/'fluoro.png';image.save(str(path))
        panel=FluorescencePanel(panel_id='chart');panel.import_image(path)
        panel.active_image()['layout_mode']='merge_only'
        canvas=FluorescenceCanvas();canvas.set_panel(panel);canvas.set_step(4)
        records=panel.analysis.setdefault('fluoro_layout_plots',[])
        record=deepcopy(_fluoro_panel().analysis['fluoro_layout_plots'][0])
    canvas.resize(900,650);canvas.show();APP.processEvents()
    initial=canvas._view_controller.backend.content_rect()
    record.update(plot_uuid='new-chart',x=initial.right()+60,y=initial.top()+40)
    records.append(record)
    canvas.refresh();APP.processEvents()
    plot=next(i for i in canvas.scene.items() if i.data(1)=='new-chart')
    paper=canvas._view_controller.backend.content_rect()
    assert paper.contains(plot.sceneBoundingRect())
    plot.setPos(plot.pos()+QPointF(30,20))
    assert canvas._view_controller.backend.content_rect()==paper
    canvas.close()


@pytest.mark.parametrize('module',['ihc','brightfield'])
def test_histology_roi_stays_on_source_pixels_through_zoom_and_pan(module):
    from ui.panels.ihc_workspace import IHCPreviewCanvas
    from ui.panels.brightfield_workspace import BrightfieldPreviewCanvas
    canvas=(IHCPreviewCanvas if module=='ihc' else BrightfieldPreviewCanvas)()
    canvas.resize(900,650)
    pixmap=QPixmap(240,160);pixmap.fill(Qt.GlobalColor.darkGreen)
    canvas.set_pixmap(pixmap)
    roi=QRect(20,25,40,30);canvas.set_roi_rect(roi)
    canvas.show();APP.processEvents()
    for factor in (1.0,1.4,.8):
        canvas.zoom_by(factor);canvas._view_controller.pan_by(25,15);APP.processEvents()
        displayed=canvas.image_label.pixmap()
        image=displayed.toImage()
        x=round(40*image.width()/240);y=round(25*image.height()/160)
        colors={image.pixelColor(x+dx,y+dy).name() for dx in range(-2,3) for dy in range(-2,3)}
        assert ('#ffd13b' if module=='ihc' else '#ff4fb5') in colors
        assert canvas.current_roi_rect()==roi
    canvas.close()


def test_browser_scale_overlay_stays_on_source_pixels_through_zoom_and_pan():
    from ui.widgets.image_browser_canvas import ImageBrowserCanvas
    from core.image_browser import ScaleBarState
    canvas=ImageBrowserCanvas();canvas.resize(900,650)
    pixmap=QPixmap(240,160);pixmap.fill(Qt.GlobalColor.darkGreen)
    canvas.set_pixmap(pixmap)
    state=ScaleBarState(visible=True,length_um=40,um_per_px=1,center_x_px=90,line_y_px=100,line_color='#FFD13B')
    canvas.set_scale_bar_state(state);canvas.show();APP.processEvents()
    for factor in (1.0,1.4,.8):
        canvas.zoom_by(factor);canvas._view_controller.pan_by(25,15);APP.processEvents()
        label=canvas.image_label
        point=label._map_source_point_to_label(90,100)
        image=label.grab().toImage()
        assert image.pixelColor(point).name()=='#ffd13b'
        assert canvas.scale_bar_state==state
    canvas.close()
