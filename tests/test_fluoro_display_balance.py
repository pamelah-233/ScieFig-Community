import os
from copy import deepcopy
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
import tifffile
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.plotting import fluoro_display as display
from core.models import FluorescencePanel, Project
from ui.panels.fluorescence_workspace import FluorescenceWorkspace

APP = QApplication.instance() or QApplication([])


def sample(plane, geometry, channel, point):
    assert hasattr(display, 'sample_display_point'), 'Region-based display points are not implemented'
    return display.sample_display_point(plane, geometry, channel, point)


def test_sampling_uses_robust_references_without_mutating_pixels():
    raw = np.full((40, 40), 1000, np.uint16)
    raw[20:] = 12000
    raw[30, 30] = 65535
    before = raw.copy()
    channel = {'display_min': 0, 'display_max': 65535}
    black = sample(raw, {'x': 0, 'y': 0, 'w': 40, 'h': 20}, channel, 'black')
    white = sample(raw, {'x': 0, 'y': 20, 'w': 40, 'h': 20}, {**channel, 'display_processing': black}, 'white')
    assert white['black_point'] == pytest.approx(1000 / 65535)
    assert white['white_point'] == pytest.approx(12000 / 65535)
    assert np.array_equal(raw, before)
    normalized = raw.astype(np.float32) / 65535
    rendered = display.apply_display_optimization(normalized, white)
    assert rendered[2, 2] == pytest.approx(0)
    assert rendered[25, 2] == pytest.approx(1)
    assert np.array_equal(display.apply_display_optimization(normalized, {**white, 'enabled': False}), normalized)


@pytest.mark.parametrize('geometry', [
    {'x': 100, 'y': 100, 'w': 3, 'h': 3},
    {'x': 0, 'y': 0, 'w': 0, 'h': 3},
    {'x': float('nan'), 'y': 0, 'w': 3, 'h': 3},
])
def test_invalid_regions_are_rejected(geometry):
    with pytest.raises(ValueError):
        sample(np.ones((12, 12), np.uint16), geometry, {'display_max': 65535}, 'white')


def test_empty_signal_and_conflicting_points_are_rejected():
    for raw, settings, point in [(np.zeros((12, 12)), {}, 'white'),
                                 (np.ones((12, 12)), {'white_point': .5}, 'black'),
                                 (np.full((12, 12), np.nan), {}, 'black')]:
        with pytest.raises(ValueError):
            sample(raw, {'x': 0, 'y': 0, 'w': 12, 'h': 12}, {'display_max': 1, 'display_processing': settings}, point)


def test_points_can_expand_a_previously_narrowed_display_range():
    from core.plotting.fluoro_render import render_merge_qimage, render_single_qimage
    raw = np.array([[500, 1500, 4000]], dtype=np.uint16)
    channel = {'index': 0, 'display_min': 1000, 'display_max': 2000, 'pseudocolor': '#0000FF'}
    channel['display_processing'] = sample(raw, {'x': 0, 'y': 0, 'w': 1, 'h': 1}, channel, 'black')
    channel['display_processing'] = sample(raw, {'x': 2, 'y': 0, 'w': 1, 'h': 1}, channel, 'white')
    assert channel['display_processing']['black_point'] == -.5
    assert channel['display_processing']['white_point'] == 3
    for image in (render_merge_qimage(raw[None], [channel]), render_single_qimage(raw[None], channel)):
        assert image.pixelColor(0, 0).blue() == 0
        assert image.pixelColor(2, 0).blue() == 255
        assert image.pixelColor(1, 0).blue() == pytest.approx(255 * 1000 / 3500, abs=1)
        assert image.pixelColor(1, 0).red() == image.pixelColor(1, 0).green() == 0


@pytest.fixture
def workspace(tmp_path):
    raw = np.full((2, 48, 64), 1000, np.uint16)
    raw[0, 24:] = 12000
    raw[1] = 6000
    path = tmp_path / 'channels.tif'
    tifffile.imwrite(path, raw, photometric='minisblack', metadata={'axes': 'CYX'})
    panel = FluorescencePanel(panel_id='balance')
    image = panel.import_image(path)
    for index, color in enumerate(('#0000FF', '#FF0000')):
        panel.update_channel(image['uid'], index, display_min=0, display_max=65535, pseudocolor=color)
    ws = FluorescenceWorkspace()
    ws.set_panel(panel)
    ws.set_step(1)
    ws.resize(1100, 750)
    ws.show()
    APP.processEvents()
    yield ws, raw, path
    ws.workflow_panel.close()
    ws.close()
    panel.dispose()


def controls(ws):
    item = ws._display_processing_controls[0]
    assert hasattr(item, 'black_point_button'), 'Display black/white buttons are missing'
    return item


def test_buttons_apply_display_only_then_undo_redo_reset_and_compare(workspace, tmp_path):
    ws, raw, _ = workspace
    image = ws.panel.active_image()
    uid = image['uid']
    c = controls(ws)
    other = deepcopy(image['channels'][1])
    before = deepcopy(image['channels'][0])
    measurement = ws.panel.create_roi_intensity_measurement(uid, 'rectangle', {'x': 2, 'y': 3, 'w': 10, 'h': 10}, [0])
    c.black_point_button.click()
    ws._add_drawn_roi('display_black_point', {'x': 1, 'y': 1, 'w': 10, 'h': 10})
    assert image['channels'][0]['display_processing']['black_point'] == pytest.approx(1000 / 65535)
    c.white_point_button.click()
    ws._add_drawn_roi('display_white_point', {'x': 1, 'y': 28, 'w': 20, 'h': 12})
    assert image['channels'][0]['display_processing']['white_point'] == pytest.approx(12000 / 65535)
    c.strength.setValue(10)
    assert image['channels'][0]['display_processing']['black_point'] > 0
    assert ws.undo_roi_clear()  # strength
    assert ws.undo_roi_clear()  # white
    assert image['channels'][0]['display_processing']['white_point'] == 1
    assert ws.undo_roi_clear(redo=True)
    from core.utils.i18n import tr
    assert c.hint.text() == tr('fluoro_display_point_applied')
    c.compare.setChecked(True)
    assert image['channels'][0]['display_processing']['enabled'] is False
    c.compare.setChecked(False)
    project = Project()
    project.add_panel(ws.panel)
    target = tmp_path / 'points.sfig'
    project.save_sfig(target)
    loaded = Project.load_sfig(target)
    try:
        assert loaded.panels[0].active_image()['channels'][0] == image['channels'][0]
    finally:
        loaded.dispose()
    c.reset_button.click()
    assert display.display_settings(image['channels'][0]['display_processing'])['black_point'] == 0
    assert display.display_settings(image['channels'][0]['display_processing'])['white_point'] == 1
    assert ws.undo_roi_clear()
    assert image['channels'][0]['display_processing']['black_point'] > 0
    after = ws.panel.create_roi_intensity_measurement(uid, 'rectangle', {'x': 2, 'y': 3, 'w': 10, 'h': 10}, [0])
    assert measurement['results_table'] == after['results_table']
    assert np.array_equal(raw, ws.panel.image_data(uid))
    assert image['channels'][1] == other
    assert image.get('background_correction') is None
    assert len(image['measurements']) == 2  # Only the two explicit measurement calls.
    assert before['display_min'] == image['channels'][0]['display_min']


def test_real_rectangle_mapping_cleans_draft_without_moving_canvas(workspace):
    ws, raw, _ = workspace
    c = controls(ws)
    ws._select_preview_channel(0)
    APP.processEvents()
    item = ws.canvas._image_items[0]
    start = ws.canvas.view.mapFromScene(item.mapToScene(QPointF(5, 5)))
    end = ws.canvas.view.mapFromScene(item.mapToScene(QPointF(15, 15)))
    center = ws.canvas.view.mapToScene(ws.canvas.view.viewport().rect().center())
    c.black_point_button.click()
    QTest.mousePress(ws.canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    draft = ws.canvas.view._temp_roi_item
    QTest.mouseMove(ws.canvas.view.viewport(), end)
    QTest.mouseRelease(ws.canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    QTest.qWait(80)
    assert ws.panel.active_image()['channels'][0]['display_processing']['black_point'] > 0
    assert ws.canvas.view.roi_mode == ''
    assert ws.canvas.view._temp_roi_item is None
    assert draft not in ws.canvas.scene.items()
    assert (ws.canvas.view.mapToScene(ws.canvas.view.viewport().rect().center()) - center).manhattanLength() < .1


def test_cancel_lock_and_image_change_do_not_apply_points(workspace):
    ws, _, path = workspace
    c = controls(ws)
    before = deepcopy(ws.panel.active_image()['channels'])
    c.black_point_button.click()
    QTest.keyClick(ws.canvas.view, Qt.Key.Key_Escape)
    assert ws.canvas.view.roi_mode == ''
    assert ws.panel.active_image()['channels'] == before
    ws.lock_display_checkbox.setChecked(True)
    assert not c.black_point_button.isEnabled()
    ws.lock_display_checkbox.setChecked(False)
    c.black_point_button.click()
    ws.panel.import_image(path)
    ws.refresh()
    current = deepcopy(ws.panel.active_image()['channels'])
    ws._add_drawn_roi('display_black_point', {'x': 1, 'y': 1, 'w': 10, 'h': 10})
    assert ws.panel.active_image()['channels'] == current


def test_refresh_and_preview_channel_changes_cancel_during_drag(workspace):
    ws, _, _ = workspace
    c = controls(ws)
    c.black_point_button.click()
    item = ws.canvas._image_items[0]
    start = ws.canvas.view.mapFromScene(item.mapToScene(QPointF(5, 5)))
    QTest.mousePress(ws.canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert ws.canvas.view._temp_roi_item is not None
    ws.refresh()
    assert ws.canvas.view.roi_mode == ''
    assert ws.canvas.view._temp_roi_item is None
    c = controls(ws)
    c.white_point_button.click()
    ws._select_preview_channel(1)
    assert ws.canvas.view.roi_mode == ''
    assert ws._display_balance.pending is None


def test_bad_reference_and_wrong_split_channel_leave_state_unchanged(workspace):
    ws, _, _ = workspace
    c = controls(ws)
    before = deepcopy(ws.panel.active_image()['channels'])
    c.black_point_button.click()
    ws._add_drawn_roi('display_black_point', {'x': 1, 'y': 1, 'w': 10, 'h': 10, 'source_channel_index': 1})
    assert ws.panel.active_image()['channels'] == before
    c.white_point_button.click()
    ws._add_drawn_roi('display_white_point', {'x': 1000, 'y': 1000, 'w': 10, 'h': 10})
    assert ws.panel.active_image()['channels'] == before
    assert not ws._roi_clear_history.undo_stack
    assert ws.canvas.view.roi_mode == ''


def test_display_only_changes_still_work_during_analysis(workspace):
    ws, _, _ = workspace
    c = controls(ws)
    ws._busy_analysis_operations.add('count')
    c.strength.setValue(25)
    assert ws.panel.active_image()['channels'][0]['display_processing']['strength'] == 25
    c.black_point_button.click()
    assert ws._display_balance.pending is None
    ws._busy_analysis_operations.clear()
