"""Adapt nuclear records to the existing staged polygon/split/merge editor."""
from copy import deepcopy
import numpy as np
from uuid import uuid4
from core.image_analysis.muscle_nuclei import classify_fiber_nuclei, summarize_fiber_nuclei


def nuclear_source_from_joint(source, corrected):
    result = deepcopy(source)
    result.update(measurement_uuid=str(uuid4()), nuclei=deepcopy(corrected['nuclei']),
        object_review=deepcopy(corrected['object_review']), geometry_parent_measurement_uuid=source['measurement_uuid'])
    for row in result['nuclei']:
        for key in ('fiber_id', 'candidate_fiber_ids', 'central_candidate_fiber_ids', 'assignment_reason',
                    'automatic_assignment_reason', 'normalized_border_distance', 'normalized_nuclear_edge_distance'):
            row.pop(key, None)
        row['classification'] = 'included' if row.get('include_in_statistics', True) else 'excluded'
        row['automatic_classification'] = row['classification']
    return result


def nuclear_review_record(result):
    record = deepcopy(result)
    record['results'] = [{**deepcopy(row), 'contour_points_px': deepcopy(row['contour'])}
                         for row in result['nuclei']]
    if isinstance(record.get('roi'), dict) and 'geometry' in record['roi']:
        record['roi'] = {'type': record['roi']['type'], 'coordinates_px': record['roi']['geometry']}
    return record


def apply_nuclear_geometry(original, edited, *, fiber_labels=None):
    old = {row['object_id']: row for row in original['nuclei']}
    rows, modified = [], set()
    for item in edited['results']:
        identifier = item['object_id']
        if item.get('review_provenance', {}).get('status') != 'edited' and identifier in old:
            rows.append(deepcopy(old[identifier]))
            continue
        row = deepcopy(item)
        points = row.get('contour_px', row.get('contour_points_px', []))
        if points and isinstance(points[0], dict):
            points = [[p['x'], p['y']] for p in points]
        row.update(contour=np.rint(points).astype(int).tolist(),
            mean_raw_signal=row.get('mean_intensity'), reviewed=True,
            classification='included' if row.get('include_in_statistics', True) else 'excluded',
            geometry_reviewed=True)
        rows.append(row);modified.add(identifier)
    result = deepcopy(original)
    if original['task'] == 'muscle':
        if fiber_labels is None:
            raise ValueError('Saved fiber labels are required to reclassify edited nuclei.')
        classified = classify_fiber_nuclei(fiber_labels, nuclei=rows,
            peripheral_fraction=original.get('parameters', {}).get('peripheral_fraction', .2))
        for row in classified['nuclei']:
            source = old.get(row['object_id'])
            if row['object_id'] not in modified and source and source.get('reviewed'):
                for key in ('classification', 'fiber_id', 'candidate_fiber_ids', 'central_candidate_fiber_ids',
                            'assignment_reason', 'automatic_classification', 'automatic_assignment_reason'):
                    if key in source:row[key] = deepcopy(source[key])
        # Keep the user's fiber inclusion decisions; geometry editing only changes nuclei.
        inclusion = {row['fiber_id']: row for row in original['fibers']}
        for row in classified['fibers']:
            source = inclusion.get(row['fiber_id'], {})
            if source.get('reviewed'):
                row.update(include_in_statistics=source.get('include_in_statistics', True), reviewed=True)
        classified['summary'] = summarize_fiber_nuclei(classified['fibers'], classified['nuclei'])
        result.update({key: classified[key] for key in ('fibers', 'nuclei', 'summary', 'classification_version')})
    else:
        result['nuclei'] = rows
    result['object_review'] = deepcopy(edited['object_review'])
    result['geometry_parent_measurement_uuid'] = original.get('measurement_uuid')
    if result.get('signal_classification'):
        result['signal_classification']['stale']=True
        for row in result['nuclei']:
            row.pop('signal_class',None);row.pop('mean_signal',None);row.pop('background_corrected_mean',None)
    return result
