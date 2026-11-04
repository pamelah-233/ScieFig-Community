"""Publish the approved macOS artifact without replacing existing installers."""
import json
import os
from pathlib import Path
import re
import subprocess

repo = os.environ['GITHUB_REPOSITORY']
tag = 'community-v1.2.3'
old_tag = 'community-v1.2.2'
source_sha = '1557ee5fbbec1b3f25961678df4f4191f51b7818'
source_run = 41834374842
temp = Path(os.environ['RUNNER_TEMP'])
counter = 0

def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)

def api(path, method='GET', payload=None):
    global counter
    args = ['api', path, '--method', method]
    if payload is not None:
        counter += 1
        body = temp/f'release-input-{counter}.json'
        body.write_text(json.dumps(payload), encoding='utf-8')
        args += ['--input', str(body)]
    output = gh(*args)
    return json.loads(output) if output.strip() else None

def optional(path):
    process = subprocess.run(['gh', 'api', path], text=True, capture_output=True)
    if process.returncode == 0:
        return json.loads(process.stdout)
    if '404' in process.stderr:
        return None
    raise RuntimeError(process.stderr)

base = f'repos/{repo}'
baseline = api(f'{base}/releases/tags/{old_tag}')
before = {a['id']: (a['name'], a['size'], a['download_count']) for a in baseline['assets']}
run = api(f'{base}/actions/runs/{source_run}')
assert run['head_sha'] == source_sha and run['status'] == 'completed' and run['conclusion'] == 'success'
assert run['repository']['full_name'] == repo
subprocess.run(['gh', 'run', 'download', str(source_run), '--repo', repo,
                '--name', 'ScieFig-1.2.3-macOS-arm64', '--dir', str(temp/'macos')], check=True)
dmg = temp/'macos/ScieFig_1.2.3_macOS_arm64.dmg'
assert dmg.is_file() and dmg.stat().st_size > 500_000_000

if optional(f'{base}/git/ref/tags/{tag}') is None:
    try:
        api(f'{base}/git/refs', 'POST', {'ref': f'refs/tags/{tag}', 'sha': source_sha})
    except subprocess.CalledProcessError:
        assert optional(f'{base}/git/ref/tags/{tag}') is not None

def mac_section():
    return f"""## macOS

| 下载 / Download | 说明 / Details |
| --- | --- |
| [macOS DMG](https://github.com/{repo}/releases/download/{tag}/{dmg.name}) | Apple Silicon · macOS 14+ · 包含运行库与离线模型 / Runtimes and offline models included |

打开 DMG，将 ScieFig 拖入 Applications。无需激活。可在界面选择 CPU 或 Apple GPU。<br>
Open the DMG and drag ScieFig to Applications. No activation required. CPU and Apple GPU can be selected in the app.

"""

def updates_section():
    images = f'https://raw.githubusercontent.com/{repo}/{os.environ["GITHUB_SHA"]}/docs/showcase/1.2.3'
    return f"""## 本次更新 / Update

### 多标记表型与细胞邻域 / Multi-marker phenotypes and neighborhoods

在人扁桃体多通道荧光图上，复用 DAPI 核分割，按原始 CD3、CD8、CD45 信号识别标记组合；表型、细胞位置和邻域结果在同一工作台联动显示。<br>
Reuse DAPI nuclear segmentation to classify combinations of raw marker signals, with linked phenotype, cell and neighborhood results.

![扁桃体多标记表型工作台]({images}/tonsil-overview.png)

### 从核分割到距离分析 / From nuclear segmentation to distance analysis

1. 导入多通道原图，核对各独立通道。 / Import a multichannel image and check its channels.
2. 在「细胞核计数」选择核专用检测与 DAPI 通道，复核分割轮廓。 / Segment the DAPI channel and review the contours.
3. 在「多标记表型与邻域」设置 marker 阈值，再选择来源表型 A 和目标表型 B，计算最近邻距离及指定半径内的邻域组成。 / Set marker cutoffs, choose source A and target B, and calculate nearest-neighbor distances and radius-based neighborhood composition.
4. 查看细胞编号、原始 marker 均值及距离，导出 CSV。 / Inspect cell IDs, raw marker means and distances, then export CSV.

<details>
<summary>查看操作截图 / View the workflow screenshots</summary>

![DAPI 核分割操作]({images}/tonsil-nuclei.png)

![来源 A、目标 B 与最近邻计算]({images}/tonsil-nearest-neighbor.png)

</details>

修复分析完成时侧栏文字挤压；完整结果表按需加载，减少打开等待。继承 1.2.3 的 WB 裁剪、条带测量与显示修复。<br>
Fix sidebar text compression at analysis completion and load detailed result tables on demand. Includes the integrated 1.2.3 WB crop, strip measurement and display fixes.

演示图片：Anna Pascual-Reguant et al., *Multiplexed histology analyses for the phenotypic and spatial characterization of human innate lymphoid cells*, Nature Communications 12, 1737 (2021)。[原论文 / Paper](https://www.nature.com/articles/s41467-021-21994-8) · [MELC tonsil 原始数据 / Dataset](https://doi.org/10.5281/zenodo.3744152) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。选择同一视野的 DAPI、CD3、CD8、CD45 通道，截图中的伪彩和轮廓由 ScieFig 生成。标记阈值应结合实验对照校准，图示组合不等同于已验证的细胞类型。<br>
Demo data: the cited MELC tonsil dataset, CC BY 4.0. False colors and overlays were generated in ScieFig.

"""

def replace_section(body, heading, replacement):
    match = re.search(r'^'+re.escape(heading)+r'[ \t]*$', body, re.M)
    if not match:
        return body.rstrip()+'\n\n'+replacement
    following = re.search(r'^## ', body[match.end():], re.M)
    end = match.end()+following.start() if following else len(body)
    return body[:match.start()]+replacement+body[end:]

windows = """## Windows

Windows 1.2.3 正在构建，下载链接将在安装包完成后补充。<br>
Windows 1.2.3 is being built; download links will be added when its packages are ready.

"""
initial = '# ScieFig 1.2.3\n\n'+windows+mac_section()+updates_section()
release = optional(f'{base}/releases/tags/{tag}')
if release is None:
    try:
        release = api(f'{base}/releases', 'POST', dict(tag_name=tag, target_commitish=source_sha,
                      name='ScieFig 1.2.3', body=initial, draft=True, prerelease=False))
    except subprocess.CalledProcessError:
        release = optional(f'{base}/releases/tags/{tag}')
        if release is None:
            raise

# Do not clobber an existing asset: its download count belongs to that object.
existing = next((a for a in release['assets'] if a['name'] == dmg.name), None)
if existing:
    assert existing['state'] == 'uploaded' and existing['size'] == dmg.stat().st_size
else:
    subprocess.run(['gh', 'release', 'upload', tag, str(dmg), '--repo', repo], check=True)

# Re-read immediately before merging to preserve any Windows publication.
release = api(f'{base}/releases/{release["id"]}')
body = release.get('body') or initial
body = replace_section(body, '## macOS', mac_section())
body = replace_section(body, '## 本次更新 / Update', updates_section())
api(f'{base}/releases/{release["id"]}', 'PATCH',
    dict(name='ScieFig 1.2.3', body=body, draft=False, prerelease=False, make_latest='true'))

# Source archives generated by GitHub are not removable release assets.
release = api(f'{base}/releases/{release["id"]}')
for a in release['assets']:
    if re.search(r'(source|源码).*\.(zip|tar\.gz)$', a['name'], re.I):
        api(f'{base}/releases/assets/{a["id"]}', 'DELETE')

after_old = api(f'{base}/releases/tags/{old_tag}')
old_assets = {a['id']: a for a in after_old['assets']}
assert after_old['id'] == baseline['id']
for asset_id, (name, size, downloads) in before.items():
    a = old_assets[asset_id]
    assert (a['name'], a['size']) == (name, size)
    assert a['download_count'] >= downloads

final = api(f'{base}/releases/{release["id"]}')
asset = next(a for a in final['assets'] if a['name'] == dmg.name)
assert not final['draft'] and not final['prerelease']
assert asset['state'] == 'uploaded' and asset['size'] == dmg.stat().st_size
print(final['html_url'])
print(asset['name'], asset['size'], asset['id'])
print('1.2.2 release and installer asset identities preserved.')
