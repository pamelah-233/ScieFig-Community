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


def test_update_requires_a_newer_stable_release_with_a_platform_installer():
    def release(version, *names, prefix='community', draft=False, prerelease=False):
        return {
            'tag_name': f'{prefix}-v{version}', 'draft': draft, 'prerelease': prerelease,
            'assets': [{'name': f'ScieFig_{version}_{name}', 'state': 'uploaded'} for name in names],
        }

    releases = [
        release('1.2.20', 'macOS_arm64.dmg', prerelease=True),
        release('1.2.19', 'Windows_Setup.exe', draft=True),
        release('1.2.18'),
        release('1.2.9', 'Windows_Setup.exe'),
        release('1.2.10', 'macOS_arm64.dmg'),
        release('1.2.10', 'macOS_arm64.dmg', prefix='macos'),
        release('1.2.17', 'macOS_arm64.dmg', prefix='windows'),
        release('1.2.11', 'Windows_CPU_Setup.exe'),
        release('1.2.3', 'macOS_arm64.dmg', 'Windows_CPU_Setup.exe'),
    ]
    for platform, version, prefix in (('darwin', '1.2.10', 'macos'), ('win32', '1.2.11', 'community')):
        assert welcome.newer_platform_release(releases, '1.2.4', platform) == (
            version, f'{welcome.PROJECT_URL}/releases/tag/{prefix}-v{version}',
        )
        assert welcome.newer_platform_release(releases, version, platform) is None
    assert welcome.newer_platform_release({}, '1.2.4', 'darwin') is None
    assert welcome.newer_platform_release(releases, '1.2.4', 'linux') is None
