"""User-wide calibration library; deliberately independent of project files."""
from __future__ import annotations

import json
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication, QObject, QSettings, pyqtSignal
from core.measurement_scale import resolve_calibration


class _LibraryEvents(QObject):
    changed = pyqtSignal()


def library_events():
    app = QCoreApplication.instance()
    if app is None:
        return None
    if not hasattr(app, '_calibration_library_events'):
        app._calibration_library_events = _LibraryEvents(app)
    return app._calibration_library_events


class CalibrationPresets:
    KEY = 'calibration/named_presets'

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings('ScieFig', 'ScieFig')

    def items(self):
        self.settings.sync()
        return json.loads(str(self.settings.value(self.KEY, '[]')))

    def _write(self, records):
        self.settings.setValue(self.KEY, json.dumps(records, ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError('Cannot save calibration presets')
        events = library_events()
        if events is not None:
            events.changed.emit()

    @staticmethod
    def _name(name, records, excluding=None):
        name = name.strip()
        if not name or any(r['name'].casefold() == name.casefold() and r['id'] != excluding for r in records):
            raise ValueError('preset_name_unique')
        return name

    def save(self, name, scale):
        records = self.items()
        name = self._name(name, records)
        resolved = resolve_calibration({}, scale)
        if not resolved['calibrated'] or min(resolved['um_per_px_x'], resolved['um_per_px_y']) <= 0:
            raise ValueError('preset_requires_calibration')
        record = dict(id=uuid4().hex, name=name,
                      um_per_px_x=resolved['um_per_px_x'], um_per_px_y=resolved['um_per_px_y'])
        if isinstance(scale.get('scale_bar_template'), dict):
            from core.layout_scale_bar import normalize_scale_bar_template
            record['scale_bar_template'] = normalize_scale_bar_template(scale['scale_bar_template'])
        self._write(records + [record])
        return record

    def rename(self, preset_id, name):
        records = self.items()
        name = self._name(name, records, preset_id)
        record = next(r for r in records if r['id'] == preset_id)
        record['name'] = name
        self._write(records)
        return record

    def delete(self, preset_id):
        self._write([r for r in self.items() if r['id'] != preset_id])
