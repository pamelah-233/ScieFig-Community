from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from core.models.layout_editor import LayoutObject
from core.utils.hashing import sha256_json


JsonMapping = dict[str, Any]

EXPORT_ITEM_FULL_LAYOUT = "full_layout"
EXPORT_ITEM_INDIVIDUAL_PLOT = "individual_plot"
EXPORT_ITEM_OVERLAY = "overlay"
EXPORT_ITEM_STATISTICS_CHART = "statistics_chart"
EXPORT_ITEM_EXTERNAL_IMAGE = "external_image"

ALLOWED_EXPORT_ITEM_TYPES = {
    EXPORT_ITEM_FULL_LAYOUT,
    EXPORT_ITEM_INDIVIDUAL_PLOT,
    EXPORT_ITEM_OVERLAY,
    EXPORT_ITEM_STATISTICS_CHART,
    EXPORT_ITEM_EXTERNAL_IMAGE,
}

_RESERVED_EXTERNAL_IMAGE_METADATA_KEYS = {
    "source_module",
    "source_panel_uuid",
    "export_item_key",
    "source_dpi",
    "preferred_width_mm",
    "preferred_height_mm",
    "export_item_type",
    "source_record_id",
    "source_record_namespace",
    "source_context_hash",
    "created_from_layout_object_id",
    "image_path",
    "raw_source",
    "source_format",
    "source_width_px",
    "source_height_px",
}


def _dict_copy(payload: Mapping[str, Any] | None) -> JsonMapping:
    return _thaw_json_like(payload or {})


def _freeze_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json_like(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json_like(item) for item in value)
    return value


def _thaw_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json_like(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw_json_like(item) for item in value]
    return value


def _frozen_metadata(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return _freeze_json_like(dict(payload or {}))


@dataclass(frozen=True)
class LayoutExportItem:
    export_item_id: str
    export_item_type: str
    source_module: str
    source_panel_uuid: str
    export_item_key: str
    source_dpi: int
    preferred_width_mm: float
    preferred_height_mm: float
    source_record_id: str = ""
    source_record_namespace: str = "visual_layout_asset"
    source_context_hash: str = ""
    created_from_layout_object_id: str = ""
    image_path: str = ""
    source_format: str = ""
    source_width_px: int | None = None
    source_height_px: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.export_item_id:
            raise ValueError("export_item_id is required")
        if self.export_item_type not in ALLOWED_EXPORT_ITEM_TYPES:
            raise ValueError(f"unsupported export_item_type: {self.export_item_type}")
        if not self.source_module:
            raise ValueError("source_module is required")
        if not self.source_panel_uuid:
            raise ValueError("source_panel_uuid is required")
        if not self.export_item_key:
            raise ValueError("export_item_key is required")
        if self.source_dpi <= 0:
            raise ValueError("source_dpi must be positive")
        if self.preferred_width_mm <= 0:
            raise ValueError("preferred_width_mm must be positive")
        if self.preferred_height_mm <= 0:
            raise ValueError("preferred_height_mm must be positive")
        if not self.source_record_namespace:
            raise ValueError("source_record_namespace is required")
        if self.source_width_px is not None and self.source_width_px <= 0:
            raise ValueError("source_width_px must be positive when provided")
        if self.source_height_px is not None and self.source_height_px <= 0:
            raise ValueError("source_height_px must be positive when provided")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    def to_dict(self) -> JsonMapping:
        return {
            "export_item_id": self.export_item_id,
            "export_item_type": self.export_item_type,
            "source_module": self.source_module,
            "source_panel_uuid": self.source_panel_uuid,
            "export_item_key": self.export_item_key,
            "source_dpi": int(self.source_dpi),
            "preferred_width_mm": float(self.preferred_width_mm),
            "preferred_height_mm": float(self.preferred_height_mm),
            "source_record_id": self.source_record_id,
            "source_record_namespace": self.source_record_namespace,
            "source_context_hash": self.source_context_hash,
            "created_from_layout_object_id": self.created_from_layout_object_id,
            "image_path": self.image_path,
            "source_format": self.source_format,
            "source_width_px": self.source_width_px,
            "source_height_px": self.source_height_px,
            "metadata": _dict_copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LayoutExportItem":
        return cls(
            export_item_id=str(payload.get("export_item_id", "")),
            export_item_type=str(payload.get("export_item_type", "")),
            source_module=str(payload.get("source_module", "")),
            source_panel_uuid=str(payload.get("source_panel_uuid", "")),
            export_item_key=str(payload.get("export_item_key", "")),
            source_dpi=int(payload.get("source_dpi", 0)),
            preferred_width_mm=float(payload.get("preferred_width_mm", 0.0)),
            preferred_height_mm=float(payload.get("preferred_height_mm", 0.0)),
            source_record_id=str(payload.get("source_record_id", "")),
            source_record_namespace=str(payload.get("source_record_namespace", "visual_layout_asset")),
            source_context_hash=str(payload.get("source_context_hash", "")),
            created_from_layout_object_id=str(payload.get("created_from_layout_object_id", "")),
            image_path=str(payload.get("image_path", "")),
            source_format=str(payload.get("source_format", "")),
            source_width_px=payload.get("source_width_px"),
            source_height_px=payload.get("source_height_px"),
            metadata=_dict_copy(payload.get("metadata", {})),
        )

    def stable_hash(self) -> str:
        return sha256_json(self.to_dict())

    def to_external_image_metadata(self) -> JsonMapping:
        metadata = {
            "source_module": self.source_module,
            "source_panel_uuid": self.source_panel_uuid,
            "export_item_key": self.export_item_key,
            "source_dpi": int(self.source_dpi),
            "preferred_width_mm": float(self.preferred_width_mm),
            "preferred_height_mm": float(self.preferred_height_mm),
            "export_item_type": self.export_item_type,
            "source_record_id": self.source_record_id,
            "source_record_namespace": self.source_record_namespace,
            "source_context_hash": self.source_context_hash,
            "created_from_layout_object_id": self.created_from_layout_object_id,
        }
        if self.image_path:
            metadata["image_path"] = self.image_path
            metadata["raw_source"] = Path(self.image_path).name
        if self.source_format:
            metadata["source_format"] = self.source_format
        if self.source_width_px is not None:
            metadata["source_width_px"] = int(self.source_width_px)
        if self.source_height_px is not None:
            metadata["source_height_px"] = int(self.source_height_px)
        extra_metadata = _dict_copy(self.metadata)
        collisions = _RESERVED_EXTERNAL_IMAGE_METADATA_KEYS & set(extra_metadata)
        if collisions:
            names = ", ".join(sorted(collisions))
            raise ValueError(f"export metadata cannot override reserved field(s): {names}")
        metadata.update(extra_metadata)
        return metadata


def layout_export_item_hash(export_item: LayoutExportItem) -> str:
    return export_item.stable_hash()


def validate_layout_export_item(export_item: LayoutExportItem) -> list[str]:
    errors: list[str] = []
    try:
        LayoutExportItem.from_dict(export_item.to_dict())
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def export_item_from_layout_object(
    *,
    export_item_id: str,
    export_item_type: str,
    source_module: str,
    source_panel_uuid: str,
    export_item_key: str,
    source_dpi: int,
    preferred_width_mm: float,
    preferred_height_mm: float,
    layout_object: LayoutObject,
    image_path: str = "",
    source_format: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> LayoutExportItem:
    source = layout_object.source
    if source is not None and source.source_panel_uuid != source_panel_uuid:
        raise ValueError("source_panel_uuid does not match layout object source")
    return LayoutExportItem(
        export_item_id=export_item_id,
        export_item_type=export_item_type,
        source_module=source_module,
        source_panel_uuid=source_panel_uuid,
        export_item_key=export_item_key,
        source_dpi=source_dpi,
        preferred_width_mm=preferred_width_mm,
        preferred_height_mm=preferred_height_mm,
        source_record_id=source.source_record_id if source else "",
        source_record_namespace=source.source_record_namespace if source else "visual_layout_asset",
        source_context_hash=source.source_context_hash if source else "",
        created_from_layout_object_id=layout_object.object_id,
        image_path=image_path,
        source_format=source_format,
        metadata=_dict_copy(metadata),
    )
