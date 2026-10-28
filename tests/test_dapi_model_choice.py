import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from core.image_analysis import cellpose_backend
from core.image_analysis.nuclei_counting import count_nuclei
from core.models import Project
from core.utils.i18n import set_language, tr
from ui.widgets.nuclei_count_controls import NucleiCountControls


METHOD = 'cellpose_cyto3_dapi_reviewed_v1'
MODEL = 'cyto3_dapi_reviewed_v1'


def test_fluoro_offers_both_models_with_original_reference_defaults(qt_application):
    controls = NucleiCountControls('fluoro')
    assert controls.method.findData(METHOD) >= 0
    params = controls.parameters()
    assert params['method'] == 'cellpose_cyto3'
    assert (params['diameter_px'], params['min_area_px'], params['cellpose_input']) == (25, 50, 'tile_normalization')
    controls.diameter.setValue(31)
    controls.method.setCurrentIndex(controls.method.findData(METHOD))
    assert controls.diameter.value() == 31
    assert controls.parameters()['cellpose_input'] == 'tile_normalization'
    for language in ('zh', 'en'):
        set_language(language); controls.retranslate()
        assert controls.method.currentData() == METHOD
        assert controls.method.currentText() == tr('nuclei_cyto3_reviewed')
        assert controls.method.currentText() != controls.method.itemText(controls.method.findData('cellpose_cyto3'))
    he = NucleiCountControls('brightfield')
    assert he.method.findData(METHOD) == -1
    assert he.parameters()['method'] == 'cellpose_nuclei'
    controls.close(); he.close()


def test_reviewed_model_routes_through_shared_worker_and_records_actual_choice(monkeypatch):
    raw = np.arange(80 * 100, dtype=np.uint16).reshape(80, 100)
    before = raw.copy()
    labels = np.zeros(raw.shape, np.int32); labels[25:35, 40:50] = 1
    monkeypatch.setattr(cellpose_backend, 'worker_command', lambda: ['worker'])

    def run(command, **kwargs):
        assert command[command.index('--model') + 1] == MODEL
        np.testing.assert_array_equal(np.load(command[1]), raw)
        options = json.loads(command[command.index('--inference-options') + 1])
        assert options['normalization_tile_px'] == 128
        target = Path(command[2]); np.save(target, labels)
        device=command[command.index('--device')+1] if '--device' in command else 'cpu'
        target.with_suffix('.json').write_text(json.dumps(dict(model=MODEL, device=device, inference_options=options)), encoding='utf-8')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cellpose_backend.subprocess, 'run', run)
    result = count_nuclei(raw, stain='dapi', method=METHOD, diameter_px=25, min_area_px=50, cellpose_input='tile_normalization')
    assert result['parameters']['method'] == METHOD
    assert result['parameters']['cellpose_input'] == 'tile_normalization'
    assert result['provenance']['model'] == MODEL
    assert len(result['nuclei']) == 1
    assert result['nuclei'][0]['mean_raw_signal'] == float(raw[labels == 1].mean())
    np.testing.assert_array_equal(raw, before)


def test_unrun_method_choices_follow_images_and_project_save(tmp_path, desktop_screen, qt_application):
    from ui.main_window import MainWindow
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    window.project_recovery.timer.stop()
    try:
        c = window.nuclei_controllers['fluoro']; panel = c.panel()
        path = tmp_path / 'dapi.tif'
        Image.fromarray(np.arange(80 * 100, dtype=np.uint16).reshape(80, 100)).save(path)
        first = panel.import_image(path); first['channels'][0]['name'] = 'DAPI'
        window.set_module('fluoro'); c.sync()
        assert c.controls.method.findData(METHOD) >= 0
        c.controls.method.setCurrentIndex(c.controls.method.findData(METHOD))
        assert not window._analysis_job_service.is_busy(c.job_key)
        second = panel.import_image(path); second['channels'][0]['name'] = 'DAPI'; c.sync()
        assert c.controls.method.currentData() == 'cellpose_cyto3'
        panel.set_active_image(first['uid']); c.sync()
        assert c.controls.method.currentData() == METHOD
        panel.analysis.setdefault('nuclei_measurements', []).append(dict(
            image_uid=first['uid'], task='dapi', channel_index=0, nuclei=[],
            parameters=dict(method='cellpose_cyto3'), provenance=dict(model='cyto3')))
        assert c.current_result() is None
        c.controls.method.setCurrentIndex(c.controls.method.findData('cellpose_cyto3'))
        assert c.current_result() is not None
        c.controls.method.setCurrentIndex(c.controls.method.findData(METHOD))
        archive = tmp_path / 'choices.sfig'; window.project.save_sfig(archive)
        window.project = Project.load_sfig(archive); c.sync()
        assert c.controls.method.currentData() == METHOD
        c.panel().set_active_image(second['uid']); c.sync()
        assert c.controls.method.currentData() == 'cellpose_cyto3'
    finally:
        window._capture_clean_project_baseline(); window.close(); window.deleteLater(); qt_application.processEvents()


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_model_selector_fits_narrow_controls(language, qt_application):
    from PyQt6.QtWidgets import QWidget, QFormLayout
    from ui.controllers.measurement_workbench_controller import readable_forms
    set_language(language)
    controls = NucleiCountControls('fluoro')
    source_form = controls.method.parentWidget().layout()
    row, _ = source_form.getWidgetPosition(controls.method)
    taken = source_form.takeRow(row)
    container = QWidget(); form = QFormLayout(container)
    form.addRow(taken.labelItem.widget(), controls.method)
    readable_forms(container)  # Scope to the changed row, with the workbench's real sizing policy.
    container.resize(320, 150); container.show()
    controls.method.setCurrentIndex(controls.method.findData(METHOD))
    qt_application.processEvents()
    assert container.width() == 320
    assert controls.method.isVisible()
    assert controls.method.height() >= controls.method.minimumSizeHint().height()
    assert controls.method.geometry().right() <= controls.method.parentWidget().width()
    assert controls.method.focusPolicy().value != 0
    assert controls.method.toolTip() == tr('nuclei_cyto3_reviewed_hint')
    container.close(); controls.close()


def test_reviewed_recipe_load_keeps_model_and_snapshot_changes_on_selection(tmp_path, desktop_screen, qt_application, monkeypatch):
    from ui.main_window import MainWindow
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    window.project_recovery.timer.stop()
    try:
        c = window.nuclei_controllers['fluoro']; panel = c.panel()
        path = tmp_path / 'dapi.tif'; Image.fromarray(np.arange(1600, dtype=np.uint16).reshape(40, 40)).save(path)
        image = panel.import_image(path); image['channels'][0]['name'] = 'DAPI'
        window.set_module('fluoro'); c.sync()
        c.controls.method.setCurrentIndex(c.controls.method.findData(METHOD))
        c.load_recipe()
        assert c.controls.parameters()['method'] == METHOD
        pending = []
        monkeypatch.setattr(window._analysis_job_service, 'start', lambda **kwargs: pending.append(kwargs))
        c.run(); assert pending[0]['is_current']()
        c.controls.method.setCurrentIndex(c.controls.method.findData('cellpose_cyto3'))
        assert not pending[0]['is_current']()
        assert not panel.analysis.get('nuclei_measurements')
    finally:
        window._capture_clean_project_baseline(); window.close(); window.deleteLater(); qt_application.processEvents()

