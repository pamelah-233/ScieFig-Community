from copy import deepcopy
import numpy as np
from skimage.measure import label, regionprops
from core.models.fluoro_panel import FluorescencePanel


def test_new_count_regions_have_true_contours_and_stable_object_ids():
    mask = np.zeros((30, 40), dtype=np.uint8)
    mask[5:15, 7:17] = 1
    rows = FluorescencePanel(panel_id='P')._detected_spots_from_regions(regionprops(label(mask)))
    assert rows[0]['object_id'] == 1
    assert len(rows[0]['contour_points_px']) >= 4
    assert min(p[0] for p in rows[0]['contour_points_px']) == 7
    assert rows[0]['area_px'] == 100


def test_new_counts_snapshot_calibration_without_modifying_old_counts(tmp_path):
    from tests.test_scale_phase1_preservation import _write_ai_tiff
    panel = FluorescencePanel(panel_id='P')
    uid = panel.import_image(_write_ai_tiff(tmp_path / 'count.tif'))['uid']
    panel.update_measurement_scale_bar(uid, um_per_px=.5)
    first = panel.count_channel_objects(uid, [0], method='manual', manual_value=100)
    saved = deepcopy(first)
    assert first[0]['measurement_scale_bar']['um_per_px'] == .5
    panel.update_measurement_scale_bar(uid, um_per_px=.25)
    panel.count_channel_objects(uid, [0], method='manual', manual_value=100)
    assert panel.analysis['object_counts'][0] == saved[0]


def test_cellpose_counts_keep_actual_instance_boundaries(monkeypatch):
    from core.image_analysis.fluorescence_counting import count_with_algorithm
    from core.image_analysis import cellpose_backend
    labels=np.zeros((30,40),dtype=np.int32)
    labels[5:15,7:17]=4
    monkeypatch.setattr(cellpose_backend,'run_cellpose_labels',lambda *a,**k:(labels,{'model':'cyto3'}))
    result=count_with_algorithm(labels,'cellpose_cyto3')
    assert result['detected_spots'][0]['object_id'] == 4
    assert min(p[0] for p in result['detected_spots'][0]['contour_points_px']) == 7
