import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from pathlib import Path
import time
import numpy as np
import pytest
import tifffile
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from core.models import Project
from core.image_analysis.cellpose_backend import pack_instance_labels
from core.image_analysis.nuclei_counting import object_rows
from core.measurement_relations import measurement_count_ratio,measurement_fusion_index

APP=QApplication.instance() or QApplication([])


def records(uid):
    fibers=np.zeros((100,160),np.int32);fibers[10:80,10:70]=1
    nuclei=np.zeros_like(fibers);nuclei[25:35,25:35]=1;nuclei[50:60,40:50]=2;nuclei[30:40,110:120]=3
    tube_rows=[dict(id=r['object_id'],contour_px=r['contour'],include_in_statistics=True) for r in object_rows(fibers,fibers)]
    nuclear_rows=object_rows(nuclei,nuclei)
    for row in nuclear_rows:row.update(classification='included',include_in_statistics=True)
    first=dict(measurement_uuid='tube-source',image_uid=uid,image_name='example',channel_index=0,
               type='myotube_measurement',results=tube_rows,roi=None,instance_labels=pack_instance_labels(fibers))
    second=dict(measurement_uuid='nuclear-source',image_uid=uid,image_name='example',channel_index=2,task='dapi',
                nuclei=nuclear_rows,parameters=dict(method='watershed'),provenance={},roi=None,instance_labels=pack_instance_labels(nuclei))
    return first,second,nuclei


def wait_job(w,key):
    deadline=time.monotonic()+20
    while w._analysis_job_service.is_busy(key) and time.monotonic()<deadline:QTest.qWait(20)
    assert not w._analysis_job_service.is_busy(key)


def test_first_level_relations_image_switch_and_sfig_roundtrip(tmp_path,desktop_screen):
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    panel=w._active_fluoro_panel();first,nuclear,labels=records('pending')
    raw=np.zeros((3,100,160),np.uint16);raw[0]=100;raw[2,labels>0]=1000
    raw[1,labels==1]=30;raw[1,labels==2]=80
    path=tmp_path/'channels.tif';tifffile.imwrite(path,raw,metadata={'axes':'CYX'},photometric='minisblack')
    image=panel.import_image(path);first['image_uid']=nuclear['image_uid']=image['uid']
    panel.analysis['myotube_measurements']=[first];panel.analysis['nuclei_measurements']=[nuclear]
    w.set_module('fluoro');deck=w.measurement_tasks['fluoro']
    for task in ('phenotype','count_ratio','fusion'):assert task in deck.pages
    fusion=deck.controllers['fusion'];deck.select('fusion');fusion.sync();fusion.run();wait_job(w,fusion.job_key)
    assert fusion.current_result()['summary']['fusion_index_percent']==pytest.approx(200/3)
    assert fusion.current_result()['parameters']['min_nuclei']==2
    phenotype=deck.controllers['phenotype'];deck.select('phenotype');phenotype.sync()
    phenotype.controls.first.setCurrentIndex(phenotype.controls.first.findData('nuclear-source'))
    phenotype.controls.channel.setCurrentIndex(1);phenotype.controls.positive.setValue(20);phenotype.controls.strong.setValue(50)
    phenotype.run();wait_job(w,phenotype.job_key)
    summary=phenotype.current_result()['summary']
    assert (summary['negative'],summary['weak_positive'],summary['strong_positive'])==(1,1,1)
    ratio=deck.controllers['count_ratio'];deck.select('count_ratio');ratio.sync()
    ratio.controls.first.setCurrentIndex(ratio.controls.first.findData('tube-source'))
    ratio.controls.second.setCurrentIndex(ratio.controls.second.findData('nuclear-source'))
    ratio.run();wait_job(w,ratio.job_key)
    assert ratio.current_result()['summary']['percent']==pytest.approx(100/3)
    nuclear['nuclei'][0]['include_in_statistics']=False
    assert not ratio.is_fresh(ratio.current_result())
    with pytest.raises(ValueError):ratio.export_csv(tmp_path/'stale.csv')
    nuclear['nuclei'][0]['include_in_statistics']=True
    ratio.export_csv(tmp_path/'ratio.csv');assert (tmp_path/'ratio.csv').exists()
    second=panel.import_image(path);fusion.sync();assert fusion.current_result() is None
    assert not fusion.controls.review_button.isEnabled()
    panel.set_active_image(image['uid']);fusion.sync();assert fusion.current_result()['summary']['fused_nuclei']==2
    archive=tmp_path/'relations.sfig';w.project.save_sfig(archive);restored=Project.load_sfig(archive)
    restored_panel=next(p for p in restored.panels if p.panel_id==panel.panel_id)
    assert restored_panel.analysis['object_relation_measurements']==panel.analysis['object_relation_measurements']
    restored.cleanup_archive_extract_root();w.close();APP.processEvents()


def test_ratio_checks_same_image_and_roi_without_inventing_blob_contours():
    source=dict(image_uid='one',measurement_uuid='a',detected_spots=[dict(object_id=1),dict(object_id=2)])
    target=dict(image_uid='one',measurement_uuid='b',detected_spots=[dict(object_id=1)])
    assert measurement_count_ratio(source,target,(50,60))['summary']['percent']==200
    target['image_uid']='two'
    with pytest.raises(ValueError,match='same image'):measurement_count_ratio(source,target,(50,60))
    target['image_uid']='one';target['roi']=dict(type='rectangle',coordinates_px=dict(x=0,y=0,w=10,h=10))
    with pytest.raises(ValueError,match='same ROI'):measurement_count_ratio(source,target,(50,60))


def test_exact_saved_labels_preserve_nuclear_holes_and_source_records():
    first,second,labels=records('one');before=deepcopy(second)
    from core.measurement_relations import geometry_record
    labels[27:30,27:30]=0;second['instance_labels']=pack_instance_labels(labels)
    np.testing.assert_array_equal(geometry_record(second,labels.shape)['labels'],labels)
    before=deepcopy(second)
    result=measurement_fusion_index(first,second,labels.shape)
    assert result['summary']['fused_nuclei']==2
    assert second==before


def test_count_can_classify_another_channel_using_raw_signal(tmp_path,desktop_screen):
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    panel=w._active_fluoro_panel();y,x=np.mgrid[:80,:120]
    signal=(1000*np.exp(-((x-30)**2+(y-40)**2)/35)+1000*np.exp(-((x-85)**2+(y-40)**2)/35)).astype(np.uint16)
    data=np.stack([np.where(x<60,80,25).astype(np.uint16),signal])
    path=tmp_path/'nuclei.tif';tifffile.imwrite(path,data,metadata={'axes':'CYX'},photometric='minisblack')
    record=panel.import_image(path);record['channels'][1]['name']='DAPI';w.set_module('fluoro')
    c=w.nuclei_controllers['fluoro'];c.sync();c.controls.channel.setCurrentIndex(1)
    c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'))
    c.controls.classify_signal.setChecked(True);c.controls.signal_channel.setCurrentIndex(0)
    c.controls.signal_positive.setValue(20);c.controls.signal_strong.setValue(50)
    c.run();wait_job(w,c.job_key)
    result=c.current_result();assert sorted(r['signal_class'] for r in result['nuclei'])==['strong_positive','weak_positive']
    assert result['parameters']['signal_classification']['channel_index']==0
    np.testing.assert_array_equal(panel.image_data(record['uid']),data)
    w.close();APP.processEvents()

