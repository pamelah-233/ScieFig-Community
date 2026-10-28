"""Store duplicate Torch DLL payloads once without changing installed paths."""
from pathlib import Path
import sys


def identical_bytes(first, second):
    if not second.is_file() or first.stat().st_size != second.stat().st_size:
        return False
    with first.open('rb') as a, second.open('rb') as b:
        while chunk := a.read(1024 * 1024):
            if chunk != b.read(len(chunk)):
                return False
        return not b.read(1)


def installer_entries(root):
    root=Path(root).resolve()
    worker=root/'cellpose/_internal'
    lines=[]
    for file in sorted(path for path in root.rglob('*') if path.is_file()):
        source=file
        twin=worker/'torch/lib'/file.name
        if file.parent==worker and file.suffix.lower()=='.dll' and identical_bytes(file,twin):
            source=twin
        relative_source=str(source.relative_to(root)).replace('/', '\\')
        relative_dir=str(file.parent.relative_to(root)).replace('/', '\\')
        destination='{app}'+('\\'+relative_dir if relative_dir!='.' else '')
        lines.append(f'Source: "{{#DistDir}}\\{relative_source}"; DestDir: "{destination}"; Flags: ignoreversion')
    return lines


if __name__=='__main__':
    lines=installer_entries(sys.argv[1])
    Path(sys.argv[2]).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Installer file entries: {len(lines)}; installed paths preserved.')
