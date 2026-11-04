"""Multi-marker spatial measurement in the existing fluorescence workbench."""
from copy import deepcopy
import csv
import json
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QEvent, Qt, pyqtSignal, QSignalBlocker
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QDoubleSpinBox, QLineEdit, QSpinBox, QFileDialog,
    QSizePolicy, QMenu,
    )

from core.utils.i18n import tr, bind_i18n
from core.measurement_scale import resolve_calibration
from core.measurement_relations import measurement_id


class MultiplexSpatialControls(QWidget):
    relation_kind = 'spatial'

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.entry = bind_i18n(QPushButton(), 'task_spatial'); self.entry.setCheckable(True)
        layout.addWidget(self.entry)
        self.body = QWidget(); body = QVBoxLayout(self.body); body.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.first = QComboBox()
        form.addRow(bind_i18n(QLabel(), 'spatial_nuclei_source'), self.first); body.addLayout(form)
        self.nuclei_button = bind_i18n(QPushButton(), 'spatial_open_nuclei'); body.addWidget(self.nuclei_button)
        menu = QMenu(self.nuclei_button)
        self.nuclei_standard = menu.addAction(tr('spatial_nuclei_standard'))
        self.nuclei_weak = menu.addAction(tr('spatial_nuclei_weak'))
        self.nuclei_button.setMenu(menu)
        self.input_hint = bind_i18n(QLabel(), 'spatial_rgb_hint'); self.input_hint.setWordWrap(True); body.addWidget(self.input_hint)
        self.nuclear_visible = QCheckBox(tr('spatial_show_nuclear')); self.nuclear_visible.setChecked(True); body.addWidget(self.nuclear_visible)
        body.addWidget(bind_i18n(QLabel(), 'spatial_markers'))
        self.marker_widget = QWidget(); self.marker_layout = QVBoxLayout(self.marker_widget)
        self.marker_layout.setContentsMargins(0, 0, 0, 0); self.marker_layout.setSpacing(6)
        self.marker_rows = []; self.aliases = {}; self.visibility = {}; body.addWidget(self.marker_widget)
        self.estimate_button = bind_i18n(QPushButton(), 'spatial_estimate_thresholds'); body.addWidget(self.estimate_button)
        self.estimate_button.setToolTip(tr('spatial_threshold_note'))
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.expansion = self._spin(0, 500, 3)
        self.radius = self._spin(.1, 10000, 20)
        self.roi = QComboBox(); self.roi.addItem(tr('spatial_full_image'), 'full'); self.roi.addItem(tr('spatial_latest_roi'), 'roi')
        for key in ('left', 'right', 'center'): self.roi.addItem(tr('spatial_roi_'+key), key)
        self.roi_type = QLineEdit(); self.experiment_group = QLineEdit()
        self.niche = QCheckBox(tr('spatial_niche_enable')); self.niche_count = QSpinBox(); self.niche_count.setRange(2, 12); self.niche_count.setValue(3)
        self.source = QComboBox(); self.target = QComboBox()
        for key, widget in (('spatial_expansion', self.expansion), ('spatial_scope', self.roi),
                            ('spatial_source', self.source), ('spatial_target', self.target), ('spatial_radius', self.radius),
                            ):
            form.addRow(bind_i18n(QLabel(), key), widget)
        body.addLayout(form)
        self.roi_buttons = []
        from ui.widgets.calibration_controls import action_grid
        for key in ('rectangle', 'freehand'):
            button = QPushButton(tr('spatial_draw_'+key)); self.roi_buttons.append(button)
        body.addWidget(action_grid(self.roi_buttons))
        from ui.widgets.workflow_presentation import WorkflowDisclosure
        advanced = QWidget(); advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0); advanced_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        for key, widget in (('spatial_roi_type', self.roi_type), ('spatial_experiment_group', self.experiment_group),
                            ('spatial_niche_count', self.niche_count)):
            advanced_form.addRow(bind_i18n(QLabel(), key), widget)
        advanced_form.addRow(self.niche)
        body.addWidget(WorkflowDisclosure('spatial_group_options', advanced))
        self.overlay = QComboBox()
        for key in ('phenotype', 'original', 'boundaries', 'niche'): self.overlay.addItem(tr('spatial_overlay_'+key), key)
        body.addWidget(bind_i18n(QLabel(), 'spatial_overlay')); body.addWidget(self.overlay)
        self.hint = bind_i18n(QLabel(), 'spatial_hint'); self.hint.setWordWrap(True); body.addWidget(self.hint)
        self.run_button = bind_i18n(QPushButton(), 'relation_run'); self.run_button.setObjectName('PrimaryButton'); body.addWidget(self.run_button)
        self.summary = QLabel(); self.summary.setWordWrap(True); body.addWidget(self.summary)
        self.inspector = QLabel(tr('spatial_inspect_hint')); self.inspector.setWordWrap(True); body.addWidget(self.inspector)
        self.legend = QLabel(); self.legend.setWordWrap(True); body.addWidget(self.legend)
        self.review_button = bind_i18n(QPushButton(), 'spatial_results'); body.addWidget(self.review_button)
        self.csv_button = bind_i18n(QPushButton(), 'relation_export'); body.addWidget(self.csv_button)
        layout.addWidget(self.body)

    @staticmethod
    def _spin(low, high, value):
        spin = QDoubleSpinBox(); spin.setRange(low, high); spin.setDecimals(3); spin.setValue(value)
        spin.setKeyboardTracking(False); spin.setMinimumHeight(36)
        return spin

    def set_channels(self, channels, nucleus_channel):
        for row in self.marker_rows: row[3].deleteLater()
        self.marker_rows = []; self.aliases = {}; self.visibility = {}
        while self.marker_layout.count(): self.marker_layout.takeAt(0)
        for i, channel in enumerate(channels):
            row = QWidget(); layout = QVBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(3)
            check = QCheckBox(str(channel.get('name') or f'Channel {i+1}'))
            check.setProperty('markerAllowed', i != nucleus_channel)
            check.setChecked(i != nucleus_channel); check.setEnabled(i != nucleus_channel)
            row.setVisible(i != nucleus_channel)
            threshold = self._spin(-1e12, 1e12, -1e12)
            threshold.setDecimals(6)
            threshold.setSpecialValueText(tr('spatial_threshold_missing'))
            threshold.setMinimumWidth(0); threshold.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            top = QHBoxLayout(); top.addWidget(check, 1)
            visible = bind_i18n(QCheckBox(), 'spatial_visible'); visible.setChecked(channel.get('visible', True)); top.addWidget(visible)
            self.visibility[i] = visible
            visible.toggled.connect(lambda checked, index=i: self.on_visibility(index, checked) if hasattr(self, 'on_visibility') else None)
            layout.addLayout(top)
            alias = QLineEdit(str(channel.get('name') or f'Marker {i+1}')); alias.setMinimumWidth(0)
            alias.setProperty('defaultMarkerName', check.text())
            inputs = QHBoxLayout(); inputs.addWidget(alias, 1); inputs.addWidget(threshold, 1)
            height = max(36, alias.sizeHint().height(), threshold.sizeHint().height()); alias.setFixedHeight(height); threshold.setFixedHeight(height)
            layout.addLayout(inputs); self.aliases[i] = alias
            self.marker_layout.addWidget(row); self.marker_rows.append((i, check, threshold, row))
            for signal in (check.toggled, alias.textChanged, threshold.valueChanged):
                signal.connect(lambda *_: self.on_markers() if hasattr(self, 'on_markers') else None)

    def parameters(self):
        return dict(markers=[dict(channel_index=i, name=self.aliases[i].text().strip() or check.text(), threshold=None if spin.value() == spin.minimum() else spin.value())
                             for i, check, spin, _ in self.marker_rows if check.isChecked() and check.property('markerAllowed')],
                    expansion=self.expansion.value(), radius=self.radius.value(),
                    source_phenotype=self.source.currentData(), target_phenotype=self.target.currentData(),
                    source_measurement=self.first.currentData(), scope=self.roi.currentData(),
                    roi_type=self.roi_type.text().strip() or 'All', experiment_group=self.experiment_group.text().strip() or 'Ungrouped',
                    niche_clusters=self.niche_count.value() if self.niche.isChecked() else 0)

    def restore_parameters(self, params):
        blockers = [QSignalBlocker(widget) for widget in self.findChildren(QWidget)]
        for key, widget in (('expansion', self.expansion), ('radius', self.radius)):
            if key in params: widget.setValue(params[key])
        if 'markers' in params:
            values = {m['channel_index']: m for m in params['markers']}
            for i, check, spin, _ in self.marker_rows:
                check.setChecked(i in values and check.property('markerAllowed'))
                if i in values: spin.setValue(values[i]['threshold'] if values[i]['threshold'] is not None else spin.minimum())
                if i in values:
                    name = values[i]['name']
                    if name in ('Combined intensity', '合成强度'):
                        name = self.aliases[i].property('defaultMarkerName')
                    self.aliases[i].setText(name)
        for key, widget in (('roi_type', self.roi_type), ('experiment_group', self.experiment_group)):
            if key in params: widget.setText(params[key])
        if 'niche_clusters' in params:
            self.niche.setChecked(bool(params['niche_clusters']))
            if params['niche_clusters']: self.niche_count.setValue(params['niche_clusters'])
        for key, widget in (('source_measurement', self.first), ('scope', self.roi),
                            ('source_phenotype', self.source), ('target_phenotype', self.target)):
            if key in params:
                index = widget.findData(params[key])
                if index >= 0: widget.setCurrentIndex(index)
                elif key in ('source_phenotype', 'target_phenotype') and params[key] is not None:
                    widget.addItem(str(params[key]), params[key]); widget.setCurrentIndex(widget.count()-1)


class MultiplexSpatialController(QObject):
    preview_changed = pyqtSignal()
    kind = 'spatial'

    def __init__(self, window):
        super().__init__(window)
        self.window = window; self.controls = MultiplexSpatialControls(); self.job_key = 'multiplex_spatial'
        self._identity = None; self._syncing = False; self._drafts = {}; self._channel_key = None; self._message = ''
        self.selected_id = None; self._dialog = None
        self.controls.installEventFilter(self)
        self.controls.run_button.clicked.connect(self.run)
        self.controls.first.currentIndexChanged.connect(self.parameters_changed)
        self.controls.entry.toggled.connect(self.sync)
        self.controls.nuclei_standard.triggered.connect(self.open_nuclei)
        self.controls.nuclei_weak.triggered.connect(lambda: self.open_nuclei(weak=True))
        self.controls.estimate_button.clicked.connect(self.estimate_thresholds)
        self.controls.review_button.clicked.connect(self.open_review)
        self.controls.csv_button.clicked.connect(self.choose_export)
        self.controls.on_visibility = self.set_visibility
        self.controls.on_markers = self.parameters_changed
        for widget in (self.controls.expansion,self.controls.radius,self.controls.niche_count):
            widget.valueChanged.connect(self.parameters_changed)
        for widget in (self.controls.roi_type,self.controls.experiment_group):
            widget.textChanged.connect(self.parameters_changed)
        for widget in (self.controls.roi,self.controls.source,self.controls.target):
            widget.currentTextChanged.connect(self.parameters_changed)
        self.controls.niche.toggled.connect(self.parameters_changed)
        self.controls.nuclear_visible.toggled.connect(lambda visible: self.set_visibility(self._nuclear_channel, visible)
            if getattr(self, '_nuclear_channel', None) is not None else None)
        self.controls.overlay.currentIndexChanged.connect(self.redraw)
        for button, mode in zip(self.controls.roi_buttons, ('rectangle', 'freehand')):
            button.clicked.connect(lambda checked=False, value=mode: window.fluoro_workspace._set_roi_tool(value))
        self._viewport = window.fluoro_workspace.canvas.view.viewport()
        self._viewport.installEventFilter(self)
        window.fluoro_workspace.active_image_combo.currentIndexChanged.connect(self.sync)
        window.fluoro_workspace.analysis_context_changed.connect(self.sync)
        window._analysis_job_service.job_finished.connect(self.sync)
        window._analysis_job_service.busy_changed.connect(self.busy_changed)

    def panel(self): return self.window._active_fluoro_panel()

    def records(self):
        uid = (self.panel().active_image() or {}).get('uid')
        return [r for r in self.panel().analysis.get('nuclei_measurements', [])
                if r.get('image_uid') == uid and r.get('task') == 'dapi' and r.get('object_kind', 'nuclei') == 'nuclei']

    def current_result(self):
        history = getattr(self.window, 'measurement_history', None)
        selected = history.selected('fluoro', 'spatial') if history else None
        if selected is not None: return selected
        uid = (self.panel().active_image() or {}).get('uid')
        return next((r for r in reversed(self.panel().analysis.get('multiplex_spatial_measurements', [])) if r.get('image_uid') == uid), None)

    def is_fresh(self, result):
        if result is None: return False
        record = next((r for r in self.panel().images() if r.get('uid') == result['image_uid']), {})
        if not record or record.get('source_hash') != result.get('source_hash'): return False
        expected = result['parameters'].get('pixel_size_xy')
        if self.calibration(record) != (tuple(expected) if expected is not None else None): return False
        scope = result.get('roi_snapshot')
        if scope is not None and scope.get('roi_id') and not any(r == scope for r in record.get('rois', [])): return False
        return any(measurement_id(r) == result['source_measurements']['nuclei']
                   and r == result['source_snapshot'] for r in self.panel().analysis.get('nuclei_measurements', []))

    def parameters_match(self, result):
        if result is None:return False
        expected=deepcopy(result.get('requested_parameters', {}))
        if not expected:return False
        return self.controls.parameters()==expected

    def parameters_changed(self,*_):
        if self._syncing:return
        self._message=''
        result=self.current_result()
        if result is not None and not self.parameters_match(result):
            self.selected_id=None;self.controls.inspector.setText(tr('spatial_inspect_hint'))
            from PyQt6 import sip
            if self._dialog is not None and not sip.isdeleted(self._dialog):self._dialog.close()
            self._dialog=None
        self.sync()

    def open_nuclei(self, checked=False, *, weak=False):
        self._follow_new_nuclei = ((self.panel().active_image() or {}).get('uid'),
                                   {measurement_id(row) for row in self.records()})
        self.window.measurement_tasks['fluoro'].select('dapi')
        controller = self.window.nuclei_controllers['fluoro']
        recipe = json.loads((self.window.resources_dir/'analysis_presets/dapi_multimarker_reference_v1_20261103.json').read_text(encoding='utf-8'))
        combo = controller.controls.channel
        if combo.currentData() is None:
            index = next((i for i in range(combo.count()) if any(s in combo.itemText(i).lower() for s in ('dapi', 'hoechst', 'dna'))), -1)
            combo.setCurrentIndex(index)
        controller.controls.set_parameters({**recipe['parameters'], **({'weak_recovery': True} if weak else {})})
        controller.sync()

    def eventFilter(self, watched, event):
        if watched is self.controls:
            if event.type() == QEvent.Type.Show: self.sync()
            return False
        canvas = self.window.fluoro_workspace.canvas
        if watched is self._viewport and not canvas.view.roi_mode:
            if event.type() == QEvent.Type.MouseButtonPress: self._press = event.position()
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if hasattr(self, '_press') and (event.position()-self._press).manhattanLength() < 5:
                    if self.window.measurement_tasks['fluoro'].current_task() == 'spatial':
                        for item in canvas.scene.items(canvas.view.mapToScene(event.position().toPoint())):
                            if str(item.data(0) or '').startswith('spatial_') and item.data(2) is not None:
                                self.select_cell(int(item.data(2))); break
        return False

    def set_visibility(self, index, visible):
        record = self.panel().active_image()
        if record:
            self.panel().update_channel(record['uid'], index, visible=visible)
            self.window.fluoro_workspace._refresh_canvas(); self.preview_changed.emit()

    def redraw(self, *_): self.preview_changed.emit()

    def sync_overview(self,visible):
        if not hasattr(self,'overview'):
            if not visible:return
            from ui.widgets.multiplex_spatial_results import SpatialOverview
            self.overview=SpatialOverview(self)
        self.overview.refresh(visible)

    def draw_overlay(self, result):
        from ui.widgets.multiplex_spatial_results import draw_spatial_overlay
        if not self.is_fresh(result) or not self.parameters_match(result): self.window.fluoro_workspace.canvas.clear_measurement_overlay(); return
        draw_spatial_overlay(self.window.fluoro_workspace.canvas, result, self.controls.overlay.currentData(), self.selected_id)

    def select_cell(self, identifier):
        result = self.current_result()
        row = next((r for r in (result or {}).get('object_table', []) if r['object_id'] == identifier), None)
        if row is None: return
        self.selected_id = int(identifier)
        expression = ' · '.join(f"{m['name']}: {v:.5g}" for m, v in zip(result['parameters']['markers'], row['marker_means']))
        text = f"#{identifier} · {row['phenotype']}\n{expression}\n"+tr('spatial_cell_neighbors').format(values=row.get('neighbor_composition', {}))
        self.controls.inspector.setText(text)
        if hasattr(self,'overview'):self.overview.inspector.setText(text)
        from PyQt6 import sip
        if self._dialog is not None and not sip.isdeleted(self._dialog): self._dialog.inspector.setText(text)
        self.preview_changed.emit()

    def group_rows(self):
        from core.image_analysis.multiplex_spatial import grouped_spatial_results
        return grouped_spatial_results([r for r in self.panel().analysis.get('multiplex_spatial_measurements', []) if self.is_fresh(r)])

    @staticmethod
    def calibration(record):
        scale = resolve_calibration(record.get('metadata'), record.get('measurement_scale_bar', record.get('scale_bar')))
        sx, sy = scale['um_per_px_x'], scale['um_per_px_y']
        return (sx, sy) if scale['calibrated'] and sx > 0 and sy > 0 else None

    def sync(self, *_):
        if self._syncing: return
        self._syncing = True
        try:
            panel = self.panel(); record = panel.active_image() or {}; identity = (id(panel), record.get('uid'))
            changed = identity != self._identity
            if changed: self._message = ''; self.selected_id = None; self.controls.inspector.setText(tr('spatial_inspect_hint'))
            if changed and self._identity is not None: self._drafts[self._identity] = self.controls.parameters()
            self._identity = identity
            current = self.current_result(); combo = self.controls.first
            result_identity = (current or {}).get('measurement_uuid')
            if result_identity != getattr(self, '_result_identity', None):
                self._result_identity = result_identity; self.selected_id = None
                self.controls.inspector.setText(tr('spatial_inspect_hint'))
                from PyQt6 import sip
                if self._dialog is not None and not sip.isdeleted(self._dialog): self._dialog.close()
                self._dialog = None
            wanted = (current or {}).get('source_measurements', {}).get('nuclei') if changed else combo.currentData()
            follow = getattr(self, '_follow_new_nuclei', None)
            if follow is not None:
                if follow[0] != record.get('uid'):
                    self._follow_new_nuclei = None
                else:
                    new = [row for row in self.records() if measurement_id(row) not in follow[1]]
                    if new:
                        wanted = measurement_id(new[-1]); self._follow_new_nuclei = None
            combo.blockSignals(True); combo.clear()
            for row in reversed(self.records()):
                channels = record.get('channels', [])
                channel = row.get('channel_index', -1)
                name = channels[channel].get('name', '') if isinstance(channel, int) and 0 <= channel < len(channels) else ''
                combo.addItem(f"{name} · {len(row['nuclei'])} · {row.get('parameters', {}).get('method', '')}", measurement_id(row))
            index = combo.findData(wanted); combo.setCurrentIndex(index if index >= 0 else (0 if combo.count() else -1)); combo.blockSignals(False)
            source = next((r for r in self.records() if measurement_id(r) == combo.currentData()), {})
            self._nuclear_channel = source.get('channel_index')
            self.controls.nuclear_visible.blockSignals(True)
            if self._nuclear_channel is not None:
                self.controls.nuclear_visible.setChecked(record['channels'][self._nuclear_channel].get('visible', True))
            self.controls.nuclear_visible.blockSignals(False)
            roi_combo = self.controls.roi; wanted_scope = roi_combo.currentData(); roi_combo.blockSignals(True)
            while roi_combo.count() > 5: roi_combo.removeItem(5)
            for n, roi in enumerate(record.get('rois', []), 1):
                roi_combo.addItem(f"ROI {n} · {roi.get('name') or roi.get('type', '')}", 'roi:'+roi['roi_id'])
            index = roi_combo.findData(wanted_scope); roi_combo.setCurrentIndex(max(0, index)); roi_combo.blockSignals(False)
            key = (identity, source.get('channel_index'), tuple(c.get('name', '') for c in record.get('channels', [])))
            if key != self._channel_key:
                previous = self.controls.parameters() if not changed else self._drafts.get(identity, (current or {}).get('requested_parameters', {}))
                self.controls.set_channels(record.get('channels', []), source.get('channel_index'))
                self.controls.restore_parameters(previous); self._channel_key = key
                if changed and not previous:
                    self.controls.experiment_group.setText(record.get('group', 'Ungrouped')); self.controls.roi_type.setText('All')
            for i, checkbox in self.controls.visibility.items():
                with QSignalBlocker(checkbox): checkbox.setChecked(record['channels'][i].get('visible', True))
            from itertools import product
            names = [m['name'] for m in self.controls.parameters()['markers']]
            all_titles = [' / '.join(name+('+' if flag else '−') for name, flag in zip(names, flags))
                          for flags in product((False, True), repeat=len(names))] if 0 < len(names) <= 8 else []
            for widget, parameter in ((self.controls.source, 'source_phenotype'), (self.controls.target, 'target_phenotype')):
                wanted = widget.currentData(); widget.blockSignals(True); widget.clear()
                widget.addItem(tr('spatial_auto_phenotype'), None)
                titles = all_titles or [row['phenotype'] for row in (current or {}).get('phenotypes', [])]
                for title in titles: widget.addItem(title, title)
                if changed: wanted = self._drafts.get(identity, (current or {}).get('requested_parameters', {})).get(parameter)
                if wanted is not None and widget.findData(wanted) < 0:
                    # A name edit must not silently select another phenotype.
                    widget.addItem(str(wanted), wanted)
                index = widget.findData(wanted)
                widget.setCurrentIndex(max(0, index))
                widget.blockSignals(False)
            unit = 'µm' if self.calibration(record) else 'px'
            for widget in (self.controls.expansion, self.controls.radius): widget.setSuffix(' '+unit)
            busy = self.window._analysis_job_service.is_busy(self.job_key)
            composite = record.get('channel_interpretation', {}).get('mode') == 'rgb_composite'
            markers = self.controls.parameters()['markers']
            ready = bool(combo.count()) and not busy and not composite and bool(markers)
            self.controls.estimate_button.setEnabled(ready)
            self.controls.run_button.setEnabled(ready and all(m['threshold'] is not None for m in markers))
            self.controls.input_hint.setVisible(record.get('channel_interpretation', {}).get('mode') in ('rgb_composite', 'forced_rgb_split'))
            matches=self.parameters_match(current)
            self.controls.review_button.setEnabled(current is not None and matches)
            self.controls.csv_button.setEnabled(self.is_fresh(current) and matches)
            self.controls.summary.setText((self._message+'\n' if self._message else '')+self.summary_text(current))
            from html import escape
            self.controls.legend.setText(' &nbsp; '.join(f'<span style="color:{r["overlay_color"]}">●</span> {escape(r["phenotype"])}'
                for r in {r['phenotype']: r for r in (current or {}).get('object_table', []) if r['include_in_statistics'] and matches}.values()))
        finally: self._syncing = False
        self.preview_changed.emit()

    def summary_text(self, result):
        if result is None: return tr('spatial_no_result')
        if not self.is_fresh(result): return tr('relation_stale_source')
        if not self.parameters_match(result):return tr('spatial_parameters_changed')
        s = result['summary']; unit = 'µm' if s['length_unit'] == 'um' else 'px'
        mean = '—' if s['nearest_mean'] is None else f"{s['nearest_mean']:.2f} {unit}"
        p = '—' if s['permutation_p'] is None else f"{s['permutation_p']:.3f}"
        area_unit = 'mm²' if s['length_unit'] == 'um' else '10⁶ px²'
        pairs = tr('spatial_pair_labels').format(a=result['parameters'].get('source_phenotype') or '—', b=result['parameters'].get('target_phenotype') or '—')
        warnings = []
        if len(result['phenotypes']) == 1: warnings.append(tr('spatial_single_phenotype'))
        for i, marker in enumerate(result['parameters']['markers']):
            values = [row['marker_means'][i] for row in result['object_table'] if row['include_in_statistics']]
            if values and marker['threshold'] > max(values): warnings.append(tr('spatial_cutoff_above_range').format(name=marker['name'], maximum=max(values)))
        return pairs+'\n'+tr('spatial_summary').format(n=s['cells'], k=len(result['phenotypes']), excluded=s['excluded'], mean=mean, p=p,
                                          area=f"{s['area']/1e6:.4f}", density=f"{s['density']*1e6:,.0f}", area_unit=area_unit)+ '\n'+tr('spatial_metrics_full').format(
                                              a=s['source_cells'], b=s['target_cells'],
                                              median='—' if s['nearest_median'] is None else f"{s['nearest_median']:.2f} {unit}",
                                              fraction='—' if s['fraction_with_neighbors'] is None else f"{s['fraction_with_neighbors']:.1%}",
                                              neighbors='—' if s.get('mean_target_neighbors') is None else f"{s['mean_target_neighbors']:.2f}",
                                              null='—' if s['null_mean'] is None else f"{s['null_mean']:.2f} {unit}")+ ('\n'+'\n'.join(warnings) if warnings else '')

    def snapshot(self):
        record = self.panel().active_image() or {}
        return deepcopy(dict(identity=self._identity, parameters=self.controls.parameters(),
                             rois=record.get('rois', []), calibration=self.calibration(record)))

    def run(self):
        self._run()

    def estimate_thresholds(self):
        self._run(estimate=True)

    def _run(self, *, estimate=False):
        if self.window._analysis_job_service.is_busy(self.job_key): return
        self._message = ''
        self.sync(); snapshot = self.snapshot(); panel = self.panel(); record = deepcopy(panel.active_image()); project = self.window.project
        if not record: return
        params = deepcopy(snapshot['parameters'])
        source = next((deepcopy(r) for r in self.records() if measurement_id(r) == params['source_measurement']), None)
        if source is None: self.controls.summary.setText(tr('spatial_no_result')); return
        if not params['markers']: self.controls.summary.setText(tr('spatial_choose_markers')); return
        if not estimate and any(m['threshold'] is None for m in params['markers']):
            self.set_message(tr('spatial_threshold_required')); return
        if not estimate and len(params['markers']) <= 8:
            from itertools import product
            titles = {' / '.join(m['name']+('+' if on else '−') for m, on in zip(params['markers'], flags))
                      for flags in product((False, True), repeat=len(params['markers']))}
            if any(params[key] is not None and params[key] not in titles for key in ('source_phenotype', 'target_phenotype')):
                self.set_message(tr('spatial_pair_required')); return
        image = panel._analysis_stack(record, panel.image_data(record['uid'])); roi = None
        if params['scope'] == 'roi' or str(params['scope']).startswith('roi:'):
            if not snapshot['rois']: self.controls.summary.setText(tr('nuclei_choose_roi')); return
            saved = next((r for r in snapshot['rois'] if 'roi:'+r['roi_id'] == params['scope']), snapshot['rois'][-1])
            roi = panel._roi_mask(image.shape[1:], saved['type'], saved['geometry'])
        elif params['scope'] in ('left', 'right', 'center'):
            h, w = image.shape[1:]
            bounds = dict(left=(0, 0, max(1,w//2), h), right=(w//2, 0, w, h),
                          center=(w//4, h//4, max(w//4+1,3*w//4), max(h//4+1,3*h//4)))[params['scope']]
            x0,y0,x1,y1 = bounds
            saved = dict(type='rectangle', geometry=dict(x=x0,y=y0,w=x1-x0,h=y1-y0))
            roi = panel._roi_mask(image.shape[1:], saved['type'], saved['geometry'])
        def work():
            if estimate:
                from core.image_analysis.multiplex_spatial import estimate_marker_thresholds
                return estimate_marker_thresholds(image, source, markers=params['markers'], expansion=params['expansion'],
                    pixel_size_xy=snapshot['calibration'], roi_mask=roi)
            from core.image_analysis.multiplex_spatial import measure_multiplex
            result = measure_multiplex(image, source, markers=params['markers'], expansion=params['expansion'], radius=params['radius'],
                source_phenotype=params['source_phenotype'], target_phenotype=params['target_phenotype'],
                pixel_size_xy=snapshot['calibration'], roi_mask=roi, niche_clusters=params.get('niche_clusters', 0))
            result.update(measurement_uuid=str(uuid4()), requested_parameters=params, source_snapshot=source,
                          source_hash=record.get('source_hash'), roi_snapshot=deepcopy(saved) if roi is not None else None)
            return result
        def accept(result):
            if estimate:
                values = dict(zip((m['channel_index'] for m in params['markers']), result))
                for i, _, spin, _ in self.controls.marker_rows:
                    if i in values:
                        with QSignalBlocker(spin): spin.setValue(values[i] if values[i] is not None else spin.minimum())
                self.parameters_changed(); self.set_message(tr('spatial_threshold_note')); return
            panel.analysis.setdefault('multiplex_spatial_measurements', []).append(result)
            history = getattr(self.window, 'measurement_history', None)
            if history: history.forget('fluoro', 'spatial')
            panel.append_audit('multiplex_spatial', result['measurement_uuid']); self.sync()
            selected = next((r['object_id'] for r in result['object_table'] if r['include_in_statistics']
                             and r['phenotype'] == result['parameters']['source_phenotype']), None)
            if selected is None:
                selected = next((r['object_id'] for r in result['object_table'] if r['include_in_statistics']), None)
            if selected is not None: self.select_cell(selected)
        self.controls.summary.setText(tr('nuclei_running'))
        self.window._analysis_job_service.start(key=self.job_key, work=work, on_success=accept,
            on_error=lambda error: self.set_message(str(error)),
            is_current=lambda: self.window.project is project and self.panel() is panel and self.snapshot() == snapshot
                and any(r == source for r in self.records()),
            on_stale=lambda: self.set_message(tr('nuclei_stale')))

    def set_message(self, message):
        self._message = message
        self.controls.summary.setText(message)

    def busy_changed(self, key, busy):
        if key == self.job_key:
            self.controls.body.setEnabled(not busy)
            if not busy: self.sync()

    def open_review(self):
        result = self.current_result()
        if result is None or not self.parameters_match(result): return
        from ui.widgets.multiplex_spatial_results import SpatialResultsDialog
        dialog = SpatialResultsDialog(self); dialog.show(); self._dialog = dialog
        return dialog

    def choose_export(self):
        path, _ = QFileDialog.getSaveFileName(self.window, tr('relation_export'), 'multiplex_spatial.csv', 'CSV (*.csv)')
        if path: self.export_csv(Path(path).with_suffix('.csv'))

    @staticmethod
    def cell_rows(result):
        unit = result['summary']['length_unit']
        rows = []
        for row in result['object_table']:
            values = {k: v for k, v in row.items() if k not in ('contour_points_px', 'cell_contour_points_px', 'overlay_color', 'marker_means', 'x', 'y', 'nearest_target')}
            values.update({'x_'+unit: row['x'], 'y_'+unit: row['y'], 'nearest_target_'+unit: row['nearest_target']})
            if not row.get('include_in_statistics') or row['phenotype'] != result['parameters'].get('source_phenotype'):
                values['nearest_target_id'] = None
            for marker, mean in zip(result['parameters']['markers'], row['marker_means']):
                values[f"ch{marker['channel_index']+1}_{marker['name']}_mean_raw"] = mean
            rows.append(values)
        return rows

    def export_csv(self, path):
        result = self.current_result()
        if not self.is_fresh(result): raise ValueError(tr('relation_stale_source'))
        if not self.parameters_match(result):raise ValueError(tr('spatial_parameters_changed'))
        def write(target, rows):
            fields = list(dict.fromkeys(k for row in rows for k in row))
            with Path(target).open('w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
                for row in rows: writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
        path = Path(path)
        write(path, [dict(image_name=result['image_name'], measurement_uuid=result['measurement_uuid'],
                          image_uid=result['image_uid'], source_measurement=result['source_measurements']['nuclei'],
                          source_parameters=result['source_snapshot'].get('parameters', {}),
                          roi=result.get('roi_snapshot'), requested_parameters=result.get('requested_parameters'),
                          parameters=result['parameters'], **result['summary'])])
        write(path.with_name(path.stem+'_phenotypes.csv'), result['phenotypes'])
        write(path.with_name(path.stem+'_cells.csv'), self.cell_rows(result))
        write(path.with_name(path.stem+'_neighbors.csv'), [r for r in self.cell_rows(result) if r.get('target_count_in_radius') is not None])
        write(path.with_name(path.stem+'_niches.csv'), result.get('niches', []))
        write(path.with_name(path.stem+'_groups.csv'), self.group_rows())

    def retranslate(self):
        self.controls.nuclei_standard.setText(tr('spatial_nuclei_standard')); self.controls.nuclei_weak.setText(tr('spatial_nuclei_weak'))
        self.controls.estimate_button.setToolTip(tr('spatial_threshold_note'))
        for _, _, spin, _ in self.controls.marker_rows: spin.setSpecialValueText(tr('spatial_threshold_missing'))
        for i, key in enumerate(('full_image', 'latest_roi', 'roi_left', 'roi_right', 'roi_center')):
            self.controls.roi.setItemText(i, tr('spatial_'+key))
        for i, key in enumerate(('phenotype', 'original', 'boundaries', 'niche')):
            self.controls.overlay.setItemText(i, tr('spatial_overlay_'+key))
        for button, key in zip(self.controls.roi_buttons, ('rectangle', 'freehand')): button.setText(tr('spatial_draw_'+key))
        self.controls.niche.setText(tr('spatial_niche_enable')); self.controls.nuclear_visible.setText(tr('spatial_show_nuclear'))
        self.sync()
