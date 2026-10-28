"""Shared calibration editor. Display styling and measurement histories stay outside."""
from copy import deepcopy

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                            QGridLayout, QGroupBox, QInputDialog, QLabel,
                            QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget)
from core.layout_scale_bar import normalize_scale_bar_template
from core.measurement_scale import measurement_scale_apply_updates, resolve_calibration
from core.utils.calibration_presets import CalibrationPresets, library_events
from core.utils.i18n import bind_i18n, tr, trf


def action_grid(buttons):
    from ui.widgets.responsive_action_grid import ResponsiveActionGrid
    return ResponsiveActionGrid(buttons)


def retranslate_scale_colors(owner):
    from core.utils.i18n import retranslate_combo
    colors = {'#111111':'black', '#222222':'dark_gray', '#FFFFFF':'white', '#FFD13B':'yellow',
              '#0066FF':'blue', '#22D7E6':'cyan', '#00D26A':'green', '#FF3333':'red',
              '#FF40FF':'magenta', '#FF2AD4':'magenta'}
    for combo in (owner.scale_bar_line_color_combo, owner.scale_bar_text_color_combo):
        retranslate_combo(combo, {value:tr('scale_color_'+name) for value,name in colors.items()})


def create_scale_appearance_group():
    group = bind_i18n(QGroupBox(), 'scale_appearance', 'title')
    form = QFormLayout(group)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return group, form


def add_scale_appearance_rows(owner, parent_form):
    """Arrange the existing display controls without replacing their signal wiring."""
    group, form = create_scale_appearance_group()
    owner.scale_appearance_group = group
    owner.scale_target_combo = QComboBox()
    owner.scale_target_combo.setMinimumContentsLength(12)
    owner.scale_target_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    form.addRow(bind_i18n(QLabel(), 'scale_target'), owner.scale_target_combo)
    owner.scale_target_combo.currentIndexChanged.connect(lambda: owner._scale_target_changed())
    rows = (
        ('scale_bar_um', owner.scale_bar_length_spin),
        ('scale_display_override', owner.scale_bar_manual_um_per_px_spin),
        ('scale_bar_font', owner.scale_bar_font_combo),
        ('scale_bar_font_size', owner.scale_bar_font_size_spin),
        ('scale_bar_line', owner.scale_bar_line_width_spin),
        ('scale_bar_line_color', owner.scale_bar_line_color_combo),
        ('scale_bar_text_color', owner.scale_bar_text_color_combo),
        ('scale_bar_text_position', owner.scale_bar_text_position_combo),
    )
    owner.scale_bar_manual_um_per_px_spin.setSpecialValueText(tr('calibration_from_image'))
    owner.scale_bar_manual_um_per_px_spin.setToolTip(tr('scale_display_override_hint'))
    for key, widget in rows:
        label = bind_i18n(QLabel(), key)
        label.setWordWrap(True)
        widget.setMinimumWidth(0)
        form.addRow(label, widget)
    if hasattr(owner, 'scale_show_text'):
        form.addRow(owner.scale_show_text)
    for button, key in (
        (owner.scale_bar_apply_current_button, 'scale_bar_apply_current'),
        (owner.scale_bar_apply_all_button, 'scale_bar_apply_all_images'),
        (owner.scale_bar_remove_current_button, 'scale_bar_remove_current'),
        (owner.scale_bar_remove_all_button, 'scale_bar_remove_all_images'),
    ):
        bind_i18n(button, key)
    form.addRow(action_grid((owner.scale_bar_apply_current_button, owner.scale_bar_apply_all_button,
                             owner.scale_bar_remove_current_button, owner.scale_bar_remove_all_button)))
    parent_form.addRow(group)


def set_scale_targets(owner, entries, preferred=None):
    combo = owner.scale_target_combo
    selected = preferred if preferred is not None else combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for label, target in entries:
        combo.addItem(label, target)
    index = next((i for i in range(combo.count()) if combo.itemData(i) == selected), -1)
    combo.setCurrentIndex(index if index >= 0 else (0 if entries else -1))
    combo.blockSignals(False)
    combo.setEnabled(bool(entries))


def apply_target_scale_style(container, state_key, targets, template, manual):
    """Change presentation for explicit targets; never copy scientific calibration."""
    states = {str(s.get('target_id')): dict(s) for s in container.get(state_key, []) if isinstance(s, dict)}
    for target in targets:
        state = states.setdefault(str(target), {'target_id': str(target)})
        state.update(enabled=True, template=dict(template))
        state.pop('manual_um_per_px', None)
        if manual > 0:
            state['manual_um_per_px'] = manual
    container[state_key] = list(states.values())


def validate_import_scale(owner, images):
    """Intake never invents a physical scale. Layout retains its px fallback."""
    if getattr(owner, 'current_step', getattr(owner.canvas, 'current_step', -1)) != 0:
        return True
    if not images:
        return False
    for image in images:
        calibration = owner.panel.measurement_scale_bar(str(image['uid']))
        if not calibration.get('um_per_px_x'):
            QMessageBox.information(owner, tr('calibration_shared'), tr('viewer_scale_bar_uncalibrated'))
            return False
    return True


def _combo_set_data(combo, value) -> None:
    if combo is None or value is None:
        return
    target = str(value)
    for index in range(combo.count()):
        data = combo.itemData(index)
        if data is not None and str(data) == target:
            combo.setCurrentIndex(index)
            return
        if combo.itemText(index) == target:
            combo.setCurrentIndex(index)
            return


def sync_scale_appearance_controls(owner, template=None, *, manual_um_per_px=0.0, show_text=None) -> None:
    if owner is None or not template:
        return
    resolved = normalize_scale_bar_template(template)
    widgets = [
        getattr(owner, "scale_bar_length_spin", None),
        getattr(owner, "scale_bar_manual_um_per_px_spin", None),
        getattr(owner, "scale_bar_font_combo", None),
        getattr(owner, "scale_bar_font_size_spin", None),
        getattr(owner, "scale_bar_line_width_spin", None),
        getattr(owner, "scale_bar_line_color_combo", None),
        getattr(owner, "scale_bar_text_color_combo", None),
        getattr(owner, "scale_bar_text_position_combo", None),
        getattr(owner, "scale_show_text", None),
    ]
    blocked = []
    for widget in widgets:
        if widget is None:
            continue
        widget.blockSignals(True)
        blocked.append(widget)
    try:
        length_spin = getattr(owner, "scale_bar_length_spin", None)
        if length_spin is not None:
            length_spin.setValue(float(resolved["label_length_um"]))
        manual_spin = getattr(owner, "scale_bar_manual_um_per_px_spin", None)
        if manual_spin is not None:
            manual_spin.setSpecialValueText(tr('calibration_from_image'))
            try:
                manual_value = float(manual_um_per_px or 0.0)
            except (TypeError, ValueError):
                manual_value = 0.0
            manual_spin.setValue(manual_value if manual_value > 0 else 0.0)
        font_combo = getattr(owner, "scale_bar_font_combo", None)
        if font_combo is not None:
            font_combo.setCurrentFont(QFont(str(resolved["font_family"])))
        font_size_spin = getattr(owner, "scale_bar_font_size_spin", None)
        if font_size_spin is not None:
            font_size_spin.setValue(float(resolved["font_size"]))
        line_width_spin = getattr(owner, "scale_bar_line_width_spin", None)
        if line_width_spin is not None:
            line_width_spin.setValue(float(resolved["line_thickness"]))
        _combo_set_data(getattr(owner, "scale_bar_line_color_combo", None), resolved["line_color"])
        _combo_set_data(getattr(owner, "scale_bar_text_color_combo", None), resolved["text_color"])
        _combo_set_data(getattr(owner, "scale_bar_text_position_combo", None), resolved["text_position"])
        show_box = getattr(owner, "scale_show_text", None)
        if show_box is not None:
            show_box.setChecked(bool(resolved["show_text"] if show_text is None else show_text))
    finally:
        for widget in blocked:
            widget.blockSignals(False)


class CalibrationControls(QGroupBox):
    calibration_applied = pyqtSignal(dict)
    scale_style_applied = pyqtSignal(dict)

    def __init__(self, parent=None, *, length_spin=None, x_spin=None, status=None,
                 actions=(), library=None):
        super().__init__(parent)
        bind_i18n(self, 'calibration_shared', 'title')
        self.library = library if library is not None else CalibrationPresets()
        self._scale = {}
        self.length_spin = length_spin if length_spin is not None else QDoubleSpinBox()
        self.x_spin = x_spin if x_spin is not None else QDoubleSpinBox()
        self.y_spin = QDoubleSpinBox()
        self.length_spin.setRange(.1, 100000)
        if length_spin is None:
            self.length_spin.setValue(50)
        for spin in (self.x_spin, self.y_spin):
            spin.setRange(0, 100000)
            spin.setDecimals(6)
            spin.setSpecialValueText(tr('calibration_from_image'))
            spin.setMinimumWidth(0)
        self.same_axes = bind_i18n(QCheckBox(), 'calibration_square_pixels')
        self.same_axes.setStyleSheet('background: transparent;')
        self.same_axes.setChecked(True)
        self.y_spin.setEnabled(False)
        self.same_axes.toggled.connect(self._link_axes)
        self.x_spin.valueChanged.connect(self._x_changed)
        self.status = status if status is not None else QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName('DimLabel')
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumContentsLength(8)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setToolTip(tr('calibration_preset_hint'))
        self.apply_preset_button = bind_i18n(QPushButton(), 'preset_apply')
        self.save_button = bind_i18n(QPushButton(), 'preset_save')
        self.rename_button = bind_i18n(QPushButton(), 'preset_rename')
        self.delete_button = bind_i18n(QPushButton(), 'preset_delete')
        self.apply_preset_button.clicked.connect(self.apply_preset)
        self.save_button.clicked.connect(self._save)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button.clicked.connect(self._delete)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)
        self.native_status = QLabel()
        self.native_status.setWordWrap(True)
        root.addWidget(self.native_status)
        root.addWidget(self.preset_combo)
        root.addWidget(action_grid((self.apply_preset_button, self.save_button, self.rename_button, self.delete_button)))
        root.addWidget(self.status)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.length_label = bind_i18n(QLabel(), 'scale_reference_length')
        self.length_label.setWordWrap(True)
        form.addRow(self.length_label, self.length_spin)
        form.addRow('X · µm/px', self.x_spin)
        form.addRow('Y · µm/px', self.y_spin)
        form.addRow(self.same_axes)
        root.addLayout(form)
        if actions:
            root.addWidget(action_grid(actions))
        library_events().changed.connect(self.reload_presets)
        self.reload_presets()

    def retranslate(self):
        from core.utils.i18n import retranslate_bound_widgets
        retranslate_bound_widgets(self)
        for spin in (self.x_spin, self.y_spin):
            spin.setSpecialValueText(tr('calibration_from_image'))
        self.preset_combo.setToolTip(tr('calibration_preset_hint'))
        if hasattr(self, '_native_calibration'):
            self._update_native_status()
        self.reload_presets()

    def _update_native_status(self):
        native = self._native_calibration
        effective = resolve_calibration(self._native_metadata, self._scale)
        if effective['calibrated'] and effective['calibration_source'] != 'embedded':
            key = 'scale_preset_active' if self._scale.get('preset_name') else 'scale_manual_active'
            self.native_status.setText(trf(key, name=self._scale.get('preset_name', ''),
                x=effective['um_per_px_x'], y=effective['um_per_px_y']))
            return
        self.native_status.setText(trf('scale_native_present', x=native['um_per_px_x'], y=native['um_per_px_y'])
                                   if native['calibrated'] else tr('scale_native_missing'))

    def _link_axes(self, linked):
        self.y_spin.setEnabled(not linked)
        if linked:
            self.y_spin.setValue(self.x_spin.value())

    def _x_changed(self, value):
        if self.same_axes.isChecked():
            self.y_spin.setValue(value)

    def set_scale(self, scale, *, metadata=None):
        self._scale = deepcopy(scale)
        native_metadata = metadata if metadata is not None else (
            dict(pixel_size_x_um=scale.get('um_per_px_x', scale.get('um_per_px')),
                 pixel_size_y_um=scale.get('um_per_px_y', scale.get('um_per_px')))
            if scale.get('calibration_source') == 'embedded' else {})
        native = resolve_calibration(native_metadata, {'calibration_source':'embedded'})
        self._native_metadata = deepcopy(native_metadata)
        self._native_calibration = native
        self._update_native_status()
        cal = resolve_calibration(dict(pixel_size_x_um=scale.get('um_per_px_x', scale.get('um_per_px', 0)),
                                       pixel_size_y_um=scale.get('um_per_px_y', scale.get('um_per_px', 0))), scale)
        embedded = cal['calibration_source'] == 'embedded'
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.same_axes.blockSignals(True)
        self.length_spin.blockSignals(True)
        self.x_spin.setValue(0 if embedded else cal['um_per_px_x'])
        self.y_spin.setValue(0 if embedded else cal['um_per_px_y'])
        linked = cal['um_per_px_x'] == cal['um_per_px_y']
        self.same_axes.setChecked(linked)
        self.y_spin.setEnabled(not linked)
        self.length_spin.setValue(float(scale.get('length_um', 50)))
        for widget in (self.x_spin, self.y_spin, self.same_axes, self.length_spin):
            widget.blockSignals(False)
        self._shown_axes = (self.x_spin.value(), self.y_spin.value())
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(scale.get('preset_id'))))
        self.status.setText(f"X: {cal['um_per_px_x']:g} · Y: {cal['um_per_px_y']:g} µm/px" if cal['calibrated'] else tr('preset_requires_calibration'))

    def updates_for(self, scale):
        axes = self.x_spin.value(), self.y_spin.value()
        if axes == getattr(self, '_shown_axes', None):
            return dict(length_um=self.length_spin.value(), calibration_source=scale.get('calibration_source', 'assumed'))
        if axes != getattr(self, '_shown_axes', axes) and axes != (0, 0):
            if scale.get('calibration_source') == 'embedded' and not self.same_axes.isChecked():
                axes = (axes[0] or float(scale.get('um_per_px_x', 0)),
                        axes[1] or float(scale.get('um_per_px_y', 0)))
            return dict(um_per_px=axes[0], um_per_px_x=axes[0], um_per_px_y=axes[1],
                        calibration_source='manual', length_um=self.length_spin.value(),
                        preset_id=None, preset_name=None)
        return measurement_scale_apply_updates(scale, length_um=self.length_spin.value(),
                    displayed_um_per_px=self.x_spin.value(), decimals=self.x_spin.decimals())

    def current_calibration(self):
        from core.measurement_scale import merge_measurement_scale_updates
        scale = merge_measurement_scale_updates(self._scale, self.updates_for(self._scale))
        if scale.get('calibration_source') == 'embedded':
            scale['calibration_source'] = 'manual'
        provider = getattr(self, 'scale_style_provider', None)
        if provider:
            scale['scale_bar_template'] = provider()
        return scale

    def reload_presets(self):
        selected = self.preset_combo.currentData()
        self.preset_combo.clear()
        self.preset_combo.addItem(tr('preset_choose'), None)
        for record in self.library.items():
            self.preset_combo.addItem(record['name'], record['id'])
            self.preset_combo.setItemData(self.preset_combo.count()-1,
                f"{record['name']}\nX: {record['um_per_px_x']:g} · Y: {record['um_per_px_y']:g} µm/px", 3)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(selected)))

    def apply_preset(self):
        record = next((r for r in self.library.items() if r['id'] == self.preset_combo.currentData()), None)
        if record is None:
            return
        updates = dict(um_per_px=record['um_per_px_x'], um_per_px_x=record['um_per_px_x'],
                       um_per_px_y=record['um_per_px_y'], calibration_source='manual',
                       preset_id=record['id'], preset_name=record['name'], length_um=self.length_spin.value())
        self.set_scale(updates, metadata=getattr(self, '_native_metadata', None))
        self.calibration_applied.emit(updates)
        if isinstance(record.get('scale_bar_template'), dict):
            self.scale_style_applied.emit(deepcopy(record['scale_bar_template']))

    def _save(self):
        name, ok = QInputDialog.getText(self, tr('preset_save'), tr('preset_name'))
        if ok:
            self._edit(lambda: self.library.save(name, self.current_calibration()))

    def _rename(self):
        preset_id = self.preset_combo.currentData()
        if preset_id is None:
            return
        name, ok = QInputDialog.getText(self, tr('preset_rename'), tr('preset_name'), text=self.preset_combo.currentText())
        if ok:
            self._edit(lambda: self.library.rename(preset_id, name))

    def _delete(self):
        preset_id = self.preset_combo.currentData()
        if preset_id is not None and QMessageBox.question(self, tr('preset_delete'), tr('preset_delete_confirm')) == QMessageBox.StandardButton.Yes:
            self._edit(lambda: self.library.delete(preset_id))

    def _edit(self, operation):
        try:
            record = operation()
            if record:
                self.preset_combo.setCurrentIndex(self.preset_combo.findData(record['id']))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr('calibration_shared'), tr(str(exc)))
