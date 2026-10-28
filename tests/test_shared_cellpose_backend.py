import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.image_analysis import cellpose_backend as backend


def fake_worker(monkeypatch, labels=None, model='cyto3', reported_device=None):
    calls = []
    monkeypatch.setattr(backend, 'worker_command', lambda: ['worker'])

    def run(command, **kwargs):
        source, target = Path(command[1]), Path(command[2])
        image = np.load(source, allow_pickle=False)
        calls.append((image.copy(), command))
        np.save(target, np.zeros(image.shape[:2], np.int32) if labels is None else labels)
        device = reported_device or (command[command.index('--device')+1] if '--device' in command else 'cpu')
        target.with_suffix('.json').write_text(json.dumps({'model': model, 'device': device, 'diameter_px': 45,
            'min_size_px': 5, 'cellpose_version': '3.1.1.3'}), encoding='utf-8')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backend.subprocess, 'run', run)
    return calls


def test_native_labels_raw_signal_and_model_parameters(monkeypatch):
    raw = np.arange(80, dtype=np.uint16).reshape(8, 10)
    original = raw.copy()
    labels = np.zeros((8, 10), np.int32)
    labels[1:4, 2:5] = 7
    calls = fake_worker(monkeypatch, labels, model='nuclei')
    actual, metadata = backend.run_cellpose_labels(raw, model='nuclei', diameter=45, min_size=5)
    np.testing.assert_array_equal(raw, original)
    np.testing.assert_array_equal(calls[0][0], raw)
    np.testing.assert_array_equal(actual, labels)
    assert metadata['model'] == 'nuclei'
    assert calls[0][1][-6:] == ['--model', 'nuclei', '--diameter', '45.0', '--min-size', '5']


@pytest.mark.parametrize('labels', [np.zeros((4, 5), np.int32), np.ones((8, 10), float),
                                     -np.ones((8, 10), np.int32)])
def test_reject_invalid_worker_labels(monkeypatch, labels):
    fake_worker(monkeypatch, labels)
    with pytest.raises(RuntimeError, match='label'):
        backend.run_cellpose_labels(np.zeros((8, 10), np.uint8))


def test_reject_wrong_worker_model(monkeypatch):
    fake_worker(monkeypatch, model='nuclei')
    with pytest.raises(RuntimeError, match='model'):
        backend.run_cellpose_labels(np.zeros((8, 10), np.uint8))


@pytest.mark.parametrize('image,options', [(np.zeros((2, 3, 4, 5)), {}),
    (np.full((8, 10), np.nan), {}), (np.zeros((8, 10)), {'diameter': 0}),
    (np.zeros((8, 10)), {'model': 'unknown'})])
def test_invalid_input_never_starts_worker(monkeypatch, image, options):
    calls = fake_worker(monkeypatch)
    with pytest.raises(ValueError):
        backend.run_cellpose_labels(image, **options)
    assert calls == []


def test_saved_label_map_is_exact_and_rejects_wrong_image_shape():
    labels = np.zeros((10, 12), np.int32)
    labels[2:6, 4:9] = 7
    packed = backend.pack_instance_labels(labels)
    np.testing.assert_array_equal(backend.unpack_instance_labels(packed, labels.shape), labels)
    with pytest.raises(ValueError, match='source image'):
        backend.unpack_instance_labels(packed, (12, 10))


@pytest.mark.skipif(sys.platform != 'darwin', reason='Apple MPS device selection')
@pytest.mark.parametrize('device', ['cpu', 'mps'])
def test_selected_device_reaches_worker_and_saved_metadata(monkeypatch, device):
    monkeypatch.setattr(backend, 'cellpose_device_preference', lambda: device)
    calls = fake_worker(monkeypatch)
    _, metadata = backend.run_cellpose_labels(np.zeros((8, 10), np.uint8))
    command = calls[0][1]
    assert command[command.index('--device')+1] == device
    assert metadata['device'] == device


@pytest.mark.skipif(sys.platform != 'darwin', reason='Apple MPS device selection')
def test_gpu_selection_never_accepts_silent_cpu_fallback(monkeypatch):
    monkeypatch.setattr(backend, 'cellpose_device_preference', lambda: 'mps')
    fake_worker(monkeypatch, reported_device='cpu')
    with pytest.raises(RuntimeError, match='selected CPU/GPU device'):
        backend.run_cellpose_labels(np.zeros((8, 10), np.uint8))
def test_requested_native_normalization_is_sent_and_verified(monkeypatch):
    monkeypatch.setattr(backend,'worker_command',lambda:['worker'])
    def run(command,**kwargs):
        requested=json.loads(command[command.index('--inference-options')+1])
        assert requested['normalization_tile_px']==128
        target=Path(command[2]);np.save(target,np.zeros((8,10),np.int32))
        target.with_suffix('.json').write_text(json.dumps(dict(model='cyto3',inference_options=requested,
            device=command[command.index('--device')+1] if '--device' in command else 'cpu')),encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(backend.subprocess,'run',run)
    _,metadata=backend.run_cellpose_labels(np.zeros((8,10),np.uint16),normalization_tile_px=128)
    assert metadata['inference_options']['normalization_tile_px']==128


def test_old_worker_cannot_silently_ignore_new_normalization(monkeypatch):
    fake_worker(monkeypatch)
    with pytest.raises(RuntimeError,match='inference settings'):
        backend.run_cellpose_labels(np.zeros((8,10),np.uint16),normalization_tile_px=128)


@pytest.mark.parametrize('device', ['cpu', 'mps' if sys.platform == 'darwin' else 'cuda'])
def test_selected_device_reaches_worker_and_saved_metadata(monkeypatch, device):
    monkeypatch.setattr(backend, 'cellpose_device_preference', lambda: device)
    calls = fake_worker(monkeypatch)
    _, metadata = backend.run_cellpose_labels(np.zeros((8, 10), np.uint8))
    command = calls[0][1]
    assert command[command.index('--device')+1] == device
    assert metadata['device'] == device


def test_gpu_selection_never_accepts_silent_cpu_fallback(monkeypatch):
    monkeypatch.setattr(backend, 'cellpose_device_preference', lambda: 'mps' if sys.platform == 'darwin' else 'cuda')
    fake_worker(monkeypatch, reported_device='cpu')
    with pytest.raises(RuntimeError, match='selected CPU/GPU device'):
        backend.run_cellpose_labels(np.zeros((8, 10), np.uint8))

