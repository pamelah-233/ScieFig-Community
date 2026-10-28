from copy import deepcopy
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from tests.test_measurement_main_preview import preview_window, import_example


def test_every_measurement_task_has_a_first_level_result_list(preview_window):
    w=preview_window
    for module,deck in w.measurement_tasks.items():
        w.set_module(module)
        if module=='fluoro':w.fluoro_workspace.set_step(2)
        else:w.workflow_ui.buttons[module][2].click()
        for task in deck.pages:
            deck.select(task);QTest.qWait(5)
            history=w.measurement_history.views[module]
            assert history.isVisible(),(module,task)
            assert not any(parent.isHidden() for parent in (history,history.parentWidget()))
            panel=w.workflow_ui.panels[module];layout=panel.layout()
            assert deck.scroll.widget().isAncestorOf(history)


@pytest.mark.parametrize('populated',[False,True])
def test_all_modules_show_results_in_the_workflow_scroll_area(preview_window,populated,tmp_path):
    from PyQt6.QtWidgets import QScrollArea
    from PyQt6.QtCore import QPoint
    w=preview_window
    if populated:
        import numpy as np
        import tifffile
        from PyQt6.QtWidgets import QTableWidgetItem
        path=tmp_path/'channels.tif'
        tifffile.imwrite(path,np.ones((2,64,80),np.uint8),metadata={'axes':'CYX'},photometric='minisblack')
        panel=w._active_fluoro_panel();image=panel.import_image(path)
        panel.analysis['nuclei_measurements']=[dict(measurement_uuid='run',image_uid=image['uid'],task='dapi',
            channel_index=0,nuclei=[],parameters=dict(method='watershed',diameter_px=25))]
        from PIL import Image
        source=tmp_path/'brightfield.png';Image.fromarray(np.full((64,80,3),150,np.uint8)).save(source)
        panel=w._active_brightfield_panel();image=panel.import_image(source)
        panel.analysis['nuclei_measurements']=[dict(measurement_uuid='he-run',image_uid=image['uid'],task='he',
            nuclei=[],parameters=dict(method='watershed',diameter_px=17))]
    for module,step in [('qpcr',1),('wb',3),('ihc',1),('fluoro',2),('brightfield',2)]:
        w.set_module(module);w.workflow_ui.buttons[module][step].click();QTest.qWait(15)
        if module=='fluoro':w.measurement_tasks[module].select('dapi');QTest.qWait(10)
        if module=='brightfield':w.measurement_tasks[module].select('he');QTest.qWait(10)
        if populated:
            table={'ihc':w.data_panel.ihc_results_table,'wb':w.data_panel.wb_density_result_table}.get(module)
            if table is not None:
                table.setColumnCount(max(1,table.columnCount()));table.setRowCount(1)
                table.setItem(0,0,QTableWidgetItem('Saved result'))
            if module=='qpcr':w.data_panel.summary.setText('qPCR analysis complete\nControl: 1.0\nTreatment: 1.8')
        h=w.measurement_history
        view={**h.views,**h.fixed_views}[module]
        panel=w.workflow_ui.panels[module];layout=panel.layout()
        assert view.isVisible(),module
        scroll=panel.findChild(QScrollArea)
        scroll.setFixedHeight(120);QTest.qWait(10)
        assert scroll.widget().isAncestorOf(view),module
        assert not view.isAncestorOf(w.workflow_ui.footers[module])
        bar=scroll.verticalScrollBar();bar.setValue(0);QTest.qWait(5)
        # Flush nested form/table layout requests before comparing positions;
        # a queued header resize is independent of scrollbar movement.
        from PyQt6.QtWidgets import QApplication
        for _ in range(8):QApplication.processEvents()
        before=view.mapTo(scroll.viewport(),QPoint()).y()
        assert bar.maximum()>0,(module,populated)
        bar.setValue(min(60,bar.maximum()));QTest.qWait(5)
        for _ in range(8):QApplication.processEvents()
        after=view.mapTo(scroll.viewport(),QPoint()).y()
        assert after==before-bar.value(),(module,populated,before,after,bar.value())
        scroll.ensureWidgetVisible(view);QTest.qWait(5)
        assert not view.visibleRegion().isEmpty(),(module,populated)


def test_nuclear_history_restores_image_run_channel_and_overlay(preview_window,tmp_path):
    w=preview_window
    panel,image,path=import_example(w,tmp_path,'fluoro')
    second=panel.import_image(path)
    records=[]
    for index,(uid,diameter) in enumerate(((image['uid'],25),(second['uid'],35),(image['uid'],45))):
        records.append(dict(measurement_uuid=f'run-{index}',image_uid=uid,image_name=f'image-{index}',task='dapi',
            channel_index=0,parameters=dict(method='watershed',diameter_px=diameter,min_area_px=8,max_area_px=2000),
            nuclei=[dict(id=1,contour=[[10+index,10],[20+index,10],[20+index,20],[10+index,20]],include_in_statistics=True)]))
    panel.analysis['nuclei_measurements']=records
    w.fluoro_workspace.refresh();deck=w.measurement_tasks['fluoro'];deck.select('dapi')
    QTest.qWait(20)
    history=w.measurement_history.views['fluoro']
    assert history.table.rowCount()==3
    history.table.setCurrentCell(2,0);QTest.qWait(30)
    assert panel.active_image()['uid']==image['uid']
    assert deck.controllers['dapi'].current_result()['measurement_uuid']=='run-0'
    assert deck.controllers['dapi'].controls.diameter.value()==25
    canvas=w.fluoro_workspace.canvas
    assert canvas.active_measurement_overlay_uuid=='dapi:run-0'
    assert canvas._measurement_overlay_items
    original=deepcopy(panel.analysis)
    w.fluoro_workspace.ai_original_preview_button.click()
    w.fluoro_workspace._refresh_canvas();QTest.qWait(20)
    assert not any(item.isVisible() for item in canvas._measurement_overlay_items)
    history.table.setCurrentCell(1,0);QTest.qWait(25)
    assert panel.active_image()['uid']==second['uid']
    assert deck.controllers['dapi'].current_result()['measurement_uuid']=='run-1'
    assert any(item.isVisible() for item in canvas._measurement_overlay_items)
    assert panel.analysis==original


def test_profile_and_overlap_keep_separate_runs(preview_window,tmp_path):
    w=preview_window
    panel,image,path=import_example(w,tmp_path,'fluoro')
    first=panel.line_profile(image['uid'],[0],dict(x1=4,y1=5,x2=30,y2=5))
    second=panel.line_profile(image['uid'],[0],dict(x1=4,y1=20,x2=30,y2=20))
    deck=w.measurement_tasks['fluoro'];deck.select('line_profile');QTest.qWait(20)
    table=w.measurement_history.views['fluoro'].table
    assert table.rowCount()==2
    table.setCurrentCell(1,0);QTest.qWait(25)
    assert w.fluoro_workspace.canvas._measurement_overlay_items
    assert panel.analysis['line_profile']==second
    for threshold in (20,60):panel.compute_channel_overlap(image['uid'],[0,1],method='manual',manual_value=threshold)
    deck.select('colocalization');QTest.qWait(20)
    assert table.rowCount()==2


@pytest.mark.parametrize('module,task',[('brightfield','he'),('brightfield','central'),('fluoro','myotube'),('brightfield','myotube')])
def test_independent_history_restores_older_run_on_same_image(preview_window,tmp_path,module,task):
    import numpy as np
    from core.image_analysis.nuclei_counting import object_rows
    from core.image_analysis.muscle_nuclei import classify_fiber_nuclei
    w=preview_window
    panel,image,path=import_example(w,tmp_path,module)
    if module=='brightfield':w.workflow_ui.buttons[module][2].click()
    labels=np.zeros((100,160),np.int32);labels[20:35,20:35]=1
    if task=='central':
        fibers=np.zeros_like(labels);fibers[5:80,5:80]=1
        result=classify_fiber_nuclei(fibers,labels);result['task']='muscle'
    elif task=='he':
        result=dict(task='he',nuclei=object_rows(labels,labels),parameters=dict(method='watershed',diameter_px=17))
    else:
        result=dict(type='myotube_measurement',params=dict(method='cyto3'),results=[],summary=dict(count=0,complete_count=0,border_truncated_count=0),calibrated=False)
    result.update(image_uid=image['uid'],image_name='example',channel_index=1 if module=='fluoro' else None)
    records=[dict(deepcopy(result),measurement_uuid=f'{task}-{index}') for index in range(2)]
    panel.analysis['myotube_measurements' if task=='myotube' else 'nuclei_measurements']=records
    deck=w.measurement_tasks[module];deck.select(task);QTest.qWait(20)
    table=w.measurement_history.views[module].table
    assert table.rowCount()==2
    table.setCurrentCell(1,0);QTest.qWait(25)
    assert deck.controllers[task].current_result()['measurement_uuid']==f'{task}-0'
    assert deck.stack.currentWidget() is deck.pages[task]


@pytest.mark.parametrize('task',['phenotype','count_ratio','fusion'])
def test_relation_history_restores_saved_parameters_and_source_results(preview_window,tmp_path,task):
    import numpy as np
    from tests.test_measurement_relations_workflow import records
    from core.measurement_relations import classify_measurement,measurement_count_ratio,measurement_fusion_index
    w=preview_window
    panel,image,path=import_example(w,tmp_path,'fluoro')
    fibers,nuclei,labels=records(image['uid']);nuclei['channel_index']=1
    panel.analysis['myotube_measurements']=[fibers];panel.analysis['nuclei_measurements']=[nuclei]
    if task=='phenotype':
        result=classify_measurement(nuclei,np.ones((100,160))*100,channel_index=1,
                                   parameters=dict(positive_threshold=20,strong_threshold=80,background=0,region='object'))
        sources={nuclei['measurement_uuid']:deepcopy(nuclei)}
    elif task=='count_ratio':
        result=measurement_count_ratio(nuclei,nuclei,(100,160));sources={nuclei['measurement_uuid']:deepcopy(nuclei)}
    else:
        result=measurement_fusion_index(fibers,nuclei,(100,160),min_nuclei=2)
        sources={fibers['measurement_uuid']:deepcopy(fibers),nuclei['measurement_uuid']:deepcopy(nuclei)}
    result.update(image_name='example',source_snapshots=sources,requested_parameters=result.get('parameters',{}))
    panel.analysis['object_relation_measurements']=[dict(deepcopy(result),measurement_uuid=f'{task}-{i}') for i in range(2)]
    deck=w.measurement_tasks['fluoro'];deck.select(task);QTest.qWait(15)
    table=w.measurement_history.views['fluoro'].table
    assert table.rowCount()==2
    table.setCurrentCell(1,0);QTest.qWait(25)
    assert deck.controllers[task].current_result()['measurement_uuid']==task+'-0'
    assert w.fluoro_workspace.canvas.active_measurement_overlay_uuid==task+':'+task+'-0'
    assert w.fluoro_workspace.canvas._measurement_overlay_items


def test_brightfield_cell_history_keeps_older_measurement_overlay(preview_window,tmp_path):
    w=preview_window
    panel,image,path=import_example(w,tmp_path,'brightfield')
    image['measurement_scale_bar']=dict(um_per_px=.5,length_um=100,calibration_source='manual')
    first=panel.measure_bright_round_cells(settings={'residual_threshold':12})
    second=panel.measure_bright_round_cells(settings={'residual_threshold':18})
    w.workflow_ui.buttons['brightfield'][2].click()
    deck=w.measurement_tasks['brightfield'];deck.select('cells');QTest.qWait(20)
    table=w.measurement_history.views['brightfield'].table
    assert table.rowCount()==2
    table.setCurrentCell(1,0);QTest.qWait(25)
    assert w.measurement_history.selected('brightfield','cells') is first
    assert w.brightfield_workspace.canvas.has_overlay_pixmap()
    assert panel.analysis['bright_round_cell_diameter_measurements'][-1] is second
    assert table.verticalScrollBar().maximum()==0
