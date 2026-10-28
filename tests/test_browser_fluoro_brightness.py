"""Browser transfers must preserve source display, not silently auto-contrast."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
import tifffile
from PIL import Image
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from core.image_analysis import io_engine
from core.image_browser import load_browser_image
from core.models.fluoro_panel import FluorescencePanel
from ui.main_window import MainWindow
from ui.widgets.fluoro_canvas import FluorescenceCanvas

APP = QApplication.instance() or QApplication([])


def pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pointer = image.constBits()
    pointer.setsize(image.sizeInBytes())
    rgba = np.frombuffer(pointer, np.uint8).reshape(image.height(), image.width(), 4).astype(float)
    # Browser displays transparent raster images over white.
    return rgba[:, :, :3] * (rgba[:, :, 3:] / 255) + 255 - rgba[:, :, 3:]


@pytest.mark.parametrize('kind', [
    'czi', 'nd2', 'gray8', 'gray16', 'float1', 'float255',
    'ome2', 'ome3', 'ome4', 'png', 'jpeg', 'bmp', 'webp', 'rgb_tiff', 'rgba',
])
def test_browser_transfer_preserves_display_and_raw_pixels(tmp_path, monkeypatch, kind):
    data = np.random.default_rng(18).integers(0, 13000, (4, 32, 48), dtype=np.uint16)
    data[1] //= 3  # A dim DAPI channel must stay dim after transfer.
    if kind in {'czi', 'nd2'}:
        path = tmp_path / ('cells.' + kind)
        path.touch()
        source = data[:2]
        metadata = {'source_format': kind.upper(), 'width_px': 48, 'height_px': 32, 'n_channels': 2}
        if kind == 'czi':
            metadata['channel_colors'] = ['#FF0100', '#0000FF']
        result = io_engine.ImageImportResult(source, metadata, '', ['Alexa Fluor 594', 'DAPI'])
        monkeypatch.setattr(io_engine, '_read_' + kind, lambda _: result)
    elif kind.startswith('ome'):
        path = tmp_path / 'cells.ome.tif'
        tifffile.imwrite(path, data[:int(kind[-1])], photometric='minisblack', metadata={'axes': 'CYX'}, ome=True)
    elif kind in {'gray8', 'gray16', 'float1', 'float255'}:
        path = tmp_path / 'gray.tif'
        source = {'gray8': (data[0] // 256).astype(np.uint8), 'gray16': data[0],
                  'float1': data[0].astype(np.float32) / 65535,
                  'float255': data[0].astype(np.float32) / 256}[kind]
        tifffile.imwrite(path, source)
    else:
        suffix = {'rgb_tiff': 'tif', 'rgba': 'png'}.get(kind, kind)
        path = tmp_path / ('raster.' + suffix)
        rgb = np.moveaxis((data // 256).astype(np.uint8), 0, -1).copy()
        if kind == 'rgba':
            rgb[:, :, 3] = np.linspace(0, 255, 48).astype(np.uint8)
        Image.fromarray(rgb if kind == 'rgba' else rgb[:, :, :3]).save(path)

    browser = load_browser_image(path)
    panel = FluorescencePanel(panel_id='F')
    record = panel.import_image(path)
    before = panel.image_data(record['uid']).copy()
    payload = {'path': str(path), 'display': {'visible': False}}
    MainWindow._apply_browser_scale_transfer_after_import(panel, record, {str(path): payload})
    canvas = FluorescenceCanvas()
    canvas.panel = panel
    try:
        for rendered in (panel._render_image_pixels(record),
                         canvas._render_fluoro_qimage(record, before, view_mode='merge')):
            delta = np.abs(pixels(rendered) - browser.rgb)
            assert delta.max() <= (0 if kind == 'czi' else 1.01), (kind, delta.max(), pixels(rendered).mean((0, 1)), browser.rgb.mean((0, 1)))
        assert np.array_equal(panel.image_data(record['uid']), before)
        assert all(not channel.get('display_processing') for channel in record['channels'])
        if kind == 'czi':
            isolated = before.copy()
            isolated[0] = 0
            expected = io_engine.cyx_to_rgb_preview(isolated, channel_colors=['#FF0100', '#0000FF'])
            single = canvas._render_fluoro_qimage(record, before, view_mode='single', channel=record['channels'][1])
            assert np.array_equal(pixels(single), expected)
    finally:
        canvas.close()


def test_transferred_display_remains_editable_and_survives_save(tmp_path, monkeypatch):
    from core.models import Project
    import core.models.fluoro_panel as panel_module
    import ui.widgets.fluoro_canvas as canvas_module

    path = tmp_path / 'cells.ome.tif'
    raw = np.arange(3 * 32 * 48, dtype=np.uint16).reshape(3, 32, 48)
    tifffile.imwrite(path, raw, photometric='minisblack', metadata={'axes': 'CYX'}, ome=True)
    panel = FluorescencePanel(panel_id='F')
    record = panel.import_image(path)
    panel.apply_browser_scale_transfer(record['uid'], {'path': str(path)})
    # An active journal style must not replace the inherited image colors.
    for module in (panel_module, canvas_module):
        monkeypatch.setattr(module, 'resolve_runtime_image_plate_style_if_active', lambda _: {'channel_colors': ['#FFFFFF'] * 3})
    canvas = FluorescenceCanvas()
    canvas.panel = panel
    try:
        assert canvas._styled_channels_for_image(record) == record['channels']
        assert panel._styled_channels_for_render(record['channels'], record) == record['channels']
    finally:
        canvas.close()
    measurement = panel.create_roi_intensity_measurement(record['uid'], 'rectangle', {'x': 1, 'y': 2, 'w': 8, 'h': 8}, [1])
    panel.update_channel(record['uid'], 1, display_max=8000, pseudocolor='#00FF00', display_processing={'strength': 25})
    after = panel.create_roi_intensity_measurement(record['uid'], 'rectangle', {'x': 1, 'y': 2, 'w': 8, 'h': 8}, [1])
    assert measurement['results_table'] == after['results_table']
    project = Project()
    project.add_panel(panel)
    target = tmp_path / 'display.sfig'
    project.save_sfig(target)
    loaded = Project.load_sfig(target)
    try:
        restored = loaded.panels[0]
        assert restored.active_image()['channels'] == record['channels']
        assert np.array_equal(restored.image_data(record['uid']), raw)
    finally:
        loaded.dispose()
        project.dispose()
