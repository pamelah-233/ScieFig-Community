from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Self
from uuid import uuid4

from core.constants import SCHEMA_VERSION, SOFTWARE_VERSION
from core.models.audit_log import AuditEntry, audit_log_from_json, audit_log_to_json
from core.utils.time import utc_now_iso


def default_coordinate_system() -> dict[str, str]:
    return {
        "data_space": "analysis_units",
        "image_space": "source_pixel",
        "layout_space": "millimeter",
        "export_space": "point",
    }


def default_unit_system() -> dict[str, str]:
    return {
        "layout_length": "mm",
        "image_length": "px",
        "microscopy_scale": "um_per_px",
    }


@dataclass
class BasePanel:
    panel_id: str
    panel_type: str = "base"
    source_module: str = ""
    schema_version: str = SCHEMA_VERSION
    software_version: str = SOFTWARE_VERSION
    panel_uuid: str = field(default_factory=lambda: str(uuid4()))
    parent_project_uuid: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)
    data: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)
    export: dict[str, Any] = field(default_factory=dict)
    audit_log: list[AuditEntry] = field(default_factory=list)
    coordinate_system: dict[str, str] = field(default_factory=default_coordinate_system)
    unit_system: dict[str, str] = field(default_factory=default_unit_system)

    PANEL_TYPE: ClassVar[str] = "base"
    SOURCE_MODULE: ClassVar[str] = "base"

    def __post_init__(self) -> None:
        if not self.panel_type or self.panel_type == "base":
            self.panel_type = self.PANEL_TYPE
        if not self.source_module:
            self.source_module = self.SOURCE_MODULE

    def touch(self) -> None:
        self.modified_at = utc_now_iso()

    def append_audit(self, action: str, detail: str) -> AuditEntry:
        entry = AuditEntry.create(action, detail)
        self.audit_log.append(entry)
        self.touch()
        return entry

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "software_version": self.software_version,
            "panel_uuid": self.panel_uuid,
            "parent_project_uuid": self.parent_project_uuid,
            "panel_id": self.panel_id,
            "panel_type": self.panel_type,
            "source_module": self.source_module,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "data": self.data,
            "analysis": self.analysis,
            "style": self.style,
            "export": self.export,
            "audit_log": audit_log_to_json(self.audit_log),
            "coordinate_system": self.coordinate_system,
            "unit_system": self.unit_system,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Self:
        return cls(
            schema_version=str(payload["schema_version"]),
            software_version=str(payload["software_version"]),
            panel_uuid=str(payload["panel_uuid"]),
            parent_project_uuid=payload.get("parent_project_uuid"),
            panel_id=str(payload["panel_id"]),
            panel_type=str(payload["panel_type"]),
            source_module=str(payload.get("source_module", "")),
            created_at=str(payload["created_at"]),
            modified_at=str(payload["modified_at"]),
            data=dict(payload.get("data", {})),
            analysis=dict(payload.get("analysis", {})),
            style=dict(payload.get("style", {})),
            export=dict(payload.get("export", {})),
            audit_log=audit_log_from_json(payload.get("audit_log", [])),
            coordinate_system=dict(payload.get("coordinate_system", default_coordinate_system())),
            unit_system=dict(payload.get("unit_system", default_unit_system())),
        )

    def render_preview(self, target_width_mm: float, target_height_mm: float, dpi: int = 150):
        raise NotImplementedError(f"{type(self).__name__} does not implement render_preview().")

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> Path | bytes:
        raise NotImplementedError(f"{type(self).__name__} does not implement render_export().")


PANEL_CLASSES: dict[str, type[BasePanel]] = {}


def register_panel_class(panel_cls: type[BasePanel]) -> type[BasePanel]:
    PANEL_CLASSES[panel_cls.PANEL_TYPE] = panel_cls
    return panel_cls


def panel_from_dict(payload: dict[str, Any]) -> BasePanel:
    from core.models import brightfield_panel, derived_plot_panel, external_image_panel, fluoro_panel, ihc_panel, qpcr_panel, wb_panel  # noqa: F401

    panel_type = str(payload.get("panel_type", "base"))
    panel_cls = PANEL_CLASSES.get(panel_type, BasePanel)
    return panel_cls.from_dict(payload)
