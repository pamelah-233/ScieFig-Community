from pathlib import Path
import os
import subprocess
import sys

import pytest
from PyQt6.QtTest import QTest

from core.utils.i18n import get_language,set_language
from ui.widgets.startup_splash import StartupSplash,COPY


def test_entrypoint_import_leaves_analysis_modules_unloaded():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',
        "import main,sys; assert 'ui.main_window' not in sys.modules; assert 'numpy' not in sys.modules; assert 'core.licensing' not in sys.modules"],
        cwd=root,env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_later_stages_do_not_run_queued_main_window_work(qt_application):
    from PyQt6.QtCore import QTimer
    splash=StartupSplash(Path(__file__).resolve().parents[1]/'resources')
    try:
        splash.show();splash.set_stage(0)
        calls=[];QTimer.singleShot(0,lambda:calls.append('queued'))
        for index in (1,2,3):splash.set_stage(index)
        assert not calls
        qt_application.processEvents()
        assert calls==['queued']
    finally:splash.dispose()


@pytest.mark.parametrize('language',['zh','en'])
def test_progress_tracks_completed_stages_instead_of_elapsed_time(qt_application,language):
    previous=get_language();set_language(language)
    splash=StartupSplash(Path(__file__).resolve().parents[1]/'resources')
    try:
        splash.show()
        for index in range(5):
            splash.set_stage(index)
            assert splash.progress_percent==25*index
            assert COPY[language][1][index] in splash.accessibleName()
        splash.set_stage(2);QTest.qWait(90)
        assert splash.progress_percent==50
        assert splash.size().width()==400 and splash.size().height()==210
        splash.finish();QTest.qWait(260)
        assert not splash.isVisible()
    finally:
        splash.dispose();set_language(previous)


@pytest.mark.parametrize('fail_window',[False,True])
def test_startup_shows_feedback_before_window_and_cleans_up_on_failure(qt_application,monkeypatch,tmp_path,fail_window):
    import main
    import core.licensing
    import ui.main_window
    import ui.widgets.startup_splash
    events=[]
    class Splash:
        def __init__(self,resources):events.append('splash')
        def show(self):events.append('splash-show')
        def set_stage(self,index):events.append(f'stage-{index}')
        def finish(self):events.append('finish')
        def close(self):events.append('splash-close')
        def dispose(self):events.append('splash-close')
    class App:
        pending_projects=[]
        def __init__(self,args):pass
        def setOrganizationName(self,value):pass
        def setApplicationName(self,value):pass
        def setApplicationVersion(self,value):pass
        def setWindowIcon(self,value):pass
        def setStyleSheet(self,value):pass
        def exec(self):events.append('exec');return 0
    class Window:
        def __init__(self,resources_dir):
            events.append('window')
            if fail_window:raise RuntimeError('startup failure')
        def show(self):events.append('window-show')
    monkeypatch.setattr(main,'ScieFigApplication',App)
    monkeypatch.setattr(main,'configure_application',lambda app:None)
    monkeypatch.setattr(main,'configure_window',lambda window:None)
    monkeypatch.setattr(main,'resolve_resources_dir',lambda:tmp_path)
    monkeypatch.setattr(main,'load_qss',lambda resources:'')
    def forbid_activation(*args, **kwargs):
        raise AssertionError('Community startup must not request device activation')
    monkeypatch.setattr(core.licensing,'LicenseManager',forbid_activation)
    monkeypatch.setattr(ui.main_window,'MainWindow',Window)
    monkeypatch.setattr(ui.widgets.startup_splash,'StartupSplash',Splash)
    monkeypatch.setattr('ui.controllers.project_open_controller.schedule_startup_project',lambda *a:events.append('project-routing'))
    monkeypatch.setattr('ui.widgets.project_welcome_dialog.schedule_project_welcome',lambda *a:events.append('project-welcome'))
    monkeypatch.setattr(sys,'excepthook',sys.excepthook)
    if fail_window:
        with pytest.raises(RuntimeError,match='startup failure'):main.main()
        assert events[-1]=='splash-close'
        assert 'finish' not in events
    else:
        assert main.main()==0
        assert events.index('window-show')<events.index('finish')<events.index('project-routing')
        assert events.index('finish')<events.index('project-welcome')<events.index('exec')
    assert events.index('splash-show')<events.index('stage-1')<events.index('stage-2')<events.index('window')
