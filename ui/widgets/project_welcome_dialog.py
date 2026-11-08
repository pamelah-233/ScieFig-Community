"""Compact project link shown once on each application launch."""
import json
import os
from pathlib import Path
import re
import sys

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtNetwork import (
    QNetworkAccessManager, QNetworkProxyFactory, QNetworkProxyQuery, QNetworkReply, QNetworkRequest,
)
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from core.constants import SOFTWARE_VERSION
from core.utils.i18n import get_language

PROJECT_URL = 'https://github.com/pamelah-233/ScieFig-Community'
RELEASES_API = 'https://api.github.com/repos/pamelah-233/ScieFig-Community/releases?per_page=30'
COPY = {
    'zh': ('ScieFig 项目主页', '查看版本更新和使用说明，该软件仅限个人免费使用',
           '开始使用', '访问 GitHub ↗', '关闭', '下载 {platform} {version} ↗'),
    'en': ('ScieFig on GitHub',
           'View version updates and the user guide. This software is free for personal use only.',
           'Start working', 'Visit GitHub ↗', 'Close', 'Get {platform} {version} ↗'),
}


def newer_platform_release(releases, current_version, platform):
    suffixes = {
        'win32': ('Windows_Setup.exe', 'Windows_CPU_Setup.exe', 'Windows_English_CPU_Setup.exe',
                  'Setup.exe', 'CPU_Setup.exe', 'English_CPU_Setup.exe'),
        'darwin': ('macOS_arm64.dmg',),
    }.get(platform)
    if not isinstance(releases, list) or not suffixes:
        return None
    current = tuple(map(int, current_version.split('-')[0].split('.')))
    latest = None
    for release in releases:
        if not isinstance(release, dict) or release.get('draft') is not False or release.get('prerelease') is not False:
            continue
        tag = release.get('tag_name')
        match = re.fullmatch(r'community-v([0-9]+\.[0-9]+\.[0-9]+)', tag) if isinstance(tag, str) else None
        if not match:
            continue
        version = match[1]
        number = tuple(map(int, version.split('.')))
        if number <= current:
            continue
        names = {f'ScieFig_{version}_{suffix}' for suffix in suffixes}
        if any(isinstance(asset, dict) and asset.get('state') == 'uploaded' and asset.get('name') in names
               for asset in release.get('assets') or []):
            candidate = (number, tag)
            if latest is None or candidate > latest:
                latest = candidate
    if latest is not None:
        number, tag = latest
        return '.'.join(map(str, number)), f'{PROJECT_URL}/releases/tag/{tag}'
    return None


class ProjectWelcomeDialog(QDialog):
    def __init__(self, parent, resources_dir: Path):
        super().__init__(parent)
        copy = COPY[get_language()]
        self.setWindowTitle('ScieFig')
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(365)
        self.setObjectName('ProjectWelcomeDialog')
        self.setStyleSheet('''
            QDialog#ProjectWelcomeDialog { background: #292827; border: 1px solid #54493e; border-radius: 9px; }
            QLabel { background: transparent; color: #e5e1da; }
            QLabel#WelcomeBrand { font-size: 16px; font-weight: 600; }
            QLabel#WelcomeVersion { color: #aaa095; font-size: 10px; }
            QLabel#WelcomeTitle { font-size: 18px; font-weight: 500; }
            QLabel#WelcomeDescription { color: #afa69c; font-size: 12px; }
            QPushButton { padding: 7px 12px; font-size: 12px; }
            QPushButton#WelcomeClose { border: none; background: transparent; padding: 0; font-size: 20px; color: #b5aaa0; }
            QPushButton#WelcomeGithub { background: #c58d6c; border: 1px solid #c58d6c; color: #211a16; border-radius: 5px; }
            QPushButton#WelcomeGithub:hover { background: #d39a79; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(23, 21, 23, 22)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(QPixmap(str(resources_dir/'icons/sciefig_app_icon.png')).scaled(
            31, 31, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        header.addWidget(icon)
        brand = QLabel('ScieFig'); brand.setObjectName('WelcomeBrand')
        header.addWidget(brand)
        header.addStretch()
        version = QLabel(SOFTWARE_VERSION); version.setObjectName('WelcomeVersion')
        header.addWidget(version)
        self.close_button = QPushButton('×'); self.close_button.setObjectName('WelcomeClose')
        self.close_button.setFixedSize(22, 24)
        self.close_button.setAccessibleName(copy[4])
        self.close_button.clicked.connect(self.reject)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        layout.addSpacing(21)
        title = QLabel(copy[0]); title.setObjectName('WelcomeTitle')
        layout.addWidget(title)
        layout.addSpacing(10)
        self.description = QLabel(copy[1]); self.description.setObjectName('WelcomeDescription')
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.description)
        layout.addSpacing(22)
        actions = QHBoxLayout()
        actions.setSpacing(9)
        actions.addStretch()
        self.start_button = QPushButton(copy[2])
        self.start_button.clicked.connect(self.accept)
        actions.addWidget(self.start_button)
        self.github_button = QPushButton(copy[3]); self.github_button.setObjectName('WelcomeGithub')
        self.project_url = PROJECT_URL
        self._update_version = None
        self._update_status = None
        self.github_button.clicked.connect(self.open_project)
        actions.addWidget(self.github_button)
        layout.addLayout(actions)
        self.start_button.setDefault(True)

    def open_project(self):
        if QDesktopServices.openUrl(QUrl(self.project_url)):
            self.accept()

    def check_platform_update(self):
        url = QUrl(RELEASES_API)
        manager = QNetworkAccessManager(self)
        proxies = QNetworkProxyFactory.systemProxyForQuery(QNetworkProxyQuery(url))
        if proxies:
            manager.setProxy(proxies[0])
        request = QNetworkRequest(url)
        request.setTransferTimeout(5000)
        request.setRawHeader(b'User-Agent', b'ScieFig')
        request.setRawHeader(b'Accept', b'application/vnd.github+json')
        reply = manager.get(request)
        deadline = QTimer(reply)
        deadline.setSingleShot(True)
        deadline.timeout.connect(reply.abort)
        deadline.start(7000)
        reply.finished.connect(lambda: self._show_update(reply, manager))

    def _show_update(self, reply, manager):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            try:
                update = newer_platform_release(json.loads(bytes(reply.readAll())), SOFTWARE_VERSION, sys.platform)
            except (ValueError, TypeError, AttributeError):
                return
            if update is None:
                return
            self._update_version, self.project_url = update
            window = self.parentWidget()
            status = window.statusBar()
            self._update_status = QLabel(status)
            self._update_status.setTextFormat(Qt.TextFormat.RichText)
            self._update_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            self._update_status.linkActivated.connect(lambda _link: self.open_project())
            self.retranslate_update()
            status.insertPermanentWidget(0, self._update_status)
        finally:
            manager.deleteLater()

    def retranslate_update(self):
        if self._update_version is None:
            return
        platform = 'macOS' if sys.platform == 'darwin' else 'Windows'
        text = COPY[get_language()][5].format(platform=platform, version=self._update_version)
        self.github_button.setText(text)
        self._update_status.setText(f'<a href="release" style="color: #d6ab94;">{text}</a>')
        self._update_status.setAccessibleName(text)


def schedule_project_welcome(window, resources_dir):
    """Do not delay startup or open a browser without a user click."""
    dialog = ProjectWelcomeDialog(window, resources_dir)
    window.project_welcome_dialog = dialog
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: dialog.open() if window.isVisible() else None)
    timer.start(220)
    if sys.platform in {'win32', 'darwin'} and getattr(sys, 'frozen', False) and os.environ.get('SCIEFIG_RELEASE_SMOKE') != '1':
        update_timer = QTimer(dialog)
        update_timer.setSingleShot(True)
        update_timer.timeout.connect(dialog.check_platform_update)
        update_timer.start(1000)
