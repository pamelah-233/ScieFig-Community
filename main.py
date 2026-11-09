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

def load_qss(resources_dir: Path) -> str:
    from PyQt6.QtCore import QDir
    QDir.setSearchPaths('sciefigSpin', [str(resources_dir / 'icons')])
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
    app = ScieFigApplication(sys.argv)
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
        if '--wb-demo' in sys.argv:
            from ui.wb_demo import prepare_demo
            prepare_demo(window)
        else:
            from ui.widgets.project_welcome_dialog import schedule_project_welcome
            schedule_project_welcome(window, resources_dir)
        if os.environ.get('SCIEFIG_RELEASE_SMOKE') == '1':
            from PyQt6.QtCore import QTimer
            if '--wb-demo' in sys.argv:
                # The synthetic project starts with deliberate edits.
                window._capture_clean_project_baseline()
            def finish_release_smoke():
                if window.is_project_dirty() or not window.close():
                    app.exit(2)
                else:
                    Path(os.environ['SCIEFIG_RELEASE_SMOKE_MARKER']).write_text('main-window-closed', encoding='utf-8')
            QTimer.singleShot(3000, finish_release_smoke)
        if app.pending_projects:
            app.open_pending_projects()
        elif '--wb-demo' not in sys.argv:
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
            from PyQt6.QtCore import QThreadPool
            # Analysis and recovery jobs can still be unwinding after the window closes.
            QThreadPool.globalInstance().waitForDone()
            app.project_window = None
            if window is not None and not sip.isdeleted(window):
                sip.delete(window)
            app.processEvents()
            sip.delete(app)


if __name__ == "__main__":
    if '--spatial-demo' in sys.argv:
        from ui.dialogs.spatial_demo import run_demo
        run_demo(load_qss(resolve_resources_dir()))
    exit_code = main()
    raise SystemExit(exit_code)
