"""Studio transitions keep anchors, state and interruption behavior stable."""
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from main import load_qss
from ui.main_window import MainWindow
from ui.widgets.workflow_presentation import WorkflowDisclosure
from ui.widgets import studio_motion

pytestmark = pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='shared studio studio presentation')
APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(desktop_screen, monkeypatch):
    monkeypatch.setattr(studio_motion, 'motion_enabled', lambda: True)
    previous = APP.styleSheet()
    APP.setStyleSheet(load_qss(Path(__file__).resolve().parents[1] / 'resources'))
    w = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    w.project_recovery.timer.stop()
    w.project_recovery._alive = False
    w._confirm_destructive_action = lambda *args: True
    w.resize(1366, 820)
    w.show()
    # Let native window exposure and the initial splitter timer finish before
    # measuring a user-triggered transition.
    QTest.qWait(150 if APP.platformName() == 'cocoa' else 20)
    yield w
    w.close()
    APP.processEvents()
    APP.setStyleSheet(previous)


def test_sidebar_switches_share_fixed_anchors_and_reverse_icons(window):
    shell = window.v9_shell
    size = window.size()
    for hide, show, collapse, restore, panel in (
        (shell.rail_hide_button, shell.rail_reopen_button, shell.collapse_rail, shell.restore_rail, shell.rail_panel),
        (shell.right_hide_button, shell.right_show_button, shell.collapse_right_panel, shell.restore_right_panel, shell.right_panel),
    ):
        point = hide.mapTo(window, QPoint())
        width = panel.width()
        assert hide.icon().cacheKey() != show.icon().cacheKey()
        collapse()
        QTest.qWait(55)
        assert 0 < panel.width() < width
        QTest.qWait(150)
        assert panel.width() == 0
        assert show.isVisible() and show.mapTo(window, QPoint()) == point
        restore()
        QTest.qWait(55)
        assert 0 < panel.width() < width
        collapse()  # Reverse an unfinished transition without losing the saved width.
        QTest.qWait(190)
        restore()
        QTest.qWait(190)
        assert abs(panel.width() - width) <= 2
        assert hide.isVisible() and hide.mapTo(window, QPoint()) == point
        assert window.size() == size


def test_disclosure_interruption_restores_height_and_arrow(monkeypatch):
    monkeypatch.setattr(studio_motion, 'motion_enabled', lambda: True)
    content = QWidget()
    layout = QVBoxLayout(content)
    for name in ['one', 'two', 'three', 'four']:
        layout.addWidget(QLabel(name))
    limits = (content.minimumHeight(), content.maximumHeight())
    section = WorkflowDisclosure('workflow_advanced', content)
    section.resize(300, 200)
    section.show()
    section.toggle.click()
    QTest.qWait(45)
    assert section.expanded and content.isVisible()
    assert section._fade_effect is not None
    assert 0.2 < section._fade_effect.opacity() < 1
    assert 0 < section.toggle.reveal < 1
    assert (content.minimumHeight(), content.maximumHeight()) == limits
    section.toggle.click()
    QTest.qWait(35)
    assert 0 < section.toggle.reveal < 1
    section.toggle.click()
    QTest.qWait(210)
    assert section.expanded and content.isVisible()
    assert (content.minimumHeight(), content.maximumHeight()) == limits
    assert section.toggle.arrowType() == Qt.ArrowType.DownArrow
    assert section.toggle.reveal == 1
    previous_height = content.height()
    for _ in range(12):
        layout.addWidget(QLabel('new result'))
    QTest.qWait(30)
    assert content.height() > previous_height
    assert section.height() >= content.height() + section.toggle.height()
    section.toggle.click()
    QTest.qWait(45)
    assert 0 < section.toggle.reveal < 1
    QTest.qWait(170)
    assert not content.isVisible()
    assert section.toggle.arrowType() == Qt.ArrowType.RightArrow
    assert section.toggle.reveal == 0
    section.close()


@pytest.mark.parametrize('attribute', ['roi_advanced_section', 'full_results_section', 'secondary_tools_section'])
@pytest.mark.parametrize('at_bottom', [False, True])
def test_disclosure_does_not_reflow_during_fade(window, attribute, at_bottom):
    window.set_module('fluoro')
    workspace = window.fluoro_workspace
    workspace.set_step(2)
    window.measurement_tasks['fluoro'].select('roi_intensity')
    QTest.qWait(30)
    section = getattr(workspace, attribute)
    scroll = section.parentWidget()
    while scroll is not None and not hasattr(scroll, 'verticalScrollBar'):
        scroll = scroll.parentWidget()
    assert scroll is not None
    scroll.verticalScrollBar().setValue(0)
    section.setExpanded(False)
    QTest.qWait(20)
    size = window.size()
    for expand in (True, False, True):
        if at_bottom:
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        section.toggle.click()
        QTest.qWait(10)
        anchor = section.toggle.mapTo(scroll.viewport(), QPoint())
        positions = []
        sizes = []
        anchors = []
        for _ in range(20):
            QTest.qWait(10)
            positions.append(section.height())
            if section._motion.state() == section._motion.State.Running:
                sizes.append(section.content.size())
            anchors.append(section.toggle.mapTo(scroll.viewport(), QPoint()))
            assert window.size() == size
        assert all(point == anchor for point in anchors)
        assert all(height == positions[0] for height in positions)
        assert all(size == sizes[0] for size in sizes)
        assert section.content.isVisible() == expand
        assert not section.toggle.toolTip()


def test_reduced_motion_switches_without_animation(window, monkeypatch):
    monkeypatch.setattr(studio_motion, 'motion_enabled', lambda: False)
    shell = window.v9_shell
    shell.collapse_right_panel()
    assert shell.right_panel.width() == 0
    shell.restore_right_panel()
    assert shell.right_panel.width() >= 240


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows animation preference')
@pytest.mark.parametrize('enabled', [False, True])
def test_windows_motion_respects_native_accessibility_setting(monkeypatch, enabled):
    import ctypes
    from types import SimpleNamespace
    monkeypatch.setattr(studio_motion, 'QApplication', SimpleNamespace(
        instance=lambda: SimpleNamespace(platformName=lambda: 'windows')))
    def query(action, size, pointer, flags):
        assert action == 0x1042 and size == flags == 0
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int)).contents.value = int(enabled)
        return 1
    monkeypatch.setattr(ctypes.windll.user32, 'SystemParametersInfoW', query)
    assert studio_motion.motion_enabled() is enabled
