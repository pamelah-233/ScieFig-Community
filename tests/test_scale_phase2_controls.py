import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
import pytest


@pytest.fixture(scope='module', autouse=True)
def application():
    app = QApplication.instance() or QApplication([])
    yield app


def test_shared_editor_preserves_axes_and_only_applies_on_request(tmp_path):
    from core.utils.calibration_presets import CalibrationPresets
    from ui.widgets.calibration_controls import CalibrationControls
    app = QApplication.instance() or QApplication([])
    library = CalibrationPresets(QSettings(str(tmp_path / 'p.ini'), QSettings.Format.IniFormat))
    editor = CalibrationControls(library=library)
    other = CalibrationControls(library=library)
    applied = []
    editor.calibration_applied.connect(applied.append)
    editor.set_scale({'um_per_px_x': .2, 'um_per_px_y': .4, 'calibration_source': 'manual', 'length_um': 100})
    record = library.save('Scope', editor.current_calibration())
    assert other.preset_combo.findData(record['id']) >= 0
    editor.preset_combo.setCurrentIndex(editor.preset_combo.findData(record['id']))
    assert applied == []
    editor.apply_preset()
    assert applied[-1]['um_per_px_x'] == .2
    assert applied[-1]['um_per_px_y'] == .4
    assert applied[-1]['preset_name'] == 'Scope'
    editor.resize(320, editor.sizeHint().height())
    editor.show()
    app.processEvents()
    assert editor.minimumSizeHint().width() <= 320
    editor.close()
    other.close()


def test_shared_editor_retains_reference_calibration_when_unchanged():
    from ui.widgets.calibration_controls import CalibrationControls
    app = QApplication.instance() or QApplication([])
    editor = CalibrationControls()
    scale = dict(um_per_px=.5, um_per_px_x=.5, um_per_px_y=.5, calibration_source='manual_line',
                 length_um=100, line_px=dict(x1=0,y1=0,x2=200,y2=0))
    editor.set_scale(scale)
    updates = editor.updates_for(scale)
    assert updates['calibration_source'] == 'manual_line'
    editor.y_spin.setValue(.75)
    updates = editor.updates_for(scale)
    assert updates['um_per_px_y'] == .75
    assert updates['calibration_source'] == 'manual'
    editor.close()


def test_modules_expose_same_editor_and_apply_to_active_image(tmp_path):
    from ui.main_window import MainWindow
    from ui.widgets.calibration_controls import CalibrationControls
    from PIL import Image
    app = QApplication.instance() or QApplication([])
    from pathlib import Path
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    try:
        assert isinstance(window.data_panel.brightfield_calibration_controls, CalibrationControls)
        assert isinstance(window.fluoro_workspace.calibration_controls, CalibrationControls)
        assert isinstance(window.ihc_workspace.calibration_controls, CalibrationControls)
        assert isinstance(window.image_browser_workspace.calibration_controls, CalibrationControls)
        for workspace in (window.brightfield_workspace, window.fluoro_workspace, window.ihc_workspace):
            group = workspace.scale_appearance_group
            assert group.isAncestorOf(workspace.scale_bar_apply_current_button)
            assert group.isAncestorOf(workspace.scale_bar_font_combo)
        path = tmp_path / 'cell.png'
        Image.new('RGB', (100, 100)).save(path)
        panel = window._active_brightfield_panel()
        uid = panel.import_image(str(path), stain_type='H&E')['uid']
        editor = window.data_panel.brightfield_calibration_controls
        editor.calibration_applied.emit(dict(um_per_px_x=.3, um_per_px_y=.6, calibration_source='manual', preset_name='Scope'))
        assert panel.measurement_scale_bar(uid)['um_per_px_y'] == .6
    finally:
        window.close()


def test_browser_preset_axes_survive_transfer_and_project_state(tmp_path):
    from pathlib import Path
    from tests.test_scale_phase1_browser import FakeBrowserImage, FakeBrowserCore, _workspace, _rgb
    path = tmp_path / 'image.png'
    image = FakeBrowserImage(path, _rgb(100, 100), {})
    workspace = _workspace(FakeBrowserCore({path: image}))
    workspace._cached_image = image
    workspace._cached_path = path
    workspace._apply_image(image)
    payload = dict(um_per_px=.3, um_per_px_x=.3, um_per_px_y=.6, calibration_source='manual',
                   preset_id='test', preset_name='Scope', length_um=50)
    workspace.calibration_controls.set_scale(payload)
    workspace._apply_calibration_preset(payload)
    workspace.bold_checkbox.setChecked(True)
    transfer = workspace._browser_transfer_payload(path)
    assert transfer['calibration']['um_per_px_y'] == .6
    saved = workspace.serialize_browser_scale_states()
    other = _workspace(FakeBrowserCore({path: image}))
    other.restore_browser_scale_states(saved)
    other._cached_image = image
    other._cached_path = path
    other._apply_image(image)
    assert other._cache_resolved_calibration(image)['um_per_px_y'] == .6
    assert other._browser_transfer_payload(path)['calibration']['preset_name'] == 'Scope'
    workspace.close()
    other.close()


def test_embedded_axes_can_be_saved_without_losing_precision(tmp_path):
    from ui.widgets.calibration_controls import CalibrationControls
    from core.utils.calibration_presets import CalibrationPresets
    library = CalibrationPresets(QSettings(str(tmp_path / 'embedded.ini'), QSettings.Format.IniFormat))
    editor = CalibrationControls(library=library)
    editor.set_scale(dict(um_per_px=.311962890625, um_per_px_x=.311962890625,
                         um_per_px_y=.42, calibration_source='embedded', length_um=100))
    record = library.save('CZI microscope', editor.current_calibration())
    assert record['um_per_px_x'] == .311962890625
    assert record['um_per_px_y'] == .42
    editor.close()


def test_editing_one_embedded_axis_preserves_the_other():
    from ui.widgets.calibration_controls import CalibrationControls
    editor = CalibrationControls()
    scale = dict(um_per_px_x=.3, um_per_px_y=.6, calibration_source='embedded')
    editor.set_scale(scale)
    editor.y_spin.setValue(.7)
    updates = editor.updates_for(scale)
    assert updates['um_per_px_x'] == .3
    assert updates['um_per_px_y'] == .7
    editor.close()


def test_named_preset_saves_and_restores_scale_appearance_without_geometry(tmp_path, monkeypatch):
    from ui.widgets.calibration_controls import CalibrationControls
    from core.utils.calibration_presets import CalibrationPresets
    from PyQt6.QtWidgets import QInputDialog
    library = CalibrationPresets(QSettings(str(tmp_path / 'style.ini'), QSettings.Format.IniFormat))
    editor = CalibrationControls(library=library)
    editor.set_scale(dict(um_per_px=.5, calibration_source='manual', length_um=50))
    editor.scale_style_provider = lambda: dict(label_length_um=25, line_color='#FFD13B',
        font_family='Arial', font_size=14, text_position='below')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('显微镜20× · 黄色标尺', True))
    editor.save_button.click()
    record = library.items()[0]
    assert record['scale_bar_template']['label_length_um'] == 25
    assert record['scale_bar_template']['line_color'] == '#FFD13B'
    assert 'line_px' not in record
    styles = []
    editor.scale_style_applied.connect(styles.append)
    editor.apply_preset_button.click()
    assert styles[-1]['text_position'] == 'below'
    assert styles[-1]['font_size'] == 14
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('重命名预设', True))
    editor.rename_button.click()
    assert library.items()[0]['name'] == '重命名预设'
    assert library.items()[0]['scale_bar_template'] == record['scale_bar_template']
    editor.close()


def test_native_calibration_hint_distinguishes_metadata_from_manual_calibration():
    from ui.widgets.calibration_controls import CalibrationControls
    from core.utils.i18n import set_language
    set_language('zh')
    editor = CalibrationControls()
    manual = dict(um_per_px=.4, calibration_source='manual', length_um=50)
    editor.set_scale(manual, metadata={'pixel_size_x_um':.25, 'pixel_size_y_um':.5})
    assert '手动标定' in editor.native_status.text()
    assert '0.4' in editor.native_status.text()
    editor.set_scale(manual, metadata={})
    assert '手动标定' in editor.native_status.text()
    editor.set_scale({}, metadata={})
    assert '无原生标定' in editor.native_status.text()
    assert '参考线' in editor.native_status.text()
    editor.close()


def test_applied_preset_replaces_missing_native_calibration_warning(tmp_path):
    from ui.widgets.calibration_controls import CalibrationControls
    from core.utils.calibration_presets import CalibrationPresets
    from core.utils.i18n import set_language
    set_language('zh')
    library = CalibrationPresets(QSettings(str(tmp_path/'preset-status.ini'), QSettings.Format.IniFormat))
    record = library.save('20×物镜', dict(um_per_px_x=.3, um_per_px_y=.3, calibration_source='manual'))
    editor = CalibrationControls(library=library)
    editor.set_scale({}, metadata={})
    editor.preset_combo.setCurrentIndex(editor.preset_combo.findData(record['id']))
    editor.apply_preset()
    assert '20×物镜' in editor.native_status.text()
    assert '使用预设' in editor.native_status.text()
    assert '无原生' not in editor.native_status.text()
    assert '0.3' in editor.native_status.text()
    editor.close()


def test_fluorescence_preset_does_not_rewrite_measurement_or_other_image(tmp_path):
    from copy import deepcopy
    from tests.test_scale_phase1_preservation import _write_ai_tiff
    from core.models import FluorescencePanel
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace
    panel = FluorescencePanel(panel_id='Phase2')
    first = panel.import_image(_write_ai_tiff(tmp_path / 'first.tif'))
    panel.update_measurement_scale_bar(first['uid'], um_per_px=.5)
    result = panel.create_roi_intensity_measurement(first['uid'], 'rectangle', {'x': 1, 'y': 1, 'w': 10, 'h': 10}, [0])
    second = panel.import_image(_write_ai_tiff(tmp_path / 'second.tif'))
    original = deepcopy(panel.analysis['measurements'])
    first_scale = deepcopy(panel.measurement_scale_bar(first['uid']))
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.calibration_controls.calibration_applied.emit(dict(um_per_px_x=.3, um_per_px_y=.6, calibration_source='manual', preset_name='Scope'))
    assert panel.measurement_scale_bar(second['uid'])['um_per_px_y'] == .6
    assert panel.measurement_scale_bar(first['uid']) == first_scale
    assert panel.analysis['measurements'] == original
    workspace.close()
