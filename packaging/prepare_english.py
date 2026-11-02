"""Prepare an English-only application source without altering the bilingual checkout."""
import argparse
import ast
from pathlib import Path
import pprint
import re
import runpy
import shutil


def prepare(root, target):
    root, target = Path(root).resolve(), Path(target).resolve()
    if target == root or root.is_relative_to(target):
        raise ValueError('Use a separate staging directory')
    if target.exists() and any(target.iterdir()):
        raise ValueError('Use an empty staging directory')
    for name in ('core', 'ui', 'resources', 'config', 'sample_data', 'packaging', 'LICENSES', 'docs'):
        shutil.copytree(root/name, target/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('main.py', 'requirements.txt', 'requirements-ai.txt', 'requirements-ai-dev.txt',
                 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'README.md'):
        shutil.copy2(root/name, target/name)

    original = root/'core/utils/i18n.py'
    translations = runpy.run_path(str(original))['TRANSLATIONS']['en']
    if any(re.search(r'[\u4e00-\u9fff]', value) for value in translations.values()):
        raise ValueError('Chinese text found in the English translation table')
    source = original.read_text(encoding='utf-8-sig')
    tail = source[source.index('def set_language('):]
    english = ('from __future__ import annotations\n\nLANGUAGE = "en"\n\n'
               'TRANSLATIONS = {"en": '+pprint.pformat(translations, sort_dicts=False)+'}\n\n'+tail)
    (target/'core/utils/i18n.py').write_text(english, encoding='utf-8')

    path = target/'ui/main_window.py'
    source = path.read_text(encoding='utf-8-sig')
    source = source.replace('            "language": menu.addMenu(tr("language")),\n', '')
    start = source.index('        language_menu = self._top_menus["language"]')
    end = source.index('        self.imagej_status_label', start)
    source = source[:start]+source[end:]
    start = source.index('        self.language_zh_button = QPushButton(')
    end = source.index('        self.setStatusBar(status)', start)
    source = source[:start]+source[end:]
    start = source.index('    def _set_language_button_state(')
    end = source.index('    def _build_shortcuts(', start)
    source = source[:start]+'    def _set_language_button_state(self, language: str) -> None:\n        pass\n\n'+source[end:]
    path.write_text(source, encoding='utf-8')

    path = target/'ui/widgets/project_welcome_dialog.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'COPY' for t in node.targets):
            values = ast.literal_eval(node.value)
            node.value = ast.parse(repr({'en': values['en']}), mode='eval').body
    path.write_text(ast.unparse(ast.fix_missing_locations(tree))+'\n', encoding='utf-8')
    for path in target.rglob('*.py'):
        compile(path.read_text(encoding='utf-8-sig'), str(path), 'exec')
    prepared = runpy.run_path(str(target/'core/utils/i18n.py'))
    assert set(prepared['TRANSLATIONS']) == {'en'}
    try:
        prepared['set_language']('zh')
    except ValueError:
        pass
    else:
        raise AssertionError('Chinese language is still selectable')
    print(f'English-only source prepared: {target}; {len(translations)} translations')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('target', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    prepare(args.root, args.target)
