"""Update selected Python modules in an existing PyInstaller onedir build.

The native bootloader, dependencies, models and inference worker are reused.
Run with the same Python minor version as the frozen application.
"""
import argparse
import marshal
from pathlib import Path
import shutil
import struct
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter


class _Archive(CArchiveWriter):
    def _write_entry(self, stream, entry):
        name, compressed, kind, data = entry
        return self._write_blob(stream, data, name, kind, compress=compressed)


def update_application(executable, root, modules):
    executable, root = Path(executable).resolve(), Path(root).resolve()
    if sys.platform == 'darwin':
        from PyInstaller.utils.osx import remove_signature_from_binary
        remove_signature_from_binary(str(executable))
    archive = CArchiveReader(str(executable))
    with executable.open('rb') as source:
        prefix = source.read(archive._start_offset)
        source.seek(archive._end_offset-archive._COOKIE_LENGTH)
        _, _, _, _, python_version, library = struct.unpack(archive._COOKIE_FORMAT, source.read(archive._COOKIE_LENGTH))
    if python_version != sys.version_info.major*100+sys.version_info.minor:
        raise RuntimeError('Use the same Python minor version as the original frozen build')
    pyz_name = next(name for name, entry in archive.toc.items() if entry[-1] == 'z')
    old_pyz = archive.open_embedded_archive(pyz_name)
    codes = {name:old_pyz.extract(name) for name in old_pyz.toc}
    entries = {name:(name, None if kind == 3 else
                       name.replace('.', '/')+('/__init__.py' if kind == 1 else '.py'), 'PYMODULE')
               for name,(kind,_,_) in old_pyz.toc.items()}
    for name in modules:
        source = root/(name.replace('.', '/')+'.py')
        codes[name] = compile(source.read_text(encoding='utf-8-sig'), str(source), 'exec')
        entries[name] = (name,str(source),'PYMODULE')
    with tempfile.TemporaryDirectory(prefix='ScieFig-refresh-', dir=executable.parent) as folder:
        folder=Path(folder)
        pyz=folder/'updated.pyz'
        ZlibArchiveWriter(str(pyz), entries.values(), code_dict=codes)
        new_entries=[]
        for name, (_,_,_,compressed,kind) in archive.toc.items():
            if name == 'main':
                data=marshal.dumps(compile((root/'main.py').read_text(encoding='utf-8-sig'),'main.py','exec'))
            elif name == pyz_name:
                data=pyz.read_bytes()
            else:
                data=archive.extract(name)
            new_entries.append((name,compressed,kind,data))
        new_entries.extend((name,0,'o',b'') for name in archive.options)
        pkg=folder/'updated.pkg'
        _Archive(str(pkg), new_entries, library.rstrip(b'\0').decode('utf-8'))
        target=folder/'updated-executable'
        with target.open('wb') as stream:
            stream.write(prefix)
            with pkg.open('rb') as source: shutil.copyfileobj(source, stream)
        shutil.copymode(executable,target)
        target.replace(executable)
    if sys.platform == 'darwin':
        from PyInstaller.utils.osx import fix_exe_for_code_signing
        fix_exe_for_code_signing(str(executable))
    elif sys.platform == 'win32':
        from PyInstaller.utils.win32.winutils import update_exe_pe_checksum
        update_exe_pe_checksum(str(executable))
    # Read back only the updated entries, without rescanning application dependencies.
    updated=CArchiveReader(str(executable)).open_embedded_archive(pyz_name)
    assert all(updated.extract(name) is not None for name in modules)
    print(f'Updated entry point and {len(modules)} modules; native dependencies and models reused.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('executable'); parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--module',action='append',default=[])
    args=parser.parse_args()
    update_application(args.executable,args.root,args.module)
