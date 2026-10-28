from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.measurement_scale import line_length_px
from test_image_browser_workspace import FakeBrowserCore, FakeBrowserImage, _rgb, _workspace

_APP=QApplication.instance() or QApplication([])


class ImmediatePool:
    def start(self,worker,*args):worker.run()


def browser(tmp_path):
    paths=[tmp_path/f'{i}.png' for i in range(3)]
    core=FakeBrowserCore({p:FakeBrowserImage(p,_rgb(240,160)) for p in paths})
    workspace=_workspace(core,thread_pool=ImmediatePool())
    workspace.open_folder(tmp_path)
    workspace.thumbnail_list.setCurrentRow(0)
    workspace.canvas.resize(760,560);workspace.canvas.show();QTest.qWait(80)
    return workspace,paths


def test_canvas_arrows_follow_thumbnail_order_and_stop_at_ends(tmp_path):
    w,paths=browser(tmp_path)
    assert not w.canvas.previous_button.isEnabled()
    assert w.canvas.next_button.isEnabled()
    w.canvas.next_button.click()
    assert w._cached_path==paths[1]
    assert w.thumbnail_list.currentRow()==1
    w.canvas.next_button.click()
    assert w._cached_path==paths[2]
    assert not w.canvas.next_button.isEnabled()
    w.canvas.previous_button.click()
    assert w._cached_path==paths[1]
    assert w.canvas.previous_button.parentWidget() is w.canvas.scroll_area.viewport()
    w.canvas.close();w.close();_APP.processEvents()


def test_arrows_handle_programmatic_selection_and_hide_for_empty_folder(tmp_path):
    w,paths=browser(tmp_path)
    w.select_image(paths[1])
    w.canvas.next_button.click()
    assert w._cached_path==paths[2]
    w.open_folder(tmp_path/'empty')
    assert w.canvas.previous_button.isHidden()
    assert w.canvas.next_button.isHidden()
    assert not w.draw_scale_line_button.isEnabled()
    w.canvas.close();w.close();_APP.processEvents()


def test_browser_draws_shared_calibration_and_keeps_it_when_editing_or_switching(tmp_path):
    w,paths=browser(tmp_path)
    w.length_spin.setValue(20)
    w.draw_scale_line_button.click()
    label=w.canvas.image_label
    start=label._map_source_point_to_label(20,100)
    end=label._map_source_point_to_label(120,125)
    QTest.mousePress(label,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(label,end)
    assert label._draft_source_line['y1']==label._draft_source_line['y2']
    QTest.mouseRelease(label,Qt.MouseButton.LeftButton,pos=end)
    _APP.processEvents()
    cal=w._cache_resolved_calibration(w._cached_image)
    assert cal['calibration_source']=='manual_line'
    assert cal['um_per_px']==pytest.approx(20/line_length_px(cal['line_px']))
    assert w.canvas.scale_bar_state.visible
    assert not label.line_selection_enabled
    assert not w.draw_scale_line_button.isChecked()
    original=cal['um_per_px']
    w.font_size_spin.setValue(22)
    assert w._cache_resolved_calibration(w._cached_image)['um_per_px']==original
    w.canvas.next_button.click();w.canvas.previous_button.click()
    assert w._cached_path==paths[0]
    assert w.canvas.scale_bar_state.um_per_px==original
    w.length_spin.setValue(40)
    assert w.canvas.scale_bar_state.um_per_px==pytest.approx(original*2)
    w.canvas.close();w.close();_APP.processEvents()


def test_browser_named_scale_preset_restores_appearance(tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QInputDialog
    from core.utils.calibration_presets import CalibrationPresets
    w,paths=browser(tmp_path)
    editor=w.calibration_controls
    editor.library=CalibrationPresets(QSettings(str(tmp_path/'browser.ini'),QSettings.Format.IniFormat))
    w._apply_calibration_preset(dict(um_per_px=.5,um_per_px_x=.5,um_per_px_y=.5,calibration_source='manual'))
    editor.set_scale(w._cache_resolved_calibration(w._cached_image))
    w.length_spin.setValue(25);w.font_size_spin.setValue(24);w.set_line_color('#FFD13B')
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:('20× 标尺',True))
    editor.save_button.click()
    w.font_size_spin.setValue(12);w.set_line_color('#FFFFFF');w.length_spin.setValue(10)
    editor.preset_combo.setCurrentIndex(1);editor.apply_preset_button.click()
    state=w.scale_bar_state_for(paths[0])
    assert state.length_um==25 and state.font_size_px==24 and state.line_color=='#FFD13B'
    assert state.um_per_px==.5
    assert '20× 标尺' in editor.native_status.text()
    assert '未读取' not in editor.native_status.text()
    w.canvas.next_button.click();w.canvas.previous_button.click()
    assert '20× 标尺' in editor.native_status.text()
    w.canvas.close();w.close();_APP.processEvents()


def test_browser_drawing_can_cancel_and_never_reuses_line_on_another_image(tmp_path):
    w,paths=browser(tmp_path)
    w.draw_scale_line_button.click()
    QTest.keyClick(w.canvas,Qt.Key.Key_Escape)
    assert not w.canvas.image_label.line_selection_enabled
    assert not w.draw_scale_line_button.isChecked()
    w.draw_scale_line_button.click()
    w.thumbnail_list.setCurrentRow(1)
    assert not w.canvas.image_label.line_selection_enabled
    assert w._manual_calibrations.get(str(paths[1])) is None
    w.canvas.close();w.close();_APP.processEvents()


def test_drawn_calibration_roundtrips_and_navigation_never_enters_export(tmp_path):
    import json
    from PyQt6.QtGui import QImage
    from core.image_browser import render_snapshot
    w,paths=browser(tmp_path)
    w.length_spin.setValue(25)
    w.draw_scale_line_button.click()
    w.canvas.line_selected.emit({'x1':20.,'y1':90.,'x2':120.,'y2':90.})
    w.set_line_color('#12AB34')
    saved=json.loads(json.dumps(w.serialize_browser_scale_states()))
    w.restore_browser_scale_states(saved)
    w.select_image(paths[0])
    cal=w._cache_resolved_calibration(w._cached_image)
    assert cal['calibration_source']=='manual_line' and cal['um_per_px']==.25
    assert w.canvas.scale_bar_state.line_color=='#12AB34'
    w._snapshot=render_snapshot
    output=tmp_path/'export.png'
    assert w.export_snapshot(output)
    image=QImage(str(output))
    assert image.size()==w.canvas.image_label._source_size
    assert image.pixelColor(5,80).getRgb()[:3]==(12,24,36)
    w.canvas.close();w.close();_APP.processEvents()
