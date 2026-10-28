"""One batch selection, progress, cancellation and Excel workflow for microscopy."""
from __future__ import annotations

from copy import deepcopy
import json
from numbers import Real
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from core.batch_analysis import BatchAnalysisJob, METHODS, apply_batch_image_result, measurement_id
from core.utils.calibration_presets import CalibrationPresets
from core.utils.i18n import get_language
from core.utils.recent_directory import recent_directory, remember_file_directory
from ui.widgets.measurement_excel_export import EXCEL_VALUE_ROLE, export_tables_xlsx


def _text(en, zh):
    return zh if get_language() == "zh" else en


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _error_text(error):
    message = str(error)
    if get_language() != 'zh':
        return message
    messages = {
        'Analysis returned no measurement records; the backend may be unavailable.': '分析没有返回测量记录，分析后端可能不可用。',
        'Calibrate the Brightfield image before measuring bright round-cell diameters.': '测量细胞尺寸前，请先校准该明场图像。',
        'Bright round-cell measurement requires complete positive X and Y calibration.': '细胞测量需要完整且为正值的 X/Y 像素尺寸校准。',
        'Select at least one image for batch analysis.': '请至少选择一张图像进行批量分析。',
        'A selected image is no longer available.': '所选图像已不可用。',
        'The batch image is no longer available.': '这张批量分析图像已不可用。',
        'Fluorescence source image data is not available.': '荧光源图像数据不可用。',
        'Cellpose runtime is missing. Please reinstall the complete ScieFig installer.': '未检测到已安装的 Cellpose 运行环境，请检查程序安装。',
        'A named calibration preset requires positive X and Y pixel sizes.': '命名校准预设需要正值的 X/Y 像素尺寸。',
    }
    if isinstance(error, KeyError) and len(error.args) == 1:
        return messages.get(str(error.args[0]), message)
    for known, translated in messages.items():
        if message == known or message == repr(known):
            return translated
    return message


def _export_table(rows, sheet, long_values):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    table = QTableWidget(len(rows), len(columns))
    table.setHorizontalHeaderLabels(columns)
    numeric_columns = tuple(index for index, key in enumerate(columns)
        if any(isinstance(row.get(key), Real) and not isinstance(row.get(key), bool) for row in rows)
        and all(row.get(key) is None or isinstance(row.get(key), Real) for row in rows))
    for row_number, row in enumerate(rows):
        for column, key in enumerate(columns):
            value = row.get(key)
            text = _json(value) if isinstance(value, (dict, list, tuple)) else str(value) if value is not None else ""
            if len(text) > 32767:
                for part, start in enumerate(range(0, len(text), 16000), 1):
                    long_values.append({"sheet": sheet, "row": row_number + 2, "field": key,
                        "image_uid": row.get("image_uid"), "measurement_id": row.get("measurement_id"),
                        "part": part, "value": text[start:start + 16000]})
                text = f"Long values: {sheet} row {row_number + 2}, {key}"
            item = QTableWidgetItem(text)
            if isinstance(value, Real) and not isinstance(value, bool):
                item.setData(EXCEL_VALUE_ROLE, value)
            table.setItem(row_number, column, item)
    return table, numeric_columns


def export_batch_results_xlsx(panel, results, path):
    """Export current measurements by their original IDs, including manual review."""
    images, measurements, objects = [], [], []
    for result in results:
        identity = {**result.provenance, "calibration": result.calibration}
        images.append({**identity, "status": result.status, "error": result.error,
                       "measurement_count": len(result.measurements)})
        if result.status != "success":
            continue
        _, key, _ = METHODS[result.provenance["method"]]
        live = {measurement_id(row): row for row in panel.analysis.get(key, [])}
        for saved in result.measurements:
            # An explicit subsequent edit wins over the batch-run snapshot.
            record = live.get(measurement_id(saved), saved)
            context = {**identity, "measurement_id": measurement_id(record)}
            metrics = {key: value for key, value in record.items()
                       if key not in {"analysis", "results", "summary", "object_table", "detected_spots", "batch_provenance"}}
            for container in ("summary", "results"):
                if isinstance(record.get(container), dict):
                    metrics.update(record[container])
            if "method" in record:
                metrics["measurement_method"] = record["method"]
            measurements.append({**metrics, **context})
            native_objects = (record.get("object_table") or record.get("detected_spots")
                              or (record.get("results") if isinstance(record.get("results"), list) else []) or [])
            for index, row in enumerate(native_objects):
                objects.append({**row, **context, "object_index": index + 1})
    tables, long_values = [], []
    try:
        for title, rows in (("Images", images), ("Measurements", measurements), ("Objects", objects)):
            if rows:
                table, numeric_columns = _export_table(rows, title, long_values)
                tables.append((title, table, numeric_columns))
        if long_values:
            table, numeric_columns = _export_table(long_values, "Long values", [])
            tables.append(("Long values", table, numeric_columns))
        return export_tables_xlsx(tables, path)
    finally:
        for _, table, _ in tables:
            table.deleteLater()


class _BatchWorker(QThread):
    item_ready = pyqtSignal(object)
    progress = pyqtSignal(int, int, str)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.error = ""

    def run(self):
        try:
            self.job.run(on_result=self.item_ready.emit, on_progress=self.progress.emit)
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__


class BatchAnalysisDialog(QDialog):
    def __init__(self, panel, method: str, options: dict, parent=None, *, on_refresh=None,
                 runner=None, library=None):
        super().__init__(parent)
        self.panel, self.method, self.options = panel, method, deepcopy(options)
        self.on_refresh, self.runner = on_refresh, runner
        self.library = library if library is not None else CalibrationPresets()
        self.results, self._worker = [], None
        self.setWindowTitle(_text("Batch analysis", "批量分析"))
        self.setModal(True)
        self.resize(760, 570)
        self.setMinimumSize(480, 420)
        root = QVBoxLayout(self)
        root.setSpacing(8)
        hint = QLabel(_text("Select imported images. The same method and settings will be used; existing measurements are kept.",
                            "勾选已导入图像，使用相同方法和参数分析；保留已有测量结果。"))
        hint.setWordWrap(True)
        root.addWidget(hint)
        whole_image = (method == "count_channel_objects"
            or (panel.panel_type == "fluorescence" and not any(options.get(key) is not None for key in ("roi_type", "geometry", "roi_mask")))
            or (options.get("roi") or {}).get("mode") == "whole_image")
        scope = (_text("Scope: full image", "范围：整幅图像") if whole_image else
                 _text("Scope: saved per-image ROI or the explicit ROI in Settings", "范围：各图像已保存的 ROI，或参数中明确指定的 ROI"))
        self.scope_label = QLabel(scope)
        self.scope_label.setWordWrap(True)
        root.addWidget(self.scope_label)
        method_names = {
            "count_channel_objects": _text("Fluorescence spot / cell count", "荧光点／细胞计数"),
            "run_ai_assisted_analysis": _text("Fluorescence AI-assisted cell area", "荧光 AI 辅助细胞面积"),
            "run_smart_assisted_analysis": _text("Fluorescence smart-assisted analysis", "荧光智能辅助分析"),
            "measure_bright_round_cells": _text("Brightfield cell size", "明场细胞尺寸"),
            "create_measurement": _text("Brightfield traditional area", "明场传统面积测量"),
            "auto_analyze_he": _text("Brightfield guided H&E area", "明场引导式 H&E 面积"),
            "create_profile_preset_measurement": _text("Brightfield stain preset area", "明场染色预设面积"),
            "create_ai_object_count_measurement": _text("H&E fiber area", "H&E 纤维面积"),
            "measure_dab_positive_area": _text("IHC DAB positive area", "IHC DAB 阳性面积"),
            "auto_analyze_dab": _text("IHC automatic DAB analysis", "IHC 自动 DAB 分析"),
        }
        form = QFormLayout()
        method_label = QLabel(method_names.get(method, method))
        method_label.setWordWrap(True)
        form.addRow(_text("Method", "方法"), method_label)
        self.settings_view = QPlainTextEdit(json.dumps(options, ensure_ascii=False, indent=2, default=str))
        self.settings_view.setReadOnly(True)
        self.settings_view.setMaximumHeight(90)
        self.settings_view.setAccessibleName(_text("Analysis settings snapshot", "分析参数快照"))
        form.addRow(_text("Settings", "参数"), self.settings_view)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(_text("Keep each image's calibration", "保留各图像的校准"), None)
        for preset in self.library.items():
            self.preset_combo.addItem(f"{preset['name']} · X {preset['um_per_px_x']:g} / Y {preset['um_per_px_y']:g} µm/px", preset)
        form.addRow(_text("Calibration", "校准"), self.preset_combo)
        root.addLayout(form)
        calibration_hint = QLabel(_text("Use a preset only for images with the same acquisition and pixel size. Resized images need recalibration. Cancellation finishes the current image first.",
            "仅对采集条件和像素尺寸相同的图像应用预设；缩放后的图像需重新校准。取消将在当前图像完成后生效。"))
        calibration_hint.setWordWrap(True)
        calibration_hint.setObjectName("DimLabel")
        root.addWidget(calibration_hint)
        selection = QHBoxLayout()
        self.select_all_button = QPushButton(_text("Select all", "全选"))
        self.select_none_button = QPushButton(_text("Select none", "全不选"))
        self.selection_label = QLabel()
        selection.addWidget(self.select_all_button)
        selection.addWidget(self.select_none_button)
        selection.addWidget(self.selection_label, 1)
        root.addLayout(selection)
        self.image_table = QTableWidget(0, 4)
        self.image_table.setHorizontalHeaderLabels([_text("Image", "图像"), _text("Calibration (µm/px)", "校准 (µm/px)"),
            _text("Status", "状态"), _text("Results / error", "结果／错误")])
        self.image_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.image_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.image_table.verticalHeader().hide()
        self.image_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.image_table.horizontalHeader().setStretchLastSection(True)
        self.image_table.setColumnWidth(0, 200)
        self.image_table.setColumnWidth(1, 160)
        self.image_table.setColumnWidth(2, 105)
        self._rows = {}
        for image in panel.images():
            row = self.image_table.rowCount()
            self.image_table.insertRow(row)
            uid = str(image["uid"])
            self._rows[uid] = row
            item = QTableWidgetItem(str(image.get("source_name", uid)))
            item.setData(Qt.ItemDataRole.UserRole, uid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setToolTip(str(image.get("source_path", "")))
            self.image_table.setItem(row, 0, item)
            scale = panel.measurement_scale_bar(uid)
            calibration = (f"X {scale['um_per_px_x']:g} / Y {scale['um_per_px_y']:g}"
                           if scale.get("calibrated") else _text("Uncalibrated", "未校准"))
            self.image_table.setItem(row, 1, QTableWidgetItem(calibration))
            self.image_table.setItem(row, 2, QTableWidgetItem(_text("Ready", "就绪")))
        root.addWidget(self.image_table, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, max(1, len(self._rows)))
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.start_button = QPushButton(_text("Run batch", "开始批量分析"))
        self.start_button.setObjectName("PrimaryButton")
        self.cancel_button = QPushButton(_text("Cancel batch", "取消批量分析"))
        self.cancel_button.setEnabled(False)
        self.export_button = QPushButton(_text("Export batch Excel…", "导出批量 Excel…"))
        self.export_button.setEnabled(False)
        self.close_button = QPushButton(_text("Close", "关闭"))
        for button in (self.start_button, self.cancel_button, self.export_button, self.close_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        root.addLayout(actions)
        self.select_all_button.clicked.connect(lambda: self._select_all(True))
        self.select_none_button.clicked.connect(lambda: self._select_all(False))
        self.image_table.itemChanged.connect(self._sync_selection)
        self.start_button.clicked.connect(self.start_batch)
        self.cancel_button.clicked.connect(self.cancel_batch)
        self.export_button.clicked.connect(self._export)
        self.close_button.clicked.connect(self.reject)
        self._sync_selection()

    @property
    def is_running(self):
        return self._worker is not None

    def selected_image_uids(self):
        return [uid for uid, row in self._rows.items()
                if self.image_table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _select_all(self, selected):
        for row in self._rows.values():
            self.image_table.item(row, 0).setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)

    def _sync_selection(self):
        count = len(self.selected_image_uids())
        self.selection_label.setText(_text(f"{count} selected", f"已选 {count} 张"))
        self.start_button.setEnabled(count > 0 and not self.is_running)
        if not self._rows:
            self.status_label.setText(_text("Import images before starting a batch.", "请先导入图像。"))

    def start_batch(self):
        if self.is_running or not self.selected_image_uids():
            return
        try:
            job = BatchAnalysisJob(self.panel, self.selected_image_uids(), self.method, self.options,
                calibration_preset=self.preset_combo.currentData(), runner=self.runner)
        except Exception as exc:
            self.status_label.setText(_error_text(exc))
            return
        self.results = []
        self.progress_bar.setRange(0, len(job.image_uids))
        self.progress_bar.setValue(0)
        self.export_button.setEnabled(False)
        for uid, row in self._rows.items():
            self.image_table.setItem(row, 2, QTableWidgetItem(_text("Queued", "等待中") if uid in job.image_uids else _text("Not selected", "未选择")))
            self.image_table.setItem(row, 3, QTableWidgetItem(""))
        self._worker = _BatchWorker(job, self)
        self._worker.item_ready.connect(self._receive_result)
        self._worker.progress.connect(self._progress)
        self._worker.finished.connect(self._finished)
        for widget in (self.image_table, self.preset_combo, self.select_all_button, self.select_none_button, self.start_button):
            widget.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.image_table.setItem(self._rows[job.image_uids[0]], 2, QTableWidgetItem(_text("Running", "运行中")))
        self.status_label.setText(_text("Running batch analysis…", "正在进行批量分析…"))
        self._worker.start()

    def cancel_batch(self):
        if self._worker is not None:
            self._worker.job.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText(_text("Finishing the current image; remaining images will be cancelled.", "当前图像完成后将取消其余图像。"))

    def _refresh(self):
        if self.on_refresh is not None:
            try:
                self.on_refresh()
            except Exception as exc:
                self.status_label.setText(_text("Results saved; preview refresh failed: ", "结果已保存，但界面刷新失败：") + str(exc))

    def _receive_result(self, result):
        try:
            changed = apply_batch_image_result(self.panel, result)
        except Exception as exc:
            changed = False
            result.status, result.error = "failed", str(exc)
        self.results.append(result)
        row = self._rows[result.image_uid]
        statuses = {"success": _text("Completed", "已完成"), "failed": _text("Failed", "失败"), "cancelled": _text("Cancelled", "已取消")}
        self.image_table.setItem(row, 2, QTableWidgetItem(statuses[result.status]))
        detail = _error_text(result.error) or (_text(f"{len(result.measurements)} measurement(s)", f"{len(result.measurements)} 条测量结果") if result.status == "success" else "")
        item = QTableWidgetItem(detail)
        item.setToolTip(detail)
        self.image_table.setItem(row, 3, item)
        if changed:
            scale = result.calibration
            if scale.get("calibrated"):
                self.image_table.setItem(row, 1, QTableWidgetItem(f"X {scale['um_per_px_x']:g} / Y {scale['um_per_px_y']:g}"))
            self._refresh()

    def _progress(self, done, total, uid):
        self.progress_bar.setValue(done)
        if done < total and self.cancel_button.isEnabled():
            next_uid = self._worker.job.image_uids[done]
            row = self._rows[next_uid]
            self.image_table.setItem(row, 2, QTableWidgetItem(_text("Running", "运行中")))

    def _finished(self):
        error = self._worker.error
        self._worker.deleteLater()
        self._worker = None
        for widget in (self.image_table, self.preset_combo, self.select_all_button, self.select_none_button):
            widget.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.export_button.setEnabled(bool(self.results))
        counts = {state: sum(result.status == state for result in self.results) for state in ("success", "failed", "cancelled")}
        self.status_label.setText(_error_text(error) or _text(
            f"Completed: {counts['success']} · Failed: {counts['failed']} · Cancelled: {counts['cancelled']}",
            f"完成：{counts['success']} · 失败：{counts['failed']} · 取消：{counts['cancelled']}"))
        self._sync_selection()
        self._refresh()

    def _export(self):
        selected, _ = QFileDialog.getSaveFileName(self, _text("Export batch Excel", "导出批量 Excel"),
            str(Path(recent_directory()) / "Batch_measurements.xlsx"), "Excel (*.xlsx)")
        if not selected:
            return
        target = Path(selected)
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")
            if target.exists() and QMessageBox.question(self, self.windowTitle(),
                _text("Replace the existing Excel file?\n", "替换已有 Excel 文件？\n") + str(target),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        try:
            export_batch_results_xlsx(self.panel, self.results, target)
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), _error_text(exc))
            return
        remember_file_directory(target)
        self.status_label.setText(_text("Excel saved: ", "Excel 已保存：") + str(target))

    def reject(self):
        if self.is_running:
            self.cancel_batch()
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_running:
            self.cancel_batch()
            event.ignore()
        else:
            super().closeEvent(event)
