"""Multi-marker spatial measurement in the existing fluorescence workbench."""
from copy import deepcopy
import csv
import json
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QDoubleSpinBox, QFileDialog,
    QDialog, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView)

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
        body.addWidget(bind_i18n(QLabel(), 'spatial_markers'))
        self.marker_widget = QWidget(); self.marker_layout = QVBoxLayout(self.marker_widget)
        self.marker_layout.setContentsMargins(0, 0, 0, 0); self.marker_layout.setSpacing(6)
        self.marker_rows = []; body.addWidget(self.marker_widget)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.expansion = self._spin(0, 500, 3)
        self.radius = self._spin(.1, 10000, 20)
        self.roi = QComboBox(); self.roi.addItem(tr('spatial_full_image'), 'full'); self.roi.addItem(tr('spatial_latest_roi'), 'roi')
        self.source = QComboBox(); self.target = QComboBox()
        for key, widget in (('spatial_expansion', self.expansion), ('spatial_scope', self.roi),
                            ('spatial_source', self.source), ('spatial_target', self.target), ('spatial_radius', self.radius)):
            form.addRow(bind_i18n(QLabel(), key), widget)
        body.addLayout(form)
        self.hint = bind_i18n(QLabel(), 'spatial_hint'); self.hint.setWordWrap(True); body.addWidget(self.hint)
        self.run_button = bind_i18n(QPushButton(), 'relation_run'); self.run_button.setObjectName('PrimaryButton'); body.addWidget(self.run_button)
        self.summary = QLabel(); self.summary.setWordWrap(True); body.addWidget(self.summary)
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
        self.marker_rows = []
        while self.marker_layout.count(): self.marker_layout.takeAt(0)
        for i, channel in enumerate(channels):
            row = QWidget(); layout = QVBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(3)
            check = QCheckBox(str(channel.get('name') or f'Channel {i+1}'))
            check.setProperty('markerAllowed', i != nucleus_channel)
            check.setChecked(i != nucleus_channel); check.setEnabled(i != nucleus_channel)
            row.setVisible(i != nucleus_channel)
            threshold = self._spin(-1e12, 1e12, 100)
            layout.addWidget(check); layout.addWidget(threshold)
            self.marker_layout.addWidget(row); self.marker_rows.append((i, check, threshold, row))

    def parameters(self):
        return dict(markers=[dict(channel_index=i, name=check.text(), threshold=spin.value())
                             for i, check, spin, _ in self.marker_rows if check.isChecked() and check.property('markerAllowed')],
                    expansion=self.expansion.value(), radius=self.radius.value(),
                    source_phenotype=self.source.currentData(), target_phenotype=self.target.currentData(),
                    source_measurement=self.first.currentData(), scope=self.roi.currentData())

    def restore_parameters(self, params):
        for key, widget in (('expansion', self.expansion), ('radius', self.radius)):
            if key in params: widget.setValue(params[key])
        if 'markers' in params:
            values = {m['channel_index']: m for m in params['markers']}
            for i, check, spin, _ in self.marker_rows:
                check.setChecked(i in values and check.property('markerAllowed'))
                if i in values: spin.setValue(values[i]['threshold'])
        for key, widget in (('source_measurement', self.first), ('scope', self.roi),
                            ('source_phenotype', self.source), ('target_phenotype', self.target)):
            if key in params:
                index = widget.findData(params[key])
                if index >= 0: widget.setCurrentIndex(index)


class MultiplexSpatialController(QObject):
    preview_changed = pyqtSignal()
    kind = 'spatial'

    def __init__(self, window):
        super().__init__(window)
        self.window = window; self.controls = MultiplexSpatialControls(); self.job_key = 'multiplex_spatial'
        self._identity = None; self._syncing = False; self._drafts = {}; self._channel_key = None; self._message = ''
        self.controls.installEventFilter(self)
        self.controls.run_button.clicked.connect(self.run)
        self.controls.first.currentIndexChanged.connect(self.sync)
        self.controls.entry.toggled.connect(self.sync)
        self.controls.nuclei_button.clicked.connect(self.open_nuclei)
        self.controls.review_button.clicked.connect(self.open_review)
        self.controls.csv_button.clicked.connect(self.choose_export)
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
        record = self.panel().active_image() or {}
        expected = result['parameters'].get('pixel_size_xy')
        if self.calibration(record) != (tuple(expected) if expected is not None else None): return False
        if result.get('roi_snapshot') is not None and (not record.get('rois') or record['rois'][-1] != result['roi_snapshot']): return False
        return any(measurement_id(r) == result['source_measurements']['nuclei']
                   and r == result['source_snapshot'] for r in self.records())

    def open_nuclei(self):
        self.window.measurement_tasks['fluoro'].select('dapi')
        controller = self.window.nuclei_controllers['fluoro']
        recipe = json.loads((self.window.resources_dir/'analysis_presets/dapi_multimarker_reference_v1_20261103.json').read_text(encoding='utf-8'))
        combo = controller.controls.channel
        if combo.currentData() is None:
            index = next((i for i in range(combo.count()) if any(s in combo.itemText(i).lower() for s in ('dapi', 'hoechst', 'dna'))), -1)
            combo.setCurrentIndex(index)
        controller.controls.set_parameters(recipe['parameters'])
        controller.sync()

    def eventFilter(self, watched, event):
        if watched is self.controls and event.type() == QEvent.Type.Show: self.sync()
        return False

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
            if changed: self._message = ''
            if changed and self._identity is not None: self._drafts[self._identity] = self.controls.parameters()
            self._identity = identity
            current = self.current_result(); combo = self.controls.first
            wanted = (current or {}).get('source_measurements', {}).get('nuclei') if changed else combo.currentData()
            combo.blockSignals(True); combo.clear()
            for row in reversed(self.records()):
                channels = record.get('channels', [])
                channel = row.get('channel_index', -1)
                name = channels[channel].get('name', '') if isinstance(channel, int) and 0 <= channel < len(channels) else ''
                combo.addItem(f"{name} · {len(row['nuclei'])} · {row.get('parameters', {}).get('method', '')}", measurement_id(row))
            index = combo.findData(wanted); combo.setCurrentIndex(index if index >= 0 else (0 if combo.count() else -1)); combo.blockSignals(False)
            source = next((r for r in self.records() if measurement_id(r) == combo.currentData()), {})
            key = (identity, source.get('channel_index'), tuple(c.get('name', '') for c in record.get('channels', [])))
            if key != self._channel_key:
                previous = self.controls.parameters() if not changed else self._drafts.get(identity, (current or {}).get('requested_parameters', {}))
                self.controls.set_channels(record.get('channels', []), source.get('channel_index'))
                self.controls.restore_parameters(previous); self._channel_key = key
            for widget, parameter, default in ((self.controls.source, 'source_phenotype', 0), (self.controls.target, 'target_phenotype', -1)):
                wanted = widget.currentData(); widget.blockSignals(True); widget.clear()
                for row in (current or {}).get('phenotypes', []): widget.addItem(row['phenotype'], row['phenotype'])
                requested = (current or {}).get('parameters', {}).get(parameter)
                if requested is not None and widget.findData(requested) < 0:
                    widget.addItem(requested+' (n=0)', requested)
                if wanted is None: wanted = requested
                index = widget.findData(wanted); widget.setCurrentIndex(index if index >= 0 else (default if default >= 0 else widget.count()-1)); widget.blockSignals(False)
            unit = 'µm' if self.calibration(record) else 'px'
            for widget in (self.controls.expansion, self.controls.radius): widget.setSuffix(' '+unit)
            busy = self.window._analysis_job_service.is_busy(self.job_key)
            composite = record.get('channel_interpretation', {}).get('mode') == 'rgb_composite'
            self.controls.run_button.setEnabled(bool(combo.count()) and not busy and not composite)
            self.controls.review_button.setEnabled(current is not None)
            self.controls.csv_button.setEnabled(self.is_fresh(current))
            self.controls.summary.setText((self._message+'\n' if self._message else '')+self.summary_text(current))
        finally: self._syncing = False
        self.preview_changed.emit()

    def summary_text(self, result):
        if result is None: return tr('spatial_no_result')
        if not self.is_fresh(result): return tr('relation_stale_source')
        s = result['summary']; unit = 'µm' if s['length_unit'] == 'um' else 'px'
        mean = '—' if s['nearest_mean'] is None else f"{s['nearest_mean']:.2f} {unit}"
        p = '—' if s['permutation_p'] is None else f"{s['permutation_p']:.3f}"
        area_unit = 'mm²' if s['length_unit'] == 'um' else '10⁶ px²'
        return tr('spatial_summary').format(n=s['cells'], k=len(result['phenotypes']), excluded=s['excluded'], mean=mean, p=p,
                                          area=f"{s['area']/1e6:.4f}", density=f"{s['density']*1e6:,.0f}", area_unit=area_unit)

    def snapshot(self):
        record = self.panel().active_image() or {}
        return deepcopy(dict(identity=self._identity, parameters=self.controls.parameters(),
                             rois=record.get('rois', []), calibration=self.calibration(record)))

    def run(self):
        if self.window._analysis_job_service.is_busy(self.job_key): return
        self._message = ''
        self.sync(); snapshot = self.snapshot(); panel = self.panel(); record = deepcopy(panel.active_image()); project = self.window.project
        if not record: return
        params = deepcopy(snapshot['parameters'])
        source = next((deepcopy(r) for r in self.records() if measurement_id(r) == params['source_measurement']), None)
        if source is None: self.controls.summary.setText(tr('spatial_no_result')); return
        if not params['markers']: self.controls.summary.setText(tr('spatial_choose_markers')); return
        image = panel.image_data(record['uid']); roi = None
        if params['scope'] == 'roi':
            if not snapshot['rois']: self.controls.summary.setText(tr('nuclei_choose_roi')); return
            saved = snapshot['rois'][-1]; roi = panel._roi_mask(image.shape[1:], saved['type'], saved['geometry'])
        def work():
            from core.image_analysis.multiplex_spatial import measure_multiplex
            result = measure_multiplex(image, source, markers=params['markers'], expansion=params['expansion'], radius=params['radius'],
                source_phenotype=params['source_phenotype'], target_phenotype=params['target_phenotype'],
                pixel_size_xy=snapshot['calibration'], roi_mask=roi)
            result.update(measurement_uuid=str(uuid4()), requested_parameters=params, source_snapshot=source,
                          source_hash=record.get('source_hash'), roi_snapshot=deepcopy(snapshot['rois'][-1]) if roi is not None else None)
            return result
        def accept(result):
            panel.analysis.setdefault('multiplex_spatial_measurements', []).append(result)
            history = getattr(self.window, 'measurement_history', None)
            if history: history.forget('fluoro', 'spatial')
            panel.append_audit('multiplex_spatial', result['measurement_uuid']); self.sync()
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
        if result is None: return
        dialog = QDialog(self.window); dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(tr('task_spatial')); dialog.resize(1050, 680); layout = QVBoxLayout(dialog)
        caption = QLabel(self.summary_text(result)); caption.setWordWrap(True); layout.addWidget(caption)
        tabs = QTabWidget(); layout.addWidget(tabs)
        for key, title in (('phenotypes', 'spatial_phenotypes'), ('object_table', 'spatial_cells')):
            rows = self.cell_rows(result) if key == 'object_table' else result[key]
            fields = list(rows[0]) if rows else []
            table = QTableWidget(len(rows), len(fields)); table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setHorizontalHeaderLabels(fields); table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            for i, row in enumerate(rows):
                for j, field in enumerate(fields): table.setItem(i, j, QTableWidgetItem(str(row.get(field, ''))))
            table.resizeColumnsToContents(); tabs.addTab(table, tr(title))
        dialog.show(); self._dialog = dialog
        return dialog

    def choose_export(self):
        path, _ = QFileDialog.getSaveFileName(self.window, tr('relation_export'), 'multiplex_spatial.csv', 'CSV (*.csv)')
        if path: self.export_csv(Path(path).with_suffix('.csv'))

    @staticmethod
    def cell_rows(result):
        unit = result['summary']['length_unit']
        rows = []
        for row in result['object_table']:
            values = {k: v for k, v in row.items() if k not in ('contour_points_px', 'overlay_color', 'marker_means', 'x', 'y', 'nearest_target')}
            values.update({'x_'+unit: row['x'], 'y_'+unit: row['y'], 'nearest_target_'+unit: row['nearest_target']})
            for marker, mean in zip(result['parameters']['markers'], row['marker_means']):
                values[f"ch{marker['channel_index']+1}_{marker['name']}_mean_raw"] = mean
            rows.append(values)
        return rows

    def export_csv(self, path):
        result = self.current_result()
        if not self.is_fresh(result): raise ValueError(tr('relation_stale_source'))
        def write(target, rows):
            fields = list(dict.fromkeys(k for row in rows for k in row))
            with Path(target).open('w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
                for row in rows: writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
        path = Path(path)
        write(path, [dict(image_name=result['image_name'], measurement_uuid=result['measurement_uuid'],
                          image_uid=result['image_uid'], source_measurement=result['source_measurements']['nuclei'],
                          source_parameters=result['source_snapshot'].get('parameters', {}),
                          roi=result.get('roi_snapshot'), parameters=result['parameters'], **result['summary'])])
        write(path.with_name(path.stem+'_phenotypes.csv'), result['phenotypes'])
        write(path.with_name(path.stem+'_cells.csv'), self.cell_rows(result))

    def retranslate(self):
        self.controls.roi.setItemText(0, tr('spatial_full_image')); self.controls.roi.setItemText(1, tr('spatial_latest_roi')); self.sync()
