import hashlib
import io
import json
import zipfile
import sys

import pytest

from core.image_analysis import model_runtime as runtime


def archive(path, extra=None, metadata=None):
    with zipfile.ZipFile(path, 'w') as bundle:
        bundle.writestr('runtime.json', json.dumps(metadata or dict(id=runtime.RUNTIME_ID, device='cuda')))
        bundle.writestr(runtime.EXECUTABLE, b'worker-test-fixture')
        for name, contents in (extra or {}).items(): bundle.writestr(name, contents)
    return path


def test_install_is_versioned_and_cpu_selection_stays_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'runtime_cache', lambda: tmp_path/'cache')
    cpu = tmp_path/'cpu'; cpu.mkdir()
    (cpu/runtime.EXECUTABLE).write_bytes(b'cpu')
    (cpu/'runtime.json').write_text(json.dumps(dict(id=runtime.RUNTIME_ID, device='cpu')))
    assert runtime.optional_gpu_required(cpu)
    assert runtime.worker_executable(cpu, 'cpu') == cpu/runtime.EXECUTABLE
    with pytest.raises(FileNotFoundError, match='GPU component'):
        runtime.worker_executable(cpu, 'cuda')
    installed = runtime.install_gpu_archive(archive(tmp_path/'gpu.zip'))
    assert runtime.worker_executable(cpu, 'cuda') == installed/runtime.EXECUTABLE
    assert runtime.worker_executable(cpu, 'cpu') == cpu/runtime.EXECUTABLE
    assert not runtime.optional_gpu_required(cpu)


def test_legacy_full_worker_is_used_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'runtime_cache', lambda: tmp_path/'cache')
    (tmp_path/runtime.EXECUTABLE).write_bytes(b'offline-full')
    assert runtime.worker_executable(tmp_path, 'cuda') == tmp_path/runtime.EXECUTABLE
    assert not runtime.optional_gpu_required(tmp_path)


@pytest.mark.parametrize('name', ['../outside.exe', '/outside.exe', 'C:/outside.exe'])
def test_unsafe_archive_is_rejected_without_installing(tmp_path, monkeypatch, name):
    monkeypatch.setattr(runtime, 'runtime_cache', lambda: tmp_path/'cache')
    with pytest.raises(ValueError, match='Unsafe'):
        runtime.install_gpu_archive(archive(tmp_path/'bad.zip', {name:b'bad'}))
    assert runtime.installed_gpu() is None
    assert not list((tmp_path/'cache').iterdir())


def test_windows_separator_is_not_accepted_in_library_mapping():
    with pytest.raises(ValueError, match='Unsafe'):
        runtime._safe_path('folder\\outside.exe')


def test_invalid_version_and_cancellation_leave_no_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'runtime_cache', lambda: tmp_path/'cache')
    with pytest.raises(ValueError, match='incompatible'):
        runtime.install_gpu_archive(archive(tmp_path/'wrong.zip', metadata=dict(id='old', device='cuda')))
    with pytest.raises(InterruptedError):
        runtime.install_gpu_archive(archive(tmp_path/'valid.zip'), cancel=lambda: True)
    assert runtime.installed_gpu() is None
    assert not list((tmp_path/'cache').iterdir())


def test_duplicate_library_is_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'runtime_cache', lambda: tmp_path/'cache')
    source='_internal/torch/lib/test.dll'; alias='_internal/test.dll'
    target = runtime.install_gpu_archive(archive(tmp_path/'gpu.zip', {source:b'library'},
        dict(id=runtime.RUNTIME_ID, device='cuda', copies={alias:source})))
    assert (target/alias).read_bytes() == (target/source).read_bytes() == b'library'


def test_download_requires_matching_integrity_before_install(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        url = 'https://downloads.example/gpu.zip'
    monkeypatch.setattr(runtime.urllib.request, 'urlopen', lambda *a, **k: Response(b'package'))
    descriptor=dict(url=Response.url, size=7, sha256=hashlib.sha256(b'package').hexdigest())
    runtime.download_gpu(descriptor, tmp_path/'valid.zip', lambda *a: None, lambda: False)
    descriptor['sha256']='0'*64
    with pytest.raises(ValueError, match='integrity'):
        runtime.download_gpu(descriptor, tmp_path/'bad.zip', lambda *a: None, lambda: False)


@pytest.mark.skipif(sys.platform != 'win32', reason='Optional NVIDIA runtime is Windows-only')
def test_cancelling_gpu_install_restores_cpu_selection(qt_application, monkeypatch):
    from PyQt6.QtWidgets import QComboBox, QWidget
    from core.image_analysis import cellpose_backend
    from ui.controllers.model_device_controller import ModelDeviceController
    from ui.widgets import gpu_runtime_dialog
    parent=QWidget(); combo=QComboBox(parent)
    combo.addItem('CPU','cpu'); combo.addItem('GPU','cuda')
    preferences=[]; checks=[]
    monkeypatch.setattr(runtime, 'optional_gpu_required', lambda folder: True)
    monkeypatch.setattr(cellpose_backend, 'set_cellpose_device_preference', preferences.append)
    class Cancelled:
        def __init__(self, *args): pass
        def exec(self): return 0
    monkeypatch.setattr(gpu_runtime_dialog, 'GpuRuntimeDialog', Cancelled)
    controller=ModelDeviceController(combo,parent)
    monkeypatch.setattr(controller, 'check_current_device', lambda: checks.append(combo.currentData()))
    try:
        combo.setCurrentIndex(1)
        assert combo.currentData() == 'cpu'
        assert preferences == ['cpu']
        assert checks == ['cpu']
        from PyQt6.QtCore import QSignalBlocker
        with QSignalBlocker(combo): combo.setCurrentIndex(1)
        controller.state='unavailable'
        combo.activated.emit(1)
        assert combo.currentData() == 'cpu'
        assert preferences == ['cpu','cpu']
    finally:
        controller.shutdown(); parent.close()
