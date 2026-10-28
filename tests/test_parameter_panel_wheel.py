"""Wheel over sidebar settings scrolls the panel, never edits a parameter."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QLabel, QLineEdit,
    QScrollArea, QSlider, QSpinBox, QTableWidget, QVBoxLayout, QWidget,
)

from ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


def wheel(widget, delta=-120, *, modifiers=Qt.KeyboardModifier.NoModifier):
    pos = widget.rect().center()
    event = QWheelEvent(
        QPointF(pos), QPointF(widget.mapToGlobal(pos)), QPoint(), QPoint(0, delta),
        Qt.MouseButton.NoButton, modifiers,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(widget, event)
    APP.processEvents()


@pytest.fixture
def window():
    widget = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    widget.project_recovery.timer.stop()
    widget.project_recovery._alive = False
    widget.show()
    APP.processEvents()
    yield widget
    widget._capture_clean_project_baseline()
    widget.close()
    APP.processEvents()


def add_settings(window, kind):
    """Use the real shell and app event filter, with a deterministic long form."""
    page = QWidget()
    layout = QVBoxLayout(page)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    layout.addWidget(scroll)
    content = QWidget()
    form = QVBoxLayout(content)
    control = kind()
    if isinstance(control, QComboBox):
        control.addItems([f"Option {i}" for i in range(60)])
        control.setCurrentIndex(5)
        control.setMaxVisibleItems(8)
    elif isinstance(control, (QSpinBox, QDoubleSpinBox, QSlider)):
        control.setRange(0, 100)
        control.setValue(50)
    form.addWidget(control)
    for i in range(30):
        label = QLabel(f"Setting {i}")
        label.setMinimumHeight(40)
        form.addWidget(label)
    scroll.setWidget(content)
    window.v9_shell.set_right_widget(page)
    APP.processEvents()
    assert scroll.verticalScrollBar().maximum() > 200
    return scroll, control


@pytest.mark.parametrize("kind", [QSpinBox, QDoubleSpinBox, QComboBox, QSlider, QLineEdit])
@pytest.mark.parametrize("focused", [False, True])
def test_wheel_over_parameter_scrolls_panel_without_changing_value(window, kind, focused):
    scroll, control = add_settings(window, kind)
    if focused:
        control.setFocus()
        APP.processEvents()
    before = control.currentIndex() if isinstance(control, QComboBox) else (
        control.value() if hasattr(control, "value") else control.text())
    wheel(control)
    assert scroll.verticalScrollBar().value() > 0, "Parameter swallowed panel scrolling"
    after = control.currentIndex() if isinstance(control, QComboBox) else (
        control.value() if hasattr(control, "value") else control.text())
    assert after == before, "Wheel edited a parameter"


def test_wheel_over_spin_editor_scrolls_panel(window):
    scroll, spin = add_settings(window, QDoubleSpinBox)
    spin.lineEdit().setFocus()
    wheel(spin.lineEdit())
    assert scroll.verticalScrollBar().value() > 0
    assert spin.value() == 50


def test_open_dropdown_scrolls_its_options_not_the_panel(window):
    scroll, combo = add_settings(window, QComboBox)
    combo.showPopup()
    APP.processEvents()
    assert combo.view().isVisible()
    bar = combo.view().verticalScrollBar()
    before = bar.value()
    wheel(combo.view().viewport())
    assert bar.value() > before
    assert scroll.verticalScrollBar().value() == 0
    combo.hidePopup()
    wheel(combo)
    assert combo.currentIndex() == 5
    assert scroll.verticalScrollBar().value() > 0


def test_spin_click_and_keyboard_remain_available(window):
    scroll, spin = add_settings(window, QSpinBox)
    spin.setFocus()
    QTest.keyClick(spin, Qt.Key.Key_Up)
    assert spin.value() == 51
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton,
                     pos=QPoint(spin.width() - 8, spin.height() // 4))
    assert spin.value() == 52


def test_table_in_settings_does_not_trap_panel_scrolling(window):
    scroll, table = add_settings(window, QTableWidget)
    table.setColumnCount(1)
    table.setRowCount(100)
    APP.processEvents()
    assert table.verticalScrollBar().maximum() > 0
    wheel(table.viewport())
    assert scroll.verticalScrollBar().value() > 0
    assert table.verticalScrollBar().value() == 0


def test_ctrl_wheel_in_settings_only_scrolls_panel(window):
    scroll, spin = add_settings(window, QSpinBox)
    wheel(spin, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert scroll.verticalScrollBar().value() > 0
    assert spin.value() == 50


def test_panel_at_scroll_limit_still_does_not_edit_values(window):
    scroll, spin = add_settings(window, QSpinBox)
    wheel(spin, delta=120)
    assert scroll.verticalScrollBar().value() == 0
    assert spin.value() == 50
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    wheel(spin)
    assert spin.value() == 50


def test_real_fluorescence_controls_scroll_without_changing_image(window, tmp_path):
    import numpy as np
    import tifffile
    path = tmp_path / "channels.tif"
    tifffile.imwrite(path, np.arange(2 * 64 * 64, dtype=np.uint16).reshape(2, 64, 64),
                     photometric="minisblack", metadata={"axes": "CYX"})
    panel = window._active_fluoro_panel()
    panel.import_image(path)
    window.set_module("fluoro")
    workspace = window.fluoro_workspace
    workspace.set_panel(panel)
    workspace.set_step(1)
    APP.processEvents()
    scroll = workspace.workflow_panel.findChild(QScrollArea)
    controls = workspace._display_processing_controls[0]
    scroll.ensureWidgetVisible(controls.strength)
    APP.processEvents()
    assert controls.strength.isVisible()
    assert scroll.verticalScrollBar().maximum() > 0
    before = scroll.verticalScrollBar().value()
    transform = workspace.canvas.view.transform()
    changes = QSignalSpy(controls.settings_changed)
    wheel(controls.strength, delta=-120 if before < scroll.verticalScrollBar().maximum() else 120)
    assert scroll.verticalScrollBar().value() != before
    assert len(changes) == 0
    assert workspace.canvas.view.transform() == transform
    wheel(workspace.canvas.view.viewport(), delta=120,
          modifiers=Qt.KeyboardModifier.ControlModifier)
    assert workspace.canvas.view.transform() != transform


def test_browser_settings_scroll_without_editing_calibration(window):
    window.set_module("viewer")
    browser = window.image_browser_workspace
    APP.processEvents()
    browser.settings_scroll.ensureWidgetVisible(browser.length_spin)
    APP.processEvents()
    assert browser.length_spin.isVisible()
    bar = browser.settings_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    before, value = bar.value(), browser.length_spin.value()
    wheel(browser.length_spin, delta=-120 if before < bar.maximum() else 120)
    assert bar.value() != before
    assert browser.length_spin.value() == value
