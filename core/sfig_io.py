from __future__ import annotations

from collections import Counter
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from typing import Any
import zlib
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import numpy as np

from core.constants import SCHEMA_VERSION, SFIG_REQUIRED_DIRS, SFIG_REQUIRED_FILES, SOFTWARE_VERSION
from core.io.migrations.ihc_hscore_v611 import migrate_ihc_hscore_v611
from core.models.composer import ComposerFigure
from core.models.external_image_panel import ExternalImagePanel
from core.models.panel import panel_from_dict
from core.models.project import Project
from core.schema_migration import migrate_project_payload
from core.utils.hashing import sha256_bytes, sha256_file, sha256_json
from core.utils.time import utc_now_iso


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _project_audit_log(project: Project) -> str:
    lines = [json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) for entry in project.audit_log]
    return "\n".join(lines) + ("\n" if lines else "")


def _panel_audit_log(panel) -> str:
    lines = [json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) for entry in panel.audit_log]
    return "\n".join(lines) + ("\n" if lines else "")


def _panel_schema() -> dict[str, Any]:
    required = [
        "schema_version",
        "software_version",
        "panel_uuid",
        "panel_id",
        "panel_type",
        "source_module",
        "created_at",
        "modified_at",
        "data",
        "analysis",
        "style",
        "export",
        "audit_log",
        "coordinate_system",
        "unit_system",
    ]
    return {"schema_version": SCHEMA_VERSION, "type": "object", "required": required}


def _project_schema() -> dict[str, Any]:
    required = [
        "schema_version",
        "software_version",
        "project_uuid",
        "title",
        "created_at",
        "modified_at",
        "panels",
        "panel_registry",
        "composer_figures",
        "active_figure_uid",
        "composer_layout",
        "audit_log",
    ]
    return {"schema_version": SCHEMA_VERSION, "type": "object", "required": required}


_ArchiveSource = Path | bytes


def _member_display_name(name: object) -> str:
    """Keep archive-member diagnostics bounded and independent of the host filesystem."""
    return repr(str(name))[:160]


def _validate_member_name(name: object, *, allow_directory: bool = False) -> str:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        raise ValueError(f"Invalid .sfig archive member path: {_member_display_name(name)}")
    path_name = name.rstrip("/") if name.endswith("/") else name
    path = PurePosixPath(path_name)
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path_name.split("/")):
        raise ValueError(f"Invalid .sfig archive member path: {_member_display_name(name)}")
    if name.endswith("/") and not allow_directory:
        raise ValueError(f"Invalid .sfig archive member path: {_member_display_name(name)}")
    return name


def _validated_archive_names(archive: ZipFile) -> set[str]:
    names = archive.namelist()
    duplicate_names = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicate_names:
        raise ValueError(f"Invalid .sfig archive; duplicate member: {_member_display_name(duplicate_names[0])}")
    for name in names:
        _validate_member_name(name, allow_directory=name.endswith("/"))
    return set(names)


def _sha256_archive_member(archive: ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _resolve_legacy_raw_member_name(declared_name: object, archive_names: set[str]) -> str:
    name = _validate_member_name(declared_name)
    if name in archive_names:
        return name
    if name.startswith("data/"):
        raise ValueError(f"Manifest raw member is missing: {_member_display_name(name)}")

    candidates = {candidate for candidate in archive_names if candidate == f"data/{name}" or candidate.endswith(f"/{name}")}
    if len(candidates) == 1:
        return next(iter(candidates))
    if not candidates:
        raise ValueError(f"Manifest raw member is missing: {_member_display_name(name)}")
    raise ValueError(f"Manifest raw member is ambiguous: {_member_display_name(name)}")


def _validate_manifest_members(archive: ZipFile, manifest: dict[str, Any], archive_names: set[str]) -> None:
    composer_hashes = manifest.get("composer_json_hashes", {})
    if composer_hashes is None:
        composer_hashes = {}
    if not isinstance(composer_hashes, dict):
        raise ValueError("Invalid manifest composer_json_hashes.")
    for declared_name, expected_hash in composer_hashes.items():
        name = _validate_member_name(declared_name)
        if name not in archive_names:
            raise ValueError(f"Manifest composer member is missing: {_member_display_name(name)}")
        if not isinstance(expected_hash, str):
            raise ValueError(f"Manifest composer hash is invalid: {_member_display_name(name)}")
        try:
            payload = json.loads(archive.read(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, BadZipFile, OSError, RuntimeError, zlib.error) as exc:
            raise ValueError(f"Manifest composer member is invalid JSON: {_member_display_name(name)}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Manifest composer member is not an object: {_member_display_name(name)}")
        if sha256_json(payload) != expected_hash:
            raise ValueError(f"Manifest composer member hash check failed: {_member_display_name(name)}")

    raw_hashes = manifest.get("raw_hashes", {})
    if raw_hashes is None:
        raw_hashes = {}
    if not isinstance(raw_hashes, dict):
        raise ValueError("Invalid manifest raw_hashes.")
    for declared_name, expected_hash in raw_hashes.items():
        name = _resolve_legacy_raw_member_name(declared_name, archive_names)
        if not isinstance(expected_hash, str):
            raise ValueError(f"Manifest raw hash is invalid: {_member_display_name(declared_name)}")
        try:
            actual_hash = _sha256_archive_member(archive, name)
        except (BadZipFile, OSError, RuntimeError, zlib.error) as exc:
            raise ValueError(f"Manifest raw member is unreadable: {_member_display_name(declared_name)}") from exc
        if actual_hash != expected_hash:
            raise ValueError(f"Manifest raw member hash check failed: {_member_display_name(declared_name)}")


def _archive_member_hash(source: _ArchiveSource) -> str:
    return sha256_bytes(source) if isinstance(source, bytes) else sha256_file(source)


def _add_archive_member(members: dict[str, _ArchiveSource], name: str, source: _ArchiveSource, reason: str) -> None:
    name = _validate_member_name(name)
    if name in members:
        raise ValueError(f"Duplicate archive asset member for {reason}: {_member_display_name(name)}")
    if isinstance(source, Path) and not source.is_file():
        raise ValueError(f"Referenced asset is unavailable for {reason}: {_member_display_name(source)}")
    members[name] = source


def _composer_payloads(project: Project) -> dict[str, dict[str, Any]]:
    project.ensure_active_composer_figure()
    return {figure.file_name: figure.to_dict() for figure in project.composer_figures}


def _wb_strip_archive_name(panel_uuid: str, strip: dict[str, Any], fallback_index: int) -> str:
    strip_id = str(strip.get("strip_id") or f"strip_{fallback_index}")
    source_path = Path(str(strip.get("resolved_image_path") or strip.get("image_path") or ""))
    suffix = source_path.suffix.lower() or ".png"
    return f"data/wb/{panel_uuid}/strips/{strip_id}{suffix}"


def _archive_record_stem(image: dict[str, Any], index: int) -> str:
    uid = str(image.get("uid", "")).strip()
    safe_uid = "".join(char if char.isascii() and (char.isalnum() or char in "-_") else "_" for char in uid)
    return f"{index:04d}_{safe_uid or 'record'}"


def _image_archive_suffix(image: dict[str, Any]) -> str:
    source_name = str(image.get("source_name") or Path(str(image.get("source_path", ""))).name)
    suffix = Path(source_name).suffix.lower()
    return suffix or ".bin"


def _image_archive_basename(image: dict[str, Any]) -> str:
    source_name = str(image.get("source_name") or Path(str(image.get("source_path", ""))).name)
    return Path(source_name).name or f"source{_image_archive_suffix(image)}"


def _image_archive_directory(kind: str, panel_uuid: str, image: dict[str, Any], index: int) -> str:
    return f"data/{kind}/{panel_uuid}/{_archive_record_stem(image, index)}"


def _fluorescence_image_archive_name(panel_uuid: str, image: dict[str, Any], index: int) -> str:
    return f"{_image_archive_directory('fluorescence', panel_uuid, image, index)}/{_image_archive_basename(image)}"


def _fluorescence_data_archive_name(panel_uuid: str, image: dict[str, Any], index: int) -> str:
    return f"{_image_archive_directory('fluorescence', panel_uuid, image, index)}/analysis.npy"


def _ihc_image_archive_name(panel_uuid: str, image: dict[str, Any], index: int) -> str:
    return f"{_image_archive_directory('ihc', panel_uuid, image, index)}/{_image_archive_basename(image)}"


def _brightfield_image_archive_name(panel_uuid: str, image: dict[str, Any], index: int) -> str:
    return f"{_image_archive_directory('brightfield', panel_uuid, image, index)}/{_image_archive_basename(image)}"


def _legacy_fluorescence_image_archive_name(panel_uuid: str, image: dict[str, Any]) -> str:
    source_name = str(image.get("source_name") or Path(str(image.get("source_path", ""))).name)
    return f"data/fluorescence/{panel_uuid}/{source_name}"


def _legacy_ihc_image_archive_name(panel_uuid: str, image: dict[str, Any]) -> str:
    source_name = str(image.get("source_name") or Path(str(image.get("source_path", ""))).name)
    return f"data/ihc/{panel_uuid}/{source_name}"


def _legacy_brightfield_image_archive_name(panel_uuid: str, image: dict[str, Any]) -> str:
    source_name = str(image.get("source_name") or Path(str(image.get("source_path", ""))).name)
    return f"data/brightfield/{panel_uuid}/{source_name}"


def _external_image_archive_name(panel_uuid: str, data: dict[str, Any]) -> str:
    return ExternalImagePanel.archive_copy_path_for(panel_uuid, data)


def _path_from_value(value: object) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(str(value))


def _first_existing_file(*candidates: object) -> Path | None:
    for candidate in candidates:
        path = _path_from_value(candidate)
        if path is not None and path.is_file():
            return path
    return None


def _has_reference(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def _referenced_external_source(panel) -> Path | None:
    data = panel.data
    has_reference = getattr(panel, "raw_source_path", None) is not None or any(
        _has_reference(data.get(key))
        for key in ("raw_source", "raw_hash", "raw_copy_path", "embedded_copy_path", "image_path")
    )
    if not has_reference:
        return None
    source = _first_existing_file(getattr(panel, "raw_source_path", None), data.get("image_path"))
    if source is None:
        raise ValueError(f"Referenced external image asset is unavailable: {_member_display_name(panel.panel_id)}")
    return source


def _referenced_wb_source(panel) -> Path | None:
    data = panel.data
    has_reference = getattr(panel, "raw_source_path", None) is not None or any(
        _has_reference(data.get(key)) for key in ("raw_source", "raw_hash", "raw_copy_path", "image_path", "image_data")
    )
    if not has_reference:
        return None
    source = _first_existing_file(getattr(panel, "raw_source_path", None), data.get("image_path"))
    if source is None:
        raise ValueError(f"Referenced Western Blot image asset is unavailable: {_member_display_name(panel.panel_id)}")
    return source


def _collect_archive_members(project: Project) -> dict[str, _ArchiveSource]:
    members: dict[str, _ArchiveSource] = {}
    for panel in project.panels:
        panel_type = str(panel.panel_type)

        if panel_type == "fluorescence":
            for index, image in enumerate(panel.data.get("fluorescence_images", [])):
                source_path = _path_from_value(image.get("source_path"))
                if source_path is not None and source_path.is_file():
                    _add_archive_member(
                        members,
                        _fluorescence_image_archive_name(panel.panel_uuid, image, index),
                        source_path,
                        f"fluorescence image {image.get('uid', index)}",
                    )
                    continue
                cached = panel.image_data(str(image.get("uid", "")))
                if not isinstance(cached, np.ndarray):
                    raise ValueError(
                        f"Referenced fluorescence image asset is unavailable: "
                        f"{_member_display_name(image.get('source_name') or image.get('uid') or index)}"
                    )
                buffer = BytesIO()
                try:
                    np.save(buffer, cached, allow_pickle=False)
                except (OSError, ValueError, TypeError) as exc:
                    raise ValueError(
                        f"Cached fluorescence image cannot be serialized: "
                        f"{_member_display_name(image.get('source_name') or image.get('uid') or index)}"
                    ) from exc
                _add_archive_member(
                    members,
                    _fluorescence_data_archive_name(panel.panel_uuid, image, index),
                    buffer.getvalue(),
                    f"cached fluorescence image {image.get('uid', index)}",
                )

        if panel_type == "ihc":
            for index, image in enumerate(panel.data.get("ihc_images", [])):
                source_path = _path_from_value(image.get("source_path"))
                if source_path is None or not source_path.is_file():
                    raise ValueError(
                        f"Referenced IHC image asset is unavailable: "
                        f"{_member_display_name(image.get('source_name') or image.get('uid') or index)}"
                    )
                _add_archive_member(
                    members,
                    _ihc_image_archive_name(panel.panel_uuid, image, index),
                    source_path,
                    f"IHC image {image.get('uid', index)}",
                )

        if panel_type == "brightfield":
            for index, image in enumerate(panel.data.get("brightfield_images", [])):
                source_path = _path_from_value(image.get("source_path"))
                if source_path is None or not source_path.is_file():
                    raise ValueError(
                        f"Referenced Brightfield image asset is unavailable: "
                        f"{_member_display_name(image.get('source_name') or image.get('uid') or index)}"
                    )
                _add_archive_member(
                    members,
                    _brightfield_image_archive_name(panel.panel_uuid, image, index),
                    source_path,
                    f"Brightfield image {image.get('uid', index)}",
                )

        if panel_type == "external_image":
            source_path = _referenced_external_source(panel)
            if source_path is not None:
                _add_archive_member(
                    members,
                    _external_image_archive_name(panel.panel_uuid, panel.data),
                    source_path,
                    f"external image {panel.panel_id}",
                )

        if panel_type == "western_blot":
            source_path = _referenced_wb_source(panel)
            if source_path is not None:
                raw_copy_path = str(panel.data.get("raw_copy_path", "")).strip()
                archive_name = raw_copy_path or f"data/wb/{source_path.name}"
                _add_archive_member(members, archive_name, source_path, f"Western Blot image {panel.panel_id}")
            for index, strip in enumerate(panel.data.get("band_strips", []), start=1):
                has_reference = any(
                    _has_reference(strip.get(key))
                    for key in ("resolved_image_path", "image_path", "embedded_image_path", "image_hash", "original_source")
                )
                if not has_reference:
                    continue
                source_path = _first_existing_file(strip.get("resolved_image_path"), strip.get("image_path"))
                if source_path is None:
                    raise ValueError(
                        f"Referenced Western Blot strip asset is unavailable: "
                        f"{_member_display_name(strip.get('strip_id') or index)}"
                    )
                _add_archive_member(
                    members,
                    _wb_strip_archive_name(panel.panel_uuid, strip, index),
                    source_path,
                    f"Western Blot strip {strip.get('strip_id', index)}",
                )

        if panel_type not in {"fluorescence", "ihc", "brightfield", "external_image", "western_blot"}:
            raw_source_path = _path_from_value(getattr(panel, "raw_source_path", None))
            raw_copy_path = str(panel.data.get("raw_copy_path", "")).strip()
            if raw_source_path is not None and raw_copy_path:
                _add_archive_member(members, raw_copy_path.replace("\\", "/"), raw_source_path, f"raw panel data {panel.panel_id}")

    return members


def _collect_raw_hashes(project: Project) -> dict[str, str]:
    return {name: _archive_member_hash(source) for name, source in _collect_archive_members(project).items()}


def _is_valid_panel_payload(panel_payload: dict[str, Any]) -> bool:
    try:
        panel_from_dict(panel_payload)
    except Exception:
        return False
    return True


def _preflight_duplicate_panels(project_payload: dict[str, Any]) -> list[str]:
    panels = project_payload.get("panels", [])
    if not isinstance(panels, list):
        return []

    first_index_by_uuid: dict[str, int] = {}
    chosen_index_by_uuid: dict[str, int] = {}
    for index, panel_payload in enumerate(panels):
        if not isinstance(panel_payload, dict):
            continue
        panel_uuid = str(panel_payload.get("panel_uuid", "")).strip()
        if not panel_uuid:
            continue
        first_index_by_uuid.setdefault(panel_uuid, index)
        if panel_uuid not in chosen_index_by_uuid and _is_valid_panel_payload(panel_payload):
            chosen_index_by_uuid[panel_uuid] = index

    for panel_uuid, first_index in first_index_by_uuid.items():
        chosen_index_by_uuid.setdefault(panel_uuid, first_index)

    deduped_panels: list[Any] = []
    skipped_duplicates: list[str] = []
    kept_uuids: set[str] = set()
    for index, panel_payload in enumerate(panels):
        if not isinstance(panel_payload, dict):
            deduped_panels.append(panel_payload)
            continue
        panel_uuid = str(panel_payload.get("panel_uuid", "")).strip()
        if not panel_uuid:
            deduped_panels.append(panel_payload)
            continue
        if panel_uuid in kept_uuids or chosen_index_by_uuid.get(panel_uuid) != index:
            skipped_duplicates.append(panel_uuid)
            continue
        deduped_panels.append(panel_payload)
        kept_uuids.add(panel_uuid)

    project_payload["panels"] = deduped_panels

    panel_registry = project_payload.get("panel_registry", [])
    if isinstance(panel_registry, list):
        deduped_registry: list[dict[str, Any]] = []
        registry_uuids: set[str] = set()
        for entry in panel_registry:
            if not isinstance(entry, dict):
                continue
            panel_uuid = str(entry.get("panel_uuid", "")).strip()
            if not panel_uuid or panel_uuid in registry_uuids or panel_uuid not in kept_uuids:
                continue
            deduped_registry.append(entry)
            registry_uuids.add(panel_uuid)
        project_payload["panel_registry"] = deduped_registry

    return skipped_duplicates


def _apply_load_time_panel_migrations(project_payload: dict[str, Any]) -> None:
    panels = project_payload.get("panels", [])
    if not isinstance(panels, list):
        return
    for panel_payload in panels:
        if not isinstance(panel_payload, dict):
            continue
        if str(panel_payload.get("panel_type", "")) == "ihc":
            migrate_ihc_hscore_v611(panel_payload)


def _archive_ready_project_payload(project: Project) -> dict[str, Any]:
    payload = deepcopy(project.to_dict())
    for panel_payload in payload.get("panels", []):
        panel_uuid = str(panel_payload.get("panel_uuid", ""))
        if str(panel_payload.get("panel_type", "")) == "external_image":
            panel = project.get_panel(panel_uuid)
            if _referenced_external_source(panel) is None:
                continue
            archive_path = _external_image_archive_name(panel_uuid, panel_payload.get("data", {}))
            panel_payload.setdefault("data", {})
            panel_payload["data"]["raw_copy_path"] = archive_path
            panel_payload["data"]["embedded_copy_path"] = archive_path
        if str(panel_payload.get("panel_type", "")) != "western_blot":
            continue
        strips = panel_payload.get("data", {}).get("band_strips", [])
        for index, strip in enumerate(strips, start=1):
            source_path = Path(str(strip.get("resolved_image_path") or strip.get("image_path") or ""))
            if source_path.exists():
                strip["embedded_image_path"] = _wb_strip_archive_name(panel_uuid, strip, index)
    return payload


def build_manifest(
    project: Project,
    project_payload: dict[str, Any],
    composer_payloads: dict[str, dict[str, Any]],
    raw_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "software_version": SOFTWARE_VERSION,
        "project_uuid": project.project_uuid,
        "created_at": project.created_at,
        "modified_at": project.modified_at,
        "saved_at": utc_now_iso(),
        "created_platform": platform.platform(),
        "dependencies": {
            "python": sys.version.split()[0],
        },
        "project_json_hash": sha256_json(project_payload),
        "composer_json_hashes": {path: sha256_json(payload) for path, payload in composer_payloads.items()},
        "raw_hashes": dict(_collect_raw_hashes(project) if raw_hashes is None else raw_hashes),
    }


def save_project(project: Project, path: str | Path) -> None:
    target = Path(path)
    if target.suffix.lower() != ".sfig":
        raise ValueError("ScieFig project files must use the .sfig extension.")

    project.assert_unique_panel_uuids()
    project.append_audit("save_project", target.name)
    project.ensure_active_composer_figure()
    project_payload = _archive_ready_project_payload(project)
    composer_payloads = _composer_payloads(project)
    archive_members = _collect_archive_members(project)
    raw_hashes = {name: _archive_member_hash(source) for name, source in archive_members.items()}
    manifest = build_manifest(project, project_payload, composer_payloads, raw_hashes)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    os.close(temporary_fd)
    temporary_path = Path(temporary_name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            for directory in SFIG_REQUIRED_DIRS:
                archive.writestr(directory, b"")
            archive.writestr("manifest.json", _json_bytes(manifest))
            archive.writestr("project.json", _json_bytes(project_payload))
            for composer_path, composer_payload in composer_payloads.items():
                archive.writestr(composer_path, _json_bytes(composer_payload))
            for archive_name, source in archive_members.items():
                if isinstance(source, bytes):
                    archive.writestr(archive_name, source)
                else:
                    archive.write(source, archive_name)
            for panel in project.panels:
                archive.writestr(f"logs/panel_audit/{panel.panel_uuid}.log", _panel_audit_log(panel))
            archive.writestr("schema/schema_version.json", _json_bytes({"schema_version": SCHEMA_VERSION, "software_version": SOFTWARE_VERSION}))
            archive.writestr("schema/panel_schema_2.3.0.json", _json_bytes(_panel_schema()))
            archive.writestr("schema/project_schema_2.3.0.json", _json_bytes(_project_schema()))
            archive.writestr(
                "schema/migrations/index.json",
                _json_bytes({"schema_version": SCHEMA_VERSION, "migrations": ["migrate_2_1_1_to_2_3_0"]}),
            )
            archive.writestr("logs/project_audit.log", _project_audit_log(project))
            archive.writestr("logs/export_history.log", "")
            archive.writestr(
                "style/active_template.json",
                _json_bytes({"preset": "Nature", "source": "default_phase_0"}),
            )
        _validate_archive_file(temporary_path)
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _read_json_member(archive: ZipFile, name: str) -> Any:
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, BadZipFile, OSError, RuntimeError, zlib.error) as exc:
        raise ValueError(f"Invalid .sfig JSON member: {_member_display_name(name)}") from exc


def _validate_archive_crc(archive: ZipFile) -> None:
    try:
        corrupt_member = archive.testzip()
    except (BadZipFile, OSError, RuntimeError, zlib.error) as exc:
        raise ValueError("Invalid .sfig archive member CRC.") from exc
    if corrupt_member is not None:
        raise ValueError(f"Invalid .sfig archive member CRC: {_member_display_name(corrupt_member)}")


def _validate_archive_file(path: Path) -> None:
    try:
        with ZipFile(path, "r") as archive:
            archive_names = _validated_archive_names(archive)
            missing = (set(SFIG_REQUIRED_DIRS) | set(SFIG_REQUIRED_FILES)) - archive_names
            if missing:
                raise ValueError(f"Invalid .sfig archive; missing: {', '.join(sorted(missing))}")
            manifest = _read_json_member(archive, "manifest.json")
            project_payload = _read_json_member(archive, "project.json")
            if not isinstance(manifest, dict) or not isinstance(project_payload, dict):
                raise ValueError("Invalid .sfig archive; manifest.json and project.json must be objects.")
            expected_hash = manifest.get("project_json_hash")
            if expected_hash and expected_hash != sha256_json(project_payload):
                raise ValueError("project.json hash check failed.")
            _validate_manifest_members(archive, manifest, archive_names)
            _validate_archive_crc(archive)
    except BadZipFile as exc:
        raise ValueError("Invalid .sfig archive.") from exc


def _extract_archive_data(archive: ZipFile, source: Path) -> Path:
    extract_root = Path(tempfile.mkdtemp(prefix=f"sciefig_{source.stem}_"))
    try:
        for name in archive.namelist():
            if name.startswith("data/") and not name.endswith("/"):
                archive.extract(name, extract_root)
    except Exception:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise
    return extract_root


def _remap_extracted_paths(project: Project, extract_root: Path) -> None:
    for panel in project.panels:
        raw_copy_path = str(panel.data.get("raw_copy_path", "")).strip()
        if raw_copy_path:
            extracted_raw = extract_root / Path(raw_copy_path)
            if extracted_raw.exists():
                panel.raw_source_path = extracted_raw
                if str(panel.panel_type) in {"western_blot", "external_image"}:
                    panel.data["image_path"] = str(extracted_raw)
        if str(panel.panel_type) == "external_image":
            # Older saves gave untouched placeholders an archive path without an asset.
            placeholder_path = _external_image_archive_name(panel.panel_uuid, {})
            if (
                panel.data.get("raw_copy_path") == placeholder_path
                and panel.data.get("embedded_copy_path") == placeholder_path
                and not (extract_root / placeholder_path).exists()
                and getattr(panel, "raw_source_path", None) is None
                and not any(_has_reference(panel.data.get(key)) for key in ("raw_source", "raw_hash", "image_path"))
            ):
                panel.data.pop("raw_copy_path", None)
                panel.data.pop("embedded_copy_path", None)
            embedded_copy_path = str(panel.data.get("embedded_copy_path", "")).strip()
            if embedded_copy_path:
                extracted_image = extract_root / Path(embedded_copy_path)
                if extracted_image.exists():
                    panel.data["image_path"] = str(extracted_image)
        if str(panel.panel_type) == "fluorescence":
            for index, image in enumerate(panel.data.get("fluorescence_images", [])):
                new_image_path = extract_root / Path(_fluorescence_image_archive_name(panel.panel_uuid, image, index))
                legacy_image_path = extract_root / Path(_legacy_fluorescence_image_archive_name(panel.panel_uuid, image))
                if new_image_path.is_file():
                    image["source_path"] = str(new_image_path)
                elif legacy_image_path.is_file():
                    image["source_path"] = str(legacy_image_path)
                extracted_data = extract_root / Path(_fluorescence_data_archive_name(panel.panel_uuid, image, index))
                if extracted_data.is_file():
                    try:
                        with extracted_data.open("rb") as handle:
                            cached = np.load(handle, allow_pickle=False)
                    except (OSError, ValueError) as exc:
                        raise ValueError(f"Invalid embedded fluorescence data: {extracted_data.relative_to(extract_root)}") from exc
                    panel.cache_image_data(str(image.get("uid", "")), np.asarray(cached))
        if str(panel.panel_type) == "ihc":
            for index, image in enumerate(panel.data.get("ihc_images", [])):
                new_image_path = extract_root / Path(_ihc_image_archive_name(panel.panel_uuid, image, index))
                legacy_image_path = extract_root / Path(_legacy_ihc_image_archive_name(panel.panel_uuid, image))
                extracted_image = new_image_path if new_image_path.is_file() else legacy_image_path
                if extracted_image.is_file():
                    image["source_path"] = str(extracted_image)
        if str(panel.panel_type) == "brightfield":
            for index, image in enumerate(panel.data.get("brightfield_images", [])):
                new_image_path = extract_root / Path(_brightfield_image_archive_name(panel.panel_uuid, image, index))
                legacy_image_path = extract_root / Path(_legacy_brightfield_image_archive_name(panel.panel_uuid, image))
                extracted_image = new_image_path if new_image_path.is_file() else legacy_image_path
                if extracted_image.is_file():
                    image["source_path"] = str(extracted_image)
        if str(panel.panel_type) == "western_blot":
            for strip in panel.data.get("band_strips", []):
                embedded_image_path = str(strip.get("embedded_image_path", "")).strip()
                if not embedded_image_path:
                    continue
                extracted_strip = extract_root / Path(embedded_image_path)
                if extracted_strip.exists():
                    strip["resolved_image_path"] = str(extracted_strip)
                    strip["image_path"] = str(extracted_strip)


def load_project(path: str | Path) -> Project:
    source = Path(path)
    extract_root: Path | None = None
    project: Project | None = None
    try:
        with ZipFile(source, "r") as archive:
            names = _validated_archive_names(archive)
            missing = {"manifest.json", "project.json"} - names
            if missing:
                raise ValueError(f"Invalid .sfig archive; missing: {', '.join(sorted(missing))}")
            manifest = _read_json_member(archive, "manifest.json")
            project_payload = _read_json_member(archive, "project.json")
            if not isinstance(manifest, dict) or not isinstance(project_payload, dict):
                raise ValueError("Invalid .sfig archive; manifest.json and project.json must be objects.")
            expected_hash = manifest.get("project_json_hash")
            if expected_hash and expected_hash != sha256_json(project_payload):
                raise ValueError("project.json hash check failed.")
            _validate_manifest_members(archive, manifest, names)
            _validate_archive_crc(archive)
            source_schema = str(manifest.get("schema_version") or project_payload.get("schema_version", "0.0.0"))
            project_payload = migrate_project_payload(project_payload, source_schema)
            skipped_duplicate_uuids = _preflight_duplicate_panels(project_payload)
            _apply_load_time_panel_migrations(project_payload)
            extract_root = _extract_archive_data(archive, source)
            composer_figures: list[ComposerFigure] = []
            for index_entry in project_payload.get("composer_figures", []):
                if not isinstance(index_entry, dict):
                    continue
                composer_path = str(index_entry.get("file", ""))
                if composer_path and composer_path in names:
                    _validate_member_name(composer_path)
                    composer_payload = _read_json_member(archive, composer_path)
                    if not isinstance(composer_payload, dict):
                        raise ValueError(f"Invalid composer member: {_member_display_name(composer_path)}")
                    try:
                        composer_figures.append(ComposerFigure.from_dict(composer_payload))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(f"Invalid composer member: {_member_display_name(composer_path)}") from exc

        project = Project.from_dict(project_payload)
        project.register_archive_extract_root(extract_root)
        extract_root = None
        _remap_extracted_paths(project, project.archive_extract_root)
        if composer_figures:
            project.set_composer_figures(composer_figures, project_payload.get("active_figure_uid"))
        else:
            project.ensure_active_composer_figure()
        if str(manifest.get("schema_version")) != SCHEMA_VERSION:
            project.append_audit("migrate_schema", f"{manifest.get('schema_version')} -> {SCHEMA_VERSION}")
        project.append_audit("load_project", source.name)
        for panel_uuid in skipped_duplicate_uuids:
            project.append_audit("load_warning", f"duplicate panel_uuid skipped: {panel_uuid}")
        return project
    except Exception:
        if project is not None:
            project.dispose()
        elif extract_root is not None:
            shutil.rmtree(extract_root, ignore_errors=True)
        raise
