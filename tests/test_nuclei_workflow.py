import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import json
import time
import numpy as np
import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from core.models import Project

APP=QApplication.instance() or QApplication([])


def window():
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources')
    w.project_recovery.timer.stop()
    assert hasattr(w,'nuclei_controllers'), 'Dedicated nuclear workflows are not connected'
    return w


def wait_job(w,key):
    deadline=time.monotonic()+20
    while w._analysis_job_service.is_busy(key) and time.monotonic()<deadline:
        QTest.qWait(20)
    assert not w._analysis_job_service.is_busy(key)


def test_round_he_profile_load_is_explicit_and_parameters_follow_image(tmp_path,desktop_screen):
    from skimage.color import hed2rgb
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    y,x=np.mgrid[:100,:120];radius=np.sqrt((x-60)**2+(y-50)**2)
    stains=np.zeros((100,120,3));stains[...,1]=.035
    stains[...,0]=.13*np.exp(-((radius-6)**2)/.8)
    stains[radius<5,0]=.012;stains[radius<5,1]=.002
    path=tmp_path/'round.png';Image.fromarray(np.round(hed2rgb(stains)*255).astype(np.uint8)).save(path)
    first=panel.import_image(path);w.set_module('brightfield');c.sync()
    index=c.controls.method.findData('he_round')
    assert index>=0, 'Round HE nuclei option is missing'
    assert not hasattr(c.controls,'preset_button'), 'Selecting a model must not require another load button'
    c.controls.diameter.setValue(19);c.controls.method.setCurrentIndex(index)
    assert c.controls.diameter.value()==19  # Merely selecting a method does not overwrite edits.
    c.load_recipe()
    assert c.controls.diameter.value()==12 and c.controls.maximum.value()==800
    params=c.controls.parameters()
    assert params['method']=='he_round' and params['he_settings']['core_blue_fraction']==.12
    params['he_settings']['core_blue_fraction']=.18
    c.controls.set_parameters(params)
    c.run();wait_job(w,c.job_key)
    assert len(c.current_result()['nuclei'])==1
    assert c.current_result()['parameters']['he_settings']==params['he_settings']
    second=panel.import_image(path);c.sync()
    assert c.current_result() is None
    panel.set_active_image(first['uid']);c.sync()
    assert c.controls.parameters()['he_settings']==params['he_settings']
    project=Project(title='Retained nuclear profiles');project.add_panel(panel)
    archive=tmp_path/'profile.sfig';project.save_sfig(archive)
    restored=Project.load_sfig(archive)
    assert restored.panels[0].analysis['nuclei_measurements'][0]['parameters']['he_settings']==params['he_settings']
    c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'));c.load_recipe()
    assert c.controls.diameter.value()==5 and c.controls.maximum.value()==200
    assert c.controls.parameters()['he_settings']['minimum_width_fraction']==.22
    w.close();APP.processEvents()


@pytest.mark.parametrize('module',['fluoro','brightfield'])
def test_native_count_background_job_export_and_project_roundtrip(tmp_path,desktop_screen,module):
    w=window();c=w.nuclei_controllers[module]
    panel=c.panel()
    y,x=np.mgrid[:80,:100]
    gray=(3000*np.exp(-((x-25)**2+(y-40)**2)/30)+3000*np.exp(-((x-75)**2+(y-40)**2)/30)).astype(np.uint16)
    if module=='brightfield':
        rgb=np.full((80,100,3),[240,150,185],np.uint8);rgb[gray>500]=[65,40,100]
        path=tmp_path/'he.png';Image.fromarray(rgb).save(path)
    else:
        path=tmp_path/'dapi.tif';Image.fromarray(gray).save(path)
    record=panel.import_image(path);w.set_module(module);c.sync()
    if module=='fluoro':
        record['channels'][0]['name']='DAPI';c.sync()
    raw=panel.image_data().copy() if module=='fluoro' else panel._load_image_rgb(record['uid'])[1].copy()
    assert c.controls.method.findData('cellpose_nuclei')>=0
    c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'))
    c.controls.run_button.click();wait_job(w,c.job_key)
    result=c.current_result()
    assert result and len(result['nuclei'])==2, c.controls.summary.text()
    after=panel.image_data() if module=='fluoro' else panel._load_image_rgb(record['uid'])[1]
    np.testing.assert_array_equal(after,raw)
    target=tmp_path/(module+'.csv');c.export_csv(target)
    assert 'object_id' in target.read_text(encoding='utf-8-sig')
    overlay=tmp_path/(module+'.png');c.export_overlay(overlay);assert overlay.exists()
    project=Project(title='Nuclei');project.add_panel(panel)
    archive=tmp_path/(module+'.sfig');project.save_sfig(archive)
    restored=Project.load_sfig(archive)
    assert restored.panels[0].analysis['nuclei_measurements']==panel.analysis['nuclei_measurements']
    w.close();APP.processEvents()


def test_fiber_review_updates_distribution_and_can_exclude_bad_segmentation(tmp_path,desktop_screen):
    from core.image_analysis.muscle_nuclei import classify_fiber_nuclei
    w=window();c=w.central_nuclei_controller;panel=c.panel()
    path=tmp_path/'he.png';Image.fromarray(np.full((80,120,3),200,np.uint8)).save(path)
    record=panel.import_image(path);w.set_module('brightfield');c.sync()
    fibers=np.zeros((80,120),np.int32);fibers[5:75,5:55]=1;fibers[5:75,65:115]=2
    nuclei=np.zeros_like(fibers);nuclei[35:40,30:35]=1
    result=classify_fiber_nuclei(fibers,nuclei)
    result.update(task='muscle',image_uid=record['uid'],parameters={},provenance={})
    w.measurement_tasks['brightfield'].select('central')
    c.accept_result(result);dialog=c.open_review()
    assert dialog.distribution_table.rowCount()==2
    assert dialog.distribution_table.item(0,1).text()=='1'
    c.reclassify(1,'peripheral',1)
    assert c.current_result()['summary']['distribution'][0]['fiber_count']==2
    c.set_fiber_included(2,False)
    assert c.current_result()['summary']['included_fibers']==1
    target=tmp_path/'distribution.csv';c.export_csv(target)
    assert (tmp_path/'distribution_distribution.csv').is_file()
    assert json.dumps(c.current_result())
    w.close();APP.processEvents()


def test_count_results_are_dropped_after_switching_image(tmp_path,desktop_screen,monkeypatch):
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    path=tmp_path/'he.png';Image.fromarray(np.full((80,100,3),[240,150,185],np.uint8)).save(path)
    panel.import_image(path);w.set_module('brightfield');c.sync()
    from core.image_analysis import nuclei_counting
    original=nuclei_counting.count_nuclei
    def delayed(*args,**kwargs):
        time.sleep(.15);return original(*args,**kwargs)
    monkeypatch.setattr(nuclei_counting,'count_nuclei',delayed)
    c.run();panel.import_image(path);c.sync();wait_job(w,c.job_key)
    assert not panel.analysis.get('nuclei_measurements')
    w.close();APP.processEvents()


def test_dapi_channel_selection_does_not_show_another_channels_result(tmp_path,desktop_screen):
    import tifffile
    w=window();c=w.nuclei_controllers['fluoro'];panel=c.panel()
    path=tmp_path/'channels.tif';data=np.zeros((2,60,80),np.uint16);data[0,20:30,20:30]=1000
    tifffile.imwrite(path,data,metadata={'axes':'CYX'},photometric='minisblack')
    record=panel.import_image(path);record['channels'][0]['name']='DAPI';w.set_module('fluoro');c.sync()
    c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'))
    c.run();wait_job(w,c.job_key)
    assert c.current_result()['channel_index']==0
    c.controls.channel.setCurrentIndex(1)
    assert c.current_result() is None
    assert not c.controls.review_button.isEnabled()
    c.controls.channel.setCurrentIndex(0)
    assert c.current_result() is not None and c.controls.review_button.isEnabled()
    w.close();APP.processEvents()


def test_dapi_input_change_invalidates_pending_snapshot(tmp_path,desktop_screen,monkeypatch):
    w=window();c=w.nuclei_controllers['fluoro'];panel=c.panel()
    raw=np.zeros((60,80),np.uint16);raw[20:30,20:30]=1000
    path=tmp_path/'dapi.tif';Image.fromarray(raw).save(path)
    record=panel.import_image(path);record['channels'][0]['name']='DAPI'
    w.set_module('fluoro');c.sync();pending=[]
    monkeypatch.setattr(w._analysis_job_service,'start',lambda **kwargs:pending.append(kwargs))
    c.controls.set_parameters(dict(method='cellpose_nuclei',cellpose_input='raw'))
    c.run();assert pending[0]['is_current']()
    c.controls.cellpose_input.setChecked(True)
    assert not pending[0]['is_current']()
    assert not panel.analysis.get('nuclei_measurements')
    w.close();APP.processEvents()


def test_nuclear_method_and_channel_restore_after_project_reopen(tmp_path,desktop_screen):
    import tifffile
    w=window();c=w.nuclei_controllers['fluoro'];panel=c.panel()
    path=tmp_path/'channels.tif';data=np.zeros((2,60,80),np.uint16);data[1,20:30,20:30]=1000
    tifffile.imwrite(path,data,metadata={'axes':'CYX'},photometric='minisblack')
    panel.import_image(path);w.set_module('fluoro');c.sync()
    c.controls.channel.setCurrentIndex(1);c.controls.diameter.setValue(24)
    c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'));c.run();wait_job(w,c.job_key)
    archive=tmp_path/'count.sfig';w.project.save_sfig(archive);restored=Project.load_sfig(archive)
    c.controls.diameter.setValue(17);c.controls.channel.setCurrentIndex(0)
    c.controls.method.setCurrentIndex(c.controls.method.findData('cellpose_nuclei'))
    w.project=restored;c.sync()
    assert c.controls.channel.currentData()==1
    assert c.controls.diameter.value()==24
    assert c.controls.method.currentData()=='watershed'
    assert c.current_result()
    w.close();APP.processEvents()


def test_current_roi_change_invalidates_pending_count(tmp_path,desktop_screen,monkeypatch):
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    path=tmp_path/'he.png';Image.fromarray(np.full((80,100,3),[240,150,185],np.uint8)).save(path)
    record=panel.import_image(path);panel.set_image_roi(record['uid'],'rectangle',dict(x=5,y=5,w=40,h=40))
    w.set_module('brightfield');c.sync();c.controls.roi.setCurrentIndex(c.controls.roi.findData('roi'))
    from core.image_analysis import nuclei_counting
    original=nuclei_counting.count_nuclei
    def delayed(*args,**kwargs):
        time.sleep(.15);return original(*args,**kwargs)
    monkeypatch.setattr(nuclei_counting,'count_nuclei',delayed)
    c.run();panel.set_image_roi(record['uid'],'rectangle',dict(x=10,y=10,w=20,h=20));wait_job(w,c.job_key)
    assert not panel.analysis.get('nuclei_measurements')
    w.close();APP.processEvents()


def test_nuclei_controls_and_review_are_localized(tmp_path,desktop_screen):
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    path=tmp_path/'he.png';Image.fromarray(np.full((80,100,3),200,np.uint8)).save(path)
    record=panel.import_image(path);w.set_module('brightfield');c.sync();w.switch_language('zh')
    assert c.controls.method.itemText(c.controls.method.findData('watershed'))=='局部对比分水岭'
    c.accept_result(dict(task='he',image_uid=record['uid'],nuclei=[],parameters=dict(method=c.controls.method.currentData()),provenance={}))
    dialog=c.open_review()
    assert '编号' in dialog.nucleus_table.horizontalHeaderItem(0).text()
    w.switch_language('en');assert c.controls.method.itemText(c.controls.method.findData('watershed'))=='Local-contrast watershed'
    w.close();APP.processEvents()


@pytest.mark.parametrize('module',['fluoro','brightfield'])
def test_measure_footer_runs_the_open_nuclear_task(desktop_screen,module):
    w=window();w.set_module(module);c=w.nuclei_controllers[module]
    if module=='fluoro':w.fluoro_workspace.set_step(2)
    else:w.data_panel.set_brightfield_step(2,emit_signal=False)
    c.controls.entry.setChecked(True);APP.processEvents();w.workflow_ui.sync()
    assert w.workflow_ui.footers[module].source is c.controls.run_button
    w.measurement_tasks[module].select('myotube');APP.processEvents();w.workflow_ui.sync()
    assert w.workflow_ui.footers[module].source is not c.controls.run_button
    w.close();APP.processEvents()


@pytest.mark.parametrize('module',['fluoro','brightfield'])
def test_multi_image_counts_review_parameters_and_export_follow_selection(tmp_path,desktop_screen,module):
    from core.utils.i18n import tr
    w=window();c=w.nuclei_controllers[module];panel=c.panel();records=[]
    y,x=np.mgrid[:80,:120]
    for index,count in enumerate((1,2,0)):
        gray=sum((2500*np.exp(-((x-(25+60*n))**2+(y-40)**2)/35) for n in range(count)),np.zeros((80,120))).astype(np.uint16)
        path=tmp_path/f'image-{index}.png' if module=='brightfield' else tmp_path/f'image-{index}.tif'
        if module=='brightfield':
            pixels=np.full((80,120,3),[240,150,185],np.uint8);pixels[gray>500]=[65,40,100]
        else:pixels=gray
        Image.fromarray(pixels).save(path);record=panel.import_image(path)
        if module=='fluoro':record['channels'][0]['name']='DAPI'
        records.append(record)
    w.set_module(module);c.controls.entry.setChecked(True)
    combo=w.fluoro_workspace.active_image_combo if module=='fluoro' else w.data_panel.brightfield_active_image_combo
    for record,diameter,count in ((records[0],12,1),(records[1],20,2)):
        combo.setCurrentIndex(combo.findData(record['uid']));APP.processEvents()
        assert panel.active_image()['uid']==record['uid']
        assert c.current_result() is None
        assert c.controls.summary.text()==tr('nuclei_no_result')
        c.controls.diameter.setValue(diameter);c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'))
        c.controls.run_button.click();wait_job(w,c.job_key)
        assert len(c.current_result()['nuclei'])==count
    dialog=c.open_review();assert dialog.nucleus_table.rowCount()==2
    combo.setCurrentIndex(combo.findData(records[0]['uid']));APP.processEvents()
    assert not dialog.isVisible()
    assert len(c.current_result()['nuclei'])==1 and c.controls.diameter.value()==12
    dialog=c.open_review();assert dialog.nucleus_table.rowCount()==1
    target=tmp_path/'selected-image.csv';c.export_csv(target)
    import csv
    rows=list(csv.DictReader(target.open(encoding='utf-8-sig')))
    assert len(rows)==1 and rows[0]['image_name']==records[0]['source_name']
    combo.setCurrentIndex(combo.findData(records[2]['uid']));APP.processEvents()
    assert c.current_result() is None and not dialog.isVisible()
    assert c.controls.summary.text()==tr('nuclei_no_result') and not c.controls.csv_button.isEnabled()
    combo.setCurrentIndex(combo.findData(records[1]['uid']));APP.processEvents()
    assert len(c.current_result()['nuclei'])==2 and c.controls.diameter.value()==20
    w.close();APP.processEvents()


@pytest.mark.parametrize('module',['fluoro','brightfield'])
def test_importing_another_image_clears_the_previous_count_display(tmp_path,desktop_screen,module):
    from core.utils.i18n import tr
    w=window();c=w.nuclei_controllers[module];panel=c.panel()
    path=tmp_path/'source.png';Image.fromarray(np.full((50,70,3),[200,170,180],np.uint8)).save(path)
    record=panel.import_image(path);w.set_module(module);c.controls.entry.setChecked(True)
    if module=='fluoro':c.controls.channel.setCurrentIndex(0)
    task='dapi' if module=='fluoro' else 'he'
    c.accept_result(dict(task=task,image_uid=record['uid'],channel_index=0 if module=='fluoro' else None,
                         nuclei=[],parameters=c.controls.parameters(),provenance={}))
    assert c.controls.csv_button.isEnabled()
    getattr(w,'import_fluorescence_images' if module=='fluoro' else 'import_brightfield_images')([str(path)])
    wait_job(w,'import:'+module)
    assert panel.active_image()['uid']!=record['uid']
    assert c.current_result() is None
    assert c.controls.summary.text()==tr('nuclei_no_result')
    assert not c.controls.csv_button.isEnabled()
    w.close();APP.processEvents()


def test_joint_analysis_reuses_independent_area_and_nuclear_measurements(tmp_path,desktop_screen,monkeypatch):
    from copy import deepcopy
    from core.image_analysis import nuclei_counting, he_fiber_area
    from core.models import brightfield_panel
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    rgb=np.full((150,400,3),245,np.uint8)
    for x in (30,100,170):
        rgb[30:120,x:x+60]=[235,70,160];rgb[70:76,x+25:x+31]=[40,30,100]
    path=tmp_path/'separate.png';Image.fromarray(rgb).save(path)
    record=panel.import_image(path);w.set_module('brightfield');c.sync()
    c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'))
    c.load_recipe();c.run();wait_job(w,c.job_key)
    nuclear=c.current_result();assert nuclear and len(nuclear['nuclei'])==3
    assert 'fibers' not in nuclear
    panel.calibrate_he_fiber_area()
    fiber=panel.create_ai_object_count_measurement(settings=dict(he_fiber_segmentation_method='anti_oversegmentation'))
    saved_fibers=panel._he_fiber_area_labels_for_measurement(fiber).copy()
    original_fiber=deepcopy(fiber);original_nuclear=deepcopy(nuclear)
    original_segmenters=(nuclei_counting.count_nuclei,he_fiber_area.analyze_he_fiber_area,brightfield_panel.analyze_he_fiber_area)
    def forbidden(*_args,**_kwargs):raise AssertionError('Joint analysis must not segment again')
    monkeypatch.setattr(nuclei_counting,'count_nuclei',forbidden)
    monkeypatch.setattr(he_fiber_area,'analyze_he_fiber_area',forbidden)
    monkeypatch.setattr(brightfield_panel,'analyze_he_fiber_area',forbidden)
    c=w.central_nuclei_controller;w.measurement_tasks['brightfield'].select('central');c.sync()
    assert hasattr(c.controls,'fiber_source') and hasattr(c.controls,'nuclear_source')
    assert c.controls.fiber_source.currentData()==fiber['measurement_uuid']
    from core.utils.i18n import tr
    assert tr('he_fiber_method_anti_oversegmentation') in c.controls.fiber_source.currentText()
    assert c.controls.nuclear_source.currentData()==nuclear['measurement_uuid']
    assert c.controls.nuclear_options.isHidden()
    c.run();wait_job(w,c.job_key)
    joint=c.current_result();assert joint, c.controls.summary.text()
    assert joint['source_measurements']==dict(fibers=fiber['measurement_uuid'],nuclei=nuclear['measurement_uuid'])
    assert len(joint['fibers'])==fiber['results']['he_fiber_count']
    assert [r['contour'] for r in joint['nuclei']]==[r['contour'] for r in nuclear['nuclei']]
    assert fiber==original_fiber and nuclear==original_nuclear
    np.testing.assert_array_equal(panel._he_fiber_area_labels_for_measurement(fiber),saved_fibers)
    c.reclassify(joint['nuclei'][0]['object_id'],'outside')
    assert fiber==original_fiber and nuclear==original_nuclear
    alternative=deepcopy(nuclear);alternative['measurement_uuid']='alternate-nuclear-result'
    panel.analysis['nuclei_measurements'].append(alternative);c.sync()
    c.controls.nuclear_source.setCurrentIndex(c.controls.nuclear_source.findData(alternative['measurement_uuid']))
    assert c.current_result() is None and not c.controls.csv_button.isEnabled()
    c.controls.nuclear_source.setCurrentIndex(c.controls.nuclear_source.findData(nuclear['measurement_uuid']))
    assert c.current_result()['measurement_uuid']==joint['measurement_uuid']
    panel.analysis['nuclei_measurements'].remove(alternative)
    w.measurement_tasks['brightfield'].select('he')
    assert w.nuclei_controllers['brightfield'].current_result()==original_nuclear
    w.measurement_tasks['brightfield'].select('central')
    assert c.controls.nuclear_source.currentData()==nuclear['measurement_uuid']
    second=panel.import_image(path);c.sync()
    assert c.controls.fiber_source.count()==0 and c.controls.nuclear_source.count()==0
    assert not c.controls.run_button.isEnabled()
    panel.set_active_image(record['uid']);c.sync()
    assert c.controls.fiber_source.currentData()==fiber['measurement_uuid']
    archive=tmp_path/'joint.sfig';w.project.save_sfig(archive);w.project=Project.load_sfig(archive);c.sync()
    assert c.current_result()['source_measurements']==joint['source_measurements']
    monkeypatch.setattr(nuclei_counting,'count_nuclei',original_segmenters[0])
    monkeypatch.setattr(he_fiber_area,'analyze_he_fiber_area',original_segmenters[1])
    monkeypatch.setattr(brightfield_panel,'analyze_he_fiber_area',original_segmenters[2])
    c.run();wait_job(w,c.job_key)
    assert c.current_result()['source_measurements']==joint['source_measurements']
    assert [r['contour'] for r in c.current_result()['fibers']]==[r['contour'] for r in joint['fibers']]
    w.close();APP.processEvents()


def test_joint_analysis_requires_independent_results_before_running(tmp_path,desktop_screen):
    from core.utils.i18n import tr
    w=window();c=w.central_nuclei_controller;panel=c.panel()
    path=tmp_path/'empty.png';Image.fromarray(np.full((80,100,3),200,np.uint8)).save(path)
    panel.import_image(path);w.set_module('brightfield');c.sync()
    w.measurement_tasks['brightfield'].select('central');c.sync()
    assert not c.controls.run_button.isEnabled()
    c.run();wait_job(w,c.job_key)
    assert not panel.analysis.get('nuclei_measurements')
    assert c.controls.summary.text()==tr('nuclei_joint_missing')
    w.close();APP.processEvents()


def test_joint_analysis_keeps_background_error_visible(tmp_path,desktop_screen,monkeypatch):
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    path=tmp_path/'source.png';Image.fromarray(np.full((80,100,3),200,np.uint8)).save(path)
    record=panel.import_image(path);w.set_module('brightfield');c.sync()
    panel.analysis['brightfield_ai_object_count_measurements']=[dict(image_uid=record['uid'],measurement_uuid='fiber',
        results=dict(he_fiber_count=1),analysis_metadata=dict(analysis_kind='he_fiber_area'),params={})]
    c.accept_result(dict(task='he',image_uid=record['uid'],nuclei=[],parameters={},provenance={}))
    def unreadable(_measurement):raise ValueError('Saved fiber boundary cannot be restored')
    monkeypatch.setattr(panel,'_he_fiber_area_labels_for_measurement',unreadable)
    c=w.central_nuclei_controller;w.measurement_tasks['brightfield'].select('central');c.sync();c.run();wait_job(w,c.job_key)
    assert c.controls.summary.text()=='Saved fiber boundary cannot be restored'
    assert c.controls.run_button.isEnabled()
    w.close();APP.processEvents()


def test_joint_analysis_rejects_results_from_different_scopes(tmp_path,desktop_screen):
    from core.utils.i18n import tr
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    path=tmp_path/'source.png';Image.fromarray(np.full((80,100,3),200,np.uint8)).save(path)
    record=panel.import_image(path);w.set_module('brightfield');c.sync()
    panel.analysis['brightfield_ai_object_count_measurements']=[dict(image_uid=record['uid'],measurement_uuid='fiber',
        results=dict(he_fiber_count=1),analysis_metadata=dict(analysis_kind='he_fiber_area'),params={},roi=dict(mode='whole_image'))]
    c.accept_result(dict(task='he',image_uid=record['uid'],nuclei=[],parameters={},provenance={},
                         roi=dict(mode='roi',type='rectangle',geometry=dict(x=5,y=5,w=20,h=20))))
    c=w.central_nuclei_controller;w.measurement_tasks['brightfield'].select('central');c.sync();c.run()
    assert c.current_result() is None
    assert c.controls.summary.text()==tr('nuclei_joint_scope_mismatch')
    assert not w._analysis_job_service.is_busy(c.job_key)
    w.close();APP.processEvents()


def test_joint_export_population_evidence_and_original_review_toggle(tmp_path,desktop_screen):
    import csv
    from copy import deepcopy
    from core.image_analysis.muscle_nuclei import classify_fiber_nuclei
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    path=tmp_path/'review.png';Image.fromarray(np.full((100,160,3),[210,140,180],np.uint8)).save(path)
    image=panel.import_image(path);w.set_module('brightfield');c.sync()
    fibers=np.zeros((100,160),np.int32);fibers[10:90,10:75]=1;fibers[10:90,85:150]=2
    labels=np.zeros_like(fibers);labels[45:50,40:45]=1;labels[7:9,40:46]=2
    result=classify_fiber_nuclei(fibers,labels)
    result.update(task='muscle',image_uid=image['uid'],parameters={},provenance={})
    c=w.central_nuclei_controller;w.measurement_tasks['brightfield'].select('central');c.accept_result(result)
    c.set_fiber_included(2,False)
    before=deepcopy(c.current_result())
    dialog=c.open_review()
    assert hasattr(dialog,'original_button')
    dialog.original_button.click();APP.processEvents()
    assert dialog.original_button.isChecked()
    assert c.current_result()==before
    dialog.original_button.click();APP.processEvents()
    target=tmp_path/'review.csv';c.export_csv(target)
    rows=list(csv.DictReader(target.open(encoding='utf-8-sig')))
    assert 'assignment_reason' in rows[0] and 'normalized_nuclear_edge_distance' in rows[0]
    distribution=list(csv.DictReader(target.with_name('review_distribution.csv').open(encoding='utf-8-sig')))
    assert {int(r['included_fibers']) for r in distribution}=={1}
    exported=list(csv.DictReader(target.with_name('review_fibers.csv').open(encoding='utf-8-sig')))
    assert exported[0]['included_in_central_distribution']=='True'
    assert exported[1]['included_in_central_distribution']=='False'
    assert c.current_result()['summary']['peripheral_review_fibers']==1
    w.close();APP.processEvents()


def test_refined_he_recipe_keeps_local_and_round_settings_separate(tmp_path,desktop_screen):
    w=window();c=w.nuclei_controllers['brightfield']
    path=tmp_path/'recipe.png';Image.fromarray(np.full((50,70,3),[210,140,180],np.uint8)).save(path)
    c.panel().import_image(path);w.set_module('brightfield');c.sync()
    index=c.controls.method.findData('he_refined')
    assert index>=0
    c.controls.diameter.setValue(19);c.controls.method.setCurrentIndex(index)
    assert c.controls.diameter.value()==19
    c.load_recipe()
    params=c.controls.parameters()
    assert params['method']=='he_refined' and params['diameter_px']==12
    assert params['he_settings']['peak_prominence_fraction']==.125
    assert params['local_he_settings']['minimum_circularity']==.25
    params['local_he_settings']['clip_percentile']=98.
    c.controls.set_parameters(params)
    assert c.controls.parameters()['local_he_settings']['clip_percentile']==98.
    c.controls.method.setCurrentIndex(c.controls.method.findData('he_round'));c.load_recipe()
    assert 'peak_prominence_fraction' not in c.controls.parameters()['he_settings']
    assert 'local_he_settings' not in c.controls.parameters()
    w.close();APP.processEvents()


def test_shared_nuclear_geometry_editor_adds_new_result_and_cancel_is_nonmutating(tmp_path,desktop_screen):
    from copy import deepcopy
    from core.image_analysis.nuclei_counting import object_rows
    w=window();c=w.nuclei_controllers['brightfield'];panel=c.panel()
    path=tmp_path/'geometry.png';Image.fromarray(np.full((80,100,3),[210,140,180],np.uint8)).save(path)
    image=panel.import_image(path);w.set_module('brightfield');c.sync()
    labels=np.zeros((80,100),np.int32);labels[20:25,30:35]=1
    rows=object_rows(labels,labels)
    for row in rows:row['classification']='included'
    c.accept_result(dict(task='he',image_uid=image['uid'],nuclei=rows,parameters=dict(method=c.controls.method.currentData()),provenance={}))
    old=deepcopy(c.current_result())
    review=c.open_review();assert hasattr(review,'geometry_button')
    dialog=c.open_geometry_review();identifier=dialog.session.add_polygon([[60,40],[65,40],[65,45],[60,45]])
    dialog._apply();APP.processEvents()
    assert c.current_result()['measurement_uuid']!=old['measurement_uuid']
    assert len(c.current_result()['nuclei'])==2
    assert panel.analysis['nuclei_measurements'][0]==old
    assert next(row for row in c.current_result()['nuclei'] if row['object_id']==identifier)['reviewed']
    before=deepcopy(panel.analysis['nuclei_measurements'])
    dialog=c.open_geometry_review();dialog.session.delete_objects([identifier]);dialog.reject();APP.processEvents()
    assert panel.analysis['nuclei_measurements']==before
    w.close();APP.processEvents()

