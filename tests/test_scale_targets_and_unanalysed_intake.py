import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QImage, QColor
from core.layout_scale_bar import scale_bar_layout_object

_APP = QApplication.instance() or QApplication([])


def test_per_target_template_controls_length_and_style():
    obj = scale_bar_layout_object(
        panel_uuid='p', module_kind='fluorescence', target_id='channel:1',
        target_rect=dict(x=0, y=0, width=200, height=100),
        source_metadata=dict(width_px=200, height_px=100, pixel_size_um=1),
        template=dict(label_length_um=50, line_color='#FFFFFF'),
        state=dict(template=dict(label_length_um=20, line_color='#FF0000')),
    )
    assert obj.width == pytest.approx(20)
    assert obj.style.stroke_color == '#FF0000'


def test_fluoro_scale_target_selector_and_independent_templates():
    from core.models import FluorescencePanel
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace
    panel = FluorescencePanel(panel_id='targets')
    record = dict(uid='a', metadata=dict(width_px=200, height_px=100, pixel_size_um=1),
                  channels=[dict(index=0, name='DAPI'), dict(index=1, name='FITC')],
                  layout_mode='merge_only', layout_item_geometries={
                      key: dict(x=i*220, y=0, width=200, height=100)
                      for i, key in enumerate(['merge_split:merge', 'merge_split:channel:0', 'merge_split:channel:1'])},
                  layout_scale_bar_template=dict(label_length_um=50),
                  layout_scale_bars=[dict(target_id='merge_split:merge', enabled=True)])
    panel.data.update(active_image_uid='a', fluorescence_images=[record, dict(record, uid='b')])
    ws = FluorescenceWorkspace()
    ws.set_panel(panel, refresh=False)
    assert hasattr(ws, 'scale_target_combo')
    ws._sync_layout_scale_appearance_controls()
    combo = ws.scale_target_combo
    index = next((i for i in range(combo.count()) if combo.itemData(i) == ('a', 'merge_split:channel:1')), -1)
    assert index >= 0
    combo.setCurrentIndex(index)
    before = deepcopy(panel.images()[1])
    ws.scale_bar_length_spin.setValue(20)
    ws._apply_layout_scale_bar(current_only=True)
    states = {s['target_id']:s for s in panel.active_image()['layout_scale_bars']}
    assert states['merge_split:channel:1']['template']['label_length_um'] == 20
    assert states['merge_split:merge'].get('template', {'label_length_um':50})['label_length_um'] == 50
    assert panel.images()[1] == before
    assert panel.active_image()['layout_scale_bar_template']['label_length_um'] == 50
    ws.close()


def test_split_preview_draws_only_target_channel_scale(tmp_path):
    import numpy as np
    import tifffile
    from core.models import FluorescencePanel
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace
    path = tmp_path / 'channels.tif'
    tifffile.imwrite(path, np.ones((2, 30, 40), dtype=np.uint16), metadata={'axes':'CYX'}, photometric='minisblack')
    panel = FluorescencePanel(panel_id='split')
    panel.import_image(path)
    record = panel.active_image()
    record.update(view_mode='split', layout_mode='merge_split', layout_scale_bars=[
        dict(target_id='merge_split:channel:1', enabled=True,
             manual_um_per_px=1, template=dict(label_length_um=10, line_color='#FF0000'))])
    ws = FluorescenceWorkspace()
    ws.set_panel(panel)
    ws.set_step(1)
    bars = [i for i in ws.canvas.scene.items() if i.data(0) == 'fluoro_scale_bar']
    assert len(bars) == 2  # line and label
    assert {i.data(1) for i in bars} == {'merge_split:channel:1'}
    assert all(i.parentItem() is not None for i in bars)
    from ui.panels.fluorescence_image_export import render_fluorescence_export_payloads
    with_scale = render_fluorescence_export_payloads(panel, record, source='original', composition='channels')
    plain = dict(record, layout_scale_bars=[])
    without_scale = render_fluorescence_export_payloads(panel, plain, source='original', composition='channels')
    assert with_scale[0][1] == without_scale[0][1]
    assert with_scale[1][1] != without_scale[1][1]
    assert with_scale[1][1].size() == without_scale[1][1].size()
    ws.close()


def test_browser_scale_can_place_label_below_line():
    from PyQt6.QtCore import QRectF, QSize
    from core.image_browser import ScaleBarState, scale_bar_layout
    assert 'text_position' in ScaleBarState.__dataclass_fields__
    state = ScaleBarState(visible=True, um_per_px=1, text_position='below')
    geometry = scale_bar_layout(QRectF(0,0,400,300), QSize(400,300), state)
    assert geometry.text_rect.top() >= geometry.line_rect.bottom()


def test_browser_appearance_reuse_preserves_each_images_calibration(tmp_path):
    from test_browser_navigation_and_calibration import browser
    ws, paths = browser(tmp_path)
    first = ws.scale_bar_state_for(paths[0])
    second = ws.scale_bar_state_for(paths[1])
    second.um_per_px = 2.5
    first.length_um = 30
    first.text_position = 'below'
    ws._apply_browser_scale_to_all(True)
    assert second.um_per_px == 2.5
    assert second.length_um == 30
    assert second.text_position == 'below'
    saved = ws.serialize_browser_scale_states()
    ws.restore_browser_scale_states(saved)
    assert ws.scale_bar_state_for(paths[1]).text_position == 'below'
    ws.close()


def test_layout_roundtrip_does_not_spread_scale_style_between_channels():
    from core.models import FluorescencePanel
    from ui.layout_adapters.fluorescence_layout_adapter import fluorescence_layout_objects, apply_fluorescence_layout_objects
    panel = FluorescencePanel(panel_id='roundtrip')
    panel.data.update(active_image_uid='a', fluorescence_images=[dict(
        uid='a', metadata=dict(width_px=200, height_px=100, pixel_size_um=1),
        layout_mode='merge_split', layout_scale_bar_template=dict(label_length_um=50),
        layout_item_geometries={f'merge_split:channel:{i}':dict(x=i*210,y=0,width=200,height=100) for i in range(2)},
        layout_scale_bars=[dict(target_id=f'merge_split:channel:{i}', enabled=True,
                               template=dict(label_length_um=10+i*10)) for i in range(2)],
    )])
    objects = fluorescence_layout_objects(panel)
    bars = [o for o in objects if o.object_type == 'scale_bar']
    assert len(bars) == 2
    from dataclasses import replace
    objects[objects.index(bars[0])] = replace(bars[0], style=replace(bars[0].style, stroke_color='#FF0000'))
    apply_fluorescence_layout_objects(panel, objects)
    restored = [o for o in fluorescence_layout_objects(panel) if o.object_type == 'scale_bar']
    assert [o.width for o in restored] == [10, 20]
    assert restored[0].style.stroke_color == '#FF0000'
    assert restored[1].style.stroke_color == '#FFFFFF'


@pytest.mark.parametrize('module', ['ihc', 'brightfield'])
def test_unanalysed_image_can_enter_composer(module, tmp_path, monkeypatch):
    from ui.main_window import MainWindow
    image = QImage(48, 32, QImage.Format.Format_RGB32)
    image.fill(QColor('#b48a99'))
    path = tmp_path / 'source.png'
    assert image.save(str(path))
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    monkeypatch.setattr(window, '_generated_asset_dir', lambda: tmp_path / 'generated')
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args[2]))
    panel = getattr(window, f'_active_{module}_panel')()
    panel.import_image(path)
    source = panel.active_image()
    calibration = panel.update_measurement_scale_bar(source['uid'], length_um=10, um_per_px=1, calibration_source='manual')
    created = getattr(window, f'send_{module}_layout_to_composer')()
    assert created, messages
    assert not source.get('measurements')
    assert source['measurement_scale_bar'] == calibration
    window._capture_clean_project_baseline()
    window.close()
