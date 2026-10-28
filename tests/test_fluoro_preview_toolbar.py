"""Preview shortcuts must not alter channel processing or scientific results."""
import os
from copy import deepcopy
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
import tifffile
from PyQt6.QtWidgets import QApplication
from core.models import FluorescencePanel
from core.utils.i18n import set_language, tr
from ui.panels.fluorescence_workspace import FluorescenceWorkspace

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def workspace(tmp_path):
    set_language('zh')
    data = np.zeros((2, 12, 14), dtype=np.uint16)
    data[0, 2:6, 3:8] = 100
    data[1, 7:10, 8:12] = 250
    path = tmp_path / 'channels.tif'
    tifffile.imwrite(path, data, metadata={'axes': 'CYX'})
    panel = FluorescencePanel(panel_id='preview-toolbar')
    panel.import_image(path)
    uid = panel.active_image()['uid']
    panel.update_channel(uid, 0, name='DAPI')
    panel.update_channel(uid, 1, name='GFP')
    ws = FluorescenceWorkspace()
    ws.set_panel(panel)
    ws.set_step(2)
    ws.resize(1100, 720)
    ws.show()
    APP.processEvents()
    yield ws
    ws.workflow_panel.close()
    ws.close()
    panel.dispose()
    APP.processEvents()
    set_language('en')


def test_shortcuts_switch_real_preview_without_changing_measurement(workspace):
    ws = workspace
    image = ws.panel.active_image()
    uid = image['uid']
    before_pixels = ws.panel.image_data(uid).copy()
    before_channels = deepcopy(image['channels'])
    before_analysis = deepcopy(ws.panel.analysis)
    settings = deepcopy(ws.roi_analysis_settings())
    toolbar = ws.preview_channels
    assert toolbar.buttons['merge'].text() == '合并图'
    assert [toolbar.buttons[i].text() for i in (0, 1)] == ['DAPI', 'GFP']
    toolbar.buttons[1].click()
    APP.processEvents()
    assert image['view_mode'] == 'single'
    assert ws.canvas.single_channel_index == 1
    assert len(ws.canvas._image_items) == 1
    assert toolbar.buttons[1].isChecked()
    assert ws.view_mode_combo.currentData() == 'single'
    assert ws.roi_analysis_settings() == settings
    assert image['channels'] == before_channels
    assert ws.panel.analysis == before_analysis
    assert np.array_equal(ws.panel.image_data(uid), before_pixels)
    toolbar.buttons['merge'].click()
    assert image['view_mode'] == 'merge'
    assert toolbar.buttons['merge'].isChecked()


def test_shortcuts_and_existing_view_selector_stay_in_sync(workspace):
    ws = workspace
    ws.preview_channels.buttons['split'].click()
    assert ws.view_mode_combo.currentData() == 'split'
    assert len(ws.canvas._image_items) == 3
    ws.view_mode_combo.setCurrentIndex(ws.view_mode_combo.findData('merge'))
    assert ws.preview_channels.buttons['merge'].isChecked()


def test_toolbar_refreshes_channel_names_on_image_change(workspace, tmp_path):
    ws = workspace
    path = tmp_path / 'other.tif'
    tifffile.imwrite(path, np.full((1, 12, 14), 500, np.uint16), metadata={'axes': 'CYX'})
    ws.panel.import_image(path)
    uid = ws.panel.active_image()['uid']
    ws.panel.update_channel(uid, 0, name='TRITC')
    ws.refresh()
    assert ws.preview_channels.buttons[0].text() == 'TRITC'
    assert 1 not in ws.preview_channels.buttons


def test_toolbar_language_switch_keeps_selected_preview(workspace):
    ws = workspace
    ws.preview_channels.buttons[1].click()
    set_language('en')
    ws.retranslate()
    assert ws.preview_channels.buttons['merge'].text() == 'Merge'
    assert ws.preview_channels.buttons[1].text() == 'GFP'
    assert ws.preview_channels.buttons[1].isChecked()
    assert ws.panel.active_image()['view_mode'] == 'single'


def test_preview_shortcuts_do_not_appear_in_layout(workspace):
    ws = workspace
    assert ws.preview_channels.isVisible()
    assert not ws.canvas.zoom_controls.object_controls.isVisible()
    ws.set_step(4)
    APP.processEvents()
    assert not ws.preview_channels.isVisible()
    assert ws.canvas.zoom_controls.object_controls.isVisible()
    ws.set_step(2)
    APP.processEvents()
    assert ws.preview_channels.isVisible()


def test_rgb_preview_does_not_bypass_explicit_channel_splitting(workspace, tmp_path):
    from PyQt6.QtGui import QImage
    ws = workspace
    pixels = np.zeros((12, 14, 3), np.uint8)
    for index, region in enumerate(((slice(2,5),slice(3,6)), (slice(6,9),slice(8,11)), (slice(0,3),slice(9,12)))):
        pixels[region[0], region[1], index] = 100
    path = tmp_path / 'rgb.png'
    assert QImage(pixels.data, 14, 12, pixels.strides[0], QImage.Format.Format_RGB888).copy().save(str(path))
    ws.panel.import_image(path)
    ws.refresh()
    image = ws.panel.active_image()
    assert image['channel_interpretation']['mode'] == 'rgb_composite'
    toolbar = ws.preview_channels
    assert toolbar.buttons['merge'].isEnabled()
    assert not toolbar.buttons['split'].isEnabled()
    assert not toolbar.buttons[0].isEnabled()
    toolbar.buttons['split'].click()
    assert image['view_mode'] == 'merge'


def test_overflow_channels_remain_accessible(workspace):
    from PyQt6.QtTest import QTest
    ws = workspace
    uid = ws.panel.active_image()['uid']
    ws.panel.update_channel(uid, 0, name='DAPI_' + 'long-marker-' * 30)
    ws.refresh()
    QTest.qWait(30)
    toolbar = ws.preview_channels
    bar = toolbar.scroll.horizontalScrollBar()
    assert bar.maximum() > 0, (toolbar.width(), toolbar.scroll.width(), toolbar.body.width(), toolbar.body.sizeHint().width())
    assert toolbar.following.isVisible()
    toolbar.following.click()
    assert bar.value() > 0


def test_changed_channel_names_keep_button_hit_rects_disjoint_after_header_reparent(tmp_path,desktop_screen):
    from pathlib import Path
    from PyQt6.QtTest import QTest
    from ui.main_window import MainWindow
    resources=Path(__file__).resolve().parents[1]/'resources';old_style=APP.styleSheet()
    APP.setStyleSheet((resources/'sciefig.qss').read_text(encoding='utf-8'))
    window=MainWindow(resources);window.resize(1280,800);window.show();window.set_module('fluoro')
    try:
        panel=window._active_fluoro_panel();path=tmp_path/'channels.tif'
        tifffile.imwrite(path,np.ones((3,40,50),np.uint16),metadata={'axes':'CYX'},photometric='minisblack')
        image=panel.import_image(path);ws=window.fluoro_workspace;ws.set_panel(panel);ws.set_step(2)
        QTest.qWait(30)
        for i,name in enumerate(('Alexa Fluor 555','Actin','DAPI')):panel.update_channel(image['uid'],i,name=name)
        ws.refresh();window.switch_language('en');QTest.qWait(80)
        toolbar=ws.preview_channels
        for i in range(3):
            button=toolbar.buttons[i]
            assert button.width()>=button.minimumSizeHint().width(),(i,button.geometry(),button.minimumSizeHint())
            if i:assert toolbar.buttons[i-1].geometry().right()<button.geometry().left()
        assert toolbar.body.width()>=toolbar.row.minimumSize().width()
    finally:
        window._capture_clean_project_baseline();window.close();APP.processEvents();APP.setStyleSheet(old_style)


def test_old_channel_buttons_hide_before_deferred_delete(workspace):
    toolbar=workspace.preview_channels;old=toolbar.buttons[0]
    image=deepcopy(workspace.panel.active_image());image['channels'][0]['name']='New DAPI name'
    toolbar.sync(image,0,2)
    # Immediate paint/grab may run before Qt processes DeferredDelete.
    assert old.isHidden()


@pytest.mark.parametrize('key, label', [
    ('canvas_force_marquee', '框选模式'), ('canvas_light_snap', '自动对齐'),
    ('canvas_contains_only', '完全框住才选'), ('zoom_fit', '全图'),
    ('workflow_advanced', '高级测量参数'), ('sample_spot_tool', '框选亮点作为计数样本'),
    ('workflow_presets_batch', '分析模式、预设与批量'),
    ('spot_method_traditional', '阈值计数'), ('strip_staging', '条带列表'),
    ('fluoro_display_compare', '查看未处理效果'),
    ('fluoro_display_reset', '重置显示调整'),
])
def test_chinese_actions_describe_the_operation(key, label):
    set_language('zh')
    try:
        assert tr(key) == label
    finally:
        set_language('en')
