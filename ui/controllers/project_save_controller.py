"""Save the current document in place or choose a new document path."""
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core.utils.i18n import tr, trf


def save_project(window, *, save_as: bool = False) -> bool:
    window.layout_edit_controller.flush()
    window.composer_workspace.controller.flush_edits()
    path = window._project_path
    if save_as or path is None:
        selected, _ = QFileDialog.getSaveFileName(
            window, tr("save_project_title"), str(path or ""), "ScieFig Project (*.sfig)"
        )
        if not selected:
            return False
        if not selected.lower().endswith(".sfig"):
            selected += ".sfig"
        path = Path(selected).resolve()
    try:
        if hasattr(window, "analysis_state"):
            window.analysis_state.capture_session()
        window._store_browser_workspace_state()
        window.project.save_sfig(path)
        window._capture_clean_project_baseline()
        if hasattr(window, "project_recovery"):
            window.project_recovery.note_manual_save()
    except Exception:
        QMessageBox.warning(window, tr("save_failed_title"), tr("save_failed_message"))
        return False
    window._project_path = path
    window.statusBar().showMessage(trf("saved_project_status", name=path.name), 4000)
    return True
