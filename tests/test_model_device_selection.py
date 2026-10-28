"""All bundled neural methods honor the same explicit CPU/GPU selector."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest


@pytest.mark.parametrize('device', ['cpu', 'cuda'])
def test_background_analysis_keeps_device_selected_when_submitted(tmp_path, monkeypatch, device):
    from PyQt6 import QtCore
    from core.image_analysis import cellpose_backend as backend
    from ui.controllers.analysis_job_service import _AnalysisWorker
    settings_class = QtCore.QSettings
    monkeypatch.setattr(QtCore, 'QSettings', lambda *args: settings_class(
        str(tmp_path/'device.ini'), settings_class.Format.IniFormat))
    backend.set_cellpose_device_preference(device)
    seen = []
    def work():
        seen.append(backend.cellpose_device_preference())
        backend.set_cellpose_device_preference('cpu' if device == 'cuda' else 'cuda')
        seen.append(backend.cellpose_device_preference())
    worker = _AnalysisWorker('two-pass-model', work)
    backend.set_cellpose_device_preference('cpu' if device == 'cuda' else 'cuda')
    worker.run()
    assert seen == [device, device]
    assert backend.cellpose_device_preference() != device


@pytest.mark.parametrize('model', ['cyto3', 'nuclei', 'cyto3_dapi_reviewed_v1',
    'cyto3_he_fiber_boundary_v1', 'cyto3_laminin_final_v1'])
@pytest.mark.parametrize('device', ['cpu', 'cuda'])
def test_every_cellpose_model_receives_and_verifies_device(monkeypatch, model, device):
    from core.image_analysis import cellpose_backend as backend
    monkeypatch.setattr(backend, 'worker_command', lambda: ['worker'])
    monkeypatch.setattr(backend, 'cellpose_device_preference', lambda: device)
    def run(command, **kwargs):
        assert command[command.index('--device')+1] == device
        assert command[command.index('--model')+1] == model
        target = Path(command[2])
        np.save(target, np.zeros((8, 10), np.int32))
        target.with_suffix('.json').write_text(json.dumps(dict(model=model, device=device)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(backend.subprocess, 'run', run)
    _, metadata = backend.run_cellpose_labels(np.zeros((8, 10), np.uint8), model=model)
    assert metadata['device'] == device


@pytest.mark.parametrize('requested,cuda,mps,expected', [
    ('cpu', True, True, 'cpu'), ('cuda', True, False, 'cuda'),
    ('mps', False, True, 'mps'), ('auto', True, False, 'cuda'),
    ('auto', False, True, 'mps'), ('auto', False, False, 'cpu')])
def test_worker_device_selection(requested, cuda, mps, expected):
    spec = importlib.util.spec_from_file_location('sciefig_worker', Path(__file__).resolve().parents[1]/'packaging/cellpose_worker.py')
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)), device=lambda value: value)
    assert worker.select_device(torch, requested) == expected


@pytest.mark.parametrize('requested', ['cuda', 'mps'])
def test_worker_never_silently_uses_cpu_for_unavailable_gpu(requested):
    spec = importlib.util.spec_from_file_location('sciefig_worker', Path(__file__).resolve().parents[1]/'packaging/cellpose_worker.py')
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)), device=lambda value: value)
    with pytest.raises(RuntimeError, match='Select Model: CPU'):
        worker.select_device(torch, requested)


@pytest.mark.parametrize('reported', ['cuda', 'cpu'])
@pytest.mark.parametrize('device', ['cpu', 'cuda'])
def test_truefad_uses_and_verifies_selected_device(monkeypatch, reported, device):
    from core.image_analysis import myotube_boundary as boundary, cellpose_backend as backend
    monkeypatch.setattr(backend, 'worker_command', lambda: ['worker'])
    monkeypatch.setattr(backend, 'cellpose_device_preference', lambda: device)
    def run(command, **kwargs):
        assert command[command.index('--device')+1] == device
        target = Path(command[2]); np.save(target, np.zeros((8,10), np.float32))
        target.with_suffix('.json').write_text(json.dumps(dict(model='truefad_myotube',
            output='native_center_response', device=reported)), encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(boundary.subprocess, 'run', run)
    if reported != device:
        with pytest.raises(RuntimeError, match='selected CPU/GPU device'):
            boundary.run_centers(np.zeros((8,10), np.uint8))
    else:
        _, metadata = boundary.run_centers(np.zeros((8,10), np.uint8))
        assert metadata['device'] == device
