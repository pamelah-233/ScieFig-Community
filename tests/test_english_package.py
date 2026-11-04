import ast
import importlib.util
from pathlib import Path
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EnglishPackageTests(unittest.TestCase):
    def test_english_source_removes_language_controls_and_preserves_gpu_protocol(self):
        spec = importlib.util.spec_from_file_location('prepare_english', ROOT/'packaging/prepare_english.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)/'english'
            module.prepare(ROOT, target)
            i18n = runpy.run_path(str(target/'core/utils/i18n.py'))
            original = runpy.run_path(str(ROOT/'core/utils/i18n.py'))
            self.assertEqual(i18n['TRANSLATIONS'], {'en': original['TRANSLATIONS']['en']})
            self.assertEqual(i18n['get_language'](), 'en')
            with self.assertRaises(ValueError):
                i18n['set_language']('zh')
            tree = ast.parse((target/'ui/main_window.py').read_text(encoding='utf-8'))
            attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            self.assertNotIn('language_zh_button', attributes)
            self.assertNotIn('language_en_button', attributes)
            self.assertNotIn('menu.addMenu(tr(\'language\'))', ast.unparse(tree))
            welcome = ast.parse((target/'ui/widgets/project_welcome_dialog.py').read_text(encoding='utf-8'))
            copy = next(node.value for node in welcome.body if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'COPY' for t in node.targets))
            self.assertEqual(set(ast.literal_eval(copy)), {'en'})
            for name in ('core/image_analysis/model_runtime.py', 'core/image_analysis/cellpose_backend.py',
                         'ui/widgets/gpu_runtime_dialog.py', 'ui/controllers/model_device_controller.py'):
                self.assertEqual((target/name).read_bytes(), (ROOT/name).read_bytes())


if __name__ == '__main__':
    unittest.main()
