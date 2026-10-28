from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
import tifffile

from core.image_analysis import cellpose_backend
from core.models import FluorescencePanel

BACKEND = 'cellpose_cyto3_laminin_final_v1'
MODEL = 'cyto3_laminin_final_v1'


def test_membrane_models_coexist_and_roundtrip_without_replacing_results(monkeypatch, tmp_path):
    calls = []
    def infer(image, *, model, diameter, min_size):
        calls.append((model, diameter, min_size))
        labels = np.zeros(image.shape, np.int32)
        labels[4:14, 5:15] = 1
        if model == MODEL:
            labels[18:23, 20:25] = 2
        return labels, dict(model=model, cellpose_version='3.1.1.3')
    monkeypatch.setattr(cellpose_backend, 'run_cellpose_labels', infer)
    path = tmp_path/'membrane.tif'
    tifffile.imwrite(path, np.ones((1, 32, 40), np.uint16), metadata={'axes':'CYX'}, photometric='minisblack')
    panel = FluorescencePanel(panel_id='F')
    record = panel.import_image(path)
    old = panel.run_ai_assisted_analysis(record['uid'], [0], settings=dict(backend='cellpose_cyto3',cellpose_diameter_px=100,min_object_area_px=60))
    saved_old = deepcopy(old)
    new = panel.run_ai_assisted_analysis(record['uid'], [0], settings=dict(backend=BACKEND,cellpose_diameter_px=70,min_object_area_px=20))
    assert calls == [('cyto3',100,60),(MODEL,70,20)]
    assert old == saved_old
    assert old[0]['metadata']['model_id'] == 'cellpose_cyto3'
    assert new[0]['metadata']['model_id'] == BACKEND
    assert new[0]['metadata']['cellpose']['model'] == MODEL
    assert new[0]['ai_object_count'] == 2
    assert sorted(o['area_px'] for o in new[0]['object_table']) == [25,100]
    restored = FluorescencePanel.from_dict(deepcopy(panel.to_dict()))
    measurements = restored.images()[0]['fluorescence_ai_assisted_measurements']
    assert len(measurements) == 2
    assert measurements[0] == saved_old[0]
    assert measurements[1]['metadata']['model_id'] == BACKEND


def test_new_membrane_failure_does_not_fall_back_to_original(monkeypatch, tmp_path):
    def infer(*args, **kwargs):
        assert kwargs['model'] == MODEL
        raise FileNotFoundError('missing reviewed membrane weights')
    monkeypatch.setattr(cellpose_backend, 'run_cellpose_labels', infer)
    path=tmp_path/'f.tif'
    tifffile.imwrite(path,np.ones((1,32,40),np.uint16),metadata={'axes':'CYX'},photometric='minisblack')
    panel=FluorescencePanel(panel_id='F');record=panel.import_image(path)
    with pytest.raises(FileNotFoundError,match='membrane weights'):
        panel.run_ai_assisted_analysis(record['uid'],[0],settings={'backend':BACKEND})
    assert not record.get('fluorescence_ai_assisted_measurements')


def test_membrane_choice_and_reference_load_preserve_legacy_defaults(qt_application):
    from ui.main_window import MainWindow
    from core.utils.i18n import get_language,set_language,tr
    previous = get_language()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    try:
        workspace=window.fluoro_workspace
        assert workspace.ai_backend_combo.currentData()=='local_heuristic'
        workspace.set_ai_analysis_settings(dict(backend='cellpose_cyto3',cellpose_diameter_px=100,min_object_area_px=80))
        old=workspace.ai_analysis_settings()
        index=workspace.ai_backend_combo.findData(BACKEND)
        assert index>=0
        workspace.ai_backend_combo.setCurrentIndex(index)
        assert workspace.ai_cellpose_diameter_spin.value()==100
        workspace.ai_membrane_preset_combo.setCurrentIndex(workspace.ai_membrane_preset_combo.findData(70))
        workspace.ai_membrane_preset_combo.activated.emit(workspace.ai_membrane_preset_combo.currentIndex())
        assert workspace.ai_analysis_settings()['backend']==BACKEND
        assert workspace.ai_analysis_settings()['cellpose_diameter_px']==70
        assert workspace.ai_analysis_settings()['min_object_area_px']==60
        window.switch_language('zh')
        assert workspace.ai_status_label.text()==tr('membrane_laminin_hint')
        assert workspace.ai_analysis_settings()['backend']==BACKEND
        workspace.set_ai_analysis_settings(old)
        assert workspace.ai_analysis_settings()==old
    finally:
        window._capture_clean_project_baseline();window.close();window.deleteLater()
        set_language(previous);qt_application.processEvents()


@pytest.mark.parametrize('language,expected', [('zh','Laminin 膜轮廓适配'), ('en','Laminin membrane adaptation')])
def test_laminin_name_describes_use(language, expected):
    from core.utils.i18n import get_language, set_language, tr
    previous=get_language()
    try:
        set_language(language)
        assert tr('membrane_laminin_method') == expected
        assert 'cyto3' in tr('membrane_laminin_hint')
    finally:
        set_language(previous)


def test_shared_worker_accepts_laminin_and_preserves_other_models(monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(cellpose_backend,'worker_command',lambda:['worker'])
    def infer(command, **kwargs):
        model=command[command.index('--model')+1]
        target=Path(command[2])
        np.save(target,np.zeros((24,32),np.int32))
        device=command[command.index('--device')+1] if '--device' in command else 'cpu'
        target.with_suffix('.json').write_text(json.dumps({'model':model,'device':device}),encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cellpose_backend.subprocess,'run',infer)
    for model in (MODEL,'cyto3','nuclei','cyto3_dapi_reviewed_v1','cyto3_he_fiber_boundary_v1'):
        labels,meta=cellpose_backend.run_cellpose_labels(np.zeros((24,32),np.uint8),model=model)
        assert labels.shape==(24,32) and meta['model']==model

