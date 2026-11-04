from pathlib import Path

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.widgets.app_shell import DEFAULT_RIGHT_WIDTH, MIN_RIGHT_WIDTH

_APP = QApplication.instance() or QApplication([])


def test_workflows_start_narrow_and_manual_resize_survives_module_switches(desktop_screen):
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    window._confirm_destructive_action = lambda *args: True
    window.resize(1600, 1000); window.show(); _APP.processEvents()
    shell = window.v9_shell
    for module in ['qpcr', 'wb', 'fluoro', 'ihc', 'brightfield', 'viewer']:
        window.set_module(module); _APP.processEvents()
        assert abs(shell.right_panel_width() - DEFAULT_RIGHT_WIDTH) <= 2, module
    # Exercise the same splitterMoved path as a user dragging the separator.
    total = sum(shell.body_splitter.sizes())
    shell.body_splitter.moveSplitter(total - 520, 2)
    _APP.processEvents()
    width = shell.right_panel_width()
    assert width > MIN_RIGHT_WIDTH + 100
    for module in ['qpcr', 'wb', 'composer', 'brightfield', 'qpcr']:
        window.set_module(module); _APP.processEvents()
        assert abs(shell._saved_right_width - width) <= 2, module
        assert MIN_RIGHT_WIDTH <= shell.right_panel_width() <= width + 2, module
    assert abs(shell.right_panel_width() - width) <= 2
    window.close(); _APP.processEvents()


def test_fluoro_header_remains_readable_when_window_narrows(tmp_path, desktop_screen, monkeypatch):
    import numpy as np
    import tifffile
    from PyQt6.QtCore import QPoint, QRect
    from main import load_qss
    from ui.controllers.model_device_controller import ModelDeviceController
    monkeypatch.setattr(ModelDeviceController, 'check_current_device', lambda self: None)
    monkeypatch.setattr(MainWindow, '_project_fingerprint', lambda self: 'layout-test')
    old_style = _APP.styleSheet()
    resources = Path(__file__).resolve().parents[1] / 'resources'
    _APP.setStyleSheet(load_qss(resources))
    window = MainWindow(resources_dir=resources)
    window._confirm_destructive_action = lambda *args: True
    try:
        path = tmp_path / 'channels.tif'
        tifffile.imwrite(path, np.ones((4, 40, 50), np.uint16), metadata={'axes': 'CYX'}, photometric='minisblack')
        window._active_fluoro_panel().import_image(path)
        window.set_module('fluoro'); window.fluoro_workspace.refresh()
        window.fluoro_workspace.set_step(2); window.show()
        for language in ('en', 'zh'):
            window.switch_language(language)
            for width in (1640, 1100, 1640):
                window.resize(width, 850)
                for _ in range(12): _APP.processEvents()
                assert window.width() == width
                shell = window.v9_shell; controls = window.fluoro_workspace.canvas.zoom_controls
                navigation = shell._workflow_navigation
                nav_rect = QRect(navigation.mapTo(shell.stagebar, QPoint()), navigation.size())
                control_rect = QRect(controls.mapTo(shell.stagebar, QPoint()), controls.size())
                assert shell.stagebar.rect().contains(nav_rect)
                assert shell.stagebar.rect().contains(control_rect)
                assert not nav_rect.intersects(control_rect)
                assert controls.width() >= controls.minimumSizeHint().width()
    finally:
        window.close(); _APP.processEvents(); _APP.setStyleSheet(old_style)
