from __future__ import annotations

import queue
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QRect, QThread, pyqtSignal
from PyQt6.QtGui import QImage


@dataclass
class Job:
    kind: str
    generation: int
    func: Callable[..., Any] | None
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class Discarded(Exception):
    pass


class EngineJobThread(QThread):
    job_ok = pyqtSignal(str, str, int, object)
    job_err = pyqtSignal(str, str, int, str)

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._stop = False
        self._discarded: set[str] = set()
        self._discard_gens: set[int] = set()
        self._running_id: str | None = None
        self._lock = threading.Lock()

    def submit(self, job: Job) -> str:
        self._queue.put(job)
        return job.job_id

    def discard_job(self, job_id: str) -> None:
        with self._lock:
            self._discarded.add(job_id)

    def discard_generation(self, generation: int) -> None:
        with self._lock:
            self._discard_gens.add(generation)

    def drain_pending(self) -> None:
        kept: list[Job | None] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                kept.append(None)
                continue
            with self._lock:
                self._discarded.add(item.job_id)
        for item in kept:
            self._queue.put(item)
        with self._lock:
            if self._running_id:
                self._discarded.add(self._running_id)

    def shutdown(self) -> None:
        self._stop = True
        self.drain_pending()
        self._queue.put(None)

    def _is_discarded(self, job: Job) -> bool:
        with self._lock:
            if job.job_id in self._discarded:
                return True
            if job.generation in self._discard_gens:
                return True
            return False

    def run(self) -> None:
        while not self._stop:
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:
                break
            if self._is_discarded(job):
                continue
            with self._lock:
                self._running_id = job.job_id
            try:
                payload = self._execute(job)
            except Discarded:
                with self._lock:
                    self._running_id = None
                continue
            except Exception as exc:
                if self._is_discarded(job):
                    with self._lock:
                        self._running_id = None
                    continue
                msg = _format_error(exc)
                self.job_err.emit(job.job_id, job.kind, job.generation, msg)
                with self._lock:
                    self._running_id = None
                continue
            if self._is_discarded(job):
                with self._lock:
                    self._running_id = None
                continue
            self.job_ok.emit(job.job_id, job.kind, job.generation, payload)
            with self._lock:
                self._running_id = None

    def _execute(self, job: Job) -> Any:
        if self._is_discarded(job):
            raise Discarded()
        if job.kind == "open":
            return _exec_open(job, self._is_discarded)
        if job.kind == "page":
            return _exec_page(job, self._is_discarded)
        if job.kind == "thumb":
            return _exec_thumb(job)
        if job.kind == "extract":
            if not callable(job.func):
                raise RuntimeError("extract_style 不可用，未使用替身提取")
            return job.func(*job.args, **job.kwargs)
        if job.kind == "preview":
            if not callable(job.func):
                raise RuntimeError("render_comparison 不可用")
            return _exec_preview(job)
        if not callable(job.func):
            raise RuntimeError(f"任务 {job.kind} 没有可调用的引擎函数")
        return job.func(*job.args, **job.kwargs)


def _format_error(exc: BaseException) -> str:
    if isinstance(exc, (ValueError, OSError)):
        return str(exc) or type(exc).__name__
    text = str(exc).strip() or type(exc).__name__
    last = traceback.format_exception_only(type(exc), exc)
    extra = "".join(last).strip()
    if extra and extra not in text:
        return f"{type(exc).__name__}: {text}"
    return f"{type(exc).__name__}: {text}"


def _exec_open(job: Job, is_discarded: Callable[[Job], bool]) -> dict[str, Any]:
    api = job.meta["api"]
    path = job.meta["path"]
    page_index = int(job.meta["page_index"])
    out_dir = Path(job.meta["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    if not callable(api.inspect_source) or not callable(api.render_page):
        raise RuntimeError("打开来源需要 inspect_source 与 render_page，引擎未绑定")
    info = api.inspect_source(path)
    if is_discarded(job):
        raise Discarded()
    png = str(out_dir / f"page_{page_index}.png")
    rendered = api.render_page(path, page_index, png, 1600)
    if is_discarded(job):
        raise Discarded()
    regions: list[Any] = []
    if callable(api.suggest_regions):
        regions = api.suggest_regions(path, page_index) or []
    return {
        "info": info,
        "rendered": rendered,
        "regions": regions,
        "path": path,
        "page_index": page_index,
        "png": _abs_path(rendered, png),
    }


def _exec_page(job: Job, is_discarded: Callable[[Job], bool]) -> dict[str, Any]:
    api = job.meta["api"]
    path = job.meta["path"]
    page_index = int(job.meta["page_index"])
    out_dir = Path(job.meta["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    if not callable(api.render_page):
        raise RuntimeError("render_page 不可用")
    png = str(out_dir / f"page_{page_index}.png")
    rendered = api.render_page(path, page_index, png, 1600)
    if is_discarded(job):
        raise Discarded()
    regions: list[Any] = []
    if callable(api.suggest_regions):
        regions = api.suggest_regions(path, page_index) or []
    return {
        "rendered": rendered,
        "regions": regions,
        "path": path,
        "page_index": page_index,
        "png": _abs_path(rendered, png),
    }


def _exec_thumb(job: Job) -> dict[str, Any]:
    api = job.meta["api"]
    path = job.meta["path"]
    page_index = int(job.meta["page_index"])
    out_dir = Path(job.meta["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    if not callable(api.render_page):
        raise RuntimeError("render_page 不可用")
    png = str(out_dir / f"thumb_{page_index}.png")
    rendered = api.render_page(path, page_index, png, 200)
    return {
        "page_index": page_index,
        "png": _abs_path(rendered, png),
        "rendered": rendered,
    }


def _exec_preview(job: Job) -> dict[str, Any]:
    payload = job.func(*job.args, **job.kwargs)
    if not isinstance(payload, dict):
        raise RuntimeError("render_comparison 返回无效结果")
    result = dict(payload)
    for key in ("baseline_png", "styled_png"):
        _trim_png_margins(str(result.get(key) or ""))
    return result


def _trim_png_margins(path: str, pad: int = 8) -> None:
    if not path:
        return
    file_path = Path(path)
    if not file_path.is_file():
        return
    image = QImage(str(file_path))
    if image.isNull() or image.width() < 16 or image.height() < 16:
        return
    bg = image.pixelColor(0, 0)
    width = image.width()
    height = image.height()

    def near_bg(x: int, y: int) -> bool:
        color = image.pixelColor(x, y)
        return (
            abs(color.red() - bg.red()) < 14
            and abs(color.green() - bg.green()) < 14
            and abs(color.blue() - bg.blue()) < 14
            and color.alpha() >= 250
        )

    min_x, min_y = width, height
    max_x, max_y = 0, 0
    for y in range(height):
        for x in range(width):
            if near_bg(x, y):
                continue
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
    if max_x <= min_x or max_y <= min_y:
        return
    min_x = max(0, min_x - pad)
    min_y = max(0, min_y - pad)
    max_x = min(width - 1, max_x + pad)
    max_y = min(height - 1, max_y + pad)
    cropped = image.copy(QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1))
    if cropped.isNull() or cropped.width() < 8 or cropped.height() < 8:
        return
    cropped.save(str(file_path))


def _abs_path(rendered: Any, fallback: str) -> str:
    path = fallback
    if isinstance(rendered, dict):
        path = str(rendered.get("path") or fallback)
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = Path(fallback).resolve()
    else:
        resolved = resolved.resolve()
    return str(resolved)
