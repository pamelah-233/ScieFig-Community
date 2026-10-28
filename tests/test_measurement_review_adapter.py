from copy import deepcopy
from importlib.util import find_spec

import numpy as np
import pytest
import tifffile

from core.models import BrightfieldPanel, FluorescencePanel, IHCPanel
from tests.test_measurement_review import fluorescence_record, session, square


def adapter():
    assert find_spec('core.measurement_review_adapter') is not None, 'Object review adapter is missing'
    from core import measurement_review_adapter
    return measurement_review_adapter


def panel_with_record():
    panel = FluorescencePanel(panel_id='Review')
    record = fluorescence_record()
    panel.analysis['fluorescence_ai_assisted_measurements'] = [record]
    panel.data['fluorescence_images'] = [{'uid': 'image-1', 'fluorescence_ai_assisted_measurements': [deepcopy(record)]}]
    return panel, record


def test_eligible_measurements_preserves_channel_records_but_excludes_aggregates():
    module = adapter()
    panel, first = panel_with_record()
    second = deepcopy(first)
    second['ai_measurement_uuid'], second['channel_index'] = 'run-2', 1
    panel.analysis['fluorescence_ai_assisted_measurements'].append(second)
    assert module.eligible_measurements(panel) == [first, second]
    ihc = IHCPanel(panel_id='IHC')
    ihc.analysis['ihc_measurements'] = [{'measurement_uuid': 'ihc-1', 'results': {'object_count': 10}}]
    assert module.eligible_measurements(ihc) == []


def test_apply_updates_both_native_collections_invalidates_statistics_and_audits():
    module = adapter()
    panel, original = panel_with_record()
    untouched = {'count_uuid': 'other', 'type': 'spot_count', 'detected_spots': [], 'count': 0}
    panel.analysis['object_counts'] = [untouched]
    panel.analysis['fluoro_statistics'] = {'stale': True}
    review = session(original)
    review.delete_objects([4])
    updated = review.updated_record()
    module.apply_review_to_panel(panel, original, updated)
    assert panel.analysis['fluorescence_ai_assisted_measurements'][0] == updated
    assert panel.images()[0]['fluorescence_ai_assisted_measurements'][0] == updated
    assert original == updated
    assert panel.analysis['object_counts'] == [untouched]
    assert 'fluoro_statistics' not in panel.analysis
    assert panel.audit_log[-1].action == 'review_measurement_objects'
    restored = FluorescencePanel.from_dict(panel.to_dict())
    assert restored.analysis['fluorescence_ai_assisted_measurements'][0] == updated


def test_apply_rejects_missing_or_mismatched_identity_without_any_mutation():
    module = adapter()
    panel, original = panel_with_record()
    before = deepcopy(panel.to_dict())
    updated = session(original).updated_record()
    updated['ai_measurement_uuid'] = 'wrong-run'
    with pytest.raises(ValueError):
        module.apply_review_to_panel(panel, original, updated)
    assert panel.to_dict() == before


def test_source_image_uses_saved_channel_background_not_current_calibration_or_correction(tmp_path):
    module = adapter()
    data = np.arange(1200, dtype=np.uint16).reshape(1, 30, 40)
    path = tmp_path / 'review.tif'
    tifffile.imwrite(path, data, metadata={'axes': 'CYX'}, photometric='minisblack')
    panel = FluorescencePanel(panel_id='Review source')
    image = panel.import_image(path)
    record = fluorescence_record()
    record['image_uid'] = image['uid']
    record['background_correction'] = {'mode': 'black_balance', 'channel_backgrounds': {'0': 5.}}
    image['background_correction'] = {'mode': 'black_balance', 'channel_backgrounds': {'0': 500.}}
    before = np.array(panel.image_data(image['uid']), copy=True)
    qimage, intensity = module.review_source_image(panel, record)
    assert not qimage.isNull()
    assert (qimage.width(), qimage.height()) == (40, 30)
    assert np.array_equal(intensity, np.maximum(data[0].astype(float) - 5., 0))
    assert np.array_equal(before, panel.image_data(image['uid']))


def test_apply_brightfield_updates_latest_alias_and_keeps_other_images():
    module = adapter()
    panel = BrightfieldPanel(panel_id='BF')
    record = {'measurement_uuid': 'bf-run', 'type': 'brightfield_bright_round_cell_diameter',
              'image_uid': 'bf-image', 'results': [{'id': 1, 'area_px': 25, 'contour_px': square()}]}
    panel.analysis['bright_round_cell_diameter_measurements'] = [record]
    panel.analysis['bright_round_cell_diameter'] = deepcopy(record)
    review = session(record)
    review.delete_objects([1])
    updated = review.updated_record()
    module.apply_review_to_panel(panel, record, updated)
    assert panel.analysis['bright_round_cell_diameter'] == updated
    assert panel.analysis['bright_round_cell_diameter_measurements'][0] == updated


def test_stale_record_changes_are_not_overwritten_by_a_review():
    module = adapter()
    panel, record = panel_with_record()
    before = deepcopy(record)
    review = session(before)
    review.delete_objects([4])
    record['object_table'][0]['area_px'] = 300
    with pytest.raises(ValueError, match='changed'):
        module.apply_review_to_panel(panel, before, review.updated_record())
    assert record['object_table'][0]['area_px'] == 300
