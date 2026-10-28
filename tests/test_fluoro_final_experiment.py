import importlib
import numpy as np
import pytest


def experiment():
    try:return importlib.import_module('scripts.analysis_experiments.fluoro_long_tube_final')
    except ModuleNotFoundError:pytest.fail('Final experiment reconstruction is not implemented')


def test_reconstruction_retains_large_holes_and_never_bridges_open_gaps():
    logits=np.full((100,130),-2.,np.float32);logits[5:95,5:55]=2;logits[5:95,75:125]=2
    logits[30:70,15:45]=-2;logits[10:12,80:82]=-2;before=logits.copy()
    labels=experiment().reconstruct(logits,20)
    assert labels[50,25]==0 and labels[50,65]==0 and labels[10,80]>0
    assert labels[20,10]!=labels[20,100]
    np.testing.assert_array_equal(logits,before)


def test_partial_score_ignores_unknown_and_marks_reference_mergers(tmp_path):
    masks=np.zeros((20,30),np.int32);masks[2:8,2:8]=1;masks[12:18,20:26]=2
    support=np.zeros_like(masks);support[masks>0]=2;support[0]=1
    reference=tmp_path/'reference.npz';np.savez(reference,native_masks=masks,native_supervision=support)
    labels=np.where(masks>0,1,0);labels[9:11,5:15]=1
    scored=experiment().partial_metrics(labels,reference)
    assert scored['collision'] and scored['coverage']==1 and scored['exterior_fpr']==0
    assert scored['scope']=='partial mixed-provenance reference pixels; not whole-field accuracy'

