from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qt_application():
    # QApplication must outlive every test widget. Recreating it between UI
    # tests can crash inside Qt before Python can report the next assertion.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    # Windows' offscreen plugin has no system font database. Geometry tests
    # need the same real UI fonts as the native Windows platform plugin.
    import sys
    if sys.platform == 'win32' and application.platformName() == 'offscreen':
        from PyQt6.QtGui import QFontDatabase
        if not QFontDatabase.families():
            from pathlib import Path
            fonts = Path(os.environ['WINDIR']) / 'Fonts'
            for name in ('segoeui.ttf', 'msyh.ttc'):
                assert QFontDatabase.addApplicationFont(str(fonts / name)) >= 0
    yield application


@pytest.fixture
def desktop_screen(monkeypatch):
    """Give desktop-size geometry tests their declared desktop viewport.

    Qt offscreen otherwise reports an 800x800 screen, and the app correctly
    clamps requested 1280/1366px windows to that unrelated test-backend size.
    """
    from types import SimpleNamespace
    from PyQt6.QtCore import QRect
    from ui.main_window import MainWindow
    screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080))
    monkeypatch.setattr(MainWindow, 'screen', lambda self: screen)
    return screen


class _UnsavedChangesQuestionInterceptor:
    def __init__(self, original_question, discard_response) -> None:
        self.original_question = original_question
        self.discard_response = discard_response

    def __call__(self, *args, **kwargs):
        title = args[1] if len(args) > 1 else kwargs.get("title")
        from core.utils.i18n import tr
        if title in {"Unsaved Changes", tr("unsaved_changes_title")}:
            return self.discard_response
        return self.original_question(*args, **kwargs)


@pytest.fixture(autouse=True)
def discard_unsaved_changes_questions(monkeypatch):
    try:
        from PyQt6.QtWidgets import QMessageBox
    except Exception:
        yield None
        return
    interceptor = _UnsavedChangesQuestionInterceptor(
        QMessageBox.question,
        QMessageBox.StandardButton.Discard,
    )
    monkeypatch.setattr(QMessageBox, "question", interceptor)
    yield interceptor


@pytest.fixture(autouse=True)
def cleanup_qt_widgets_after_test(discard_unsaved_changes_questions, qt_application):
    yield
    try:
        import gc

        from PyQt6.QtCore import QCoreApplication, QEvent
        from PyQt6.QtWidgets import QApplication
        from ui.widgets.editable_text_item import cleanup_registered_graphics_scenes
    except Exception:
        return
    app = QApplication.instance()
    if app is not None:
        widgets = list(app.topLevelWidgets())
        for widget in widgets:
            try:
                widget.close()
            except RuntimeError:
                continue
        app.processEvents()
        cleanup_registered_graphics_scenes()
        for widget in widgets:
            try:
                module_name = type(widget).__module__
                is_sciefig_widget = module_name.startswith("ui.") and widget.parentWidget() is None
                if widget.inherits("QMainWindow") or widget.inherits("QDialog") or is_sciefig_widget:
                    widget.deleteLater()
            except RuntimeError:
                continue
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        gc.collect()
