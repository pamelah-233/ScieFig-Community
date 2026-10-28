import json
import cv2
import numpy as np
import pytest

from core.image_analysis.cellpose_cells import measurements_from_labels, worker_command


def test_labels_measure_both_shapes_and_preserve_calibration():
    labels = np.zeros((120, 180), np.int32)
    cv2.circle(labels, (35, 40), 12, 1, -1)
    cv2.ellipse(labels, (115, 65), (30, 6), 0, 0, 360, 2, -1)
    rgb = np.full((*labels.shape, 3), 120, np.uint8)
    result = measurements_from_labels(rgb, labels, .5, {})
    assert result['summary']['round_count'] == 1
    assert result['summary']['elongated_count'] == 1
    a, b = result['results']
    assert 11 < a['equivalent_diameter_um'] < 13
    assert b['max_feret_um'] == pytest.approx(30, abs=.1)  # raster ellipse can have a diagonal extremum
    assert b['min_feret_um'] == pytest.approx(6)
    assert a['detection_source'] == 'cellpose_cyto3'
    json.dumps(result)


def test_roi_contact_is_incomplete_not_silently_measured_as_small_cell():
    labels = np.zeros((80, 80), np.int32)
    cv2.circle(labels, (40, 40), 15, 1, -1)
    roi = np.zeros_like(labels, bool)
    roi[:, :45] = True
    result = measurements_from_labels(np.zeros((80,80,3),np.uint8), labels, .5, {}, roi)
    row = result['results'][0]
    assert row['border_truncated']
    assert not row['include_in_statistics']


def test_frozen_worker_requires_bundled_program_and_does_not_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        worker_command(tmp_path)
    worker = tmp_path / 'cellpose' / 'ScieFig_Cellpose.exe'
    worker.parent.mkdir()
    worker.touch()
    assert worker_command(tmp_path) == [str(worker)]


def test_cell_size_worker_uses_saved_diameter_and_minimum_and_keeps_provenance(monkeypatch):
    from core.image_analysis import cellpose_cells
    calls=[]
    def infer(image, **options):
        calls.append(options)
        return np.zeros(image.shape,np.int32),dict(model='cyto3',diameter_px=options['diameter'],min_size_px=options['min_size'])
    monkeypatch.setattr(cellpose_cells,'run_cellpose_labels',infer)
    raw=np.full((70,90,3),120,np.uint8);before=raw.copy()
    result=cellpose_cells.analyze_cellpose_cells(raw,.5,dict(model_diameter_px=45,model_min_size_px=25,exclude_scale_bar=False))
    assert calls==[dict(diameter=45.,min_size=25)]
    assert result['settings']['model_diameter_px']==45
    assert result['settings']['model_min_size_px']==25
    assert result['cellpose']['diameter_px']==45
    np.testing.assert_array_equal(raw,before)
