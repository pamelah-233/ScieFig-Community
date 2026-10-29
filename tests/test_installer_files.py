from pathlib import Path
import importlib.util


def entries_module():
    source=Path(__file__).resolve().parents[1]/'packaging/installer_files.py'
    spec=importlib.util.spec_from_file_location('installer_files',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_identical_torch_dlls_share_one_source_but_keep_both_destinations(tmp_path):
    root=tmp_path/'cellpose/_internal';lib=root/'torch/lib';lib.mkdir(parents=True)
    (root/'c10.dll').write_bytes(b'library'*100)
    (lib/'c10.dll').write_bytes(b'library'*100)
    lines=entries_module().installer_entries(tmp_path)
    assert len(lines)==2
    assert all('Source: "{#DistDir}\\cellpose\\_internal\\torch\\lib\\c10.dll";' in line for line in lines)
    assert any('DestDir: "{app}\\cellpose\\_internal";' in line for line in lines)
    assert any('DestDir: "{app}\\cellpose\\_internal\\torch\\lib";' in line for line in lines)


def test_same_sized_different_files_are_not_merged(tmp_path):
    root=tmp_path/'cellpose/_internal';lib=root/'torch/lib';lib.mkdir(parents=True)
    (root/'c10.dll').write_bytes(b'abcd')
    (lib/'c10.dll').write_bytes(b'abce')
    (tmp_path/'ScieFig_R10.exe').write_bytes(b'app')
    lines=entries_module().installer_entries(tmp_path)
    assert len(lines)==3
    assert any('Source: "{#DistDir}\\cellpose\\_internal\\c10.dll";' in line for line in lines)
    assert any('DestDir: "{app}";' in line for line in lines)


def test_cpu_override_keeps_application_and_excludes_full_gpu_payload(tmp_path):
    app=tmp_path/'app';cpu=tmp_path/'cpu'
    (app/'cellpose').mkdir(parents=True);cpu.mkdir()
    (app/'ScieFig_R10.exe').write_bytes(b'app')
    (app/'cellpose/torch_cuda.dll').write_bytes(b'gpu')
    (cpu/'ScieFig_Cellpose.exe').write_bytes(b'cpu')
    (cpu/'runtime.json').write_text('cpu')
    lines=entries_module().installer_entries(app,cpu)
    assert len(lines)==3
    assert not any('torch_cuda' in line for line in lines)
    assert any('DestDir: "{app}\\cellpose"' in line and 'ScieFig_Cellpose.exe' in line for line in lines)
