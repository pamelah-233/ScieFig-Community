"""Add English artifacts and organize release links without replacing existing assets."""
import json
import os
from pathlib import Path
import subprocess
import urllib.request


def api(path, data=None):
    request = urllib.request.Request(
        'https://api.github.com/repos/'+os.environ['GITHUB_REPOSITORY']+'/'+path,
        data=None if data is None else json.dumps(data).encode(),
        method='GET' if data is None else 'PATCH',
        headers={'Authorization': 'Bearer '+os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                 'User-Agent': 'ScieFig-release'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    tag = 'community-v1.2.2'
    before = api('releases/tags/'+tag)
    if before['id'] != 428009536 or before['draft']:
        raise RuntimeError('Unexpected release identity')
    original = {asset['id']: asset for asset in before['assets']}
    names = {asset['name']: asset for asset in before['assets']}
    for name in ('ScieFig_1.2.2_English_CPU_Setup.exe', 'ScieFig_1.2.2_English_source.zip'):
        path = Path('artifacts/english')/name
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError('Missing English artifact: '+name)
        if name in names:
            if names[name]['size'] != path.stat().st_size:
                raise RuntimeError('Existing asset differs; refusing replacement: '+name)
            print('Preserved existing English asset:', name)
        else:
            subprocess.run(['gh', 'release', 'upload', tag, str(path), '--repo',
                            os.environ['GITHUB_REPOSITORY']], check=True)
    base = 'https://github.com/'+os.environ['GITHUB_REPOSITORY']
    download = base+'/releases/download/'+tag+'/'
    body = f'''# ScieFig 1.2.2

## Windows

| 下载 / Download | 说明 / Details |
| --- | --- |
| [完整安装包 / Complete installer]({download}ScieFig_1.2.2_Setup.exe) | 中英文界面，包含 CPU 与 NVIDIA GPU 环境 / Chinese and English UI, CPU and NVIDIA GPU runtimes included |
| [基础安装包 / Basic installer]({download}ScieFig_1.2.2_CPU_Setup.exe) | 中英文界面，全部功能与模型，使用 CPU / Chinese and English UI, all features and models, CPU runtime |
| [纯英文基础安装包 / English-only basic installer]({download}ScieFig_1.2.2_English_CPU_Setup.exe) | 固定英文，无语言切换按钮；全部功能与模型，使用 CPU / English only, no language switch; all features and models, CPU runtime |
| [NVIDIA GPU 加速组件 / GPU component]({download}ScieFig_GPU_cellpose3_torch212_win64_v1.zip) | 适用于上述两个基础安装包；已有组件可继续使用 / Optional for both basic installers; existing component remains compatible |

**完整安装包已含 GPU 环境，无需重复安装增补组件。两个基础安装包均可选装同一个 GPU 组件。**

**The complete installer already includes the GPU runtime. Both basic installers support the same optional GPU component.**

## macOS

| 下载 / Download | 说明 / Details |
| --- | --- |
| [macOS DMG]({download}ScieFig_1.2.2_macOS_arm64.dmg) | Apple Silicon · macOS 14+ |
| [macOS PKG]({download}ScieFig_1.2.2_macOS_arm64.pkg) | Apple Silicon · macOS 14+ |

## 源码与说明 / Source and documentation

- [中英文版对应源码 / Bilingual source]({base}/tree/community-v1.2.2)
- [纯英文版对应源码 / English-only source]({download}ScieFig_1.2.2_English_source.zip)
- [使用说明 / User guide]({base}/blob/main/docs/GETTING_STARTED.md)

## 本次更新 / Update

新增纯英文 Windows 基础安装包，移除语言切换入口，兼容现有 NVIDIA GPU 增补包。继续提供包含 CPU 与 GPU 环境的完整离线安装包。

Added an English-only Windows basic installer without language switching, compatible with the existing NVIDIA GPU component. The complete offline installer with CPU and GPU runtimes remains available.
'''
    api('releases/'+str(before['id']), {'body': body})
    after = api('releases/tags/'+tag)
    current = {asset['id']: asset for asset in after['assets']}
    assert after['id'] == before['id']
    for asset_id, asset in original.items():
        updated = current[asset_id]
        assert (updated['name'], updated['size']) == (asset['name'], asset['size'])
        assert updated['download_count'] >= asset['download_count']
    evidence = {'release_id': after['id'], 'url': after['html_url'],
                'before': [{'id': a['id'], 'name': a['name'], 'download_count': a['download_count']} for a in before['assets']],
                'after': [{'id': a['id'], 'name': a['name'], 'download_count': a['download_count'], 'size': a['size']} for a in after['assets']]}
    Path('release-preservation.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
