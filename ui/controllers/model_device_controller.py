"""Nonblocking, runtime-confirmed feedback for the shared model selector."""
import json
from pathlib import Path
import tempfile
import sys

from PyQt6.QtCore import QObject, QEvent, QProcess, QSignalBlocker, QTimer
from PyQt6.QtWidgets import QApplication

from core.image_analysis import cellpose_backend
from core.utils.i18n import tr


class ModelDeviceController(QObject):
    def __init__(self, combo, parent):
        super().__init__(parent)
        self.combo = combo
        self.state = 'unchecked'
        self.error = ''
        self._ready = set()
        self._process = None
        self._folder = None
        self._target = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.check_current_device)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._timed_out)
        combo.currentIndexChanged.connect(self._selected)
        combo.activated.connect(lambda index: self._selected(index) if self.state == 'unavailable' else None)
        parent.installEventFilter(self)
        parent.destroyed.connect(self.shutdown)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.retranslate()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            self._timer.start(400)
        elif event.type() == QEvent.Type.Hide:
            self.shutdown()
        return False

    def _selected(self, _index):
        self._timer.stop()
        self._stop_probe()
        if self.combo.currentData() == 'cuda' and sys.platform == 'win32':
            from core.image_analysis import model_runtime
            if model_runtime.optional_gpu_required(cellpose_backend.worker_root()):
                self.state = 'unchecked'
                self.retranslate()
                from ui.widgets.gpu_runtime_dialog import GpuRuntimeDialog
                resources = (Path(getattr(sys, '_MEIPASS'))/'resources' if getattr(sys, 'frozen', False)
                             else Path(__file__).resolve().parents[2]/'resources')
                dialog = GpuRuntimeDialog(self.combo.window(), resources)
                if not dialog.exec():
                    with QSignalBlocker(self.combo):
                        self.combo.setCurrentIndex(self.combo.findData('cpu'))
        cellpose_backend.set_cellpose_device_preference(self.combo.currentData())
        self.check_current_device()

    def check_current_device(self):
        self._timer.stop()
        self._stop_probe()
        device = self.combo.currentData()
        self.error = ''
        self.state = 'ready' if device in self._ready else 'checking'
        self.retranslate()
        if self.state == 'ready':
            return
        try:
            self._folder = tempfile.TemporaryDirectory(prefix='ScieFig-device-probe-')
            command, self._target = cellpose_backend.device_probe_command(Path(self._folder.name), device)
            process = QProcess(self)
            self._process = process
            process.finished.connect(lambda code, status: self._finished(process, device, code))
            process.errorOccurred.connect(lambda error: self._process_error(process, error))
            process.start(command[0], command[1:])
            self._timeout.start(120000)
        except Exception as exc:
            self._unavailable(str(exc))

    def _finished(self, process, device, code):
        if process is not self._process:
            return
        try:
            if code != 0:
                detail = bytes(process.readAllStandardError()).decode('utf-8', errors='replace')
                raise RuntimeError(detail[-1800:] or process.errorString())
            metadata = json.loads(self._target.with_suffix('.json').read_text(encoding='utf-8'))
            if metadata.get('device') != device:
                raise RuntimeError(tr('model_device_mismatch'))
        except Exception as exc:
            self._unavailable(str(exc))
            return
        self._ready.add(device)
        self.state = 'ready'
        self._stop_probe()
        self.retranslate()

    def _process_error(self, process, error):
        if process is self._process and error == QProcess.ProcessError.FailedToStart:
            self._unavailable(process.errorString())

    def _timed_out(self):
        self._unavailable(tr('model_device_timeout'))

    def _unavailable(self, detail):
        self.error = detail
        self.state = 'unavailable'
        self._stop_probe()
        self.retranslate()

    def _stop_probe(self):
        self._timeout.stop()
        process, self._process = self._process, None
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1000)
            process.deleteLater()
        if self._folder is not None:
            self._folder.cleanup()
            self._folder = None

    def shutdown(self):
        self._timer.stop()
        self._stop_probe()

    def retranslate(self):
        device = self.combo.currentData()
        key = {'checking': 'model_device_checking', 'unavailable': 'model_device_unavailable',
               'unchecked': 'model_device_unchecked'}.get(self.state,
                   'model_device_cpu_ready' if device == 'cpu' else 'model_device_gpu_ready')
        with QSignalBlocker(self.combo):
            for index, label in enumerate(('cellpose_device_cpu', 'cellpose_device_gpu')):
                text = tr(label)
                if self.combo.itemData(index) == device:
                    text += ' · '+tr(key)
                self.combo.setItemText(index, text)
        hint = tr('cellpose_device_hint')+'\n'+tr(key)
        if self.error:
            hint += '\n'+self.error
        self.combo.setToolTip(hint)
        self.combo.setAccessibleName(tr('cellpose_device_label')+' · '+tr(key))
