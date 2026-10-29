"""Versioned optional Windows GPU workers; bundled CPU and full offline workers remain usable."""
import hashlib
import json
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

RUNTIME_ID = 'cellpose3-torch212-win64-v1'
EXECUTABLE = 'ScieFig_Cellpose.exe'


def runtime_cache():
    from PyQt6.QtCore import QStandardPaths
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / 'ScieFig/model-runtimes'


def installed_gpu():
    folder = runtime_cache() / RUNTIME_ID / 'cuda'
    return folder if valid_runtime(folder, 'cuda') else None


def valid_runtime(folder, device):
    try:
        data = json.loads((Path(folder)/'runtime.json').read_text(encoding='utf-8'))
        return (data['id'] == RUNTIME_ID and data['device'] == device
                and (Path(folder)/EXECUTABLE).is_file())
    except (OSError, ValueError, KeyError):
        return False


def optional_gpu_required(bundled):
    return valid_runtime(bundled, 'cpu') and installed_gpu() is None


def worker_executable(bundled, device):
    bundled = Path(bundled)
    if device == 'cuda' and valid_runtime(bundled, 'cpu'):
        gpu = installed_gpu()
        if gpu is None:
            raise FileNotFoundError('GPU component is not installed. Select GPU in the toolbar to install it, or select CPU.')
        return gpu / EXECUTABLE
    return bundled / EXECUTABLE


def read_download_descriptor(resources):
    descriptor = json.loads((Path(resources)/'model_runtime.json').read_text(encoding='utf-8'))
    if descriptor.get('id') != RUNTIME_ID:
        raise ValueError('GPU runtime descriptor version mismatch')
    return descriptor


def _cancelled(cancel):
    if cancel():
        raise InterruptedError('Installation cancelled')


def download_gpu(descriptor, destination, progress, cancel):
    url, expected = descriptor.get('url', ''), descriptor.get('sha256', '')
    if not url.startswith('https://') or len(expected) != 64 or descriptor.get('size', 0) <= 0:
        raise ValueError('GPU component download is not configured for this build. Import a local component package instead.')
    request = urllib.request.Request(url, headers={'User-Agent': 'ScieFig'})
    digest = hashlib.sha256()
    count = 0
    with urllib.request.urlopen(request, timeout=20) as response, Path(destination).open('wb') as output:
        if not response.url.startswith('https://'):
            raise ValueError('GPU component download redirected to an insecure URL')
        while True:
            _cancelled(cancel)
            chunk = response.read(1024*1024)
            if not chunk:
                break
            count += len(chunk)
            if count > descriptor['size']:
                raise ValueError('GPU component exceeds its expected size')
            digest.update(chunk)
            output.write(chunk)
            progress('download', count, descriptor['size'])
    if count != descriptor['size'] or digest.hexdigest() != expected:
        raise ValueError('GPU component integrity check failed; no runtime was installed')


def _safe_path(name):
    path = PurePosixPath(name)
    if ('\\' in name or ':' in name or path.is_absolute() or not path.parts
            or any(part in {'.', '..'} for part in path.parts)):
        raise ValueError('Unsafe GPU package path')
    return path


def install_gpu_archive(archive, progress=lambda *args: None, cancel=lambda: False):
    cache = runtime_cache()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gpu-install-', dir=cache) as temporary:
        stage = Path(temporary)/'runtime'
        stage.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            paths = set()
            total = sum(member.file_size for member in members)
            if total > 10*1024**3 or len(members) > 20000:
                raise ValueError('GPU package is too large')
            for member in members:
                path = _safe_path(member.filename)
                key = str(path).casefold()
                if key in paths or stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError('Duplicate or linked GPU package path')
                paths.add(key)
            metadata = json.loads(bundle.read('runtime.json'))
            if metadata.get('id') != RUNTIME_ID or metadata.get('device') != 'cuda':
                raise ValueError('GPU package is incompatible with this application')
            done = 0
            for member in members:
                _cancelled(cancel)
                target = stage.joinpath(*_safe_path(member.filename).parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open('wb') as output:
                    while chunk := source.read(1024*1024):
                        _cancelled(cancel)
                        output.write(chunk)
                        done += len(chunk)
                        progress('install', done, total)
            for alias, original in metadata.get('copies', {}).items():
                target = stage.joinpath(*_safe_path(alias).parts)
                source = stage.joinpath(*_safe_path(original).parts)
                if target.exists() or not source.is_file():
                    raise ValueError('Invalid shared GPU library mapping')
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    target.hardlink_to(source)
                except OSError:
                    shutil.copyfile(source, target)
        if not valid_runtime(stage, 'cuda'):
            raise ValueError('GPU package has no valid inference executable')
        _cancelled(cancel)
        destination = cache/RUNTIME_ID/'cuda'
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if valid_runtime(destination, 'cuda'):
                return destination
            raise ValueError('An incomplete GPU runtime already exists at '+str(destination))
        stage.rename(destination)
        return destination
