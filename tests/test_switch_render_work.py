import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage


def test_reopened_brightfield_measurement_reuses_mask_and_invalidates_params(tmp_path, monkeypatch):
    from core.models import BrightfieldPanel
    import core.models.brightfield_panel as module
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'source.png'
    image = QImage(24, 24, QImage.Format.Format_RGB888)
    image.fill(0xffb090)
    image.save(str(path))
    panel = BrightfieldPanel(panel_id='switch')
    record = panel.import_image(path)
    measurement = {'measurement_uuid': 'saved', 'image_uid': record['uid'], 'params': {}, 'roi': {}}
    panel.analysis['brightfield_measurements'] = [measurement]
    calls = []
    def analyze(rgb, settings, **kwargs):
        calls.append(settings)
        return {'mask': np.ones(rgb.shape[:2], bool)}
    monkeypatch.setattr(module, 'analyze_brightfield_image', analyze)
    first = panel.render_measurement_overlay(measurement_uuid='saved')
    second = panel.render_measurement_overlay(measurement_uuid='saved')
    assert len(calls) == 1
    np.testing.assert_array_equal(first, second)
    measurement['params']['min_object_area'] = 20
    panel.render_measurement_overlay(measurement_uuid='saved')
    assert len(calls) == 2


def test_entering_qpcr_preview_does_not_render_hidden_layout(monkeypatch):
    from ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    calls = []
    monkeypatch.setattr(window.qpcr_layout_canvas, 'refresh', lambda: calls.append('layout'))
    try:
        window.set_module('viewer')
        window.set_module('qpcr')
        assert not calls
        window.show_qpcr_layout()
        assert calls == ['layout']
    finally:
        window._capture_clean_project_baseline()
        window.close()
        app.processEvents()


def test_reopened_ihc_overlay_does_not_recalculate_gmm_statistics(tmp_path, monkeypatch):
    from core.models import IHCPanel
    import core.models.ihc_panel as module
    app = QApplication.instance() or QApplication([])
    path = tmp_path/'ihc.png'
    image = QImage(16, 16, QImage.Format.Format_RGB888)
    image.fill(0x805030)
    image.save(str(path))
    panel = IHCPanel(panel_id='ihc-switch')
    record = panel.import_image(path)
    settings = module.default_analysis_settings()
    measurement = {'measurement_uuid': 'ihc-saved', 'image_uid': record['uid'], 'params': settings, 'roi': {}}
    panel.analysis['ihc_measurements'] = [measurement]
    _, rgb = panel._load_image_rgb(record['uid'])
    expected = panel._analyze_dab_threshold(rgb, settings)['mask']
    def forbidden(*args, **kwargs):
        raise AssertionError('Displaying a saved overlay must not refit GMM statistics')
    monkeypatch.setattr(module, 'classify_dab_gmm', forbidden)
    actual = panel.render_measurement_overlay(measurement_uuid='ihc-saved')
    np.testing.assert_array_equal(actual, module.render_mask_overlay(rgb, expected, alpha=.35))


def test_traditional_fiber_count_uses_background_runner(monkeypatch):
    from types import SimpleNamespace
    from ui.controllers.brightfield_ai_object_count_controller import BrightfieldAIObjectCountController
    calls = []
    panel = SimpleNamespace(active_image=lambda: {'uid': 'image'}, active_roi=lambda uid: {},
        he_fiber_area_calibration=lambda uid: .5)
    controller = BrightfieldAIObjectCountController(get_panel=lambda: panel,
        get_analysis_settings=lambda: {'he_fiber_segmentation_method': 'original'},
        show_ai_warning=lambda *args: None, refresh_after_ai_measurement=lambda result: None,
        run_async=lambda **kwargs: True)
    monkeypatch.setattr(controller, '_run_background', lambda *args: calls.append(args))
    controller.run_object_count()
    assert calls and calls[0][1] == 'image'


def test_composer_thumbnail_reuses_unchanged_data_but_updates_on_edit(monkeypatch):
    from core.models import QPCRPanel
    from ui.widgets.composer_canvas import ComposerCanvas
    app = QApplication.instance() or QApplication([])
    canvas = ComposerCanvas()
    panel = QPCRPanel(panel_id='thumbnail')
    calls = []
    def render(*args, **kwargs):
        calls.append(1)
        image = QImage(20, 20, QImage.Format.Format_RGB888)
        image.fill(0xffffff)
        return image
    monkeypatch.setattr(panel, 'render_preview', render)
    try:
        canvas.panel_thumbnail(panel)
        canvas.panel_thumbnail(panel)
        assert len(calls) == 1
        panel.style['preset'] = 'Science'
        canvas.panel_thumbnail(panel)
        assert len(calls) == 2
        panel.data['processed_data'] = {'edited': True}
        canvas.panel_thumbnail(panel)
        assert len(calls) == 3
    finally:
        canvas.close()


def test_qpcr_preview_reuses_current_figure_until_data_changes(monkeypatch):
    from ui.main_window import MainWindow
    import ui.main_window as module
    from matplotlib.figure import Figure
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    calls = []
    def build(*args, **kwargs):
        calls.append(1)
        return Figure()
    monkeypatch.setattr(module, 'build_qpcr_figure', build)
    panel = window._active_qpcr_panel()
    panel.data['processed_data'] = {'fold_change': [{'target': 'IL6', 'fold_change': 1}]}
    try:
        window._refresh_qpcr_ui(panel)
        window.set_module('viewer')
        window.set_module('qpcr')
        assert len(calls) == 1
        panel.data['processed_data']['fold_change'][0]['fold_change'] = 2
        window._refresh_qpcr_ui(panel)
        assert len(calls) == 2
    finally:
        window._capture_clean_project_baseline()
        window.close()
        app.processEvents()
