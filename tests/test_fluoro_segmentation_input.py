"""Explicit per-image segmentation input; never replace quantitative pixels."""
from pathlib import Path
from copy import deepcopy
import numpy as np
import pytest


@pytest.fixture
def panel(tmp_path):
    import tifffile
    from core.models.fluoro_panel import FluorescencePanel
    p=FluorescencePanel(panel_id='fluoro-input-test');data=np.full((2,80,180),100,np.uint16)
    data[:,20:40,20:150]=400
    path=tmp_path/'signals.tif';tifffile.imwrite(path,data,metadata={'axes':'CYX'},photometric='minisblack')
    record=p.import_image(path)
    for i,ch in enumerate(record['channels']):
        ch.update(name='MYHC' if i==0 else 'DAPI',display_min=0,display_max=1000,display_processing={'strength':50})
    return p,record,data


def test_adjusted_input_matches_channel_processing_not_overlay_or_pseudocolor(panel):
    from core.plotting.fluoro_display import apply_display_optimization
    p,record,data=panel
    np.testing.assert_array_equal(p.segmentation_input_data(record['uid']),data)
    record['segmentation_input']='adjusted'
    adjusted=p.segmentation_input_data(record['uid'])
    expected=np.rint(apply_display_optimization(data[0]/1000,{'strength':50})*255).astype(np.uint8)
    np.testing.assert_array_equal(adjusted[0],expected)
    assert adjusted.dtype==np.uint8 and adjusted.shape==data.shape
    record['channels'][0]['pseudocolor']='#00FF00';record['scale_bar']={'visible':True}
    np.testing.assert_array_equal(p.segmentation_input_data(record['uid']),adjusted)
    context=p.segmentation_input_context(record['uid'])
    assert context['mode']=='adjusted'
    record['channels'][0]['display_processing']['strength']=80
    assert p.segmentation_input_context(record['uid'])!=context
    np.testing.assert_array_equal(p.image_data(record['uid']),data)


@pytest.mark.parametrize('algorithm',['traditional','cellpose_cyto3','cellpose_nuclei','log'])
def test_counters_use_selected_input_and_preserve_raw_intensities(panel,monkeypatch,algorithm):
    from core.image_analysis import cellpose_backend
    p,record,data=panel;record['segmentation_input']='adjusted';calls=[]
    def worker(image,**kwargs):
        calls.append(image.copy());labels=np.zeros(image.shape,np.int32);labels[20:40,20:150]=1
        return labels,{'model':kwargs.get('model')}
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',worker)
    result=p.count_channel_objects(record['uid'],[0],method='manual',manual_value=100,algorithm=algorithm)[0]
    assert result['segmentation_input']['mode']=='adjusted'
    if algorithm.startswith('cellpose'):
        np.testing.assert_array_equal(calls[0],p.segmentation_input_data(record['uid'])[0])
        assert result['mean_intensity']==400
    if algorithm=='traditional':assert result['mean_intensity']==400
    np.testing.assert_array_equal(p.image_data(record['uid']),data)


def test_area_model_uses_adjusted_labels_but_measures_raw_pixels(panel,monkeypatch):
    from core.image_analysis import cellpose_backend
    p,record,data=panel;record['segmentation_input']='adjusted';calls=[]
    def worker(image,**kwargs):
        calls.append(image.copy());labels=np.zeros(image.shape,np.int32);labels[20:40,20:150]=1
        return labels,{'model':'cyto3'}
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',worker)
    result=p.run_ai_assisted_analysis(record['uid'],[0],settings={'backend':'cellpose_cyto3'})[0]
    np.testing.assert_array_equal(calls[0],p.segmentation_input_data(record['uid'])[0])
    assert result['segmentation_input']['mode']=='adjusted'
    assert result['mean_intensity']==400
    assert result['object_table'][0]['mean_intensity']==400
    assert result['integrated_intensity']==400*20*130
    np.testing.assert_array_equal(p.image_data(record['uid']),data)


def test_local_area_and_smart_methods_share_adjusted_input(panel,monkeypatch):
    from core.models import fluoro_panel
    p,record,data=panel;record['segmentation_input']='adjusted';seen=[]
    for name in ('analyze_fluorescence_ai_assisted_local_heuristic','analyze_fluorescence_smart_assisted'):
        original=getattr(fluoro_panel,name)
        def spy(source,*args,_original=original,**kwargs):
            seen.append(source.copy());return _original(source,*args,**kwargs)
        monkeypatch.setattr(fluoro_panel,name,spy)
    ai=p.run_ai_assisted_analysis(record['uid'],[0],settings={'backend':'local_heuristic'})
    smart=p.run_smart_assisted_analysis(record['uid'],[0])
    assert seen and len(seen)>=2
    for source in seen:np.testing.assert_array_equal(source,p.segmentation_input_data(record['uid']))
    for result in ai+smart:assert result['segmentation_input']['mode']=='adjusted'
    np.testing.assert_array_equal(p.image_data(record['uid']),data)


def test_ui_input_is_explicit_per_image_and_part_of_stale_job_snapshots(panel,qt_application,desktop_screen,tmp_path):
    from ui.main_window import MainWindow
    from core.models import Project
    p,record,data=panel
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    try:
        p=w._active_fluoro_panel();record=p.import_image(tmp_path/'signals.tif')
        record['channels'][0]['display_processing']={'strength':50}
        w.set_module('fluoro');w.fluoro_workspace.set_panel(p)
        deck=w.measurement_tasks['fluoro'];assert hasattr(deck,'input_buttons')
        deck.sync_input_selection()
        assert deck.input_buttons['raw'].isChecked()
        before=w.myotube_controllers['fluoro'].input_snapshot()
        deck.input_buttons['adjusted'].click()
        assert record['segmentation_input']=='adjusted'
        assert w.myotube_controllers['fluoro'].input_snapshot()!=before
        assert w.nuclei_controllers['fluoro'].input_snapshot()['segmentation_input']['mode']=='adjusted'
        before=w.fluoro_workspace._current_analysis_inputs_for('ai')
        record['channels'][0]['display_processing']['strength']=80
        assert w.fluoro_workspace._current_analysis_inputs_for('ai')!=before
        guards=[]
        w.fluoro_workspace.set_analysis_runner(lambda **kwargs:guards.append(kwargs['is_settings_current']) or True)
        w.fluoro_workspace._run_analysis_operation('ai',lambda x:None,lambda x:None,lambda:True)
        assert guards[-1]()
        record['channels'][0]['display_processing']['strength']=60
        assert not guards[-1]()
        archive=tmp_path/'input.sfig';w.project.save_sfig(archive)
        restored=Project.load_sfig(archive)
        assert next(x for x in restored.panels if x.panel_id==p.panel_id).active_image()['segmentation_input']=='adjusted'
        other=p.import_image(tmp_path/'signals.tif');w.fluoro_workspace.refresh()
        assert deck.input_buttons['raw'].isChecked()
        p.set_active_image(record['uid']);w.fluoro_workspace.refresh()
        assert deck.input_buttons['adjusted'].isChecked()
    finally:
        w._capture_clean_project_baseline();w.close();qt_application.processEvents()


def test_dapi_and_myotube_jobs_receive_adjusted_channel_and_keep_raw_measurements(panel,qt_application,desktop_screen,tmp_path,monkeypatch):
    from ui.main_window import MainWindow
    from core.image_analysis import cellpose_backend,nuclei_counting
    from PyQt6.QtTest import QTest
    import time
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    try:
        p=w._active_fluoro_panel();record=p.import_image(tmp_path/'signals.tif')
        for index,ch in enumerate(record['channels']):ch.update(name='MYHC' if index==0 else 'DAPI',display_min=0,display_max=1000,display_processing={'strength':50})
        record['segmentation_input']='adjusted'
        w.set_module('fluoro');w.fluoro_workspace.refresh()
        seen=[];original=nuclei_counting.count_nuclei
        def worker(image,**kwargs):
            labels=np.zeros(image.shape,np.int32);labels[20:40,20:150]=1
            return labels,{'model':kwargs.get('model')}
        def nuclei(source,**kwargs):seen.append(source.copy());return original(source,**kwargs)
        monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',worker)
        monkeypatch.setattr(nuclei_counting,'count_nuclei',nuclei)
        for task,c in (('dapi',w.nuclei_controllers['fluoro']),('myotube',w.myotube_controllers['fluoro'])):
            w.measurement_tasks['fluoro'].select(task);c.sync()
            if task=='dapi':c.controls.method.setCurrentIndex(c.controls.method.findData('cellpose_nuclei'))
            else:np.testing.assert_array_equal(c._source()[0],p.segmentation_input_data(record['uid'])[0])
            c.run();deadline=time.monotonic()+10
            while w._analysis_job_service.is_busy(c.job_key) and time.monotonic()<deadline:QTest.qWait(20)
            result=c.current_result();assert result is not None,c.controls.summary.text()
            assert result['segmentation_input']['mode']=='adjusted'
            if task=='myotube':assert result['results'][0]['mean_intensity']==400
        np.testing.assert_array_equal(seen[0],p.segmentation_input_data(record['uid'])[1])
        np.testing.assert_array_equal(p.image_data(record['uid']),panel[2])
        p.run_ai_assisted_analysis(record['uid'],[0],settings={'backend':'cellpose_cyto3'})
        w.fluoro_workspace.refresh();w.measurement_tasks['fluoro'].select('ai_assisted')
        f=w.fluoro_workspace
        assert f.ai_original_preview_button.isEnabled() and f.ai_overlay_preview_button.isEnabled()
        f.ai_overlay_preview_button.click();assert f.ai_overlay_preview_button.isChecked()
        f.ai_original_preview_button.click();assert f.ai_original_preview_button.isChecked()
    finally:
        w._capture_clean_project_baseline();w.close();qt_application.processEvents()
