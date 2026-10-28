"""User-wide named analysis presets. Independent of calibration and project files."""
from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication, QObject, QSettings, pyqtSignal


KEY = "analysis/named_presets"

ALLOWED_METHODS = {
    ("ihc", "auto_analyze_dab"),
    ("ihc", "measure_dab_positive_area"),
    ("brightfield", "measure_bright_round_cells"),
    ("brightfield", "create_ai_object_count_measurement"),
    ("brightfield", "create_measurement"),
    ("brightfield", "auto_analyze_he"),
    ("brightfield", "create_profile_preset_measurement"),
    ("fluorescence", "count_channel_objects"),
    ("fluorescence", "run_ai_assisted_analysis"),
    ("fluorescence", "roi_intensity"),
}

FORBIDDEN_KEYS = {
    "um_per_px",
    "um_per_px_x",
    "um_per_px_y",
    "calibration_source",
    "preset_id",
    "preset_name",
    "image_uid",
    "image_uuid",
    "uid",
    "roi",
    "geometry",
    "mask",
    "items",
    "coordinates_px",
    "results",
    "object_table",
    "detected_spots",
    "contour_points_px",
    "measurement_uuid",
    "ai_measurement_uuid",
    "smart_measurement_uuid",
    "count_uuid",
    "channel_indices",
}


class _LibraryEvents(QObject):
    changed = pyqtSignal()


def library_events():
    app = QCoreApplication.instance()
    if app is None:
        return None
    if not hasattr(app, "_analysis_library_events"):
        app._analysis_library_events = _LibraryEvents(app)
    return app._analysis_library_events


def _scalar(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value
    return None


def sanitize_settings(settings) -> dict:
    if not isinstance(settings, dict):
        return {}
    cleaned = {}
    for key, value in settings.items():
        name = str(key)
        if name in FORBIDDEN_KEYS:
            continue
        if isinstance(value, dict):
            nested = sanitize_settings(value)
            if nested:
                cleaned[name] = nested
            continue
        if isinstance(value, (list, tuple)):
            continue
        scalar = _scalar(value)
        if scalar is value or isinstance(value, (bool, int, float, str)) or value is None:
            cleaned[name] = scalar
    return cleaned


def method_allowed(module: str, method: str) -> bool:
    return (str(module), str(method)) in ALLOWED_METHODS


class AnalysisPresets:
    KEY = KEY

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings("ScieFig", "ScieFig")

    def items(self):
        self.settings.sync()
        raw = str(self.settings.value(self.KEY, "[]") or "[]")
        try:
            records = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return records if isinstance(records, list) else []

    def items_for(self, module: str, method: str):
        return [record for record in self.items() if record.get("module") == module and record.get("method") == method]

    def _write(self, records):
        self.settings.setValue(self.KEY, json.dumps(records, ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("Cannot save analysis presets")
        events = library_events()
        if events is not None:
            events.changed.emit()

    @staticmethod
    def _name(name, records, excluding=None):
        name = str(name or "").strip()
        if not name or any(r.get("name", "").casefold() == name.casefold() and r.get("id") != excluding for r in records):
            raise ValueError("preset_name_unique")
        return name

    def save(self, name, module, method, settings):
        module = str(module)
        method = str(method)
        if not method_allowed(module, method):
            raise ValueError("analysis_preset_incompatible")
        records = self.items()
        name = self._name(name, records)
        record = {
            "id": uuid4().hex,
            "name": name,
            "module": module,
            "method": method,
            "settings": sanitize_settings(deepcopy(settings) if isinstance(settings, dict) else {}),
        }
        self._write(records + [record])
        return record

    def rename(self, preset_id, name):
        records = self.items()
        name = self._name(name, records, preset_id)
        record = next(r for r in records if r.get("id") == preset_id)
        record["name"] = name
        self._write(records)
        return record

    def delete(self, preset_id):
        self._write([record for record in self.items() if record.get("id") != preset_id])

    def compatible(self, record, module, method) -> bool:
        if not isinstance(record, dict):
            return False
        return record.get("module") == module and record.get("method") == method and method_allowed(module, method)
