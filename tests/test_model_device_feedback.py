import json
import sys
import time

import pytest
from PyQt6 import QtCore
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget


@pytest.mark.parametrize('reported', ['cuda', 'cpu', 'unavailable'])
def test_toolbar_only_reports_connected_after_worker_confirmation(
        tmp_path, monkeypatch, qt_application, reported):
    from core.image_analysis import cellpose_backend as backend
    from ui.widgets.app_shell import V9AppShell
    from core.utils.i18n import tr
    settings_class = QtCore.QSettings
    monkeypatch.setattr(QtCore, 'QSettings', lambda *args: settings_class(
        str(tmp_path/'device.ini'), settings_class.Format.IniFormat))
    script = tmp_path/'probe.py'
    script.write_text('import sys,json,time\nfrom pathlib import Path\n'
        'time.sleep(.1)\n'
        + ("sys.stderr.write('CUDA unavailable');sys.exit(1)\n" if reported == 'unavailable' else
           f"Path(sys.argv[1]).with_suffix('.json').write_text(json.dumps({{'device': {reported!r}}}))\n"))
    def probe(folder, device):
        target = folder/'labels.npy'
        return [sys.executable, str(script), str(target)], target
    monkeypatch.setattr(backend, 'device_probe_command', probe, raising=False)
    shell = V9AppShell(QWidget(), QWidget(), QWidget(), {}, ['Nature'])
    try:
        assert hasattr(shell, 'model_device_controller')
        combo = shell.cellpose_device_combo
        combo.setCurrentIndex(combo.findData('cuda'))
        assert tr('model_device_checking') in combo.currentText()
        deadline = time.monotonic()+5
        while shell.model_device_controller.state == 'checking' and time.monotonic() < deadline:
            qt_application.processEvents()
            QTest.qWait(10)
        expected = 'ready' if reported == 'cuda' else 'unavailable'
        assert shell.model_device_controller.state == expected
        assert tr('model_device_gpu_ready' if expected == 'ready' else 'model_device_unavailable') in combo.currentText()
        assert combo.currentData() == backend.cellpose_device_preference() == 'cuda'
        if reported == 'unavailable':
            assert 'CUDA unavailable' in combo.toolTip()
        combo.setCurrentIndex(combo.findData('cpu'))
        assert tr('model_device_checking') in combo.currentText()
        shell.model_device_controller.retranslate()
        assert tr('model_device_checking') in combo.currentText()
    finally:
        shell.close()
        shell.deleteLater()
        qt_application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
