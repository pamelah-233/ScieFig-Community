"""Native application integration and presentation, separate from analysis UI."""
from pathlib import Path
import re
import sys

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtGui import QColor, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QAbstractScrollArea, QHeaderView, QTableView


MACOS_COLORS = {
    '#16130f': '#1e1e1f', '#221d17': '#262525', '#221e18': '#262525',
    '#211c17': '#171717', '#1c1712': '#262525', '#2c2620': '#2e2c2a',
    '#342d25': '#37322e', '#38302a': '#3a3734', '#38322a': '#3a3734',
    '#4a3f36': '#45403b', '#44372c': '#45403b', '#4a3a2d': '#45403b',
    '#39291f': '#3d322b', '#39281f': '#3d322b', '#30271f': '#312c27',
    '#f0eae2': '#e9e5df', '#e9dbce': '#e9e5df', '#a39689': '#b4ada5',
    '#6e6258': '#aaa39b', '#ac9e8e': '#b4ada5', '#b8a899': '#bcb3a8',
    '#d97757': '#c58d6c', '#e08a6e': '#d39a79', '#f5b092': '#efd1b9',
    '#ffc19e': '#efd1b9', '#85553e': '#8d6951', '#5a4b40': '#65584d',
}


def macos_theme_stylesheet(stylesheet):
    """Recolor UI chrome only; image data, plot styles and paper stay intact."""
    return re.sub(r'#[0-9a-fA-F]{6}\b',
                  lambda match: MACOS_COLORS.get(match[0].lower(), match[0]), stylesheet)


class ScieFigApplication(QApplication):
    def __init__(self, arguments):
        super().__init__(arguments)
        self.project_window = None
        self.pending_projects = []

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file()
            if Path(path).suffix.lower() == ".sfig":
                self.pending_projects.append(path)
                if self.project_window is not None:
                    QTimer.singleShot(0, self.open_pending_projects)
                return True
        return super().event(event)

    def open_pending_projects(self):
        if self.project_window is None:
            return
        from ui.controllers.project_open_controller import open_project_path
        while self.pending_projects:
            open_project_path(self.project_window, self.pending_projects.pop(0))
        self.project_window.showNormal()
        self.project_window.raise_()
        self.project_window.activateWindow()


class _MacScrollPresentation(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Polish:
            if isinstance(watched, QAbstractItemView) and not isinstance(watched, QHeaderView):
                # The Qt default moves tables by entire columns. A continuous
                # scrollbar must track pixels, including tables created lazily.
                watched.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
                if isinstance(watched, QTableView):
                    from ui.widgets.table_presentation import readable_table
                    readable_table(watched)
            if isinstance(watched, QAbstractScrollArea):
                watched.horizontalScrollBar().setTracking(True)
        return False


def configure_application(app):
    if sys.platform not in {'darwin', 'win32'}:
        return
    if not hasattr(app, '_mac_scroll_presentation'):
        app._mac_scroll_presentation = _MacScrollPresentation(app)
        app.installEventFilter(app._mac_scroll_presentation)
    app.setStyle("Fusion")
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(13)
    app.setFont(font)
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#1e1e1f"),
        (QPalette.ColorRole.WindowText, "#e9e5df"),
        (QPalette.ColorRole.Base, "#262525"),
        (QPalette.ColorRole.AlternateBase, "#2e2c2a"),
        (QPalette.ColorRole.Text, "#e9e5df"),
        (QPalette.ColorRole.Button, "#2e2c2a"),
        (QPalette.ColorRole.ButtonText, "#e9e5df"),
        (QPalette.ColorRole.Highlight, "#8d6951"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.ToolTipBase, "#342d25"),
        (QPalette.ColorRole.ToolTipText, "#f0eae2"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)


def configure_window(window):
    if sys.platform not in {'darwin', 'win32'}:
        return
    if sys.platform == 'darwin':
        window.menuBar().setNativeMenuBar(True)
    from PyQt6.QtWidgets import QLayout
    window.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    window.setMinimumSize(900, 640)
    screen = window.screen()
    if screen:
        area = screen.availableGeometry()
        window.resize(min(1440, int(area.width() * .94)), min(900, int(area.height() * .92)))
        window.move(area.center() - window.rect().center())
    from ui.widgets.app_shell import V9AppShell
    shell = window.findChild(V9AppShell)
    if shell:
        shell.set_rail_width(68)

