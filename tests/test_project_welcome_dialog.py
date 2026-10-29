from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget

from core.utils.i18n import get_language, set_language
from ui.widgets import project_welcome_dialog as welcome


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_welcome_copy_close_and_project_link(qt_application, monkeypatch, language):
    previous = get_language()
    set_language(language)
    parent = QWidget()
    dialog = welcome.ProjectWelcomeDialog(parent, Path(__file__).resolve().parents[1]/'resources')
    urls = []
    monkeypatch.setattr(welcome.QDesktopServices, 'openUrl', lambda url: urls.append(url.toString()) or True)
    try:
        dialog.show()
        assert dialog.width() == 365
        assert dialog.description.text() == welcome.COPY[language][1]
        if language == 'zh':
            assert dialog.description.text() == '查看版本更新和使用说明，该软件仅限个人免费使用'
        QTest.mouseClick(dialog.github_button, Qt.MouseButton.LeftButton)
        assert urls == [welcome.PROJECT_URL]
        assert not dialog.isVisible()
        dialog.show()
        QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
        assert not dialog.isVisible()
        dialog.show()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible()
    finally:
        dialog.close()
        parent.close()
        set_language(previous)


def test_welcome_is_shown_on_each_launch_without_opening_browser(qt_application, monkeypatch):
    parent = QWidget()
    parent.show()
    urls = []
    monkeypatch.setattr(welcome.QDesktopServices, 'openUrl', lambda url: urls.append(url) or True)
    try:
        for _ in range(2):
            welcome.schedule_project_welcome(parent, Path(__file__).resolve().parents[1]/'resources')
            QTest.qWait(280)
            assert parent.project_welcome_dialog.isVisible()
            parent.project_welcome_dialog.reject()
        assert urls == []
    finally:
        parent.close()
