from copy import deepcopy
import numpy as np
from core.image_analysis.muscle_nuclei import classify_fiber_nuclei, summarize_fiber_nuclei


def test_nuclear_body_reaching_peripheral_band_is_not_central_by_centroid():
    fibers = np.zeros((100, 100), np.int32)
    fibers[10:90, 10:90] = 1
    labels = np.zeros_like(fibers)
    labels[45:55, 12:35] = 1
    row = classify_fiber_nuclei(fibers, labels)['nuclei'][0]
    assert row['classification'] == 'peripheral'
    assert row['normalized_border_distance'] > .2
    assert row['normalized_nuclear_edge_distance'] < .2


def test_unique_boundary_overlap_below_half_can_be_peripheral_not_uncertain():
    fibers = np.zeros((80, 100), np.int32)
    fibers[10:70, 10:90] = 7
    labels = np.zeros_like(fibers)
    labels[7:12, 40:46] = 5  # 40% inside a single clear fiber edge
    row = classify_fiber_nuclei(fibers, labels)['nuclei'][0]
    assert row['classification'] == 'peripheral'
    assert row['fiber_id'] == 7
    assert row['overlap_fraction'] == .4


def test_near_border_without_overlap_needs_review_not_automatic_myofiber_assignment():
    fibers = np.zeros((80, 100), np.int32)
    fibers[10:70, 10:90] = 7
    labels = np.zeros_like(fibers)
    labels[7:9, 40:46] = 5
    result = classify_fiber_nuclei(fibers, labels)
    row = result['nuclei'][0]
    assert row['classification'] == 'uncertain'
    assert row['fiber_id'] is None and row['candidate_fiber_ids'] == [7]
    assert result['summary']['peripheral_review_fibers'] == 1
    assert result['summary']['included_fibers'] == 1  # Central count is still certainly zero.


def test_between_neighboring_fibers_is_not_forced_onto_dominant_neighbor():
    fibers = np.zeros((80, 100), np.int32)
    fibers[10:70, 5:45] = 1
    fibers[10:70, 48:95] = 2
    labels = np.zeros_like(fibers)
    labels[30:35, 43:50] = 9
    row = classify_fiber_nuclei(fibers, labels)['nuclei'][0]
    assert row['classification'] == 'uncertain'
    assert row['candidate_fiber_ids'] == [1, 2] or row['candidate_fiber_ids'] == [2, 1]


def test_population_denominator_and_nuclear_totals_use_same_included_fibers():
    fibers = [dict(fiber_id=1, include_in_statistics=True, border_truncated=False),
        dict(fiber_id=2, include_in_statistics=False, border_truncated=True),
        dict(fiber_id=3, include_in_statistics=False, border_truncated=False),
        dict(fiber_id=4, include_in_statistics=True, border_truncated=False)]
    nuclei = [dict(classification='central', fiber_id=i, include_in_statistics=True) for i in (1, 2, 3, 4)]
    nuclei.append(dict(classification='uncertain', fiber_id=None, candidate_fiber_ids=[4], include_in_statistics=True))
    nuclei.append(dict(classification='uncertain', fiber_id=None, candidate_fiber_ids=[1], include_in_statistics=False))
    before = deepcopy(nuclei)
    summary = summarize_fiber_nuclei(fibers, nuclei)
    assert summary['central_nuclei'] == 1
    assert summary['included_fibers'] == 1 and summary['review_fibers'] == 1
    assert summary['excluded_manual_fibers'] == 1 and summary['excluded_edge_fibers'] == 1
    assert summary['total_fibers'] == sum(summary[k] for k in
        ('included_fibers', 'review_fibers', 'excluded_manual_fibers', 'excluded_edge_fibers'))
    assert sum(row['fiber_count'] for row in summary['distribution']) == 1
    assert sum(row['percentage'] for row in summary['distribution']) == 100
    assert nuclei == before


def test_uncertain_boundary_ownership_does_not_invalidate_a_known_central_count():
    fibers = np.zeros((100, 100), np.int32)
    fibers[10:90, 10:90] = 7
    labels = np.zeros_like(fibers)
    labels[45:50, 45:50] = 1
    labels[7:9, 40:46] = 2
    result = classify_fiber_nuclei(fibers, labels)
    assert result['summary']['included_fibers'] == 1
    assert result['summary']['central_nuclei'] == 1
    assert result['summary']['peripheral_review_fibers'] == 1
    assert result['summary']['distribution'][1]['fiber_count'] == 1


def test_explicitly_reviewed_source_edge_nucleus_keeps_its_inclusion_choice():
    from core.image_analysis.nuclei_counting import object_rows
    fibers=np.zeros((60,80),np.int32);fibers[:50,5:75]=1
    labels=np.zeros_like(fibers);labels[:5,30:35]=3
    rows=object_rows(labels,labels)
    rows[0].update(reviewed=True,include_in_statistics=True,classification='included')
    result=classify_fiber_nuclei(fibers,nuclei=rows)
    assert result['nuclei'][0]['classification']=='peripheral'
    assert result['nuclei'][0]['reviewed'] is True
