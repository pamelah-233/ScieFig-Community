"""Bridge object review to the existing panel records and immutable source pixels."""
from copy import deepcopy

import cv2
import numpy as np
from PyQt6.QtGui import QImage

from core.measurement_review import object_rows


COLLECTION_KEYS = (
    'myotube_measurements',
    'object_counts', 'fluorescence_ai_assisted_measurements',
    'fluorescence_smart_assisted_measurements', 'bright_round_cell_diameter_measurements',
    'brightfield_measurements', 'brightfield_ai_object_count_measurements', 'ihc_measurements',
)
IDENTITY_KEYS = ('measurement_uuid', 'ai_measurement_uuid', 'smart_measurement_uuid', 'count_uuid')


def measurement_identity(record):
    for key in IDENTITY_KEYS:
        if record.get(key):
            return key, str(record[key])
    return '', ''


def _stored_records(panel):
    for key in COLLECTION_KEYS:
        yield from panel.analysis.get(key, [])
    alias = panel.analysis.get('bright_round_cell_diameter')
    if isinstance(alias, dict):
        yield alias
    for image in panel.images():
        for key in ('measurements', *COLLECTION_KEYS):
            yield from image.get(key, [])


def eligible_measurements(panel):
    """All saved object-level runs, preserving separate channels and run identities."""
    rows, seen = [], set()
    for record in _stored_records(panel):
        if not isinstance(record, dict):
            continue
        identity = measurement_identity(record)
        if not identity[1] or identity in seen:
            continue
        try:
            object_rows(record)
        except ValueError:
            continue
        seen.add(identity)
        rows.append(record)
    return rows


def apply_review_to_panel(panel, original_record, updated_record):
    """Commit an accepted working copy in-place to all existing native references.

    Identity and stale-record checks happen before any mutation. Recalibration of
    the current image is deliberately irrelevant to this operation.
    """
    identity = measurement_identity(original_record)
    if not identity[1] or measurement_identity(updated_record) != identity:
        raise ValueError('Reviewed measurement identity must match the original run.')
    if any(updated_record.get(key) != original_record.get(key) for key in ('image_uid', 'type', 'channel_index')):
        raise ValueError('Review cannot change the measurement image, channel, or type.')
    object_rows(updated_record)
    targets = []
    seen = set()
    for record in _stored_records(panel):
        if isinstance(record, dict) and measurement_identity(record) == identity and id(record) not in seen:
            if record != original_record:
                raise ValueError('This measurement changed while it was being reviewed. Reopen object review.')
            targets.append(record)
            seen.add(id(record))
    if not targets:
        raise ValueError('The reviewed measurement no longer exists in this panel.')
    updated = deepcopy(updated_record)
    for target in targets:
        target.clear()
        target.update(deepcopy(updated))
    statistics_keys = {
        'fluorescence': ('fluoro_statistics',),
        'brightfield': ('brightfield_statistics', 'brightfield_ai_object_count_statistics',
                       'brightfield_ai_object_count_plot_statistics'),
        'ihc': ('ihc_statistics',),
    }
    for key in statistics_keys.get(panel.panel_type, ()):
        panel.analysis.pop(key, None)
    panel.append_audit('review_measurement_objects',
                       f'{identity[1]}; operations={len(updated.get("object_review", {}).get("audit", []))}')
    return updated


def review_source_image(panel, record):
    """Display raw pixels; return intensity values in the run's saved correction context."""
    uid = str(record.get('image_uid', ''))
    if panel.panel_type == 'fluorescence':
        data = panel.image_data(uid)
        if data is None:
            raise ValueError('Source image data is unavailable for object review.')
        channel = int(record.get('channel_index', 0))
        if channel < 0 or channel >= len(data):
            raise ValueError('The saved measurement channel is unavailable.')
        raw = np.asarray(data[channel])
        display = np.nan_to_num(raw.astype(np.float64))
        lo, hi = float(display.min()), float(display.max())
        display = np.asarray(np.clip((display - lo) * (255. / (hi - lo)) if hi > lo else display, 0, 255), np.uint8)
        display = np.ascontiguousarray(display)
        image = QImage(display.data, display.shape[1], display.shape[0], display.strides[0], QImage.Format.Format_Grayscale8).copy()
        intensity = raw.astype(np.float64, copy=True)
        context = record.get('input_preprocessing_context', {})
        correction = record.get('background_correction', context.get('image_background_correction', {'mode': 'none'}))
        if correction.get('mode') == 'black_balance':
            values = correction.get('channel_backgrounds', {})
            background = float(values.get(str(channel), values.get(channel, 0.)))
            intensity = np.maximum(intensity - background, 0.)
        elif correction.get('mode', 'none') != 'none':
            # Unknown historic processing cannot be reproduced from current settings.
            intensity = None
        return image, intensity
    image = panel.source_qimage(uid).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise ValueError('Source image data is unavailable for object review.')
    pointer = image.constBits()
    pointer.setsize(image.sizeInBytes())
    rgba = np.frombuffer(pointer, np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)
    gray = cv2.cvtColor(rgba[:, :image.width(), :3], cv2.COLOR_RGB2GRAY).copy()
    return image, gray
