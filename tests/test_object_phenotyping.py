import numpy as np
import pytest
from core.image_analysis.object_phenotyping import classify_object_signal, count_ratio
from core.image_analysis.fusion_index import calculate_fusion_index


def test_signal_classes_keep_fixed_thresholds_background_and_source():
    labels=np.zeros((40,80),np.int32); signal=np.full(labels.shape,20.,np.float32)
    for i,value in enumerate([25.,40.,70.],1):
        labels[10:20,20*i-10:20*i]=i; signal[labels==i]=value
    original=signal.copy()
    result=classify_object_signal(labels,signal,positive_threshold=15,strong_threshold=40,background=20)
    assert [r['signal_class'] for r in result['objects']]==['negative','weak_positive','strong_positive']
    assert result['summary']['positive_percent']==pytest.approx(200/3)
    np.testing.assert_array_equal(signal,original)


def test_nuclear_ring_excludes_other_nuclei_and_does_not_share_pixels():
    labels=np.zeros((30,40),np.int32); labels[10:15,10:15]=1; labels[10:15,17:22]=2
    signal=np.full(labels.shape,10.); signal[labels>0]=10000
    result=classify_object_signal(labels,signal,positive_threshold=15,strong_threshold=40,region='perinuclear_ring')
    assert all(r['mean_signal']==10 for r in result['objects'])
    assert result['summary']['negative']==2


def test_zero_denominator_and_independent_ratio_are_explicit():
    assert count_ratio(0,0,subset=True)['percent'] is None
    assert count_ratio(12,10)['percent']==120
    with pytest.raises(ValueError):count_ratio(12,10,subset=True)


def test_zero_marker_after_black_balance_is_negative():
    labels=np.zeros((20,20),np.int32);labels[5:15,5:15]=1
    result=classify_object_signal(labels,np.zeros_like(labels),positive_threshold=0,strong_threshold=10)
    assert result['summary']['negative']==1 and result['summary']['positive_percent']==0


def fusion_fixture():
    fibers=np.zeros((100,220),np.int32); nuclei=np.zeros_like(fibers)
    for i,n in enumerate([0,1,2,3],1):
        x=5+(i-1)*50; fibers[10:80,x:x+40]=i
        for j in range(n):
            nuclei[20+j*15:25+j*15,x+12:x+18]=int(nuclei.max())+1
    nuclei[88:92,20:24]=7
    return fibers,nuclei


def test_fusion_counts_nuclei_in_multinucleated_fibers_and_all_eligible_nuclei():
    fibers,nuclei=fusion_fixture(); result=calculate_fusion_index(fibers,nuclei)
    assert [r['nuclei_count'] for r in result['fibers']]==[0,1,2,3]
    assert result['summary']['fused_nuclei']==5
    assert result['summary']['eligible_nuclei']==7
    assert result['summary']['fusion_index_percent']==pytest.approx(500/7)
    stricter=calculate_fusion_index(fibers,nuclei,min_nuclei=3)
    assert stricter['summary']['fused_nuclei']==3


def test_ambiguous_nucleus_is_not_duplicated_or_reported_as_final_fusion_index():
    fibers=np.zeros((40,60),np.int32);fibers[5:35,5:30]=1;fibers[5:35,30:55]=2
    nuclei=np.zeros_like(fibers);nuclei[15:20,27:33]=1
    result=calculate_fusion_index(fibers,nuclei)
    assert result['nuclei'][0]['assignment']=='uncertain'
    assert sum(r['nuclei_count'] for r in result['fibers'])==0
    assert result['summary']['fusion_index_percent'] is None
    assert result['summary']['eligible_nuclei']==1


def test_excluded_edge_fiber_removes_its_nuclei_from_both_counts():
    fibers,nuclei=fusion_fixture();fibers[:11,155:195]=4
    result=calculate_fusion_index(fibers,nuclei)
    assert result['summary']['excluded_nuclei']==3
    assert result['summary']['eligible_nuclei']==4
    assert result['summary']['fused_nuclei']==2
    assert result['summary']['fusion_index_percent']==50
    assert calculate_fusion_index(fibers,nuclei,edge_policy='visible')['summary']['eligible_nuclei']==7


def test_fusion_rejects_mismatched_native_spaces_and_preserves_inputs():
    fibers,nuclei=fusion_fixture();old=fibers.copy()
    calculate_fusion_index(fibers,nuclei)
    np.testing.assert_array_equal(fibers,old)
    with pytest.raises(ValueError):calculate_fusion_index(fibers,nuclei[:20])

