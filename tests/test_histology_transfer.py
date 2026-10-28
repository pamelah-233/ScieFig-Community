import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
from core.models import IHCPanel, BrightfieldPanel

APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('kind,cls', [('ihc', IHCPanel), ('brightfield', BrightfieldPanel)])
def test_histology_transfer_keeps_raw_source_and_one_editable_calibrated_scale(tmp_path, kind, cls):
    from core.histology_transfer import apply_browser_histology_transfer
    path = tmp_path/'source.png'
    image=QImage(320,240,QImage.Format.Format_RGB32); image.fill(0xffc0b0);image.save(str(path))
    panel=cls(panel_id='transfer');record=panel.import_image(path)
    raw=panel.source_qimage(record['uid'])
    payload={'path':str(path),'calibration':{'um_per_px_x':.4,'um_per_px_y':.4,'calibration_source':'manual'},
             'display':{'visible':True,'length_um':50,'center_x_px':180,'line_y_px':200,'line_color':'#FFFFFF','text_color':'#FFFFFF','font_size_px':18}}
    apply_browser_histology_transfer(panel,record,payload,kind)
    apply_browser_histology_transfer(panel,record,payload,kind)
    assert panel.measurement_scale_bar(record['uid'])['um_per_px']==pytest.approx(.4)
    assert panel.source_qimage(record['uid'])==raw
    states=panel.data[kind+'_layout']['scale_bars']
    assert len(states)==1 and states[0]['target_id']==record['uid']
    assert record['source_path']==str(path)
    assert panel.analysis.get(kind+'_measurements',[])==[]
    template=states[0].get('template',{})
    assert template.get('font_size')==18 and template.get('text_color')=='#FFFFFF'


@pytest.mark.parametrize('kind',['ihc','brightfield'])
def test_browser_histology_action_uses_existing_import_worker_without_switching(desktop_screen, monkeypatch, kind):
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources')
    w.project_recovery.timer.stop();w._confirm_destructive_action=lambda *a:True
    calls=[];monkeypatch.setattr(w,'_start_microscopy_import',lambda *a,**kw:calls.append((a,kw)))
    w.set_module('viewer')
    browser=w.image_browser_workspace
    assert hasattr(browser,'send_to_'+kind+'_button')
    payloads=[{'path':'first.tif'},{'path':'second.png'}]
    browser.send_to_histology_transfer_requested.emit(kind,payloads)
    assert w.active_module=='viewer'
    assert calls[-1][0][0]==kind and calls[-1][0][1]==['first.tif','second.png']
    assert calls[-1][1]['transfer_by_path']=={p['path']:p for p in payloads}
    w.close();APP.processEvents()


@pytest.mark.parametrize('kind',['ihc','brightfield'])
def test_browser_transfer_completes_real_import_and_preserves_existing_images(tmp_path,desktop_screen,kind):
    import time
    from PyQt6.QtTest import QTest
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    panel=getattr(w,'_active_'+kind+'_panel')()
    files=[]
    for name,color in [('first.png',0xffc0b0),('second.png',0xf0e0d0)]:
        path=tmp_path/name;image=QImage(320,240,QImage.Format.Format_RGB32);image.fill(color);image.save(str(path));files.append(path)
    panel.import_image(files[0]);w.set_module('viewer')
    payload={'path':str(files[1]),'calibration':{'um_per_px_x':.5,'um_per_px_y':.5,'calibration_source':'manual'},
             'display':{'visible':True,'length_um':25,'center_x_px':220,'line_y_px':210,'text_color':'#FFFF00','font_size_px':16}}
    w.image_browser_workspace.send_to_histology_transfer_requested.emit(kind,[payload])
    deadline=time.monotonic()+10
    while w._analysis_job_service.is_busy('import:'+kind) and time.monotonic()<deadline:QTest.qWait(20)
    assert not w._analysis_job_service.is_busy('import:'+kind)
    assert len(panel.images())==2
    record=panel.images()[-1]
    assert record['source_name']=='second.png' and record['browser_scale_bar']==payload['display']
    assert panel.measurement_scale_bar(record['uid'])['um_per_px']==.5
    assert panel.source_qimage(record['uid']).size()==QImage(str(files[1])).size()
    assert w.active_module=='viewer'
    w.close();APP.processEvents()
