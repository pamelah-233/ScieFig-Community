from copy import deepcopy
from types import SimpleNamespace
import json
import cv2
import numpy as np
import pytest
from test_myotube_workflow import myotube_window,import_example,wait_myotube_job


def test_methods_keep_default_and_parameters(qt_application):
    from ui.widgets.myotube_controls import MyotubeControls
    from core.utils.i18n import set_language,get_language
    language=get_language()
    try:
        set_language('zh');controls=MyotubeControls('brightfield')
        assert [controls.method.itemText(i) for i in range(2)]==['通用肌管轮廓','长肌管边界适配']
        assert controls.method.currentData()=='cyto3'
        controls.diameter.setValue(91);controls.minimum.setValue(83)
        controls.method.setCurrentIndex(1)
        assert controls.parameters()['method']=='truefad_rim_path'
        assert controls.diameter.value()==91 and controls.minimum.value()==83
        assert not controls.diameter.isEnabled()
        assert 'TRUEFAD' in controls.hint.text()
        controls.method.setCurrentIndex(0)
        assert controls.diameter.isEnabled() and controls.diameter.value()==91
        set_language('en');controls.retranslate()
        assert controls.method.itemText(1)=='Long-myotube boundary adaptation'
        controls.close()
    finally:set_language(language)


def test_old_result_identity_and_method_selection(qt_application):
    from ui.controllers.myotube_controller import MyotubeController
    from ui.widgets.myotube_controls import MyotubeControls
    old=dict(image_uid='a',channel_index=None,params={},measurement_uuid='old')
    new=dict(image_uid='a',channel_index=None,params={'method':'truefad_rim_path'},measurement_uuid='new')
    panel=SimpleNamespace(active_image=lambda:{'uid':'a'},analysis={'myotube_measurements':[old,new]})
    controls=MyotubeControls('brightfield')
    controller=SimpleNamespace(panel=lambda:panel,controls=controls,module='brightfield')
    assert MyotubeController.current_result(controller) is old
    controls.method.setCurrentIndex(1)
    assert MyotubeController.current_result(controller) is new
    restored=json.loads(json.dumps(panel.analysis))
    assert restored['myotube_measurements'][0]==old
    assert restored['myotube_measurements'][1]['params']['method']=='truefad_rim_path'
    controls.close()


def test_overlapping_candidates_keep_independent_geometry(monkeypatch):
    from core.image_analysis import myotube_cells,myotube_boundary,cellpose_backend
    shape=(120,300);source=np.full((*shape,3),100,np.uint8);source[0,0]=120
    first=np.zeros(shape,np.uint8);second=first.copy()
    cv2.rectangle(first,(30,30),(230,50),1,-1)
    cv2.rectangle(second,(80,40),(260,60),1,-1)
    before=source.copy();params=dict(method='truefad_rim_path',exclude_scale_bar=False)
    monkeypatch.setattr(myotube_boundary,'run_centers',lambda gray:(np.zeros(shape),{'model':'truefad_myotube'}))
    monkeypatch.setattr(myotube_boundary,'boundary_masks',lambda *args:([first,second],[
        dict(overlap_fraction=.3,endpoint_status='model_center_ends_unverified'),
        dict(overlap_fraction=.3,endpoint_status='model_center_ends_unverified')]))
    result=myotube_cells.analyze_myotubes(source,modality='brightfield',settings=params,
        calibration=dict(um_per_px=.5,calibrated=True))
    assert result['provenance']['model']=='truefad_myotube'
    assert len(result['results'])==2 and len(result['instance_masks'])==2
    for expected,row,saved in zip([first,second],result['results'],result['instance_masks']):
        assert row['area_px']==int(expected.sum())
        assert row['area_um2']==pytest.approx(expected.sum()*.25)
        assert row['max_width_px']==pytest.approx(20,abs=.3)
        assert row['geometry_review_required'] and not row['include_in_statistics']
        x,y,w,h=saved['bbox_xywh']
        mask=cellpose_backend.unpack_instance_labels(saved['mask'],(h,w))
        np.testing.assert_array_equal(mask,expected[y:y+h,x:x+w])
    assert result['summary']['complete_count']==0
    assert 'instance_labels' not in result  # A flat label map would overwrite overlap.
    np.testing.assert_array_equal(source,before)


def test_general_path_still_uses_cyto3_and_fluoro_rejects_brightfield_method(monkeypatch):
    from core.image_analysis import myotube_cells,cellpose_backend
    source=np.full((90,250,3),100,np.uint8);source[0,0]=150
    labels=np.zeros(source.shape[:2],np.int32);labels[30:50,30:220]=1
    calls=[]
    def run(image,**kwargs):calls.append(kwargs);return labels,{'model':'cyto3'}
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',run)
    result=myotube_cells.analyze_myotubes(source,modality='brightfield',settings={'exclude_scale_bar':False})
    assert calls[0]['model']=='cyto3' and calls[0]['diameter']==60
    assert result['provenance']['model']=='cyto3'
    with pytest.raises(ValueError,match='method'):
        myotube_cells.analyze_myotubes(source[:,:,0],modality='fluoro',settings={'method':'truefad_rim_path'})


def test_boundary_paths_follow_a_long_two_rim_structure():
    from core.image_analysis.myotube_boundary import boundary_masks
    gray=np.full((120,300),100,np.uint8)
    gray[43:46,20:280]=240;gray[65:68,20:280]=240
    probability=np.ones(gray.shape,np.float32);probability[53:58,30:270]=0
    masks,rows=boundary_masks(gray,probability)
    assert len(masks)==1 and len(rows)==1
    ys,xs=np.nonzero(masks[0])
    assert xs.max()-xs.min()>210
    assert 18<=ys.max()-ys.min()<=28
    assert rows[0]['endpoint_status']=='model_center_ends_unverified'


def test_missing_runtime_does_not_fall_back(monkeypatch):
    from core.image_analysis import myotube_boundary,cellpose_backend
    def missing():raise FileNotFoundError('runtime missing')
    monkeypatch.setattr(cellpose_backend,'worker_command',missing)
    with pytest.raises(FileNotFoundError,match='runtime missing'):
        myotube_boundary.run_centers(np.zeros((40,80),np.uint8))


def test_method_switch_and_project_roundtrip(myotube_window,tmp_path,monkeypatch):
    from core.image_analysis import cellpose_backend,myotube_boundary
    from core.models import Project
    window=myotube_window
    controller,panel,record,labels,_=import_example(window,'brightfield',tmp_path)
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',lambda *a,**k:(labels,{'model':'cyto3'}))
    controller.controls.diameter.setValue(88);controller.run();wait_myotube_job(window,controller)
    old=deepcopy(controller.current_result())
    controller.controls.method.setCurrentIndex(1)
    assert controller.current_result() is None and controller.controls.table.rowCount()==0
    assert controller.controls.diameter.value()==88
    assert len(panel.analysis['myotube_measurements'])==1  # Selecting never runs inference.
    monkeypatch.setattr(myotube_boundary,'run_centers',lambda gray:(np.zeros(gray.shape),{'model':'truefad_myotube'}))
    monkeypatch.setattr(myotube_boundary,'boundary_masks',lambda *args:([np.uint8(labels==1)],
        [dict(overlap_fraction=0.,endpoint_status='model_center_ends_unverified')]))
    controller.run();wait_myotube_job(window,controller)
    assert controller.current_result()['params']['method']=='truefad_rim_path'
    assert panel.analysis['myotube_measurements'][0]==old
    path=tmp_path/'both-methods.sfig';window.project.save_sfig(path)
    restored=Project.load_sfig(path)
    other=next(p for p in restored.panels if p.panel_type==panel.panel_type)
    assert other.analysis['myotube_measurements']==panel.analysis['myotube_measurements']
    assert other.active_image()['myotube_method']=='truefad_rim_path'
    restored.cleanup_archive_extract_root()
    pending=[];monkeypatch.setattr(window._analysis_job_service,'start',lambda **kw:pending.append(kw))
    controller.run();assert pending[0]['is_current']()
    controller.controls.method.setCurrentIndex(0)
    assert not pending[0]['is_current']() and controller.current_result()==old

