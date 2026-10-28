from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from core.constants import SCHEMA_VERSION
from core.models.audit_log import AuditEntry, audit_log_from_json, audit_log_to_json
from core.plotting.style_presets import get_journal_preset
from core.utils.time import utc_now_iso


def _nature_preset() -> dict[str, Any]:
    return get_journal_preset("Nature")


def _nature_label_case() -> str:
    return str(_nature_preset().get("typography", {}).get("panel_label_case", "lowercase"))


def default_panel_label(index: int, label_case: str | None = None) -> str:
    base = chr(ord("A") + int(index))
    if (label_case or _nature_label_case()).lower() == "lowercase":
        return base.lower()
    return base


def panel_source_module(panel: Any) -> str:
    data = getattr(panel, "data", None)
    panel_type = str(getattr(panel, "panel_type", ""))
    is_normalized_export_item = isinstance(data, dict) and (
        panel_type == "external_image" or bool(data.get("export_item_key")) or bool(data.get("export_item_type"))
    )
    if is_normalized_export_item:
        normalized_source = str(data.get("source_module") or "").strip()
        if normalized_source:
            return normalized_source
    return str(getattr(panel, "source_module", "") or panel_type or "unknown")


def default_composer_global_style() -> dict[str, Any]:
    preset = _nature_preset()
    typography = dict(preset.get("typography", {}))
    return {
        "journal_preset": "Nature",
        "panel_label_case": str(typography.get("panel_label_case", "lowercase")),
        "panel_label_size_pt": float(typography.get("panel_label_size_pt", 8.0)),
        "panel_label_weight": str(typography.get("panel_label_weight", "bold")),
        "font_family": str(typography.get("font_family", "Arial")),
    }


def default_composer_export_settings() -> dict[str, Any]:
    export = dict(_nature_preset().get("export", {}))
    return {
        "journal_preset": "Nature",
        "primary_format": str(export.get("primary_format", "svg")),
        "secondary_formats": list(export.get("secondary_formats", ["pdf", "tiff", "png"])),
        "dpi": max(1200, int(export.get("dpi", 1200))),
    }


def default_composer_canvas() -> dict[str, Any]:
    canvas = dict(_nature_preset().get("canvas", {}))
    return {
        "width_mm": float(canvas.get("double_column_width_mm", 183.0)),
        "height_mm": float(canvas.get("default_height_mm", 230.0)),
        "margin_mm": float(canvas.get("margin_mm", 2.0)),
        "background_color": str(canvas.get("background_color", "#FFFFFF")),
        "journal_preset": "Nature",
    }


def _composer_file_name(figure_id: str) -> str:
    safe = "".join(ch for ch in figure_id if ch.isalnum())
    return f"composer/{safe or 'Figure'}.json"


@dataclass
class AnnotationObject:
    type: str
    x_mm: float = 0.0
    y_mm: float = 0.0
    properties: dict[str, Any] = field(default_factory=dict)
    annotation_uid: str = field(default_factory=lambda: str(uuid4()))
    layer_index: int = 0
    locked: bool = False
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_uid": self.annotation_uid,
            "type": self.type,
            "x_mm": float(self.x_mm),
            "y_mm": float(self.y_mm),
            "properties": self.properties,
            "layer_index": int(self.layer_index),
            "locked": bool(self.locked),
            "visible": bool(self.visible),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnnotationObject":
        return cls(
            annotation_uid=str(payload.get("annotation_uid") or uuid4()),
            type=str(payload["type"]),
            x_mm=float(payload.get("x_mm", 0.0)),
            y_mm=float(payload.get("y_mm", 0.0)),
            properties=dict(payload.get("properties", {})),
            layer_index=int(payload.get("layer_index", 0)),
            locked=bool(payload.get("locked", False)),
            visible=bool(payload.get("visible", True)),
        )


@dataclass
class PanelReference:
    panel_uid: str
    source_module: str
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = 40.0
    height_mm: float = 30.0
    figure_label: str = "A"
    label_position: str = "top-left"
    label_auto: bool = True
    label_locked: bool = False
    label_visible: bool = True
    border: bool = False
    border_width_pt: float = 0.5
    border_color: str = "#000000"
    aspect_locked: bool = True
    original_aspect_ratio: float = 1.333
    layer_index: int = 0
    locked: bool = False
    visible: bool = True
    # Empty means inherit the panel/export item's persisted chart kind.  This
    # is intentionally reference-local so two Composer placements of one
    # statistical asset can render different kinds without mutating the source.
    chart_kind: str = ""
    chart_text_overrides: dict[str, dict] = field(default_factory=dict)
    ref_uid: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_uid": self.ref_uid,
            "panel_uid": self.panel_uid,
            "source_module": self.source_module,
            "x_mm": float(self.x_mm),
            "y_mm": float(self.y_mm),
            "width_mm": float(self.width_mm),
            "height_mm": float(self.height_mm),
            "figure_label": self.figure_label,
            "label_position": self.label_position,
            "label_auto": bool(self.label_auto),
            "label_locked": bool(self.label_locked),
            "label_visible": bool(self.label_visible),
            "border": bool(self.border),
            "border_width_pt": float(self.border_width_pt),
            "border_color": self.border_color,
            "aspect_locked": bool(self.aspect_locked),
            "original_aspect_ratio": float(self.original_aspect_ratio),
            "layer_index": int(self.layer_index),
            "locked": bool(self.locked),
            "visible": bool(self.visible),
            "chart_kind": str(self.chart_kind or ""),
            "chart_text_overrides": {kind: dict(values) for kind, values in self.chart_text_overrides.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PanelReference":
        return cls(
            ref_uid=str(payload.get("ref_uid") or uuid4()),
            panel_uid=str(payload.get("panel_uid") or payload.get("panel_uuid")),
            source_module=str(payload.get("source_module", "unknown")),
            x_mm=float(payload.get("x_mm", 0.0)),
            y_mm=float(payload.get("y_mm", 0.0)),
            width_mm=float(payload.get("width_mm", 40.0)),
            height_mm=float(payload.get("height_mm", 30.0)),
            figure_label=str(payload.get("figure_label", "A")),
            label_position=str(payload.get("label_position", "top-left")),
            label_auto=bool(payload.get("label_auto", True)),
            label_locked=bool(payload.get("label_locked", False)),
            label_visible=bool(payload.get("label_visible", True)),
            border=bool(payload.get("border", False)),
            border_width_pt=float(payload.get("border_width_pt", 0.5)),
            border_color=str(payload.get("border_color", "#000000")),
            aspect_locked=bool(payload.get("aspect_locked", True)),
            original_aspect_ratio=float(payload.get("original_aspect_ratio", 1.333)),
            layer_index=int(payload.get("layer_index", 0)),
            locked=bool(payload.get("locked", False)),
            visible=bool(payload.get("visible", True)),
            chart_kind=str(payload.get("chart_kind", "") or ""),
            chart_text_overrides={kind: dict(values) for kind, values in payload.get("chart_text_overrides", {}).items()},
        )


@dataclass
class ComposerFigure:
    figure_id: str = "Figure 1"
    figure_uid: str = field(default_factory=lambda: str(uuid4()))
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)
    canvas: dict[str, Any] = field(default_factory=default_composer_canvas)
    panel_refs: list[PanelReference] = field(default_factory=list)
    annotations: list[AnnotationObject] = field(default_factory=list)
    global_style: dict[str, Any] = field(default_factory=default_composer_global_style)
    journal_preset: str = "Nature"
    export_settings: dict[str, Any] = field(default_factory=default_composer_export_settings)
    legend_text: str = ""
    audit_log: list[AuditEntry] = field(default_factory=list)
    file_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.global_style:
            self.global_style = default_composer_global_style()
        else:
            self.global_style = {**default_composer_global_style(), **self.global_style}
        if not self.export_settings:
            self.export_settings = default_composer_export_settings()
        else:
            self.export_settings = {**default_composer_export_settings(), **self.export_settings}
        if not self.journal_preset or self.journal_preset == "nature_double":
            self.journal_preset = "Nature"
        self.canvas = {**default_composer_canvas(), **self.canvas}

    @property
    def file_name(self) -> str:
        return str(self.file_ref or _composer_file_name(self.figure_id))

    def touch(self) -> None:
        self.modified_at = utc_now_iso()

    def append_audit(self, action: str, detail: str) -> AuditEntry:
        entry = AuditEntry.create(action, detail)
        self.audit_log.append(entry)
        self.touch()
        return entry

    def to_index_dict(self) -> dict[str, str]:
        return {
            "figure_uid": self.figure_uid,
            "figure_id": self.figure_id,
            "file": self.file_name,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_uid": self.figure_uid,
            "figure_id": self.figure_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "canvas": self.canvas,
            "panel_refs": [ref.to_dict() for ref in self.panel_refs],
            "annotations": [annotation.to_dict() for annotation in self.annotations],
            "global_style": self.global_style,
            "journal_preset": self.journal_preset,
            "export_settings": self.export_settings,
            "legend_text": self.legend_text,
            "audit_log": audit_log_to_json(self.audit_log),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ComposerFigure":
        if "canvas" not in payload and "file" in payload:
            return cls(
                figure_uid=str(payload.get("figure_uid") or uuid4()),
                figure_id=str(payload.get("figure_id", "Figure 1")),
                file_ref=str(payload.get("file", "")) or None,
            )
        return cls(
            figure_uid=str(payload.get("figure_uid") or uuid4()),
            figure_id=str(payload.get("figure_id", "Figure 1")),
            schema_version=str(payload.get("schema_version", SCHEMA_VERSION)),
            created_at=str(payload.get("created_at", utc_now_iso())),
            modified_at=str(payload.get("modified_at", utc_now_iso())),
            canvas=dict(payload.get("canvas", default_composer_canvas())),
            panel_refs=[PanelReference.from_dict(item) for item in payload.get("panel_refs", [])],
            annotations=[AnnotationObject.from_dict(item) for item in payload.get("annotations", [])],
            global_style=dict(payload.get("global_style", default_composer_global_style())),
            journal_preset=str(payload.get("journal_preset", "Nature")),
            export_settings=dict(payload.get("export_settings", default_composer_export_settings())),
            legend_text=str(payload.get("legend_text", "")),
            audit_log=audit_log_from_json(payload.get("audit_log", [])),
            file_ref=str(payload.get("file", "")) or None,
        )

    @classmethod
    def from_layout(cls, layout: "ComposerLayout", panels_by_uuid: dict[str, Any] | None = None, figure_id: str = "Figure 1") -> "ComposerFigure":
        canvas = default_composer_canvas()
        canvas.update({"width_mm": float(layout.width_mm), "height_mm": float(layout.height_mm)})
        figure = cls(figure_id=figure_id, canvas=canvas)
        for index, cell in enumerate(layout.cells):
            if not cell.panel_uuid:
                continue
            panel = panels_by_uuid.get(cell.panel_uuid) if panels_by_uuid else None
            source_module = panel_source_module(panel)
            figure.panel_refs.append(
                PanelReference(
                    panel_uid=cell.panel_uuid,
                    source_module=str(source_module),
                    x_mm=float(cell.x_mm),
                    y_mm=float(cell.y_mm),
                    width_mm=float(cell.width_mm),
                    height_mm=float(cell.height_mm),
                    figure_label=cell.panel_letter or default_panel_label(index, figure.global_style.get("panel_label_case")),
                    layer_index=index,
                )
            )
        return figure

    def to_layout(self, preset: str = "2x2") -> "ComposerLayout":
        layout = ComposerLayout.create(preset)
        layout.width_mm = float(self.canvas.get("width_mm", layout.width_mm))
        layout.height_mm = float(self.canvas.get("height_mm", layout.height_mm))
        layout.rebuild_empty_cells()
        for index, ref in enumerate(self.panel_refs[: len(layout.cells)]):
            layout.cells[index].panel_uuid = ref.panel_uid
            layout.cells[index].panel_letter = ref.figure_label
            layout.cells[index].x_mm = ref.x_mm
            layout.cells[index].y_mm = ref.y_mm
            layout.cells[index].width_mm = ref.width_mm
            layout.cells[index].height_mm = ref.height_mm
        return layout


LAYOUT_PRESETS: dict[str, tuple[int, int]] = {
    "2x2": (2, 2),
    "2x3": (2, 3),
    "3x3": (3, 3),
    "3x4": (3, 4),
}


@dataclass
class ComposerCell:
    row: int
    col: int
    panel_uuid: str | None = None
    panel_letter: str | None = None
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = 0.0
    height_mm: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "panel_uuid": self.panel_uuid,
            "panel_letter": self.panel_letter,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ComposerCell":
        return cls(
            row=int(payload["row"]),
            col=int(payload["col"]),
            panel_uuid=payload.get("panel_uuid"),
            panel_letter=payload.get("panel_letter"),
            x_mm=float(payload.get("x_mm", 0.0)),
            y_mm=float(payload.get("y_mm", 0.0)),
            width_mm=float(payload.get("width_mm", 0.0)),
            height_mm=float(payload.get("height_mm", 0.0)),
        )


@dataclass
class ComposerLayout:
    preset: str = "2x2"
    rows: int = 2
    cols: int = 2
    width_mm: float = 180.0
    height_mm: float = 140.0
    gap_mm: float = 6.0
    cells: list[ComposerCell] = field(default_factory=list)

    @classmethod
    def create(cls, preset: str = "2x2") -> "ComposerLayout":
        rows, cols = LAYOUT_PRESETS[preset]
        layout = cls(preset=preset, rows=rows, cols=cols)
        layout.rebuild_empty_cells()
        return layout

    def rebuild_empty_cells(self) -> None:
        cell_width = (self.width_mm - self.gap_mm * (self.cols - 1)) / self.cols
        cell_height = (self.height_mm - self.gap_mm * (self.rows - 1)) / self.rows
        self.cells = []
        for row in range(self.rows):
            for col in range(self.cols):
                self.cells.append(
                    ComposerCell(
                        row=row,
                        col=col,
                        x_mm=col * (cell_width + self.gap_mm),
                        y_mm=row * (cell_height + self.gap_mm),
                        width_mm=cell_width,
                        height_mm=cell_height,
                    )
                )

    def set_preset(self, preset: str) -> None:
        if preset not in LAYOUT_PRESETS:
            raise ValueError(f"Unsupported Composer layout preset: {preset}")
        self.preset = preset
        self.rows, self.cols = LAYOUT_PRESETS[preset]
        self.rebuild_empty_cells()

    def place_panel(self, panel_uuid: str) -> ComposerCell:
        for cell in self.cells:
            if cell.panel_uuid is None:
                cell.panel_uuid = panel_uuid
                cell.panel_letter = default_panel_label(self.cells.index(cell))
                return cell
        raise ValueError("Composer layout has no empty cells.")

    def place_panel_at(self, panel_uuid: str, x_mm: float, y_mm: float) -> ComposerCell:
        for cell in self.cells:
            if cell.x_mm <= x_mm <= cell.x_mm + cell.width_mm and cell.y_mm <= y_mm <= cell.y_mm + cell.height_mm:
                cell.panel_uuid = panel_uuid
                cell.panel_letter = cell.panel_letter or default_panel_label(self.cells.index(cell))
                return cell
        return self.place_panel(panel_uuid)

    def remove_panel(self, panel_uuid: str) -> None:
        for cell in self.cells:
            if cell.panel_uuid == panel_uuid:
                cell.panel_uuid = None
                cell.panel_letter = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "rows": self.rows,
            "cols": self.cols,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "gap_mm": self.gap_mm,
            "cells": [cell.to_dict() for cell in self.cells],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ComposerLayout":
        if not payload:
            return cls.create("2x2")
        return cls(
            preset=str(payload.get("preset", "2x2")),
            rows=int(payload.get("rows", 2)),
            cols=int(payload.get("cols", 2)),
            width_mm=float(payload.get("width_mm", 180.0)),
            height_mm=float(payload.get("height_mm", 140.0)),
            gap_mm=float(payload.get("gap_mm", 6.0)),
            cells=[ComposerCell.from_dict(item) for item in payload.get("cells", [])],
        )
