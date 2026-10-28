from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from core.utils.hashing import sha256_json


JsonMapping = dict[str, Any]


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


def _dict_copy(payload: Mapping[str, Any] | None) -> JsonMapping:
    return _thaw_json_like(payload or {})


@dataclass(frozen=True)
class LayoutSourceIdentity:
    """Module-neutral pointer back to the source record represented by a layout object."""

    source_module: str
    source_panel_uuid: str
    source_record_id: str = ""
    source_record_namespace: str = "visual_layout_asset"
    source_context_hash: str = ""

    def __post_init__(self) -> None:
        if not self.source_module:
            raise ValueError("source_module is required")
        if not self.source_panel_uuid:
            raise ValueError("source_panel_uuid is required")
        if not self.source_record_namespace:
            raise ValueError("source_record_namespace is required")

    def to_dict(self) -> JsonMapping:
        return {
            "source_module": self.source_module,
            "source_panel_uuid": self.source_panel_uuid,
            "source_record_id": self.source_record_id,
            "source_record_namespace": self.source_record_namespace,
            "source_context_hash": self.source_context_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LayoutSourceIdentity":
        return cls(
            source_module=str(payload.get("source_module", "")),
            source_panel_uuid=str(payload.get("source_panel_uuid", "")),
            source_record_id=str(payload.get("source_record_id", "")),
            source_record_namespace=str(payload.get("source_record_namespace", "visual_layout_asset")),
            source_context_hash=str(payload.get("source_context_hash", "")),
        )


@dataclass(frozen=True)
class LayoutObjectStyle:
    stroke_color: str = "#000000"
    fill_color: str = "transparent"
    text_color: str = "#000000"
    stroke_width: float = 1.0
    opacity: float = 1.0
    font_family: str = "Arial"
    font_size_pt: float = 10.0
    bold: bool = False
    italic: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.stroke_width < 0:
            raise ValueError("stroke_width must be non-negative")
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError("opacity must be between 0 and 1")
        if self.font_size_pt <= 0:
            raise ValueError("font_size_pt must be positive")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    def to_dict(self) -> JsonMapping:
        return {
            "stroke_color": self.stroke_color,
            "fill_color": self.fill_color,
            "text_color": self.text_color,
            "stroke_width": float(self.stroke_width),
            "opacity": float(self.opacity),
            "font_family": self.font_family,
            "font_size_pt": float(self.font_size_pt),
            "bold": bool(self.bold),
            "italic": bool(self.italic),
            "metadata": _dict_copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "LayoutObjectStyle":
        payload = payload or {}
        return cls(
            stroke_color=str(payload.get("stroke_color", "#000000")),
            fill_color=str(payload.get("fill_color", "transparent")),
            text_color=str(payload.get("text_color", "#000000")),
            stroke_width=float(payload.get("stroke_width", 1.0)),
            opacity=float(payload.get("opacity", 1.0)),
            font_family=str(payload.get("font_family", "Arial")),
            font_size_pt=float(payload.get("font_size_pt", 10.0)),
            bold=bool(payload.get("bold", False)),
            italic=bool(payload.get("italic", False)),
            metadata=_dict_copy(payload.get("metadata", {})),
        )


@dataclass(frozen=True)
class LayoutObject:
    object_id: str
    object_type: str
    x: float
    y: float
    width: float
    height: float
    rotation_deg: float = 0.0
    z_order: int = 0
    locked: bool = False
    hidden: bool = False
    source: LayoutSourceIdentity | None = None
    style: LayoutObjectStyle = field(default_factory=LayoutObjectStyle)
    text: str = ""
    asset_key: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.object_id:
            raise ValueError("object_id is required")
        if not self.object_type:
            raise ValueError("object_type is required")
        if self.width < 0:
            raise ValueError("width must be non-negative")
        if self.height < 0:
            raise ValueError("height must be non-negative")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    def to_dict(self) -> JsonMapping:
        return {
            "object_id": self.object_id,
            "object_type": self.object_type,
            "x": float(self.x),
            "y": float(self.y),
            "width": float(self.width),
            "height": float(self.height),
            "rotation_deg": float(self.rotation_deg),
            "z_order": int(self.z_order),
            "locked": bool(self.locked),
            "hidden": bool(self.hidden),
            "source": self.source.to_dict() if self.source else None,
            "style": self.style.to_dict(),
            "text": self.text,
            "asset_key": self.asset_key,
            "metadata": _dict_copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LayoutObject":
        source_payload = payload.get("source")
        source = LayoutSourceIdentity.from_dict(source_payload) if isinstance(source_payload, Mapping) else None
        return cls(
            object_id=str(payload.get("object_id", "")),
            object_type=str(payload.get("object_type", "")),
            x=float(payload.get("x", 0.0)),
            y=float(payload.get("y", 0.0)),
            width=float(payload.get("width", 0.0)),
            height=float(payload.get("height", 0.0)),
            rotation_deg=float(payload.get("rotation_deg", 0.0)),
            z_order=int(payload.get("z_order", 0)),
            locked=bool(payload.get("locked", False)),
            hidden=bool(payload.get("hidden", False)),
            source=source,
            style=LayoutObjectStyle.from_dict(payload.get("style")),
            text=str(payload.get("text", "")),
            asset_key=str(payload.get("asset_key", "")),
            metadata=_dict_copy(payload.get("metadata", {})),
        )

    def stable_hash(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class LayoutCanvasSpec:
    canvas_id: str
    width_px: int
    height_px: int
    dpi: int = 300
    background_color: str = "#FFFFFF"
    unit: str = "px"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.canvas_id:
            raise ValueError("canvas_id is required")
        if self.width_px <= 0:
            raise ValueError("width_px must be positive")
        if self.height_px <= 0:
            raise ValueError("height_px must be positive")
        if self.dpi <= 0:
            raise ValueError("dpi must be positive")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    def to_dict(self) -> JsonMapping:
        return {
            "canvas_id": self.canvas_id,
            "width_px": int(self.width_px),
            "height_px": int(self.height_px),
            "dpi": int(self.dpi),
            "background_color": self.background_color,
            "unit": self.unit,
            "metadata": _dict_copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LayoutCanvasSpec":
        return cls(
            canvas_id=str(payload.get("canvas_id", "")),
            width_px=int(payload.get("width_px", 0)),
            height_px=int(payload.get("height_px", 0)),
            dpi=int(payload.get("dpi", 300)),
            background_color=str(payload.get("background_color", "#FFFFFF")),
            unit=str(payload.get("unit", "px")),
            metadata=_dict_copy(payload.get("metadata", {})),
        )

    def stable_hash(self) -> str:
        return sha256_json(self.to_dict())


def layout_object_hash(layout_object: LayoutObject) -> str:
    return layout_object.stable_hash()


def validate_layout_object(layout_object: LayoutObject) -> list[str]:
    errors: list[str] = []
    try:
        LayoutObject.from_dict(layout_object.to_dict())
    except ValueError as exc:
        errors.append(str(exc))
    if layout_object.hidden and layout_object.locked:
        return errors
    return errors
