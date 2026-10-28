import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow


def test_optional_backends_parameters_and_legacy_defaults():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    try:
        controls, workspace = window.data_panel, window.fluoro_workspace
        assert controls.brightfield_he_fiber_method_combo.currentData() == 'original'
        assert workspace.ai_backend_combo.currentData() == 'local_heuristic'
        controls.set_brightfield_he_fiber_analysis_settings({
            'he_fiber_segmentation_method': 'cellpose_cyto3', 'he_fiber_cellpose_diameter_px': 75})
        assert controls.brightfield_analysis_settings()['he_fiber_cellpose_diameter_px'] == 75
        assert controls.brightfield_he_fiber_cellpose_diameter_spin.isEnabled()
        assert not controls.brightfield_he_fiber_splitting_slider.isEnabled()
        workspace.set_ai_analysis_settings({'backend': 'cellpose_cyto3', 'cellpose_diameter_px': 55})
        assert workspace.ai_analysis_settings()['backend'] == 'cellpose_cyto3'
        assert workspace.ai_analysis_settings()['cellpose_diameter_px'] == 55
        assert not workspace.ai_sensitivity_spin.isEnabled()
        assert workspace.ai_cellpose_diameter_spin.isEnabled()
        controls.set_cell_analysis_settings(dict(backend='cellpose',model_diameter_px=45,model_min_size_px=25))
        assert controls.bright_round_cell_analysis_settings()['model_diameter_px']==45
        assert controls.bright_round_cell_analysis_settings()['model_min_size_px']==25
        assert controls.brightfield_cellpose_diameter_spin.isEnabled()
        controls.brightfield_cellpose_strategy_combo.setCurrentIndex(controls.brightfield_cellpose_strategy_combo.findData('traditional'))
        assert not controls.brightfield_cellpose_diameter_spin.isEnabled()
        workspace.measure_mode = 'ai_assisted'
        assert window.microscopy_workflow.batch_spec('fluorescence')[1]['settings']['backend'] == 'cellpose_cyto3'
    finally:
        window._capture_clean_project_baseline()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_he_cellpose_runs_in_snapshot_and_discards_changed_settings(monkeypatch, tmp_path):
    import numpy as np
    import tifffile
    from core.image_analysis import cellpose_backend
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    try:
        panel = window._active_brightfield_panel()
        rgb = np.full((40, 50, 3), [246, 241, 244], np.uint8)
        rgb[5:35, 5:45] = [215, 74, 132]
        path = tmp_path / 'he.tif'
        tifffile.imwrite(path, rgb, photometric='rgb')
        image = panel.import_image(path)
        panel.calibrate_he_fiber_area(image_uid=image['uid'])
        window.data_panel.set_brightfield_he_fiber_analysis_settings({'he_fiber_segmentation_method': 'cellpose_cyto3'})
        jobs = []
        controller = window.brightfield_ai_object_count_controller
        monkeypatch.setattr(controller, '_run_async', lambda **kwargs: jobs.append(kwargs) or True)
        monkeypatch.setattr(cellpose_backend, 'run_cellpose_labels', lambda image, **kwargs:
            (np.zeros(image.shape[:2], np.int32), dict(**kwargs)))
        controller.run_object_count()
        assert len(jobs) == 1
        assert not panel.analysis.get('brightfield_ai_object_count_measurements')
        jobs[0]['work']()
        assert not panel.analysis.get('brightfield_ai_object_count_measurements')
        assert jobs[0]['is_current']()
        window.data_panel.brightfield_he_fiber_cellpose_diameter_spin.setValue(120)
        assert not jobs[0]['is_current']()
        jobs[0]['on_stale']()
        assert window.data_panel.brightfield_ai_object_count_button.isEnabled()
    finally:
        window._capture_clean_project_baseline()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_fluoro_cellpose_fields_are_not_squeezed_in_scroll_panel(desktop_screen):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    try:
        window.resize(1280, 800)
        window.show()
        window.set_module('fluoro')
        workspace = window.fluoro_workspace
        workspace.set_step(2)
        workspace._set_measure_mode('ai_assisted')
        workspace.set_ai_analysis_settings({'backend': 'cellpose_cyto3'})
        for _ in range(8):
            app.processEvents()
        from ui.widgets.workflow_presentation import WorkflowDisclosure
        section = next(s for s in workspace.ai_assisted_group.findChildren(WorkflowDisclosure)
                       if s.title_key == 'task_parameters')
        assert not workspace.ai_min_area_spin.isVisible()
        section.toggle.click()
        for _ in range(8):
            app.processEvents()
        for field in (workspace.ai_backend_combo, workspace.ai_cellpose_diameter_spin, workspace.ai_min_area_spin):
            assert field.isVisible()
            assert field.height() >= field.minimumSizeHint().height()
    finally:
        window._capture_clean_project_baseline()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_membrane_reference_parameters_require_explicit_load_and_remain_editable():
    app=QApplication.instance() or QApplication([])
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    try:
        workspace=window.fluoro_workspace
        assert workspace.ai_membrane_preset_row.minimumSizeHint().width()<=220
        workspace.set_ai_analysis_settings(dict(backend='cellpose_cyto3',cellpose_diameter_px=55,min_object_area_px=7))
        before=workspace.ai_analysis_settings()
        workspace.ai_membrane_preset_combo.setCurrentIndex(1)
        assert workspace.ai_analysis_settings()==before
        workspace.ai_membrane_preset_combo.activated.emit(workspace.ai_membrane_preset_combo.currentIndex())
        assert workspace.ai_analysis_settings()['cellpose_diameter_px']==180
        assert workspace.ai_analysis_settings()['min_object_area_px']==80
        workspace.ai_cellpose_diameter_spin.setValue(165)
        assert workspace.ai_analysis_settings()['cellpose_diameter_px']==165
        workspace.set_ai_analysis_settings(before)
        assert workspace.ai_analysis_settings()==before
    finally:
        window._capture_clean_project_baseline();window.close();window.deleteLater();app.processEvents()


def test_dapi_input_option_and_explicit_recipe_restore_without_touching_he_defaults():
    app=QApplication.instance() or QApplication([])
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    try:
        controller=window.nuclei_controllers['fluoro'];controls=controller.controls
        controls.set_parameters(dict(method='cellpose_nuclei',diameter_px=29,cellpose_input='local_contrast'))
        assert controls.parameters()['cellpose_input']=='local_contrast'
        assert controls.diameter.value()==29
        controller.load_recipe()
        assert controls.diameter.value()==25
        assert controls.parameters()['cellpose_input']=='raw'
        controls.set_parameters(dict(method='cellpose_nuclei',diameter_px=29,cellpose_input='local_contrast'))
        assert controls.parameters()['cellpose_input']=='local_contrast'
        assert 'cellpose_input' not in window.nuclei_controllers['brightfield'].controls.parameters()
    finally:
        window._capture_clean_project_baseline();window.close();window.deleteLater();app.processEvents()
