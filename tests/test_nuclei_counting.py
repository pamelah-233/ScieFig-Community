import importlib.util
import numpy as np
import pytest
from scipy import ndimage as ndi


def nuclear_api():
    assert importlib.util.find_spec('core.image_analysis.nuclei_counting'), 'Nuclear counting is not implemented'
    from core.image_analysis.nuclei_counting import count_nuclei
    return count_nuclei


def test_dapi_count_uses_native_channel_and_separates_touching_nuclei():
    count = nuclear_api()
    y,x=np.mgrid[:80,:100]
    image=(3000*np.exp(-((x-34)**2+(y-40)**2)/45)+3000*np.exp(-((x-47)**2+(y-40)**2)/45)).astype(np.uint16)
    before=image.copy()
    result=count(image,stain='dapi',diameter_px=12,min_area_px=15)
    assert len(result['nuclei'])==2
    assert [r['center_x_px'] for r in result['nuclei']]==pytest.approx([34,47],abs=2)
    assert all(r['mean_raw_signal']>255 for r in result['nuclei'])
    np.testing.assert_array_equal(image,before)
    assert not count(np.zeros_like(image),stain='dapi')['nuclei']


def test_he_separation_roi_and_cellpose_parameters(monkeypatch):
    count=nuclear_api()
    image=np.full((60,100,3),[240,150,185],np.uint8)
    image[20:30,20:30]=[65,40,100]
    image[20:30,70:80]=[65,40,100]
    mask=np.zeros((60,100),bool);mask[:,:50]=True
    result=count(image,stain='he',diameter_px=12,min_area_px=10,roi_mask=mask)
    assert len(result['nuclei'])==1 and result['nuclei'][0]['center_x_px']<50
    from core.image_analysis import cellpose_backend
    seen={}
    def worker(signal,**params):
        seen.update(params);seen['signal']=signal.copy()
        return ndi.label(signal>signal.mean())[0],dict(model='nuclei',version='test')
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',worker)
    advanced=count(image,stain='he',method='cellpose_nuclei',diameter_px=17,min_area_px=9)
    assert seen['model']=='nuclei' and seen['diameter']==17 and seen['min_size']==9
    assert seen['signal'][24,24]>seen['signal'][0,0]
    assert advanced['provenance']['model']=='nuclei'
    with pytest.raises(ValueError):count(image,stain='dapi')


def test_optional_dapi_cellpose_local_contrast_keeps_raw_signal_and_saves_input_settings(monkeypatch):
    from core.image_analysis import cellpose_backend
    y,x=np.mgrid[:80,:100]
    raw=(100+2000*np.exp(-((x-30)**2+(y-40)**2)/40)+250*np.exp(-((x-70)**2+(y-40)**2)/40)).astype(np.uint16)
    before=raw.copy();seen=[]
    labels=np.zeros(raw.shape,np.int32);labels[35:45,25:35]=1
    def worker(signal,**params):
        seen.append(signal.copy());return labels,dict(model='nuclei')
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',worker)
    result=nuclear_api()(raw,stain='dapi',method='cellpose_nuclei',diameter_px=25,
                         min_area_px=8,cellpose_input='local_contrast')
    assert seen[0].dtype==np.uint8 and not np.array_equal(seen[0],raw)
    assert result['nuclei'][0]['mean_raw_signal']==pytest.approx(raw[labels==1].mean())
    assert result['parameters']['cellpose_input']=='local_contrast'
    assert result['provenance']['input_preparation']['clip_limit']==2
    np.testing.assert_array_equal(raw,before)


def test_he_local_detection_keeps_faint_nuclei_beside_strong_nuclei():
    from skimage.color import hed2rgb
    y,x=np.mgrid[:100,:160]
    stains=np.zeros((100,160,3),float)
    stains[...,1]=.035
    for center,amplitude in [(30,.18),(80,.045),(130,.035)]:
        stains[...,0]+=amplitude*np.exp(-((x-center)**2+(y-50)**2)/8)
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    before=image.copy()
    result=nuclear_api()(image,stain='he',diameter_px=8,min_area_px=5,max_area_px=150)
    assert len(result['nuclei'])==3
    assert sorted(r['center_x_px'] for r in result['nuclei'])==pytest.approx([30,80,130],abs=2)
    np.testing.assert_array_equal(image,before)


def test_he_fiber_rim_is_not_split_into_nuclear_objects():
    from skimage.color import hed2rgb
    y,x=np.mgrid[:120,:160]
    stains=np.zeros((120,160,3),float);stains[...,1]=.035
    radius=np.sqrt((x-80)**2+(y-60)**2)
    stains[...,0]=.08*np.exp(-((radius-42)**2)/.8)
    stains[...,0]+=.12*np.exp(-((x-80)**2+(y-60)**2)/8)
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=nuclear_api()(image,stain='he',diameter_px=8,min_area_px=5,max_area_px=150)
    assert len(result['nuclei'])==1
    row=result['nuclei'][0]
    assert (row['center_x_px'],row['center_y_px'])==pytest.approx((80,60),abs=2)


def test_he_ragged_weak_fiber_rim_does_not_create_phantom_peak_seeds():
    from skimage.color import hed2rgb
    y,x=np.mgrid[:120,:160]
    radius=np.sqrt((x-80)**2+(y-60)**2)
    angle=np.arctan2(y-60,x-80)
    stains=np.zeros((120,160,3),float);stains[...,1]=.035
    stains[...,0]=(.015+.006*np.cos(angle*17))*np.exp(-((radius-42-.4*np.cos(angle*23))**2)/.8)
    stains[...,0]+=.12*np.exp(-((x-80)**2+(y-60)**2)/8)
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=nuclear_api()(image,stain='he',diameter_px=8,min_area_px=3,max_area_px=150)
    assert len(result['nuclei'])==1


def test_he_dark_core_does_not_hide_the_surrounding_nuclear_body():
    from skimage.color import hed2rgb
    y,x=np.mgrid[:80,:100]
    stains=np.zeros((80,100,3),float);stains[...,1]=.035
    stains[...,0]=.12*np.exp(-((x-50)**2+(y-40)**2)/6)
    stains[40,50,0]=1.5
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=nuclear_api()(image,stain='he',diameter_px=5,min_area_px=3,max_area_px=100)
    assert len(result['nuclei'])==1


def test_he_elongated_nucleus_is_not_discarded_as_a_fiber_rim():
    from skimage.color import hed2rgb
    y,x=np.mgrid[:80,:100]
    stains=np.zeros((80,100,3),float);stains[...,1]=.035
    stains[...,0]=.16*np.exp(-((x-50)**2/35+(y-40)**2/1.5))
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=nuclear_api()(image,stain='he',diameter_px=5,min_area_px=3,max_area_px=100)
    assert len(result['nuclei'])==1


def test_he_touching_faint_nucleus_is_not_absorbed_by_strong_neighbor():
    from skimage.color import hed2rgb
    y,x=np.mgrid[:80,:100]
    stains=np.zeros((80,100,3),float);stains[...,1]=.035
    stains[...,0]=(.18*np.exp(-((x-45)**2+(y-40)**2)/3.2)
                    +.07*np.exp(-((x-50)**2+(y-40)**2)/3.2))
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=nuclear_api()(image,stain='he',diameter_px=6,min_area_px=3,max_area_px=100)
    assert len(result['nuclei'])==2
    assert sorted(r['center_x_px'] for r in result['nuclei'])==pytest.approx([45,50],abs=1)


def test_he_branching_collagen_fragment_is_not_a_nucleus():
    from skimage.color import hed2rgb
    y,x=np.mgrid[:100,:120]
    stains=np.zeros((100,120,3),float);stains[...,1]=.035
    stains[20:22,20:33,0]=.08;stains[20:33,20:22,0]=.08
    stains[...,0]+=.15*np.exp(-((x-80)**2+(y-65)**2)/6)
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=nuclear_api()(image,stain='he',diameter_px=5,min_area_px=3,max_area_px=200)
    assert len(result['nuclei'])==1
    row=result['nuclei'][0]
    assert (row['center_x_px'],row['center_y_px'])==pytest.approx((80,65),abs=1)


def test_he_retained_profile_is_replayable_and_overrides_do_not_mutate_baseline():
    import inspect,json
    from pathlib import Path
    from skimage.color import hed2rgb
    from core.image_analysis import nuclei_counting as api
    assert 'he_settings' in inspect.signature(api.count_nuclei).parameters
    profile_path=Path(__file__).resolve().parents[1]/'resources/analysis_presets/he_local_contrast_v1_20261021.json'
    assert profile_path.is_file(), 'The approved optimization baseline must be retained separately'
    profile=json.loads(profile_path.read_text(encoding='utf-8'))
    assert profile['algorithm']=='he_local_contrast_v1'
    assert profile['parameters']['he_settings']==dict(api.HE_LOCAL_CONTRAST_V1)
    y,x=np.mgrid[:80,:100]
    stains=np.zeros((80,100,3),float);stains[...,1]=.035
    stains[...,0]=.15*np.exp(-((x-50)**2+(y-40)**2)/6)
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    baseline=api.count_nuclei(image,**profile['parameters'])
    params=baseline['parameters']
    assert params['he_settings']==dict(api.HE_LOCAL_CONTRAST_V1)
    replay=api.count_nuclei(image,**params)
    np.testing.assert_array_equal(replay['labels'],baseline['labels'])
    custom={'minimum_width_fraction':2.}
    adjusted=api.count_nuclei(image,stain='he',diameter_px=5,min_area_px=3,he_settings=custom)
    assert not adjusted['nuclei'] and len(baseline['nuclei'])==1
    custom['minimum_width_fraction']=3.
    assert adjusted['parameters']['he_settings']['minimum_width_fraction']==2.
    assert api.HE_LOCAL_CONTRAST_V1['minimum_width_fraction']==.22
    np.testing.assert_array_equal(api.count_nuclei(image,**profile['parameters'])['labels'],baseline['labels'])
    with pytest.raises(TypeError):api.HE_LOCAL_CONTRAST_V1['minimum_width_fraction']=3.
    with pytest.raises(ValueError):api.count_nuclei(image,stain='he',he_settings={'unknown':2})
    with pytest.raises(ValueError):api.count_nuclei(image,stain='he',he_settings={'smooth_sigma':float('nan')})


def test_he_uniform_nuclear_body_has_one_marker_not_two():
    y,x=np.mgrid[:80,:100]
    image=np.full((80,100,3),[240,150,185],np.uint8)
    for cx in (25,75):image[(x-cx)**2+(y-40)**2<54]=[65,40,100]
    result=nuclear_api()(image,stain='he',diameter_px=17,min_area_px=8)
    assert len(result['nuclei'])==2


def round_he_example(centers,*,white=False):
    from skimage.color import hed2rgb
    y,x=np.mgrid[:120,:160]
    stains=np.zeros((120,160,3),float);stains[...,1]=.035
    for cx,cy in centers:
        radius=np.sqrt((x-cx)**2+(y-cy)**2)
        stains[...,0]+=.13*np.exp(-((radius-6)**2)/.8)
        interior=radius<5
        stains[interior,0]=.012 if not white else 0
        stains[interior,1]=.002 if not white else 0
    return np.round(hed2rgb(stains)*255).astype(np.uint8)


def test_round_he_hollow_nuclear_body_is_one_complete_object():
    from core.image_analysis import nuclei_counting as api
    assert hasattr(api,'HE_ROUND_NUCLEI_V1'), 'Round/pale-core detection is not implemented'
    image=round_he_example([(60,60)]);before=image.copy()
    result=api.count_nuclei(image,stain='he',method='he_round',diameter_px=12,min_area_px=8,max_area_px=300)
    assert len(result['nuclei'])==1
    assert result['labels'][60,60]>0
    assert (result['nuclei'][0]['center_x_px'],result['nuclei'][0]['center_y_px'])==pytest.approx((60,60),abs=1)
    np.testing.assert_array_equal(before,image)


def test_round_he_keeps_touching_rings_separate():
    from core.image_analysis import nuclei_counting as api
    assert hasattr(api,'HE_ROUND_NUCLEI_V1')
    image=round_he_example([(55,60),(69,60)])
    result=api.count_nuclei(image,stain='he',method='he_round',diameter_px=12,min_area_px=8,max_area_px=300)
    assert len(result['nuclei'])==2


def test_round_he_rejects_white_void_without_dropping_small_real_nucleus():
    from core.image_analysis import nuclei_counting as api
    assert hasattr(api,'HE_ROUND_NUCLEI_V1')
    image=round_he_example([(45,60)],white=True)
    real=round_he_example([(110,60)])
    image[:,80:]=real[:,80:]
    result=api.count_nuclei(image,stain='he',method='he_round',diameter_px=12,min_area_px=8,max_area_px=300)
    assert len(result['nuclei'])==1
    assert result['nuclei'][0]['center_x_px']>80


def test_round_he_does_not_fill_the_whole_muscle_fiber():
    from core.image_analysis import nuclei_counting as api
    from skimage.color import hed2rgb
    assert hasattr(api,'HE_ROUND_NUCLEI_V1')
    y,x=np.mgrid[:120,:160];r=np.sqrt((x-80)**2+(y-60)**2)
    stains=np.zeros((120,160,3));stains[...,1]=.035
    stains[...,0]=.09*np.exp(-((r-35)**2)/.8)
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=api.count_nuclei(image,stain='he',method='he_round',diameter_px=12,min_area_px=8,max_area_px=300)
    assert not result['nuclei']


def test_round_he_small_bright_punctum_does_not_erase_a_real_blue_body():
    from core.image_analysis import nuclei_counting as api
    y,x=np.mgrid[:100,:120]
    image=np.full((100,120,3),[240,150,185],np.uint8)
    image[(x-60)**2+(y-50)**2<=36]=[100,65,160]
    image[48:53,58:63]=255
    result=api.count_nuclei(image,stain='he',method='he_round',diameter_px=12,min_area_px=8,max_area_px=300)
    assert len(result['nuclei'])==1
    assert result['labels'][50,60]>0


def test_round_he_thin_attached_stroma_does_not_distort_nuclear_body():
    from core.image_analysis import nuclei_counting as api
    image=round_he_example([(60,60)])
    image[60:62,66:95]=[80,50,130]
    result=api.count_nuclei(image,stain='he',method='he_round',diameter_px=12,min_area_px=8,max_area_px=300)
    assert len(result['nuclei'])==1
    assert result['nuclei'][0]['center_x_px']==pytest.approx(60,abs=2)


def test_round_he_small_solid_nucleus_survives_white_void_rejection():
    from core.image_analysis import nuclei_counting as api
    image=round_he_example([(45,60)],white=True)
    y,x=np.mgrid[:120,:160]
    image[(x-110)**2+(y-60)**2<=9]=[70,40,120]
    result=api.count_nuclei(image,stain='he',method='he_round',diameter_px=12,min_area_px=8,max_area_px=300)
    assert len(result['nuclei'])==1 and result['nuclei'][0]['center_x_px']>100


def test_muscle_distribution_counts_fibers_not_total_nuclei():
    assert importlib.util.find_spec('core.image_analysis.muscle_nuclei'), 'Per-fiber nuclear distribution is not implemented'
    from core.image_analysis.muscle_nuclei import classify_fiber_nuclei, summarize_fiber_nuclei
    fibers=np.zeros((100,250),np.int32)
    nuclei=np.zeros_like(fibers)
    index=1
    for fiber_id,central_count in enumerate([0,1,2,3],1):
        left=(fiber_id-1)*60+5
        fibers[10:90,left:left+50]=fiber_id
        for n in range(central_count):
            nuclei[30+n*16:34+n*16,left+23:left+27]=index;index+=1
    nuclei[12:16,70:74]=index;index+=1  # peripheral in fiber 2
    nuclei[2:5,240:244]=index          # interstitial/outside
    result=classify_fiber_nuclei(fibers,nuclei,peripheral_fraction=.2)
    assert [r['central_nuclei'] for r in result['fibers']]==[0,1,2,3]
    assert result['fibers'][1]['peripheral_nuclei']==1
    assert result['summary']['distribution']==[
        {'central_nuclei':k,'fiber_count':1,'percentage':25.0} for k in range(4)]
    assert result['summary']['outside_nuclei']==1
    result['nuclei'][-1]['classification']='central'
    result['nuclei'][-1]['fiber_id']=1
    updated=summarize_fiber_nuclei(result['fibers'],result['nuclei'])
    assert updated['distribution'][1]['fiber_count']==2


def test_ambiguous_and_edge_fibers_do_not_bias_distribution():
    from core.image_analysis.muscle_nuclei import classify_fiber_nuclei
    fibers=np.zeros((60,100),np.int32);fibers[5:55,5:50]=1;fibers[5:55,50:95]=2;fibers[:4,5:95]=3
    nuclei=np.zeros_like(fibers);nuclei[25:30,47:53]=1
    result=classify_fiber_nuclei(fibers,nuclei)
    assert result['nuclei'][0]['classification']=='uncertain'
    assert result['summary']['review_fibers']==0
    assert result['summary']['peripheral_review_fibers']==2
    assert result['summary']['included_fibers']==2
    assert result['summary']['excluded_edge_fibers']==1


def test_fiber_segmentation_preserves_narrow_white_gaps_and_nuclear_holes():
    import inspect
    from core.image_analysis.he_fiber_area import analyze_he_fiber_area,build_auto_he_fiber_calibration
    assert 'preserve_fiber_boundaries' in inspect.signature(analyze_he_fiber_area).parameters
    image=np.full((150,400,3),245,np.uint8)
    for x in (30,92,154):
        image[30:120,x:x+60]=[235,70,160]
        image[70:76,x+25:x+31]=[40,30,100]
    result=analyze_he_fiber_area(image,build_auto_he_fiber_calibration(image),segmentation_method='anti_oversegmentation',
        preserve_fiber_boundaries=True,splitting_sensitivity=85)
    labels=result['he_fiber_labels']
    ids=[labels[60,x+30] for x in (30,92,154)]
    assert len(set(ids))==3 and min(ids)>0
    assert not labels[:,90:92].any()
    assert labels[73,57]==ids[0]


def test_joint_classification_uses_saved_nuclear_rows_without_changing_either_input():
    from copy import deepcopy
    from core.image_analysis.nuclei_counting import object_rows
    from core.image_analysis.muscle_nuclei import classify_fiber_nuclei
    fibers=np.zeros((80,150),np.int32);fibers[5:75,5:65]=7;fibers[5:75,80:140]=12
    labels=np.zeros_like(fibers);labels[35:40,30:35]=3;labels[7:11,100:104]=9
    labels[40:44,110:114]=15
    rows=object_rows(labels,np.full(labels.shape,.25))
    for row in rows:row['classification']='included'
    rows[-1].update(classification='excluded',include_in_statistics=False,reviewed=True)
    original_rows=deepcopy(rows);original_fibers=fibers.copy()
    result=classify_fiber_nuclei(fibers,nuclei=rows)
    assert [r['classification'] for r in result['nuclei']]==['central','peripheral','excluded']
    assert [r['object_id'] for r in result['nuclei']]==[3,9,15]
    assert [r['fiber_id'] for r in result['fibers']]==[7,12]
    assert result['nuclei'][0]['mean_raw_signal']==.25
    assert result['summary']['distribution']==[
        dict(central_nuclei=0,fiber_count=1,percentage=50.),
        dict(central_nuclei=1,fiber_count=1,percentage=50.)]
    assert rows==original_rows
    np.testing.assert_array_equal(fibers,original_fibers)


def test_refined_he_merges_shallow_lobes_but_keeps_touching_nuclei_separate():
    from core.image_analysis.nuclei_counting import count_nuclei
    from skimage.color import hed2rgb
    y,x=np.mgrid[:120,:180]
    stains=np.zeros((120,180,3),float);stains[...,1]=.035
    # A single bilobed body with a shallow internal waist.
    body=((x-40)**2+(y-60)**2<49)|((x-46)**2+(y-60)**2<49)
    stains[body,0]=.13
    image=np.round(hed2rgb(stains)*255).astype(np.uint8)
    result=count_nuclei(image,stain='he',method='he_refined',diameter_px=12,min_area_px=8,max_area_px=800)
    assert len(result['nuclei'])==1
    adjacent=round_he_example([(55,60),(69,60)])
    result=count_nuclei(adjacent,stain='he',method='he_refined',diameter_px=12,min_area_px=8,max_area_px=800)
    assert len(result['nuclei'])==2
    assert result['parameters']['he_settings']['peak_prominence_fraction']==.125
    assert result['parameters']['he_settings']['supplement_min_area_fraction']==.25
    assert result['provenance']['supplement_min_area_px'] >= 28
    assert result['parameters']['local_he_settings']


def test_refined_candidate_merge_rejects_nearby_fragment_not_a_faint_separate_nucleus():
    from core.image_analysis.he_round_nuclei import merge_nuclear_candidates
    primary=np.zeros((80,120),np.int32);primary[30:40,30:40]=7
    candidates=np.zeros_like(primary)
    candidates[40:42,33:37]=1  # Little overlap, but close to the same nuclear body.
    candidates[30:36,80:86]=2
    original=primary.copy()
    result=merge_nuclear_candidates(primary,candidates)
    assert set(np.unique(result))=={0,7,8}
    assert result[41,35]==0 and result[32,82]==8
    np.testing.assert_array_equal(primary,original)


def test_refined_he_still_rejects_white_voids_and_retains_real_pale_nucleus():
    from core.image_analysis.nuclei_counting import count_nuclei
    image=round_he_example([(45,60)],white=True)
    image[:,80:]=round_he_example([(110,60)])[:,80:]
    result=count_nuclei(image,stain='he',method='he_refined',diameter_px=12,min_area_px=8,max_area_px=800)
    assert len(result['nuclei'])==1
    assert result['nuclei'][0]['center_x_px']>100
