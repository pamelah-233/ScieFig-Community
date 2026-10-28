from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QFileDialog, QDialog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

import main as main_module
from core.licensing import LICENSE_FILENAME, LicenseManager
from ui.dialogs.license_activation_dialog import LicenseActivationDialog


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "issuer_private_key.pem"
    public_path = tmp_path / "license_public_key.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def _issue_license(tmp_path: Path, manager: LicenseManager, private_key_path: Path) -> Path:
    from scripts.licensing.issue_r095_license import issue_license_file

    output_path = tmp_path / "issued_license.json"
    issue_license_file(
        private_key_path=private_key_path,
        machine_code=manager.current_machine_code(),
        output_path=output_path,
        license_id="ui-lic-0001",
        issued_at="2026-06-22T12:00:00Z",
    )
    return output_path


def _manager(tmp_path: Path, public_key_path: Path) -> LicenseManager:
    return LicenseManager(
        public_key_path=public_key_path,
        license_storage_path=tmp_path / LICENSE_FILENAME,
        machine_id_provider=lambda: "windows-machine-guid-ui",
    )


def test_activation_dialog_shows_machine_code_and_invalid_import_feedback(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    _private_key_path, public_key_path = _write_keypair(tmp_path)
    manager = _manager(tmp_path, public_key_path)
    dialog = LicenseActivationDialog(manager=manager)
    invalid_path = tmp_path / "invalid_license.json"
    invalid_path.write_text('{"not":"a-license"}', encoding="utf-8")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(invalid_path), "JSON (*.json)"))

    dialog._import_license()
    app.processEvents()

    assert manager.current_machine_code() in dialog.machine_code_field.text()
    assert "invalid" in dialog.status_label.text().casefold()
    assert dialog.result() == 0
    dialog.close()
    app.processEvents()


def test_activation_dialog_accepts_valid_import_and_persists_to_programdata_only(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    private_key_path, public_key_path = _write_keypair(tmp_path)
    manager = _manager(tmp_path, public_key_path)
    license_path = _issue_license(tmp_path, manager, private_key_path)
    dialog = LicenseActivationDialog(manager=manager)

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(license_path), "JSON (*.json)"))

    dialog._import_license()
    app.processEvents()

    assert dialog.result() == int(dialog.DialogCode.Accepted)
    assert (tmp_path / LICENSE_FILENAME).exists()
    assert not any(path.suffix == ".sfig" for path in tmp_path.rglob("*"))
    dialog.close()
    app.processEvents()


def test_main_shows_activation_before_main_window_when_license_missing(monkeypatch, tmp_path: Path) -> None:
    _app()
    events: list[str] = []

    class FakeManager:
        def license_status(self):
            return type("Status", (), {"is_valid": False})()

    class FakeDialog:
        def __init__(self, manager):
            events.append("dialog")

        def exec(self):
            return int(QDialog.DialogCode.Rejected)

    class FakeWindow:
        def __init__(self, *args, **kwargs):
            events.append("window")

        def show(self):
            events.append("show")

    class FakeApp:
        def __init__(self, argv):
            self.argv = argv
            self.pending_projects = []

        def setApplicationVersion(self, version):pass

        def setWindowIcon(self, icon):pass

        def setOrganizationName(self, name):
            self.organization_name = name

        def setApplicationName(self, name):
            self.application_name = name

        def setStyleSheet(self, text):
            self.stylesheet = text

        def exec(self):
            return 0

    monkeypatch.setattr(main_module, "ScieFigApplication", FakeApp)
    monkeypatch.setattr(main_module, "configure_application", lambda app: None)
    monkeypatch.setattr(main_module, "configure_window", lambda window: None)
    monkeypatch.setattr(main_module, "load_qss", lambda _path: "")
    monkeypatch.setattr(main_module, "resolve_resources_dir", lambda: tmp_path)
    monkeypatch.setattr(main_module, "build_license_manager", lambda resources_dir=None: FakeManager())
    monkeypatch.setattr('ui.dialogs.license_activation_dialog.LicenseActivationDialog', FakeDialog)
    monkeypatch.setattr('ui.main_window.MainWindow', FakeWindow)
    monkeypatch.setattr(main_module.sys,'excepthook',main_module.sys.excepthook)

    assert main_module.main() == 1
    assert events == ["dialog"]


def test_main_constructs_main_window_after_activation_accepts(monkeypatch, tmp_path: Path) -> None:
    _app()
    events: list[str] = []

    class FakeManager:
        def __init__(self):
            self.calls = 0

        def license_status(self):
            self.calls += 1
            return type("Status", (), {"is_valid": self.calls > 1})()

    fake_manager = FakeManager()

    class FakeDialog:
        def __init__(self, manager):
            events.append("dialog")

        def exec(self):
            return int(QDialog.DialogCode.Accepted)

    class FakeWindow:
        def __init__(self, *args, **kwargs):
            events.append("window")
            from types import SimpleNamespace
            self.project_recovery = SimpleNamespace(schedule_startup_recovery=lambda: None)

        def show(self):
            events.append("show")

    class FakeApp:
        def __init__(self, argv):
            self.argv = argv
            self.pending_projects = []

        def setApplicationVersion(self, version):pass

        def setWindowIcon(self, icon):pass

        def setOrganizationName(self, name):
            self.organization_name = name

        def setApplicationName(self, name):
            self.application_name = name

        def setStyleSheet(self, text):
            self.stylesheet = text

        def exec(self):
            events.append("exec")
            return 0

    monkeypatch.setattr(main_module, "ScieFigApplication", FakeApp)
    monkeypatch.setattr(main_module, "configure_application", lambda app: None)
    monkeypatch.setattr(main_module, "configure_window", lambda window: None)
    monkeypatch.setattr(main_module, "load_qss", lambda _path: "")
    monkeypatch.setattr(main_module, "resolve_resources_dir", lambda: tmp_path)
    monkeypatch.setattr(main_module, "build_license_manager", lambda resources_dir=None: fake_manager)
    monkeypatch.setattr('ui.dialogs.license_activation_dialog.LicenseActivationDialog', FakeDialog)
    monkeypatch.setattr('ui.main_window.MainWindow', FakeWindow)
    monkeypatch.setattr(main_module.sys,'excepthook',main_module.sys.excepthook)

    assert main_module.main() == 0
    assert events == ["dialog", "window", "show", "exec"]
