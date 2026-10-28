from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_ROOTS = {"cellpose", "torch", "fastremap", "fill_voids", "numba"}


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_clean_import(code: str) -> None:
    completed = _run_python(code)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _forbidden_imports_under(root: Path) -> list[str]:
    matches: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in FORBIDDEN_IMPORT_ROOTS:
                        matches.append(f"{path}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                top = module.split(".")[0]
                if top in FORBIDDEN_IMPORT_ROOTS:
                    matches.append(f"{path}:from {module}")
    return matches


def test_importing_brightfield_module_does_not_import_onnxruntime() -> None:
    _assert_clean_import("import sys; import core.image_analysis.brightfield; assert 'onnxruntime' not in sys.modules")


def test_importing_brightfield_panel_does_not_import_onnxruntime() -> None:
    _assert_clean_import("import sys; import core.models.brightfield_panel; assert 'onnxruntime' not in sys.modules")


def test_importing_brightfield_panel_does_not_import_ai_pack() -> None:
    _assert_clean_import("import sys; import core.models.brightfield_panel; assert 'core.ai_pack' not in sys.modules")


def test_importing_postprocess_module_does_not_import_postprocessors() -> None:
    _assert_clean_import(
        "import sys; import core.ai_pack.postprocess; assert 'core.ai_pack.postprocessors.cellpose_flow_minimal' not in sys.modules"
    )


def test_no_forbidden_runtime_packages_in_core() -> None:
    assert _forbidden_imports_under(REPO_ROOT / "core") == []


def test_no_forbidden_runtime_packages_in_ui() -> None:
    assert _forbidden_imports_under(REPO_ROOT / "ui") == []


def test_scripts_ai_pack_export_module_is_not_imported_by_core() -> None:
    _assert_clean_import(
        "import sys; import core; import ui; assert 'scripts.ai_pack.export.export_cellpose_onnx' not in sys.modules"
    )
