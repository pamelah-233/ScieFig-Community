"""Build the self-contained Apple Silicon preview using prepared local models."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('dist/macos'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    worker = args.worker_dir.resolve()
    if not (worker / 'ScieFig_Cellpose').is_file():
        parser.error('A built macOS Cellpose worker is required.')
    target = args.output.resolve() / 'ScieFig.app'
    if target.exists():
        parser.error(f'Refusing to overwrite an installed application: {target}')
    build = root / 'build/macos'
    staging = build / '.app-staging.noindex'
    iconset = build / 'ScieFig.iconset'
    iconset.mkdir(parents=True, exist_ok=True)
    source_icon = root / 'resources/icons/sciefig_app_icon.png'
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            filename = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
            run('/usr/bin/sips', '-z', size * scale, size * scale, source_icon,
                '--out', iconset / filename)
    run('/usr/bin/iconutil', '-c', 'icns', iconset, '-o', build / 'ScieFig.icns')
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', staging, '--workpath', build / 'app-work',
        root / 'packaging/ScieFig-macOS.spec')
    app = staging / 'ScieFig.app'
    run(sys.executable, root / 'packaging/collect_cellpose_licenses.py',
        app / 'Contents/Resources/THIRD_PARTY_LICENSES')
    shutil.copytree(worker, app / 'Contents/Resources/cellpose')
    run('/usr/bin/codesign', '--force', '--sign', '-', app)
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.move(str(app), str(target))
    print(target)


if __name__ == '__main__':
    main()
