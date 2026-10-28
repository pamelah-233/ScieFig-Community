from copy import deepcopy
from importlib.util import find_spec

import numpy as np
import pytest


def session(record=None, shape=(30, 40), image=None):
    assert find_spec('core.measurement_review') is not None, 'Object review engine is missing'
    from core.measurement_review import ObjectReviewSession
    return ObjectReviewSession(record or fluorescence_record(), shape, intensity_image=image)


def square(x=2, y=3, size=4):
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size]]


def fluorescence_record():
    return {
        'ai_measurement_uuid': 'run-1', 'image_uid': 'image-1',
        'type': 'fluorescence_ai_assisted_measurement', 'channel_index': 0,
        'ai_object_count': 2, 'model_name': 'original-model',
        'measurement_scale_bar': {'um_per_px_x': .5, 'um_per_px_y': 2.,
                                  'calibrated': True, 'calibration_source': 'manual'},
        'object_table': [
            {'object_id': 4, 'area_px': 25, 'centroid_x': 4., 'centroid_y': 5.,
             'contour_points_px': [{'x': x, 'y': y} for x, y in square()]},
            {'object_id': 9, 'area_px': 9, 'centroid_x': 16., 'centroid_y': 5.,
             'contour_points_px': [{'x': x, 'y': y} for x, y in square(15, 4, 2)]},
        ],
    }


def test_add_recalculates_pixels_physical_dimensions_and_provenance_without_mutation():
    original = fluorescence_record()
    before = deepcopy(original)
    raw = np.arange(1200).reshape(30, 40)
    review = session(original, image=raw)
    identifier = review.add_polygon(square(22, 10, 3))
    row = review.object(identifier)
    assert row['area_px'] == 16
    assert row['area_um2'] == 16
    assert row['width_um'] == 2
    assert row['height_um'] == 8
    assert row['review_provenance']['status'] == 'edited'
    assert row['review_provenance']['action'] == 'add'
    assert row['mean_intensity'] == np.mean(raw[10:14, 22:26])
    assert review.object(4)['review_provenance']['status'] == 'automatic'
    assert original == before
    assert np.array_equal(raw, np.arange(1200).reshape(30, 40))
    result = review.updated_record()
    assert result['ai_object_count'] == 3
    assert result['model_name'] == 'original-model'
    assert result['object_review']['audit'][-1]['action'] == 'add'


def test_replace_keeps_id_and_undo_redo_restore_calculated_values_exactly():
    review = session()
    before = deepcopy(review.objects)
    review.replace_polygon(4, square(1, 1, 7))
    corrected = deepcopy(review.objects)
    assert review.object(4)['area_px'] == 64
    assert review.object(9) == before[1]
    assert review.can_undo and not review.can_redo
    assert review.undo()
    assert review.objects == before
    assert review.can_redo
    assert review.redo()
    assert review.objects == corrected


def test_delete_and_branch_after_undo_never_reuse_an_object_id():
    review = session()
    first = review.add_polygon(square(20, 10))
    assert review.undo()
    second = review.add_polygon(square(20, 10))
    assert second != first
    review.delete_objects([4])
    assert [row['object_id'] for row in review.objects] == [9, second]
    assert not review.can_redo
    assert review.updated_record()['ai_object_count'] == 2
    assert review.updated_record()['object_review']['audit'][-1]['before'][0]['object_id'] == 4


def test_split_partitions_pixels_without_gap_then_merge_restores_union():
    review = session()
    children = review.split_object(4, (4, 0), (4, 12))
    assert len(children) == 2
    assert sum(review.object(value)['area_px'] for value in children) == 25
    assert review.object(9)['object_id'] == 9
    assert review.updated_record()['ai_object_count'] == 3
    merged = review.merge_objects(children)
    assert review.object(merged)['area_px'] == 25
    assert review.object(merged)['review_provenance']['parent_object_ids'] == children
    assert review.updated_record()['ai_object_count'] == 2
    merged_rows = deepcopy(review.objects)
    assert review.undo() and review.redo()
    assert review.objects == merged_rows


def test_disconnected_merge_preserves_components_without_filling_background():
    review = session()
    identifier = review.merge_objects([4, 9])
    row = review.object(identifier)
    assert row['area_px'] == 34
    assert len(row['review_contours_px']) == 2
    assert review.updated_record()['ai_object_count'] == 1
    reopened = session(review.updated_record())
    children = reopened.split_object(identifier, (10, 0), (10, 29))
    assert sum(reopened.object(value)['area_px'] for value in children) == 34


def test_invalid_polygon_and_non_crossing_split_do_not_change_state():
    review = session()
    before = deepcopy(review.objects)
    for polygon in ([[0, 0], [1, 1]], square(39, 2), [[1, 1], [4, 4], [1, 4], [4, 1]]):
        with pytest.raises(ValueError):
            review.add_polygon(polygon)
    with pytest.raises(ValueError):
        review.split_object(4, (25, 0), (25, 12))
    assert review.objects == before
    assert not review.can_undo


def test_geometry_missing_is_not_invented_from_bbox_or_blob_radius():
    record = {'type': 'spot_count', 'count_uuid': 'spot-run', 'count': 1,
              'detected_spots': [{'x': 3, 'y': 4, 'w': 8, 'h': 9,
                                  'centroid_x': 7, 'centroid_y': 8, 'detection_radius_px': 3}]}
    review = session(record)
    row = review.objects[0]
    assert row['review_geometry_available'] is False
    assert row.get('area_px') is None
    with pytest.raises(ValueError, match='geometry'):
        review.split_object(row['object_id'], (7, 0), (7, 20))
    review.replace_polygon(row['object_id'], square())
    result = review.updated_record()
    assert result['detected_spots'][0]['area_px'] == 25
    assert result['detected_spots'][0]['area_um2'] is None
    assert result['count'] == 1


def test_missing_snapshot_never_uses_physical_values_from_elsewhere():
    record = fluorescence_record()
    record.pop('measurement_scale_bar')
    review = session(record)
    identifier = review.add_polygon(square(20, 10))
    row = review.object(identifier)
    assert row['area_um2'] is None
    assert row['equivalent_diameter_um'] is None
    assert row['max_feret_um'] is None
    assert review.updated_record()['object_review']['calibration_snapshot'] == {}


def test_saved_review_calibration_survives_later_record_scale_changes():
    review = session()
    identifier = review.add_polygon(square(20, 10))
    result = review.updated_record()
    result['measurement_scale_bar'] = {'um_per_px': 100., 'calibrated': True}
    reopened = session(result)
    reopened.replace_polygon(identifier, square(20, 10, 2))
    assert reopened.object(identifier)['area_um2'] == 9


def test_brightfield_edits_refresh_native_results_analysis_and_summary():
    record = {'type': 'brightfield_bright_round_cell_diameter', 'measurement_uuid': 'bf-1',
              'measurement_scale_bar': fluorescence_record()['measurement_scale_bar'],
              'results': [{'id': 12, 'contour_px': square(), 'area_px': 25,
                           'shape_class': 'round', 'include_in_statistics': True}],
              'analysis': {'results': [], 'summary': {}}, 'summary': {'count': 1, 'round_count': 1}}
    review = session(record)
    review.replace_polygon(12, square(1, 1, 6))
    updated = review.updated_record()
    assert updated['results'][0]['id'] == 12
    assert updated['results'][0]['area_px'] == 49
    assert updated['results'][0]['max_feret_um'] > updated['results'][0]['max_feret_px']
    assert updated['analysis']['results'] == updated['results']
    assert updated['analysis']['summary'] == updated['summary']
    assert updated['summary']['count'] == 1
    assert updated['summary']['mean_equivalent_diameter_um'] == updated['results'][0]['equivalent_diameter_um']


def test_aggregate_only_ihc_is_rejected_without_fabricated_nuclei():
    with pytest.raises(ValueError, match='aggregate'):
        session({'type': 'dab_positive_area', 'results': {'positive_area_px': 500, 'object_count': 20}})


def test_incomplete_geometry_invalidates_aggregate_area_after_delete():
    record = {'type': 'spot_count', 'count_uuid': 'spots', 'count': 2,
              'total_positive_area_px': 100, 'mean_intensity': 5.,
              'detected_spots': [{'centroid_x': 1, 'centroid_y': 2},
                                  {'centroid_x': 4, 'centroid_y': 7}]}
    review = session(record)
    review.delete_objects([review.objects[0]['object_id']])
    updated = review.updated_record()
    assert updated['count'] == 1
    assert updated['total_positive_area_px'] is None
    assert updated['mean_intensity'] is None


def test_roi_and_excluded_region_are_enforced_on_manual_polygons():
    record = fluorescence_record()
    record['roi_or_geometry'] = {'type': 'rectangle', 'coordinates_px': {'x': 1, 'y': 1, 'w': 20, 'h': 15}}
    review = session(record)
    with pytest.raises(ValueError, match='ROI'):
        review.add_polygon(square(22, 10))


def test_duplicate_ids_are_rejected_instead_of_linking_wrong_objects():
    record = fluorescence_record()
    record['object_table'][1]['object_id'] = 4
    with pytest.raises(ValueError, match='duplicate'):
        session(record)


def test_sample_spot_reference_geometry_is_not_mistaken_for_analysis_roi():
    record = {'type': 'spot_count', 'count_uuid': 'sample-run', 'method': 'sample_like',
              'count': 0, 'detected_spots': [],
              'roi_or_geometry': {'type': 'rectangle', 'coordinates_px': {'x': 2, 'y': 3, 'w': 4, 'h': 4}}}
    review = session(record)
    identifier = review.add_polygon(square(20, 10))
    assert review.object(identifier)['area_px'] == 25


def test_manual_contour_respects_saved_border_exclusion_margin():
    record = {'type': 'brightfield_bright_round_cell_diameter', 'measurement_uuid': 'border-run',
              'results': [], 'params': {'border_margin_px': 3},
              'measurement_scale_bar': fluorescence_record()['measurement_scale_bar']}
    review = session(record)
    identifier = review.add_polygon(square(2, 10))
    assert review.object(identifier)['border_truncated'] is True
    assert review.object(identifier)['include_in_statistics'] is False
