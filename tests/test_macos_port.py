from pathlib import Path
import sys
import pytest

from PyQt6.QtCore import QEvent

from core.models import Project
from ui.local_style_extraction.ui_theme import stylesheet


def test_temporary_directory_alias_keeps_project_lifecycle_safe(tmp_path, monkeypatch):
    import core.models.project as module
    real = tmp_path / 'real-temp'
    real.mkdir()
    alias = tmp_path / 'alias-temp'
    alias.symlink_to(real, target_is_directory=True)
    owned = alias / 'sciefig_owned'
    owned.mkdir()
    monkeypatch.setattr(module.tempfile, 'gettempdir', lambda: str(alias))
    project = Project()
    project.register_archive_extract_root(owned)
    project.dispose()
    assert not owned.exists()
    outside = tmp_path / 'sciefig_user_data'
    outside.mkdir()
    assert module._validated_archive_extract_root(outside) is None
    linked = alias / 'sciefig_link'
    linked.symlink_to(outside, target_is_directory=True)
    assert module._validated_archive_extract_root(linked) is None


def test_local_style_tokens_are_complete_and_label_backgrounds_transparent():
    for theme in ('light', 'dark'):
        qss = stylesheet(theme)
        assert '$' not in qss
        assert '#38302a2' not in qss
        assert '#d977572' not in qss
        assert 'QLabel { background: transparent; }' in qss


def test_macos_frozen_cellpose_uses_bundled_resources(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core.image_analysis import cellpose_backend as backend
    executable = tmp_path / 'ScieFig.app/Contents/MacOS/ScieFig'
    worker = tmp_path / 'ScieFig.app/Contents/Resources/cellpose/ScieFig_Cellpose'
    worker.parent.mkdir(parents=True)
    worker.touch()
    monkeypatch.setattr(backend, 'sys', SimpleNamespace(platform='darwin', frozen=True, executable=str(executable)))
    assert backend.worker_command() == [str(worker)]


def test_finder_open_event_is_queued_until_window_is_ready():
    from types import SimpleNamespace
    from ui.platform_support import ScieFigApplication
    state = SimpleNamespace(project_window=None, pending_projects=[])
    event = SimpleNamespace(type=lambda: QEvent.Type.FileOpen, file=lambda: '/tmp/example.sfig')
    assert ScieFigApplication.event(state, event)
    assert state.pending_projects == ['/tmp/example.sfig']


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='desktop device selector')
def test_cpu_gpu_toolbar_selection_is_visible_and_remembered(tmp_path, monkeypatch, qt_application):
    from PyQt6 import QtCore
    from PyQt6.QtWidgets import QWidget
    from ui.widgets.app_shell import V9AppShell
    from core.image_analysis.cellpose_backend import cellpose_device_preference
    settings_class = QtCore.QSettings
    monkeypatch.setattr(QtCore, 'QSettings', lambda *args: settings_class(str(tmp_path/'devices.ini'), settings_class.Format.IniFormat))
    for device in ('cpu', 'mps' if sys.platform == 'darwin' else 'cuda'):
        shell = V9AppShell(QWidget(), QWidget(), QWidget(), {}, ['Nature'])
        shell.resize(1024, 720)
        shell.show()
        qt_application.processEvents()
        combo = shell.cellpose_device_combo
        combo.setCurrentIndex(combo.findData(device))
        assert combo.isVisible() and combo.width() >= combo.minimumSizeHint().width()
        assert cellpose_device_preference() == device
        from core.utils import i18n
        for language, prefix in (('zh', '模型：'), ('en', 'Model: ')):
            monkeypatch.setattr(i18n, 'LANGUAGE', language)
            shell.retranslate()
            assert combo.currentText().startswith(prefix + ('CPU' if device == 'cpu' else 'GPU') + ' · ')
            assert combo.currentData() == device
        next_shell = V9AppShell(QWidget(), QWidget(), QWidget(), {}, ['Nature'])
        assert next_shell.cellpose_device_combo.currentData() == device
        shell.close()
        next_shell.close()
