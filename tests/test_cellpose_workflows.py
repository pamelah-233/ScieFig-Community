from copy import deepcopy

import numpy as np
import pytest
import tifffile

from core.image_analysis import cellpose_backend
from core.image_analysis.he_fiber_area import analyze_he_fiber_area
from core.models import BrightfieldPanel, FluorescencePanel


def stub_inference(monkeypatch):
    calls = []

    def run(image, *, model, diameter, min_size):
        calls.append((image.copy(), model, diameter, min_size))
        labels = np.zeros(image.shape[:2], np.int32)
        labels[5:15, 5:15] = 1
        labels[20:30, 25:35] = 2
        return labels, dict(model=model, diameter_px=diameter, min_size_px=min_size,
                            cellpose_version='3.1.1.3', niter=200)

    monkeypatch.setattr(cellpose_backend, 'run_cellpose_labels', run)
    return calls


def test_he_cellpose_native_roi_and_no_colour_calibration_dependency(monkeypatch):
    calls = stub_inference(monkeypatch)
    rgb = np.full((40, 50, 3), 180, np.uint8)
    roi = np.zeros((40, 50), bool)
    roi[:18] = True
    result = analyze_he_fiber_area(rgb, {}, segmentation_method='cellpose_cyto3',
        cellpose_diameter_px=90, min_area_px=20, max_area_px=0, roi_mask=roi)
    assert result['results']['he_fiber_count'] == 1
    assert result['results']['he_fiber_areas_px'] == [100]
    assert result['he_fiber_labels'].shape == rgb.shape[:2]
    assert result['analysis_metadata']['cellpose']['model'] == 'cyto3'
    assert calls[0][1:] == ('cyto3', 90, 20)
    np.testing.assert_array_equal(calls[0][0], rgb)


def test_he_cellpose_saved_parameters_provenance_and_label_replay(monkeypatch, tmp_path):
    calls = stub_inference(monkeypatch)
    path = tmp_path / 'he.tif'
    rgb = np.full((40, 50, 3), [246, 241, 244], np.uint8)
    rgb[5:35, 5:45] = [215, 74, 132]
    tifffile.imwrite(path, rgb, photometric='rgb')
    panel = BrightfieldPanel(panel_id='B')
    image = panel.import_image(path)
    panel.calibrate_he_fiber_area(image_uid=image['uid'])
    row = panel.create_ai_object_count_measurement(image_uid=image['uid'], settings={
        'he_fiber_segmentation_method': 'cellpose_cyto3', 'he_fiber_cellpose_diameter_px': 90,
        'min_object_area_px': 20, 'max_object_area_px': 0})
    assert row['model_name'] == 'cellpose:cyto3'
    assert row['results']['ai_object_count'] == 2
    assert row['analysis_metadata']['cellpose']['diameter_px'] == 90
    restored = BrightfieldPanel.from_dict(deepcopy(panel.to_dict()))
    assert restored.data['analysis_settings']['he_fiber_cellpose_diameter_px'] == 90
    monkeypatch.setattr(cellpose_backend, 'run_cellpose_labels', lambda *args, **kwargs:
        (_ for _ in ()).throw(AssertionError('saved labels must not run inference again')))
    labels = restored._he_fiber_area_labels_for_measurement(row)
    assert labels.shape == (40, 50)
    assert len(calls) == 1
    assert np.count_nonzero(labels) == 200


def test_fluoro_cellpose_selected_raw_channels_image_local_and_roundtrip(monkeypatch, tmp_path):
    calls = stub_inference(monkeypatch)
    raw = np.arange(2 * 40 * 50, dtype=np.uint16).reshape(2, 40, 50)
    path = tmp_path / 'fluoro.tif'
    tifffile.imwrite(path, raw, metadata={'axes': 'CYX'}, photometric='minisblack')
    panel = FluorescencePanel(panel_id='F')
    first = panel.import_image(path)
    second = panel.import_image(path)
    rows = panel.run_ai_assisted_analysis(first['uid'], [1], settings={
        'backend': 'cellpose_cyto3', 'cellpose_diameter_px': 40, 'min_object_area_px': 5})
    assert len(calls) == 1
    np.testing.assert_array_equal(calls[0][0], raw[1])
    assert rows[0]['ai_object_count'] == 2
    assert rows[0]['metadata']['model_execution_mode'] == 'runtime'
    assert rows[0]['metadata']['model_id'] == 'cellpose_cyto3'
    assert rows[0]['metadata']['cellpose']['diameter_px'] == 40
    assert rows[0]['object_table'][0]['mean_intensity'] == pytest.approx(raw[1, 5:15, 5:15].mean())
    restored = FluorescencePanel.from_dict(deepcopy(panel.to_dict()))
    images = {image['uid']: image for image in restored.images()}
    assert images[first['uid']]['fluorescence_ai_assisted_measurements'] == rows
    assert images[second['uid']].get('fluorescence_ai_assisted_measurements', []) == []
    np.testing.assert_array_equal(panel.image_data(first['uid']), raw)


def test_explicit_cellpose_failure_does_not_fall_back(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError('worker failed')
    monkeypatch.setattr(cellpose_backend, 'run_cellpose_labels', fail)
    path = tmp_path / 'f.tif'
    tifffile.imwrite(path, np.ones((1, 40, 50), np.uint16), metadata={'axes': 'CYX'}, photometric='minisblack')
    panel = FluorescencePanel(panel_id='F')
    image = panel.import_image(path)
    with pytest.raises(RuntimeError, match='worker failed'):
        panel.run_ai_assisted_analysis(image['uid'], [0], settings={'backend': 'cellpose_cyto3'})
    assert panel.analysis.get('fluorescence_ai_assisted_measurements', []) == []


def test_unknown_backend_is_not_silently_treated_as_local(tmp_path):
    path = tmp_path / 'f.tif'
    tifffile.imwrite(path, np.ones((1, 40, 50), np.uint16), metadata={'axes': 'CYX'}, photometric='minisblack')
    panel = FluorescencePanel(panel_id='F')
    image = panel.import_image(path)
    with pytest.raises(ValueError, match='backend'):
        panel.run_ai_assisted_analysis(image['uid'], [0], settings={'backend': 'missing'})
