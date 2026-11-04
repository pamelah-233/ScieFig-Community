"""Add verified Windows installers to the existing 1.2.3 release."""
import argparse
import base64
import json
from pathlib import Path
import subprocess


def api(repo, path):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{repo}/{path}']))


def update_homepage(repo, release, windows_names, folder):
    readme = api(repo, 'contents/README.md')
    body = base64.b64decode(readme['content']).decode('utf-8')
    start = body.index('## 下载 / Download')
    end = body.index('\n## ', start + 5)
    base = f'https://github.com/{repo}/releases/download/community-v1.2.3/'
    mac = next(asset['browser_download_url'] for asset in release['assets']
               if 'macOS' in asset['name'] and asset['name'].endswith('.dmg'))
    downloads = f'''## 下载 / Download

### Windows 1.2.3

| 安装包 / Installer | 下载 / Download |
| --- | --- |
| 完整离线包，包含 CPU / NVIDIA GPU 运行库 / Full offline package | [Windows 完整安装包]({base}{windows_names['ScieFig_1.2.3_Setup.exe']}) |
| CPU 基础包 / CPU package | [Windows CPU 安装包]({base}{windows_names['ScieFig_1.2.3_CPU_Setup.exe']}) |
| 纯英文 CPU 包 / English-only CPU package | [Windows English CPU installer]({base}{windows_names['ScieFig_1.2.3_English_CPU_Setup.exe']}) |
| NVIDIA GPU 补充包 / GPU component | [现有 GPU 补充包 / Existing GPU component](https://github.com/{repo}/releases/download/community-v1.2.2/ScieFig_GPU_cellpose3_torch212_win64_v1.zip) |

CPU 基础包和纯英文 CPU 包可加装上方 NVIDIA GPU 补充包。<br>
The CPU and English CPU packages support the existing NVIDIA GPU component.

### macOS 1.2.3

**[macOS DMG · Apple Silicon]({mac})**

Mac 安装包包含运行库与离线模型，可在界面选择 CPU 或 Apple GPU。<br>
The Mac package includes runtimes and offline models, with CPU or Apple GPU selection.

[1.2.3 更新详情 / What's new](https://github.com/{repo}/releases/tag/community-v1.2.3) · [使用说明 / Getting started](docs/GETTING_STARTED.md) · [历史版本 / Previous releases](https://github.com/{repo}/releases)
'''
    updated = body[:start] + downloads + body[end:]
    updated = updated.replace('**Mac 界面与性能**', '**界面与性能**')
    if updated == body:
        return None
    patch = folder/'homepage-patch.json'
    patch.write_text(json.dumps(dict(message='Add verified Windows 1.2.3 downloads', sha=readme['sha'],
                                    content=base64.b64encode(updated.encode('utf-8')).decode('ascii'))), encoding='utf-8')
    result = json.loads(subprocess.check_output(['gh', 'api', '--method', 'PUT', f'repos/{repo}/contents/README.md',
                                                '--input', str(patch)]))
    checked = api(repo, 'contents/README.md?ref=' + result['commit']['sha'])
    assert base64.b64decode(checked['content']).decode('utf-8') == updated
    return result['commit']['sha']


def publish(repo, run_id, commit, folder):
    run = api(repo, f'actions/runs/{run_id}')
    assert run['conclusion'] == 'success' and run['head_sha'] == commit
    assert run['path'] == '.github/workflows/build-windows-1.2.3.yml'
    manifest = json.loads((folder/'windows-candidate.json').read_text(encoding='utf-8-sig'))
    assert manifest['commit'] == commit and str(manifest['run']) == str(run_id)
    assert manifest['version'] == '1.2.3' and manifest['upgrade'] == 'passed'
    expected = {
        'ScieFig_1.2.3_Setup.exe': 'ScieFig_1.2.3_Windows_Setup.exe',
        'ScieFig_1.2.3_CPU_Setup.exe': 'ScieFig_1.2.3_Windows_CPU_Setup.exe',
        'ScieFig_1.2.3_English_CPU_Setup.exe': 'ScieFig_1.2.3_Windows_English_CPU_Setup.exe',
    }
    sizes = {item['Name']: item['Length'] for item in manifest['packages']}
    assert set(sizes) == set(expected)
    for source in expected:
        assert (folder/source).stat().st_size == sizes[source]
    tag = 'community-v1.2.3'
    before = api(repo, f'releases/tags/{tag}')
    assert not before['draft']
    old_assets = {asset['id']: asset for asset in before['assets']}
    existing = {asset['name']: asset for asset in before['assets']}
    for source, destination in expected.items():
        asset = existing.get(destination)
        if asset:
            assert asset['size'] == sizes[source] and asset['state'] == 'uploaded'
            assert asset['created_at'] >= run['created_at']
            continue
        target = folder/destination
        (folder/source).rename(target)
        subprocess.run(['gh', 'release', 'upload', tag, str(target), '--repo', repo], check=True)

    # Read the latest notes after uploads, preserving any concurrent macOS edits.
    current = api(repo, f'releases/tags/{tag}')
    body = current['body'] or ''
    start = body.index('## Windows')
    end = body.index('## macOS', start)
    base = f'https://github.com/{repo}/releases/download/{tag}/'
    windows = f'''## Windows

| 下载 / Download | 说明 / Details |
| --- | --- |
| [Windows 完整安装包 / Full installer]({base}{expected['ScieFig_1.2.3_Setup.exe']}) | 包含离线模型、CPU 与 NVIDIA GPU 运行库 / Includes offline models, CPU and NVIDIA GPU runtimes |
| [Windows CPU 基础包 / CPU installer]({base}{expected['ScieFig_1.2.3_CPU_Setup.exe']}) | 包含离线模型与 CPU 运行库，可加装现有 GPU 扩展包 / Includes offline models and CPU runtime; supports the existing GPU component |
| [Windows 纯英文 CPU 包 / English CPU installer]({base}{expected['ScieFig_1.2.3_English_CPU_Setup.exe']}) | 纯英文界面，包含离线模型与 CPU 运行库，可加装 GPU 扩展包 / English-only interface, offline models and CPU runtime; supports the GPU component |
| [现有 GPU 补充包 / Existing GPU component](https://github.com/{repo}/releases/download/community-v1.2.2/ScieFig_GPU_cellpose3_torch212_win64_v1.zip) | 供 CPU 基础包和纯英文 CPU 包加装 NVIDIA GPU 加速，沿用兼容的 v1 运行库 / Adds NVIDIA GPU acceleration to the CPU packages using the compatible v1 runtime |

Windows x64。安装后可直接使用，无需激活。CPU 基础包和纯英文 CPU 包可加装上方 GPU 补充包，在应用顶部选择 GPU 后导入。<br>
Windows x64. Install and launch without activation. The CPU and English CPU packages support the existing GPU component, available from the GPU selector in the app.

已复核三个安装包的安装、启动、WB 流程及六个离线模型，并验证 1.2.2 原位升级和现有 GPU 组件兼容。<br>
All three installers were checked for installation, startup, the WB workflow and six offline models, including an in-place upgrade from 1.2.2 and compatibility with the existing GPU component.

<!-- windows-1.2.3 source={commit} build={run_id} -->

'''
    updated_body = body[:start] + windows + body[end:]
    patch = folder/'release-patch.json'
    patch.write_text(json.dumps({'body': updated_body}, ensure_ascii=False), encoding='utf-8')
    subprocess.run(['gh', 'api', '--method', 'PATCH', f'repos/{repo}/releases/{current["id"]}',
                    '--input', str(patch), '--silent'], check=True)
    after = api(repo, f'releases/tags/{tag}')
    assets = {asset['id']: asset for asset in after['assets']}
    for identifier, original in old_assets.items():
        assert assets[identifier]['name'] == original['name']
        assert assets[identifier]['size'] == original['size']
        assert assets[identifier]['download_count'] >= original['download_count']
    assert after['body'].endswith(body[end:])
    assert set(expected.values()).issubset({asset['name'] for asset in after['assets']})
    homepage_commit = update_homepage(repo, after, expected, folder)
    report = dict(release=after['html_url'], commit=commit, run=run_id,
                  homepage_commit=homepage_commit,
                  assets=[dict(id=asset['id'], name=asset['name'], size=asset['size'],
                               download_count=asset['download_count']) for asset in after['assets']])
    (folder/'publication-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repo')
    parser.add_argument('run_id')
    parser.add_argument('commit')
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    publish(args.repo, args.run_id, args.commit, args.folder)
