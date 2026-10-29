from __future__ import annotations

import sys
import os
from pathlib import Path
if os.environ.get('SCIEFIG_RELEASE_SMOKE') == '1':
    import faulthandler
    _smoke_fault_log = open(os.environ['SCIEFIG_RELEASE_SMOKE_LOG'], 'w', encoding='utf-8')
    faulthandler.enable(file=_smoke_fault_log, all_threads=True)
from PyQt6.QtGui import QIcon

from PyQt6.QtWidgets import QApplication, QMessageBox

from ui.platform_support import ScieFigApplication, configure_application, configure_window
from core.constants import SOFTWARE_VERSION

_windows_application = None


def load_qss(resources_dir: Path) -> str:
    qss_path = resources_dir / "sciefig.qss"
    stylesheet = qss_path.read_text(encoding="utf-8")
    if sys.platform in {'darwin', 'win32'}:
        from ui.platform_support import macos_theme_stylesheet
        stylesheet = macos_theme_stylesheet(stylesheet)
        stylesheet += "\n" + (resources_dir / "macos.qss").read_text(encoding="utf-8")
        if sys.platform == 'win32':
            # Qt may resolve an unavailable Apple family to a wide fallback on Windows.
            stylesheet = stylesheet.replace('".AppleSystemUIFont", "Helvetica Neue", "PingFang SC", "Segoe UI", "Microsoft YaHei UI"',
                                            '"Segoe UI", "Microsoft YaHei UI"')
            stylesheet = stylesheet.replace('".AppleSystemUIFont", "PingFang SC", "Segoe UI", "Microsoft YaHei UI"',
                                            '"Segoe UI", "Microsoft YaHei UI"')
    return stylesheet


def resolve_app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def resolve_resources_dir() -> Path:
    return resolve_app_root() / "resources"


def main() -> int:
    global _windows_application
    app = ScieFigApplication(sys.argv)
    if sys.platform == 'win32' and isinstance(app, QApplication):
        # Retain Qt until the process boundary: Windows Qt teardown
        # crashes after a successfully closed main window (0xC0000005).
        _windows_application = app
    app.setOrganizationName("ScieFig")
    app.setApplicationName("ScieFig")
    app.setApplicationVersion(SOFTWARE_VERSION)
    configure_application(app)
    splash=None
    window=None
    previous_excepthook = sys.excepthook

    def handle_exception(exc_type, exc, tb) -> None:
        if splash is not None:splash.close()
        from core.utils.logging import log_exception
        exc.__traceback__ = tb
        log_path = log_exception("Unhandled application exception", exc)
        QMessageBox.critical(None, "ScieFig Error", f"{exc}\n\nFull traceback was written to:\n{log_path}")

    sys.excepthook = handle_exception
    resources_dir = resolve_resources_dir()
    from ui.widgets.startup_splash import StartupSplash
    splash=StartupSplash(resources_dir);splash.show();splash.set_stage(0)
    try:
        app.setWindowIcon(QIcon(str(resources_dir / "icons" / "sciefig_app_icon.png")))
        app.setStyleSheet(load_qss(resources_dir))
        splash.set_stage(1)
        from ui.main_window import MainWindow
        from ui.controllers.project_open_controller import schedule_startup_project
        splash.set_stage(2)
        window = MainWindow(resources_dir=resources_dir)
        splash.set_stage(3)
        configure_window(window)
        app.project_window = window
        window.show()
        splash.finish()
        from ui.widgets.project_welcome_dialog import schedule_project_welcome
        schedule_project_welcome(window, resources_dir)
        if os.environ.get('SCIEFIG_RELEASE_SMOKE') == '1':
            from PyQt6.QtCore import QTimer
            def finish_release_smoke():
                if window.is_project_dirty() or not window.close():
                    app.exit(2)
                else:
                    Path(os.environ['SCIEFIG_RELEASE_SMOKE_MARKER']).write_text('main-window-closed', encoding='utf-8')
            QTimer.singleShot(3000, finish_release_smoke)
        if app.pending_projects:
            app.open_pending_projects()
        else:
            schedule_startup_project(window, sys.argv[1:])
        return app.exec()
    finally:
        # The exception hook closes over this variable. Release the QWidget
        # and its QPixmap while QApplication still exists, also on early exit.
        splash.dispose()
        splash=None
        sys.excepthook = previous_excepthook
        if isinstance(app, QApplication):
            from PyQt6 import sip
            app.project_window = None
            if window is not None and not sip.isdeleted(window):
                sip.delete(window)
            app.processEvents()
            if _windows_application is None:
                sip.delete(app)


if __name__ == "__main__":
    exit_code = main()
    if _windows_application is not None:
        # main() has already completed window/worker cleanup and save prompts.
        # Avoid the failing native QApplication finalizer in the frozen build.
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.flush()
        os._exit(exit_code)
    raise SystemExit(exit_code)
