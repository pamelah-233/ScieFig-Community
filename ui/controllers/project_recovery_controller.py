from __future__ import annotations

import os
import threading
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QRunnable, QStandardPaths, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from core.project_recovery import ProjectRecoveryStore, RecoveryDraftInfo, autosave_blocked
from core.utils.i18n import bind_i18n, tr, trf


DEFAULT_INTERVAL_MS = 60_000


def _session_has_user_state(payload: dict) -> bool:
    selected = payload.get("selected_record_by_module") or {}
    if any(bool(value) for value in selected.values()):
        return True
    if int(payload.get("fluoro_step") or 0):
        return True
    preview = payload.get("preview_mode_by_module") or {}
    if any(str(mode) == "overlay" for mode in preview.values()):
        return True
    module = str(payload.get("active_module") or "")
    return bool(module) and module not in {"qpcr", "wb"}


def default_recovery_root() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(location) / "recovery"


class ProjectRecoveryDialog(QDialog):
    def __init__(self, drafts: list[RecoveryDraftInfo], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectRecoveryDialog")
        self.setWindowTitle(tr("recovery_title"))
        self.choice = "later"
        self.selected = drafts[0] if drafts else None
        layout = QVBoxLayout(self)
        message = bind_i18n(QLabel(), "recovery_message")
        message.setWordWrap(True)
        layout.addWidget(message)
        self.draft_list = QListWidget()
        self.draft_list.setObjectName("ProjectRecoveryDraftList")
        if len(drafts) > 1:
            layout.addWidget(bind_i18n(QLabel(), "recovery_select_draft"))
        for draft in drafts:
            item = QListWidgetItem(
                trf(
                    "recovery_draft_item",
                    title=str(draft.title or draft.project_uuid),
                    time=str(draft.modified_at or ""),
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, draft)
            self.draft_list.addItem(item)
        if self.draft_list.count():
            self.draft_list.setCurrentRow(0)
        self.draft_list.setVisible(len(drafts) > 1)
        layout.addWidget(self.draft_list)
        buttons = QHBoxLayout()
        restore = bind_i18n(QPushButton(), "recovery_restore")
        restore.setObjectName("ProjectRecoveryRestore")
        restore.setDefault(True)
        discard = bind_i18n(QPushButton(), "recovery_discard")
        discard.setObjectName("ProjectRecoveryDiscard")
        later = bind_i18n(QPushButton(), "recovery_later")
        later.setObjectName("ProjectRecoveryLater")
        restore.clicked.connect(lambda: self._finish("restore"))
        discard.clicked.connect(lambda: self._finish("discard"))
        later.clicked.connect(lambda: self._finish("later"))
        buttons.addWidget(restore)
        buttons.addWidget(discard)
        buttons.addWidget(later)
        layout.addLayout(buttons)
        self.draft_list.itemSelectionChanged.connect(self._sync_selected)

    def _sync_selected(self) -> None:
        item = self.draft_list.currentItem()
        if item is not None:
            self.selected = item.data(Qt.ItemDataRole.UserRole)

    def _finish(self, choice: str) -> None:
        self._sync_selected()
        self.choice = choice
        if choice == "later":
            self.reject()
            return
        self.accept()


class _WriteSignals(QObject):
    finished = pyqtSignal(str, int, object, str)


class _WriteJob(QRunnable):
    def __init__(
        self,
        store,
        snapshot,
        project_uuid: str,
        generation: int,
        fingerprint: str,
        should_keep,
        signals: _WriteSignals,
    ) -> None:
        super().__init__()
        self.store = store
        self.snapshot = snapshot
        self.project_uuid = project_uuid
        self.generation = generation
        self.fingerprint = fingerprint
        self.should_keep = should_keep
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        error = None
        try:
            self.store.write_snapshot(self.snapshot)
        except Exception as exc:
            error = exc
        finally:
            try:
                self.snapshot.dispose()
            except Exception:
                pass
        try:
            if not self.should_keep(self.project_uuid, self.generation):
                self.store.discard_owned_draft(self.project_uuid)
        except Exception:
            pass
        self.signals.finished.emit(self.project_uuid, self.generation, error, self.fingerprint)


class ProjectRecoveryController(QObject):
    def __init__(self, window, *, store: ProjectRecoveryStore | None = None, interval_ms: int = DEFAULT_INTERVAL_MS) -> None:
        super().__init__(window)
        self.window = window
        self._store = store
        self._session_id = store.session_id if store is not None else str(uuid4())
        self._lock = threading.Lock()
        self._retire_uuids: set[str] = set()
        self._generation = 0
        self._write_active = False
        self._alive = True
        self._startup_handled = False
        self._held_source_draft: RecoveryDraftInfo | None = None
        self._last_written_fingerprint: str | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(int(interval_ms))
        self.timer.timeout.connect(self.maybe_autosave)
        self.timer.start()

    @property
    def store(self) -> ProjectRecoveryStore:
        if self._store is None:
            self._store = ProjectRecoveryStore(default_recovery_root(), self._session_id, os.getpid())
        return self._store

    def maybe_autosave(self) -> None:
        if not self._alive or self._write_active:
            return
        if self._is_blocked():
            return
        project_uuid = str(self.window.project.project_uuid)
        with self._lock:
            if project_uuid in self._retire_uuids:
                return
        self._capture_live_session()
        if not self.window.is_project_dirty():
            return
        fingerprint = self.window._project_fingerprint()
        if fingerprint == self._last_written_fingerprint:
            return
        generation = self._generation
        try:
            snapshot = self.store.capture_snapshot(self.window.project)
        except Exception:
            self._show_status(tr("recovery_capture_failed"))
            return
        self._write_active = True
        signals = _WriteSignals()
        signals.finished.connect(self._on_write_finished, Qt.ConnectionType.QueuedConnection)
        job = _WriteJob(
            self.store,
            snapshot,
            project_uuid,
            generation,
            fingerprint,
            self._should_keep_draft,
            signals,
        )
        QThreadPool.globalInstance().start(job)

    def schedule_startup_recovery(self) -> None:
        QTimer.singleShot(0, self._delayed_startup_recovery)

    def _delayed_startup_recovery(self) -> None:
        if not self._alive or self._startup_handled:
            return
        if self.window.is_project_dirty():
            self._startup_handled = True
            self._show_status(tr("recovery_restore_blocked"))
            return
        self.prompt_startup_recovery()

    def prompt_startup_recovery(self) -> None:
        if not self._alive or self._startup_handled:
            return
        drafts = [item for item in self.store.list_drafts() if item.recoverable]
        if not drafts:
            self._startup_handled = True
            return
        choice, selected = self._choose_recovery(drafts)
        self._startup_handled = True
        if choice == "later" or selected is None:
            return
        if choice == "discard":
            try:
                self.store.discard_draft(selected)
            except Exception:
                self._show_status(tr("recovery_discard_failed"))
                return
            self._show_status(tr("recovery_discarded_status"))
            return
        if choice == "restore":
            if self.window.is_project_dirty():
                self._show_status(tr("recovery_restore_blocked"))
                return
            self._restore_draft(selected)

    def note_leaving_project(self, project_uuid: str | None = None) -> None:
        uuid = str(project_uuid or self.window.project.project_uuid)
        with self._lock:
            self._retire_uuids.add(uuid)
            self._generation += 1
        if not self._write_active:
            self._discard_owned(uuid)
            self._discard_held()
            with self._lock:
                self._retire_uuids.discard(uuid)
        self._last_written_fingerprint = None

    def note_manual_save(self) -> None:
        self.note_leaving_project(self.window.project.project_uuid)

    def note_session_ending(self) -> None:
        self._alive = False
        self.timer.stop()
        self.note_leaving_project()

    def _choose_recovery(self, drafts: list[RecoveryDraftInfo]) -> tuple[str, RecoveryDraftInfo | None]:
        dialog = ProjectRecoveryDialog(drafts, self.window)
        dialog.exec()
        return dialog.choice, dialog.selected

    def _restore_draft(self, draft: RecoveryDraftInfo) -> None:
        previous = self.window.project
        baseline = self.window._clean_project_fingerprint
        try:
            restored = self.store.load_draft(draft)
        except Exception:
            self._show_status(tr("recovery_restore_failed"))
            return
        self.window.project = restored
        try:
            self.window._load_browser_workspace_state()
            if hasattr(self.window, "analysis_state"):
                self.window.analysis_state.restore_session()
        except Exception:
            self.window.project = previous
            self.window._clean_project_fingerprint = baseline
            try:
                restored.dispose()
            except Exception:
                pass
            self._show_status(tr("recovery_restore_failed"))
            return
        self.window._dispose_project_transient_caches(previous)
        self.window._project_path = None
        self.window._clean_project_fingerprint = baseline
        self._held_source_draft = draft
        self._show_status(tr("recovery_restored_status"))

    def _capture_live_session(self) -> None:
        window = self.window
        project = window.project
        previous_session = deepcopy(getattr(project, "session_ui_state", None) or {})
        previous_browser = deepcopy(getattr(project, "browser_workspace_state", None) or {})
        was_dirty = window.is_project_dirty()
        if hasattr(window, "analysis_state"):
            window.analysis_state.capture_session()
            new_session = dict(getattr(project, "session_ui_state", None) or {})
            wb_navigation_only = (
                new_session.get("active_module") == "wb"
                and {key: value for key, value in new_session.items() if key != "active_module"}
                == {key: value for key, value in previous_session.items() if key != "active_module"}
            )
            if new_session != previous_session and not was_dirty and (
                (not previous_session and not _session_has_user_state(new_session)) or wb_navigation_only
            ):
                project.session_ui_state = previous_session
        if hasattr(window, "_store_browser_workspace_state"):
            window._store_browser_workspace_state()
            new_browser = getattr(project, "browser_workspace_state", None) or {}
            if new_browser != previous_browser and not previous_browser:
                folder = str(new_browser.get("folder") or "") if isinstance(new_browser, dict) else ""
                images = new_browser.get("images") if isinstance(new_browser, dict) else None
                if not folder and not images:
                    project.browser_workspace_state = previous_browser

    def _is_blocked(self) -> bool:
        service = getattr(self.window, "_analysis_job_service", None)
        jobs_busy = bool(service.has_busy_jobs()) if service is not None and hasattr(service, "has_busy_jobs") else False
        fluoro = getattr(self.window, "fluoro_workspace", None)
        fluoro_busy = bool(getattr(fluoro, "_busy_analysis_operations", None))
        return autosave_blocked(
            analysis_busy=jobs_busy or fluoro_busy,
            import_busy=False,
            batch_busy=self._batch_running(),
        )

    def _batch_running(self) -> bool:
        from ui.dialogs.batch_analysis_dialog import BatchAnalysisDialog

        for widget in self.window.findChildren(BatchAnalysisDialog):
            if widget.is_running:
                return True
        return False

    def _should_keep_draft(self, project_uuid: str, generation: int) -> bool:
        with self._lock:
            if project_uuid in self._retire_uuids:
                return False
            return generation == self._generation and self._alive

    def _on_write_finished(self, project_uuid: str, generation: int, error: object, fingerprint: str = "") -> None:
        self._write_active = False
        with self._lock:
            retired = project_uuid in self._retire_uuids
            self._retire_uuids.discard(project_uuid)
        if not self._alive:
            return
        if retired:
            self._discard_held()
            return
        if error is not None and generation == self._generation:
            self._show_status(tr("recovery_write_failed"))
            return
        if error is None and generation == self._generation:
            self._last_written_fingerprint = fingerprint
            self._release_held_after_durable_draft()

    def _release_held_after_durable_draft(self) -> None:
        held = self._held_source_draft
        if held is None:
            return
        current = self.store.draft_path(self.window.project.project_uuid)
        try:
            same_owned = held.session_id == self.store.session_id and held.path.resolve() == current.resolve()
        except OSError:
            same_owned = False
        if same_owned:
            self._held_source_draft = None
            return
        try:
            self.store.discard_draft(held)
        except Exception:
            return
        self._held_source_draft = None

    def _discard_owned(self, project_uuid: str) -> None:
        if self._store is None and self._write_active is False:
            return
        try:
            self.store.discard_owned_draft(project_uuid)
        except Exception:
            pass

    def _discard_held(self) -> None:
        held = self._held_source_draft
        self._held_source_draft = None
        if held is None:
            return
        try:
            self.store.discard_draft(held)
        except Exception:
            pass

    def _show_status(self, message: str) -> None:
        status = getattr(self.window, "statusBar", None)
        if callable(status):
            status().showMessage(message, 4000)
