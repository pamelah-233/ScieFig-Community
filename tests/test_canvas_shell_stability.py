"""Canvas chrome must settle before the first visible module frame."""
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QFontDatabase, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.utils.i18n import get_language, set_language
from ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(desktop_screen):
    for font in ('arial.ttf', 'msyh.ttc'):
        QFontDatabase.addApplicationFont(f'C:/Windows/Fonts/{font}')
    resources = Path(__file__).resolve().parents[1] / 'resources'
    previous_style, previous_language = APP.styleSheet(), get_language()
    APP.setStyleSheet((resources / 'sciefig.qss').read_text(encoding='utf-8'))
    win = MainWindow(resources)
    win.project_recovery.timer.stop()
    win.project_recovery._alive = False
    win._confirm_destructive_action = lambda *args: True
    win.resize(1600, 1000)
    win.show()
    QTest.qWait(150)
    yield win
    win.close()
    APP.processEvents()
    APP.setStyleSheet(previous_style)
    set_language(previous_language)


class PaintFrames(QObject):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.viewport = canvas.scroll_area.viewport() if hasattr(canvas, 'scroll_area') else canvas.view.viewport()
        self.frames = []
        self.viewport.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint and self.canvas.isVisible():
            self.frames.append((self.viewport.width(), self.viewport.height(),
                                round(self.canvas._view_controller.zoom_factor, 5)))
        return False


@pytest.mark.parametrize('language', ['en', 'zh'])
def test_module_switch_first_painted_canvas_has_final_geometry(window, language):
    window.switch_language(language)
    QTest.qWait(100)
    canvases = dict(qpcr=window.preview_canvas, wb=window.wb_canvas,
                    fluoro=window.fluoro_workspace.canvas, ihc=window.ihc_workspace.canvas,
                    brightfield=window.brightfield_workspace.canvas,
                    viewer=window.image_browser_workspace.canvas, composer=window.composer_canvas)
    probes = {name: PaintFrames(canvas) for name, canvas in canvases.items()}
    for module in ['wb', 'fluoro', 'ihc', 'brightfield', 'viewer', 'composer', 'qpcr', 'wb', 'viewer']:
        probe = probes[module]
        probe.frames.clear()
        window.set_module(module)
        QTest.qWait(180)
        assert probe.frames, module
        assert len(set(probe.frames)) == 1, (module, probe.frames)


@pytest.mark.parametrize('module,step', [('ihc', 3), ('brightfield', 4)])
def test_loaded_histology_layout_first_paint_has_final_geometry(window, tmp_path, module, step):
    source = tmp_path / 'image.png'
    image = QImage(160, 120, QImage.Format.Format_RGB32)
    image.fill(0xffe0b0)
    assert image.save(str(source))
    panel = getattr(window, f'_active_{module}_panel')()
    panel.import_image(source)
    panel.data['active_step'] = step
    canvas = getattr(window, f'{module}_workspace').layout_canvas
    probe = PaintFrames(canvas)
    window.set_module(module)
    QTest.qWait(180)
    assert probe.frames
    assert len(set(probe.frames)) == 1, probe.frames


def test_long_analysis_status_does_not_resize_window_or_canvas_on_module_return(window):
    window._active_qpcr_panel().style['analysis_summary'] = 'Target analysis summary with several groups. ' * 8
    window.set_module('wb')
    QTest.qWait(100)
    width = window.width()
    probe = PaintFrames(window.preview_canvas)
    window.set_module('qpcr')
    QTest.qWait(180)
    assert window.width() == width
    assert len(set(probe.frames)) == 1, probe.frames


def test_browser_zoom_uses_global_toolbar_without_a_canvas_row(window):
    window.set_module('viewer')
    QTest.qWait(100)
    shell = window.v9_shell
    canvas = window.image_browser_workspace.canvas
    assert shell.global_toolbar.isAncestorOf(canvas.zoom_controls)
    assert canvas.zoom_controls.isVisible()
    assert canvas.layout().indexOf(canvas.zoom_controls) == -1
    canvas.zoom_controls.zoom_in_button.click()
    assert not canvas._view_controller.fit_mode
    zoom = canvas._view_controller.zoom_factor
    window.set_module('wb')
    QTest.qWait(100)
    window.set_module('viewer')
    QTest.qWait(100)
    assert canvas._view_controller.zoom_factor == zoom
    canvas.zoom_controls.fit_button.click()
    assert canvas._view_controller.fit_mode


def test_every_module_sidebar_can_be_dragged_to_240_pixels(window):
    shell = window.v9_shell
    for module in ['qpcr', 'wb', 'fluoro', 'ihc', 'brightfield', 'viewer', 'composer']:
        window.set_module(module)
        QTest.qWait(100)
        shell.body_splitter.moveSplitter(shell.body_splitter.width() - 240 - shell.body_splitter.handleWidth(), 2)
        APP.processEvents()
        assert 236 <= shell.right_panel_width() <= 244, (module, shell.right_panel_width())
        assert abs(shell._saved_right_width - shell.right_panel_width()) <= 2
    shell.collapse_right_panel()
    shell.restore_right_panel()
    APP.processEvents()
    assert 236 <= shell.right_panel_width() <= 244
