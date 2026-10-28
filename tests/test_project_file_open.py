import importlib
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from core.models import Project
from ui.main_window import MainWindow

APP=QApplication.instance() or QApplication([])
ROOT=Path(__file__).resolve().parents[1]


def opener():
    name='ui.controllers.project_open_controller'
    assert importlib.util.find_spec(name) is not None
    return importlib.import_module(name)


def test_startup_opens_unicode_space_path_without_file_dialog_or_recovery(tmp_path,monkeypatch):
    opening=opener()
    path=tmp_path/'中文 实验.SFIG'
    Project(title='Associated project').save_sfig(path)
    window=MainWindow(resources_dir=ROOT/'resources')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_: (_ for _ in ()).throw(AssertionError('No file picker on startup')))
    recovered=[]
    monkeypatch.setattr(window.project_recovery,'schedule_startup_recovery',lambda:recovered.append(True))
    opening.schedule_startup_project(window,[str(path)])
    APP.processEvents()
    assert window.project.title=='Associated project'
    assert not recovered
    window.close();APP.processEvents()


def test_startup_without_file_keeps_recovery_workflow(monkeypatch):
    opening=opener()
    window=MainWindow(resources_dir=ROOT/'resources')
    recovered=[]
    monkeypatch.setattr(window.project_recovery,'schedule_startup_recovery',lambda:recovered.append(True))
    opening.schedule_startup_project(window,[])
    assert recovered==[True]
    window.close();APP.processEvents()


def test_failed_path_open_keeps_current_project(tmp_path,monkeypatch):
    opening=opener()
    window=MainWindow(resources_dir=ROOT/'resources')
    previous=window.project
    warnings=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:warnings.append(args))
    assert not opening.open_project_path(window,tmp_path/'missing.sfig',confirm_unsaved=False)
    assert window.project is previous
    assert warnings
    window.close();APP.processEvents()


def test_installer_registers_sfig_with_quoted_executable_and_file_argument():
    script=(ROOT/'packaging/ScieFig_R10.iss').read_text(encoding='utf8')
    assert 'ChangesAssociations=yes' in script
    assert r'Software\Classes\.sfig\OpenWithProgids' in script
    assert r'Software\Classes\ScieFig.Project\DefaultIcon' in script
    assert r'Software\Classes\ScieFig.Project\shell\open\command' in script
    assert 'ValueData: """{app}\\ScieFig_R10.exe"" ""%1"""' in script
    assert 'UserChoice' not in script
