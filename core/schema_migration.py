from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.constants import SCHEMA_VERSION


def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = str(version).split(".")
    values = [int(part) for part in parts[:3]]
    while len(values) < 3:
        values.append(0)
    return tuple(values)


def migrate_project_payload(project_payload: dict[str, Any], source_version: str) -> dict[str, Any]:
    if _version_tuple(source_version) > _version_tuple(SCHEMA_VERSION):
        raise ValueError("This .sfig file was created by a newer ScieFig schema. Please upgrade ScieFig.")

    migrated = deepcopy(project_payload)
    if _version_tuple(source_version) < _version_tuple("2.3.0"):
        migrated = migrate_2_1_1_to_2_3_0(migrated)

    migrated["schema_version"] = SCHEMA_VERSION
    for panel in migrated.get("panels", []):
        panel["schema_version"] = SCHEMA_VERSION
        panel.setdefault("source_module", _source_module_for_panel(panel.get("panel_type", "")))
    return migrated


def migrate_2_1_1_to_2_3_0(project_payload: dict[str, Any]) -> dict[str, Any]:
    project_payload.setdefault("panel_registry", _panel_registry_index(project_payload.get("panels", [])))
    project_payload.setdefault("composer_figures", [])
    project_payload.setdefault("active_figure_uid", None)
    return project_payload


def _panel_registry_index(panels: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "panel_uuid": str(panel.get("panel_uuid", "")),
            "panel_id": str(panel.get("panel_id", "")),
            "panel_type": str(panel.get("panel_type", "")),
            "source_module": _source_module_for_panel(panel.get("panel_type", "")),
        }
        for panel in panels
    ]


def _source_module_for_panel(panel_type: str) -> str:
    mapping = {
        "qPCR_bar_scatter": "qpcr",
        "western_blot": "wb",
        "fluorescence": "fluorescence",
        "external_image": "external",
    }
    return mapping.get(str(panel_type), "unknown")
