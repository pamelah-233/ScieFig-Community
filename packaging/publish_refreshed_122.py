"""Promote validated replacement assets without transferring them between releases."""
import json
import os
import urllib.error
import urllib.request

BASE='https://api.github.com/repos/'+os.environ['GITHUB_REPOSITORY']
TOKEN=os.environ['GH_TOKEN']


def api(method,path,data=None):
    body=None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(BASE+'/'+path,data=body,method=method,headers={
        'Authorization':'Bearer '+TOKEN,'Accept':'application/vnd.github+json',
        'Content-Type':'application/json','User-Agent':'ScieFig-release'})
    with urllib.request.urlopen(request,timeout=60) as response:
        raw=response.read()
        return json.loads(raw) if raw else None


def main():
    tag='community-v1.2.2'
    draft=next(r for r in api('GET','releases?per_page=100') if r['tag_name']=='build-refresh-v1.2.2')
    old=api('GET','releases/tags/'+tag)
    if not draft['draft'] or draft['target_commitish'] != os.environ['GITHUB_SHA'] or old.get('immutable'):
        raise RuntimeError('Unexpected or immutable release state')
    required={'ScieFig_1.2.2_Setup.exe','ScieFig_1.2.2_CPU_Setup.exe',
              'ScieFig_GPU_cellpose3_torch212_win64_v1.zip','ScieFig_1.2.2_Windows_source.zip',
              'ScieFig_1.2.2_macOS_arm64.dmg','ScieFig_1.2.2_macOS_arm64.pkg',
              'ScieFig_1.2.2_macOS_source.zip','DEPENDENCY_SOURCES.json'}
    assets={a['name']:a for a in draft['assets']}
    if not required.issubset(assets) or any(assets[n]['state']!='uploaded' or assets[n]['size']<=0 for n in required):
        raise RuntimeError('Required verified packages are missing')
    expected=json.loads(os.environ['EXPECTED_REFS'])
    for name,sha in expected.items():
        if api('GET','git/ref/tags/'+name)['object']['sha']!=sha:
            raise RuntimeError('Source tag changed during build; publication stopped')
    # Preserve the product description; only the new runtime-distribution note is added.
    marker='<!-- gpu-split-1.2.2 -->'
    body=old['body'].split(marker)[0].rstrip()+'\n\n'+marker+'\n'+open('docs/COMMUNITY_RELEASE_NOTES.md',encoding='utf-8').read()
    retired='retired-1.2.2-'+str(old['id'])
    promoted=False
    try:
        api('PATCH',f'releases/{old["id"]}',dict(tag_name=retired,draft=True))
        for name in expected:
            api('PATCH','git/refs/tags/'+name,dict(sha=os.environ['GITHUB_SHA'],force=True))
        api('PATCH',f'releases/{draft["id"]}',dict(tag_name=tag,name='ScieFig 1.2.2',
            target_commitish=os.environ['GITHUB_SHA'],body=body,draft=False,make_latest='true'))
        live=api('GET','releases/tags/'+tag)
        if live['id']!=draft['id'] or live['draft']:
            raise RuntimeError('Replacement release not visible')
        promoted=True
    finally:
        if not promoted:
            # Restore the previous publication if promotion did not complete.
            try:
                live=api('GET','releases/tags/'+tag)
                promoted=live['id']==draft['id'] and not live['draft']
            except urllib.error.HTTPError:
                pass
            if not promoted:
                for name,sha in expected.items():
                    if api('GET','git/ref/tags/'+name)['object']['sha']==os.environ['GITHUB_SHA']:
                        api('PATCH','git/refs/tags/'+name,dict(sha=sha,force=True))
                api('PATCH',f'releases/{old["id"]}',dict(tag_name=tag,draft=False))
    if promoted:
        api('DELETE',f'releases/{old["id"]}')
        for temporary in (retired,'build-refresh-v1.2.2'):
            try:
                reference=api('GET','git/ref/tags/'+temporary)
                if reference['object']['sha'] in {*expected.values(),os.environ['GITHUB_SHA']}:
                    api('DELETE','git/refs/tags/'+temporary)
            except urllib.error.HTTPError as exc:
                if exc.code!=404: raise
        print('ScieFig 1.2.2 replacement published; superseded packages removed.')


if __name__=='__main__': main()
