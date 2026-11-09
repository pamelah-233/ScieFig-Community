"""Check staged Windows applications and the existing optional GPU runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare_gpu(archive, descriptor_path, cache):
    from core.image_analysis import model_runtime
    descriptor = json.loads(descriptor_path.read_text(encoding='utf-8-sig'))
    if descriptor['id'] != model_runtime.RUNTIME_ID or archive.stat().st_size != descriptor['size']:
        raise ValueError('Published GPU component identity or size mismatch')
    # This archive executes native code. Check its published digest before import.
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != descriptor['sha256']:
        raise ValueError('Published GPU component digest mismatch')
    model_runtime.runtime_cache = lambda: cache
    installed = model_runtime.install_gpu_archive(archive)
    print('Imported existing GPU component:', installed, flush=True)


def code_names(code):
    import types
    names = set(code.co_names)
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            names.update(code_names(value))
    return names


def verify_startup(exe, output):
    output.mkdir(parents=True, exist_ok=True)
    smoke = []
    for name, arguments in (('main', []), ('wb', ['--wb-demo'])):
        marker = output / (name + '-ok.txt')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', SCIEFIG_RELEASE_SMOKE='1',
                   SCIEFIG_RELEASE_SMOKE_MARKER=str(marker),
                   SCIEFIG_RELEASE_SMOKE_LOG=str(output / (name + '-fault.txt')),
                   APPDATA=str(output / 'roaming'), LOCALAPPDATA=str(output / 'local'))
        Path(env['APPDATA']).mkdir(exist_ok=True)
        Path(env['LOCALAPPDATA']).mkdir(exist_ok=True)
        result = subprocess.run([str(exe), *arguments], cwd=exe.parent, env=env, capture_output=True, text=True, timeout=120)
        (output / (name + '-stdout.txt')).write_text(result.stdout, encoding='utf-8')
        (output / (name + '-stderr.txt')).write_text(result.stderr, encoding='utf-8')
        fault = output / (name + '-fault.txt')
        if result.returncode != 0 or not marker.is_file() or marker.read_text() != 'main-window-closed' or not fault.is_file() or fault.stat().st_size:
            details = fault.read_text(encoding='utf-8', errors='replace') if fault.exists() else ''
            raise RuntimeError(f'{exe.name} {name} startup check failed (exit {result.returncode}):\n'
                               f'{result.stderr}\n{details}')
        smoke.append(name)
    return smoke


def verify_application(folder, flavor, cache, output):
    import numpy as np
    from PyInstaller.archive.readers import CArchiveReader
    from core.image_analysis import model_runtime
    from core.image_analysis.nuclei_counting import object_rows
    from core.image_analysis.cellpose_backend import pack_instance_labels
    from core.image_analysis.multiplex_spatial import measure_multiplex

    output.mkdir(parents=True, exist_ok=True)
    exe = folder / 'ScieFig_R10.exe'
    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive(next(name for name, entry in archive.toc.items() if entry[-1] == 'z'))
    constants = {}
    exec(pyz.extract('core.constants'), constants)
    assert constants['SOFTWARE_VERSION'] == '1.2.5'
    if flavor == 'english':
        translations = {'__name__': 'candidate_i18n'}
        exec(pyz.extract('core.utils.i18n'), translations)
        assert set(translations['TRANSLATIONS']) == {'en'}
        try:
            translations['set_language']('zh')
        except ValueError:
            pass
        else:
            raise AssertionError('English build allows Chinese selection')
        names = code_names(pyz.extract('ui.main_window'))
        assert not names.intersection({'language_zh_button', 'language_en_button'})

    smoke = verify_startup(exe, output)

    bundled = folder / 'cellpose'
    model_runtime.runtime_cache = lambda: cache
    cpu_worker = model_runtime.worker_executable(bundled, 'cpu')
    gpu_worker = model_runtime.worker_executable(bundled, 'cuda')
    if flavor != 'full':
        assert model_runtime.valid_runtime(bundled, 'cpu')
        assert gpu_worker == cache / model_runtime.RUNTIME_ID / 'cuda' / model_runtime.EXECUTABLE
        assert not any(bundled.rglob('torch_cuda.dll'))
    else:
        assert model_runtime.valid_runtime(bundled, 'cuda')
        assert gpu_worker == cpu_worker
    descriptor = model_runtime.read_download_descriptor(folder / '_internal/resources')
    assert descriptor['id'] == model_runtime.RUNTIME_ID

    y, x = np.mgrid[:128, :128]
    image = sum(np.exp(-((y-cy)**2+(x-cx)**2)/80).astype(np.float32)
                for cy, cx in ((32, 32), (32, 96), (96, 32), (96, 96)))
    source = output / 'worker-input.npy'
    np.save(source, image)
    models = ('cyto3', 'nuclei', 'cyto3_dapi_reviewed_v1', 'cyto3_he_fiber_boundary_v1',
              'cyto3_laminin_final_v1', 'truefad_myotube')
    tested = []
    for model in models:
        target = output / (model + '.npy')
        result = subprocess.run([str(cpu_worker), str(source), str(target), '--model', model,
                                 '--device', 'cpu', '--min-size', '8'], capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr
        labels = np.load(target)
        metadata = json.loads(target.with_suffix('.json').read_text())
        assert labels.shape == image.shape and np.isfinite(labels).all()
        assert metadata['model'] == model and metadata['device'] == 'cpu'
        if model == 'nuclei':
            source_record = dict(measurement_uuid='candidate-nuclei', image_uid='candidate-image',
                                 channel_index=0, nuclei=object_rows(labels, image), instance_labels=pack_instance_labels(labels))
            spatial = measure_multiplex(np.stack([image, image * 2, image * .5]), source_record,
                                       markers=[dict(channel_index=1, name='Red', threshold=.1),
                                                dict(channel_index=2, name='Green', threshold=.1)])
            assert spatial['summary']['cells'] > 0
            (output / 'spatial.json').write_text(json.dumps(spatial), encoding='utf-8')
        tested.append(model)
    # Exercise the old optional worker through the unchanged CPU-package routing.
    target = output / 'optional-worker.npy'
    result = subprocess.run([str(gpu_worker), str(source), str(target), '--model', 'nuclei',
                             '--device', 'cpu', '--min-size', '8'], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    assert json.loads(target.with_suffix('.json').read_text())['model'] == 'nuclei'
    report = dict(version='1.2.5', flavor=flavor, startup=smoke, models=tested,
                  optional_gpu_component=model_runtime.RUNTIME_ID, optional_worker_cpu_execution=True,
                  cuda_execution='Requires the separate NVIDIA hardware check')
    (output / 'checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare-gpu')
    prepare.add_argument('archive', type=Path)
    prepare.add_argument('descriptor', type=Path)
    prepare.add_argument('cache', type=Path)
    startup = sub.add_parser('verify-startup')
    startup.add_argument('executable', type=Path)
    startup.add_argument('output', type=Path)
    verify = sub.add_parser('verify-app')
    verify.add_argument('folder', type=Path)
    verify.add_argument('flavor', choices=('full', 'cpu', 'english'))
    verify.add_argument('cache', type=Path)
    verify.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare-gpu':
        prepare_gpu(args.archive.resolve(), args.descriptor.resolve(), args.cache.resolve())
    elif args.command == 'verify-startup':
        print(verify_startup(args.executable.resolve(), args.output.resolve()), flush=True)
    else:
        verify_application(args.folder.resolve(), args.flavor, args.cache.resolve(), args.output.resolve())
