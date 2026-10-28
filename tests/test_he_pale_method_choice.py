import importlib
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from core.image_analysis import cellpose_backend
from core.image_analysis.nuclei_counting import count_nuclei, nuclear_signal
from core.models import Project
from core.utils.i18n import set_language, tr
from ui.widgets.nuclei_count_controls import NucleiCountControls


METHOD = 'cellpose_cyto3_he_pale_v1'


def implementation():
    name = 'core.image_analysis.he_pale_nuclei'
    assert importlib.util.find_spec(name), 'Accepted HE supplement is not connected'
    return importlib.import_module(name)


def test_addition_filter_preserves_pale_core_and_base_masks():
    module = implementation()
    base = np.zeros((48,48),np.int32); base[1:3,1:12] = 1
    merged = base.copy(); merged[8:15,5:12] = 2; merged[22:29,5:12] = 3
    merged[33,5:15] = 4; merged[35,20] = 4
    h = np.zeros(merged.shape,np.float32)
    h[merged == 2] = .0001; h[9:14,6:11] = .0003
    h[merged == 3] = .04; h[23:28,6:11] = .001
    before = [a.copy() for a in (base,merged,h)]
    filtered, decisions = module.filter_added_nuclei(base,merged,h)
    np.testing.assert_array_equal(filtered[base > 0],base[base > 0])
    np.testing.assert_array_equal(filtered == 2,merged == 2)
    assert not np.any(np.isin(filtered,[3,4]))
    assert {d['label'] for d in decisions} == {2,3,4}
    for original, copy in zip((base,merged,h),before):
        np.testing.assert_array_equal(original,copy)


def test_two_pass_he_route_uses_existing_worker_and_measures_original_signal(monkeypatch):
    source = np.full((70,80,3),[240,180,200],np.uint8)
    source[29:34,41:46] = [90,60,130]
    base = np.zeros(source.shape[:2],np.int32); base[5:15,5:15] = 1
    proposal = np.zeros_like(base); proposal[28:35,40:47] = 1
    calls = []

    def inference(image, **kwargs):
        np.testing.assert_array_equal(image,nuclear_signal(source,'he'))
        calls.append(kwargs)
        return (base if len(calls) == 1 else proposal).copy(), dict(model='cyto3',model_weights='cyto3',inference_options=kwargs)

    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',inference)
    result = count_nuclei(source,stain='he',method=METHOD,diameter_px=12,min_area_px=8)
    assert len(calls) == 2 and all(c['model'] == 'cyto3' for c in calls)
    assert (calls[0]['diameter'],calls[0]['min_size'],calls[0]['normalization_tile_px']) == (12,8,128)
    assert (calls[1]['diameter'],calls[1]['min_size'],calls[1]['normalization_tile_px'],calls[1]['cellprob_threshold']) == (8,5,32,-.5)
    np.testing.assert_array_equal(result['labels'][base > 0],base[base > 0])
    assert len(result['nuclei']) == 2
    for row in result['nuclei']:
        assert row['mean_raw_signal'] == float(nuclear_signal(source,'he')[result['labels'] == row['object_id']].mean())
    assert result['parameters']['he_pale_settings']['min_solidity'] == .6
    assert result['provenance']['version'] == 'he_cyto3_pale_v1'
    assert result['provenance']['baseline']['model'] == result['provenance']['supplement']['model'] == 'cyto3'


def test_he_method_is_not_applicable_to_dapi():
    with pytest.raises(ValueError,match='H&E'):
        count_nuclei(np.zeros((20,20)),stain='dapi',method=METHOD)


def test_he_choices_keep_defaults_and_only_explicit_recipe_changes_parameters(qt_application):
    controls = NucleiCountControls('brightfield')
    assert controls.parameters()['method'] == 'cellpose_nuclei'
    for method in ['cellpose_nuclei','watershed','he_round','he_refined','cellpose_cyto3',METHOD]:
        assert controls.method.findData(method) >= 0
    controls.diameter.setValue(23)
    controls.method.setCurrentIndex(controls.method.findData(METHOD))
    assert controls.diameter.value() == 23
    assert not hasattr(controls,"preset_button")
    params = dict(method=METHOD,diameter_px=12,min_area_px=8,max_area_px=2000,he_pale_settings=dict(min_solidity=.7))
    controls.set_parameters(params)
    assert controls.parameters()['he_pale_settings'] == dict(min_solidity=.7)
    controls.method.setCurrentIndex(controls.method.findData('cellpose_cyto3'))
    assert controls.parameters()['cellpose_input'] == 'tile_normalization'
    assert 'he_pale_settings' not in controls.parameters()
    controls.method.setCurrentIndex(controls.method.findData(METHOD))
    assert controls.parameters()['he_pale_settings'] == dict(min_solidity=.7)
    controls.close()


def test_he_selection_and_results_follow_images_and_project_save(tmp_path,desktop_screen,qt_application):
    from ui.main_window import MainWindow
    window = MainWindow(Path(__file__).resolve().parents[1]/'resources')
    window.project_recovery.timer.stop()
    try:
        c = window.nuclei_controllers['brightfield']; panel = c.panel()
        path = tmp_path/'he.png'; Image.fromarray(np.full((40,50,3),[230,160,185],np.uint8)).save(path)
        first = panel.import_image(path); window.set_module('brightfield'); c.sync()
        assert c.controls.method.findData(METHOD) >= 0
        c.controls.method.setCurrentIndex(c.controls.method.findData(METHOD))
        assert not window._analysis_job_service.is_busy(c.job_key)
        c.load_recipe()
        assert c.controls.parameters()['method'] == METHOD
        assert c.controls.diameter.value() == 12
        assert c.controls.parameters()['he_pale_settings']['min_core_to_rim'] == 1.25
        second = panel.import_image(path); c.sync()
        assert c.controls.method.currentData() == 'cellpose_nuclei'
        panel.set_active_image(first['uid']); c.sync()
        assert c.controls.method.currentData() == METHOD
        results = panel.analysis.setdefault('nuclei_measurements',[])
        original = dict(image_uid=first['uid'],task='he',nuclei=[],parameters=dict(method='cellpose_cyto3'),provenance=dict(model='cyto3'))
        results.append(original)
        assert c.current_result() is None
        c.controls.method.setCurrentIndex(c.controls.method.findData('cellpose_cyto3'))
        assert c.current_result() is original
        c.controls.method.setCurrentIndex(c.controls.method.findData(METHOD))
        new = dict(image_uid=first['uid'],task='he',nuclei=[],parameters=dict(method=METHOD),provenance=dict(model='cyto3'))
        results.append(new)
        assert c.current_result() is new
        archive = tmp_path/'he-methods.sfig'; window.project.save_sfig(archive)
        window.project = Project.load_sfig(archive); c.sync()
        assert c.controls.method.currentData() == METHOD
        assert c.current_result()['parameters']['method'] == METHOD
        assert len(c.panel().analysis['nuclei_measurements']) == 2
        c.controls.method.setCurrentIndex(c.controls.method.findData('cellpose_cyto3'))
        assert c.current_result()['parameters']['method'] == 'cellpose_cyto3'
        c.panel().set_active_image(second['uid']); c.sync()
        assert c.controls.method.currentData() == 'cellpose_nuclei'
    finally:
        window._capture_clean_project_baseline(); window.close(); window.deleteLater(); qt_application.processEvents()


@pytest.mark.parametrize('language',['zh','en'])
def test_he_method_text_fits_existing_narrow_selector(language,qt_application):
    from PyQt6.QtWidgets import QWidget,QFormLayout
    from ui.controllers.measurement_workbench_controller import readable_forms
    set_language(language); controls = NucleiCountControls('brightfield')
    assert controls.method.findData(METHOD) >= 0
    controls.method.setCurrentIndex(controls.method.findData(METHOD)); controls.retranslate()
    assert controls.method.currentText() == tr('nuclei_he_pale')
    assert controls.method.toolTip() == tr('nuclei_he_pale_hint')
    source = controls.method.parentWidget().layout(); row,_ = source.getWidgetPosition(controls.method)
    taken = source.takeRow(row); container = QWidget(); form = QFormLayout(container)
    form.addRow(taken.labelItem.widget(),controls.method); readable_forms(container)
    container.resize(320,150); container.show(); qt_application.processEvents()
    assert container.width() == 320
    assert controls.method.geometry().right() <= controls.method.parentWidget().width()
    assert controls.method.height() >= controls.method.minimumSizeHint().height()
    assert controls.method.focusPolicy().value != 0
    container.close(); controls.close()

