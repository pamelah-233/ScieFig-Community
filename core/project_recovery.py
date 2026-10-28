from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import numpy as np

from core.models.project import Project
from core.sfig_io import _collect_archive_members
from core.utils.time import utc_now_iso


_PATH_KEYS = ("source_path", "image_path", "resolved_image_path")


def pid_is_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def autosave_blocked(*, analysis_busy: bool = False, import_busy: bool = False, batch_busy: bool = False) -> bool:
    return bool(analysis_busy or import_busy or batch_busy)


def _normalized_id(value: str) -> str:
    text = str(value).strip()
    try:
        return str(UUID(text))
    except ValueError:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if not text or len(text) > 80 or any(character not in allowed for character in text):
            raise ValueError("Invalid recovery identity.")
        return text


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _replace_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _needs_pin(path: Path, extract_root: Path | None) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if extract_root is not None:
        try:
            resolved.relative_to(Path(extract_root).resolve())
            return True
        except (ValueError, OSError):
            pass
    try:
        resolved.relative_to(Path(tempfile.gettempdir()).resolve())
    except (ValueError, OSError):
        return False
    return any(part.startswith("sciefig_") for part in resolved.parts)


def _maybe_pin(value: object, pin_dir_factory, extract_root: Path | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_file() or not _needs_pin(path, extract_root):
        return None
    destination = pin_dir_factory() / f"{uuid4().hex}_{path.name}"
    shutil.copy2(path, destination)
    return str(destination)


def _pin_mapping(mapping: dict[str, Any], pin_dir_factory, extract_root: Path | None) -> None:
    for key in _PATH_KEYS:
        if key not in mapping:
            continue
        pinned = _maybe_pin(mapping.get(key), pin_dir_factory, extract_root)
        if pinned is not None:
            mapping[key] = pinned
    for value in mapping.values():
        if isinstance(value, dict):
            _pin_mapping(value, pin_dir_factory, extract_root)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _pin_mapping(item, pin_dir_factory, extract_root)


def _live_fluorescence_array(panel: Any, image_uid: str) -> np.ndarray | None:
    if not image_uid:
        return None
    cache = getattr(panel, "_image_cache", None)
    if isinstance(cache, dict) and image_uid in cache:
        return np.array(cache[image_uid], copy=True)
    spills = getattr(panel, "_spilled_image_paths", None)
    if isinstance(spills, dict):
        spilled = spills.get(image_uid)
        if spilled is not None and Path(spilled).is_file():
            return np.array(np.load(Path(spilled), allow_pickle=False), copy=True)
    image_data = getattr(panel, "image_data", None)
    if callable(image_data):
        data = image_data(image_uid)
        if data is not None:
            return np.array(data, copy=True)
    return None


def _copy_sourceless_fluorescence(live_panel: Any, clone_panel: Any) -> None:
    if str(getattr(clone_panel, "panel_type", "")) != "fluorescence":
        return
    images = clone_panel.data.get("fluorescence_images", [])
    if not isinstance(images, list):
        return
    cache_image_data = getattr(clone_panel, "cache_image_data", None)
    if not callable(cache_image_data):
        raise ValueError("Fluorescence snapshot cannot retain native arrays.")
    for image in images:
        if not isinstance(image, dict):
            continue
        source_path = str(image.get("source_path") or "").strip()
        if source_path and Path(source_path).is_file():
            continue
        image_uid = str(image.get("uid") or "").strip()
        array = _live_fluorescence_array(live_panel, image_uid)
        if array is None:
            raise ValueError(
                "Referenced fluorescence image asset is unavailable: "
                f"{image_uid or image.get('source_name') or 'unknown'}"
            )
        cache_image_data(image_uid, np.asarray(array))


def _pin_panel_assets(panel: Any, pin_dir_factory, extract_root: Path | None) -> None:
    raw = getattr(panel, "raw_source_path", None)
    pinned = _maybe_pin(raw, pin_dir_factory, extract_root)
    if pinned is not None:
        panel.raw_source_path = Path(pinned)
    if isinstance(panel.data, dict):
        _pin_mapping(panel.data, pin_dir_factory, extract_root)


def _dispose_owned_project(project: Project, pin_dir: tempfile.TemporaryDirectory | None) -> None:
    for panel in list(project.panels):
        dispose = getattr(panel, "dispose", None)
        if callable(dispose):
            dispose()
    project.dispose()
    if pin_dir is not None:
        pin_dir.cleanup()


@dataclass
class RecoveryDraftInfo:
    path: Path
    session_id: str
    project_uuid: str
    owner_pid: int
    title: str
    modified_at: str
    panel_ids: list[str]
    panel_uuids: list[str]
    recoverable: bool
    is_own_session: bool
    owner_alive: bool


@dataclass
class RecoverySnapshot:
    project: Project
    _pin_dir: tempfile.TemporaryDirectory | None = field(default=None, repr=False, compare=False)

    def dispose(self) -> None:
        project = self.project
        pin_dir = self._pin_dir
        self._pin_dir = None
        _dispose_owned_project(project, pin_dir)


def capture_recovery_snapshot(project: Project) -> RecoverySnapshot:
    clone = Project.from_dict(deepcopy(project.to_dict()))
    if project.composer_figures:
        clone.set_composer_figures(deepcopy(project.composer_figures), project.active_figure_uid)
    live_by_uuid = {panel.panel_uuid: panel for panel in project.panels}
    pin_dir_holder: tempfile.TemporaryDirectory | None = None

    def pin_dir() -> Path:
        nonlocal pin_dir_holder
        if pin_dir_holder is None:
            pin_dir_holder = tempfile.TemporaryDirectory(prefix="sciefig_recovery_pin_", ignore_cleanup_errors=True)
        return Path(pin_dir_holder.name)

    extract_root = getattr(project, "archive_extract_root", None)
    try:
        for clone_panel in clone.panels:
            live_panel = live_by_uuid[clone_panel.panel_uuid]
            raw = getattr(live_panel, "raw_source_path", None)
            if raw is not None:
                clone_panel.raw_source_path = raw
            _pin_panel_assets(clone_panel, pin_dir, extract_root)
            _copy_sourceless_fluorescence(live_panel, clone_panel)
        _collect_archive_members(clone)
    except Exception:
        _dispose_owned_project(clone, pin_dir_holder)
        raise
    return RecoverySnapshot(project=clone, _pin_dir=pin_dir_holder)


class ProjectRecoveryStore:
    def __init__(self, root: Path | str, session_id: str, owner_pid: int | None = None) -> None:
        self.root = Path(root)
        self.session_id = _normalized_id(session_id)
        self.owner_pid = int(os.getpid() if owner_pid is None else owner_pid)
        self._write_lock = threading.Lock()
        self.session_dir = self.root / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        _replace_json(
            self.session_dir / "session.json",
            {
                "schema": "sciefig.recovery.v1",
                "session_id": self.session_id,
                "owner_pid": self.owner_pid,
                "started_at": utc_now_iso(),
            },
        )

    @property
    def write_in_flight(self) -> bool:
        return self._write_lock.locked()

    def capture_snapshot(self, project: Project) -> RecoverySnapshot:
        return capture_recovery_snapshot(project)

    def draft_path(self, project_uuid: str) -> Path:
        return self.session_dir / f"{_normalized_id(project_uuid)}.sfig"

    def meta_path(self, project_uuid: str) -> Path:
        return self.session_dir / f"{_normalized_id(project_uuid)}.meta.json"

    def write_snapshot(self, snapshot: RecoverySnapshot) -> Path:
        if not self._write_lock.acquire(blocking=False):
            raise RuntimeError("Recovery write already in flight.")
        try:
            target = self.draft_path(snapshot.project.project_uuid)
            snapshot.project.save_sfig(target)
            try:
                self._write_meta(target, snapshot.project)
            except OSError:
                pass
            return target
        finally:
            self._write_lock.release()

    def list_drafts(self) -> list[RecoveryDraftInfo]:
        drafts: list[RecoveryDraftInfo] = []
        if not self.root.is_dir():
            return drafts
        for session_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            session_id = _try_id(session_dir.name)
            if session_id is None:
                continue
            owner_pid = _session_owner_pid(session_dir)
            for sfig_path in sorted(session_dir.glob("*.sfig")):
                project_uuid = _try_id(sfig_path.stem)
                if project_uuid is None or not _contained(sfig_path, self.root):
                    continue
                info = self._draft_info(sfig_path, session_id, project_uuid, owner_pid)
                if info is not None:
                    drafts.append(info)
        return drafts

    def load_draft(self, draft: RecoveryDraftInfo) -> Project:
        if not draft.recoverable:
            raise PermissionError("Draft is owned by a live session.")
        if not _contained(draft.path, self.root) or not draft.path.is_file():
            raise FileNotFoundError(draft.path)
        return Project.load_sfig(draft.path)

    def discard_owned_draft(self, project_uuid: str) -> None:
        self._unlink_owned(self.draft_path(project_uuid), self.meta_path(project_uuid))

    def discard_draft(self, draft: RecoveryDraftInfo) -> None:
        if not draft.recoverable:
            raise PermissionError("Draft is owned by a live session.")
        if draft.session_id != self.session_id and draft.owner_alive:
            raise PermissionError("Draft is owned by a live session.")
        if not _contained(draft.path, self.root):
            raise PermissionError("Draft is outside the recovery store.")
        meta = draft.path.with_name(f"{draft.path.stem}.meta.json")
        self._unlink_owned(draft.path, meta)

    def _unlink_owned(self, *paths: Path) -> None:
        for path in paths:
            if path.is_file() and _contained(path, self.root):
                path.unlink()

    def _write_meta(self, target: Path, project: Project) -> None:
        _replace_json(
            self.meta_path(project.project_uuid),
            {
                "schema": "sciefig.recovery.v1",
                "session_id": self.session_id,
                "project_uuid": _normalized_id(project.project_uuid),
                "owner_pid": self.owner_pid,
                "title": project.title,
                "modified_at": project.modified_at,
                "saved_at": utc_now_iso(),
                "panel_ids": [panel.panel_id for panel in project.panels],
                "panel_uuids": [panel.panel_uuid for panel in project.panels],
            },
        )

    def _draft_info(
        self,
        sfig_path: Path,
        session_id: str,
        project_uuid: str,
        session_owner_pid: int,
    ) -> RecoveryDraftInfo | None:
        meta = _read_json(sfig_path.with_name(f"{sfig_path.stem}.meta.json")) or {}
        title = str(meta.get("title") or "")
        modified_at = str(meta.get("modified_at") or "")
        panel_ids = [str(item) for item in meta.get("panel_ids", [])] if isinstance(meta.get("panel_ids"), list) else []
        panel_uuids = [str(item) for item in meta.get("panel_uuids", [])] if isinstance(meta.get("panel_uuids"), list) else []
        owner_pid = int(meta.get("owner_pid", session_owner_pid) or session_owner_pid)
        if not title or not panel_ids:
            listing = _listing_from_archive(sfig_path)
            if listing is None:
                return None
            title = title or listing["title"]
            modified_at = modified_at or listing["modified_at"]
            panel_ids = panel_ids or listing["panel_ids"]
            panel_uuids = panel_uuids or listing["panel_uuids"]
        is_own_session = session_id == self.session_id
        owner_alive = pid_is_alive(owner_pid)
        recoverable = is_own_session or not owner_alive
        return RecoveryDraftInfo(
            path=sfig_path,
            session_id=session_id,
            project_uuid=project_uuid,
            owner_pid=owner_pid,
            title=title,
            modified_at=modified_at,
            panel_ids=panel_ids,
            panel_uuids=panel_uuids,
            recoverable=recoverable,
            is_own_session=is_own_session,
            owner_alive=owner_alive,
        )


def _try_id(value: str) -> str | None:
    try:
        return _normalized_id(value)
    except ValueError:
        return None


def _session_owner_pid(session_dir: Path) -> int:
    payload = _read_json(session_dir / "session.json") or {}
    try:
        return int(payload.get("owner_pid", -1))
    except (TypeError, ValueError):
        return -1


def _listing_from_archive(path: Path) -> dict[str, Any] | None:
    try:
        from zipfile import ZipFile

        with ZipFile(path, "r") as archive:
            payload = json.loads(archive.read("project.json").decode("utf-8"))
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    panels = payload.get("panels", [])
    panel_ids: list[str] = []
    panel_uuids: list[str] = []
    if isinstance(panels, list):
        for panel in panels:
            if not isinstance(panel, dict):
                continue
            panel_ids.append(str(panel.get("panel_id", "")))
            panel_uuids.append(str(panel.get("panel_uuid", "")))
    return {
        "title": str(payload.get("title", "")),
        "modified_at": str(payload.get("modified_at", "")),
        "panel_ids": panel_ids,
        "panel_uuids": panel_uuids,
    }
