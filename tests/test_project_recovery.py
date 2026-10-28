from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import tifffile

from core.models import FluorescencePanel, Project
from core.utils.hashing import sha256_json
from core.project_recovery import (
    ProjectRecoveryStore,
    autosave_blocked,
    pid_is_alive,
)
import core.sfig_io as sfig_io
import core.project_recovery as project_recovery


def _fingerprint(project: Project) -> str:
    return sha256_json(project.to_dict())


def _audit_payload(project: Project) -> list[dict[str, str]]:
    return [entry.to_dict() for entry in project.audit_log]


def _store(tmp_path: Path, session_id: str | None = None, owner_pid: int | None = None) -> ProjectRecoveryStore:
    return ProjectRecoveryStore(
        tmp_path / "recovery",
        session_id=session_id or str(uuid4()),
        owner_pid=os.getpid() if owner_pid is None else owner_pid,
    )


def _sourceless_fluoro_project() -> tuple[Project, np.ndarray, str]:
    project = Project(title="Recovery Source-less")
    panel = FluorescencePanel(panel_id="Figure1C")
    image_uid = str(uuid4())
    pixels = np.arange(24, dtype=np.uint16).reshape(2, 3, 4)
    panel.data["fluorescence_images"] = [
        {
            "uid": image_uid,
            "source_path": "",
            "source_name": "cached.tif",
            "source_hash": "sha256:" + "0" * 64,
            "analysis_data_shape": list(pixels.shape),
            "analysis_dtype": str(pixels.dtype),
            "measurement_scale_bar": {"um_per_px_x": 0.25, "um_per_px_y": 0.25, "calibration_source": "manual"},
            "rois": [{"roi_uuid": "roi-1", "type": "rectangle", "x": 0, "y": 0, "w": 2, "h": 2}],
            "measurements": [],
        }
    ]
    panel.data["active_image_uid"] = image_uid
    panel.data["analysis_ui_state_by_image"] = {
        image_uid: {"method": "count", "settings": {"min_area_px": 4, "enabled": False}}
    }
    panel.analysis["object_counts"] = [
        {
            "count_uuid": "count-1",
            "image_uid": image_uid,
            "object_review": {"version": 1, "audit": [{"action": "keep", "object_id": 1}]},
            "results": [{"object_id": 1, "area_px": 6}],
        }
    ]
    panel.cache_image_data(image_uid, pixels)
    project.add_panel(panel)
    project.session_ui_state = {
        "active_module": "fluorescence",
        "preview_mode_by_module": {"fluorescence": "overlay"},
        "selected_result_by_module": {"fluorescence": "count-1"},
    }
    project.browser_workspace_state = {"zoom": 1.5}
    return project, pixels, image_uid


def _write_fluoro_tiff(path: Path) -> Path:
    data = np.arange(24, dtype=np.uint16).reshape(2, 3, 4)
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def test_roundtrip_preserves_ids_session_ui_review_calibration_and_pixels(tmp_path):
    store = _store(tmp_path)
    project, pixels, image_uid = _sourceless_fluoro_project()
    panel = project.panels[0]
    snapshot = store.capture_snapshot(project)
    try:
        store.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
    drafts = store.list_drafts()
    assert len(drafts) == 1
    restored = store.load_draft(drafts[0])
    try:
        restored_panel = restored.panels[0]
        restored_pixels = restored_panel.image_data(image_uid)
        assert restored.project_uuid == project.project_uuid
        assert restored_panel.panel_uuid == panel.panel_uuid
        assert restored_panel.panel_id == "Figure1C"
        assert restored.session_ui_state == project.session_ui_state
        assert restored.browser_workspace_state == project.browser_workspace_state
        assert restored_panel.data["analysis_ui_state_by_image"][image_uid]["settings"]["enabled"] is False
        assert restored_panel.data["fluorescence_images"][0]["measurement_scale_bar"]["um_per_px_x"] == 0.25
        assert restored_panel.analysis["object_counts"][0]["count_uuid"] == "count-1"
        assert restored_panel.analysis["object_counts"][0]["object_review"]["audit"][0]["action"] == "keep"
        assert restored_pixels is not None
        assert restored_pixels.dtype == np.uint16
        assert np.array_equal(restored_pixels, pixels)
    finally:
        restored.dispose()
        panel.dispose()
        project.dispose()


def test_snapshot_and_write_leave_live_audit_and_dirty_unchanged(tmp_path, monkeypatch):
    store = _store(tmp_path)
    project, pixels, image_uid = _sourceless_fluoro_project()
    live_source_path = str(project.panels[0].data["fluorescence_images"][0]["source_path"])
    before_fp = _fingerprint(project)
    before_audit = _audit_payload(project)
    before_modified = project.modified_at
    original_save = Project.save_sfig

    def guarded(self, path):
        assert self is not project
        return original_save(self, path)

    monkeypatch.setattr(Project, "save_sfig", guarded)
    snapshot = store.capture_snapshot(project)
    try:
        store.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
    assert _fingerprint(project) == before_fp
    assert _audit_payload(project) == before_audit
    assert project.modified_at == before_modified
    assert project.panels[0].data["fluorescence_images"][0]["source_path"] == live_source_path
    assert np.array_equal(project.panels[0].image_data(image_uid), pixels)
    project.panels[0].dispose()
    project.dispose()


def test_recovery_write_does_not_touch_existing_manual_sfig(tmp_path):
    store = _store(tmp_path)
    project, _pixels, _image_uid = _sourceless_fluoro_project()
    manual = tmp_path / "user_project.sfig"
    manual.write_bytes(b"manual-sfig-bytes")
    snapshot = store.capture_snapshot(project)
    try:
        target = store.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
    assert target != manual
    assert manual.read_bytes() == b"manual-sfig-bytes"
    project.panels[0].dispose()
    project.dispose()


def test_write_failure_retains_prior_valid_draft(tmp_path, monkeypatch):
    store = _store(tmp_path)
    project, pixels, image_uid = _sourceless_fluoro_project()
    snapshot = store.capture_snapshot(project)
    try:
        first = store.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
    original = first.read_bytes()
    project.title = "Changed After First Draft"
    project.session_ui_state = {"active_module": "ihc"}

    def fail_replace(*args, **kwargs):
        raise OSError("injected archive write failure")

    monkeypatch.setattr(sfig_io.os, "replace", fail_replace)
    snapshot = store.capture_snapshot(project)
    with pytest.raises(OSError, match="injected archive write failure"):
        try:
            store.write_snapshot(snapshot)
        finally:
            snapshot.dispose()
    assert first.read_bytes() == original
    restored = store.load_draft(store.list_drafts()[0])
    try:
        assert restored.title == "Recovery Source-less"
        assert np.array_equal(restored.panels[0].image_data(image_uid), pixels)
    finally:
        restored.dispose()
        project.panels[0].dispose()
        project.dispose()


def test_sourceless_fluorescence_native_array_equality(tmp_path):
    store = _store(tmp_path)
    project, pixels, image_uid = _sourceless_fluoro_project()
    snapshot = store.capture_snapshot(project)
    try:
        store.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
    restored = store.load_draft(store.list_drafts()[0])
    try:
        restored_pixels = restored.panels[0].image_data(image_uid)
        assert restored_pixels.dtype == pixels.dtype
        assert restored_pixels.shape == pixels.shape
        assert np.array_equal(restored_pixels, pixels)
        assert restored_pixels.flags["C_CONTIGUOUS"]
    finally:
        restored.dispose()
        project.panels[0].dispose()
        project.dispose()


def test_extracted_sfig_assets_recover_after_original_project_disposed(tmp_path):
    source = _write_fluoro_tiff(tmp_path / "extracted_source.tif")
    original = Project(title="Extracted Recovery")
    panel = FluorescencePanel(panel_id="Figure1C")
    record = panel.import_image(source)
    original.add_panel(panel)
    archive = tmp_path / "original.sfig"
    original.save_sfig(archive)
    panel.dispose()
    original.dispose()
    loaded = Project.load_sfig(archive)
    extract_root = loaded.archive_extract_root
    assert extract_root is not None and extract_root.exists()
    expected = np.array(loaded.panels[0].image_data(str(record["uid"])), copy=True)
    store = _store(tmp_path)
    snapshot = store.capture_snapshot(loaded)
    loaded.dispose()
    assert not extract_root.exists()
    try:
        store.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
    restored = store.load_draft(store.list_drafts()[0])
    try:
        restored_panel = restored.panels[0]
        restored_pixels = restored_panel.image_data(str(record["uid"]))
        assert restored_pixels is not None
        assert np.array_equal(np.asarray(restored_pixels), expected)
        restored_source = Path(str(restored_panel.data["fluorescence_images"][0]["source_path"]))
        assert restored_source.is_file()
    finally:
        restored.dispose()


def test_different_sessions_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(project_recovery, "pid_is_alive", lambda pid: True)
    store_a = _store(tmp_path, session_id=str(uuid4()), owner_pid=111)
    store_b = _store(tmp_path, session_id=str(uuid4()), owner_pid=222)
    project_a, _pixels_a, _uid_a = _sourceless_fluoro_project()
    project_b, _pixels_b, _uid_b = _sourceless_fluoro_project()
    project_a.project_uuid = project_b.project_uuid
    project_a.title = "Session A"
    project_b.title = "Session B"
    snap_a = store_a.capture_snapshot(project_a)
    snap_b = store_b.capture_snapshot(project_b)
    try:
        path_a = store_a.write_snapshot(snap_a)
        path_b = store_b.write_snapshot(snap_b)
    finally:
        snap_a.dispose()
        snap_b.dispose()
    assert path_a != path_b
    draft_a = next(item for item in store_a.list_drafts() if item.session_id == store_a.session_id)
    draft_b = next(item for item in store_b.list_drafts() if item.session_id == store_b.session_id)
    restored_a = store_a.load_draft(draft_a)
    restored_b = store_b.load_draft(draft_b)
    try:
        assert restored_a.title == "Session A"
        assert restored_b.title == "Session B"
        with pytest.raises(PermissionError):
            store_a.discard_draft(draft_b)
        assert path_b.is_file()
        store_a.discard_owned_draft(project_a.project_uuid)
        assert not path_a.is_file()
        assert path_b.is_file()
    finally:
        restored_a.dispose()
        restored_b.dispose()
        project_a.panels[0].dispose()
        project_b.panels[0].dispose()
        project_a.dispose()
        project_b.dispose()


def test_list_drafts_does_not_load_sfig_or_extract_assets(tmp_path, monkeypatch):
    store = _store(tmp_path)
    project, _pixels, _image_uid = _sourceless_fluoro_project()
    snapshot = store.capture_snapshot(project)
    try:
        store.write_snapshot(snapshot)
    finally:
        snapshot.dispose()

    def boom(*args, **kwargs):
        raise AssertionError("list_drafts must not load archives")

    monkeypatch.setattr(Project, "load_sfig", boom)
    monkeypatch.setattr(sfig_io, "load_project", boom)
    drafts = store.list_drafts()
    assert len(drafts) == 1
    assert drafts[0].project_uuid == project.project_uuid
    assert drafts[0].title == "Recovery Source-less"
    assert drafts[0].panel_ids == ["Figure1C"]
    project.panels[0].dispose()
    project.dispose()


def test_discard_refuses_foreign_live_and_allows_dead_owner(tmp_path, monkeypatch):
    live = {111: True, 222: True}
    monkeypatch.setattr(project_recovery, "pid_is_alive", lambda pid: live.get(pid, False))
    store_a = _store(tmp_path, session_id=str(uuid4()), owner_pid=111)
    store_b = _store(tmp_path, session_id=str(uuid4()), owner_pid=222)
    project, _pixels, _image_uid = _sourceless_fluoro_project()
    snapshot = store_b.capture_snapshot(project)
    try:
        path = store_b.write_snapshot(snapshot)
    finally:
        snapshot.dispose()
    foreign = [item for item in store_a.list_drafts() if item.session_id == store_b.session_id][0]
    assert foreign.recoverable is False
    with pytest.raises(PermissionError):
        store_a.load_draft(foreign)
    with pytest.raises(PermissionError):
        store_a.discard_draft(foreign)
    assert path.is_file()
    live[222] = False
    orphan = [item for item in store_a.list_drafts() if item.session_id == store_b.session_id][0]
    assert orphan.recoverable is True
    restored = store_a.load_draft(orphan)
    restored.dispose()
    store_a.discard_draft(orphan)
    assert not path.is_file()
    project.panels[0].dispose()
    project.dispose()


def test_autosave_blocked_helper_and_missing_asset_fails_closed(tmp_path):
    assert autosave_blocked(analysis_busy=True, import_busy=False, batch_busy=False) is True
    assert autosave_blocked(analysis_busy=False, import_busy=True, batch_busy=False) is True
    assert autosave_blocked(analysis_busy=False, import_busy=False, batch_busy=True) is True
    assert autosave_blocked(analysis_busy=False, import_busy=False, batch_busy=False) is False
    store = _store(tmp_path)
    project, _pixels, image_uid = _sourceless_fluoro_project()
    project.panels[0].remove_cached_image_data(image_uid)
    with pytest.raises(ValueError):
        store.capture_snapshot(project)
    assert store.list_drafts() == []
    project.panels[0].dispose()
    project.dispose()


def test_pid_is_alive_for_current_process():
    assert pid_is_alive(os.getpid()) is True
    assert pid_is_alive(-1) is False
