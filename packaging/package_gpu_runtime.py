"""Bundle a standalone GPU worker once and generate the matching download descriptor."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from installer_files import identical_bytes

RUNTIME_ID = 'cellpose3-torch212-win64-v1'


def package_runtime(source, output, url):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not (source/'ScieFig_Cellpose.exe').is_file():
        raise FileNotFoundError('Frozen GPU worker not found')
    if output.is_relative_to(source):
        raise ValueError('Output must be outside the runtime directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError('GPU archive already exists')
    copies = {}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_LZMA) as bundle:
        for path in sorted(source.rglob('*')):
            if not path.is_file() or path.name == 'runtime.json':
                continue
            relative = path.relative_to(source).as_posix()
            twin = source/'_internal/torch/lib'/path.name
            if path.parent == source/'_internal' and path.suffix.lower() == '.dll' and identical_bytes(path, twin):
                copies[relative] = twin.relative_to(source).as_posix()
                continue
            bundle.write(path, relative)
        bundle.writestr('runtime.json', json.dumps(dict(id=RUNTIME_ID, device='cuda', copies=copies)))
    # The downloaded archive executes native code; this digest binds it to the shipped descriptor.
    digest = hashlib.sha256()
    with output.open('rb') as stream:
        while chunk := stream.read(1024*1024): digest.update(chunk)
    descriptor = dict(id=RUNTIME_ID, url=url, size=output.stat().st_size, sha256=digest.hexdigest())
    output.with_suffix('.json').write_text(json.dumps(descriptor, indent=2)+'\n', encoding='utf-8')
    print(f'{output}: {descriptor["size"]:,} bytes; {len(copies)} shared libraries stored once')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source'); parser.add_argument('output'); parser.add_argument('--url', required=True)
    args = parser.parse_args()
    package_runtime(args.source, args.output, args.url)
