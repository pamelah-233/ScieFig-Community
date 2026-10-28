import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from core.utils.i18n import set_language


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_import_calibration_is_short_aligned_and_quiet_without_image(language):
    app = QApplication.instance() or QApplication([])
    set_language(language)
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    try:
        for module in ('fluoro', 'ihc', 'brightfield'):
            window.set_module(module)
            window.workflow_ui.scales.sync(module, 0)
            editor = window.workflow_ui.scales.editors[module]
            heading = window.workflow_ui.headings[module]
            assert editor.native_status.isHidden()
            assert editor.status.isHidden()
            assert len(editor.title()) < 26
            assert editor.parentWidget().layout().contentsMargins().left() == heading.layout().contentsMargins().left()
            assert not hasattr(window.workflow_ui.scales, 'hints')
            editor.set_scale({'um_per_px': .25, 'calibration_source': 'embedded'},
                             metadata={'pixel_size_x_um': .25, 'pixel_size_y_um': .25})
            assert len(editor.native_status.text()) < 85
            assert editor.apply_preset_button is not None and editor.save_button is not None
    finally:
        window._capture_clean_project_baseline()
        window.close()
        app.processEvents()
