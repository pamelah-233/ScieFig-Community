from PyQt6.QtCore import QSettings
import pytest


def test_named_library_survives_restart_and_retains_axes(tmp_path):
    from core.utils.calibration_presets import CalibrationPresets
    path = str(tmp_path / 'prefs.ini')
    library = CalibrationPresets(QSettings(path, QSettings.Format.IniFormat))
    preset = library.save('显微镜 A · 20×', {'um_per_px_x': .25, 'um_per_px_y': .5})
    reopened = CalibrationPresets(QSettings(path, QSettings.Format.IniFormat))
    assert reopened.items() == [preset]
    renamed = reopened.rename(preset['id'], '显微镜 A · 相机2')
    assert renamed['id'] == preset['id']
    assert renamed['um_per_px_y'] == .5
    reopened.delete(preset['id'])
    assert library.items() == []


def test_presets_do_not_store_geometry_or_silently_overwrite(tmp_path):
    from core.utils.calibration_presets import CalibrationPresets
    library = CalibrationPresets(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    record = library.save('Scope', {'um_per_px': .4, 'line_px': {'x1': 1}, 'enabled': True})
    assert set(record) == {'id', 'name', 'um_per_px_x', 'um_per_px_y'}
    with pytest.raises(ValueError):
        library.save('Scope', {'um_per_px': 2})
    with pytest.raises(ValueError):
        library.save('Unknown', {'um_per_px': 0})
    with pytest.raises(ValueError):
        library.save('Missing Y', {'um_per_px_x': .4})
    assert library.items() == [record]
