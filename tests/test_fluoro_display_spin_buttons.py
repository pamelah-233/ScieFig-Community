"""Exercise the visible arrow buttons with the shipped Qt stylesheet."""
import os
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QFormLayout, QStyle, QStyleFactory, QStyleOptionSpinBox
from ui.widgets.fluoro_display_controls import FluoroDisplayControls

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_channel_processing_reflows_after_narrow_sidebar_switch(tmp_path, language, desktop_screen, request):
    import numpy as np
    import tifffile
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QWidget,QLabel,QAbstractButton,QAbstractSpinBox
    from ui.main_window import MainWindow
    old_font, old_sheet = APP.font(), APP.styleSheet()
    request.addfinalizer(lambda: APP.setFont(old_font))
    request.addfinalizer(lambda: APP.setStyleSheet(old_sheet))
    APP.setFont(QFont('Microsoft YaHei',10))
    APP.setStyleSheet((ROOT/'resources/sciefig.qss').read_text(encoding='utf-8'))
    w=MainWindow(ROOT/'resources');w.project_recovery.timer.stop()
    path=tmp_path/'channels.tif'
    tifffile.imwrite(path,np.arange(2*64*80,dtype=np.uint16).reshape(2,64,80),metadata={'axes':'CYX'},photometric='minisblack')
    panel=w._active_fluoro_panel();panel.import_image(path)
    try:
        w.resize(1600,1000);w.set_module('fluoro');w.fluoro_workspace.set_step(1);w.show()
        for _ in range(12):APP.processEvents()
        w.set_module('brightfield');w.resize(1280,800);w.switch_language(language);w.v9_shell.set_right_panel_width(280)
        w.set_module('fluoro');w.fluoro_workspace.set_step(1)
        for _ in range(20):APP.processEvents()
        for group in w.fluoro_workspace._display_processing_controls:
            controls=[x for x in group.findChildren(QWidget) if isinstance(x,(QLabel,QAbstractButton,QAbstractSpinBox)) and x.parentWidget() is group]
            for index,first in enumerate(controls):
                for second in controls[index+1:]:
                    assert not first.geometry().intersects(second.geometry()),(group.size(),group.heightForWidth(group.width()),getattr(first,'text',lambda:'')(),first.geometry(),getattr(second,'text',lambda:'')(),second.geometry())
    finally:
        w._capture_clean_project_baseline();w.close();APP.processEvents()


@pytest.mark.parametrize('field, initial, step', [('strength', 0, 5), ('denoise', 40, 10), ('radius', 48, 1)])
@pytest.mark.parametrize('style', ['Fusion', 'windows11'])
def test_visible_display_arrows_change_the_value(field, initial, step, style, request):
    previous = APP.styleSheet()
    previous_style = APP.style().objectName()
    request.addfinalizer(lambda: APP.setStyleSheet(previous))
    request.addfinalizer(lambda: APP.setStyle(QStyleFactory.create(previous_style)))
    APP.setStyle(QStyleFactory.create(style))
    APP.setStyleSheet((ROOT / 'resources/sciefig.qss').read_text(encoding='utf-8'))
    controls = FluoroDisplayControls(dict(background_radius=48, denoise=40))
    controls.resize(360, 360)
    controls.show()
    APP.processEvents()
    spin = getattr(controls, field)
    spin.setValue(initial)
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    up = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxUp, spin)
    down = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxDown, spin)
    assert up.width() >= 24 and down.width() >= 24
    assert up.center().x() == down.center().x() and up.bottom() < down.top()
    events = QSignalSpy(controls.settings_changed)
    # Click where the active Qt style actually paints each visible arrow.
    upper = QPoint(spin.width() - 12, spin.height() // 4)
    lower = QPoint(spin.width() - 12, spin.height() * 3 // 4)
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=upper)
    assert spin.value() == initial + step, (up, down, upper)
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=lower)
    assert spin.value() == initial, (up, down, lower)
    assert len(events) == 2
    controls.close()


@pytest.mark.parametrize('language', ['zh', 'en'])
@pytest.mark.parametrize('style', ['Fusion', 'windows11'])
def test_channel_labels_are_centered_on_their_fields(tmp_path, language, style, request):
    import numpy as np
    import tifffile
    from core.models import FluorescencePanel
    from core.utils.i18n import get_language, set_language
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace

    previous_sheet, previous_style = APP.styleSheet(), APP.style().objectName()
    previous_language = get_language()
    request.addfinalizer(lambda: APP.setStyleSheet(previous_sheet))
    request.addfinalizer(lambda: APP.setStyle(QStyleFactory.create(previous_style)))
    request.addfinalizer(lambda: set_language(previous_language))
    APP.setStyle(QStyleFactory.create(style))
    APP.setStyleSheet((ROOT / 'resources/sciefig.qss').read_text(encoding='utf-8'))
    set_language(language)
    path = tmp_path / 'dapi.tif'
    tifffile.imwrite(path, np.arange(32 * 48, dtype=np.uint16).reshape(32, 48))
    panel = FluorescencePanel(panel_id='alignment')
    panel.import_image(path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(1)
    workspace.workflow_panel.setFixedWidth(460)
    workspace.workflow_panel.show()
    APP.processEvents()
    controls = workspace._display_processing_controls[0]
    outer_form = controls.parentWidget().layout()
    try:
        for form, rows in ((outer_form, range(1, 4)), (controls.layout(), range(3))):
            for row in rows:
                label = form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget()
                field = form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget()
                if label.geometry().bottom() < field.geometry().top():
                    # Existing narrow-sidebar wrapping is intentional, not
                    # a misaligned pair on the same row.
                    assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapLongRows
                    continue
                assert abs(label.geometry().center().y() - field.geometry().center().y()) <= 1, (label.text(), label.geometry(), field.geometry())
    finally:
        workspace.workflow_panel.close()
        workspace.close()
        panel.dispose()
