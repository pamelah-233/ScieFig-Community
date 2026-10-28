"""One project-opening path for the file picker and Windows file associations."""
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from core.models import Project
from core.utils.i18n import tr, trf


def open_project_path(window, path: str | Path, *, confirm_unsaved: bool = True) -> bool:
    if confirm_unsaved and not window._confirm_destructive_action(tr('action_open_project')):
        return False
    previous_project = window.project
    previous_baseline = window._clean_project_fingerprint
    loaded_project = None
    try:
        loaded_project = Project.load_sfig(path)
        window.project = loaded_project
        window._load_browser_workspace_state()
        if hasattr(window, 'analysis_state'):
            window.analysis_state.restore_session()
        else:
            window.set_module(window.active_module)
        window._capture_clean_project_baseline()
        if hasattr(window, 'project_recovery'):
            window.project_recovery.note_leaving_project(previous_project.project_uuid)
    except Exception:
        window.project = previous_project
        window._clean_project_fingerprint = previous_baseline
        if loaded_project is not None and loaded_project is not previous_project:
            window._dispose_project_transient_caches(loaded_project)
        try:
            window.set_module(window.active_module)
        except Exception:
            pass
        QMessageBox.warning(window, tr('open_failed_title'), tr('open_failed_message'))
        return False
    window._dispose_project_transient_caches(previous_project)
    window._project_path = Path(path).resolve()
    window.statusBar().showMessage(trf('loaded_project_status', name=Path(path).name), 4000)
    return True


def schedule_startup_project(window, arguments: list[str]) -> None:
    path = next((Path(value) for value in arguments if Path(value).suffix.lower() == '.sfig'), None)
    if path is None:
        window.project_recovery.schedule_startup_recovery()
    else:
        # A fresh startup has no user edits. Do not replace the requested file with a recovery prompt.
        QTimer.singleShot(0, lambda: open_project_path(window, path, confirm_unsaved=False))
