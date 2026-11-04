import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class WindowsPublicationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('windows_publication', ROOT/'packaging/publish_windows_123.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        names = ('ScieFig_1.2.3_Setup.exe', 'ScieFig_1.2.3_CPU_Setup.exe', 'ScieFig_1.2.3_English_CPU_Setup.exe')
        for name in names:
            (self.folder/name).write_bytes(b'installer')
        self.manifest = dict(commit='build-commit', run='1', version='1.2.3', upgrade='passed',
                             packages=[dict(Name=name, Length=9) for name in names])
        (self.folder/'windows-candidate.json').write_text(json.dumps(self.manifest))
        self.mac_notes = '## macOS\n\nKeep macOS details and screenshots.\n'
        self.release = dict(id=10, draft=False, body='## Windows\n\nPending.\n\n'+self.mac_notes,
                            html_url='https://github.com/example/repo/releases/tag/community-v1.2.3',
                            assets=[dict(id=1, name='ScieFig_1.2.3_macOS_arm64.dmg', size=100, download_count=23,
                                         browser_download_url='https://example.test/mac.dmg')])
        homepage = '# ScieFig\n\n## 下载 / Download\n\nPending.\n\n## Features\n\nKeep features.\n'
        self.readme = dict(sha='original-readme', content=base64.b64encode(homepage.encode()).decode())
        self.uploads = []

    def api(self, repo, path):
        if path == 'actions/runs/1':
            return dict(conclusion='success', head_sha='build-commit', path='.github/workflows/build-windows-1.2.3.yml',
                        created_at='2026-11-04T00:00:00Z')
        if path == 'releases/tags/community-v1.2.3':
            return deepcopy(self.release)
        if path.startswith('contents/README.md'):
            return deepcopy(self.readme)
        raise AssertionError(path)

    def fake_run(self, command, **kwargs):
        if command[1:3] == ['release', 'upload']:
            path = Path(command[4])
            self.uploads.append(path.name)
            self.release['assets'].append(dict(id=len(self.release['assets'])+1, name=path.name,
                                               size=path.stat().st_size, download_count=0,
                                               state='uploaded', created_at='2026-11-04T01:00:00Z'))
        else:
            payload = json.loads(Path(command[command.index('--input')+1]).read_text(encoding='utf-8'))
            self.release['body'] = payload['body']
            self.release['assets'][0]['download_count'] += 1

    def fake_output(self, command, **kwargs):
        payload = json.loads(Path(command[command.index('--input')+1]).read_text(encoding='utf-8'))
        self.assertEqual(payload['sha'], 'original-readme')
        self.readme['content'] = payload['content']
        return json.dumps(dict(commit=dict(sha='homepage-commit'))).encode()

    def test_publication_groups_installers_and_preserves_mac_downloads_and_homepage(self):
        with patch.object(self.module, 'api', self.api), patch.object(self.module.subprocess, 'run', self.fake_run), \
                patch.object(self.module.subprocess, 'check_output', self.fake_output):
            self.module.publish('example/repo', '1', 'build-commit', self.folder)
        self.assertEqual(len(self.uploads), 3)
        self.assertTrue(all('_Windows_' in name for name in self.uploads))
        self.assertEqual(sum(name.endswith('.exe') for name in self.uploads), 3)
        self.assertTrue(self.release['body'].endswith(self.mac_notes))
        self.assertEqual(self.release['assets'][0]['id'], 1)
        self.assertGreaterEqual(self.release['assets'][0]['download_count'], 23)
        homepage = base64.b64decode(self.readme['content']).decode()
        self.assertLess(homepage.index('### Windows'), homepage.index('### macOS'))
        self.assertIn('Keep features.', homepage)
        self.assertNotIn('Pending.', homepage)
        self.assertNotIn('source.zip', homepage)
        self.assertIn('community-v1.2.2/ScieFig_GPU_cellpose3_torch212_win64_v1.zip', homepage)
        self.assertIn('community-v1.2.2/ScieFig_GPU_cellpose3_torch212_win64_v1.zip', self.release['body'])
        report = json.loads((self.folder/'publication-check.json').read_text())
        self.assertEqual(report['homepage_commit'], 'homepage-commit')

    def test_mismatched_build_stops_before_upload(self):
        self.manifest['commit'] = 'another-commit'
        (self.folder/'windows-candidate.json').write_text(json.dumps(self.manifest))
        with patch.object(self.module, 'api', self.api), patch.object(self.module.subprocess, 'run', self.fake_run):
            with self.assertRaises(AssertionError):
                self.module.publish('example/repo', '1', 'build-commit', self.folder)
        self.assertFalse(self.uploads)


if __name__ == '__main__':
    unittest.main()
