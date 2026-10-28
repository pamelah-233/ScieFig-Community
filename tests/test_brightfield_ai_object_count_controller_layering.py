from __future__ import annotations

import ast
import importlib
from pathlib import Path


CONTROLLER_PATH = Path("ui/controllers/brightfield_ai_object_count_controller.py")
MAIN_WINDOW_PATH = Path("ui/main_window.py")
FORBIDDEN_UI_SYMBOLS = {"MainWindow", "QWidget", "QDialog", "QMessageBox"}
FORBIDDEN_TRADITIONAL_REFERENCES = {
    "brightfield_measurements",
    "run_brightfield_analysis",
    "BrightfieldTraditional",
    "traditional_statistics",
}
FORBIDDEN_NON_C3_ENTRYPOINTS = {
    "run_brightfield_statistics",
    "copy_brightfield_ai_statistics_table",
    "export_brightfield_ai_statistics_csv",
    "export_brightfield_ai_statistics_csv_to_path",
    "save_sfig",
    "load_sfig",
}


def _controller_tree() -> ast.Module:
    return ast.parse(CONTROLLER_PATH.read_text(encoding="utf-8"))


def _main_window_tree() -> ast.Module:
    return ast.parse(MAIN_WINDOW_PATH.read_text(encoding="utf-8"))


def _main_window_class() -> ast.ClassDef:
    for node in _main_window_tree().body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow class not found")


def _attribute_path(node: ast.AST) -> list[str] | None:
    path: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        path.insert(0, current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        path.insert(0, current.id)
        return path
    return None


def _assert_mainwindow_wrapper(method_name: str, expected_call_path: list[str]) -> None:
    for item in _main_window_class().body:
        if isinstance(item, ast.FunctionDef) and item.name == method_name:
            assert not item.decorator_list
            assert len(item.args.posonlyargs) == 0
            assert [argument.arg for argument in item.args.args] == ["self"]
            assert len(item.args.kwonlyargs) == 0
            assert item.args.vararg is None
            assert item.args.kwarg is None
            body = list(item.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            assert len(body) == 1
            statement = body[0]
            assert isinstance(statement, ast.Expr)
            call = statement.value
            assert isinstance(call, ast.Call)
            assert call.args == []
            assert call.keywords == []
            assert _attribute_path(call.func) == expected_call_path
            return
    raise AssertionError(f"MainWindow.{method_name} not found")


def _assert_no_mainwindow_imports(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names = {alias.name for alias in node.names}
            assert "ui.main_window" not in imported_names
            assert imported_names.isdisjoint(FORBIDDEN_UI_SYMBOLS)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "ui.main_window"
            imported_names = {alias.name for alias in node.names}
            assert imported_names.isdisjoint(FORBIDDEN_UI_SYMBOLS)


def _assert_no_qt_widget_calls(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_UI_SYMBOLS - {"MainWindow"}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN_UI_SYMBOLS - {"MainWindow"}


def _referenced_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


def test_brightfield_ai_object_count_controller_module_imports() -> None:
    module = importlib.import_module("ui.controllers.brightfield_ai_object_count_controller")
    assert hasattr(module, "BrightfieldAIObjectCountController")


def test_brightfield_ai_object_count_controller_does_not_import_mainwindow() -> None:
    _assert_no_mainwindow_imports(_controller_tree())


def test_brightfield_ai_object_count_controller_does_not_instantiate_qwidget() -> None:
    _assert_no_qt_widget_calls(_controller_tree())


def test_brightfield_ai_object_count_controller_does_not_reference_traditional_storage() -> None:
    referenced_names = _referenced_names(_controller_tree())
    for forbidden_reference in FORBIDDEN_TRADITIONAL_REFERENCES:
        assert forbidden_reference not in referenced_names


def test_brightfield_ai_object_count_controller_does_not_reference_non_c3_entrypoints() -> None:
    referenced_names = _referenced_names(_controller_tree())
    for forbidden_reference in FORBIDDEN_NON_C3_ENTRYPOINTS:
        assert forbidden_reference not in referenced_names


def test_mainwindow_run_brightfield_ai_object_count_is_single_delegation() -> None:
    _assert_mainwindow_wrapper(
        "run_brightfield_ai_object_count",
        ["self", "brightfield_ai_object_count_controller", "run_object_count"],
    )


def test_brightfield_ai_object_count_controller_constructor_stays_narrow() -> None:
    tree = _controller_tree()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "BrightfieldAIObjectCountController":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    arg_names = {
                        argument.arg
                        for argument in item.args.args + item.args.kwonlyargs
                    }
                    assert "main_window" not in arg_names
                    assert "data_panel" not in arg_names
                    annotations = {
                        annotation.id
                        for argument in item.args.args + item.args.kwonlyargs
                        if isinstance(argument.annotation, ast.Name)
                        for annotation in [argument.annotation]
                    }
                    assert "MainWindow" not in annotations
                    return
    raise AssertionError("BrightfieldAIObjectCountController.__init__ not found")
