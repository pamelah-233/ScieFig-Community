import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
from core.models import IHCPanel,BrightfieldPanel
APP=QApplication.instance() or QApplication([])


def test_white_balance_neutralizes_patch_without_mutating_input():
    from core.plotting.histology_display import sample_white_balance,apply_white_balance
    rgb=np.full((20,30,3),[200,225,250],np.uint8);before=rgb.copy()
    settings=sample_white_balance(rgb,{'x':0,'y':0,'w':10,'h':10})
    output=apply_white_balance(rgb,settings)
    np.testing.assert_array_equal(rgb,before)
    assert np.ptp(output[0,0])<=1
    np.testing.assert_array_equal(apply_white_balance(rgb,None),rgb)
    with pytest.raises(ValueError):sample_white_balance(np.zeros_like(rgb),{'x':0,'y':0,'w':8,'h':8})
    with pytest.raises(ValueError):sample_white_balance(rgb,{'x':-1,'y':0,'w':8,'h':8})


@pytest.mark.parametrize('cls',[IHCPanel,BrightfieldPanel])
def test_white_balance_stays_image_local_and_analysis_uses_raw_pixels(tmp_path,cls):
    from core.plotting.histology_display import sample_white_balance
    file=tmp_path/'tissue.png';q=QImage(40,30,QImage.Format.Format_RGB32);q.fill(0xffb0c0);q.save(str(file))
    panel=cls(panel_id='balance');record=panel.import_image(file);second=panel.import_image(file)
    raw=panel.source_qimage(record['uid']);_,rgb=panel._load_image_rgb(record['uid']);analysis=deepcopy(panel.analysis)
    record['display_white_balance']=sample_white_balance(rgb,{'x':0,'y':0,'w':10,'h':10})
    assert panel.display_qimage(record['uid'])!=raw
    assert panel.source_qimage(record['uid'])==raw
    assert panel.display_qimage(second['uid'])==raw
    assert panel.analysis==analysis
    np.testing.assert_array_equal(panel._load_image_rgb(record['uid'])[1],rgb)


@pytest.mark.parametrize('kind',['ihc','brightfield'])
def test_white_balance_controls_read_current_image_and_reset(tmp_path,desktop_screen,kind):
    from pathlib import Path
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w._confirm_destructive_action=lambda *a:True
    w.project_recovery.timer.stop()
    panel=getattr(w,'_active_'+kind+'_panel')()
    file=tmp_path/'tissue.png';q=QImage(40,30,QImage.Format.Format_RGB32);q.fill(0xffb0c0);q.save(str(file))
    record=panel.import_image(file);w.set_module(kind);APP.processEvents()
    controller=getattr(w,kind+'_workspace').white_balance
    controller.apply_region({'x':0,'y':0,'w':10,'h':10})
    assert record.get('display_white_balance')
    assert panel.active_roi(record['uid']) is None
    controller.reset_button.click();assert 'display_white_balance' not in record
    w.close();APP.processEvents()


@pytest.mark.parametrize('kind',['ihc','brightfield'])
def test_white_patch_mouse_selection_preserves_measurement_roi_and_export(tmp_path,desktop_screen,kind):
    from pathlib import Path
    from PyQt6.QtCore import QRect,Qt
    from PyQt6.QtTest import QTest
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    panel=getattr(w,'_active_'+kind+'_panel')()
    file=tmp_path/'tissue.png';q=QImage(400,300,QImage.Format.Format_RGB32);q.fill(0xffb0c0);q.save(str(file))
    record=panel.import_image(file)
    panel.set_image_roi(record['uid'],'rectangle',dict(x=100,y=100,w=80,h=80))
    before=deepcopy(record['roi'])
    w.set_module(kind);w.show();APP.processEvents()
    workspace=getattr(w,kind+'_workspace');controller=workspace.white_balance
    controller.pick_button.click();APP.processEvents()
    label=workspace.canvas.image_label
    selection=label._map_source_rect_to_label(QRect(20,20,50,40))
    assert not selection.isEmpty()
    QTest.mousePress(label,Qt.MouseButton.LeftButton,pos=selection.topLeft())
    QTest.mouseMove(label,selection.bottomRight())
    QTest.mouseRelease(label,Qt.MouseButton.LeftButton,pos=selection.bottomRight());APP.processEvents()
    assert record.get('display_white_balance') and controller.pending is None
    assert record['roi']==before
    from core.plotting.histology_display import qimage_rgb
    expected=qimage_rgb(panel.display_qimage())
    target=tmp_path/'balanced.png'
    assert hasattr(controller,'export_to_path'), 'White-balanced native export is missing'
    controller.export_to_path(target)
    np.testing.assert_array_equal(qimage_rgb(QImage(str(target))),expected)
    raw_path=tmp_path/'original.png';getattr(w,'export_'+kind+'_original_image_to_path')(raw_path)
    np.testing.assert_array_equal(qimage_rgb(QImage(str(raw_path))),qimage_rgb(panel.source_qimage()))
    w.close();APP.processEvents()


@pytest.mark.parametrize('kind',['ihc','brightfield'])
def test_white_balance_reaches_layout_and_survives_save(tmp_path,desktop_screen,kind):
    from types import SimpleNamespace
    from core.models import Project
    from ui.layout_adapters.ihc_layout_adapter import IHC_SOURCE_IMAGE_KEY
    from ui.layout_adapters.brightfield_layout_adapter import BRIGHTFIELD_SOURCE_IMAGE_KEY
    from pathlib import Path
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    panel=getattr(w,'_active_'+kind+'_panel')()
    file=tmp_path/'tissue.png';q=QImage(40,30,QImage.Format.Format_RGB32);q.fill(0xffb0c0);q.save(str(file))
    record=panel.import_image(file);w.set_module(kind);APP.processEvents()
    controller=getattr(w,kind+'_workspace').white_balance
    controller.apply_region({'x':0,'y':0,'w':10,'h':10})
    key=IHC_SOURCE_IMAGE_KEY if kind=='ihc' else BRIGHTFIELD_SOURCE_IMAGE_KEY
    rendered=getattr(w,'_'+kind+'_layout_pixmap_provider')(panel,None)(SimpleNamespace(asset_key=key))
    from core.plotting.histology_display import qimage_rgb
    np.testing.assert_array_equal(qimage_rgb(rendered.toImage()),qimage_rgb(panel.display_qimage()))
    project=Project(title='Balance');project.add_panel(panel);target=tmp_path/'balance.sfig';project.save_sfig(target)
    restored_project=Project.load_sfig(target);saved=restored_project.panels[0]
    assert saved.display_qimage()==panel.display_qimage()
    assert saved.source_qimage()==panel.source_qimage()
    w.close();APP.processEvents()
