"""Download or import the optional GPU worker without blocking the UI thread."""
from pathlib import Path
import tempfile

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from core.image_analysis import model_runtime
from core.utils.i18n import get_language

COPY = {
    'zh': ('安装 GPU 加速组件', 'GPU 组件只需安装一次。可从 GitHub 下载，也可导入网盘下载的组件包。',
           '从 GitHub 下载', '导入本地组件包…', '取消', '下载中', '安装中',
           '已安装，正在检测 GPU。', '安装已取消', '此构建尚未配置在线下载，请导入本地组件包。', '正在取消…'),
    'en': ('Install GPU acceleration', 'Install the GPU component once. Download from GitHub or import a package from a cloud drive.',
           'Download from GitHub', 'Import local package…', 'Cancel', 'Downloading', 'Installing',
           'Installed. Checking GPU.', 'Installation cancelled', 'Online download is not configured for this build. Import a local package.', 'Cancelling…'),
}


class _InstallWorker(QThread):
    progress = pyqtSignal(str, int, int)
    failed = pyqtSignal(str)
    installed = pyqtSignal()

    def __init__(self, descriptor, archive, parent):
        super().__init__(parent)
        self.descriptor, self.archive = descriptor, archive

    def run(self):
        try:
            progress = lambda phase, done, total: self.progress.emit(phase, int(done*100/max(total, 1)), 100)
            with tempfile.TemporaryDirectory(prefix='ScieFig-gpu-download-') as folder:
                archive = self.archive or str(Path(folder)/'gpu.zip')
                if not self.archive:
                    model_runtime.download_gpu(self.descriptor, archive, progress, self.isInterruptionRequested)
                model_runtime.install_gpu_archive(archive, progress, self.isInterruptionRequested)
            self.installed.emit()
        except InterruptedError:
            self.failed.emit(COPY[get_language()][9])
        except Exception as exc:
            self.failed.emit(str(exc))


class GpuRuntimeDialog(QDialog):
    def __init__(self, parent, resources):
        super().__init__(parent)
        self.copy = COPY[get_language()]
        self.setWindowTitle(self.copy[0])
        self.setMinimumWidth(380)
        self.worker = None
        try:
            self.descriptor = model_runtime.read_download_descriptor(resources)
        except (OSError, ValueError):
            self.descriptor = {}
        layout = QVBoxLayout(self)
        description = QLabel(self.copy[1]); description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel(''); self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        self.download = QPushButton(self.copy[2])
        self.download.setEnabled(bool(self.descriptor.get('url') and self.descriptor.get('sha256')))
        if not self.download.isEnabled():
            self.status.setText(self.copy[10])
        self.local = QPushButton(self.copy[3])
        self.cancel = QPushButton(self.copy[4])
        for button in (self.download, self.local, self.cancel): actions.addWidget(button)
        layout.addLayout(actions)
        self.download.clicked.connect(lambda: self.start_install(''))
        self.local.clicked.connect(self.import_local)
        self.cancel.clicked.connect(self.reject)
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def import_local(self):
        filename, _ = QFileDialog.getOpenFileName(self, self.copy[3], '', 'ScieFig GPU (*.zip)')
        if filename: self.start_install(filename)

    def start_install(self, archive):
        self.download.setEnabled(False); self.local.setEnabled(False)
        self.worker = _InstallWorker(self.descriptor, archive, self)
        self.worker.progress.connect(self.update_progress)
        self.worker.failed.connect(self.show_error)
        self.worker.installed.connect(lambda: self.status.setText(self.copy[8]))
        self.worker.finished.connect(self.install_finished)
        self.worker.start()

    def update_progress(self, phase, percent, _total):
        self.status.setText(f'{self.copy[5 if phase == "download" else 6]} {percent}%')
        self.progress.setValue(percent)

    def show_error(self, message):
        self.status.setText(message)

    def install_finished(self):
        success = model_runtime.installed_gpu() is not None
        if self.worker.isInterruptionRequested():
            super().reject()
        elif success:
            self.accept()
        else:
            self.local.setEnabled(True)
            self.download.setEnabled(bool(self.descriptor.get('url') and self.descriptor.get('sha256')))

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText(self.copy[11])
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.reject(); event.ignore()
        else:
            super().closeEvent(event)

    def shutdown(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait()
