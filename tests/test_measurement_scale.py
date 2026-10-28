from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.measurement_scale import line_length_px, resolve_measurement_scale
from core.models import BrightfieldPanel, FluorescencePanel
import ui.main_window as main_window_module
from ui.main_window import MainWindow
from ui.panels.fluorescence_workspace import FluorescenceWorkspace


STALE_UM_PER_PX = 0.173611
LENGTH_UM = 100.0
NEW_LINE = {"x1": 0.0, "y1": 10.0, "x2": 227.0, "y2": 10.0}
TRANSLATED_LINE = {"x1": 15.0, "y1": 25.0, "x2": 242.0, "y2": 25.0}
EXPECTED_UM_PER_PX = LENGTH_UM / 227.0


def _image_record() -> dict[str, dict[str, int]]:
    return {"metadata": {"width_px": 1024, "height_px": 768}}


def _stale_scale() -> dict[str, object]:
    return {
        "enabled": True,
        "length_um": LENGTH_UM,
        "um_per_px": STALE_UM_PER_PX,
        "line_px": {"x1": 0.0, "y1": 0.0, "x2": 576.0, "y2": 0.0},
        "calibration_source": "assumed",
    }


def _write_png(path: Path) -> Path:
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 200, 200))
    assert image.save(str(path))
    return path


def test_redrawn_manual_line_recomputes_um_per_px_instead_of_cached_value() -> None:
    current = _stale_scale()
    current.update(
        {
            "length_um": LENGTH_UM,
            "line_px": dict(NEW_LINE),
            "calibration_source": "manual_line",
        }
    )

    resolved = resolve_measurement_scale(_image_record(), current)

    assert resolved["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert resolved["um_per_px"] != pytest.approx(STALE_UM_PER_PX)
    assert resolved["calibration_source"] == "manual_line"
    assert resolved["line_length_px"] == pytest.approx(227.0)
    assert resolved["um_per_px"] * resolved["line_length_px"] == pytest.approx(LENGTH_UM)


def test_typed_um_per_px_takes_priority_over_old_line() -> None:
    current = {
        "enabled": True,
        "length_um": LENGTH_UM,
        "um_per_px": EXPECTED_UM_PER_PX,
        "line_px": dict(NEW_LINE),
        "calibration_source": "manual_line",
    }
    current.update({"length_um": LENGTH_UM, "um_per_px": 0.5, "calibration_source": "manual"})

    resolved = resolve_measurement_scale(_image_record(), current)

    assert resolved["um_per_px"] == pytest.approx(0.5)
    assert resolved["calibration_source"] == "manual"
    assert resolved["line_px"] == NEW_LINE


def test_translated_manual_line_keeps_calibration_when_length_unchanged() -> None:
    current = {
        "enabled": True,
        "length_um": LENGTH_UM,
        "um_per_px": EXPECTED_UM_PER_PX,
        "line_px": dict(NEW_LINE),
        "calibration_source": "manual_line",
    }
    current.update(
        {
            "length_um": LENGTH_UM,
            "line_px": dict(TRANSLATED_LINE),
            "calibration_source": "manual_line",
        }
    )

    resolved = resolve_measurement_scale(_image_record(), current)

    assert line_length_px(resolved["line_px"]) == pytest.approx(227.0)
    assert resolved["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert resolved["calibration_source"] == "manual_line"
    assert resolved["line_px"] == TRANSLATED_LINE
    assert resolved["um_per_px"] * resolved["line_length_px"] == pytest.approx(LENGTH_UM)


def test_brightfield_update_measurement_scale_bar_redraws_line_over_cached_um_per_px(tmp_path: Path) -> None:
    panel = BrightfieldPanel(panel_id="Figure1ScaleBug")
    imported = panel.import_image(_write_png(tmp_path / "scale_bug.png"))
    panel.update_measurement_scale_bar(
        imported["uid"],
        um_per_px=STALE_UM_PER_PX,
        length_um=LENGTH_UM,
        calibration_source="manual",
    )

    resolved = panel.update_measurement_scale_bar(
        imported["uid"],
        length_um=LENGTH_UM,
        line_px=dict(NEW_LINE),
        calibration_source="manual_line",
    )

    assert resolved["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert resolved["calibration_source"] == "manual_line"
    assert panel.measurement_scale_bar(imported["uid"])["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)


def test_fluorescence_update_measurement_scale_bar_redraws_line_over_cached_um_per_px() -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    uid = "fluoro-scale-bug"
    panel.data["fluorescence_images"] = [
        {
            "uid": uid,
            "metadata": {"width_px": 1024, "height_px": 768},
            "measurement_scale_bar": _stale_scale(),
        }
    ]

    resolved = panel.update_measurement_scale_bar(
        uid,
        length_um=LENGTH_UM,
        line_px=dict(NEW_LINE),
        calibration_source="manual_line",
    )

    assert resolved["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert resolved["calibration_source"] == "manual_line"
    assert panel.measurement_scale_bar(uid)["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)


def _close_window(window: MainWindow, app: QApplication) -> None:
    window._capture_clean_project_baseline()
    window.close()
    window.deleteLater()
    app.processEvents()


def _open_brightfield_window(tmp_path: Path, monkeypatch) -> tuple[MainWindow, BrightfieldPanel, str, QApplication]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    window.set_module("brightfield")
    panel = window._active_brightfield_panel()
    imported = panel.import_image(_write_png(tmp_path / "scale_apply.png"))
    return window, panel, str(imported["uid"]), app


def _open_fluorescence_workspace(tmp_path: Path) -> tuple[FluorescenceWorkspace, FluorescencePanel, str, QApplication]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    data = np.zeros((2, 32, 32), dtype=np.uint16)
    data[0, 8:16, 8:16] = 120
    tiff_path = tmp_path / "scale_apply.tif"
    tifffile.imwrite(tiff_path, data, metadata={"axes": "CYX"})
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(tiff_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()
    return workspace, panel, str(image["uid"]), app


def test_brightfield_and_fluorescence_fill_length_then_draw_line(tmp_path: Path, monkeypatch) -> None:
    window, bf_panel, bf_uid, app = _open_brightfield_window(tmp_path, monkeypatch)
    window.data_panel.brightfield_he_fiber_scale_length_spin.setValue(LENGTH_UM)
    window.set_brightfield_measurement_scale_line(dict(NEW_LINE))
    bf_scale = bf_panel.measurement_scale_bar(bf_uid)
    assert bf_scale["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert bf_scale["calibration_source"] == "manual_line"
    assert window.data_panel.brightfield_he_fiber_scale_um_per_px_spin.value() == pytest.approx(EXPECTED_UM_PER_PX, abs=5e-7)
    _close_window(window, app)

    workspace, fl_panel, fl_uid, app = _open_fluorescence_workspace(tmp_path)
    workspace.ai_scale_length_spin.setValue(LENGTH_UM)
    workspace._add_drawn_roi("scale_line", dict(NEW_LINE))
    fl_scale = fl_panel.measurement_scale_bar(fl_uid)
    assert fl_scale["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert fl_scale["calibration_source"] == "manual_line"
    assert workspace.ai_scale_um_per_px_spin.value() == pytest.approx(EXPECTED_UM_PER_PX, abs=5e-7)
    workspace.deleteLater()
    app.processEvents()


def test_brightfield_and_fluorescence_draw_line_then_apply_new_length(tmp_path: Path, monkeypatch) -> None:
    window, bf_panel, bf_uid, app = _open_brightfield_window(tmp_path, monkeypatch)
    window.set_brightfield_measurement_scale_line(dict(NEW_LINE))
    drawn = bf_panel.measurement_scale_bar(bf_uid)
    assert drawn["um_per_px"] == pytest.approx(50.0 / 227.0)
    window.data_panel.brightfield_he_fiber_scale_length_spin.setValue(LENGTH_UM)
    window.apply_brightfield_measurement_scale()
    bf_scale = bf_panel.measurement_scale_bar(bf_uid)
    assert bf_scale["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert bf_scale["calibration_source"] == "manual_line"
    assert window.data_panel.brightfield_he_fiber_scale_um_per_px_spin.value() == pytest.approx(EXPECTED_UM_PER_PX, abs=5e-7)
    _close_window(window, app)

    workspace, fl_panel, fl_uid, app = _open_fluorescence_workspace(tmp_path)
    workspace._add_drawn_roi("scale_line", dict(NEW_LINE))
    drawn = fl_panel.measurement_scale_bar(fl_uid)
    assert drawn["um_per_px"] == pytest.approx(50.0 / 227.0)
    workspace.ai_scale_length_spin.setValue(LENGTH_UM)
    workspace._apply_ai_measurement_scale()
    fl_scale = fl_panel.measurement_scale_bar(fl_uid)
    assert fl_scale["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert fl_scale["calibration_source"] == "manual_line"
    assert workspace.ai_scale_um_per_px_spin.value() == pytest.approx(EXPECTED_UM_PER_PX, abs=5e-7)
    workspace.deleteLater()
    app.processEvents()


def test_brightfield_and_fluorescence_typed_um_per_px_overrides_line(tmp_path: Path, monkeypatch) -> None:
    window, bf_panel, bf_uid, app = _open_brightfield_window(tmp_path, monkeypatch)
    window.set_brightfield_measurement_scale_line(dict(NEW_LINE))
    window.data_panel.brightfield_he_fiber_scale_um_per_px_spin.setValue(0.5)
    window.apply_brightfield_measurement_scale()
    bf_scale = bf_panel.measurement_scale_bar(bf_uid)
    assert bf_scale["um_per_px"] == pytest.approx(0.5)
    assert bf_scale["calibration_source"] == "manual"
    assert bf_scale["line_px"] == NEW_LINE
    _close_window(window, app)

    workspace, fl_panel, fl_uid, app = _open_fluorescence_workspace(tmp_path)
    workspace._add_drawn_roi("scale_line", dict(NEW_LINE))
    workspace.ai_scale_um_per_px_spin.setValue(0.5)
    workspace._apply_ai_measurement_scale()
    fl_scale = fl_panel.measurement_scale_bar(fl_uid)
    assert fl_scale["um_per_px"] == pytest.approx(0.5)
    assert fl_scale["calibration_source"] == "manual"
    assert fl_scale["line_px"] == NEW_LINE
    workspace.deleteLater()
    app.processEvents()


def test_brightfield_and_fluorescence_translate_line_keeps_calibration(tmp_path: Path, monkeypatch) -> None:
    window, bf_panel, bf_uid, app = _open_brightfield_window(tmp_path, monkeypatch)
    window.data_panel.brightfield_he_fiber_scale_length_spin.setValue(LENGTH_UM)
    window.set_brightfield_measurement_scale_line(dict(NEW_LINE))
    window.set_brightfield_measurement_scale_line(dict(TRANSLATED_LINE))
    bf_scale = bf_panel.measurement_scale_bar(bf_uid)
    assert bf_scale["line_px"] == TRANSLATED_LINE
    assert bf_scale["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert bf_scale["calibration_source"] == "manual_line"
    _close_window(window, app)

    workspace, fl_panel, fl_uid, app = _open_fluorescence_workspace(tmp_path)
    workspace.ai_scale_length_spin.setValue(LENGTH_UM)
    workspace._add_drawn_roi("scale_line", dict(NEW_LINE))
    workspace._add_drawn_roi("scale_line", dict(TRANSLATED_LINE))
    fl_scale = fl_panel.measurement_scale_bar(fl_uid)
    assert fl_scale["line_px"] == TRANSLATED_LINE
    assert fl_scale["um_per_px"] == pytest.approx(EXPECTED_UM_PER_PX)
    assert fl_scale["calibration_source"] == "manual_line"
    workspace.deleteLater()
    app.processEvents()
