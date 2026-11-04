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
