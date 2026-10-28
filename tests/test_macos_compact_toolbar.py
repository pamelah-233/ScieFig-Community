import sys
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QWidget

from core.utils import i18n
from main import load_qss
from ui.widgets.app_shell import V9AppShell


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='shared studio toolbar presentation')
def test_narrow_toolbar_preserves_icons_actions_and_translated_names(qt_application, monkeypatch):
    previous = qt_application.styleSheet()
    qt_application.setStyleSheet(load_qss(Path(__file__).resolve().parents[1] / 'resources'))
    calls = []
    shell = V9AppShell(QWidget(), QWidget(), QWidget(),
                       {name: lambda n=name: calls.append(n) for name in ('new', 'open', 'save', 'save_as', 'export')}, ['Nature'])
    shell.show()
    try:
        for language in ('en', 'zh'):
            monkeypatch.setattr(i18n, 'LANGUAGE', language)
            shell.retranslate()
            for width, compact in ((1600, False), (890, True), (800, True), (1600, False)):
                shell.resize(width, 720)
                qt_application.processEvents()
                assert shell.width() == width
                assert shell._toolbar_compact == compact
                buttons = list(shell._action_buttons.values())
                for button in buttons:
                    label = i18n.tr(button.property('i18nKey'))
                    assert button.text() == ('' if compact else label)
                    assert button.toolTip() == button.accessibleName() == label
                    assert not button.icon().isNull() and button.isVisible()
                    assert button.geometry().right() < shell.global_toolbar.width()
                    if compact:
                        assert button.width() == 34
                        button.click()
                assert all(a.geometry().right() < b.geometry().left() for a, b in zip(buttons[:3], buttons[1:4]))
            shell.resize(890, 720)
            qt_application.processEvents()
            shell.retranslate()
            qt_application.processEvents()
            assert all(not b.text() for b in shell._action_buttons.values())
        assert set(calls) == {'new', 'open', 'save', 'save_as', 'export'}
    finally:
        shell.close()
        qt_application.setStyleSheet(previous)


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='shared studio toolbar presentation')
def test_main_window_language_and_module_changes_keep_compact_buttons(qt_application, desktop_screen):
    from ui.main_window import MainWindow
    previous = qt_application.styleSheet()
    qt_application.setStyleSheet(load_qss(Path(__file__).resolve().parents[1] / 'resources'))
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    window.project_recovery.timer.stop()
    window.project_recovery._alive = False
    window._confirm_destructive_action = lambda *args: True
    window.show()
    try:
        window.resize(890, 720)
        qt_application.processEvents()
        for language in ('zh', 'en'):
            window.switch_language(language)
            for module in ('wb', 'viewer', 'fluoro', 'stylelab', 'viewer', 'qpcr'):
                window.set_module(module)
                window.resize(890, 720)
                qt_application.processEvents()
                for button in window.v9_shell._action_buttons.values():
                    assert button.width() == 34
                    assert not button.text() and button.toolTip()
                if module=='viewer':
                    from PyQt6.QtCore import QPoint
                    controls=window.image_browser_workspace.canvas.zoom_controls
                    toolbar=window.v9_shell.global_toolbar
                    assert not controls.fit_button.text()
                    previous_right=-1
                    for child in (window.v9_shell.cellpose_device_combo,controls,window.v9_shell.preset_label):
                        left=child.mapTo(toolbar,QPoint()).x()
                        assert left>previous_right
                        previous_right=left+child.width()-1
                        assert previous_right<toolbar.width()
                    for button in (controls.fit_button,controls.zoom_out_button,controls.zoom_in_button):
                        rect=button.rect();rect.moveTopLeft(button.mapTo(controls,QPoint()))
                        assert controls.rect().contains(rect)
                        assert button.width()>=button.minimumSizeHint().width()
    finally:
        window.close()
        qt_application.setStyleSheet(previous)
