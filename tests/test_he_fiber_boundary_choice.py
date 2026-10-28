import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile
from core.image_analysis import cellpose_backend
from core.image_analysis.he_fiber_area import analyze_he_fiber_area,normalize_he_fiber_segmentation_method
from core.models import BrightfieldPanel,Project
from core.utils.i18n import set_language,tr

METHOD='cellpose_cyto3_he_fiber_boundary_v1'
MODEL='cyto3_he_fiber_boundary_v1'


def test_boundary_model_keeps_native_area_and_saved_identity(monkeypatch,tmp_path):
    assert normalize_he_fiber_segmentation_method(METHOD)==METHOD
    labels=np.zeros((40,50),np.int32);labels[5:15,5:15]=1;labels[20:30,25:35]=2
    calls=[]
    def run(image,**kwargs):
        calls.append(kwargs)
        return labels.copy(),dict(model=kwargs['model'],diameter_px=kwargs['diameter'],min_size_px=kwargs['min_size'])
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',run)
    rgb=np.full((40,50,3),[235,170,200],np.uint8);rgb[5:35,5:45]=[215,74,132]
    path=tmp_path/'he.tif';tifffile.imwrite(path,rgb,photometric='rgb')
    panel=BrightfieldPanel(panel_id='B');record=panel.import_image(path)
    panel.calibrate_he_fiber_area(image_uid=record['uid'])
    params=dict(he_fiber_segmentation_method=METHOD,he_fiber_cellpose_diameter_px=50,min_object_area_px=60,max_object_area_px=0)
    row=panel.create_ai_object_count_measurement(record['uid'],settings=params)
    assert calls[0]['model']==MODEL
    assert row['model_name']=='cellpose:'+MODEL
    assert row['results']['ai_object_count']==2 and row['results']['ai_object_total_area_px']==200
    assert row['analysis_metadata']['he_fiber_segmentation_method']==METHOD
    assert row['analysis_metadata']['cellpose']['model']==MODEL
    project=Project(title='HE coexistence');project.add_panel(panel)
    archive=tmp_path/'he.sfig';project.save_sfig(archive)
    restored_project=Project.load_sfig(archive)
    restored=restored_project.panels[0]
    assert restored.data['analysis_settings']['he_fiber_segmentation_method']==METHOD
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',lambda *a,**k:pytest.fail('Reopening must not infer'))
    np.testing.assert_array_equal(restored._he_fiber_area_labels_for_measurement(row),labels)


def test_worker_accepts_exact_model_without_silent_fallback(monkeypatch):
    monkeypatch.setattr(cellpose_backend,'worker_command',lambda:['worker'])
    def run(command,**kwargs):
        assert command[command.index('--model')+1]==MODEL
        target=Path(command[2]);np.save(target,np.zeros((20,30),np.int32))
        device=command[command.index('--device')+1] if '--device' in command else 'cpu'
        target.with_suffix('.json').write_text(json.dumps(dict(model=MODEL,device=device)),encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cellpose_backend.subprocess,'run',run)
    labels,meta=cellpose_backend.run_cellpose_labels(np.zeros((20,30),np.uint8),model=MODEL)
    assert labels.shape==(20,30) and meta['model']==MODEL


def test_he_ui_coexistence_and_explicit_reference_load(qt_application,desktop_screen):
    from ui.main_window import MainWindow
    window=MainWindow(Path(__file__).resolve().parents[1]/'resources');window.project_recovery.timer.stop()
    try:
        data=window.data_panel;combo=data.brightfield_he_fiber_method_combo
        assert combo.currentData()=='original'
        assert all(combo.findData(k)>=0 for k in ['original','anti_oversegmentation','cellpose_cyto3',METHOD])
        data.brightfield_he_fiber_cellpose_diameter_spin.setValue(77)
        combo.setCurrentIndex(combo.findData(METHOD))
        assert data.brightfield_he_fiber_cellpose_diameter_spin.value()==77
        assert data.brightfield_he_fiber_cellpose_diameter_spin.isEnabled()
        assert not data.brightfield_he_fiber_splitting_slider.isEnabled()
        assert not hasattr(data,"brightfield_he_fiber_reference_button")
        data._load_he_fiber_reference()
        settings=data.brightfield_analysis_settings()
        assert settings['he_fiber_cellpose_diameter_px']==50
        assert settings['min_object_area_px']==60 and settings['max_object_area_px']==0
        for language in ('zh','en'):
            window.switch_language(language)
            assert combo.currentData()==METHOD and combo.currentText()==tr('he_fiber_method_'+METHOD)
        assert not window._analysis_job_service.is_busy('brightfield_ai_object_count')
    finally:
        window._capture_clean_project_baseline();window.close();window.deleteLater();qt_application.processEvents()


@pytest.mark.parametrize('language',['zh','en'])
def test_method_names_describe_purpose_without_version_ranking(language,qt_application):
    from ui.widgets.nuclei_count_controls import NucleiCountControls
    set_language(language)
    expected={'nuclei_cyto3':('通用核检测','General nuclear detection'),
        'nuclei_cyto3_reviewed':('DAPI 轮廓适配','DAPI contour adaptation'),
        'nuclei_he_pale':('浅小核补查','Pale-small nucleus recovery'),
        'nuclei_he_round':('浅染圆核识别','Pale round-nucleus detection'),
        'nuclei_he_refined':('大小核联合识别','Mixed-size nuclear detection'),
        'nuclei_cellpose':('核专用检测','Nucleus-specific detection'),
        'nuclei_watershed':('局部对比分水岭','Local-contrast watershed'),
        'he_fiber_method_original':('染色特征分割','Stain-feature segmentation'),
        'he_fiber_method_anti_oversegmentation':('区域连通分割','Region-connectivity segmentation'),
        'he_fiber_method_cellpose_cyto3':('通用纤维分割','General fiber segmentation'),
        'local_fluoro_segmentation':('信号阈值分割','Signal-threshold segmentation'),
        'cellpose_area_method':('通用细胞分割','General cell segmentation'),
        'he_fiber_method_'+METHOD:('组织边界适配','Tissue-boundary adaptation')}
    for key,names in expected.items():
        assert tr(key)==names[0 if language=='zh' else 1]
        assert not re.search(r'原版|微调|优化|第二轮|旧版|original|tuned|refined|experimental',tr(key),re.I)
    controls=NucleiCountControls('fluoro')
    controls.method.setCurrentIndex(controls.method.findData('cellpose_cyto3_dapi_reviewed_v1'))
    before=controls.parameters();controls.retranslate()
    assert controls.parameters()==before
    assert 'cyto3' in controls.method.toolTip().lower()
    controls.close()

