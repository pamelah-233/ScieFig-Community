"""Main-canvas results, history selection and original/overlay state."""
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image
from PyQt6.QtTest import QTest
from PyQt6.QtCore import QPoint, Qt, QItemSelectionModel


@pytest.fixture
def preview_window(qt_application, desktop_screen):
    from ui.main_window import MainWindow
    w = MainWindow(Path(__file__).resolve().parents[1]/'resources')
    w.project_recovery.timer.stop()
    w.project_recovery._alive = False
    w._confirm_destructive_action = lambda *args: True
    w.resize(1450, 900)
    w.show()
    w.v9_shell.model_device_controller.shutdown()
    yield w
    w.close()
    qt_application.processEvents()


def import_example(w, tmp_path, module):
    panel = getattr(w, '_active_'+module+'_panel')()
    path = tmp_path/('channels.tif' if module == 'fluoro' else 'he.png')
    if module == 'fluoro':
        data = np.full((2, 100, 160), 40, np.uint16)
        tifffile.imwrite(path, data, metadata={'axes': 'CYX'}, photometric='minisblack')
    else:
        Image.fromarray(np.full((100, 160, 3), [230, 160, 190], np.uint8)).save(path)
    record = panel.import_image(path)
    w.set_module(module)
    workspace = getattr(w, module+'_workspace')
    if module == 'fluoro':
        workspace.set_panel(panel)
        workspace.set_step(2)
    else:
        panel.data['active_step'] = 2
        w.data_panel.brightfield_context_stack.setCurrentIndex(2)
        workspace.set_step(2)
        w._refresh_brightfield_preview(panel)
    return panel, record, path


@pytest.mark.parametrize('module,task', [('fluoro', 'dapi'), ('brightfield', 'he'), ('brightfield', 'central')])
def test_nuclear_results_show_in_main_canvas_and_toggle(preview_window, tmp_path, module, task):
    from core.image_analysis.nuclei_counting import object_rows
    from core.image_analysis.muscle_nuclei import classify_fiber_nuclei
    w = preview_window
    panel, record, path = import_example(w, tmp_path, module)
    deck = w.measurement_tasks[module]
    deck.select(task)
    c = deck.controllers[task]
    labels = np.zeros((100, 160), np.int32)
    labels[30:40, 35:45] = 1
    if task == 'central':
        fibers = np.zeros_like(labels); fibers[10:85, 10:100] = 1
        result = classify_fiber_nuclei(fibers, labels)
    else:
        c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'))
        result = dict(nuclei=object_rows(labels, labels), parameters=c.controls.parameters())
        for row in result['nuclei']:
            row.update(classification='included', include_in_statistics=True)
    result.update(task='muscle' if task == 'central' else task, image_uid=record['uid'],
                  image_name='example', channel_index=c.controls.channel.currentData() if module == 'fluoro' else None)
    c.accept_result(result)
    QTest.qWait(40)
    workspace = getattr(w, module+'_workspace')
    overlay = workspace.ai_overlay_preview_button if module == 'fluoro' else workspace.overlay_preview_button
    original = workspace.ai_original_preview_button if module == 'fluoro' else workspace.original_preview_button
    canvas = workspace.canvas
    assert overlay.isEnabled() and overlay.isVisible()
    if module == 'fluoro':
        assert canvas._measurement_overlay_items
        assert all(item.isVisible() for item in canvas._measurement_overlay_items)
    else:
        assert canvas.has_overlay_pixmap() and canvas.current_preview_mode() == 'overlay'
    saved = deepcopy(panel.analysis['nuclei_measurements'])
    original.click()
    if module == 'fluoro':
        workspace._refresh_canvas()
    else:
        w._refresh_brightfield_preview(panel)
    QTest.qWait(40)
    if module == 'fluoro':
        assert canvas._measurement_overlay_items
        assert not any(item.isVisible() for item in canvas._measurement_overlay_items)
        workspace.preview_channels.buttons['split'].click()
        QTest.qWait(40)
        assert not any(item.isVisible() for item in canvas._measurement_overlay_items)
    else:
        assert canvas.current_preview_mode() == 'original'
    overlay.click()
    if module == 'fluoro':
        workspace.preview_channels.buttons[1].click(); QTest.qWait(40)
        assert not canvas._measurement_overlay_items, 'Nuclei from channel 0 must not be drawn on channel 1'
        workspace.preview_channels.buttons['merge'].click(); QTest.qWait(40)
        assert canvas._measurement_overlay_items
        workspace.set_step(4); QTest.qWait(40)
        assert not canvas._measurement_overlay_items
        workspace.set_step(2); QTest.qWait(40)
        assert deck.current_task() == task
        assert canvas._measurement_overlay_items
        w.set_module('wb'); QTest.qWait(40)
        w.set_module('fluoro'); QTest.qWait(40)
        assert deck.current_task() == task
        assert canvas._measurement_overlay_items
    assert panel.analysis['nuclei_measurements'] == saved
    second = panel.import_image(path)
    if module == 'fluoro': workspace.refresh()
    else: w._refresh_brightfield_preview(panel)
    c.sync(); QTest.qWait(40)
    assert c.current_result() is None
    assert not (canvas._measurement_overlay_items if module == 'fluoro' else canvas.has_overlay_pixmap())
    panel.set_active_image(record['uid'])
    if module == 'fluoro': workspace.refresh()
    else: w._refresh_brightfield_preview(panel)
    c.sync(); QTest.qWait(40)
    assert c.current_result() is not None
    assert overlay.isEnabled()
    review = c.open_review()
    assert review.isVisible()
    review.close()


def test_fluoro_count_toggle_and_old_result_survive_canvas_refresh(preview_window, tmp_path):
    w = preview_window
    panel, image, _ = import_example(w, tmp_path, 'fluoro')
    ws = w.fluoro_workspace
    rows = [dict(count_uuid=str(i), image_uid=image['uid'], image_name='example', channel_index=0,
                 count=1, detected_spots=[dict(x=10+40*i,y=15,w=10,h=10)]) for i in (1, 2)]
    image['object_counts'] = rows
    panel.analysis['object_counts'] = rows
    w.measurement_tasks['fluoro'].select('spot_count')
    ws.refresh(); QTest.qWait(40)
    ws.count_table.setCurrentCell(0, 0)
    QTest.qWait(40)
    assert ws.canvas.active_measurement_overlay_uuid == '1'
    assert ws.ai_overlay_preview_button.isEnabled()
    ws.ai_original_preview_button.click()
    ws._refresh_canvas(); QTest.qWait(40)
    assert ws.canvas.active_measurement_overlay_uuid == '1'
    assert not any(item.isVisible() for item in ws.canvas._measurement_overlay_items)


def test_preview_toolbar_has_distinct_groups_and_aligned_buttons(preview_window, tmp_path):
    from main import load_qss
    from core.utils import i18n
    from PyQt6.QtWidgets import QApplication
    w = preview_window
    app = QApplication.instance(); previous = app.styleSheet()
    app.setStyleSheet(load_qss(w.resources_dir))
    try:
        import_example(w, tmp_path, 'fluoro')
        ws = w.fluoro_workspace
        w.measurement_tasks['fluoro'].select('dapi')
        for language in ('zh', 'en'):
            w.switch_language(language)
            w.resize(1800, 900); QTest.qWait(40)
            navigation = w.workflow_ui.bars['fluoro']
            group = w.workflow_ui.preview_groups['fluoro'][1]
            assert group.objectName() == 'MeasurementPreviewGroup'
            assert group.height() == navigation.height() == ws.preview_channels.height()
            buttons = [*ws.step_buttons, ws.ai_original_preview_button, ws.ai_overlay_preview_button,
                       ws.preview_channels.buttons['merge'], ws.preview_channels.buttons[0],
                       ws.preview_channels.buttons['split']]
            centers = [button.mapTo(w.v9_shell.stagebar, button.rect().center()).y() for button in buttons]
            assert max(centers)-min(centers) <= 1
            assert len({button.height() for button in buttons}) == 1
            w.resize(940, 760); QTest.qWait(40)
            for button in buttons:
                point = button.mapTo(w.v9_shell.stagebar, QPoint())
                assert point.y() >= 0 and point.y()+button.height() <= w.v9_shell.stagebar.height()
    finally:
        app.setStyleSheet(previous)


def test_ai_history_current_row_wins_over_other_selected_rows(preview_window, tmp_path):
    w = preview_window
    panel, image, _ = import_example(w, tmp_path, 'fluoro')
    ws = w.fluoro_workspace
    records = [dict(ai_measurement_uuid='ai-'+str(i), image_uid=image['uid'], channel_index=0,
                    ai_object_count=1, object_table=[dict(object_id=i, area_px=100,
                        contour_points_px=[dict(x=10+30*i, y=10), dict(x=20+30*i, y=10), dict(x=20+30*i, y=20)])])
               for i in (1, 2)]
    image['fluorescence_ai_assisted_measurements'] = records
    panel.analysis['fluorescence_ai_assisted_measurements'] = records
    w.measurement_tasks['fluoro'].select('ai_assisted')
    ws.refresh(); QTest.qWait(40)
    table = ws.ai_results_table
    table.setCurrentCell(0, 0)
    table.setCurrentCell(1, 0, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    QTest.qWait(40)
    assert {item.data(1) for item in ws.canvas._measurement_overlay_items} == {'ai-2'}
    ws._refresh_canvas(); QTest.qWait(40)
    assert {item.data(1) for item in ws.canvas._measurement_overlay_items} == {'ai-2'}
    ws.refresh(); QTest.qWait(40)
    assert {item.data(1) for item in ws.canvas._measurement_overlay_items} == {'ai-2'}
    table.selectAll()
    table.setCurrentCell(0, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
    QTest.qWait(40)
    assert {item.data(1) for item in ws.canvas._measurement_overlay_items} == {'ai-1'}


def test_pending_preview_is_cancelled_when_window_is_closed(preview_window):
    controller = preview_window.measurement_preview
    controller.schedule()
    assert controller._timer.isActive()
    preview_window.close()
    assert not controller._timer.isActive()


@pytest.mark.parametrize('task', ['phenotype', 'count_ratio', 'fusion'])
def test_derived_measurements_preview_saved_objects_and_hide_stale_results(preview_window, tmp_path, task):
    import time
    from core.image_analysis.nuclei_counting import object_rows
    w = preview_window
    panel, image, _ = import_example(w, tmp_path, 'fluoro')
    fibers = np.zeros((100, 160), np.int32); fibers[10:80, 10:100] = 1
    nuclei = np.zeros_like(fibers); nuclei[25:35,25:35] = 1; nuclei[50:60,60:70] = 2
    first = dict(measurement_uuid='fiber-source', image_uid=image['uid'], image_name='example',
                 channel_index=0, type='myotube_measurement', roi=None,
                 results=[dict(id=r['object_id'],contour_px=r['contour'],include_in_statistics=True)
                          for r in object_rows(fibers,fibers)])
    second = dict(measurement_uuid='nuclei-source', image_uid=image['uid'], image_name='example',
                  channel_index=1, task='dapi', roi=None, nuclei=object_rows(nuclei,nuclei), parameters={'method':'watershed'})
    for row in second['nuclei']:row.update(classification='included',include_in_statistics=True)
    panel.analysis['myotube_measurements'] = [first]
    panel.analysis['nuclei_measurements'] = [second]
    deck = w.measurement_tasks['fluoro']; deck.select(task); c = deck.controllers[task]
    c.sync()
    c.controls.first.setCurrentIndex(c.controls.first.findData('nuclei-source' if task=='phenotype' else 'fiber-source'))
    if task == 'phenotype':c.controls.channel.setCurrentIndex(c.controls.channel.findData(1))
    else:c.controls.second.setCurrentIndex(c.controls.second.findData('nuclei-source'))
    c.run()
    deadline = time.monotonic()+20
    while w._analysis_job_service.is_busy(c.job_key) and time.monotonic()<deadline:QTest.qWait(20)
    QTest.qWait(40)
    result = c.current_result()
    assert result is not None, c.controls.summary.text()
    ws = w.fluoro_workspace
    assert len(ws.canvas._measurement_overlay_items) == (2 if task=='phenotype' else 3)
    assert ws.ai_overlay_preview_button.isEnabled()
    ws.ai_original_preview_button.click();c.sync();QTest.qWait(40)
    assert not any(item.isVisible() for item in ws.canvas._measurement_overlay_items)
    second['nuclei'][0]['include_in_statistics'] = False
    c.sync();QTest.qWait(40)
    assert not ws.canvas._measurement_overlay_items


@pytest.mark.parametrize('module,task', [('fluoro','dapi'), ('brightfield','he')])
def test_session_restore_keeps_independent_task_and_original_preview(preview_window, tmp_path, module, task):
    from core.image_analysis.nuclei_counting import object_rows
    w = preview_window
    panel, image, _ = import_example(w, tmp_path, module)
    deck = w.measurement_tasks[module];deck.select(task);c = deck.controllers[task]
    c.controls.method.setCurrentIndex(c.controls.method.findData('watershed'))
    labels = np.zeros((100,160),np.int32);labels[20:30,20:30] = 1
    rows = object_rows(labels,labels)
    for row in rows:row.update(classification='included',include_in_statistics=True)
    c.accept_result(dict(task=task,nuclei=rows,image_uid=image['uid'],image_name='example',
        channel_index=c.controls.channel.currentData() if module=='fluoro' else None,parameters=c.controls.parameters()))
    QTest.qWait(40)
    workspace = getattr(w,module+'_workspace')
    original = workspace.ai_original_preview_button if module=='fluoro' else workspace.original_preview_button
    overlay = workspace.ai_overlay_preview_button if module=='fluoro' else workspace.overlay_preview_button
    original.click();w.analysis_state.capture_session()
    assert w.project.session_ui_state['preview_mode_by_module'][module]=='original'
    overlay.click();w.analysis_state.restore_session();QTest.qWait(60)
    assert deck.current_task()==task
    if module=='fluoro':assert not any(item.isVisible() for item in workspace.canvas._measurement_overlay_items)
    else:assert workspace.canvas.current_preview_mode()=='original'
