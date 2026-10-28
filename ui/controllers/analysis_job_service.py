from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class _AnalysisWorkerSignals(QObject):
    succeeded = pyqtSignal(str, object)
    failed = pyqtSignal(str, object)


class _AnalysisWorker(QRunnable):
    def __init__(self, key: str, work: Callable[[], Any]) -> None:
        super().__init__()
        from core.image_analysis.cellpose_backend import cellpose_device_preference
        self._model_device = cellpose_device_preference()
        self._key = key
        self._work = work
        self.signals = _AnalysisWorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        from core.image_analysis.cellpose_backend import cellpose_device_scope
        try:
            with cellpose_device_scope(self._model_device):
                result = self._work()
            self.signals.succeeded.emit(self._key, result)
        except Exception as exc:
            self.signals.failed.emit(self._key, exc)


@dataclass(frozen=True)
class _AnalysisJob:
    on_success: Callable[[Any], None]
    on_error: Callable[[Exception], None]
    is_current: Callable[[], bool]
    on_stale: Callable[[], None] | None


class AnalysisJobService(QObject):
    """Run isolated analysis work off the GUI thread and marshal results back."""

    busy_changed = pyqtSignal(str, bool)
    job_finished = pyqtSignal(str, str, object)

    def __init__(self, parent: QObject | None = None, *, thread_pool: QThreadPool | None = None) -> None:
        super().__init__(parent)
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._busy_keys: set[str] = set()
        self._jobs: dict[str, _AnalysisJob] = {}
        self._shutdown = False

    def start(
        self,
        *,
        key: str,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        is_current: Callable[[], bool],
        on_stale: Callable[[], None] | None = None,
    ) -> bool:
        """Start one job per key; all callbacks execute in this QObject's thread."""
        normalized_key = str(key).strip()
        if self._shutdown or not normalized_key or normalized_key in self._busy_keys:
            return False

        self._busy_keys.add(normalized_key)
        self._jobs[normalized_key] = _AnalysisJob(on_success, on_error, is_current, on_stale)
        self.busy_changed.emit(normalized_key, True)
        worker = _AnalysisWorker(normalized_key, work)
        worker.signals.succeeded.connect(self._handle_success)
        worker.signals.failed.connect(self._handle_failure)
        try:
            self._thread_pool.start(worker)
        except Exception as exc:
            self._handle_failure(normalized_key, exc)
        return True

    def shutdown(self) -> None:
        """Suppress callbacks for jobs that outlive a closing window."""
        self._shutdown = True
        self._jobs.clear()
        self._busy_keys.clear()

    def is_busy(self, key: str) -> bool:
        return str(key).strip() in self._busy_keys

    def has_busy_jobs(self) -> bool:
        return bool(self._busy_keys)

    def _handle_success(self, key: str, result: Any) -> None:
        job = self._jobs.pop(key, None)
        if job is None:
            self._dispose_result(result)
            return
        outcome, error = 'completed', None
        try:
            try:
                is_current = job.is_current()
            except Exception as exc:
                outcome, error = 'failed', exc
                self._invoke_without_propagating(lambda: job.on_error(exc))
                return
            if not is_current:
                outcome = 'stale'
                if job.on_stale is not None:
                    self._invoke_without_propagating(job.on_stale)
                return
            error = self._invoke_result_callback(job.on_success, result, job.on_error)
            if error is not None:
                outcome = 'failed'
        finally:
            self._dispose_result(result)
            self._finish(key)
            self.job_finished.emit(key, outcome, error)

    def _handle_failure(self, key: str, error: Any) -> None:
        job = self._jobs.pop(key, None)
        if job is None:
            return
        try:
            exception = error if isinstance(error, Exception) else RuntimeError(str(error))
            self._invoke_without_propagating(lambda: job.on_error(exception))
        finally:
            self._finish(key)
            self.job_finished.emit(key, 'failed', exception)

    def _finish(self, key: str) -> None:
        self._busy_keys.discard(key)
        self.busy_changed.emit(key, False)

    @staticmethod
    def _invoke_result_callback(
        callback: Callable[[Any], None],
        result: Any,
        on_error: Callable[[Exception], None],
    ) -> Exception | None:
        try:
            callback(result)
        except Exception as exc:
            AnalysisJobService._invoke_without_propagating(lambda: on_error(exc))
            return exc
        return None

    @staticmethod
    def _invoke_without_propagating(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            return

    @staticmethod
    def _dispose_result(result: Any) -> None:
        dispose = getattr(result, "dispose", None)
        if callable(dispose):
            AnalysisJobService._invoke_without_propagating(dispose)
