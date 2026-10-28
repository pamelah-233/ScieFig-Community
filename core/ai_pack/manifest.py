from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from core.constants import (
    AI_PACK_ALLOWED_COMMERCIAL_USE_STATUSES,
    AI_PACK_DEFAULT_COMMERCIAL_USE_STATUS,
    AI_PACK_MANIFEST_VERSION,
)


class ManifestError(ValueError):
    """Raised when a manifest payload is malformed."""


_SHA256_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_SAFE_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REQUIRED_ENTRY_FIELDS = {
    "model_id",
    "display_name",
    "version",
    "source_url",
    "sha256",
    "file_size_bytes",
    "license",
    "commercial_use_status",
    "input_schema",
    "output_schema",
    "postprocess",
    "cache_subdir",
}
_OPTIONAL_ENTRY_FIELDS = {"license_url", "description", "tags"}
_ALLOWED_ENTRY_FIELDS = _REQUIRED_ENTRY_FIELDS | _OPTIONAL_ENTRY_FIELDS


def _require_nonempty_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"Manifest entry missing required field: {key}")
    return value.strip()


def _require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"Manifest field {key!r} must be a positive integer") from None
    if value <= 0:
        raise ManifestError(f"Manifest field {key!r} must be > 0")
    return value


def _require_nonempty_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict) or not value:
        raise ManifestError(f"Manifest field {key!r} must be a non-empty dict")
    return dict(value)


def validate_cache_subdir(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise ManifestError("cache_subdir must be a non-empty relative path")
    if ":" in raw:
        raise ManifestError("cache_subdir must not contain drive syntax")
    if raw.startswith("/"):
        raise ManifestError("cache_subdir must be relative")
    segments = raw.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ManifestError("cache_subdir must not contain empty/current/parent segments")
    normalized = PurePosixPath(raw)
    if normalized.is_absolute():
        raise ManifestError("cache_subdir must be relative")
    return str(normalized)


@dataclass(slots=True)
class ModelManifestEntry:
    model_id: str
    display_name: str
    version: str
    source_url: str
    sha256: str
    file_size_bytes: int
    license: str
    commercial_use_status: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    postprocess: str
    cache_subdir: str
    license_url: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "version": self.version,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "file_size_bytes": int(self.file_size_bytes),
            "license": self.license,
            "commercial_use_status": self.commercial_use_status,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "postprocess": self.postprocess,
            "cache_subdir": self.cache_subdir,
            "license_url": self.license_url,
            "description": self.description,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModelManifestEntry":
        if not isinstance(payload, dict):
            raise ManifestError(f"Manifest entry must be a dict, got {type(payload).__name__}")

        unexpected_keys = sorted(set(payload) - _ALLOWED_ENTRY_FIELDS)
        if unexpected_keys:
            unexpected = ", ".join(unexpected_keys)
            raise ManifestError(f"Manifest entry contains unexpected fields: {unexpected}")

        required_string_fields = (
            "model_id",
            "display_name",
            "version",
            "source_url",
            "sha256",
            "license",
            "commercial_use_status",
            "postprocess",
            "cache_subdir",
        )
        values = {key: _require_nonempty_str(payload, key) for key in required_string_fields}
        file_size = _require_positive_int(payload, "file_size_bytes")
        input_schema = _require_nonempty_dict(payload, "input_schema")
        output_schema = _require_nonempty_dict(payload, "output_schema")

        sha256 = values["sha256"]
        if not _SHA256_RE.match(sha256):
            raise ManifestError("sha256 field must be formatted as 'sha256:' followed by 64 hex characters")

        model_id = values["model_id"]
        if not _SAFE_MODEL_ID_RE.match(model_id):
            raise ManifestError(f"Unsafe model_id in manifest entry: {model_id}")

        commercial_use_status = values["commercial_use_status"].lower()
        if commercial_use_status not in AI_PACK_ALLOWED_COMMERCIAL_USE_STATUSES:
            commercial_use_status = AI_PACK_DEFAULT_COMMERCIAL_USE_STATUS

        tags_value = payload.get("tags", [])
        if tags_value in (None, ""):
            tags: list[str] = []
        elif isinstance(tags_value, (list, tuple)):
            tags = [str(tag).strip() for tag in tags_value if str(tag).strip()]
        else:
            raise ManifestError("Manifest field 'tags' must be a list when provided")

        return cls(
            model_id=values["model_id"],
            display_name=values["display_name"],
            version=values["version"],
            source_url=values["source_url"],
            sha256=sha256,
            file_size_bytes=file_size,
            license=values["license"],
            commercial_use_status=commercial_use_status,
            input_schema=input_schema,
            output_schema=output_schema,
            postprocess=values["postprocess"],
            cache_subdir=validate_cache_subdir(values["cache_subdir"]),
            license_url=str(payload.get("license_url", "") or "").strip(),
            description=str(payload.get("description", "") or "").strip(),
            tags=tags,
        )

    def allows_commercial_use(self) -> bool:
        return self.commercial_use_status == "allowed"


@dataclass(slots=True)
class ModelManifest:
    manifest_version: str
    entries: list[ModelManifestEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModelManifest":
        if not isinstance(payload, dict):
            raise ManifestError("Manifest payload must be a dict")

        version = str(payload.get("manifest_version", "")).strip()
        if version != AI_PACK_MANIFEST_VERSION:
            raise ManifestError(
                f"Unsupported manifest_version: {version!r}; expected {AI_PACK_MANIFEST_VERSION!r}"
            )

        entries_payload = payload.get("entries", [])
        if not isinstance(entries_payload, list):
            raise ManifestError("Manifest 'entries' must be a list")

        entries = [ModelManifestEntry.from_dict(entry) for entry in entries_payload]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in entries:
            if entry.model_id in seen:
                duplicates.add(entry.model_id)
            seen.add(entry.model_id)
        if duplicates:
            duplicate_ids = ", ".join(sorted(duplicates))
            raise ManifestError(f"duplicate model_id in manifest: {duplicate_ids}")

        return cls(
            manifest_version=version,
            entries=entries,
        )

    @classmethod
    def load_from_file(cls, path: str | Path) -> "ModelManifest":
        target = Path(path)
        if not target.is_file():
            raise FileNotFoundError(f"Manifest file not found: {target}")
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)

    def find(self, model_id: str) -> ModelManifestEntry | None:
        target = str(model_id).strip()
        for entry in self.entries:
            if entry.model_id == target:
                return entry
        return None


def load_from_file(path: str | Path) -> ModelManifest:
    return ModelManifest.load_from_file(path)


def empty_manifest() -> ModelManifest:
    return ModelManifest(manifest_version=AI_PACK_MANIFEST_VERSION, entries=[])
