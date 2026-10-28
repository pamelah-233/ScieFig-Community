import sys
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QWidget


@pytest.mark.skipif(sys.platform!='win32',reason='Windows studio parity')
def test_windows_uses_shared_studio_theme():
    from main import load_qss
    qss=load_qss(Path(__file__).resolve().parents[1]/'resources')
    assert '#1e1e1f' in qss
    assert 'toolbarIconOnly' in qss
    assert 'studioPreview' in qss


@pytest.mark.skipif(sys.platform!='win32',reason='Windows studio parity')
def test_windows_toolbar_has_same_compact_actions(qt_application):
    from ui.widgets.app_shell import V9AppShell
    calls=[]
    shell=V9AppShell(QWidget(),QWidget(),QWidget(),{key:lambda k=key:calls.append(k) for key in ('new','open','save','save_as','export')},['Nature'])
    shell.resize(890,720);shell.show();qt_application.processEvents()
    try:
        assert hasattr(shell,'_action_buttons')
        assert shell.cellpose_device_combo.isVisible()
        assert shell.cellpose_device_combo.findData('cpu')>=0
        assert shell.cellpose_device_combo.findData('cuda')>=0
        assert shell._toolbar_compact
        for button in shell._action_buttons.values():
            assert button.width()==34 and not button.text()
            assert not button.icon().isNull() and button.toolTip()==button.accessibleName()
    finally:shell.close();shell.deleteLater();qt_application.processEvents()
