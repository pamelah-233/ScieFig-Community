from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from core.constants import APP_NAME
from core.utils.time import utc_now_iso


STYLE_PRESET_REGISTRY_SCHEMA_VERSION = 1
STYLE_PRESET_REGISTRY_FILENAME = "style_presets.json"


class StylePresetRegistryError(ValueError):
    """Raised when the external style preset registry payload is malformed."""


def default_style_preset_registry_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / APP_NAME / "style_lab" / STYLE_PRESET_REGISTRY_FILENAME


def _deep_copy_payload(value: Any) -> Any:
    return deepcopy(value)


def _normalize_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _deep_copy_payload(dict(value))
    return {}


def _normalize_warning_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _builtin_preset_plot_style(preset_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from core.plotting.style_presets import get_journal_preset, plot_style_from_preset

    preset = get_journal_preset(preset_name)
    return preset, plot_style_from_preset(preset)


def _require_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StylePresetRegistryError(f"Invalid style preset registry payload: {field_name} must be a JSON object.")
    return _deep_copy_payload(dict(value))


def _require_list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise StylePresetRegistryError(f"Invalid style preset registry payload: {field_name} must be a list.")
    return list(value)


def _require_string_field(payload: dict[str, Any], field_name: str) -> str:
    if field_name not in payload:
        raise StylePresetRegistryError(f"Invalid style preset registry payload: missing required field '{field_name}'.")
    value = payload[field_name]
    if not isinstance(value, str):
        raise StylePresetRegistryError(f"Invalid style preset registry payload: field '{field_name}' must be a string.")
    return str(value)


def _require_non_empty_string_field(payload: dict[str, Any], field_name: str) -> str:
    value = _require_string_field(payload, field_name)
    if not value.strip():
        raise StylePresetRegistryError(
            f"Invalid style preset registry payload: field '{field_name}' must be a non-empty string."
        )
    return value


def _require_metadata_string_field(payload: dict[str, Any], field_name: str, *, allow_blank: bool = False) -> str:
    value = payload.get(field_name)
    qualified_name = f"source_metadata.{field_name}"
    if not isinstance(value, str):
        raise StylePresetRegistryError(
            f"Invalid style preset registry payload: field '{qualified_name}' must be a string."
        )
    if not allow_blank and not value.strip():
        raise StylePresetRegistryError(
            f"Invalid style preset registry payload: field '{qualified_name}' must be a non-empty string."
        )
    return str(value)


def _require_metadata_int_field(
    payload: dict[str, Any],
    field_name: str,
    *,
    qualified_name: str | None = None,
    min_value: int | None = None,
) -> int:
    display_name = qualified_name or f"source_metadata.{field_name}"
    if field_name not in payload:
        raise StylePresetRegistryError(
            f"Invalid style preset registry payload: missing required field '{display_name}'."
        )
    value = payload[field_name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise StylePresetRegistryError(
            f"Invalid style preset registry payload: field '{display_name}' must be an integer."
        )
    normalized = int(value)
    if min_value is not None and normalized < min_value:
        raise StylePresetRegistryError(
            f"Invalid style preset registry payload: field '{display_name}' must be >= {min_value}."
        )
    return normalized


def _require_metadata_int_dict(
    source_metadata: dict[str, Any],
    field_name: str,
    child_fields: tuple[str, ...],
    *,
    min_value: int | None = None,
) -> dict[str, int]:
    qualified_name = f"source_metadata.{field_name}"
    value = source_metadata.get(field_name)
    if not isinstance(value, dict):
        raise StylePresetRegistryError(
            f"Invalid style preset registry payload: field '{qualified_name}' must be a JSON object."
        )
    normalized: dict[str, int] = {}
    for child_field in child_fields:
        child_qualified_name = f"source_metadata.{field_name}.{child_field}"
        normalized[child_field] = _require_metadata_int_field(
            value,
            child_field,
            qualified_name=child_qualified_name,
            min_value=min_value,
        )
    return normalized


def _require_known_seed_preset_name(source_metadata: dict[str, Any]) -> str:
    seed_preset_name = _require_metadata_string_field(source_metadata, "seed_preset_name")
    from core.plotting.style_presets import journal_preset_names

    known_names = {str(name).casefold() for name in journal_preset_names()}
    if seed_preset_name.casefold() not in known_names:
        raise StylePresetRegistryError(
            "Invalid style preset registry payload: field 'source_metadata.seed_preset_name' must be a known built-in preset name."
        )
    return seed_preset_name


def _require_present_fields(payload: dict[str, Any], field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        if field_name not in payload:
            raise StylePresetRegistryError(
                f"Invalid style preset registry payload: missing required field '{field_name}'."
            )


def _require_registry_metadata(source_metadata: dict[str, Any]) -> dict[str, Any]:
    required_fields = (
        "seed_preset_name",
        "source_kind",
        "source_path",
        "source_page_index",
        "source_page_count",
        "source_size_px",
        "source_crop_rect",
    )
    for field_name in required_fields:
        if field_name not in source_metadata:
            raise StylePresetRegistryError(
                f"Invalid style preset registry payload: missing required field 'source_metadata.{field_name}'."
            )
    metadata = _deep_copy_payload(dict(source_metadata))
    metadata["seed_preset_name"] = _require_known_seed_preset_name(metadata)
    metadata["source_kind"] = _require_metadata_string_field(metadata, "source_kind")
    metadata["source_path"] = _require_metadata_string_field(metadata, "source_path", allow_blank=True)
    metadata["source_page_index"] = _require_metadata_int_field(metadata, "source_page_index", min_value=0)
    metadata["source_page_count"] = _require_metadata_int_field(metadata, "source_page_count", min_value=1)
    metadata["source_size_px"] = _require_metadata_int_dict(
        metadata,
        "source_size_px",
        ("width", "height"),
        min_value=0,
    )
    metadata["source_crop_rect"] = _require_metadata_int_dict(
        metadata,
        "source_crop_rect",
        ("x", "y", "width", "height"),
        min_value=0,
    )
    return metadata


@dataclass(slots=True)
class StylePresetRecord:
    preset_id: str = field(default_factory=lambda: uuid4().hex)
    display_name: str = ""
    source_type: str = "user_modified"
    plot_style: dict[str, Any] = field(default_factory=dict)
    extraction_profile: dict[str, Any] = field(default_factory=dict)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_style_profile_draft(
        cls,
        draft,
        *,
        source_type: str = "extracted_draft",
        preset_id: str | None = None,
    ) -> "StylePresetRecord":
        source_metadata = {
            "seed_preset_name": str(draft.seed_preset_name),
            "source_kind": str(draft.source_kind),
            "source_path": str(draft.source_path),
            "source_page_index": int(draft.source_page_index),
            "source_page_count": int(draft.source_page_count),
            "source_size_px": _deep_copy_payload(dict(draft.source_size_px)),
            "source_crop_rect": _deep_copy_payload(dict(draft.source_crop_rect)),
        }
        return cls(
            preset_id=str(preset_id or draft.draft_id or uuid4().hex),
            display_name=str(draft.display_name),
            source_type=str(source_type),
            plot_style=_deep_copy_payload(dict(draft.plot_style)),
            extraction_profile=_deep_copy_payload(dict(draft.extraction_profile)),
            source_metadata=source_metadata,
            warnings=_deep_copy_payload([str(item) for item in draft.warnings]),
        )

    @classmethod
    def from_builtin_preset_copy(cls, preset_name: str, *, display_name: str | None = None) -> "StylePresetRecord":
        preset, plot_style = _builtin_preset_plot_style(preset_name)
        return cls(
            display_name=str(display_name or f"{preset_name} copy"),
            source_type="builtin_copy",
            plot_style=plot_style,
            extraction_profile={},
            source_metadata={
                "builtin_preset_name": str(preset_name),
                "builtin_preset_id": str(preset.get("id", "")),
                "seed_preset_name": str(preset_name),
                "source_kind": "builtin",
                "source_path": "",
                "source_page_index": 0,
                "source_page_count": 1,
                "source_size_px": {"width": 0, "height": 0},
                "source_crop_rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            },
            warnings=[],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": str(self.preset_id),
            "display_name": str(self.display_name),
            "source_type": str(self.source_type),
            "plot_style": _deep_copy_payload(dict(self.plot_style)),
            "extraction_profile": _deep_copy_payload(dict(self.extraction_profile)),
            "source_metadata": _deep_copy_payload(dict(self.source_metadata)),
            "warnings": _deep_copy_payload([str(item) for item in self.warnings]),
            "created_at": str(self.created_at),
            "updated_at": str(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StylePresetRecord":
        # Persisted external registry records must already carry stable identities.
        # New-record creation is handled by explicit constructors such as
        # from_style_profile_draft() and from_builtin_preset_copy().
        if not isinstance(payload, dict):
            raise StylePresetRegistryError("Invalid style preset registry payload: preset entry must be a JSON object.")
        _require_present_fields(
            payload,
            (
                "preset_id",
                "display_name",
                "source_type",
                "plot_style",
                "extraction_profile",
                "source_metadata",
                "warnings",
                "created_at",
                "updated_at",
            ),
        )
        plot_style = _require_dict(payload.get("plot_style"), field_name="plot_style")
        extraction_profile = _require_dict(payload.get("extraction_profile"), field_name="extraction_profile")
        source_metadata = _require_registry_metadata(
            _require_dict(payload.get("source_metadata"), field_name="source_metadata")
        )
        warnings_payload = payload.get("warnings")
        if not isinstance(warnings_payload, list):
            raise StylePresetRegistryError("Invalid style preset registry payload: field 'warnings' must be a list.")
        return cls(
            preset_id=_require_non_empty_string_field(payload, "preset_id"),
            display_name=_require_string_field(payload, "display_name"),
            source_type=_require_non_empty_string_field(payload, "source_type"),
            plot_style=plot_style,
            extraction_profile=extraction_profile,
            source_metadata=source_metadata,
            warnings=_normalize_warning_list(warnings_payload),
            created_at=_require_string_field(payload, "created_at"),
            updated_at=_require_string_field(payload, "updated_at"),
        )

    def to_style_profile_draft(self):
        from core.models.style_profile_draft import STYLE_PROFILE_DRAFT_WARNINGS, StyleProfileDraft

        source_metadata = dict(self.source_metadata)
        source_size = dict(source_metadata.get("source_size_px", {}))
        source_crop = dict(source_metadata.get("source_crop_rect", {}))
        warnings = _normalize_warning_list(self.warnings) or list(STYLE_PROFILE_DRAFT_WARNINGS)
        seed_preset_name = str(
            source_metadata.get("seed_preset_name")
            or source_metadata.get("builtin_preset_name")
            or "Nature"
        )
        return StyleProfileDraft(
            draft_id=str(self.preset_id),
            display_name=str(self.display_name),
            seed_preset_name=seed_preset_name,
            source_kind=str(source_metadata.get("source_kind", "saved_preset")),
            source_path=str(source_metadata.get("source_path", "")),
            source_page_index=int(source_metadata.get("source_page_index", 0) or 0),
            source_page_count=max(1, int(source_metadata.get("source_page_count", 1) or 1)),
            source_size_px={
                "width": int(source_size.get("width", 0) or 0),
                "height": int(source_size.get("height", 0) or 0),
            },
            source_crop_rect={
                "x": int(source_crop.get("x", 0) or 0),
                "y": int(source_crop.get("y", 0) or 0),
                "width": int(source_crop.get("width", 0) or 0),
                "height": int(source_crop.get("height", 0) or 0),
            },
            plot_style=_deep_copy_payload(dict(self.plot_style)),
            extraction_profile=_deep_copy_payload(dict(self.extraction_profile)),
            warnings=warnings,
        )


class StylePresetRegistry:
    def __init__(self, storage_path: str | Path) -> None:
        self.storage_path = Path(storage_path)

    def list_presets(self) -> list[StylePresetRecord]:
        return [StylePresetRecord.from_dict(record.to_dict()) for record in self._load_records()]

    def get_preset(self, preset_id: str) -> StylePresetRecord | None:
        for record in self._load_records():
            if record.preset_id == preset_id:
                return StylePresetRecord.from_dict(record.to_dict())
        return None

    def save_draft(self, draft, *, source_type: str = "extracted_draft", preset_id: str | None = None) -> StylePresetRecord:
        return self.save_preset(
            StylePresetRecord.from_style_profile_draft(draft, source_type=source_type, preset_id=preset_id)
        )

    def save_preset(self, preset: StylePresetRecord) -> StylePresetRecord:
        candidate = StylePresetRecord.from_dict(preset.to_dict())
        records = self._load_records()
        now = utc_now_iso()
        for index, existing in enumerate(records):
            if existing.preset_id != candidate.preset_id:
                continue
            candidate.created_at = existing.created_at or candidate.created_at or now
            candidate.updated_at = now
            records[index] = candidate
            self._write_records(records)
            return StylePresetRecord.from_dict(candidate.to_dict())
        candidate.created_at = candidate.created_at or now
        candidate.updated_at = now
        records.append(candidate)
        self._write_records(records)
        return StylePresetRecord.from_dict(candidate.to_dict())

    def rename_preset(self, preset_id: str, display_name: str) -> StylePresetRecord:
        record = self.get_preset(preset_id)
        if record is None:
            raise KeyError(f"Style preset not found: {preset_id}")
        normalized_name = display_name.strip()
        if normalized_name:
            record.display_name = normalized_name
        return self.save_preset(record)

    def duplicate_preset(self, preset_id: str, display_name: str | None = None) -> StylePresetRecord:
        record = self.get_preset(preset_id)
        if record is None:
            raise KeyError(f"Style preset not found: {preset_id}")
        duplicate = StylePresetRecord.from_dict(record.to_dict())
        duplicate.preset_id = uuid4().hex
        duplicate.display_name = str(display_name or f"{record.display_name} copy")
        duplicate.source_type = "user_modified"
        duplicate.created_at = ""
        duplicate.updated_at = ""
        return self.save_preset(duplicate)

    def delete_preset(self, preset_id: str) -> bool:
        records = self._load_records()
        remaining = [record for record in records if record.preset_id != preset_id]
        if len(remaining) == len(records):
            return False
        self._write_records(remaining)
        return True

    def _load_records(self) -> list[StylePresetRecord]:
        payload = self._load_payload()
        preset_payloads = _require_list(payload.get("presets"), field_name="presets")
        records: list[StylePresetRecord] = []
        for index, item in enumerate(preset_payloads):
            if not isinstance(item, dict):
                raise StylePresetRegistryError(
                    f"Invalid style preset registry payload: non-dict preset entry at index {index}."
                )
            records.append(StylePresetRecord.from_dict(dict(item)))
        record_ids = [record.preset_id for record in records]
        if len(record_ids) != len(set(record_ids)):
            raise StylePresetRegistryError("Invalid style preset registry payload: duplicate preset_id values detected.")
        return records

    def _load_payload(self) -> dict[str, Any]:
        try:
            storage_exists = self.storage_path.exists()
        except OSError as exc:
            raise StylePresetRegistryError("Could not inspect style preset registry storage.") from exc
        if not storage_exists:
            return {
                "schema_version": STYLE_PRESET_REGISTRY_SCHEMA_VERSION,
                "presets": [],
            }
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise StylePresetRegistryError("Could not read style preset registry.") from exc
        except json.JSONDecodeError as exc:
            raise StylePresetRegistryError("Invalid style preset registry JSON.") from exc
        if not isinstance(payload, dict):
            raise StylePresetRegistryError("Invalid style preset registry payload: root object must be a JSON object.")
        if "schema_version" not in payload:
            raise StylePresetRegistryError("Invalid style preset registry payload: missing required field 'schema_version'.")
        if payload.get("schema_version") != STYLE_PRESET_REGISTRY_SCHEMA_VERSION:
            raise StylePresetRegistryError(
                "Invalid style preset registry payload: unsupported schema_version."
            )
        if "presets" not in payload:
            raise StylePresetRegistryError("Invalid style preset registry payload: missing required field 'presets'.")
        return _deep_copy_payload(dict(payload))

    def _write_records(self, records: list[StylePresetRecord]) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StylePresetRegistryError("Could not prepare style preset storage.") from exc
        payload = {
            "schema_version": STYLE_PRESET_REGISTRY_SCHEMA_VERSION,
            "presets": [record.to_dict() for record in records],
        }
        tmp_path = self.storage_path.with_name(f"{self.storage_path.name}.{uuid4().hex}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=True)
                handle.write("\n")
        except (OSError, TypeError, ValueError) as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StylePresetRegistryError("Could not write style preset registry.") from exc
        try:
            tmp_path.replace(self.storage_path)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StylePresetRegistryError("Could not update style preset registry.") from exc
