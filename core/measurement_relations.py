"""Image-local derived classifications and ratios from saved measurement objects."""
from copy import deepcopy
import numpy as np
from core.measurement_review import ObjectReviewSession, object_rows
from core.image_analysis.cellpose_backend import unpack_instance_labels
from core.image_analysis.object_phenotyping import classify_object_signal, count_ratio
from core.image_analysis.fusion_index import calculate_fusion_index


def measurement_id(record):
    return record.get('measurement_uuid') or record.get('ai_measurement_uuid') or record.get('smart_measurement_uuid')


def _record_session(record, shape):
    source = deepcopy(record)
    if 'nuclei' in source:
        source['results'] = [{**r, 'contour_points_px':r['contour']} for r in source['nuclei']]
    roi = source.get('roi') or source.get('roi_or_geometry') or {}
    if 'geometry' in roi:
        kind = roi.get('type'); coordinates = deepcopy(roi['geometry'])
        if kind in ('circle','ellipse') and 'cx' in coordinates:
            coordinates = dict(x=coordinates['cx']-coordinates['rx'],y=coordinates['cy']-coordinates['ry'],
                               w=coordinates['rx']*2,h=coordinates['ry']*2)
        source['roi'] = dict(type={'square':'rectangle','freehand':'polygon'}.get(kind,kind), coordinates_px=coordinates)
    return ObjectReviewSession(source, shape)


def geometry_record(record, shape):
    session = _record_session(record, shape)
    geometry = session.native_geometry()
    if record.get('instance_labels') and not record.get('object_review'):
        exact = unpack_instance_labels(record['instance_labels'], shape)
        labels = np.zeros(shape, np.int32)
        for row in geometry['objects']:
            labels[exact == row['object_id']] = row['label_id']
        geometry.update(labels=labels, overlapping_ids=[])
    return geometry


def _same_image(first, second):
    if not first.get('image_uid') or first['image_uid'] != second.get('image_uid'):
        raise ValueError('Choose results from the same image.')


def _excluded(geometry):
    return [r['label_id'] for r in geometry['objects'] if not r.get('include_in_statistics', True)]


def classify_measurement(record, signal, *, channel_index, parameters, signal_source='raw'):
    geometry = geometry_record(record, np.shape(signal))
    result = classify_object_signal(geometry['labels'], signal, roi_mask=geometry['roi_mask'],
                                   excluded_ids=_excluded(geometry), **parameters)
    lookup = {r['label_id']:r for r in geometry['objects']}
    for row in result['objects']:
        original = lookup[row['object_id']]
        row['source_object_id'] = original['object_id']
        row['contour_points_px'] = original.get('contour_points_px', original.get('contour_px', []))
        if row['object_id'] in geometry['overlapping_ids']:
            row['review_required'] = True
    if geometry['overlapping_ids']:
        result['summary']['positive_percent'] = None
        result['summary']['overlapping_objects'] = len(geometry['overlapping_ids'])
    result.update(image_uid=record['image_uid'], source_measurements={'objects':measurement_id(record)},
                  signal_channel_index=int(channel_index), signal_source=signal_source,
                  type='object_classification', image_name=record.get('image_name',''))
    return result


def measurement_count_ratio(numerator, denominator, shape):
    _same_image(numerator, denominator)
    first, second = _record_session(numerator, shape), _record_session(denominator, shape)
    if not np.array_equal(first.measurement_scope, second.measurement_scope):
        raise ValueError('Numerator and denominator must use the same ROI.')
    count = lambda session: sum(r.get('include_in_statistics',True) for r in session.objects)
    return dict(type='count_ratio', image_uid=numerator['image_uid'],
                image_name=numerator.get('image_name',''), summary=count_ratio(count(first), count(second)),
                source_measurements=dict(numerator=measurement_id(numerator),denominator=measurement_id(denominator)))


def measurement_fusion_index(fibers, nuclei, shape, *, min_nuclei=2, edge_policy='exclude'):
    _same_image(fibers, nuclei)
    fg, ng = geometry_record(fibers, shape), geometry_record(nuclei, shape)
    if not np.array_equal(fg['roi_mask'], ng['roi_mask']):
        raise ValueError('Myotube and nuclear results must use the same ROI.')
    review = set(fg['overlapping_ids'])
    review.update(r['label_id'] for r in fg['objects'] if r.get('geometry_review_required'))
    result = calculate_fusion_index(fg['labels'], ng['labels'], min_nuclei=min_nuclei,
                roi_mask=fg['roi_mask'], excluded_fiber_ids=_excluded(fg), excluded_nuclear_ids=_excluded(ng),
                review_fiber_ids=review, edge_policy=edge_policy)
    if ng['overlapping_ids']:
        result['summary'].update(review_required=True,status='review_required',fusion_index_percent=None)
    for row in result['fibers']:
        row['source_fiber_id'] = fg['objects'][row['fiber_id']-1]['object_id']
    for row in result['nuclei']:
        row['source_nucleus_id'] = ng['objects'][row['object_id']-1]['object_id']
    result.update(type='fusion_index',image_uid=fibers['image_uid'],image_name=fibers.get('image_name',''),
                  source_measurements=dict(fibers=measurement_id(fibers),nuclei=measurement_id(nuclei)))
    return result

