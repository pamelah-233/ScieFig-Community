from copy import deepcopy

import numpy as np
import pytest

from core.models import BrightfieldPanel, IHCPanel
from core.plotting.histology_display import qimage_rgb
from core.plotting.histology_processing import apply_histology_processing, estimate_illumination, histology_input_context


def test_flat_field_reduces_smooth_illumination_without_moving_objects():
    y, x = np.mgrid[:384, :512]
    field = .7 + .25 * np.exp(-((x - 256)**2 + (y - 192)**2) / 16000)
    source = np.full((384, 512, 3), 240., dtype=np.float32)
    mask = (x % 64 < 20) & (y % 64 < 20)
    source[mask] = [150, 70, 120]
    source = np.rint(source * field[:, :, None]).astype(np.uint8)
    before = source.copy()
    settings = estimate_illumination(source)
    result = apply_histology_processing(source, {'illumination_correction': settings})
    assert result[~mask, 0].std() < source[~mask, 0].std() * .3
    assert np.array_equal(result[:, :, 1] < 100, mask)
    np.testing.assert_array_equal(source, before)
    settings['enabled'] = False
    np.testing.assert_array_equal(apply_histology_processing(source, {'illumination_correction': settings}), source)


@pytest.mark.parametrize('cls', [BrightfieldPanel, IHCPanel])
def test_histology_analysis_explicitly_selects_adjusted_pixels(tmp_path, cls):
    from PIL import Image
    source = np.tile(np.arange(32, 224, dtype=np.uint8), (128, 1))
    rgb = np.stack([source, source // 2, source], axis=-1)
    path = tmp_path / 'source.png'
    Image.fromarray(rgb).save(path)
    panel = cls(panel_id='processing')
    record = panel.import_image(path)
    raw = panel._load_image_rgb(record['uid'])[1].copy()
    record['rgb_display'] = {'brightness': 10, 'contrast': 20}
    adjusted = qimage_rgb(panel.display_qimage(record['uid']))
    assert not np.array_equal(raw, adjusted)
    np.testing.assert_array_equal(panel._load_image_rgb(record['uid'])[1], raw)
    assert histology_input_context(record) == {}
    record['segmentation_input'] = 'adjusted'
    np.testing.assert_array_equal(panel._load_image_rgb(record['uid'])[1], adjusted)
    context = histology_input_context(record)
    previous = deepcopy(context)
    record['rgb_display']['brightness'] = 20
    assert context == previous
    assert histology_input_context(record) != context
    np.testing.assert_array_equal(qimage_rgb(panel.source_qimage(record['uid'])), raw)
