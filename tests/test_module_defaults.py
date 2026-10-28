from copy import deepcopy

import pytest

from tests.test_canvas_empty_copy import empty_window


@pytest.mark.parametrize('module,preset_module', [('fluoro','fluorescence'),('ihc','ihc'),('brightfield','brightfield')])
def test_reset_restores_built_in_advanced_parameters_without_running_analysis(empty_window, module, preset_module):
    w=empty_window
    baseline=deepcopy(w.analysis_state.capture_widget_state(preset_module))
    w.set_module(module)
    if module=='fluoro':
        w.fluoro_workspace.count_diameter_spin.setValue(170)
        w.fluoro_workspace.ai_sensitivity_spin.setValue(1.8)
        deck=w.measurement_tasks[module]
        deck.select('dapi')
        deck.controllers['dapi'].controls.diameter.setValue(99)
        deck.controllers['dapi'].controls.minimum.setValue(153)
        deck.controllers['fusion'].controls.min_nuclei.setValue(7)
    elif module=='brightfield':
        w.data_panel.brightfield_cellpose_reference_combo.setCurrentIndex(1)
        deck=w.measurement_tasks[module]
        deck.select('myotube')
        deck.controllers['myotube'].controls.minimum.setValue(1234)
        deck.controllers['he'].controls.diameter.setValue(99)
    else:
        settings=w.data_panel.ihc_analysis_settings()
        settings['positive_threshold']=.77
        w.data_panel.set_ihc_analysis_settings(settings)
    selected=w.measurement_tasks[module].current_task() if module in w.measurement_tasks else None
    w.module_defaults.button.click()
    restored=w.analysis_state.capture_widget_state(preset_module)
    for key in ('count','ai','roi','expert','assisted','foolproof','cells','fiber','independent'):
        if key in baseline:assert restored[key]==baseline[key],key
    if selected is not None:assert w.measurement_tasks[module].current_task()==selected
    assert not w._analysis_job_service.has_busy_jobs()


def test_reset_is_available_in_every_module_and_keeps_data_and_layout(empty_window, qt_application):
    from PyQt6.QtWidgets import QFrame
    w=empty_window
    for module in ('qpcr','wb','fluoro','ihc','brightfield','viewer','composer','stylelab'):
        w.set_module(module)
        qt_application.processEvents()
        button=w.stylelab_workspace.reset_defaults_button if module=='stylelab' else w.module_defaults.button
        assert button.isVisible(),module
        project_before=deepcopy(w.project.to_dict())
        button.click()
        qt_application.processEvents()
        assert len(w.project.panels)==len(project_before['panels'])
        assert [panel.panel_uuid for panel in w.project.panels]==[panel['panel_uuid'] for panel in project_before['panels']]
        assert w.project.to_dict()['composer_figures']==project_before['composer_figures']


def test_reset_parameters_survive_session_restore_without_redefining_defaults(empty_window, tmp_path):
    import numpy as np
    import tifffile
    w=empty_window
    path=tmp_path/'channel.tif'
    tifffile.imwrite(path,np.ones((48,64),np.uint8))
    panel=w._active_fluoro_panel()
    image=panel.import_image(path)
    w.set_module('fluoro');w.fluoro_workspace.set_step(2)
    deck=w.measurement_tasks['fluoro'];deck.select('dapi')
    expected=deepcopy(deck.controllers['dapi'].controls.parameters())
    controls=deck.controllers['dapi'].controls
    controls.method.setCurrentIndex(controls.method.findData('cellpose_nuclei'))
    controls.diameter.setValue(99)
    panel.analysis['nuclei_measurements']=[dict(measurement_uuid='saved',image_uid=image['uid'],task='dapi',nuclei=[],parameters=controls.parameters())]
    saved_results=deepcopy(panel.analysis)
    image['channels'][0]['display_processing']={'strength':60,'denoise':90,'background_radius':57}
    w.module_defaults.button.click()
    assert controls.parameters()==expected
    assert image['channels'][0].get('display_processing',{})=={}
    assert panel.analysis==saved_results
    state=deepcopy(w.analysis_state.capture_widget_state('fluorescence'))
    controls.diameter.setValue(80)
    w.analysis_state.apply_widget_state('fluorescence',state)
    assert controls.parameters()==expected
    assert panel.analysis==saved_results
    w.set_module('wb');w.set_module('fluoro')
    assert controls.parameters()==expected
    # A fresh window opening the saved project must keep the reset state;
    # saved measurements may still contain their original analysis parameters.
    from ui.main_window import MainWindow
    from ui.controllers.project_open_controller import open_project_path
    from pathlib import Path
    w.analysis_state.capture_session()
    destination=tmp_path/'defaults.sfig'
    w.project.save_sfig(destination)
    fresh=MainWindow(Path(__file__).resolve().parents[1]/'resources')
    fresh.project_recovery.timer.stop();fresh.project_recovery._alive=False
    fresh._confirm_destructive_action=lambda *args:True
    fresh.v9_shell.model_device_controller.shutdown()
    try:
        assert open_project_path(fresh,destination,confirm_unsaved=False)
        assert fresh.nuclei_controllers['fluoro'].controls.parameters()==expected
        fresh.nuclei_controllers['fluoro'].controls.diameter.setValue(90)
        fresh.module_defaults.button.click()
        assert fresh.nuclei_controllers['fluoro'].controls.parameters()==expected
    finally:fresh.close()
