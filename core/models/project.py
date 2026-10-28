from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import tempfile
from typing import Any, Self
from uuid import uuid4
import weakref

from core.constants import SCHEMA_VERSION, SOFTWARE_VERSION
from core.models.audit_log import AuditEntry, audit_log_from_json, audit_log_to_json
from core.models.composer import ComposerFigure, ComposerLayout
from core.models.panel import BasePanel, panel_from_dict
from core.models.registry import PanelRegistry
from core.utils.time import utc_now_iso


def _validated_archive_extract_root(extract_root: Path | str | None) -> Path | None:
    if extract_root is None:
        return None

    root = Path(extract_root)
    if not root.is_absolute() or root.is_symlink():
        return None
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved_root = root.resolve()
    except OSError:
        return None
    # macOS exposes its temporary directory through /var -> /private/var.
    # Validate the canonical destination while still rejecting a symlink root.
    if resolved_root.parent != temp_root or not resolved_root.name.startswith("sciefig_"):
        return None
    return resolved_root


def _cleanup_archive_extract_root(extract_root: Path | str | None) -> None:
    root = _validated_archive_extract_root(extract_root)
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


@dataclass
class Project:
    title: str = "Untitled Figure"
    schema_version: str = SCHEMA_VERSION
    software_version: str = SOFTWARE_VERSION
    project_uuid: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)
    panels: list[BasePanel] = field(default_factory=list)
    audit_log: list[AuditEntry] = field(default_factory=list)
    composer_layout: dict[str, Any] = field(default_factory=dict)
    composer_figures: list[ComposerFigure] = field(default_factory=list)
    active_figure_uid: str | None = None
    browser_workspace_state: dict[str, Any] = field(default_factory=dict)
    session_ui_state: dict[str, Any] = field(default_factory=dict)
    archive_extract_root: Path | None = field(default=None, init=False, repr=False, compare=False)
    _archive_extract_finalizer: weakref.finalize | None = field(default=None, init=False, repr=False, compare=False)

    def touch(self) -> None:
        self.modified_at = utc_now_iso()

    def cleanup_archive_extract_root(self) -> None:
        """Remove the temporary archive assets owned by this project instance."""
        extract_root = self.archive_extract_root
        finalizer = self._archive_extract_finalizer
        self.archive_extract_root = None
        self._archive_extract_finalizer = None
        if finalizer is not None:
            finalizer.detach()
        _cleanup_archive_extract_root(extract_root)

    def register_archive_extract_root(self, extract_root: Path) -> None:
        """Register a validated temporary root for explicit and automatic cleanup."""
        if self.archive_extract_root is not None or self._archive_extract_finalizer is not None:
            raise RuntimeError("Project already owns an archive extraction root.")
        root = _validated_archive_extract_root(extract_root)
        if root is None:
            raise ValueError("Archive extraction root must be an owned temporary directory.")
        finalizer = weakref.finalize(self, _cleanup_archive_extract_root, root)
        self.archive_extract_root = root
        self._archive_extract_finalizer = finalizer

    def dispose(self) -> None:
        """Release transient assets owned by this project instance."""
        self.cleanup_archive_extract_root()

    def append_audit(self, action: str, detail: str) -> AuditEntry:
        entry = AuditEntry.create(action, detail)
        self.audit_log.append(entry)
        self.touch()
        return entry

    def assert_unique_panel_uuids(self) -> None:
        seen: set[str] = set()
        for panel in self.panels:
            panel_uuid = str(panel.panel_uuid or "").strip()
            if not panel_uuid:
                raise ValueError("Panel Object must have panel_uuid before being added to Project.")
            if panel_uuid in seen:
                raise ValueError(f"Duplicate panel_uuid in Project: {panel_uuid}")
            seen.add(panel_uuid)

    def add_panel(self, panel: BasePanel) -> None:
        if any(existing.panel_uuid == panel.panel_uuid for existing in self.panels):
            raise ValueError(f"Duplicate panel_uuid in Project: {panel.panel_uuid}")
        panel.parent_project_uuid = self.project_uuid
        panel.touch()
        self.panels.append(panel)
        self.append_audit("add_panel", f"{panel.panel_id} ({panel.panel_type})")

    def remove_panel(self, panel_uuid: str) -> BasePanel:
        for index, panel in enumerate(self.panels):
            if panel.panel_uuid == panel_uuid:
                removed = self.panels.pop(index)
                for figure in self.composer_figures:
                    figure.panel_refs = [ref for ref in figure.panel_refs if ref.panel_uid != panel_uuid]
                if isinstance(self.composer_layout, dict):
                    for cell in self.composer_layout.get("cells", []):
                        if isinstance(cell, dict) and cell.get("panel_uuid") == panel_uuid:
                            cell["panel_uuid"] = None
                            cell["panel_letter"] = None
                self.append_audit("remove_panel", f"{removed.panel_id} ({removed.panel_type})")
                return removed
        raise KeyError(f"Panel not found: {panel_uuid}")

    def get_panel(self, panel_uuid: str) -> BasePanel | None:
        return self.panel_registry().get(panel_uuid)

    def panel_registry(self) -> PanelRegistry:
        self.assert_unique_panel_uuids()
        return PanelRegistry.from_panels(self.panels)

    def get_composer_layout(self) -> ComposerLayout:
        if not self.composer_layout:
            self.composer_layout = ComposerLayout.create("2x2").to_dict()
        return ComposerLayout.from_dict(self.composer_layout)

    def set_composer_layout(self, layout: ComposerLayout) -> None:
        self.composer_layout = layout.to_dict()
        self._sync_active_figure_from_layout(layout)
        self.append_audit("update_composer_layout", f"{layout.preset}, {layout.rows}x{layout.cols}")

    def ensure_active_composer_figure(self) -> ComposerFigure:
        if self.composer_figures:
            if self.active_figure_uid:
                for figure in self.composer_figures:
                    if figure.figure_uid == self.active_figure_uid:
                        return figure
            self.active_figure_uid = self.composer_figures[0].figure_uid
            return self.composer_figures[0]
        layout = self.get_composer_layout()
        figure = ComposerFigure.from_layout(layout, {panel.panel_uuid: panel for panel in self.panels})
        self.composer_figures.append(figure)
        self.active_figure_uid = figure.figure_uid
        return figure

    def set_composer_figures(self, figures: list[ComposerFigure], active_figure_uid: str | None = None) -> None:
        self.composer_figures = figures
        self.active_figure_uid = active_figure_uid
        if self.composer_figures and not self.active_figure_uid:
            self.active_figure_uid = self.composer_figures[0].figure_uid
        if self.composer_figures and not self.composer_layout:
            self.composer_layout = self.composer_figures[0].to_layout().to_dict()

    def _sync_active_figure_from_layout(self, layout: ComposerLayout) -> None:
        panels_by_uuid = {panel.panel_uuid: panel for panel in self.panels}
        if not self.composer_figures:
            figure = ComposerFigure.from_layout(layout, panels_by_uuid)
            self.composer_figures.append(figure)
            self.active_figure_uid = figure.figure_uid
            return
        active = self.ensure_active_composer_figure()
        migrated = ComposerFigure.from_layout(layout, panels_by_uuid, figure_id=active.figure_id)
        active.canvas = migrated.canvas
        active.panel_refs = migrated.panel_refs
        active.touch()

    def to_dict(self) -> dict[str, Any]:
        self.assert_unique_panel_uuids()
        return {
            "schema_version": self.schema_version,
            "software_version": self.software_version,
            "project_uuid": self.project_uuid,
            "title": self.title,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "panels": [panel.to_dict() for panel in self.panels],
            "panel_registry": self.panel_registry().to_index(),
            "composer_figures": [figure.to_index_dict() for figure in self.composer_figures],
            "active_figure_uid": self.active_figure_uid,
            "composer_layout": self.composer_layout,
            "audit_log": audit_log_to_json(self.audit_log),
            "browser_workspace_state": deepcopy(self.browser_workspace_state)
            if isinstance(self.browser_workspace_state, dict)
            else {},
            "session_ui_state": deepcopy(self.session_ui_state)
            if isinstance(self.session_ui_state, dict)
            else {},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Self:
        return cls(
            schema_version=str(payload["schema_version"]),
            software_version=str(payload["software_version"]),
            project_uuid=str(payload["project_uuid"]),
            title=str(payload.get("title", "Untitled Figure")),
            created_at=str(payload["created_at"]),
            modified_at=str(payload["modified_at"]),
            panels=[panel_from_dict(item) for item in payload.get("panels", [])],
            composer_layout=dict(payload.get("composer_layout", {})),
            composer_figures=[ComposerFigure.from_dict(item) for item in payload.get("composer_figures", []) if isinstance(item, dict)],
            active_figure_uid=payload.get("active_figure_uid"),
            audit_log=audit_log_from_json(payload.get("audit_log", [])),
            browser_workspace_state=deepcopy(payload.get("browser_workspace_state") or {})
            if isinstance(payload.get("browser_workspace_state"), dict)
            else {},
            session_ui_state=deepcopy(payload.get("session_ui_state") or {})
            if isinstance(payload.get("session_ui_state"), dict)
            else {},
        )

    def save_sfig(self, path: str | Path) -> None:
        from core.sfig_io import save_project

        save_project(self, path)

    @classmethod
    def load_sfig(cls, path: str | Path) -> Self:
        from core.sfig_io import load_project

        return load_project(path)
