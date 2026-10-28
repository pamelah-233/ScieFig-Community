"""Shared entry points: current-parameter batches and explicit measurement review."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox, QPushButton
from core.utils.i18n import bind_i18n, tr
from ui.widgets.calibration_controls import action_grid


class MicroscopyWorkflowController:
    def __init__(self, window):
        self.window = window
        self.review_buttons, self.batch_buttons = {}, {}
        layouts = {
            'fluorescence': window.fluoro_workspace.context_stack.widget(2).layout(),
            'brightfield': window.data_panel.brightfield_measure_group.layout(),
            'ihc': window.data_panel.ihc_results_group.layout(),
        }
        for module, layout in layouts.items():
            review = bind_i18n(QPushButton(), 'review_objects')
            batch = bind_i18n(QPushButton(), 'batch_analysis')
            review.clicked.connect(lambda checked=False, name=module: self.review(name))
            batch.clicked.connect(lambda checked=False, name=module: self.batch(name))
            self.review_buttons[module], self.batch_buttons[module] = review, batch
            layout.insertWidget(0, action_grid((review, batch)))

    def panel(self, module):
        if module == 'fluorescence':
            return self.window.fluoro_workspace.panel
        return (self.window._active_brightfield_panel() if module == 'brightfield'
                else self.window._active_ihc_panel())

    def batch_spec(self, module):
        controls, fluoro = self.window.data_panel, self.window.fluoro_workspace
        whole = {'mode': 'whole_image', 'items': [], 'active_item_uuid': None}
        if module == 'fluorescence':
            if fluoro.measure_mode == 'spot_count':
                return 'count_channel_objects', dict(channel_indices=fluoro._effective_count_channels(),
                    method=fluoro.threshold_method_combo.currentData() or fluoro.threshold_method_combo.currentText(),
                    manual_value=fluoro.threshold_value_spin.value(),
                    min_area_px=fluoro.count_min_area_spin.value(), algorithm=fluoro.count_algorithm_combo.currentData(),
                    diameter_px=fluoro.count_diameter_spin.value(), sensitivity=fluoro.count_sensitivity_spin.value())
            if fluoro.measure_mode == 'ai_assisted':
                return 'run_ai_assisted_analysis', dict(channel_indices=fluoro._effective_ai_channels(),
                    settings=fluoro.ai_analysis_settings())
            raise ValueError(tr('batch_choose_count_mode'))
        if module == 'ihc':
            mode = controls.current_ihc_guided_mode()
            if mode == 'expert':
                return 'measure_dab_positive_area', dict(settings=controls.ihc_analysis_settings(), roi=whole)
            settings = controls.ihc_guided_analysis_settings() if mode == 'assisted' else controls.ihc_foolproof_analysis_settings()
            return 'auto_analyze_dab', dict(settings=settings, guided_mode=mode, roi=whole)
        kind = controls.brightfield_measure_type_combo.currentData()
        if kind == 'cells':
            return 'measure_bright_round_cells', dict(settings=controls.bright_round_cell_analysis_settings(), roi=whole)
        if kind == 'fiber':
            return 'create_ai_object_count_measurement', dict(settings=controls.brightfield_analysis_settings(), roi=whole)
        mode = controls.current_brightfield_guided_mode()
        stain = controls.brightfield_stain_type_combo.currentText()
        if mode == 'expert':
            return 'create_measurement', dict(settings=controls.brightfield_analysis_settings(), roi=whole)
        if stain != 'H&E':
            if mode == 'assisted':
                raise ValueError(tr('brightfield_assisted_non_he_unavailable'))
            return 'create_profile_preset_measurement', dict(stain_type=stain, guided_mode=mode, roi=whole)
        settings = controls.brightfield_guided_analysis_settings() if mode == 'assisted' else controls.brightfield_foolproof_analysis_settings()
        return 'auto_analyze_he', dict(settings=settings, guided_mode=mode, roi=whole)

    def batch(self, module):
        from ui.dialogs.batch_analysis_dialog import BatchAnalysisDialog
        panel = self.panel(module)
        service = self.window._analysis_job_service
        import_module = 'fluoro' if module == 'fluorescence' else module
        busy = service.is_busy(f'import:{import_module}') or service.is_busy(import_module)
        if module == 'brightfield':
            busy = busy or service.is_busy('brightfield-cellpose') or service.is_busy('brightfield-fiber')
        if module == 'fluorescence':
            busy = busy or bool(self.window.fluoro_workspace._busy_analysis_operations)
        if busy:
            QMessageBox.information(self.window, tr('batch_analysis'), tr('batch_wait_current'))
            return
        if panel is None or not panel.images():
            QMessageBox.information(self.window, tr('batch_analysis'), tr('scale_no_image_selected'))
            return
        try:
            method, options = self.batch_spec(module)
            dialog = BatchAnalysisDialog(panel, method, options, self.window,
                                         on_refresh=lambda: self.refresh(module))
            dialog.exec()
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self.window, tr('batch_analysis'), str(exc))

    def selected_identity(self, module):
        from core.measurement_review_adapter import measurement_identity
        if module == 'fluorescence':
            return self.window.fluoro_workspace.selected_result_identity()
        if module == 'ihc':
            uuid = self.window.data_panel.selected_ihc_measurement_uuid()
            return ('measurement_uuid', uuid) if uuid else None
        kind = self.window.data_panel.brightfield_measure_type_combo.currentData()
        if kind == 'cells':
            panel = self.panel(module)
            measurement = panel.analysis.get('bright_round_cell_diameter') if panel is not None else None
            if isinstance(measurement, dict) and measurement_identity(measurement)[1]:
                return measurement_identity(measurement)
            return None
        if kind == 'fiber':
            uuid = self.window.data_panel.selected_brightfield_ai_measurement_uuid()
            return ('measurement_uuid', uuid) if uuid else None
        uuid = self.window.data_panel.selected_brightfield_measurement_uuid()
        return ('measurement_uuid', uuid) if uuid else None

    def review(self, module):
        from core.measurement_review_adapter import eligible_measurements, apply_review_to_panel, measurement_identity
        from ui.dialogs.object_review_dialog import ObjectReviewDialog
        panel = self.panel(module)
        rows = eligible_measurements(panel) if panel is not None else []
        selected = self.selected_identity(module)
        if selected:
            record = next((row for row in rows if measurement_identity(row) == selected), None)
            if record is None:
                QMessageBox.information(self.window, tr('review_objects'), tr('review_no_objects'))
                return
        elif not rows:
            QMessageBox.information(self.window, tr('review_objects'), tr('review_no_objects'))
            return
        else:
            # Reverse chronology, but selection is resolved by the actual record/ID.
            rows = list(reversed(rows))
            labels = [f"{r.get('image_name') or r.get('image_uid', '')} · {r.get('channel_name', '')} · {measurement_identity(r)[1]}" for r in rows]
            if len(rows) > 1:
                chosen, ok = QInputDialog.getItem(self.window, tr('review_objects'), tr('review_choose_run'), labels, 0, False)
                if not ok:
                    return
                record = rows[labels.index(chosen)]
            else:
                record = rows[0]
        try:
            dialog = ObjectReviewDialog.from_measurement(panel, record, self.window)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                updated = apply_review_to_panel(panel, dialog.session.original, dialog.applied_record)
                panel.set_active_image(str(updated['image_uid']))
                self.refresh(module, updated)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self.window, tr('review_objects'), str(exc))

    def refresh(self, module, reviewed=None):
        window, panel = self.window, self.panel(module)
        if module == 'fluorescence':
            workspace = window.fluoro_workspace
            workspace.refresh()
            if reviewed:
                identity = str(reviewed.get('count_uuid') or reviewed.get('ai_measurement_uuid') or reviewed.get('measurement_uuid') or '')
                for table in (workspace.count_table, workspace.ai_results_table, workspace.ai_summary_stats_table, workspace.measurement_table):
                    for row in range(table.rowCount()):
                        item = table.item(row, 0)
                        if item and str(item.data(Qt.ItemDataRole.UserRole) or '') == identity:
                            table.selectRow(row)
                            break
                if reviewed.get('ai_measurement_uuid'):
                    workspace.canvas.show_ai_assisted_overlay([reviewed])
                    workspace._sync_ai_overlay_buttons(has_overlay=bool(reviewed.get('ai_object_count')), show_overlay=True)
                elif reviewed.get('count_uuid'):
                    workspace.canvas.show_count_overlay(reviewed)
            return
        if module == 'ihc':
            window._refresh_ihc_measurement_views(panel, reviewed)
            return
        window._refresh_brightfield_measurement_views(panel)
        if reviewed and isinstance(reviewed.get('results'), list):
            window.data_panel.populate_bright_round_cell_results(reviewed)
            rgb = panel.render_bright_round_cell_overlay(reviewed)
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
            window.brightfield_workspace.canvas.set_display_state(QPixmap.fromImage(image),
                title=tr('review_objects'), body=str(reviewed.get('image_name', '')))
