from copy import deepcopy
import numpy as np
from core.measurement_review import ObjectReviewSession
from core.nuclear_geometry_review import nuclear_review_record, apply_nuclear_geometry
from core.image_analysis.nuclei_counting import object_rows
from core.image_analysis.muscle_nuclei import classify_fiber_nuclei


def test_shared_polygon_review_adds_missing_nucleus_without_changing_original():
    labels=np.zeros((80,100),np.int32);labels[20:25,30:35]=1
    original=dict(task='he',nuclei=object_rows(labels,np.full(labels.shape,.25)),measurement_uuid='original')
    before=deepcopy(original)
    session=ObjectReviewSession(nuclear_review_record(original),labels.shape,intensity_image=np.full(labels.shape,.25))
    identifier=session.add_polygon([[60,40],[65,40],[65,45],[60,45]])
    updated=apply_nuclear_geometry(original,session.updated_record())
    assert len(updated['nuclei'])==2 and original==before
    added=next(row for row in updated['nuclei'] if row['object_id']==identifier)
    assert added['mean_raw_signal']==.25 and added['reviewed']
    assert added['classification']=='included'
    assert updated['object_review']['audit'][-1]['action']=='add'


def test_shared_geometry_correction_reclassifies_joint_and_preserves_source_pair():
    fibers=np.zeros((80,100),np.int32);fibers[5:75,5:95]=7
    labels=np.zeros_like(fibers);labels[20:25,30:35]=1
    original=classify_fiber_nuclei(fibers,labels)
    original.update(task='muscle',measurement_uuid='joint',source_measurements=dict(fibers='F',nuclei='N'))
    before=deepcopy(original)
    session=ObjectReviewSession(nuclear_review_record(original),labels.shape,intensity_image=np.full(labels.shape,.25))
    session.add_polygon([[60,40],[65,40],[65,45],[60,45]])
    updated=apply_nuclear_geometry(original,session.updated_record(),fiber_labels=fibers)
    assert updated['fibers'][0]['central_nuclei']==2
    assert updated['summary']['distribution'][2]['fiber_count']==1
    assert updated['source_measurements']==original['source_measurements']
    assert original==before


def test_review_merge_uses_all_mask_parts_when_deciding_fiber_ownership():
    fibers=np.zeros((80,160),np.int32);fibers[5:75,5:75]=7;fibers[5:75,85:155]=12
    labels=np.zeros_like(fibers);labels[35:40,35:40]=1;labels[35:40,115:120]=2
    original=classify_fiber_nuclei(fibers,labels);original['task']='muscle'
    session=ObjectReviewSession(nuclear_review_record(original),labels.shape)
    session.merge_objects([1,2])
    updated=apply_nuclear_geometry(original,session.updated_record(),fiber_labels=fibers)
    assert len(updated['nuclei'])==1
    assert updated['nuclei'][0]['classification']=='uncertain'
    assert set(updated['nuclei'][0]['candidate_fiber_ids'])=={7,12}


def test_joint_geometry_can_be_replayed_from_a_new_independent_nuclear_source():
    from core.nuclear_geometry_review import nuclear_source_from_joint
    fibers=np.zeros((80,100),np.int32);fibers[5:75,5:95]=7
    labels=np.zeros_like(fibers);labels[20:25,30:35]=1
    source=dict(task='he',measurement_uuid='N',nuclei=object_rows(labels,labels),parameters={'method':'watershed'})
    joint=classify_fiber_nuclei(fibers,labels);joint['task']='muscle'
    session=ObjectReviewSession(nuclear_review_record(joint),labels.shape)
    session.add_polygon([[60,40],[65,40],[65,45],[60,45]])
    corrected=apply_nuclear_geometry(joint,session.updated_record(),fiber_labels=fibers)
    new_source=nuclear_source_from_joint(source,corrected)
    assert new_source['measurement_uuid']!='N' and len(source['nuclei'])==1
    replay=classify_fiber_nuclei(fibers,nuclei=new_source['nuclei'])
    assert replay['summary']==corrected['summary']
