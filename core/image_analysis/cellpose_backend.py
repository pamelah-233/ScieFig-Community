"""Shared offline Cellpose transport; image and instance labels use source pixels."""
import json
import base64
import zlib
from pathlib import Path
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar

import numpy as np

_analysis_device = ContextVar('sciefig_analysis_device', default=None)


def cellpose_device_preference():
    selected = _analysis_device.get()
    if selected is not None:
        return selected
    from PyQt6.QtCore import QSettings
    device = QSettings('ScieFig', 'ScieFig').value('cellpose/device', 'mps' if sys.platform == 'darwin' else 'cpu')
    return device if device in {'cpu', 'mps' if sys.platform == 'darwin' else 'cuda'} else 'cpu'


def set_cellpose_device_preference(device):
    if device not in {'cpu', 'mps' if sys.platform == 'darwin' else 'cuda'}:
        raise ValueError('Choose CPU or the platform GPU (MPS on macOS, CUDA on Windows).')
    from PyQt6.QtCore import QSettings
    settings = QSettings('ScieFig', 'ScieFig')
    settings.setValue('cellpose/device', device)
    settings.sync()


@contextmanager
def cellpose_device_scope(device):
    """Keep every model pass of an already-submitted analysis on one device."""
    token = _analysis_device.set(device)
    try:
        yield
    finally:
        _analysis_device.reset(token)


def device_probe_command(folder, device):
    """Check the actual bundled inference runtime, not just a driver listing."""
    source, target = Path(folder)/'probe.npy', Path(folder)/'labels.npy'
    y, x = np.mgrid[:64, :64]
    image = (200*np.exp(-((x-32)**2+(y-32)**2)/64)).astype(np.float32)
    np.save(source, image, allow_pickle=False)
    with cellpose_device_scope(device):
        command = worker_command()
    return (command+[str(source), str(target), '--device', device,
            '--model', 'cyto3', '--diameter', '16', '--min-size', '5'], target)


def prepare_cellpose_channel(image, mode='raw'):
    """Optional detection-only normalization; callers measure original channel values."""
    array=np.asarray(image)
    if mode=='raw':return array.copy(),dict(mode='raw')
    if mode!='local_contrast' or array.ndim!=2 or not np.isfinite(array).all():
        raise ValueError('Invalid Cellpose channel input mode or image.')
    import cv2
    low,high=np.percentile(array,[1,99.8])
    scaled=np.uint8(np.clip((array.astype(float)-low)/max(float(high-low),1e-8),0,1)*255)
    prepared=cv2.createCLAHE(clipLimit=2.,tileGridSize=(8,8)).apply(scaled)
    return prepared,dict(mode=mode,percentiles=[1,99.8],range_values=[float(low),float(high)],
                         clip_limit=2.,tile_grid=[8,8])


def pack_instance_labels(labels):
    """JSON-safe exact labels for saved results; no inference on project reopen."""
    array = np.asarray(labels, dtype='<i4')
    return {'encoding': 'zlib-int32-le', 'shape': list(array.shape),
            'data': base64.b64encode(zlib.compress(array.tobytes())).decode('ascii')}


def unpack_instance_labels(payload, image_shape):
    if payload.get('encoding') != 'zlib-int32-le' or tuple(payload.get('shape', ())) != tuple(image_shape):
        raise ValueError('Saved Cellpose labels do not match the source image.')
    size = int(np.prod(image_shape)) * 4
    decompressor = zlib.decompressobj()
    data = decompressor.decompress(base64.b64decode(payload['data'], validate=True), size + 1)
    if len(data) != size or not decompressor.eof or decompressor.unused_data:
        raise ValueError('Invalid saved Cellpose label data.')
    labels = np.frombuffer(data, dtype='<i4').reshape(image_shape).copy()
    if np.any(labels < 0):
        raise ValueError('Invalid saved Cellpose label IDs.')
    return labels


def worker_root(root=None):
    if root is None:
        if getattr(sys, 'frozen', False):
            root = (Path(sys.executable).parent.parent / 'Resources' if sys.platform == 'darwin'
                    else Path(sys.executable).parent)
        else:
            root = Path(__file__).resolve().parents[2] / 'dist'
    return Path(root)/'cellpose'


def worker_command(root=None):
    folder = worker_root(root)
    if sys.platform == 'win32':
        from .model_runtime import worker_executable
        executable = worker_executable(folder, cellpose_device_preference())
    else:
        executable = folder/'ScieFig_Cellpose'
    if not executable.is_file():
        raise FileNotFoundError('Cellpose runtime is missing. Please reinstall the complete ScieFig installer.')
    return [str(executable)]


def run_cellpose_labels(image, *, model='cyto3', diameter=30., min_size=60,
                        normalization_tile_px=0, invert=False, niter=200,
                        flow_threshold=.4, cellprob_threshold=0.):
    image = np.asarray(image)
    if (image.ndim != 2 and not (image.ndim == 3 and image.shape[-1] == 3)) or not image.size:
        raise ValueError('Cellpose requires a nonempty 2D channel or H×W×3 RGB image.')
    if image.dtype.kind not in 'uif' or not np.isfinite(image).all():
        raise ValueError('Cellpose requires finite numeric image values.')
    diameter = float(diameter)
    if model not in {'cyto3', 'nuclei', 'cyto3_dapi_reviewed_v1', 'cyto3_he_fiber_boundary_v1', 'cyto3_laminin_final_v1'} or not np.isfinite(diameter) or diameter <= 0 or int(min_size) < 1:
        raise ValueError('Invalid Cellpose model, diameter or minimum area.')
    if (not np.isfinite([normalization_tile_px, niter, flow_threshold, cellprob_threshold]).all()
            or int(normalization_tile_px) != normalization_tile_px or normalization_tile_px < 0
            or int(niter) != niter or niter < 0 or flow_threshold < 0 or not isinstance(invert, (bool, np.bool_))):
        raise ValueError('Invalid Cellpose normalization, dynamics or threshold setting.')
    options = dict(normalization_tile_px=int(normalization_tile_px), invert=bool(invert),
                   niter=int(niter), flow_threshold=float(flow_threshold), cellprob_threshold=float(cellprob_threshold))
    defaults = dict(normalization_tile_px=0, invert=False, niter=200, flow_threshold=.4, cellprob_threshold=0.)
    advanced = options != defaults
    extra = ['--inference-options', json.dumps(options)] if advanced else []
    command = worker_command()
    device = cellpose_device_preference()
    device_options = ['--device', device]
    with tempfile.TemporaryDirectory(prefix='ScieFig-cellpose-') as folder:
        source, target = Path(folder) / 'image.npy', Path(folder) / 'labels.npy'
        np.save(source, image, allow_pickle=False)
        result = subprocess.run(
            command + [str(source), str(target)] + device_options + ['--model', model, '--diameter', str(diameter),
                       '--min-size', str(int(min_size))] + extra,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('Cellpose failed: ' + (result.stderr or result.stdout)[-1800:])
        metadata_path = target.with_suffix('.json')
        if not metadata_path.is_file():
            raise RuntimeError('Please update the bundled Cellpose runtime; it does not report the selected model.')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if metadata.get('model') != model:
            raise RuntimeError('Cellpose returned a different model than requested.')
        if advanced and metadata.get('inference_options') != options:
            raise RuntimeError('Cellpose runtime did not apply the requested inference settings.')
        if device is not None and metadata.get('device') != device:
            raise RuntimeError('Cellpose did not use the selected CPU/GPU device. Update the bundled runtime.')
        labels = np.load(target, allow_pickle=False)
        if (labels.shape != image.shape[:2] or labels.dtype.kind not in 'ui'
                or np.any(labels < 0) or np.any(labels > np.iinfo(np.int32).max)):
            raise RuntimeError('Cellpose returned invalid native-resolution instance labels.')
        return labels.astype(np.int32, copy=False), metadata
