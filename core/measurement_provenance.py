"""Compact reproducibility rows for measurement workbook exports (no image pixels)."""
from copy import deepcopy


MEASUREMENT_KEYS = ('measurements', 'object_counts', 'fluorescence_ai_assisted_measurements',
                    'fluorescence_smart_assisted_measurements', 'brightfield_measurements',
                    'brightfield_ai_object_count_measurements', 'bright_round_cell_diameter_measurements', 'ihc_measurements')


def measurement_provenance_rows(panel):
    if panel is None:
        return []
    images = {str(image.get('uid')): image for image in panel.images()}
    rows = []
    for source in MEASUREMENT_KEYS:
        for record in panel.analysis.get(source, []):
            uid = str(record.get('image_uid') or record.get('image_uuid') or '')
            image = images.get(uid, {})
            rows.append(dict(source=source, measurement_id=record.get('measurement_uuid') or record.get('count_uuid') or record.get('ai_measurement_uuid') or record.get('smart_measurement_uuid'),
                image_id=uid, image_name=record.get('image_name') or image.get('source_name'),
                path=image.get('source_path') or image.get('path'),
                channel=record.get('channel_name') or record.get('channel_index'),
                method=record.get('algorithm') or record.get('method') or record.get('analysis_type') or record.get('type'),
                parameters=deepcopy(record.get('algorithm_parameters') or record.get('settings') or record.get('parameters') or record.get('params') or record.get('batch_provenance', {}).get('settings') or {}),
                metadata=deepcopy(record.get('metadata') or {}),
                preprocessing=deepcopy(record.get('input_preprocessing_context') or record.get('background_correction') or {}),
                calibration=deepcopy(record.get('measurement_scale_bar') or {}),
                review=deepcopy(record.get('object_review') or {}),
                created_at=record.get('created_at')))
    for key, result in panel.analysis.items():
        if isinstance(result, dict) and 'statistics' in key:
            for row in result.get('plot_data', []):
                rows.append(dict(source=key, sample_id=row.get('sample_key'), image_id=row.get('image_uid'),
                                 group=row.get('group'), pair_id=row.get('pair_id'), value=row.get('value'),
                                 method=result.get('test')))
    return rows
