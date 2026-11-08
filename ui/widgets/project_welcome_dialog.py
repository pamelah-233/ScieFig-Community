"""Compact project link shown once on each application launch."""
import json
import os
from pathlib import Path
import re
import sys

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkProxyFactory, QNetworkProxyQuery, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from core.constants import SOFTWARE_VERSION
from core.utils.i18n import get_language

PROJECT_URL = 'https://github.com/pamelah-233/ScieFig-Community'
RELEASES_API = 'https://api.github.com/repos/pamelah-233/ScieFig-Community/releases?per_page=30'
COPY = {
    'zh': ('ScieFig 项目主页', '查看版本更新和使用说明，该软件仅限个人免费使用',
           '开始使用', '访问 GitHub ↗', '关闭', '下载 Windows {version} ↗'),
    'en': ('ScieFig on GitHub',
           'View version updates and the user guide. This software is free for personal use only.',
           'Start working', 'Visit GitHub ↗', 'Close', 'Get Windows {version} ↗'),
}


def newer_windows_release(releases, current_version):
    current = tuple(map(int, current_version.split('-')[0].split('.')))
    latest = None
    for release in releases:
        tag = release.get('tag_name', '')
        match = re.fullmatch(r'(windows|community)-v(\d+\.\d+\.\d+)', tag)
        if not match or release.get('draft') or release.get('prerelease'):
            continue
        platform, version = match.groups()
        number = tuple(map(int, version.split('.')))
        if number <= current:
            continue
        prefix = f'ScieFig_{version}_'
        names = {prefix + suffix for suffix in (
            'Windows_Setup.exe', 'Windows_CPU_Setup.exe', 'Windows_English_CPU_Setup.exe',
        )}
        if platform == 'community':
            names.update(prefix + suffix for suffix in ('Setup.exe', 'CPU_Setup.exe', 'English_CPU_Setup.exe'))
        if any(asset.get('state') == 'uploaded' and asset.get('name') in names for asset in release.get('assets', [])):
            candidate = (number, platform == 'windows', tag)
            if latest is None or candidate > latest:
                latest = candidate
    if latest is not None:
        number, _, tag = latest
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
        self.github_button.clicked.connect(self.open_project)
        actions.addWidget(self.github_button)
        layout.addLayout(actions)
        self.start_button.setDefault(True)

    def open_project(self):
        if QDesktopServices.openUrl(QUrl(self.project_url)):
            self.accept()

    def check_windows_update(self):
        url = QUrl(RELEASES_API)
        manager = QNetworkAccessManager(self)
        proxies = QNetworkProxyFactory.systemProxyForQuery(QNetworkProxyQuery(url))
        if proxies:
            manager.setProxy(proxies[0])
        request = QNetworkRequest(url)
        request.setTransferTimeout(5000)
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
                update = newer_windows_release(json.loads(bytes(reply.readAll())), SOFTWARE_VERSION)
            except (ValueError, TypeError, AttributeError):
                return
            if update is None:
                return
            version, self.project_url = update
            text = COPY[get_language()][5].format(version=version)
            self.github_button.setText(text)
            window = self.parentWidget()
            button = QPushButton(text, window)
            button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.project_url)))
            window.statusBar().addPermanentWidget(button)
        finally:
            manager.deleteLater()


def schedule_project_welcome(window, resources_dir):
    """Do not delay startup or open a browser without a user click."""
    dialog = ProjectWelcomeDialog(window, resources_dir)
    window.project_welcome_dialog = dialog
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: dialog.open() if window.isVisible() else None)
    timer.start(220)
    if sys.platform == 'win32' and os.environ.get('SCIEFIG_RELEASE_SMOKE') != '1':
        dialog.check_windows_update()
