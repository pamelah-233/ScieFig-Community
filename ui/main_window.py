from __future__ import annotations

import sys

import csv
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QImage, QImageWriter, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PyQt6.QtSvg import QSvgGenerator, QSvgRenderer
from PyQt6.QtWidgets import (
    QFileDialog,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.constants import WB_STEP_CROP, WB_STEP_DENSITY, WB_STEP_EXPORT, WB_STEP_IMPORT, WB_STEP_LABELS, WB_STEP_LAYOUT, WB_STEP_STATS
from core.densitometry import measure_densitometry
from core.brightfield_statistics import (
    BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
    BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL,
    BRIGHTFIELD_SEGMENTED_REGIONS_LABEL,
    collect_brightfield_ai_object_area_distribution_rows,
    compute_brightfield_ai_object_count_statistics,
    compute_brightfield_statistics,
    summarize_brightfield_ai_object_count_measurements,
)
from core.ihc_statistics import compute_ihc_statistics
from core.image_analysis.brightfield_guided import BRIGHTFIELD_PREVIEW_DEBOUNCE_MS
from core.style_preset_resolver import last_runtime_style_preset_status
from core.measurement_freshness import (
    STATUS_I18N_KEYS,
    analysis_job_fingerprint,
    attach_analysis_inputs,
    build_analysis_inputs,
    display_status,
    record_image_uid,
    saved_inputs_from_record,
)
from core.utils.i18n import bind_i18n, get_language, retranslate_bound_widgets, set_language, tr, trf
from core.utils.hashing import sha256_json
from core.utils.image_formats import BRIGHTFIELD_IMAGE_SUFFIXES, COMPOSER_IMAGE_SUFFIXES, FLUORESCENCE_IMAGE_SUFFIXES, IHC_IMAGE_SUFFIXES, WB_IMAGE_SUFFIXES, image_filter
from core.utils.logging import log_exception
from core.utils.recent_directory import recent_directory, remember_file_directory
from core.utils.commands import SnapshotCommand, UndoManager
from core.utils.text_formatting import display_unit
from core.utils.svg_text import apply_svg_text_overrides
from core.chart_payload import layout_output_chart_kind
from ui.controllers.brightfield_ai_object_count_controller import BrightfieldAIObjectCountController
from ui.controllers.analysis_job_service import AnalysisJobService
from ui.controllers.chart_kind_controller import ChartKindController, selected_layout_items, unique_layout_object_refs
from ui.controllers.panel_wheel_controller import filter_panel_wheel
from ui.controllers.ihc_analysis_controller import IHCAnalysisController
from ui.controllers.ihc_mode import normalize_ihc_mode
from ui.module_selector import ModuleSelector
from ui.dialogs.wb_export_dialog import WBExportDialog
from ui.widgets.style_sidebar import StyleSidebar
from ui.widgets.chart_kind_controls import ChartKindControls
from ui.widgets.editable_text_item import cleanup_registered_graphics_scenes
from ui.widgets.app_shell import V9AppShell
from ui.export_menu import (
    ACTION_FIGURE,
    ACTION_IMAGE,
    ACTION_SUMMARY_JSON,
    ACTION_TABLE_AI_STATS_CSV,
    ACTION_TABLE_EXCEL,
    ACTION_TABLE_RESULTS_CSV,
    ACTION_TABLE_STATS_CSV,
    FIGURE_DIALOG_FILTER,
    apply_native_png_suffix,
    apply_selected_figure_suffix,
    build_export_menu,
    menu_entries_for_module,
    suggested_export_path,
)


class _LazyProject:
    """Keep the historical module attribute without importing model dependencies at import time."""

    @staticmethod
    def _implementation():
        from core.models import Project as implementation

        return implementation

    def __call__(self, *args, **kwargs):
        return self._implementation()(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._implementation(), name)


Project = _LazyProject()


@dataclass(frozen=True)
class _MicroscopyImportResult:
    panel: Any
    records: list[dict[str, Any]]
    failed_path: str | None = None
    error: Exception | None = None
    preview: QImage | None = None


@dataclass(frozen=True)
class _FluorescenceAnalysisResult:
    panel: Any
    output: Any

    def dispose(self) -> None:
        self.panel.dispose()


def build_distribution_comparison_figure(*args, **kwargs):
    from core.plotting.statistics_plot import build_distribution_comparison_figure as implementation

    return implementation(*args, **kwargs)


def build_group_statistics_figure(*args, **kwargs):
    from core.plotting.statistics_plot import build_group_statistics_figure as implementation

    return implementation(*args, **kwargs)


def resolve_statistics_plot_style(*args, **kwargs):
    from core.plotting.statistics_plot import resolve_statistics_plot_style as implementation

    return implementation(*args, **kwargs)


def build_densitometry_figure(*args, **kwargs):
    from core.plotting_densitometry import build_densitometry_figure as implementation

    return implementation(*args, **kwargs)


def build_wb_statistics_figure(*args, **kwargs):
    from core.plotting_densitometry import build_wb_statistics_figure as implementation

    return implementation(*args, **kwargs)


def build_qpcr_figure(*args, **kwargs):
    from core.plotting import build_qpcr_figure as implementation

    return implementation(*args, **kwargs)


def export_fluoro_figure_scene(*args, **kwargs):
    from core.export import export_fluoro_figure_scene as implementation

    return implementation(*args, **kwargs)


def export_fluoro_measurements(*args, **kwargs):
    from core.export import export_fluoro_measurements as implementation

    return implementation(*args, **kwargs)


def export_fluoro_metadata(*args, **kwargs):
    from core.export import export_fluoro_metadata as implementation

    return implementation(*args, **kwargs)


def export_fluoro_source_folder(*args, **kwargs):
    from core.export import export_fluoro_source_folder as implementation

    return implementation(*args, **kwargs)


def export_qpcr_panel(*args, **kwargs):
    from core.export import export_qpcr_panel as implementation

    return implementation(*args, **kwargs)


def export_wb_figure_scene(*args, **kwargs):
    from core.export import export_wb_figure_scene as implementation

    return implementation(*args, **kwargs)


def export_wb_source_folder(*args, **kwargs):
    from core.export import export_wb_source_folder as implementation

    return implementation(*args, **kwargs)


def _load_runtime_dependencies() -> None:
    """Load the existing eager UI stack when MainWindow construction begins."""
    global BrightfieldPanel, ExternalImagePanel, FluorescencePanel, IHCPanel, QPCRPanel, WesternBlotPanel
    global normalize_brightfield_stain_type
    global FLUORESCENCE_LAYOUT_EXPORT_KEY, apply_fluorescence_export_item_metadata
    global fluorescence_export_item_for_scene_bounds, fluorescence_plot_export_key
    global BRIGHTFIELD_LAYOUT_EXPORT_KEY, BRIGHTFIELD_OVERLAY_EXPORT_KEY, BRIGHTFIELD_SOURCE_IMAGE_KEY
    global BRIGHTFIELD_STATISTICS_EXPORT_KEY, apply_brightfield_export_item_metadata, brightfield_export_item_for_scene_bounds
    global IHC_LAYOUT_EXPORT_KEY, IHC_OVERLAY_EXPORT_KEY, IHC_SOURCE_IMAGE_KEY, IHC_STATISTICS_EXPORT_KEY
    global apply_ihc_export_item_metadata, ihc_export_item_for_scene_bounds
    global QPCR_LAYOUT_EXPORT_KEY, apply_qpcr_export_item_metadata, qpcr_export_item_for_scene_bounds, qpcr_plot_export_key
    global apply_wb_export_item_metadata, wb_export_item_for_scene_bounds
    global DataInputPanel, MODULE_PLACEHOLDERS, localized_module_placeholder, BrightfieldWorkspace, FluorescenceWorkspace, IHCWorkspace, ImageBrowserWorkspace, QPCRLayoutWorkspace
    global PreviewCanvas, ComposerWorkspace, StyleLabWorkspace, WBCanvas, EditableMovableTextItem
    global BrightfieldTraditionalAnalysisController, journal_preset_names

    from core.models import BrightfieldPanel, ExternalImagePanel, FluorescencePanel, IHCPanel, QPCRPanel, WesternBlotPanel
    from core.models.brightfield_panel import normalize_brightfield_stain_type
    from core.plotting.style_presets import journal_preset_names
    from ui.controllers.brightfield_traditional_controller import BrightfieldTraditionalAnalysisController
    from ui.layout_adapters.brightfield_layout_adapter import (
        BRIGHTFIELD_LAYOUT_EXPORT_KEY,
        BRIGHTFIELD_OVERLAY_EXPORT_KEY,
        BRIGHTFIELD_SOURCE_IMAGE_KEY,
        BRIGHTFIELD_STATISTICS_EXPORT_KEY,
        apply_brightfield_export_item_metadata,
        brightfield_export_item_for_scene_bounds,
    )
    from ui.layout_adapters.fluorescence_layout_adapter import (
        FLUORESCENCE_LAYOUT_EXPORT_KEY,
        apply_fluorescence_export_item_metadata,
        fluorescence_export_item_for_scene_bounds,
        fluorescence_plot_export_key,
    )
    from ui.layout_adapters.ihc_layout_adapter import (
        IHC_LAYOUT_EXPORT_KEY,
        IHC_OVERLAY_EXPORT_KEY,
        IHC_SOURCE_IMAGE_KEY,
        IHC_STATISTICS_EXPORT_KEY,
        apply_ihc_export_item_metadata,
        ihc_export_item_for_scene_bounds,
    )
    from ui.layout_adapters.qpcr_layout_adapter import (
        QPCR_LAYOUT_EXPORT_KEY,
        apply_qpcr_export_item_metadata,
        qpcr_export_item_for_scene_bounds,
        qpcr_plot_export_key,
    )
    from ui.layout_adapters.wb_layout_adapter import apply_wb_export_item_metadata, wb_export_item_for_scene_bounds
    from ui.panels.brightfield_workspace import BrightfieldWorkspace
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace
    from ui.panels.ihc_workspace import IHCWorkspace
    from ui.panels.image_browser_workspace import ImageBrowserWorkspace
    from ui.panels.placeholder_panel import DataInputPanel, MODULE_PLACEHOLDERS, localized_module_placeholder
    from ui.panels.qpcr_workspace import QPCRLayoutWorkspace
    from ui.panels.composer_workspace import ComposerWorkspace
    from ui.widgets.preview_canvas import PreviewCanvas
    from ui.widgets.style_lab_workspace import StyleLabWorkspace
    from ui.widgets.wb_canvas import WBCanvas
    from ui.widgets.editable_text_item import EditableMovableTextItem


WB_COMPOSER_EXPORT_DPI = 1200
QPCR_COMPOSER_EXPORT_DPI = 1200
FLUORESCENCE_COMPOSER_EXPORT_DPI = 1200
IHC_COMPOSER_EXPORT_DPI = 1200
BRIGHTFIELD_COMPOSER_EXPORT_DPI = 1200

V9_STAGE_TITLE_KEYS = {
    "qpcr": "v9_stage_qpcr",
    "wb": "v9_stage_wb",
    "fluoro": "v9_stage_fluoro",
    "ihc": "v9_stage_ihc",
    "brightfield": "v9_stage_brightfield",
    "viewer": "v9_stage_viewer",
    "composer": "v9_stage_composer",
    "stylelab": "v9_stage_stylelab",
}

V9_RIGHT_TITLE_KEYS = {
    "qpcr": "v9_right_qpcr",
    "wb": "v9_right_wb",
    "fluoro": "v9_right_fluoro",
    "ihc": "v9_right_ihc",
    "brightfield": "v9_right_brightfield",
    "viewer": "v9_right_viewer",
    "composer": "v9_right_composer",
    "stylelab": "v9_right_stylelab",
}


class CurrentPageStackedWidget(QStackedWidget):
    def sizeHint(self) -> QSize:
        widget = self.currentWidget()
        if widget is not None:
            return widget.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        widget = self.currentWidget()
        if widget is not None:
            return widget.minimumSizeHint()
        return super().minimumSizeHint()


class MainWindow(QMainWindow):
    def __init__(self, resources_dir: Path, parent=None) -> None:
        super().__init__(parent)
        _load_runtime_dependencies()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resources_dir = resources_dir
        self.project = self._create_default_project()
        self._analysis_job_service = AnalysisJobService(self)
        self._analysis_job_service.busy_changed.connect(self._handle_analysis_busy_changed)
        self._analysis_attempts: dict[tuple[str, str], dict[str, Any]] = {}
        self._analysis_attempts_by_job: dict[str, dict[str, Any]] = {}
        self._freshness_alive = True
        self._analysis_button_enabled_before_busy: dict[str, bool] = {}
        self._import_control_enabled_before_busy: dict[str, list[tuple[QPushButton, bool]]] = {}
        self._clean_project_fingerprint: str | None = None
        self._project_path: Path | None = None
        self.active_module = "qpcr"
        self.wb_history = UndoManager(max_steps=50)
        self._wb_selected_strip_index = 0
        self._active_wb_text_item: EditableMovableTextItem | None = None
        self._active_wb_line_item = None
        self._ihc_example_samples_by_image: dict[str, list[dict[str, object]]] = {}
        self._ihc_active_example_mode = ""
        self._syncing_ihc_mode = False
        self._syncing_brightfield_mode = False
        self._brightfield_preview_debounce_timer = QTimer(self)
        self._brightfield_preview_debounce_timer.setSingleShot(True)
        self._brightfield_preview_debounce_timer.setInterval(BRIGHTFIELD_PREVIEW_DEBOUNCE_MS)
        self._brightfield_preview_debounce_timer.timeout.connect(self._flush_brightfield_preview_debounce)
        self._brightfield_preview_debounce_pending = False
        self.chart_kind_controller = ChartKindController()
        self.chart_kind_controls = ChartKindControls()
        self.chart_kind_controls.kind_chosen.connect(self._on_chart_kind_chosen)

        self.setWindowTitle("ScieFig")
        self.resize(1240, 780)
        self._pending_lane_edge_x: float | None = None
        self._build_menu()
        self._build_central_ui()
        self._build_status_bar()
        from ui.controllers.workflow_presentation_controller import WorkflowPresentationController
        self.workflow_ui = WorkflowPresentationController(self)
        from ui.controllers.analysis_feedback_controller import AnalysisFeedbackController
        self.analysis_feedback = AnalysisFeedbackController(self)
        self._build_shortcuts()
        self._apply_button_readability_policy()
        QApplication.instance().installEventFilter(self)
        self.set_module("qpcr")
        self._apply_button_readability_policy()
        self._ensure_within_screen()
        self._capture_clean_project_baseline()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.layout_edit_controller.flush()
        self.composer_workspace.controller.flush_edits()
        if not self._confirm_destructive_action(tr("action_close_sciefig")):
            event.ignore()
            return
        if not self.stylelab_workspace.shutdown_local_extraction():
            event.ignore()
            return
        if hasattr(self, "project_recovery"):
            self.project_recovery.note_session_ending()
        self._freshness_alive = False
        self.analysis_feedback.timer.stop()
        self._analysis_job_service.shutdown()
        self._dispose_project_transient_caches(self.project)
        cleanup_registered_graphics_scenes()
        super().closeEvent(event)

    def _dispose_project_transient_caches(self, project: Project) -> None:
        for panel in project.panels:
            dispose = getattr(panel, "dispose", None)
            if callable(dispose):
                dispose()
        project.dispose()

    def _project_fingerprint(self) -> str:
        return sha256_json(self.project.to_dict())

    @staticmethod
    def _microscopy_import_project_fingerprint(project) -> str:
        payload = deepcopy(project.to_dict())
        payload["panels"] = [str(panel.panel_uuid) for panel in project.panels]
        payload.pop("panel_registry", None)
        payload.pop("browser_workspace_state", None)
        return sha256_json(payload)

    def _capture_clean_project_baseline(self) -> None:
        self._clean_project_fingerprint = self._project_fingerprint()

    def is_project_dirty(self) -> bool:
        return self._project_fingerprint() != self._clean_project_fingerprint

    def _confirm_destructive_action(self, action: str) -> bool:
        if not self.is_project_dirty():
            return True
        response = QMessageBox.question(
            self,
            tr("unsaved_changes_title"),
            trf("save_changes_before_action", action=action),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response == QMessageBox.StandardButton.Save:
            return self.save_project()
        return response == QMessageBox.StandardButton.Discard

    def eventFilter(self, watched, event) -> bool:
        shell = getattr(self, "v9_shell", None)
        if filter_panel_wheel(watched, event, shell.right_panel if shell else None):
            return True
        return super().eventFilter(watched, event)

    def _apply_button_readability_policy(self) -> None:
        """Keep action rows usable when the module rail narrows the workflow pane."""
        roots = [
            self,
            self.data_panel,
            self.qpcr_workflow_panel,
            self.wb_workflow_panel,
            self.fluoro_workspace.workflow_panel,
            self.ihc_workspace.workflow_panel,
            self.brightfield_workspace.workflow_panel,
            self.composer_workspace,
            self.style_sidebar,
        ]
        buttons = {
            button
            for root in roots
            if not any(other is not root and other.isAncestorOf(root) for other in roots)
            for button in root.findChildren(QPushButton)
        }
        for button in buttons:
            if sys.platform in {'darwin', 'win32'} and button in getattr(getattr(self, 'v9_shell', None), '_action_buttons', {}).values():
                continue  # The responsive toolbar owns its button widths and tooltips.
            if button.minimumWidth():
                button.setMinimumWidth(0)
            if not button.toolTip() or bool(button.property("sciefigAutoButtonTooltip")):
                if button.toolTip() != button.text():
                    button.setToolTip(button.text())
                if not button.property("sciefigAutoButtonTooltip"):
                    button.setProperty("sciefigAutoButtonTooltip", True)

    def _create_default_project(self) -> Project:
        project = Project(title="Untitled Figure")
        project.append_audit("create_project", project.title)
        project.add_panel(QPCRPanel(panel_id="Figure1A"))
        project.add_panel(WesternBlotPanel(panel_id="Figure1B"))
        project.add_panel(FluorescencePanel(panel_id="Figure1C"))
        project.add_panel(ExternalImagePanel(panel_id="Figure1D"))
        project.add_panel(IHCPanel(panel_id="Figure1E"))
        project.add_panel(BrightfieldPanel(panel_id="Figure1F"))
        return project

    def _build_menu(self) -> None:
        menu = self.menuBar()
        self._top_menus = {
            "file": menu.addMenu(tr("file")),
            "edit": menu.addMenu(tr("edit")),
            "tools": menu.addMenu(tr("tools")),
            "presets": menu.addMenu(tr("presets")),
            "imagej": menu.addMenu(tr("imagej")),
            "language": menu.addMenu(tr("language")),
            "help": menu.addMenu(tr("help")),
        }
        file_menu = self._top_menus["file"]
        self._file_menu_actions = {
            "new_project": file_menu.addAction(tr("new_project"), self.new_project),
            "save_sfig": file_menu.addAction(tr("save_sfig"), self.save_project),
            "open_sfig": file_menu.addAction(tr("open_sfig"), self.open_project),
        }
        language_menu = self._top_menus["language"]
        language_menu.addAction("English", lambda: self.switch_language("en"))
        language_menu.addAction("中文", lambda: self.switch_language("zh"))

        self.imagej_status_label = bind_i18n(QLabel(), "imagej_disconnected")
        self.imagej_status_label.setObjectName("DimLabel")
        menu.setCornerWidget(self.imagej_status_label, Qt.Corner.TopRightCorner)
        menu.setVisible(False)

    def _retranslate_menu(self) -> None:
        for key, menu in self._top_menus.items():
            menu.setTitle(tr(key))
        for key, action in self._file_menu_actions.items():
            action.setText(tr(key))

    def _build_central_ui(self) -> None:
        self.module_selector = ModuleSelector(self.resources_dir)
        self.module_selector.module_changed.connect(self.set_module)

        center = QWidget()
        center.setObjectName("V9LegacyStageContent")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self.data_panel = DataInputPanel()
        self.composer_workspace = ComposerWorkspace()
        self.composer_canvas = self.composer_workspace.canvas
        self.composer_workspace.import_requested.connect(self.import_external_image_for_composer)
        self.composer_workspace.files_import_requested.connect(self.import_external_images_to_shelf)
        self.composer_workspace.remove_asset_requested.connect(lambda _uid:self.remove_selected_composer_asset())
        self.composer_workspace.focus_changed.connect(self._composer_focus_changed)
        def report_ihc_failure(title: str, context: str, exc: Exception) -> None:
            self._finish_analysis_attempt("ihc", failed=True)
            log_path = log_exception(context, exc)
            QMessageBox.critical(self, title, trf("error_with_traceback", error=exc, path=log_path))

        def report_brightfield_failure(title: str, context: str, exc: Exception) -> None:
            self._finish_analysis_attempt("brightfield", failed=True)
            log_path = log_exception(context, exc)
            QMessageBox.critical(self, title, trf("error_with_traceback", error=exc, path=log_path))

        self.ihc_analysis_controller = IHCAnalysisController(
            get_panel=self._active_ihc_panel,
            get_expert_settings=self.data_panel.ihc_analysis_settings,
            get_assisted_settings=self.data_panel.ihc_guided_analysis_settings,
            get_foolproof_settings=self.data_panel.ihc_foolproof_analysis_settings,
            get_current_mode=self.data_panel.current_ihc_guided_mode,
            get_overlay_opacity=self.data_panel.ihc_overlay_opacity,
            get_current_task_type=self.data_panel.current_ihc_task_type,
            get_examples_for_image=lambda image_uid: list(self._ihc_example_samples_by_image.get(image_uid, [])),
            clear_examples_for_image=lambda image_uid: self._ihc_example_samples_by_image.pop(image_uid, None),
            clear_example_click_mode=lambda: self._set_ihc_example_click_mode(None),
            refresh_measurement_views=self._refresh_ihc_measurement_views,
            notify_user=lambda title, message: QMessageBox.information(self, title, message),
            report_failure=report_ihc_failure,
            run_async=self._analysis_job_service.start,
            apply_panel_snapshot=self._apply_analysis_panel_snapshot,
            capture_analysis_inputs=lambda: self._current_analysis_inputs("ihc"),
        )
        self.brightfield_traditional_analysis_controller = BrightfieldTraditionalAnalysisController(
            get_panel=self._active_brightfield_panel,
            cancel_preview_debounce=self._cancel_brightfield_guided_preview_debounce,
            get_current_mode=lambda: self._normalized_brightfield_mode(self.data_panel.current_brightfield_guided_mode()),
            get_current_stain_type=lambda: self.data_panel.brightfield_stain_type_combo.currentText() or "H&E",
            get_expert_settings=self.data_panel.brightfield_analysis_settings,
            get_assisted_settings=self.data_panel.brightfield_guided_analysis_settings,
            get_foolproof_settings=self.data_panel.brightfield_foolproof_analysis_settings,
            refresh_measurement_views=self._refresh_brightfield_measurement_views,
            notify_user=lambda title, message: QMessageBox.information(self, title, message),
            report_failure=report_brightfield_failure,
            run_async=self._analysis_job_service.start,
            apply_panel_snapshot=self._apply_analysis_panel_snapshot,
            capture_analysis_inputs=lambda: self._current_analysis_inputs("brightfield"),
        )
        self.brightfield_ai_object_count_controller = BrightfieldAIObjectCountController(
            get_panel=self._active_brightfield_panel,
            get_analysis_settings=self.data_panel.brightfield_analysis_settings,
            show_ai_warning=self._show_brightfield_ai_warning,
            refresh_after_ai_measurement=self._refresh_brightfield_after_ai_measurement,
            run_async=self._analysis_job_service.start,
            clone_panel=self._clone_panel_for_worker,
            apply_panel_snapshot=self._apply_analysis_panel_snapshot,
            set_busy=lambda busy: self.data_panel.brightfield_ai_object_count_button.setEnabled(not busy),
            can_start=lambda: not any(self._analysis_job_service.is_busy(key) for key in
                ('brightfield', 'brightfield-cellpose', 'brightfield-fiber', 'import:brightfield')),
        )
        self.data_panel.secondary_button.clicked.connect(self.create_mock_panel)
        self.data_panel.import_button.clicked.connect(self.import_active_module)
        self.data_panel.run_analysis_button.clicked.connect(self.run_qpcr_analysis)
        self.data_panel.copy_summary_button.clicked.connect(self.copy_analysis_summary)
        self.data_panel.ihc_run_analysis_button.clicked.connect(self.run_ihc_analysis)
        self.data_panel.ihc_copy_table_button.clicked.connect(self.copy_ihc_results_table)
        self.data_panel.ihc_stats_run_button.clicked.connect(self.run_ihc_statistics)
        self.data_panel.ihc_copy_stats_button.clicked.connect(self.copy_ihc_statistics_table)
        self.data_panel.ihc_export_stats_csv_button.clicked.connect(self.export_ihc_statistics_csv)
        self.data_panel.ihc_add_outputs_button.clicked.connect(self.add_ihc_outputs_to_composer)
        self.data_panel.ihc_export_overlay_button.clicked.connect(self.export_ihc_overlay_image)
        self.data_panel.ihc_export_results_csv_button.clicked.connect(self.export_ihc_results_csv)
        self.data_panel.ihc_export_summary_json_button.clicked.connect(self.export_ihc_summary_json)
        self.data_panel.ihc_step_changed.connect(self.set_ihc_workflow_step)
        self.data_panel.ihc_image_selected.connect(self.select_ihc_image)
        self.data_panel.ihc_measurement_selected.connect(self.select_ihc_measurement_result)
        self.data_panel.ihc_roi_draw_button.toggled.connect(self.toggle_ihc_roi_selection)
        self.data_panel.ihc_roi_clear_button.clicked.connect(self.clear_ihc_roi_selection)
        self.data_panel.ihc_mark_positive_button.toggled.connect(self.toggle_ihc_positive_example_mode)
        self.data_panel.ihc_mark_negative_button.toggled.connect(self.toggle_ihc_negative_example_mode)
        self.data_panel.ihc_stop_sampling_button.toggled.connect(self.toggle_ihc_sampling_off_mode)
        self.data_panel.ihc_recalibrate_button.clicked.connect(self.recalibrate_ihc_from_examples)
        self.data_panel.ihc_delete_example_button.clicked.connect(self.delete_selected_ihc_example)
        self.data_panel.ihc_revert_auto_button.clicked.connect(self.revert_ihc_to_auto)
        self.data_panel.ihc_overlay_opacity_slider.valueChanged.connect(self.update_ihc_overlay_opacity)
        self.data_panel.ihc_mode_combo.currentIndexChanged.connect(self._handle_ihc_mode_changed)
        self.data_panel.ihc_try_assisted_button.clicked.connect(self.try_ihc_assisted_mode)
        self.data_panel.brightfield_mode_combo.currentIndexChanged.connect(self._handle_brightfield_mode_changed)
        self.data_panel.brightfield_foolproof_sensitivity_combo.currentIndexChanged.connect(
            self._handle_brightfield_foolproof_sensitivity_changed
        )
        self.data_panel.brightfield_assisted_sensitivity_combo.currentIndexChanged.connect(
            self._handle_brightfield_assisted_sensitivity_changed
        )
        self.data_panel.brightfield_assisted_small_region_filter_combo.currentIndexChanged.connect(
            self._handle_brightfield_assisted_small_region_filter_changed
        )
        self.data_panel.brightfield_preview_mask_button.clicked.connect(self.preview_brightfield_segmentation)
        self.data_panel.brightfield_run_analysis_button.clicked.connect(self.run_brightfield_analysis)
        self.data_panel.brightfield_he_fiber_calibrate_button.clicked.connect(self.calibrate_brightfield_he_fiber_area)
        self.data_panel.brightfield_he_fiber_draw_scale_line_button.clicked.connect(self.start_brightfield_measurement_scale_line)
        self.data_panel.brightfield_he_fiber_apply_scale_button.clicked.connect(self.apply_brightfield_measurement_scale)
        self.data_panel.brightfield_calibration_controls.calibration_applied.connect(self._apply_brightfield_calibration_preset)
        self.data_panel.brightfield_he_fiber_refresh_results_button.clicked.connect(self.refresh_brightfield_measurement_scale_results)
        self.data_panel.brightfield_he_fiber_reset_scale_button.clicked.connect(self.reset_brightfield_measurement_scale)
        self.data_panel.brightfield_he_fiber_splitting_slider.valueChanged.connect(self._persist_brightfield_he_fiber_settings)
        self.data_panel.brightfield_he_fiber_foreground_combo.currentIndexChanged.connect(self._persist_brightfield_he_fiber_settings)
        self.data_panel.brightfield_he_fiber_method_combo.currentIndexChanged.connect(self._persist_brightfield_he_fiber_settings)
        self.data_panel.brightfield_he_fiber_cellpose_diameter_spin.valueChanged.connect(self._persist_brightfield_he_fiber_settings)
        self.data_panel.brightfield_bright_round_cell_measure_button.clicked.connect(self.run_bright_round_cell_diameter)
        self.data_panel.brightfield_measure_type_combo.currentIndexChanged.connect(self.refresh_brightfield_measurement_type)
        self.data_panel.brightfield_ai_object_count_button.clicked.connect(self.run_brightfield_ai_object_count)
        self.data_panel.brightfield_copy_table_button.clicked.connect(self.copy_brightfield_results_table)
        self.data_panel.brightfield_copy_ai_results_button.clicked.connect(self.copy_brightfield_ai_results_table)
        self.data_panel.brightfield_stats_run_button.clicked.connect(self.run_brightfield_statistics)
        self.data_panel.brightfield_pairing_button.clicked.connect(lambda: self._edit_statistics_pairs('brightfield'))
        self.data_panel.ihc_pairing_button.clicked.connect(lambda: self._edit_statistics_pairs('ihc'))
        self.data_panel.wb_pairing_button.clicked.connect(lambda: self._edit_statistics_pairs('wb'))
        from core.measurement_provenance import measurement_provenance_rows
        self.data_panel.ihc_measurement_excel_button.provenance_provider = lambda: measurement_provenance_rows(self._active_ihc_panel())
        self.data_panel.brightfield_measurement_excel_button.provenance_provider = lambda: measurement_provenance_rows(self._active_brightfield_panel())
        self.data_panel.brightfield_copy_stats_button.clicked.connect(self.copy_brightfield_statistics_table)
        self.data_panel.brightfield_copy_ai_stats_button.clicked.connect(self.copy_brightfield_ai_statistics_table)
        self.data_panel.brightfield_export_stats_csv_button.clicked.connect(self.export_brightfield_statistics_csv)
        self.data_panel.brightfield_export_ai_stats_csv_button.clicked.connect(self.export_brightfield_ai_statistics_csv)
        self.data_panel.brightfield_export_chart_png_button.clicked.connect(self.export_brightfield_current_chart)
        self.data_panel.brightfield_stats_plot_source_combo.currentIndexChanged.connect(self.refresh_brightfield_stats_plot_source)
        self.data_panel.brightfield_stats_chart_mode_combo.currentIndexChanged.connect(self.refresh_brightfield_stats_chart)
        self.data_panel.brightfield_stats_frequency_mode_combo.currentIndexChanged.connect(self.refresh_brightfield_stats_chart)
        self.data_panel.brightfield_add_outputs_button.clicked.connect(self.add_brightfield_outputs_to_composer)
        self.data_panel.brightfield_apply_metadata_button.clicked.connect(self.update_brightfield_image_metadata)
        self.data_panel.brightfield_lock_batch_threshold_button.clicked.connect(self.lock_brightfield_batch_threshold)
        self.data_panel.brightfield_unlock_batch_threshold_button.clicked.connect(self.unlock_brightfield_batch_threshold)
        self.data_panel.brightfield_step_changed.connect(self.set_brightfield_workflow_step)
        self.data_panel.brightfield_image_selected.connect(self.select_brightfield_image)
        self.data_panel.brightfield_measurement_selected.connect(self.select_brightfield_measurement_result)
        self.data_panel.brightfield_ai_measurement_selected.connect(self.select_brightfield_ai_measurement_result)
        self.data_panel.brightfield_roi_draw_button.toggled.connect(self.toggle_brightfield_roi_selection)
        self.data_panel.brightfield_roi_clear_button.clicked.connect(self.clear_brightfield_roi_selection)
        self.data_panel.qpcr_to_composer_button.clicked.connect(self.send_qpcr_plots_to_composer)
        self.data_panel.qpcr_preview_button.clicked.connect(self.show_qpcr_preview)
        self.data_panel.qpcr_layout_button.clicked.connect(self.show_qpcr_layout)
        self.data_panel.qpcr_add_text_button.clicked.connect(self.add_qpcr_layout_text)
        self.data_panel.qpcr_add_shape_button.clicked.connect(self.add_qpcr_layout_shape)
        self.data_panel.qpcr_same_size_button.clicked.connect(lambda: self.qpcr_layout_canvas.make_selected_same_size())
        self.data_panel.qpcr_align_row_button.clicked.connect(lambda: self.qpcr_layout_canvas.align_selected("row"))
        self.data_panel.qpcr_align_column_button.clicked.connect(lambda: self.qpcr_layout_canvas.align_selected("column"))
        self.data_panel.qpcr_distribute_h_button.clicked.connect(lambda: self.qpcr_layout_canvas.distribute_selected("horizontal"))
        self.data_panel.qpcr_distribute_v_button.clicked.connect(lambda: self.qpcr_layout_canvas.distribute_selected("vertical"))
        self.data_panel.qpcr_delete_selected_button.clicked.connect(lambda: self.qpcr_layout_canvas.delete_selected())
        self.data_panel.qpcr_step_changed.connect(self.set_qpcr_workflow_step)
        self.data_panel.journal_preset_changed.connect(self.set_active_module_journal_preset)
        self.data_panel.wb_import_strip_button.clicked.connect(self.import_active_module)
        self.data_panel.wb_strip_table.files_dropped.connect(self.import_wb_images)
        self.data_panel.wb_step_changed.connect(self.set_wb_workflow_step)
        self.data_panel.wb_export_button.clicked.connect(self.export_active_figure)
        self.data_panel.files_dropped.connect(self.handle_external_files_dropped)
        self.data_panel.wb_apply_crop_button.clicked.connect(self.apply_wb_crop)
        self.data_panel.wb_roi_mode_button.clicked.connect(self.enable_wb_roi_mode)
        self.data_panel.wb_full_blot_button.clicked.connect(self.show_wb_full_blot)
        self.data_panel.wb_move_image_button.clicked.connect(self.enable_wb_move_mode)
        self.data_panel.wb_box_select_text_button.clicked.connect(self.enable_wb_text_box_select_mode)
        self.data_panel.wb_lane_anchor_button.clicked.connect(self.enable_wb_lane_anchor_mode)
        self.data_panel.wb_auto_lane_button.clicked.connect(self.auto_detect_wb_lanes)
        self.data_panel.wb_marker_anchor_button.clicked.connect(self.enable_wb_marker_anchor_mode)
        self.data_panel.wb_clear_anchors_button.clicked.connect(self.clear_wb_anchors)
        self.data_panel.wb_flip_h_button.clicked.connect(self.flip_wb_horizontal)
        self.data_panel.wb_flip_v_button.clicked.connect(self.flip_wb_vertical)
        self.data_panel.wb_preview_layout_button.clicked.connect(lambda: self.apply_wb_nature_layout(preview=True))
        self.data_panel.wb_apply_layout_button.clicked.connect(self.apply_wb_layout_selection)
        self.data_panel.wb_density_measure_button.clicked.connect(self.run_wb_densitometry)
        self.data_panel.wb_density_copy_button.clicked.connect(self.copy_wb_densitometry_table)
        self.data_panel.wb_stats_run_button.clicked.connect(self.run_wb_statistics)
        self.data_panel.wb_stats_add_to_layout_button.clicked.connect(self.add_wb_statistics_plot_to_layout)
        self.data_panel.wb_stats_apply_style_button.clicked.connect(self.apply_wb_stats_plot_style)
        self.data_panel.wb_export_to_composer_button.clicked.connect(self.export_selected_wb_items_to_composer)
        self.data_panel.wb_reset_button.clicked.connect(self.reset_wb_to_original)
        self.data_panel.wb_undo_button.clicked.connect(self.undo_wb)
        self.data_panel.wb_redo_button.clicked.connect(self.redo_wb)
        self.data_panel.wb_add_band_row_button.clicked.connect(self.data_panel.add_wb_band_config_row)
        self.data_panel.wb_add_group_level_button.clicked.connect(self.data_panel.show_wb_group_level_editor)
        self.data_panel.wb_add_condition_row_button.clicked.connect(self.data_panel.show_wb_condition_row_editor)
        self.data_panel.wb_strip_table.itemSelectionChanged.connect(self.select_wb_strip_from_table)
        self.data_panel.wb_strip_table.itemChanged.connect(self.update_wb_strip_from_table)
        self.data_panel.wb_strip_table.strip_action_requested.connect(self.handle_wb_strip_action)
        self.data_panel.wb_strip_up_button.clicked.connect(lambda: self.move_wb_strip(-1))
        self.data_panel.wb_strip_down_button.clicked.connect(lambda: self.move_wb_strip(1))
        self.data_panel.wb_strip_delete_button.clicked.connect(self.delete_wb_strip)
        self.data_panel.wb_text_size_spin.valueChanged.connect(lambda value: self.apply_wb_selected_text_style({"font_size_pt": float(value)}))
        self.data_panel.wb_text_bold_button.clicked.connect(lambda checked: self.apply_wb_selected_text_style({"bold": bool(checked)}))
        self.data_panel.wb_text_italic_button.clicked.connect(lambda checked: self.apply_wb_selected_text_style({"italic": bool(checked)}))
        self.data_panel.wb_text_color_combo.currentTextChanged.connect(
            lambda _value: self.apply_wb_selected_text_style({"color": self.data_panel.wb_color_combo_value(self.data_panel.wb_text_color_combo)})
        )
        self.data_panel.wb_text_rotation_spin.valueChanged.connect(lambda value: self.apply_wb_selected_text_style({"rotation": float(value)}))
        self.data_panel.wb_text_x_spin.valueChanged.connect(lambda value: self.move_wb_selected_text(x=float(value)))
        self.data_panel.wb_text_y_spin.valueChanged.connect(lambda value: self.move_wb_selected_text(y=float(value)))
        self.data_panel.wb_apply_text_type_button.clicked.connect(self.apply_wb_selected_text_style_to_type)
        self.data_panel.wb_line_width_spin.valueChanged.connect(lambda value: self.live_wb_selected_line_change(width=float(value)))
        self.data_panel.wb_line_color_combo.currentTextChanged.connect(
            lambda _value: self.live_wb_selected_line_change(color=self.data_panel.wb_color_combo_value(self.data_panel.wb_line_color_combo))
        )
        self.data_panel.wb_line_x_spin.valueChanged.connect(lambda value: self.move_wb_selected_line(x=float(value)))
        self.data_panel.wb_line_y_spin.valueChanged.connect(lambda value: self.move_wb_selected_line(y=float(value)))
        self.data_panel.wb_apply_line_style_button.clicked.connect(self.apply_wb_selected_line_style)
        self.data_panel.wb_stats_font_size_spin.valueChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_plot_width_spin.valueChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_plot_height_spin.valueChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_bar_fill_combo.currentTextChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_bar_edge_combo.currentTextChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_bar_width_spin.valueChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_bar_line_width_spin.valueChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_axis_line_width_spin.valueChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_point_shape_combo.currentTextChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_point_size_spin.valueChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_stats_point_color_combo.currentTextChanged.connect(lambda _value: self.preview_wb_stats_plot_style_live())
        self.data_panel.wb_rotation_spin.valueChanged.connect(self.update_wb_transform)
        self.data_panel.wb_fine_rotation_slider.valueChanged.connect(self.update_wb_transform)
        self.data_panel.wb_brightness_spin.valueChanged.connect(self.update_wb_transform)
        self.data_panel.wb_contrast_spin.valueChanged.connect(self.update_wb_transform)
        self.preview_canvas = PreviewCanvas()
        self.preview_canvas.files_dropped.connect(self.handle_external_files_dropped)
        self.qpcr_preview_workspace = QWidget()
        qpcr_preview_layout = QHBoxLayout(self.qpcr_preview_workspace)
        qpcr_preview_layout.setContentsMargins(0, 0, 0, 0)
        qpcr_preview_layout.setSpacing(0)
        qpcr_preview_layout.addWidget(self.preview_canvas, 1)
        self.qpcr_layout_workspace = QPCRLayoutWorkspace()
        self.qpcr_layout_canvas = self.qpcr_layout_workspace.canvas
        self.qpcr_layout_canvas.scene.selectionChanged.connect(self._sync_chart_kind_controls)
        self.qpcr_layout_workspace.preview_requested.connect(self.show_qpcr_preview)
        self.qpcr_layout_workspace.add_text_requested.connect(self.add_qpcr_layout_text)
        self.qpcr_layout_workspace.add_shape_requested.connect(self.add_qpcr_layout_shape)
        self.qpcr_layout_workspace.same_size_requested.connect(self.qpcr_layout_canvas.make_selected_same_size)
        self.qpcr_layout_workspace.align_row_requested.connect(lambda: self.qpcr_layout_canvas.align_selected("row"))
        self.qpcr_layout_workspace.align_column_requested.connect(lambda: self.qpcr_layout_canvas.align_selected("column"))
        self.qpcr_layout_workspace.distribute_h_requested.connect(lambda: self.qpcr_layout_canvas.distribute_selected("horizontal"))
        self.qpcr_layout_workspace.distribute_v_requested.connect(lambda: self.qpcr_layout_canvas.distribute_selected("vertical"))
        self.qpcr_layout_workspace.delete_requested.connect(self.qpcr_layout_canvas.delete_selected)
        self.qpcr_layout_workspace.to_composer_requested.connect(self.send_qpcr_plots_to_composer)
        self.data_panel.set_qpcr_layout_tools_panel(self.qpcr_layout_workspace.take_tools_panel())
        self.qpcr_workflow_panel = self._build_qpcr_workflow_panel()
        self.wb_canvas = WBCanvas()
        from ui.widgets.canvas_view_controller import CanvasFontAction
        CanvasFontAction(self.data_panel.wb_text_font_combo.currentFontChanged, self.wb_canvas,
                         lambda font: self.apply_wb_selected_text_style({"font_family": font.family()}))
        CanvasFontAction(self.data_panel.wb_stats_font_combo.currentFontChanged, self.wb_canvas,
                         lambda _font: self.preview_wb_stats_plot_style_live())
        self.wb_canvas.scene.selectionChanged.connect(self._sync_chart_kind_controls)
        self.wb_canvas.files_dropped.connect(self.handle_external_files_dropped)
        self.wb_canvas.roi_selected.connect(self.set_wb_roi_from_canvas)
        self.wb_canvas.lane_anchor_selected.connect(self.set_wb_lane_anchor_from_canvas)
        self.wb_canvas.marker_anchor_selected.connect(self.set_wb_marker_anchor_from_canvas)
        self.wb_canvas.crop_rect_changed.connect(self.update_wb_crop_from_canvas)
        self.wb_canvas.crop_deleted.connect(self.delete_wb_crop_from_canvas)
        self.wb_canvas.crop_reordered.connect(self.reorder_wb_crop_from_canvas)
        self.wb_canvas.strip_dropped.connect(self.place_wb_strip_from_staging)
        self.wb_canvas.layout_text_changed.connect(self.update_wb_layout_text_from_canvas)
        self.wb_canvas.layout_item_moved.connect(self.update_wb_layout_item_position)
        self.wb_canvas.layout_style_changed.connect(self.update_wb_layout_text_style)
        self.wb_canvas.layout_line_moved.connect(self.update_wb_layout_line_position)
        self.wb_canvas.undo_requested.connect(self.undo_wb)
        self.wb_canvas.redo_requested.connect(self.redo_wb)
        self.wb_canvas.align_row_requested.connect(self.align_wb_selected_row)
        self.wb_canvas.align_column_requested.connect(self.align_wb_selected_column)
        self.wb_canvas.sync_lines_requested.connect(self.sync_wb_selected_line_level)
        self.wb_canvas.recenter_labels_requested.connect(self.recenter_wb_group_labels)
        self.wb_canvas.scene.selectionChanged.connect(self.sync_wb_text_toolbar_from_selection)
        self.wb_canvas_toolbar_panel = self.wb_canvas.take_toolbar_widget()
        self.composer_canvas.preflight_requested.connect(self.preflight_composer_export)
        self.composer_canvas.export_requested.connect(self.export_active_figure)
        self.composer_canvas.scene.selectionChanged.connect(self._sync_chart_kind_controls)
        self.composer_canvas.document_changed.connect(self._update_composer_summary)
        self.composer_canvas.pages_changed.connect(self._update_composer_summary)
        self.fluoro_workspace = FluorescenceWorkspace()
        self.fluoro_workspace.set_analysis_runner(self._start_fluorescence_analysis)
        self.fluoro_workspace.files_dropped.connect(self.import_fluorescence_images)
        self.fluoro_workspace.canvas.scene.selectionChanged.connect(self._sync_chart_kind_controls)
        self.fluoro_workspace.context_stack.currentChanged.connect(lambda _index: self._sync_chart_kind_controls())
        self.fluoro_workspace.import_button.clicked.connect(self.import_active_module)
        self.fluoro_workspace.remove_button.clicked.connect(self.remove_selected_fluorescence_image)
        self.fluoro_workspace.merge_selected_requested.connect(self.merge_selected_fluorescence_images)
        self.fluoro_workspace.save_active_image_requested.connect(self.save_active_fluorescence_image)
        self.fluoro_workspace.force_split_rgb_requested.connect(self.toggle_active_fluorescence_rgb_split)
        self.fluoro_workspace.export_requested.connect(self.export_active_figure)
        self.fluoro_workspace.export_overlay_requested.connect(self.export_fluorescence_overlay_image)
        self.fluoro_workspace.layout_to_composer_requested.connect(self.send_fluorescence_layout_to_composer)
        self.ihc_workspace = IHCWorkspace(self.data_panel.take_ihc_context_panel())
        self.ihc_workspace.files_dropped.connect(self.import_ihc_images)
        self.ihc_workspace.import_requested.connect(self.import_active_module)
        self.ihc_workspace.remove_requested.connect(self.remove_selected_ihc_image)
        self.ihc_workspace.canvas.roi_selected.connect(self.set_ihc_roi_from_canvas)
        self.ihc_workspace.point_selected.connect(self.apply_ihc_example_click)
        self.ihc_workspace.layout_requested.connect(self.show_ihc_layout)
        self.ihc_workspace.layout_to_composer_requested.connect(self.send_ihc_layout_to_composer)
        self.ihc_workspace.layout_canvas.scene.selectionChanged.connect(self._sync_chart_kind_controls)
        self.brightfield_workspace = BrightfieldWorkspace(self.data_panel.take_brightfield_context_panel())
        self.brightfield_workspace.files_dropped.connect(self.import_brightfield_images)
        self.brightfield_workspace.import_requested.connect(self.import_active_module)
        self.brightfield_workspace.remove_requested.connect(self.remove_selected_brightfield_image)
        self.brightfield_workspace.canvas.roi_selected.connect(self.set_brightfield_roi_from_canvas)
        self.brightfield_workspace.canvas.line_selected.connect(self.set_brightfield_measurement_scale_line)
        self.brightfield_workspace.canvas.line_dragged.connect(self.preview_brightfield_measurement_scale_line)
        self.brightfield_workspace.layout_requested.connect(self.show_brightfield_layout)
        self.brightfield_workspace.layout_to_composer_requested.connect(self.send_brightfield_layout_to_composer)
        self.brightfield_workspace.layout_canvas.scene.selectionChanged.connect(self._sync_chart_kind_controls)
        self.brightfield_workspace.export_overlay_requested.connect(self.export_brightfield_overlay_image)
        self.image_browser_workspace = ImageBrowserWorkspace(self)
        self.image_browser_workspace.hide()
        self.image_browser_workspace.status_changed.connect(lambda text: self.statusBar().showMessage(text, 4000))
        self.image_browser_workspace.export_failed.connect(lambda text: self.statusBar().showMessage(text, 4000))
        self.image_browser_workspace.send_to_fluorescence_transfer_requested.connect(self.import_browser_fluorescence_transfer)
        from ui.controllers.browser_histology_transfer_controller import BrowserHistologyTransferController
        self.browser_histology_transfer = BrowserHistologyTransferController(self)
        from ui.controllers.nuclei_count_controller import NucleiCountController
        self.nuclei_controllers = {module: NucleiCountController(self, module) for module in ('fluoro', 'brightfield')}
        self.central_nuclei_controller = NucleiCountController(self,'brightfield',task='muscle')
        from ui.controllers.myotube_controller import MyotubeController
        self.myotube_controllers = {module: MyotubeController(self, module) for module in ('fluoro', 'brightfield')}
        self.image_browser_workspace.browser_state_changed.connect(self._on_browser_workspace_state_changed)
        from ui.controllers.microscopy_workflow_controller import MicroscopyWorkflowController
        from ui.controllers.analysis_state_controller import AnalysisStateController
        from ui.controllers.project_recovery_controller import ProjectRecoveryController
        self.microscopy_workflow = MicroscopyWorkflowController(self)
        self.analysis_state = AnalysisStateController(self)
        self.project_recovery = ProjectRecoveryController(self)
        self._connect_freshness_setting_watchers()
        self.wb_workspace = QWidget()
        wb_workspace_layout = QHBoxLayout(self.wb_workspace)
        wb_workspace_layout.setContentsMargins(0, 0, 0, 0)
        wb_workspace_layout.setSpacing(0)
        wb_workspace_layout.addWidget(self.wb_canvas, 1)
        self.wb_workflow_panel = self._build_wb_workflow_panel()

        self.canvas_stack = CurrentPageStackedWidget()
        self.canvas_stack.addWidget(self.qpcr_preview_workspace)
        self.canvas_stack.addWidget(self.qpcr_layout_workspace)
        self.canvas_stack.addWidget(self.wb_workspace)
        self.canvas_stack.addWidget(self.composer_workspace)
        self.canvas_stack.addWidget(self.fluoro_workspace)
        self.canvas_stack.addWidget(self.ihc_workspace)
        self.canvas_stack.addWidget(self.brightfield_workspace)
        self.canvas_stack.addWidget(self.image_browser_workspace.canvas)
        self.stylelab_workspace = StyleLabWorkspace(render_initial_preview=False)
        from ui.controllers.layout_edit_controller import LayoutEditController
        self.layout_edit_controller=LayoutEditController(self)
        self.stylelab_workspace.runtime_saved_preset_activated.connect(self.apply_style_lab_runtime_saved_preset)
        self.canvas_stack.addWidget(self.stylelab_workspace)
        center_layout.addWidget(self.canvas_stack, 1)

        self.style_sidebar = StyleSidebar()

        self.v9_shell = V9AppShell(
            module_selector=self.module_selector,
            stage_widget=center,
            right_widget=self.style_sidebar,
            actions={
                "new": lambda: self.new_project(),
                "open": lambda: self.open_project(),
                "save": lambda: self.save_project(),
                "save_as": lambda: self.save_project_as(),
                "export": lambda: self.export_active_module(),
            },
            preset_names=journal_preset_names(),
            on_preset_changed=lambda preset: self.set_active_module_journal_preset(preset),
        )
        self.v9_shell.right_layout.insertWidget(1, self.chart_kind_controls)
        self.setCentralWidget(self.v9_shell)




    def _build_wb_workflow_panel(self) -> QWidget:
        panel, content_layout = self._build_workflow_right_panel("WBWorkflowRightPanel", "WBWorkflowRightScroll")

        self.data_panel.wb_staging_panel.setMinimumWidth(0)
        self.data_panel.wb_staging_panel.setMaximumWidth(16777215)
        self.data_panel.wb_staging_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.data_panel.wb_context_panel.setMinimumWidth(0)
        self.data_panel.wb_context_panel.setMaximumWidth(16777215)
        self.data_panel.wb_context_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        step_group = self._right_workbench_group("workflow_steps")
        step_group.layout().addWidget(self.data_panel.wb_step_nav)
        step_group.layout().addWidget(self.data_panel.wb_step_hint)
        content_layout.addWidget(step_group, 0)

        canvas_tools_group = self._right_workbench_group("canvas_tools")
        canvas_tools_group.layout().addWidget(self.wb_canvas_toolbar_panel)
        canvas_tools_group.layout().addWidget(self.data_panel.wb_reset_button)
        self.wb_canvas_tools_group = canvas_tools_group
        content_layout.addWidget(canvas_tools_group, 0)

        staging_group = self._right_workbench_group("strip_staging")
        self.data_panel.wb_strip_label.setVisible(False)
        staging_group.layout().addWidget(self.data_panel.wb_staging_panel)
        self.wb_staging_group = staging_group
        content_layout.addWidget(staging_group, 0)

        workflow_group = self._right_workbench_group("workflow")
        workflow_group.layout().addWidget(self.data_panel.wb_context_panel, 1)
        self.wb_workflow_group = workflow_group
        content_layout.addWidget(workflow_group, 1)
        content_layout.addStretch(1)
        return panel

    def _right_workbench_group(self, title_key: str) -> QFrame:
        group=QFrame()
        group.setObjectName('V9RightWorkbenchGroup')
        layout=QVBoxLayout(group)
        layout.setContentsMargins(10,8,10,10)
        layout.setSpacing(8)
        label=bind_i18n(QLabel(),title_key)
        label.setObjectName('SectionLabel')
        layout.addWidget(label)
        return group

    def _build_qpcr_workflow_panel(self) -> QWidget:
        panel, content_layout = self._build_workflow_right_panel("QPCRWorkflowRightPanel", "QPCRWorkflowRightScroll")

        self.data_panel.qpcr_context_panel.setMinimumWidth(0)
        self.data_panel.qpcr_context_panel.setMaximumWidth(16777215)
        self.data_panel.qpcr_context_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        step_group = self._right_workbench_group("workflow_steps")
        step_group.layout().addWidget(self.data_panel.qpcr_step_nav)
        content_layout.addWidget(step_group, 0)

        canvas_tools_group = self._right_workbench_group("canvas_tools")
        if self.data_panel.qpcr_layout_tools_panel is not None:
            canvas_tools_group.layout().addWidget(self.data_panel.qpcr_layout_tools_panel)
        self.qpcr_canvas_tools_group = canvas_tools_group
        canvas_tools_group.setVisible(False)
        content_layout.addWidget(canvas_tools_group, 0)

        workflow_group = self._right_workbench_group("workflow")
        workflow_group.layout().addWidget(self.data_panel.qpcr_context_panel, 1)
        self.qpcr_workflow_group = workflow_group
        content_layout.addWidget(workflow_group, 1)
        content_layout.addStretch(1)
        return panel

    def _build_workflow_right_panel(self, panel_object_name: str, scroll_object_name: str) -> tuple[QWidget, QVBoxLayout]:
        panel = QWidget()
        panel.setObjectName(panel_object_name)
        root = QVBoxLayout(panel)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        scroll = QScrollArea()
        scroll.setObjectName(scroll_object_name)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        return panel, content_layout





    def _build_status_bar(self) -> None:
        status = QStatusBar()
        self.status_label = QLabel()
        self.status_label.setObjectName("DimLabel")
        # A long analysis summary must not increase the window's minimum width
        # after a module switch and refit every canvas a second time.
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(QLabel("600 DPI"))
        status.addPermanentWidget(QLabel("RGB"))
        self.nature_preset_status_label = bind_i18n(QLabel(), "nature_preset")
        status.addPermanentWidget(self.nature_preset_status_label)
        status.addPermanentWidget(QLabel("PDF/SVG/TIFF/PNG"))
        self.language_zh_button = QPushButton("中文")
        self.language_zh_button.setObjectName("V9LanguageButtonZh")
        self.language_zh_button.setCheckable(True)
        self.language_zh_button.clicked.connect(lambda _checked=False: self._switch_language_from_status("zh"))
        status.addPermanentWidget(self.language_zh_button)
        self.language_en_button = QPushButton("EN")
        self.language_en_button.setObjectName("V9LanguageButtonEn")
        self.language_en_button.setCheckable(True)
        self.language_en_button.clicked.connect(lambda _checked=False: self._switch_language_from_status("en"))
        status.addPermanentWidget(self.language_en_button)
        self.setStatusBar(status)
        self.composer_workspace.status_changed.connect(
            lambda text: status.showMessage(text,5000) if self.active_module=='composer' else None)
        self._set_language_button_state(get_language())

    def _switch_language_from_status(self, language: str) -> None:
        self.switch_language(language)
        self._set_language_button_state(language)

    def _set_language_button_state(self, language: str) -> None:
        if not hasattr(self, "language_en_button") or not hasattr(self, "language_zh_button"):
            return
        self.language_en_button.setChecked(language == "en")
        self.language_zh_button.setChecked(language == "zh")

    def _build_shortcuts(self) -> None:
        self._file_shortcuts = [
            QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save_project),
        ]
        self._edit_shortcuts=[
            QShortcut(QKeySequence.StandardKey.Undo,self,activated=lambda:self._undo_active_editor(False)),
            QShortcut(QKeySequence.StandardKey.Redo,self,activated=lambda:self._undo_active_editor(True))]

    def _undo_active_editor(self,redo=False):
        if self.workflow_ui.scales.positions.undo(redo):
            return
        if self._source_layout_context(self.active_module) is not None:
            self._sync_shared_layout_editor()
            (self.layout_edit_controller.redo if redo else self.layout_edit_controller.undo)()
        elif self.active_module=='wb':
            (self.redo_wb if redo else self.undo_wb)()
        elif self.active_module == 'fluoro':
            self.fluoro_workspace.undo_roi_clear(redo=redo)

    def _handle_analysis_busy_changed(self, key: str, busy: bool) -> None:
        normalized_key = str(key)
        if normalized_key.startswith("import:"):
            self._handle_import_busy_changed(normalized_key, busy)
            return
        if normalized_key.startswith("fluoro-analysis:"):
            self.fluoro_workspace.set_analysis_busy(normalized_key.split(":", 1)[1], busy)
            if busy:
                self.statusBar().showMessage(tr("fluorescence_analysis_running"))
            self._sync_attempt_from_busy(normalized_key, busy)
            self._refresh_result_freshness_status()
            return
        button_by_key = {
            "ihc": self.data_panel.ihc_run_analysis_button,
            "brightfield": self.data_panel.brightfield_run_analysis_button,
        }
        button = button_by_key.get(normalized_key)
        if button is not None:
            if busy:
                self._analysis_button_enabled_before_busy[normalized_key] = button.isEnabled()
                button.setEnabled(False)
                self.statusBar().showMessage(
                    trf("analysis_running", module=tr(f"v9_module_{normalized_key}"))
                )
            else:
                button.setEnabled(self._analysis_button_enabled_before_busy.pop(normalized_key, True))
        self._sync_attempt_from_busy(normalized_key, busy)
        self._refresh_result_freshness_status()

    def _method_for_job_key(self, key: str) -> str | None:
        if key in {"ihc", "brightfield"}:
            return key
        if key == "brightfield-cellpose":
            return "bright_round_cells"
        if key == 'brightfield-fiber':
            return 'brightfield_fiber'
        if key.startswith("fluoro-analysis:"):
            return {
                "count": "fluoro_count",
                "sample-count": "fluoro_count",
                "ai": "fluoro_ai",
                "roi-intensity": "fluoro_roi",
            }.get(key.split(":", 1)[1])
        return None

    def _active_image_uid_for_method(self, method: str) -> str:
        if method.startswith("fluoro"):
            panel = self._active_fluoro_panel()
        elif method == "ihc":
            panel = self._active_ihc_panel()
        else:
            panel = self._active_brightfield_panel()
        image = panel.active_image() if panel is not None else None
        return str(image.get("uid", "")).strip() if image else ""

    def _panel_for_method(self, method: str):
        if method.startswith("fluoro"):
            return self._active_fluoro_panel()
        if method == "ihc":
            return self._active_ihc_panel()
        return self._active_brightfield_panel()

    def _attempt_scope(self, method: str, image_uid: str, panel=None) -> tuple[str, str, str, str]:
        target = panel if panel is not None else self._panel_for_method(method)
        return (
            str(getattr(self.project, "project_uuid", "") or ""),
            str(getattr(target, "panel_uuid", "") or "") if target is not None else "",
            str(image_uid or ""),
            str(method),
        )

    def _begin_analysis_attempt(self, job_key: str, image_uid: str, method: str, inputs: dict[str, Any] | None) -> None:
        scope = self._attempt_scope(method, image_uid)
        attempt = {
            "state": "running",
            "job_key": str(job_key),
            "image_uid": str(image_uid or ""),
            "method": str(method),
            "scope": scope,
            "inputs": deepcopy(inputs) if inputs else None,
        }
        self._analysis_attempts[scope] = attempt
        self._analysis_attempts_by_job[str(job_key)] = attempt

    def _finish_analysis_attempt(
        self,
        job_key: str,
        *,
        success: bool = False,
        failed: bool = False,
        stale: bool = False,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        attempt = self._analysis_attempts_by_job.get(str(job_key))
        if success:
            if attempt and records:
                for record in records:
                    if isinstance(record, dict) and saved_inputs_from_record(record) is None:
                        attach_analysis_inputs(record, attempt.get("inputs"))
            if attempt:
                self._analysis_attempts.pop(attempt.get("scope"), None)
            self._analysis_attempts_by_job.pop(str(job_key), None)
        elif failed:
            if attempt is None:
                method = self._method_for_job_key(str(job_key)) or str(job_key)
                self._begin_analysis_attempt(str(job_key), self._active_image_uid_for_method(method), method, None)
                attempt = self._analysis_attempts_by_job.get(str(job_key))
            if attempt:
                attempt["state"] = "failed"
        elif stale:
            if attempt and attempt.get("state") == "running":
                self._analysis_attempts.pop(attempt.get("scope"), None)
                self._analysis_attempts_by_job.pop(str(job_key), None)
        self._refresh_result_freshness_status()

    def _sync_attempt_from_busy(self, key: str, busy: bool) -> None:
        method = self._method_for_job_key(key)
        if method is None:
            return
        if busy:
            self._begin_analysis_attempt(key, self._active_image_uid_for_method(method), method, self._current_analysis_inputs(method))
            return
        attempt = self._analysis_attempts_by_job.get(key)
        if attempt and attempt.get("state") == "running":
            self._finish_analysis_attempt(key, stale=True)

    def _attempt_state(self, image_uid: str, method: str) -> str | None:
        attempt = self._analysis_attempts.get(self._attempt_scope(method, image_uid))
        if not attempt:
            return None
        return str(attempt.get("state") or "") or None

    def _current_analysis_inputs(self, method: str) -> dict[str, Any] | None:
        if method == "ihc":
            panel = self._active_ihc_panel()
            image = panel.active_image() if panel is not None else None
            image_uid = str(image.get("uid", "")).strip() if image else ""
            mode = self.data_panel.current_ihc_guided_mode()
            if mode == "expert":
                settings = dict(self.data_panel.ihc_analysis_settings())
            elif mode == "assisted":
                settings = dict(self.data_panel.ihc_guided_analysis_settings())
            else:
                settings = dict(self.data_panel.ihc_foolproof_analysis_settings())
            settings["mode"] = mode
            roi = panel.active_roi(image_uid) if panel is not None and image_uid else None
            calibration = panel.measurement_scale_bar(image_uid) if panel is not None and image_uid else None
            return build_analysis_inputs(method="ihc", settings=settings, image_uid=image_uid, roi=roi if isinstance(roi, dict) else None, calibration=calibration if isinstance(calibration, dict) else None)
        if method == "brightfield":
            panel = self._active_brightfield_panel()
            image = panel.active_image() if panel is not None else None
            image_uid = str(image.get("uid", "")).strip() if image else ""
            mode = str(self.data_panel.current_brightfield_guided_mode() or "").strip().lower()
            stain = str(self.data_panel.brightfield_stain_type_combo.currentText() or "H&E")
            if mode == "expert":
                settings = dict(self.data_panel.brightfield_analysis_settings())
            elif mode == "assisted":
                settings = dict(self.data_panel.brightfield_guided_analysis_settings())
            else:
                settings = dict(self.data_panel.brightfield_foolproof_analysis_settings())
            settings["mode"] = mode
            settings["stain_type"] = stain
            roi = panel.active_roi(image_uid) if panel is not None and image_uid else None
            calibration = panel.measurement_scale_bar(image_uid) if panel is not None and image_uid else None
            return build_analysis_inputs(method="brightfield", settings=settings, image_uid=image_uid, roi=roi if isinstance(roi, dict) else None, calibration=calibration if isinstance(calibration, dict) else None)
        if method in {"bright_round_cells", "brightfield-cellpose"}:
            panel = self._active_brightfield_panel()
            image = panel.active_image() if panel is not None else None
            image_uid = str(image.get("uid", "")).strip() if image else ""
            settings = dict(self.data_panel.bright_round_cell_analysis_settings())
            roi = panel.active_roi(image_uid) if panel is not None and image_uid else None
            calibration = panel.measurement_scale_bar(image_uid) if panel is not None and image_uid else None
            return build_analysis_inputs(method="bright_round_cells", settings=settings, image_uid=image_uid, roi=roi if isinstance(roi, dict) else None, calibration=calibration if isinstance(calibration, dict) else None)
        if method == "brightfield_fiber":
            panel = self._active_brightfield_panel()
            image = panel.active_image() if panel is not None else None
            image_uid = str(image.get("uid", "")).strip() if image else ""
            settings = dict(self.data_panel.brightfield_analysis_settings())
            roi = panel.active_roi(image_uid) if panel is not None and image_uid else None
            calibration = panel.measurement_scale_bar(image_uid) if panel is not None and image_uid else None
            return build_analysis_inputs(method="brightfield_fiber", settings=settings, image_uid=image_uid, roi=roi if isinstance(roi, dict) else None, calibration=calibration if isinstance(calibration, dict) else None)
        if method.startswith("fluoro"):
            return self.fluoro_workspace._current_analysis_inputs_for(
                {"fluoro_count": "count", "fluoro_ai": "ai", "fluoro_roi": "roi-intensity"}.get(method, "")
            )
        return None

    def _freshness_record_for_method(self, method: str) -> dict[str, Any] | None:
        if method == "ihc":
            panel = self._active_ihc_panel()
            return self._active_ihc_measurement(panel) if panel is not None else None
        if method == "brightfield":
            panel = self._active_brightfield_panel()
            return self._active_brightfield_measurement(panel) if panel is not None else None
        if method == "bright_round_cells":
            panel = self._active_brightfield_panel()
            if panel is None:
                return None
            measurement = panel.analysis.get("bright_round_cell_diameter")
            image = panel.active_image()
            if isinstance(measurement, dict) and image and record_image_uid(measurement) == str(image.get("uid", "")):
                return measurement
            return None
        if method == "brightfield_fiber":
            panel = self._active_brightfield_panel()
            if panel is None:
                return None
            selected = self.data_panel.selected_brightfield_ai_measurement_uuid()
            rows = list(panel.analysis.get("brightfield_ai_object_count_measurements", []))
            if selected:
                match = next((row for row in rows if str(row.get("measurement_uuid", "")) == selected), None)
                if match:
                    return match
            image = panel.active_image()
            uid = str(image.get("uid", "")).strip() if image else ""
            for row in reversed(rows):
                if record_image_uid(row) == uid:
                    return row
            return None
        if method == "fluoro_count":
            identity = self.fluoro_workspace.selected_result_identity()
            if identity and identity[0] == "count_uuid":
                return self.fluoro_workspace._count_result_by_uuid(identity[1])
            image = self.fluoro_workspace._active_image() if self.fluoro_workspace.panel is not None else None
            rows = list(image.get("object_counts", [])) if image else []
            return rows[-1] if rows else None
        if method == "fluoro_ai":
            identity = self.fluoro_workspace.selected_result_identity()
            if identity and identity[0] == "ai_measurement_uuid":
                return self.fluoro_workspace._ai_result_by_uuid(identity[1])
            image = self.fluoro_workspace._active_image() if self.fluoro_workspace.panel is not None else None
            rows = list(image.get("fluorescence_ai_assisted_measurements", [])) if image else []
            return rows[-1] if rows else None
        if method == "fluoro_roi":
            identity = self.fluoro_workspace.selected_result_identity()
            if identity and identity[0] == "measurement_uuid":
                return self.fluoro_workspace._measurement_by_uuid(identity[1])
            image = self.fluoro_workspace._active_image() if self.fluoro_workspace.panel is not None else None
            rows = [row for row in (image.get("measurements", []) if image else []) if row.get("type") == "roi_intensity"]
            return rows[-1] if rows else None
        return None

    def _set_freshness_label(self, label, method: str) -> None:
        if label is None or not getattr(self, "_freshness_alive", False):
            return
        record = self._freshness_record_for_method(method)
        image_uid = record_image_uid(record) if record else self._active_image_uid_for_method(method)
        status = display_status(
            has_result=isinstance(record, dict),
            saved_inputs=saved_inputs_from_record(record) if record else None,
            current_inputs=self._current_analysis_inputs(method),
            attempt=self._attempt_state(image_uid, method),
        )
        if isinstance(status, tuple):
            text = " · ".join(tr(STATUS_I18N_KEYS[part]) for part in status if part in STATUS_I18N_KEYS)
        else:
            text = tr(STATUS_I18N_KEYS.get(status, STATUS_I18N_KEYS["not_calculated"]))
        try:
            label.setText(text)
        except RuntimeError:
            return

    def _refresh_result_freshness_status(self) -> None:
        if not getattr(self, "_freshness_alive", False):
            return
        data = self.data_panel
        fluoro = self.fluoro_workspace
        self._set_freshness_label(getattr(data, "ihc_freshness_status_label", None), "ihc")
        self._set_freshness_label(getattr(data, "brightfield_freshness_status_label", None), "brightfield")
        self._set_freshness_label(getattr(data, "brightfield_cell_freshness_status_label", None), "bright_round_cells")
        self._set_freshness_label(getattr(data, "brightfield_ai_freshness_status_label", None), "brightfield_fiber")
        self._set_freshness_label(getattr(fluoro, "count_freshness_status_label", None), "fluoro_count")
        self._set_freshness_label(getattr(fluoro, "ai_freshness_status_label", None), "fluoro_ai")
        self._set_freshness_label(getattr(fluoro, "roi_freshness_status_label", None), "fluoro_roi")

    def _watch_freshness(self, widget) -> None:
        if widget is None:
            return
        for signal_name in ("valueChanged", "currentIndexChanged", "toggled", "itemSelectionChanged"):
            signal = getattr(widget, signal_name, None)
            if signal is not None:
                signal.connect(self._refresh_result_freshness_status)

    def _connect_freshness_setting_watchers(self) -> None:
        data, fluoro = self.data_panel, self.fluoro_workspace
        widgets = [
            data.ihc_mode_combo, data.ihc_task_type_combo, data.ihc_sensitivity_combo, data.ihc_cleanup_combo,
            data.ihc_tissue_correction_checkbox, data.ihc_positive_threshold_spin, data.ihc_weak_threshold_spin,
            data.ihc_moderate_threshold_spin, data.ihc_strong_threshold_spin, data.ihc_min_area_spin,
            data.ihc_active_image_combo, data.ihc_results_table,
            data.brightfield_mode_combo, data.brightfield_stain_type_combo, data.brightfield_sensitivity_combo,
            data.brightfield_cleanup_combo, data.brightfield_foolproof_sensitivity_combo,
            data.brightfield_assisted_sensitivity_combo, data.brightfield_assisted_small_region_filter_combo,
            data.brightfield_segmentation_mode_combo, data.brightfield_h_min_spin, data.brightfield_h_max_spin,
            data.brightfield_s_min_spin, data.brightfield_s_max_spin, data.brightfield_v_min_spin, data.brightfield_v_max_spin,
            data.brightfield_l_min_spin, data.brightfield_l_max_spin, data.brightfield_a_min_spin, data.brightfield_a_max_spin,
            data.brightfield_b_min_spin, data.brightfield_b_max_spin, data.brightfield_gray_threshold_spin,
            data.brightfield_gray_invert_checkbox, data.brightfield_fill_holes_checkbox,
            data.brightfield_cleanup_min_area_spin, data.brightfield_min_object_area_spin, data.brightfield_max_object_area_spin,
            data.brightfield_he_fiber_method_combo, data.brightfield_he_fiber_foreground_combo,
            data.brightfield_he_fiber_cellpose_diameter_spin,
            data.brightfield_he_fiber_splitting_slider, data.brightfield_measure_type_combo,
            data.brightfield_bright_round_cell_threshold_spin, data.brightfield_bright_round_cell_elongated_contrast_threshold_spin,
            data.brightfield_bright_round_cell_min_brightness_spin, data.brightfield_bright_round_cell_min_circularity_spin,
            data.brightfield_bright_round_cell_min_diameter_um_spin, data.brightfield_bright_round_cell_max_diameter_um_spin,
            data.brightfield_cellpose_strategy_combo, data.brightfield_active_image_combo, data.brightfield_results_table,
            data.brightfield_cellpose_diameter_spin, data.brightfield_cellpose_min_size_spin,
            data.brightfield_ai_results_table, data.brightfield_calibration_controls.x_spin,
            data.brightfield_calibration_controls.y_spin,
            fluoro.threshold_method_combo, fluoro.threshold_value_spin, fluoro.count_min_area_spin,
            fluoro.count_algorithm_combo, fluoro.count_diameter_spin, fluoro.count_sensitivity_spin,
            fluoro.count_channel_list, fluoro.ai_sensitivity_spin, fluoro.ai_min_area_spin, fluoro.ai_max_area_spin,
            fluoro.ai_backend_combo, fluoro.ai_cellpose_diameter_spin,
            fluoro.ai_allow_restricted_checkbox, fluoro.ai_channel_list, fluoro.roi_channel_combo,
            fluoro.roi_x, fluoro.roi_y, fluoro.roi_w, fluoro.roi_h, fluoro.count_table, fluoro.ai_results_table,
            fluoro.ai_summary_stats_table, fluoro.measurement_table, fluoro.calibration_controls.x_spin,
            fluoro.calibration_controls.y_spin, fluoro.active_image_combo,
            self.ihc_workspace.calibration_controls.x_spin, self.ihc_workspace.calibration_controls.y_spin,
        ]
        for widget in widgets:
            self._watch_freshness(widget)
        if hasattr(fluoro, "roi_background_method_combo"):
            self._watch_freshness(fluoro.roi_background_method_combo)
        for applied in (
            data.brightfield_calibration_controls.calibration_applied,
            fluoro.calibration_controls.calibration_applied,
            self.ihc_workspace.calibration_controls.calibration_applied,
        ):
            applied.connect(lambda *_args: self._refresh_result_freshness_status())

    def _import_control_buttons(self, module_id: str) -> list[QPushButton]:
        if module_id == "fluoro":
            return [
                self.fluoro_workspace.import_button,
                self.fluoro_workspace.remove_button,
                self.fluoro_workspace.merge_selected_button,
            ]
        if module_id == "ihc":
            return [self.ihc_workspace.import_button, self.ihc_workspace.remove_button]
        if module_id == "brightfield":
            return [self.brightfield_workspace.import_button, self.brightfield_workspace.remove_button]
        return []

    def _handle_import_busy_changed(self, key: str, busy: bool) -> None:
        module_id = key.split(":", 1)[1] if ":" in key else ""
        buttons = self._import_control_buttons(module_id)
        if busy:
            self._import_control_enabled_before_busy[key] = [(button, button.isEnabled()) for button in buttons]
            for button in buttons:
                button.setEnabled(False)
            self.statusBar().showMessage(
                trf("import_running", module=tr(f"v9_module_{module_id}"))
            )
            return

        previous = self._import_control_enabled_before_busy.pop(key, [])
        for button, enabled in previous:
            try:
                button.setEnabled(enabled)
            except RuntimeError:
                continue
        if module_id == "fluoro":
            self.fluoro_workspace._sync_merge_selected_button()

    @staticmethod
    def _apply_analysis_panel_snapshot(live_panel, result_panel) -> None:
        """Apply worker-owned analysis state after the GUI-thread freshness check."""
        live_style = deepcopy(getattr(live_panel, "style", None))
        live_opacity = None
        live_ui_state = None
        if isinstance(getattr(live_panel, "data", None), dict):
            live_opacity = dict(live_panel.data.get("guided_state") or {}).get("overlay_opacity")
            live_ui_state = deepcopy(live_panel.data.get("analysis_ui_state_by_image"))
        live_panel.data = result_panel.data
        live_panel.analysis = result_panel.analysis
        live_panel.audit_log = result_panel.audit_log
        live_panel.modified_at = result_panel.modified_at
        if live_style is not None:
            live_panel.style = live_style
        if live_opacity is not None and isinstance(live_panel.data, dict):
            guided = dict(live_panel.data.get("guided_state") or {})
            guided["overlay_opacity"] = live_opacity
            live_panel.data["guided_state"] = guided
        if live_ui_state is not None and isinstance(live_panel.data, dict):
            live_panel.data["analysis_ui_state_by_image"] = live_ui_state
        if hasattr(result_panel, "_dab_analysis_cache"):
            for measurement_uuid, entry in result_panel._dab_analysis_cache.items():
                live_panel._dab_analysis_cache.pop(measurement_uuid, None)
                live_panel._dab_analysis_cache[measurement_uuid] = entry
            while len(live_panel._dab_analysis_cache) > 16:
                live_panel._dab_analysis_cache.pop(next(iter(live_panel._dab_analysis_cache)))
        if hasattr(result_panel, "_brightfield_analysis_mask_cache"):
            live_panel._merge_brightfield_analysis_mask_cache(result_panel._brightfield_analysis_mask_cache)
        if hasattr(result_panel, "_transient_preview_state"):
            live_panel._transient_preview_state = result_panel._transient_preview_state

    @staticmethod
    def _clone_panel_for_worker(panel):
        snapshot = type(panel).from_dict(deepcopy(panel.to_dict()))
        if hasattr(panel, "IMAGE_CACHE_BYTE_BUDGET"):
            snapshot.IMAGE_CACHE_BYTE_BUDGET = int(panel.IMAGE_CACHE_BYTE_BUDGET)
        return snapshot

    def _start_fluorescence_analysis(
        self,
        *,
        operation: str,
        panel: FluorescencePanel,
        work,
        on_success,
        is_settings_current,
    ) -> bool:
        key = f"fluoro-analysis:{str(operation).strip()}"
        if self._analysis_job_service.is_busy(key):
            return False
        project = self.project
        project_fingerprint = self._microscopy_import_project_fingerprint(project)
        panel_fingerprint = analysis_job_fingerprint(panel)
        try:
            snapshot = self._clone_panel_for_worker(panel)
            snapshot.borrow_readonly_image_data(panel)
        except Exception as exc:
            self._report_fluorescence_analysis_failure(operation, exc)
            return False

        def run_work() -> _FluorescenceAnalysisResult:
            try:
                output = work(snapshot)
            except Exception:
                snapshot.dispose()
                raise
            return _FluorescenceAnalysisResult(panel=snapshot, output=output)

        def is_current() -> bool:
            try:
                return (
                    self.project is project
                    and self._active_fluoro_panel() is panel
                    and self._microscopy_import_project_fingerprint(project) == project_fingerprint
                    and analysis_job_fingerprint(panel) == panel_fingerprint
                    and bool(is_settings_current())
                )
            except Exception:
                return False

        def apply_result(result: _FluorescenceAnalysisResult) -> None:
            result_panel = result.panel
            panel.data = result_panel.data
            panel.analysis = result_panel.analysis
            panel.audit_log = result_panel.audit_log
            panel.modified_at = result_panel.modified_at
            panel.adopt_imported_cache(result_panel, result_panel.worker_owned_cached_image_uids())
            on_success(result.output)
            self.statusBar().showMessage(tr("fluorescence_analysis_complete"), 4000)

        return self._analysis_job_service.start(
            key=key,
            work=run_work,
            on_success=apply_result,
            on_error=lambda exc: self._report_fluorescence_analysis_failure(operation, exc),
            is_current=is_current,
            on_stale=lambda: self.statusBar().showMessage(
                tr("fluorescence_analysis_stale"),
                5000,
            ),
        )

    def _report_fluorescence_analysis_failure(self, operation: str, error: Exception) -> None:
        self._finish_analysis_attempt(f"fluoro-analysis:{str(operation).strip()}", failed=True)
        log_path = log_exception(f"Fluorescence {operation} analysis failed", error)
        QMessageBox.critical(
            self,
            tr("fluorescence_analysis_failed_title"),
            trf("error_with_traceback", error=error, path=log_path),
        )

    @staticmethod
    def _apply_import_panel_snapshot(live_panel, result_panel, imported_uids: list[str]) -> None:
        """Adopt import state after freshness validation, retaining live transient caches."""
        live_panel.data = result_panel.data
        live_panel.analysis = result_panel.analysis
        live_panel.audit_log = result_panel.audit_log
        live_panel.modified_at = result_panel.modified_at
        adopt_cache = getattr(live_panel, "adopt_imported_cache", None)
        if callable(adopt_cache):
            adopt_cache(result_panel, imported_uids)

    def run_ihc_analysis(self) -> None:
        self.ihc_analysis_controller.run_analysis()

    def recalibrate_ihc_from_examples(self) -> None:
        self.ihc_analysis_controller.recalibrate_from_examples()

    def revert_ihc_to_auto(self) -> None:
        self.ihc_analysis_controller.revert_to_auto()

    def run_brightfield_analysis(self) -> None:
        self.brightfield_traditional_analysis_controller.run_analysis()

    def run_bright_round_cell_diameter(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            QMessageBox.information(
                self,
                tr("bright_round_cell_diameter"),
                tr("bright_round_cell_no_image"),
            )
            return
        image_uid = str(active_image.get("uid", "")).strip()
        scale = dict(panel.measurement_scale_bar(image_uid))
        if bool(scale.get("assumed")) or str(scale.get("calibration_source", "")).strip().lower() == "assumed":
            QMessageBox.information(
                self,
                tr("bright_round_cell_diameter"),
                tr("bright_round_cell_scale_required"),
            )
            return
        if float(scale.get("um_per_px", 0.0) or 0.0) <= 0:
            QMessageBox.information(
                self,
                tr("bright_round_cell_diameter"),
                tr("bright_round_cell_scale_required"),
            )
            return

        settings = self.data_panel.bright_round_cell_analysis_settings()
        captured = self._current_analysis_inputs("bright_round_cells")
        self._start_bright_round_cell_measurement(panel, image_uid, settings, captured)

    def _start_bright_round_cell_measurement(self, panel, image_uid, settings, captured_inputs=None) -> None:
        key = 'brightfield-cellpose'
        if self._analysis_job_service.is_busy(key):
            return
        snapshot = self._clone_panel_for_worker(panel)
        project = self.project
        fingerprint = analysis_job_fingerprint(panel)
        button = self.data_panel.brightfield_bright_round_cell_measure_button
        button.setEnabled(False)

        def finish():
            button.setEnabled(True)

        def success(measurement):
            try:
                self._apply_analysis_panel_snapshot(panel, snapshot)
                if isinstance(measurement, dict):
                    attach_analysis_inputs(measurement, captured_inputs)
                self._complete_bright_round_cell_measurement(panel, measurement)
            finally:
                finish()

        def failure(exc):
            finish()
            self._finish_analysis_attempt(key, failed=True)
            log_path = log_exception('Bright round cell measurement failed', exc)
            QMessageBox.critical(self, tr('bright_round_cell_diameter'), trf('error_with_traceback', error=exc, path=log_path))

        started = self._analysis_job_service.start(
            key=key,
            work=lambda: snapshot.measure_bright_round_cells(image_uid=image_uid, settings=settings),
            on_success=success, on_error=failure,
            is_current=lambda: self.project is project and self._active_brightfield_panel() is panel
                and analysis_job_fingerprint(panel) == fingerprint and panel.data.get('active_image_uid') == image_uid
                and self.data_panel.bright_round_cell_analysis_settings() == settings,
            on_stale=finish,
        )
        if not started:
            finish()

    def _complete_bright_round_cell_measurement(self, panel, measurement) -> None:
        self._refresh_brightfield_preview(panel)
        summary = dict(measurement.get("summary", {})) if isinstance(measurement, dict) else {}
        if not summary and isinstance(measurement, dict) and isinstance(measurement.get("analysis"), dict):
            summary = dict(measurement["analysis"].get("summary", {}))
        result_rows = measurement.get("results", []) if isinstance(measurement, dict) else []
        count = int(summary.get("count", len(result_rows) if isinstance(result_rows, list) else 0) or 0)
        self.statusBar().showMessage(trf("bright_round_cell_measurement_complete", count=count), 3000)
        self._refresh_result_freshness_status()

    def refresh_brightfield_measurement_type(self, *_args) -> None:
        if self.active_module != "brightfield":
            return
        if hasattr(self, "analysis_state") and not self.analysis_state._restoring:
            self.analysis_state.capture_outgoing("brightfield")
            self.analysis_state.reload_bar("brightfield")
        panel = self._active_brightfield_panel()
        image = panel.active_image()
        if image is None:
            return
        mode = self.data_panel.brightfield_measure_type_combo.currentData()
        collection = {"area": "brightfield_measurements", "fiber": "brightfield_ai_object_count_measurements"}.get(mode)
        latest = next((m for m in reversed(panel.analysis.get(collection, [])) if m.get("image_uid") == image["uid"]), None) if collection else None
        self._refresh_brightfield_preview(panel, measurement_uuid=latest["measurement_uuid"] if latest else None)

    def calibrate_brightfield_he_fiber_area(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            QMessageBox.warning(self, tr("he_fiber_area"), tr("import_he_before_calibration"))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("calibrate_he_title"))
        dialog.setObjectName("BrightfieldHEFiberCalibrationDialog")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel(tr("calibrate_he_heading"))
        title.setObjectName("SectionLabel")
        body = QLabel(tr("he_calibration_body"))
        body.setWordWrap(True)
        body.setObjectName("DimLabel")
        status = QLabel(f"{tr('active_image')}: {active_image.get('source_name', '')}")
        status.setWordWrap(True)
        run_button = QPushButton(tr("calibrate_active_image"))
        run_button.setObjectName("PrimaryButton")
        close_button = QPushButton(tr("close"))
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addWidget(status)
        layout.addWidget(run_button)
        layout.addWidget(close_button)

        def _run_calibration() -> None:
            try:
                calibration = panel.calibrate_he_fiber_area(str(active_image.get("uid", "")))
                status.setText(
                    trf(
                        "calibrated_examples",
                        positive=int(calibration.get("positive_example_px", 0) or 0),
                        negative=int(calibration.get("negative_example_px", 0) or 0),
                    )
                )
                self._update_brightfield_he_fiber_calibration_status(panel)
                self.statusBar().showMessage(tr("he_fiber_calibrated"), 3000)
            except Exception as exc:
                log_path = log_exception(tr("he_calibration_failed"), exc)
                QMessageBox.critical(
                    self,
                    tr("he_calibration_failed"),
                    trf("error_with_traceback", error=exc, path=log_path),
                )

        run_button.clicked.connect(_run_calibration)
        close_button.clicked.connect(dialog.accept)
        dialog.exec()

    def run_brightfield_ai_object_count(self) -> None:
        self.brightfield_ai_object_count_controller.run_object_count()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_within_screen()
        QTimer.singleShot(0, self._ensure_within_screen)

    def _ensure_within_screen(self) -> None:
        """确保窗口完全在可用屏幕区域内。"""
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        geo = self.geometry()
        frame = self.frameGeometry()
        left_frame = geo.left() - frame.left()
        top_frame = geo.top() - frame.top()
        frame_extra_width = max(0, frame.width() - geo.width())
        frame_extra_height = max(0, frame.height() - geo.height())

        target_geo = QRect(geo)
        max_geo_width = max(1, available.width() - frame_extra_width)
        max_geo_height = max(1, available.height() - frame_extra_height)
        if frame.width() > available.width():
            target_geo.setWidth(max_geo_width)
        if frame.height() > available.height():
            target_geo.setHeight(max_geo_height)

        target_frame = QRect(
            QPoint(target_geo.left() - left_frame, target_geo.top() - top_frame),
            QSize(target_geo.width() + frame_extra_width, target_geo.height() + frame_extra_height),
        )
        if target_frame.bottom() > available.bottom():
            target_frame.moveBottom(available.bottom())
        if target_frame.right() > available.right():
            target_frame.moveRight(available.right())
        if target_frame.top() < available.top():
            target_frame.moveTop(available.top())
        if target_frame.left() < available.left():
            target_frame.moveLeft(available.left())

        target_geo.moveTopLeft(QPoint(target_frame.left() + left_frame, target_frame.top() + top_frame))
        if target_geo != geo:
            self.setGeometry(target_geo)
            self._schedule_active_canvas_layout_refresh()

    def _schedule_active_canvas_layout_refresh(self) -> None:
        QTimer.singleShot(0, self._refresh_active_canvas_layout)
        QTimer.singleShot(80, self._refresh_active_canvas_layout)

    def _refresh_active_canvas_layout(self) -> None:
        if self.active_module == "composer":
            self.composer_canvas.recenter_canvas()
        elif self.active_module == "wb":
            if self.wb_canvas._fit_mode:
                self.wb_canvas.fit_to_canvas()
        elif self.active_module == "fluoro":
            if self.fluoro_workspace.canvas._fit_mode:
                self.fluoro_workspace.canvas.fit_to_canvas()
        elif self.active_module == "ihc":
            if self.ihc_workspace.canvas._fit_mode:
                self.ihc_workspace.canvas.fit_to_canvas()
        elif self.active_module == "viewer":
            self.image_browser_workspace.canvas.schedule_fit_after_layout()
        elif self.active_module == "qpcr" and self.canvas_stack.currentWidget() is self.qpcr_layout_workspace:
            if self.qpcr_layout_canvas._fit_mode:
                self.qpcr_layout_canvas.fit_to_canvas()
        else:
            self.preview_canvas.schedule_fit_after_layout()

    def switch_language(self, language: str) -> None:
        if self.active_module == "stylelab":
            previous_default_status = tr("stylelab_status")
        elif self.active_module == "viewer":
            previous_default_status = tr("viewer_status_ready")
        else:
            previous_default_status = localized_module_placeholder(self.active_module)["status"]
        status_uses_default_copy = self.status_label.text() == previous_default_status
        set_language(language)
        self._retranslate_menu()
        retranslate_bound_widgets(self)
        self.data_panel.retranslate()
        self._refresh_result_freshness_status()
        self.module_selector.retranslate()
        self.wb_canvas.retranslate()
        self.qpcr_layout_workspace.retranslate()
        self.composer_workspace.retranslate()
        self.chart_kind_controls.retranslate()
        self.fluoro_workspace.retranslate()
        self.ihc_workspace.retranslate()
        self.brightfield_workspace.retranslate()
        for controller in self.nuclei_controllers.values():
            controller.retranslate()
        self.central_nuclei_controller.retranslate()
        if hasattr(self,'layout_edit_controller'):
            self.layout_edit_controller.controls.retranslate()
            if self.layout_edit_controller.tools.bar is not None:self.layout_edit_controller.tools.bar.retranslate()
        for controller in self.myotube_controllers.values():
            controller.retranslate()
        if hasattr(self,'measurement_tasks'):
            for deck in self.measurement_tasks.values():deck.retranslate()
        if hasattr(self, "analysis_state"):
            self.analysis_state.retranslate()
        self.image_browser_workspace.retranslate()
        self.stylelab_workspace.retranslate()
        self.style_sidebar.retranslate()
        self.preview_canvas.retranslate()
        if hasattr(self, "v9_shell"):
            self.v9_shell.retranslate()
            self._sync_export_button_for_module(self.active_module)
            right_hide_button = self.v9_shell.findChild(QPushButton, "V9RightPanelHideButton")
            if right_hide_button is not None:
                right_hide_button.setToolTip(tr("v9_hide_workflow"))
            self.v9_shell.set_stage_title(tr(V9_STAGE_TITLE_KEYS.get(self.active_module, self.active_module)))
            self.v9_shell.set_right_title(tr(V9_RIGHT_TITLE_KEYS.get(self.active_module, "tools")))
        if status_uses_default_copy:
            if self.active_module == "stylelab":
                status_text = tr("stylelab_status")
            elif self.active_module == "viewer":
                status_text = tr("viewer_status_ready")
            else:
                status_text = localized_module_placeholder(self.active_module)["status"]
            self.status_label.setText(status_text)
        self._apply_button_readability_policy()
        self._set_language_button_state(language)

    def _v9_stage_tools_for_module(self, module_id: str) -> list[tuple[str, object]]:
        fit = self._refresh_active_canvas_layout
        tools = {
            "qpcr": [
                ("v9_fit", fit),
                ("v9_preview", self.show_qpcr_preview),
                ("layout", self.show_qpcr_layout),
            ],
            "wb": [
                ("v9_fit", fit),
                ("v9_crop", self.enable_wb_roi_mode),
                ("v9_density", self.run_wb_densitometry),
                ("v9_undo", self.undo_wb),
            ],
            "fluoro": [
                ("v9_fit", fit),
                ("v9_channels", lambda: self._set_fluoro_workflow_step(1)),
                ("v9_roi", lambda: self._set_fluoro_workflow_step(2)),
                ("layout", lambda: self._set_fluoro_workflow_step(4)),
            ],
            "ihc": [
                ("v9_fit", fit),
                ("v9_original", self.ihc_workspace.show_original_preview),
                ("v9_overlay", self.ihc_workspace.show_overlay_preview),
                ("layout", self.show_ihc_layout),
            ],
            "brightfield": [
                ("v9_fit", fit),
                ("v9_original", self.brightfield_workspace.show_original_preview),
                ("v9_overlay", self.brightfield_workspace.show_overlay_preview),
                ("layout", self.show_brightfield_layout),
            ],
            "viewer": [],
            "composer": [],
            "stylelab": [
                ("v9_preset", None),
                ("v9_reference", None),
                ("v9_preview", None),
                ("v9_save_draft", None),
            ],
        }.get(module_id, [("v9_fit", fit)])
        if self._source_layout_context(module_id) is not None:
            tools.append(('layout_annotation_tools',self.show_shared_layout_editor))
        return tools

    def _source_layout_context(self,module_id):
        if module_id=='qpcr' and self.canvas_stack.currentWidget() is self.qpcr_layout_workspace:
            return module_id,self._active_qpcr_panel(),self.qpcr_layout_canvas,None
        if module_id=='wb' and self.wb_canvas.panel is not None and self.wb_canvas.panel.data.get('wb_layout',{}).get('enabled') and self.wb_canvas.view.mode=='move':
            return module_id,self.wb_canvas.panel,self.wb_canvas,None
        if module_id=='fluoro' and self.fluoro_workspace.canvas.current_step==4 and self.fluoro_workspace.canvas.panel is not None and self.fluoro_workspace.canvas.panel.active_image() is not None:
            return module_id,self._active_fluoro_panel(),self.fluoro_workspace.canvas,self.fluoro_workspace
        if module_id=='ihc' and self.ihc_workspace.current_step==3 and self.ihc_workspace.layout_canvas.canvas_spec is not None:
            return module_id,self._active_ihc_panel(),self.ihc_workspace.layout_canvas,self.ihc_workspace
        if module_id=='brightfield' and self.brightfield_workspace.current_step==4 and self.brightfield_workspace.layout_canvas.canvas_spec is not None:
            return module_id,self._active_brightfield_panel(),self.brightfield_workspace.layout_canvas,self.brightfield_workspace
        return None

    def _sync_shared_layout_editor(self):
        self._update_composer_summary()
        for shortcut in getattr(self,'_edit_shortcuts',[]):
            shortcut.setEnabled(self.active_module!='composer')
        if not hasattr(self,'layout_edit_controller'):return
        context=self._source_layout_context(self.active_module)
        if context is None:self.layout_edit_controller.set_target()
        else:self.layout_edit_controller.set_target(*context)
        state=(self.active_module,bool(context))
        if hasattr(self,'v9_shell') and state!=getattr(self,'_layout_tools_state',None):
            self._layout_tools_state=state
            self.v9_shell.set_stage_tools(self._v9_stage_tools_for_module(self.active_module))

    def _update_composer_summary(self):
        if self.active_module!='composer' or self.composer_canvas.project is None:return
        figure=self.composer_canvas.current_figure()
        count=sum(ref.visible for ref in figure.panel_refs)
        pages=len(self.project.composer_figures)
        self.status_label.setText(f'排版：{count} 个图像对象 | {pages} 页' if get_language()=='zh' else f'Composer: {count} panels | {pages} pages')
        self.status_label.setToolTip(self.composer_workspace.interaction_hint())

    def show_shared_layout_editor(self):
        self._sync_shared_layout_editor()
        self.layout_edit_controller.show()

    def _expand_right_workflow_widget(self, widget: QWidget) -> None:
        widget.setMinimumHeight(0)
        widget.setMaximumHeight(16777215)
        widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def _sync_export_button_for_module(self, module_id: str | None = None) -> None:
        if not hasattr(self, "v9_shell"):
            return
        button = self.v9_shell._action_buttons.get("export")
        if button is None:
            return
        module_id = module_id or self.active_module
        if module_id == "stylelab" or not menu_entries_for_module(module_id):
            button.setEnabled(False)
            button.setToolTip(tr("export_stylelab_empty"))
            return
        button.setEnabled(True)
        button.setToolTip(tr('export') if sys.platform in {'darwin', 'win32'} else '')

    def _chart_kind_layout_context(self) -> dict[str, Any] | None:
        """Return the one selected chart target for the contextual selector."""
        module = str(self.active_module or "")
        panel = None
        canvas = None
        if module == "qpcr" and self.canvas_stack.currentWidget() is self.qpcr_layout_workspace:
            panel, canvas = self._active_qpcr_panel(), self.qpcr_layout_canvas
        elif module == "wb":
            panel, canvas = self._active_wb_panel(), self.wb_canvas
        elif module == "fluoro" and self.fluoro_workspace.canvas.current_step == 4:
            panel, canvas = self._active_fluoro_panel(), self.fluoro_workspace.canvas
        elif module == "ihc" and self.ihc_workspace.current_step == 3:
            panel, canvas = self._active_ihc_panel(), self.ihc_workspace.layout_canvas
        elif module == "brightfield" and self.brightfield_workspace.current_step == 4:
            panel, canvas = self._active_brightfield_panel(), self.brightfield_workspace.layout_canvas
        if panel is None or canvas is None:
            return None
        items = selected_layout_items(canvas)
        refs = unique_layout_object_refs(items)
        if len(refs) != 1:
            return None
        selection = self.chart_kind_controller.inspect_layout(panel, refs[0])
        if not selection.visible:
            return None
        return {"context": "layout", "panel": panel, "canvas": canvas, "items": items, "selection": selection}

    def _chart_kind_composer_context(self) -> dict[str, Any] | None:
        if self.active_module != "composer":
            return None
        if self.composer_canvas.project is None:
            return None
        figure = self.composer_canvas.current_figure()
        selected = [item for item in self.composer_canvas.scene.selectedItems() if item.data(0) == "panel_ref"]
        if len(selected) != 1:
            return None
        ref_uid = str(selected[0].data(1) or "")
        ref = next((candidate for candidate in figure.panel_refs if candidate.ref_uid == ref_uid), None)
        if ref is None:
            return None
        panel = self.project.get_panel(ref.panel_uid)
        if panel is None:
            return None
        source_context_hash = ""
        source_uuid = str(getattr(panel, "data", {}).get("source_panel_uuid") or "")
        source_panel = self.project.get_panel(source_uuid) if source_uuid else None
        if source_panel is not None:
            source_context_hash = self.composer_canvas._source_panel_context_hash(panel, source_panel)
        selection = self.chart_kind_controller.inspect_composer(
            panel,
            ref,
            project=self.project,
            source_context_hash=source_context_hash,
        )
        if not selection.visible:
            return None
        return {"context": "composer", "panel": panel, "ref": ref, "item": selected[0], "selection": selection}

    def _chart_kind_context(self) -> dict[str, Any] | None:
        return self._chart_kind_composer_context() or self._chart_kind_layout_context()

    def _sync_chart_kind_controls(self) -> None:
        self._sync_shared_layout_editor()
        if not hasattr(self, "chart_kind_controls"):
            return
        context = self._chart_kind_context()
        if context is None:
            self.chart_kind_controls.hide()
            return
        selection = context["selection"]
        if not selection.enabled:
            self.chart_kind_controls.show_disabled(selection.reason or "chart_kind_reason_requires_source")
            return
        self.chart_kind_controls.set_options(
            selection.options,
            selection.chart_kind,
            unavailable=selection.unavailable,
            enabled=True,
            reason=selection.reason,
        )

    def _set_fluoro_workflow_step(self, step_index: int) -> None:
        self.fluoro_workspace.set_step(step_index)
        self._sync_chart_kind_controls()

    def _restore_chart_kind_selection(self, context: dict[str, Any], object_ref: Any) -> None:
        canvas = context["canvas"]
        target = str(getattr(object_ref, "object_id", None) or object_ref or "")
        selected = None
        if context["context"] == "layout":
            if canvas is self.qpcr_layout_canvas:
                uid = target.rsplit(":", 1)[-1]
                selected = next(
                    (item for item in canvas.scene.items() if item.data(0) == "qpcr_layout_plot" and str(item.data(1) or "") == uid),
                    None,
                )
            elif canvas is self.wb_canvas:
                selected = next((item for item in canvas.scene.items() if item.data(0) == "wb_stats_plot"), None)
            elif canvas is self.fluoro_workspace.canvas:
                uid = target.rsplit(":", 1)[-1]
                selected = next(
                    (
                        item
                        for item in canvas.scene.items()
                        if str(item.data(0) or "") == "fluoro_stats_plot" and str(item.data(1) or "") == uid
                    ),
                    None,
                )
            else:
                selected = next(
                    (
                        item
                        for item in canvas.scene.items()
                        if str(getattr(getattr(item, "layout_object", None), "object_id", "")) == target
                    ),
                    None,
                )
        if selected is not None:
            selected.setSelected(True)

    def _rerender_chart_kind_layout(self, context: dict[str, Any], object_ref: Any) -> None:
        canvas = context["canvas"]
        if canvas is self.qpcr_layout_canvas:
            canvas.refresh()
        elif canvas is self.wb_canvas:
            canvas.render()
        elif canvas is self.fluoro_workspace.canvas:
            canvas.refresh()
        elif canvas is self.ihc_workspace.layout_canvas:
            self.ihc_workspace._refresh_layout_after_scale_bar_change()
        elif canvas is self.brightfield_workspace.layout_canvas:
            self.brightfield_workspace._refresh_layout_after_scale_bar_change()
        self._restore_chart_kind_selection(context, object_ref)

    def _on_chart_kind_chosen(self, kind: str) -> None:
        context = self._chart_kind_context()
        if context is None:
            return
        selection = context["selection"]
        if context["context"] == "composer":
            if self.chart_kind_controller.apply_composer_kind(context["panel"], context["ref"], kind):
                self.composer_canvas.reference_changed()
                self.composer_canvas.render(preserve_selection=True)
                self._sync_chart_kind_controls()
            return
        if self.chart_kind_controller.apply_layout_kind(selection.panel, selection.object_ref, kind):
            self._rerender_chart_kind_layout(context, selection.object_ref)
            self._sync_chart_kind_controls()

    def _configure_v9_shell_for_module(self, module_id: str) -> None:
        if not hasattr(self, "v9_shell"):
            return
        if module_id!='composer' or hasattr(self,'_stylelab_previous_right_collapsed'):
            self.v9_shell.set_right_panel_available(True)
        if module_id == 'stylelab':
            if not hasattr(self, "_stylelab_previous_right_collapsed"):
                self._stylelab_previous_right_collapsed = self.v9_shell.is_right_panel_collapsed()
            self.v9_shell.collapse_right_panel()
        elif hasattr(self, "_stylelab_previous_right_collapsed"):
            if not self._stylelab_previous_right_collapsed:
                self.v9_shell.restore_right_panel()
            del self._stylelab_previous_right_collapsed
        self.v9_shell.set_right_panel_available(True)
        self.v9_shell.set_stage_title(tr(V9_STAGE_TITLE_KEYS.get(module_id, module_id)))
        self.v9_shell.set_stage_tools(self._v9_stage_tools_for_module(module_id))
        self._sync_export_button_for_module(module_id)
        self.v9_shell.set_style_preset(self._active_module_journal_preset(module_id))
        right_title = tr(V9_RIGHT_TITLE_KEYS.get(module_id, "tools"))

        if module_id == "qpcr":
            self._expand_right_workflow_widget(self.qpcr_workflow_panel)
            self.v9_shell.set_right_widget(self.qpcr_workflow_panel, right_title)
            self.data_panel.setVisible(False)
            return
        if module_id == "wb":
            self.v9_shell.set_right_widget(self.wb_workflow_panel, right_title)
            self.data_panel.setVisible(False)
            return
        if module_id == "fluoro":
            self.v9_shell.set_right_widget(self.fluoro_workspace.workflow_panel, right_title)
            self.data_panel.setVisible(False)
            return
        if module_id == "ihc":
            self.v9_shell.set_right_widget(self.ihc_workspace.workflow_panel, right_title)
            self.data_panel.setVisible(False)
            return
        if module_id == "brightfield":
            self.v9_shell.set_right_widget(self.brightfield_workspace.workflow_panel, right_title)
            self.data_panel.setVisible(False)
            return
        if module_id == "viewer":
            self._expand_right_workflow_widget(self.image_browser_workspace.side_panel)
            self.v9_shell.set_right_widget(self.image_browser_workspace.side_panel, right_title)
            self.data_panel.setVisible(False)
            return
        if module_id == "composer":
            self.v9_shell.set_right_widget(self.composer_workspace.dock_sidebar(self.v9_shell),right_title)
            self.data_panel.setVisible(False)
            return

        self.v9_shell.set_right_widget(self.style_sidebar, right_title)
        self.style_sidebar.setVisible(True)
        self.data_panel.setVisible(False)

    def set_module(self, module_id: str) -> None:
        if hasattr(self,'layout_edit_controller'):self.layout_edit_controller.flush()
        if self.active_module=='composer' and module_id!='composer':
            self.composer_workspace.controller.flush_edits()
            if self.composer_workspace._focus_mode:self.composer_workspace.toggle_focus()
            self.v9_shell.right_layout.insertWidget(1,self.chart_kind_controls)
        if hasattr(self, "analysis_state"):
            previous = {"ihc": "ihc", "brightfield": "brightfield", "fluoro": "fluorescence"}.get(self.active_module)
            if previous:
                self.analysis_state.capture_outgoing(previous)
        if self.active_module == "brightfield" or module_id == "brightfield":
            self._cancel_brightfield_guided_preview_debounce()
        self.active_module = module_id
        self.module_selector.set_active_module(module_id)
        self._sync_chart_kind_controls()
        if module_id == "stylelab":
            self._configure_v9_shell_for_module(module_id)
            self.style_sidebar.set_module(module_id)
            self.stylelab_workspace.open_local_extraction()
            self.canvas_stack.setCurrentWidget(self.stylelab_workspace)
            self.status_label.setText(tr("stylelab_status"))
            self.canvas_stack.updateGeometry()
            self._ensure_within_screen()
            self._schedule_active_canvas_layout_refresh()
            self._sync_chart_kind_controls()
            return
        if module_id == "viewer":
            self._configure_v9_shell_for_module(module_id)
            self.canvas_stack.setCurrentWidget(self.image_browser_workspace.canvas)
            self.status_label.setText(tr("viewer_status_ready"))
            self.canvas_stack.updateGeometry()
            self._ensure_within_screen()
            self._schedule_active_canvas_layout_refresh()
            self._sync_chart_kind_controls()
            return
        content = localized_module_placeholder(module_id)
        self.data_panel.set_module(module_id)
        self._configure_v9_shell_for_module(module_id)
        if module_id in {"qpcr", "wb"}:
            self.data_panel.set_journal_preset(self._active_module_journal_preset(module_id))
        if module_id == "ihc":
            self.ihc_workspace.canvas.set_module(module_id, content["canvas"])
        elif module_id == "brightfield":
            self.brightfield_workspace.canvas.set_module(module_id, content["canvas"])
        elif module_id == 'qpcr':
            self.preview_canvas.set_module(module_id, content["canvas"])
        if module_id in {"qpcr", "wb", "fluoro", "composer", "stylelab"}:
            self.style_sidebar.set_module(module_id)
        self.status_label.setText(content["status"])
        if module_id == "composer":
            self.canvas_stack.setCurrentWidget(self.composer_workspace)
            self.composer_workspace.set_project(self.project)
            self.composer_workspace.attach_chart_controls(self.chart_kind_controls)
        elif module_id == "wb":
            self.canvas_stack.setCurrentWidget(self.wb_workspace)
            self.wb_canvas.set_panel(self._active_wb_panel())
            self.set_wb_workflow_step(self.data_panel.wb_context_stack.currentIndex())
        elif module_id == "fluoro":
            self.canvas_stack.setCurrentWidget(self.fluoro_workspace)
            fluoro_panel = self._active_fluoro_panel()
            self.fluoro_workspace.set_panel(fluoro_panel, refresh=False)
            self.fluoro_workspace.refresh(refresh_canvas=False)
            step_index = int(fluoro_panel.data.get("active_step", 0) or 0)
            self.fluoro_workspace.set_step(step_index)
            if hasattr(self, "analysis_state"):
                self.analysis_state.restore_incoming("fluorescence")
        elif module_id == "ihc":
            self.canvas_stack.setCurrentWidget(self.ihc_workspace)
            panel = self._active_ihc_panel()
            self.ihc_workspace.set_panel(panel)
            self._sync_ihc_controls_from_panel(panel)
            self.data_panel.populate_ihc_import_controls(
                panel.images(),
                str(panel.active_image().get("uid", "")) if panel.active_image() is not None else None,
            )
            self.data_panel.populate_ihc_measurements(list(panel.analysis.get("ihc_measurements", [])))
            self.data_panel.populate_ihc_stats_controls(list(panel.analysis.get("ihc_measurements", [])))
            self.data_panel.populate_ihc_output_status(list(panel.data.get("composer_outputs", [])))
            step_index = int(panel.data.get("active_step", 0) or 0)
            self.data_panel.set_ihc_step(step_index, emit_signal=False)
            self.ihc_workspace.set_step(step_index)
            if panel.analysis.get("ihc_statistics"):
                self.data_panel.populate_ihc_stats_results(panel.analysis["ihc_statistics"])
                self._refresh_ihc_stats_chart(panel.analysis["ihc_statistics"])
            measurement = self._active_ihc_measurement(panel)
            if step_index == 3 and (measurement is not None or panel.active_image() is not None):
                self.show_ihc_layout()
            elif panel.active_image() is not None:
                self._refresh_ihc_preview(panel)
            self._update_ihc_qc_status(measurement)
            self._update_ihc_assisted_ui_state(panel)
            if hasattr(self, "analysis_state"):
                self.analysis_state.restore_incoming("ihc")
        elif module_id == "brightfield":
            self.canvas_stack.setCurrentWidget(self.brightfield_workspace)
            panel = self._active_brightfield_panel()
            self.brightfield_workspace.set_panel(panel)
            self._sync_brightfield_controls_from_panel(panel)
            self.data_panel.populate_brightfield_import_controls(
                panel.images(),
                str(panel.active_image().get("uid", "")) if panel.active_image() is not None else None,
            )
            self.data_panel.populate_brightfield_measurements(list(panel.analysis.get("brightfield_measurements", [])))
            self._sync_brightfield_stats_controls(panel)
            self._refresh_brightfield_ai_views(panel)
            self.data_panel.populate_brightfield_output_status(list(panel.data.get("composer_outputs", [])))
            step_index = int(panel.data.get("active_step", 0) or 0)
            self.data_panel.set_brightfield_step(step_index, emit_signal=False)
            self.brightfield_workspace.set_step(step_index)
            self._restore_brightfield_statistics_view(panel)
            measurement = self._active_brightfield_measurement(panel)
            if step_index == 4 and (measurement is not None or panel.active_image() is not None):
                self.show_brightfield_layout()
            elif panel.active_image() is not None:
                self.refresh_brightfield_measurement_type()
            self._update_brightfield_measurement_status(measurement)
            self._update_brightfield_qc_status(measurement)
            self._set_brightfield_measurement_summary(measurement)
            self._refresh_result_freshness_status()
            if hasattr(self, "analysis_state"):
                self.analysis_state.restore_incoming("brightfield")
        else:
            self.show_qpcr_preview()
        self.canvas_stack.updateGeometry()
        self._ensure_within_screen()
        self._schedule_active_canvas_layout_refresh()
        self._sync_chart_kind_controls()

    def _active_module_journal_preset(self, module_id: str | None = None) -> str:
        target_module = module_id or self.active_module
        if target_module == "qpcr":
            return str(self._active_qpcr_panel().style.get("preset", "Nature"))
        if target_module == "wb":
            return str(self._active_wb_panel().style.get("preset", "Nature"))
        if target_module == "fluoro":
            return str(self._active_fluoro_panel().style.get("preset", "Nature"))
        if target_module == "ihc":
            return str(self._active_ihc_panel().style.get("preset", "Nature"))
        if target_module == "brightfield":
            return str(self._active_brightfield_panel().style.get("preset", "Nature"))
        if target_module == "composer":
            return str(self.project.ensure_active_composer_figure().journal_preset or "Nature")
        return "Nature"

    def set_active_module_journal_preset(self, preset_name: str) -> None:
        if not preset_name:
            return
        if self.active_module == "qpcr":
            panel = self._active_qpcr_panel()
            panel.style["preset"] = preset_name
            panel.append_audit("set_journal_preset", preset_name)
            self._refresh_qpcr_ui(panel)
            self.project.touch()
            return
        if self.active_module == "wb":
            panel = self._active_wb_panel()
            panel.style["preset"] = preset_name
            panel.data["journal_preset"] = preset_name
            panel.append_audit("set_journal_preset", preset_name)
            if panel.data.get("densitometry"):
                self._refresh_wb_density_chart(panel.data["densitometry"])
            if panel.data.get("wb_statistics"):
                self._refresh_wb_stats_chart(panel.data["wb_statistics"])
                self.wb_canvas.render()
            self.project.touch()
            return
        if self.active_module == "fluoro":
            panel = self._active_fluoro_panel()
            panel.style["preset"] = preset_name
            panel.append_audit("set_journal_preset", preset_name)
            if panel.analysis.get("fluoro_statistics"):
                self.fluoro_workspace._refresh_fluoro_stats_chart(dict(panel.analysis["fluoro_statistics"]))
            if self.fluoro_workspace.canvas.current_step == 4:
                self.fluoro_workspace.canvas.refresh()
            self.project.touch()
            return
        if self.active_module == "ihc":
            panel = self._active_ihc_panel()
            panel.style["preset"] = preset_name
            panel.append_audit("set_journal_preset", preset_name)
            if panel.analysis.get("ihc_statistics"):
                self._refresh_ihc_stats_chart(dict(panel.analysis["ihc_statistics"]))
            self.project.touch()
            return
        if self.active_module == "brightfield":
            panel = self._active_brightfield_panel()
            panel.style["preset"] = preset_name
            panel.append_audit("set_journal_preset", preset_name)
            if self.data_panel.brightfield_stats_plot_source() == "ai_object_count":
                self._refresh_brightfield_stats_chart(self._brightfield_ai_plot_statistics_result(panel))
            elif panel.analysis.get("brightfield_statistics"):
                self._refresh_brightfield_stats_chart(dict(panel.analysis["brightfield_statistics"]))
            self.project.touch()
            return
        if self.active_module == "composer":
            figure = self.project.ensure_active_composer_figure()
            figure.journal_preset = preset_name
            figure.global_style["journal_preset"] = preset_name
            figure.append_audit("set_journal_preset", preset_name)
            self.project.touch()

    def apply_style_lab_runtime_saved_preset(self) -> None:
        self._refresh_style_lab_preset_dependents()
        status = last_runtime_style_preset_status()
        if status.source == "runtime_registry":
            message = tr("saved_style_applied_status")
        else:
            message = tr("builtin_style_preview_status")
        self.statusBar().showMessage(message, 3000)

    def _refresh_style_lab_preset_dependents(self) -> None:
        self.composer_canvas._plot_payload_bytes_cache.clear()
        self.composer_canvas._pixmap_cache.clear()
        self.preview_canvas.clear_figure()
        self._refresh_qpcr_ui(self._active_qpcr_panel())

        wb_panel = self._active_wb_panel()
        if wb_panel.data.get("densitometry"):
            self._refresh_wb_density_chart(wb_panel.data["densitometry"])
        if wb_panel.data.get("wb_statistics"):
            self._refresh_wb_stats_chart(wb_panel.data["wb_statistics"])
        if wb_panel.data.get("wb_layout", {}).get("enabled"):
            self.wb_canvas.render()

        fluoro_panel = self._active_fluoro_panel()
        if fluoro_panel.analysis.get("fluoro_statistics"):
            self.fluoro_workspace._refresh_fluoro_stats_chart(dict(fluoro_panel.analysis["fluoro_statistics"]))
        if self.fluoro_workspace.canvas.current_step == 4:
            self.fluoro_workspace.canvas.refresh()

        ihc_panel = self._active_ihc_panel()
        if ihc_panel.analysis.get("ihc_statistics"):
            self._refresh_ihc_stats_chart(dict(ihc_panel.analysis["ihc_statistics"]))

        brightfield_panel = self._active_brightfield_panel()
        if self.data_panel.brightfield_stats_plot_source() == "ai_object_count":
            self._refresh_brightfield_stats_chart(self._brightfield_ai_plot_statistics_result(brightfield_panel))
        elif brightfield_panel.analysis.get("brightfield_statistics"):
            self._refresh_brightfield_stats_chart(dict(brightfield_panel.analysis["brightfield_statistics"]))
        self.composer_canvas.render()

    def preflight_composer_export(self) -> None:
        if self.active_module != "composer":
            return
        messages = self.composer_workspace.show_preflight()
        errors = sum(1 for message in messages if str(message.get("severity", "")).lower() == "error")
        warnings = sum(1 for message in messages if str(message.get("severity", "")).lower() == "warning")
        self.statusBar().showMessage(
            trf("composer_preflight_status", errors=errors, warnings=warnings),
            4000,
        )

    def set_wb_workflow_step(self, step_index: int) -> None:
        if self.active_module != "wb":
            return
        mode_by_step = {
            WB_STEP_IMPORT: "move",
            WB_STEP_CROP: "roi",
            WB_STEP_LABELS: "lane",
            WB_STEP_DENSITY: "move",
            WB_STEP_STATS: "move",
            WB_STEP_LAYOUT: "move",
            WB_STEP_EXPORT: "move",
        }
        panel = self._active_wb_panel()
        if int(panel.data.get("wb_active_step", WB_STEP_IMPORT)) != int(step_index):
            panel.data["wb_active_step"] = int(step_index)
        if step_index == WB_STEP_DENSITY:
            self._sync_wb_labels_for_density(panel)
        self.wb_canvas.set_mode(mode_by_step.get(step_index, "move"))
        self.wb_canvas.set_panel(panel)
        if hasattr(self, "wb_canvas_tools_group"):
            self.wb_canvas_tools_group.setVisible(step_index == WB_STEP_LAYOUT)
        if hasattr(self, "wb_staging_group"):
            self.wb_staging_group.setVisible(step_index in {WB_STEP_IMPORT, WB_STEP_CROP, WB_STEP_LABELS, WB_STEP_DENSITY})
        self.status_label.setText(self.data_panel.wb_step_hint.text())
        if step_index == WB_STEP_DENSITY:
            self._populate_wb_density_controls(panel)
            existing = panel.data.get("densitometry")
            if existing:
                self.data_panel.populate_wb_density_results(existing)
                self._refresh_wb_density_chart(existing)
                self.wb_canvas.set_density_overlay(existing)
        elif step_index == WB_STEP_STATS:
            self.data_panel.populate_wb_stats_controls(list(panel.data.get("densitometry_runs", [])) or ([panel.data["densitometry"]] if panel.data.get("densitometry") else []))
            if panel.data.get("wb_statistics"):
                self.data_panel.populate_wb_stats_results(panel.data["wb_statistics"])
                self._refresh_wb_stats_chart(panel.data["wb_statistics"])
        elif step_index == WB_STEP_EXPORT:
            self.data_panel.populate_wb_export_items(panel.data)
        else:
            self.wb_canvas.set_density_overlay(None)
        self._sync_chart_kind_controls()

    def set_qpcr_workflow_step(self, step_index: int) -> None:
        if self.active_module != "qpcr":
            return
        self.data_panel.set_qpcr_step(step_index, emit_signal=False)
        if step_index == 2:
            self.show_qpcr_layout()
            return
        self.data_panel.setVisible(False)
        self.qpcr_workflow_panel.setVisible(True)
        self.data_panel.set_qpcr_layout_mode(False)
        if hasattr(self, "qpcr_canvas_tools_group"):
            self.qpcr_canvas_tools_group.setVisible(False)
        if hasattr(self, "qpcr_workflow_group"):
            self.qpcr_workflow_group.setVisible(True)
        self.canvas_stack.setCurrentWidget(self.qpcr_preview_workspace)
        if step_index == 3:
            self.statusBar().showMessage(tr("choose_export_format_status"), 3000)
        self._schedule_active_canvas_layout_refresh()
        self._sync_chart_kind_controls()

    def new_project(self) -> None:
        if not self._confirm_destructive_action(tr("action_create_new_project")):
            return
        if hasattr(self, "project_recovery"):
            self.project_recovery.note_leaving_project()
        previous_project = self.project
        self.project = self._create_default_project()
        self._project_path = None
        self._dispose_project_transient_caches(previous_project)
        self.image_browser_workspace.clear_browser_workspace_state()
        if hasattr(self, "analysis_state"):
            with self.analysis_state.suspend_capture():
                self.set_module(self.active_module)
        else:
            self.set_module(self.active_module)
        self._capture_clean_project_baseline()
        self.statusBar().showMessage(tr("new_project_created_status"), 3000)

    def create_mock_panel(self) -> None:
        panel_number = len(self.project.panels) + 1
        mapping = {
            "qpcr": QPCRPanel,
            "wb": WesternBlotPanel,
            "fluoro": FluorescencePanel,
            "brightfield": BrightfieldPanel,
            "composer": ExternalImagePanel,
        }
        panel_cls = mapping[self.active_module]
        panel = panel_cls(panel_id=f"Figure1{chr(64 + min(panel_number, 26))}")
        panel.append_audit("create_mock_panel", self.active_module)
        self.project.add_panel(panel)
        self.statusBar().showMessage(trf("mock_panel_added_status", panel_id=panel.panel_id), 3000)
        self.refresh_composer_panel_list()

    def _active_qpcr_panel(self) -> QPCRPanel:
        for panel in self.project.panels:
            if isinstance(panel, QPCRPanel):
                return panel
        panel = QPCRPanel(panel_id=f"Figure1{chr(65 + len(self.project.panels))}")
        self.project.add_panel(panel)
        return panel

    def _active_wb_panel(self) -> WesternBlotPanel:
        for panel in self.project.panels:
            if isinstance(panel, WesternBlotPanel):
                return panel
        panel = WesternBlotPanel(panel_id=f"Figure1{chr(65 + len(self.project.panels))}")
        self.project.add_panel(panel)
        return panel

    def _active_fluoro_panel(self) -> FluorescencePanel:
        for panel in self.project.panels:
            if isinstance(panel, FluorescencePanel):
                return panel
        panel = FluorescencePanel(panel_id=f"Figure1{chr(65 + len(self.project.panels))}")
        self.project.add_panel(panel)
        return panel

    def _active_ihc_panel(self) -> IHCPanel:
        for panel in self.project.panels:
            if isinstance(panel, IHCPanel):
                return panel
        panel = IHCPanel(panel_id=f"Figure1{chr(65 + len(self.project.panels))}")
        self.project.add_panel(panel)
        return panel

    def _active_brightfield_panel(self) -> BrightfieldPanel:
        for panel in self.project.panels:
            if isinstance(panel, BrightfieldPanel):
                return panel
        panel = BrightfieldPanel(panel_id=f"Figure1{chr(65 + len(self.project.panels))}")
        self.project.add_panel(panel)
        return panel

    def _capture_wb_state(self) -> dict:
        panel = self._active_wb_panel()
        return {
            "data": deepcopy(panel.data),
            "analysis": deepcopy(panel.analysis),
            "style": deepcopy(panel.style),
            "export": deepcopy(panel.export),
            "modified_at": panel.modified_at,
        }

    def _restore_wb_state(self, state: dict) -> None:
        panel = self._active_wb_panel()
        panel.data = deepcopy(state.get("data", {}))
        panel.analysis = deepcopy(state.get("analysis", {}))
        panel.style = deepcopy(state.get("style", {}))
        panel.export = deepcopy(state.get("export", {}))
        panel.modified_at = str(state.get("modified_at", panel.modified_at))
        self._refresh_wb_ui(panel)

    def _record_wb_change(self, description: str, before: dict) -> None:
        after = self._capture_wb_state()
        if before == after:
            return
        self.wb_history.record(SnapshotCommand(description, before, after, self._restore_wb_state))

    def _wb_band_sources_for_ui(self, panel: WesternBlotPanel) -> list[dict]:
        crops = list(panel.data.get("crops", []))
        if crops:
            return crops
        strips = list(panel.data.get("band_strips", []))
        placed_ids = list(panel.data.get("placed_strip_ids", []))
        if placed_ids:
            by_id = {str(strip.get("strip_id")): strip for strip in strips}
            strips = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
        return [
            {
                "crop_id": strip.get("strip_id", f"strip_{index + 1}"),
                "protein": strip.get("protein_name", ""),
                "roi": strip.get("crop_roi", {}),
            }
            for index, strip in enumerate(strips)
        ]

    def _populate_wb_density_controls(self, panel: WesternBlotPanel) -> None:
        strips = list(panel.data.get("band_strips", []))
        placed_ids = list(panel.data.get("placed_strip_ids", []))
        if placed_ids:
            by_id = {str(strip.get("strip_id")): strip for strip in strips}
            strips = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
        strip_info = [
            {
                "strip_id": str(strip.get("strip_id", f"strip_{index + 1}")),
                "protein_name": str(strip.get("protein_name", "")),
            }
            for index, strip in enumerate(strips)
        ]
        lane_labels = list(panel.data.get("annotations", {}).get("lane_labels", []))
        if not lane_labels:
            lane_labels = list(panel.data.get("wb_layout", {}).get("lane_labels", []))
        if not lane_labels:
            lane_count = len(panel.data.get("annotations", {}).get("lane_anchors", []))
            if lane_count <= 0:
                lane_count = self.data_panel.wb_lane_count_spin.value()
            lane_labels = [f"Lane {index + 1}" for index in range(lane_count)]
        self.data_panel.populate_wb_density_controls(strip_info, lane_labels)

    def _sync_wb_labels_for_density(self, panel: WesternBlotPanel) -> None:
        entered_lane_labels = self.data_panel.wb_lane_labels()
        existing_lane_labels = list(panel.data.get("annotations", {}).get("lane_labels", []))
        if entered_lane_labels:
            lane_count = self.data_panel.wb_lane_count_spin.value()
            lane_labels = entered_lane_labels[:lane_count]
            if len(lane_labels) < lane_count:
                lane_labels += [str(index + 1) for index in range(len(lane_labels), lane_count)]
        elif existing_lane_labels:
            lane_labels = existing_lane_labels
        else:
            lane_labels = self.data_panel.wb_lane_labels_for_count()
        if lane_labels:
            annotations = dict(panel.data.get("annotations", {}))
            annotations["lane_labels"] = lane_labels
            panel.data["annotations"] = annotations
        bands = self.data_panel.wb_band_configuration()
        strips = list(panel.data.get("band_strips", []))
        placed_ids = list(panel.data.get("placed_strip_ids", []))
        ordered_indices = list(range(len(strips)))
        if placed_ids:
            by_id = {str(strip.get("strip_id")): index for index, strip in enumerate(strips)}
            ordered_indices = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
        for row, strip_index in enumerate(ordered_indices):
            if row >= len(bands) or strip_index >= len(strips):
                continue
            protein = str(bands[row].get("protein", "")).strip()
            if protein:
                strips[strip_index]["protein_name"] = protein
        if strips:
            panel.data["band_strips"] = strips
        layout = dict(panel.data.get("wb_layout", {}))
        layout["enabled"] = bool(layout.get("enabled", False))
        layout["lane_labels"] = lane_labels
        layout["bands"] = [
            {
                "band_index": index,
                "strip_id": strips[strip_index].get("strip_id") if strip_index < len(strips) else None,
                "protein": str(bands[index].get("protein", "")).strip(),
                "mw_kda": str(bands[index].get("mw_kda", "")).strip(),
                "is_loading_ctrl": bool(bands[index].get("is_loading_ctrl", False)),
            }
            for index, strip_index in enumerate(ordered_indices[: len(bands)])
        ]
        panel.data["wb_layout"] = layout

    def _editing_band_strips(self, panel: WesternBlotPanel) -> bool:
        return len(panel.data.get("band_strips", [])) > 1 and not panel.data.get("crops")

    def _selected_wb_strip_index(self, panel: WesternBlotPanel) -> int | None:
        strips = panel.data.get("band_strips", [])
        if not strips:
            return None
        table_index = self.data_panel.selected_wb_strip_index()
        if table_index is not None:
            self._wb_selected_strip_index = table_index
        else:
            self._wb_selected_strip_index = int(panel.data.get("selected_strip_index", self._wb_selected_strip_index))
        self._wb_selected_strip_index = max(0, min(int(self._wb_selected_strip_index), len(strips) - 1))
        return self._wb_selected_strip_index

    def import_active_module(self) -> None:
        try:
            if self.active_module in {"fluoro", "ihc", "brightfield"} and self._analysis_job_service.is_busy(
                f"import:{self.active_module}"
            ):
                return
            if self.active_module == "wb":
                paths, _ = QFileDialog.getOpenFileNames(
                    self,
                    tr("import_wb_images_title"),
                    recent_directory(),
                    image_filter("Western Blot images", WB_IMAGE_SUFFIXES),
                )
                if paths:
                    remember_file_directory(paths[0])
                    self.import_wb_images(paths)
                return
            if self.active_module == "fluoro":
                paths, _ = QFileDialog.getOpenFileNames(
                    self,
                    tr("import_fluorescence_images_title"),
                    recent_directory(),
                    image_filter("Fluorescence images", FLUORESCENCE_IMAGE_SUFFIXES),
                )
                if paths:
                    remember_file_directory(paths[0])
                    self.import_fluorescence_images(paths)
                return
            if self.active_module == "ihc":
                paths, _ = QFileDialog.getOpenFileNames(
                    self,
                    tr("import_ihc_images_title"),
                    recent_directory(),
                    image_filter("Histology / IHC images", IHC_IMAGE_SUFFIXES),
                )
                if paths:
                    remember_file_directory(paths[0])
                    self.import_ihc_images(paths)
                return
            if self.active_module == "brightfield":
                paths, _ = QFileDialog.getOpenFileNames(
                    self,
                    tr("import_brightfield_images_title"),
                    recent_directory(),
                    image_filter("Brightfield images", BRIGHTFIELD_IMAGE_SUFFIXES),
                )
                if paths:
                    remember_file_directory(paths[0])
                    self.import_brightfield_images(paths)
                return
            if self.active_module != "qpcr":
                QMessageBox.information(self, tr("import"), tr("import_pipeline_later"))
                return
            path, _ = QFileDialog.getOpenFileName(
                self,
                tr("import_qpcr_data_title"),
                recent_directory(),
                "qPCR data (*.csv *.tsv *.txt *.xlsx *.xls)",
            )
            if not path:
                return
            remember_file_directory(path)
            panel = self._active_qpcr_panel()
            options = panel.load_qpcr_file(path, instrument=self.data_panel.instrument_combo.currentText())
            self.project.append_audit("import_qpcr_panel", Path(path).name)
            self._refresh_qpcr_import_ui(panel, options)
            self.statusBar().showMessage(
                trf("qpcr_import_status", name=Path(path).name),
                6000,
            )
        except Exception as exc:
            log_path = log_exception("qPCR import failed", exc)
            QMessageBox.critical(
                self,
                tr("qpcr_import_failed_title"),
                trf("error_with_traceback", error=exc, path=log_path),
            )

    def handle_external_files_dropped(self, paths: list[str]) -> None:
        if self.active_module == "composer":
            self.import_external_images_to_shelf(paths)
            return
        if self.active_module == "wb":
            self.import_wb_images(paths)
            return
        if self.active_module == "ihc":
            self.import_ihc_images(paths)
            return
        if self.active_module == "brightfield":
            self.import_brightfield_images(paths)
            return
        if self.active_module == "fluoro":
            self.import_fluorescence_images(paths)

    def import_fluorescence_images(self, paths: list[str]) -> None:
        panel = self._active_fluoro_panel()
        self.fluoro_workspace.set_panel(panel, refresh=False)
        self._start_microscopy_import("fluoro", paths, panel)

    def import_browser_fluorescence_transfer(self, payloads: list) -> None:
        items = [item for item in list(payloads or []) if isinstance(item, dict) and str(item.get("path", "")).strip()]
        if not items:
            return
        paths = [str(item["path"]) for item in items]
        transfer_by_path = {str(item["path"]): deepcopy(item) for item in items}
        panel = self._active_fluoro_panel()
        self.fluoro_workspace.set_panel(panel, refresh=False)
        self._start_microscopy_import("fluoro", paths, panel, transfer_by_path=transfer_by_path)

    @staticmethod
    def _apply_browser_scale_transfer_after_import(
        snapshot_panel,
        record: dict[str, Any],
        transfer_by_path: dict[str, Any] | None,
    ) -> None:
        if not transfer_by_path or not isinstance(record, dict):
            return
        path = str(record.get("source_path") or "")
        payload = transfer_by_path.get(path)
        if payload is None and path:
            payload = transfer_by_path.get(str(Path(path)))
        if payload is None:
            return
        snapshot_panel.apply_browser_scale_transfer(str(record.get("uid", "")), payload)

    def import_ihc_images(self, paths: list[str]) -> None:
        self._start_microscopy_import("ihc", paths, self._active_ihc_panel())

    def import_brightfield_images(self, paths: list[str]) -> None:
        self._start_microscopy_import("brightfield", paths, self._active_brightfield_panel())

    @staticmethod
    def _qimage_from_microscopy_result(imported: Any) -> QImage:
        """Build the full-resolution display image from the importer's decoded pixels."""
        data = np.asarray(imported.analysis_data)
        if data.ndim != 3 or data.shape[1] < 1 or data.shape[2] < 1:
            raise ValueError(f"Expected CYX microscopy data, got shape {data.shape}")
        channels, height, width = (int(data.shape[0]), int(data.shape[1]), int(data.shape[2]))

        if not (data.dtype == np.uint8 or data.dtype == np.uint16):
            if np.issubdtype(data.dtype, np.floating):
                finite = data[np.isfinite(data)]
                maximum = float(finite.max()) if finite.size else 1.0
                data = np.clip(data * (65535.0 / max(maximum, 1.0)), 0, 65535).astype(np.uint16)
            else:
                data = np.clip(data, 0, 65535).astype(np.uint16)

        if channels == 1:
            plane = np.ascontiguousarray(data[0])
            if plane.dtype == np.uint8:
                return QImage(
                    plane.data,
                    width,
                    height,
                    int(plane.strides[0]),
                    QImage.Format.Format_Grayscale8,
                ).copy()
            if plane.dtype == np.uint16:
                return QImage(
                    plane.data,
                    width,
                    height,
                    int(plane.strides[0]),
                    QImage.Format.Format_Grayscale16,
                ).copy()
            plane = np.ascontiguousarray(np.clip(plane, 0, 255).astype(np.uint8))
            return QImage(
                plane.data,
                width,
                height,
                int(plane.strides[0]),
                QImage.Format.Format_Grayscale8,
            ).copy()

        if channels == 2:
            if data.dtype == np.uint8:
                rgba = np.empty((height, width, 4), dtype=np.uint8)
                gray_alpha = np.moveaxis(data, 0, -1)
                rgba[:, :, :3] = gray_alpha[:, :, :1]
                rgba[:, :, 3] = gray_alpha[:, :, 1]
                image_format = QImage.Format.Format_RGBA8888
            else:
                rgba = np.empty((height, width, 4), dtype=np.uint16)
                gray_alpha = np.moveaxis(data, 0, -1)
                rgba[:, :, :3] = gray_alpha[:, :, :1]
                rgba[:, :, 3] = gray_alpha[:, :, 1]
                image_format = QImage.Format.Format_RGBA64
            return QImage(
                rgba.data,
                width,
                height,
                int(rgba.strides[0]),
                image_format,
            ).copy()

        if channels >= 3 and data.dtype == np.uint8:
            rgba = np.empty((height, width, 4), dtype=np.uint8)
            rgba[:, :, :3] = np.moveaxis(data[:3], 0, -1).astype(np.uint8, copy=False)
            rgba[:, :, 3] = data[3] if channels >= 4 else np.iinfo(np.uint8).max
            return QImage(
                rgba.data,
                width,
                height,
                int(rgba.strides[0]),
                QImage.Format.Format_RGBA8888,
            ).copy()

        if channels >= 3:
            rgba = np.empty((height, width, 4), dtype=np.uint16)
            rgba[:, :, :3] = np.moveaxis(data[:3], 0, -1)
            rgba[:, :, 3] = data[3] if channels >= 4 else np.iinfo(np.uint16).max
            return QImage(
                rgba.data,
                width,
                height,
                int(rgba.strides[0]),
                QImage.Format.Format_RGBA64,
            ).copy()
        raise ValueError(f"Unsupported microscopy preview channel count: {channels}")

    @staticmethod
    def _import_snapshot_file(snapshot_panel, path: str, module_id: str) -> tuple[dict[str, Any], QImage | None]:
        if module_id == "fluoro":
            return snapshot_panel.import_image(path), None

        from core.image_analysis.io_engine import read_microscopy_image

        imported = read_microscopy_image(path)
        record = snapshot_panel.import_image_from_decoded(path, imported)
        return record, MainWindow._qimage_from_microscopy_result(imported)

    def _start_microscopy_import(
        self,
        module_id: str,
        paths: list[str],
        panel,
        transfer_by_path: dict[str, Any] | None = None,
    ) -> None:
        normalized_paths = [str(path) for path in paths if str(path).strip()]
        if not normalized_paths:
            return
        key = f"import:{module_id}"
        if self._analysis_job_service.is_busy(key):
            return

        project = self.project
        project_fingerprint = self._microscopy_import_project_fingerprint(project)
        panel_fingerprint = sha256_json(panel.to_dict())
        transfers = {str(path): deepcopy(payload) for path, payload in dict(transfer_by_path or {}).items()}
        try:
            snapshot_panel = self._clone_panel_for_worker(panel)
        except Exception as exc:
            self._report_microscopy_import_failure(module_id, normalized_paths[0], exc, partial_count=0)
            return

        def work() -> _MicroscopyImportResult:
            records: list[dict[str, Any]] = []
            failed_path: str | None = None
            error: Exception | None = None
            preview: QImage | None = None
            for path in normalized_paths:
                try:
                    record, file_preview = self._import_snapshot_file(snapshot_panel, path, module_id)
                    if module_id == "fluoro":
                        self._apply_browser_scale_transfer_after_import(snapshot_panel, record, transfers)
                    elif path in transfers:
                        from core.histology_transfer import apply_browser_histology_transfer
                        apply_browser_histology_transfer(snapshot_panel, record, transfers[path], module_id)
                    records.append(record)
                    preview = file_preview
                except Exception as exc:
                    failed_path = path
                    error = exc
                    break
            return _MicroscopyImportResult(snapshot_panel, records, failed_path, error, preview)

        def current_panel() -> Any:
            if module_id == "fluoro":
                return self._active_fluoro_panel()
            if module_id == "ihc":
                return self._active_ihc_panel()
            return self._active_brightfield_panel()

        def is_current() -> bool:
            try:
                return (
                    self.project is project
                    and current_panel() is panel
                    and self._microscopy_import_project_fingerprint(project) == project_fingerprint
                    and sha256_json(panel.to_dict()) == panel_fingerprint
                )
            except Exception:
                return False

        def on_success(result: _MicroscopyImportResult) -> None:
            imported_uids = [str(record.get("uid", "")) for record in result.records]
            if imported_uids:
                self._apply_import_panel_snapshot(panel, result.panel, imported_uids)
                self._complete_microscopy_import(module_id, panel, result.records, result.preview)
            if result.error is not None and result.failed_path is not None:
                self._report_microscopy_import_failure(
                    module_id,
                    result.failed_path,
                    result.error,
                    partial_count=len(result.records),
                )

        def on_stale() -> None:
            self.statusBar().showMessage(
                trf(
                    "stale_import_status",
                    module=tr(f"v9_module_{module_id}"),
                ),
                5000,
            )

        try:
            self._analysis_job_service.start(
                key=key,
                work=work,
                on_success=on_success,
                on_error=lambda exc: self._report_microscopy_import_failure(
                    module_id,
                    normalized_paths[0],
                    exc,
                    partial_count=0,
                ),
                is_current=is_current,
                on_stale=on_stale,
            )
        except Exception as exc:
            self._report_microscopy_import_failure(module_id, normalized_paths[0], exc, partial_count=0)

    def _complete_microscopy_import(
        self,
        module_id: str,
        panel,
        records: list[dict[str, Any]],
        preview: QImage | None,
    ) -> None:
        imported_names = [str(record.get("source_name", "")) for record in records]
        if module_id == "fluoro":
            self.fluoro_workspace.set_panel(panel, refresh=False)
            self.fluoro_workspace.refresh(refresh_canvas=False)
            if str(self.fluoro_workspace.mode_combo.currentData() or "").lower() == "one_click":
                self.fluoro_workspace.set_step(4, refresh=False)
            else:
                self.fluoro_workspace.set_step(1, refresh=False)
            self.fluoro_workspace._refresh_canvas()
            self.project.append_audit("import_fluorescence_images", ", ".join(imported_names))
            self.statusBar().showMessage(
                trf(
                    "imported_images_status",
                    module=tr("v9_module_fluoro"),
                    count=len(imported_names),
                ),
                4000,
            )
            return
        if module_id == "ihc":
            self.ihc_workspace.set_panel(panel)
            self.project.append_audit("import_ihc_images", ", ".join(imported_names))
            self.data_panel.populate_ihc_import_controls(
                panel.images(),
                str(panel.active_image().get("uid", "")) if panel.active_image() is not None else None,
            )
            self.data_panel.populate_ihc_measurements(list(panel.analysis.get("ihc_measurements", [])))
            self.data_panel.populate_ihc_stats_controls(list(panel.analysis.get("ihc_measurements", [])))
            self.data_panel.populate_ihc_output_status(list(panel.data.get("composer_outputs", [])))
            self._sync_ihc_controls_from_panel(panel)
            self._refresh_ihc_preview(panel, preview=preview)
            self._update_ihc_qc_status(self._active_ihc_measurement(panel))
            self._update_ihc_assisted_ui_state(panel)
            self.statusBar().showMessage(
                trf(
                    "imported_images_status",
                    module=tr("histology_ihc"),
                    count=len(imported_names),
                ),
                4000,
            )
            return

        self.brightfield_workspace.set_panel(panel)
        self.project.append_audit("import_brightfield_images", ", ".join(imported_names))
        self.data_panel.populate_brightfield_import_controls(
            panel.images(),
            str(panel.active_image().get("uid", "")) if panel.active_image() is not None else None,
        )
        self.data_panel.populate_brightfield_measurements(list(panel.analysis.get("brightfield_measurements", [])))
        self._sync_brightfield_stats_controls(panel)
        self._refresh_brightfield_ai_views(panel)
        self.data_panel.populate_brightfield_output_status(list(panel.data.get("composer_outputs", [])))
        self._sync_brightfield_controls_from_panel(panel)
        self._refresh_brightfield_preview(panel, preview=preview)
        measurement = self._active_brightfield_measurement(panel)
        self._update_brightfield_measurement_status(measurement)
        self._update_brightfield_qc_status(measurement)
        self._set_brightfield_measurement_summary(measurement)
        self._update_brightfield_he_fiber_calibration_status(panel)
        self.statusBar().showMessage(
            trf(
                "imported_images_status",
                module=tr("v9_module_brightfield"),
                count=len(imported_names),
            ),
            4000,
        )

    def _report_microscopy_import_failure(
        self,
        module_id: str,
        failed_path: str,
        error: Exception,
        *,
        partial_count: int,
    ) -> None:
        label_keys = {
            "fluoro": "v9_module_fluoro",
            "ihc": "histology_ihc",
            "brightfield": "v9_module_brightfield",
        }
        label = tr(label_keys.get(module_id, f"v9_module_{module_id}"))
        log_path = log_exception(f"{label} import failed", error)
        partial = trf("partial_import_prefix", count=partial_count) if partial_count else ""
        QMessageBox.critical(
            self,
            trf("import_failed_title", module=label),
            trf(
                "import_failed_body",
                partial=partial,
                path=failed_path,
                error=error,
                log_path=log_path,
            ),
        )

    def remove_selected_fluorescence_image(self) -> None:
        panel = self._active_fluoro_panel()
        uid = self.fluoro_workspace.selected_image_uid()
        if not uid:
            return
        if not panel.remove_image(uid):
            return
        panel.append_audit("remove_fluorescence_image", uid)
        self.fluoro_workspace.refresh()
        self.statusBar().showMessage(
            trf("removed_image_status", module=tr("v9_module_fluoro")),
            2500,
        )

    def merge_selected_fluorescence_images(self, image_uids: list[str] | None = None) -> None:
        panel = self._active_fluoro_panel()
        selected = list(image_uids or self.fluoro_workspace.selected_merge_image_uids())
        try:
            merged = panel.merge_images_as_channels(selected)
        except Exception as exc:
            QMessageBox.warning(self, tr("merge_fluorescence_channels_title"), str(exc))
            return
        self.fluoro_workspace.refresh()
        self.fluoro_workspace.set_step(1)
        self.project.append_audit("merge_fluorescence_images_as_channels", str(merged.get("source_name", "")))
        self.statusBar().showMessage(
            trf(
                "merged_fluorescence_status",
                count=len(selected),
                name=merged.get("source_name", "merged"),
            ),
            4000,
        )

    def save_active_fluorescence_image(self, path: str | None = None) -> Path | None:
        panel = self._active_fluoro_panel()
        image = panel.active_image()
        if image is None:
            QMessageBox.information(
                self,
                tr("save_fluorescence_image_title"),
                tr("import_fluorescence_before_save"),
            )
            return None
        target_path = path
        if target_path is None:
            stem = Path(str(image.get("source_name", "fluorescence_image"))).stem or "fluorescence_image"
            target_path, _ = QFileDialog.getSaveFileName(
                self,
                tr("save_active_fluorescence_image_title"),
                f"{stem}.ome.tif",
                "OME-TIFF (*.ome.tif *.ome.tiff);;TIFF (*.tif *.tiff)",
            )
            if not target_path:
                return None
        try:
            target = panel.save_image_data_as_ome_tiff(str(image.get("uid", "")), target_path)
        except Exception as exc:
            QMessageBox.warning(self, tr("save_fluorescence_image_title"), str(exc))
            return None
        self.project.append_audit("save_fluorescence_image_data", target.name)
        self.statusBar().showMessage(
            trf("saved_fluorescence_status", name=target.name),
            4000,
        )
        return target

    def toggle_active_fluorescence_rgb_split(self) -> None:
        panel = self._active_fluoro_panel()
        image = panel.active_image()
        if image is None:
            return
        interpretation_mode = dict(image.get("channel_interpretation", {})).get("mode")
        try:
            if interpretation_mode == "forced_rgb_split":
                panel.restore_auto_detected_rgb_signal(str(image.get("uid", "")))
                status_message = tr("fluorescence_auto_detection_restored")
                audit_action = "restore_fluorescence_auto_single_signal"
            else:
                panel.force_split_rgb_samples(str(image.get("uid", "")))
                status_message = tr("fluorescence_rgb_split_status")
                audit_action = "force_split_fluorescence_rgb_samples"
        except Exception as exc:
            QMessageBox.warning(self, tr("fluorescence_channel_detection_title"), str(exc))
            return
        self.fluoro_workspace.refresh()
        self.project.append_audit(audit_action, str(image.get("source_name", "")))
        self.statusBar().showMessage(status_message, 4000)

    def remove_selected_ihc_image(self) -> None:
        panel = self._active_ihc_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        uid = str(active_image.get("uid", "")).strip()
        images = [image for image in panel.images() if str(image.get("uid", "")).strip() != uid]
        panel.data["ihc_images"] = images
        panel._dab_analysis_cache = {
            key: value
            for key, value in panel._dab_analysis_cache.items()
            if not str(key).startswith(f"{uid}:")
        }
        panel.data["active_image_uid"] = images[0].get("uid") if images else None
        panel.append_audit("remove_ihc_image", uid)
        new_uid = str(images[0].get("uid", "")) if images else ""
        self.data_panel.populate_ihc_import_controls(images, new_uid)
        self._refresh_ihc_preview(panel)
        self._sync_ihc_controls_from_panel(panel)
        self.statusBar().showMessage(
            trf("removed_image_status", module=tr("v9_module_ihc")),
            2500,
        )

    def remove_selected_brightfield_image(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        uid = str(active_image.get("uid", "")).strip()
        images = [image for image in panel.images() if str(image.get("uid", "")).strip() != uid]
        panel.data["brightfield_images"] = images
        panel._transient_preview_state.pop(uid, None)
        panel._transient_he_fiber_area_masks.pop(uid, None)
        panel._transient_he_fiber_area_labels.pop(uid, None)
        panel.data["active_image_uid"] = images[0].get("uid") if images else None
        panel.append_audit("remove_brightfield_image", uid)
        new_uid = str(images[0].get("uid", "")) if images else ""
        self.data_panel.populate_brightfield_import_controls(images, new_uid)
        self._refresh_brightfield_preview(panel)
        self._sync_brightfield_controls_from_panel(panel)
        self._refresh_brightfield_ai_views(panel)
        self._update_brightfield_he_fiber_calibration_status(panel)
        self.statusBar().showMessage(
            trf("removed_image_status", module=tr("v9_module_brightfield")),
            2500,
        )

    def import_wb_image(self, path: str) -> None:
        try:
            panel = self._active_wb_panel()
            image_data = panel.import_full_blot(path)
            self.project.append_audit("import_wb_full_blot", Path(path).name)
            self.wb_history.clear()
            self._wb_selected_strip_index = max(0, len(panel.data.get("band_strips", [])) - 1)
            # Establish the new image selection before controls can refresh the
            # canvas; the previous table row must not override this import.
            panel.data["selected_strip_index"] = self._wb_selected_strip_index
            self.data_panel.populate_wb_strips(panel.data.get("band_strips", []), self._wb_selected_strip_index)
            self.data_panel.configure_wb_image_bounds(int(image_data["width_px"]), int(image_data["height_px"]))
            self.data_panel.wb_rotation_spin.setValue(float(panel.data.get("adjustments", {}).get("rotation", 0.0)))
            self.data_panel.wb_fine_rotation_slider.setValue(0)
            self._refresh_wb_ui(panel, show_full=True)
            self.statusBar().showMessage(
                trf("imported_wb_status", name=Path(path).name),
                4000,
            )
        except Exception as exc:
            log_path = log_exception("Western Blot import failed", exc)
            QMessageBox.critical(
                self,
                tr("wb_import_failed_title"),
                trf("error_with_traceback", error=exc, path=log_path),
            )

    def import_wb_images(self, paths: list[str]) -> None:
        for path in paths:
            self.import_wb_image(path)
        if paths:
            self.data_panel.set_wb_step(WB_STEP_IMPORT)

    def _sync_ihc_controls_from_panel(self, panel: IHCPanel) -> None:
        guided_state = dict(panel.data.get("guided_state", {}))
        self._syncing_ihc_mode = True
        try:
            self.data_panel.set_ihc_guided_mode(normalize_ihc_mode(guided_state.get("guided_mode")))
        finally:
            self._syncing_ihc_mode = False
        self._set_combo_data(self.data_panel.ihc_sensitivity_combo, str(guided_state.get("sensitivity", "medium")))
        self._set_combo_data(self.data_panel.ihc_cleanup_combo, str(guided_state.get("cleanup_level", "medium")))
        self.data_panel.ihc_tissue_correction_checkbox.setChecked(bool(guided_state.get("tissue_background_correction", True)))
        self.data_panel.set_ihc_overlay_opacity(float(guided_state.get("overlay_opacity", 0.35)))

    def _sync_brightfield_controls_from_panel(self, panel: BrightfieldPanel) -> None:
        guided_state = panel._merge_guided_state(panel.data.get("guided_state"))
        panel.data["guided_state"] = dict(guided_state)
        self._syncing_brightfield_mode = True
        try:
            self.data_panel.set_brightfield_guided_mode(self._normalized_brightfield_mode(guided_state.get("guided_mode")))
            self.data_panel.set_brightfield_foolproof_threshold_bias(int(guided_state.get("foolproof_threshold_bias", 0)))
            self.data_panel.set_brightfield_assisted_threshold_bias(int(guided_state.get("assisted_threshold_bias", 0)))
            self.data_panel.set_brightfield_assisted_small_region_filter_bias(
                int(guided_state.get("assisted_small_region_filter_bias", 0))
            )
        finally:
            self._syncing_brightfield_mode = False
        self._set_combo_data(self.data_panel.brightfield_sensitivity_combo, str(guided_state.get("sensitivity", "medium")))
        self._set_combo_data(self.data_panel.brightfield_cleanup_combo, str(guided_state.get("cleanup_level", "low")))
        self._update_brightfield_batch_threshold_ui_state(panel)
        self._update_brightfield_he_fiber_calibration_status(panel)

    def _persist_brightfield_he_fiber_settings(self) -> None:
        panel = self._active_brightfield_panel()
        panel.persist_he_fiber_analysis_settings(self.data_panel.brightfield_analysis_settings())

    def _update_brightfield_he_fiber_calibration_status(self, panel: BrightfieldPanel) -> None:
        self.data_panel.set_brightfield_he_fiber_analysis_settings(dict(panel.data.get("analysis_settings", {})))
        active_image = panel.active_image()
        if active_image is None:
            self.data_panel.brightfield_he_fiber_calibration_status_label.setText(
                tr("he_fiber_area") + ": " + tr("import_he_before_calibration")
            )
            self.data_panel.brightfield_calibration_controls.set_scale({})
            self.data_panel.brightfield_he_fiber_scale_status_label.setText(tr("scale_no_image_selected"))
            self.brightfield_workspace.canvas.set_measurement_scale_bar({})
            return
        scale = panel.measurement_scale_bar(str(active_image.get("uid", "")))
        self._sync_brightfield_measurement_scale_controls(scale)
        calibration = panel.he_fiber_area_calibration(str(active_image.get("uid", "")))
        if calibration is None:
            self.data_panel.brightfield_he_fiber_calibration_status_label.setText(
                f"{tr('he_fiber_area')}: {tr('not_calibrated')} | {trf('active_image_value', name=active_image.get('source_name', ''))}"
            )
            return
        self.data_panel.brightfield_he_fiber_calibration_status_label.setText(
            f"{tr('he_fiber_area')}: {tr('calibrated')} | {trf('active_image_value', name=active_image.get('source_name', ''))} | "
            f"{tr('positive')} {int(calibration.get('positive_example_px', 0) or 0)} px, "
            f"{tr('negative')} {int(calibration.get('negative_example_px', 0) or 0)} px"
        )

    def _sync_brightfield_measurement_scale_controls(self, scale: dict[str, object]) -> None:
        image = self._active_brightfield_panel().active_image() or {}
        self.data_panel.brightfield_calibration_controls.set_scale(scale, metadata=image.get('metadata', {}))
        source = str(scale.get("calibration_source", "assumed"))
        um_per_px = float(scale.get("um_per_px", 0.0) or 0.0)
        length_um = float(scale.get("length_um", 50.0) or 50.0)
        self.data_panel.brightfield_he_fiber_scale_length_spin.blockSignals(True)
        self.data_panel.brightfield_he_fiber_scale_um_per_px_spin.blockSignals(True)
        self.data_panel.brightfield_he_fiber_scale_length_spin.setValue(length_um)
        self.data_panel.brightfield_he_fiber_scale_um_per_px_spin.setValue(um_per_px if source != "embedded" else 0.0)
        self.data_panel.brightfield_he_fiber_scale_length_spin.blockSignals(False)
        self.data_panel.brightfield_he_fiber_scale_um_per_px_spin.blockSignals(False)
        label = trf("scale_status", um_per_px=um_per_px, length_um=length_um, source=tr('scale_source_'+source))
        if source == "assumed":
            label += tr("scale_assumed_suffix")
        self.data_panel.brightfield_he_fiber_scale_status_label.setText(label)
        self.brightfield_workspace.canvas.set_measurement_scale_bar(scale)

    def start_brightfield_measurement_scale_line(self) -> None:
        panel = self._active_brightfield_panel()
        if panel.active_image() is None:
            QMessageBox.information(self, tr("he_fiber_area"), tr("import_he_before_calibration"))
            return
        self.brightfield_workspace.show_preview()
        self.brightfield_workspace.canvas.set_roi_selection_enabled(False)
        self.brightfield_workspace.canvas.set_line_selection_enabled(True)
        self.statusBar().showMessage(tr("known_scale_line_hint"), 4000)

    def set_brightfield_measurement_scale_line(self, line: dict) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        moving_existing = bool(getattr(self, "_brightfield_scale_line_move_active", False))
        self._brightfield_scale_line_move_active = False
        if moving_existing:
            self._commit_brightfield_display_line_move(str(active_image.get("uid", "")), dict(line))
            return
        length_um = self.data_panel.brightfield_he_fiber_scale_length_spin.value()
        scale = panel.update_measurement_scale_bar(
            str(active_image.get("uid", "")),
            length_um=length_um,
            line_px=dict(line),
            calibration_source="manual_line",
        )
        self.brightfield_workspace.canvas.set_line_selection_enabled(False)
        self._sync_brightfield_measurement_scale_controls(scale)
        self.statusBar().showMessage(tr("measurement_scale_applied"), 3000)

    def preview_brightfield_measurement_scale_line(self, line: dict) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        from ui.panels.brightfield_workspace import measurement_preview_line_px

        self._brightfield_scale_line_move_active = True
        scale = dict(panel.measurement_scale_bar(str(active_image.get("uid", ""))))
        metadata = dict(active_image.get("metadata") or {})
        preview_line = self._length_preserving_line(
            measurement_preview_line_px(scale) or dict(scale.get("line_px") or {}),
            dict(line),
            int(metadata.get("width_px") or metadata.get("width") or 0),
            int(metadata.get("height_px") or metadata.get("height") or 0),
        )
        if "display_line_px" in scale:
            scale["display_line_px"] = preview_line
            if str(scale.get("calibration_source") or "") == "manual_line":
                scale["line_px"] = dict(preview_line)
        else:
            scale["line_px"] = dict(preview_line)
        self.brightfield_workspace.canvas.set_measurement_scale_bar(scale)

    def _commit_brightfield_display_line_move(self, image_uid: str, line: dict) -> None:
        from ui.panels.brightfield_workspace import measurement_preview_line_px

        panel = self._active_brightfield_panel()
        scale = dict(panel.measurement_scale_bar(image_uid))
        active_image = panel.active_image() or {}
        metadata = dict(active_image.get("metadata") or {})
        moved = self._length_preserving_line(
            measurement_preview_line_px(scale) or dict(scale.get("line_px") or {}),
            dict(line),
            int(metadata.get("width_px") or metadata.get("width") or 0),
            int(metadata.get("height_px") or metadata.get("height") or 0),
        )
        updates: dict[str, object] = {"display_line_px": dict(moved)}
        if str(scale.get("calibration_source") or "") == "manual_line":
            updates["line_px"] = dict(moved)
            updates["calibration_source"] = "manual_line"
        resolved = panel.update_measurement_scale_bar(image_uid, **updates)
        self._sync_brightfield_measurement_scale_controls(resolved)

    @staticmethod
    def _length_preserving_line(original: dict, proposed: dict, width_px: int, height_px: int) -> dict[str, float]:
        from math import hypot

        def _coord(payload: dict, key: str) -> float:
            try:
                return float(payload.get(key, 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        if not original or not proposed:
            return {key: _coord(proposed, key) for key in ("x1", "y1", "x2", "y2")}
        ox1, oy1, ox2, oy2 = (_coord(original, key) for key in ("x1", "y1", "x2", "y2"))
        px1, py1, px2, py2 = (_coord(proposed, key) for key in ("x1", "y1", "x2", "y2"))
        orig_dx = ox2 - ox1
        orig_dy = oy2 - oy1
        if hypot(orig_dx, orig_dy) <= 0:
            return {"x1": px1, "y1": py1, "x2": px2, "y2": py2}
        center_x = (px1 + px2) / 2.0
        center_y = (py1 + py2) / 2.0
        x1 = center_x - orig_dx / 2.0
        y1 = center_y - orig_dy / 2.0
        x2 = center_x + orig_dx / 2.0
        y2 = center_y + orig_dy / 2.0
        width = max(1.0, float(width_px or 0) - 1.0)
        height = max(1.0, float(height_px or 0) - 1.0)
        min_x, max_x = min(x1, x2), max(x1, x2)
        min_y, max_y = min(y1, y2), max(y1, y2)
        shift_x = 0.0
        shift_y = 0.0
        if min_x < 0.0:
            shift_x = -min_x
        elif max_x > width:
            shift_x = width - max_x
        if min_y < 0.0:
            shift_y = -min_y
        elif max_y > height:
            shift_y = height - max_y
        return {"x1": x1 + shift_x, "y1": y1 + shift_y, "x2": x2 + shift_x, "y2": y2 + shift_y}

    def apply_brightfield_measurement_scale(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        image_uid = str(active_image.get("uid", ""))
        updates = self.data_panel.brightfield_calibration_controls.updates_for(panel.measurement_scale_bar(image_uid))
        scale = panel.update_measurement_scale_bar(image_uid, **updates)
        self._sync_brightfield_measurement_scale_controls(scale)
        self._show_scale_history_status()

    def _apply_brightfield_calibration_preset(self, updates) -> None:
        panel = self._active_brightfield_panel()
        image = panel.active_image()
        if image is None:
            return
        scale = panel.update_measurement_scale_bar(str(image['uid']), **updates)
        self._sync_brightfield_measurement_scale_controls(scale)
        self._show_scale_history_status()

    def reset_brightfield_measurement_scale(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        scale = panel.update_measurement_scale_bar(str(active_image.get("uid", "")), length_um=50.0, um_per_px=0.0, line_px={}, preset_id=None, preset_name=None)
        self._sync_brightfield_measurement_scale_controls(scale)
        self._show_scale_history_status()

    def _show_scale_history_status(self) -> None:
        history = tr("scale_history_uses_previous_calibration")
        self.statusBar().showMessage(history, 4000)
        label = self.data_panel.brightfield_he_fiber_scale_status_label
        current = str(label.text() or "").strip()
        if history not in current:
            label.setText(f"{current} {history}".strip() if current else history)

    def refresh_brightfield_measurement_scale_results(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        scale = panel.measurement_scale_bar(str(active_image.get("uid", "")))
        panel.rescale_ai_object_count_measurements(str(active_image.get("uid", "")), scale)
        selected_uuid = self.data_panel.selected_brightfield_ai_measurement_uuid()
        self._sync_brightfield_measurement_scale_controls(scale)
        self._refresh_brightfield_ai_views(panel, selected_measurement_uuid=selected_uuid)
        self._sync_brightfield_stats_controls(panel)
        self._restore_brightfield_statistics_view(panel)

    def _normalized_brightfield_mode(self, mode: object) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized == "guided":
            return "foolproof"
        if normalized in {"assisted", "expert"}:
            return normalized
        return "foolproof"

    def _set_combo_data(self, combo, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _format_metric_float(self, value: object, decimals: int, suffix: str = "") -> str:
        try:
            return f"{float(value):.{int(decimals)}f}{suffix}"
        except (TypeError, ValueError):
            return "n/a"

    def _format_metric_int(self, value: object, suffix: str = "") -> str:
        try:
            return f"{int(value)}{suffix}"
        except (TypeError, ValueError):
            return "n/a"

    def _measurement_reliability_status(self, measurement: dict | None) -> str:
        if measurement is None:
            return ""
        reliability = dict(measurement.get("reliability", {}))
        return str(measurement.get("reliability_status") or reliability.get("status", "")).strip().upper()

    def _measurement_warning_messages(self, measurement: dict | None) -> list[str]:
        if measurement is None:
            return []
        messages: list[str] = []
        for source in (
            measurement.get("warnings", []),
            dict(measurement.get("reliability", {})).get("messages", []),
            measurement.get("qc_messages", []),
        ):
            for item in source:
                text = str(item).strip()
                if text and text not in messages:
                    messages.append(text)
        return messages

    def _update_ihc_measurement_status(self, measurement: dict | None) -> None:
        if measurement is None:
            self.data_panel.ihc_reliability_banner.clear()
            self.data_panel.ihc_threshold_status_label.setText(f"{tr('measurement_status')}: {tr('awaiting_analysis')}.")
            return
        status = self._measurement_reliability_status(measurement) or "PASS"
        results = dict(measurement.get("results", {}))
        params = dict(measurement.get("params", {}))
        threshold_value = float(params.get("positive_threshold", 0.0))
        threshold_source = str(
            measurement.get("threshold_source")
            or dict(measurement.get("analysis_metadata", {})).get("threshold_source", "manual")
        ).strip() or "manual"
        text = f"{tr('measurement_status')}: {status} | {tr('threshold_value')}: {threshold_value:.3f} ({threshold_source})"
        self.data_panel.ihc_reliability_banner.set_status(
            status,
            self._measurement_warning_messages(measurement),
            metrics=[
                (tr("positive_area"), self._format_metric_float(results.get("positive_area_percent"), 2, "%")),
                (tr("h_score"), self._format_metric_float(results.get("h_score"), 1)),
            ],
        )
        self.data_panel.ihc_threshold_status_label.setText(text)

    def _update_ihc_qc_status(self, measurement: dict | None) -> None:
        if measurement is None:
            self.data_panel.ihc_qc_status_label.setText(tr("qc_awaiting_analysis"))
            return
        reliability_status = str(measurement.get("reliability_status", "")).strip()
        qc_messages = [str(message).strip() for message in measurement.get("qc_messages", []) if str(message).strip()]
        if reliability_status or qc_messages:
            qc_summary = f"{tr('qc')}: {reliability_status or 'PASS'}"
            if qc_messages:
                qc_summary += " | " + "; ".join(qc_messages)
            self.data_panel.ihc_qc_status_label.setText(qc_summary)
            return
        self.data_panel.ihc_qc_status_label.setText(tr("qc_awaiting_analysis"))

    def _update_ihc_assisted_ui_state(self, panel: IHCPanel) -> None:
        is_assisted = str(self.data_panel.current_ihc_guided_mode() or "").strip().lower() == "assisted"
        active_image = panel.active_image()
        image_uid = str(active_image.get("uid", "")).strip() if active_image is not None else ""
        measurement = self._active_ihc_measurement(panel) if active_image is not None else None
        can_sample = is_assisted and active_image is not None and measurement is not None
        can_revert = False
        if is_assisted and active_image is not None and measurement is not None:
            auto_uuid = str(dict(panel.data.get("guided_state", {})).get("auto_measurement_uuid_by_image", {}).get(image_uid, "")).strip()
            can_revert = bool(auto_uuid)
        samples = list(self._ihc_example_samples_by_image.get(image_uid, [])) if image_uid else []
        self.data_panel.populate_ihc_example_samples(samples)
        self.ihc_workspace.set_example_markers(samples if is_assisted else [])
        self.data_panel.ihc_examples_list.setEnabled(is_assisted and bool(samples))
        self.data_panel.ihc_delete_example_button.setEnabled(is_assisted and bool(samples))
        self.data_panel.ihc_batch_threshold_label.setText(self._format_ihc_batch_threshold_status(panel, image_uid))
        self.data_panel.set_ihc_assisted_action_state(
            can_sample=can_sample,
            can_recalibrate=is_assisted and bool(samples),
            can_revert=is_assisted and (can_revert or bool(samples)),
        )
        self.data_panel.ihc_export_overlay_button.setEnabled(active_image is not None)
        self.data_panel.ihc_export_results_csv_button.setEnabled(bool(panel.analysis.get("ihc_measurements")))
        self.data_panel.ihc_export_summary_json_button.setEnabled(bool(panel.analysis.get("ihc_measurements")))
        if not is_assisted:
            self._set_ihc_example_click_mode(None)

    def _format_ihc_batch_threshold_status(self, panel: IHCPanel, image_uid: str) -> str:
        guided_state = dict(panel.data.get("guided_state", {}))
        batch_state = dict(guided_state.get("batch_threshold_state", {}))
        threshold = batch_state.get("batch_threshold")
        if threshold is None:
            return tr("batch_threshold_waiting")
        parts = [
            f"Batch threshold: {'locked' if bool(batch_state.get('locked', False)) else 'current'} {float(threshold):.3f}"
        ]
        source_uid = str(batch_state.get("batch_threshold_source_image", "")).strip()
        if source_uid:
            source_image = next((image for image in panel.images() if str(image.get("uid", "")).strip() == source_uid), None)
            if source_image is not None:
                parts.append(f"Seed image: {source_image.get('source_name', source_uid)}")
        override_value = dict(batch_state.get("example_guided_overrides", {})).get(image_uid)
        if override_value is not None:
            parts.append(f"This image override: {float(override_value):.3f}")
        override_warnings = list(batch_state.get("override_warnings", []))
        if override_warnings:
            parts.append(f"Overrides logged: {len(override_warnings)}")
        return " | ".join(parts)

    def _set_ihc_example_click_mode(self, mode: str | None) -> None:
        normalized = str(mode or "").strip().lower()
        self._ihc_active_example_mode = normalized if normalized in {"positive", "negative"} else ""
        self.data_panel.set_ihc_example_click_mode(self._ihc_active_example_mode or None)
        if self._ihc_active_example_mode:
            self.data_panel.ihc_roi_draw_button.blockSignals(True)
            self.data_panel.ihc_roi_draw_button.setChecked(False)
            self.data_panel.ihc_roi_draw_button.blockSignals(False)
            self.ihc_workspace.set_roi_selection_enabled(False)
        self._refresh_ihc_point_selection_state()

    def _refresh_ihc_point_selection_state(self) -> None:
        point_selection_enabled = (
            self.active_module == "ihc"
            and self.ihc_workspace.current_step == 1
            and bool(self._ihc_active_example_mode)
        )
        self.ihc_workspace.set_point_selection_enabled(point_selection_enabled)

    def _refresh_ihc_measurement_views(
        self,
        panel: IHCPanel,
        measurement: dict | None = None,
        *,
        status_message: str | None = None,
    ) -> None:
        selected_measurement_uuid = str(measurement.get("measurement_uuid", "")).strip() if measurement is not None else None
        self.data_panel.populate_ihc_measurements(
            list(panel.analysis.get("ihc_measurements", [])),
            selected_measurement_uuid=selected_measurement_uuid,
        )
        self.data_panel.populate_ihc_stats_controls(list(panel.analysis.get("ihc_measurements", [])))
        if not panel.analysis.get('ihc_statistics'):
            self.data_panel.populate_ihc_stats_results({})
            self.data_panel.ihc_stats_summary_label.setText(tr('statistics_recalculate_required'))
            self._refresh_ihc_stats_chart({})
        self._refresh_ihc_preview(panel, measurement_uuid=selected_measurement_uuid)
        self._update_ihc_measurement_status(measurement)
        self._update_ihc_qc_status(measurement)
        self._set_ihc_measurement_summary(measurement)
        self._refresh_result_freshness_status()
        self._update_ihc_assisted_ui_state(panel)
        self.ihc_workspace.canvas.show_overlay_preview()
        if status_message:
            self.statusBar().showMessage(status_message, 3000)

    def _refresh_ihc_preview(
        self,
        panel: IHCPanel,
        measurement_uuid: str | None = None,
        preview: QImage | None = None,
    ) -> None:
        self.ihc_workspace.set_panel(panel)
        canvas = self.ihc_workspace.canvas
        active_image = panel.active_image()
        if active_image is None:
            canvas.set_module("ihc", localized_module_placeholder("ihc")["canvas"])
            canvas.clear_roi_rect()
            self.ihc_workspace.set_example_markers([])
            self.data_panel.ihc_roi_status_label.setText(tr("roi_whole_image"))
            self.status_label.setText(localized_module_placeholder("ihc")["status"])
            self._refresh_ihc_point_selection_state()
            return
        image_uid = str(active_image.get("uid", ""))
        example_markers = self._ihc_example_samples_by_image.get(image_uid, [])
        if str(self.data_panel.current_ihc_guided_mode() or "").strip().lower() != "assisted":
            example_markers = []
        self.ihc_workspace.set_example_markers(example_markers)
        roi_rect = None
        if measurement_uuid is not None:
            measurement = panel.measurement_by_uuid(measurement_uuid)
            if measurement is not None:
                roi_rect = self._ihc_roi_rect_from_snapshot(dict(measurement.get("roi", {})))
        if roi_rect is None:
            roi_rect = self._ihc_roi_rect_from_snapshot(panel.active_roi(image_uid))
        if roi_rect is not None:
            self.data_panel.ihc_roi_status_label.setText(
                trf("roi_rectangle", x=roi_rect.x(), y=roi_rect.y(), w=roi_rect.width(), h=roi_rect.height())
            )
        else:
            self.data_panel.ihc_roi_status_label.setText(tr("roi_whole_image"))
        roi_selection_enabled = (
            self.ihc_workspace.current_step == 1
            and self.data_panel.ihc_roi_draw_button.isChecked()
            and not self._ihc_active_example_mode
        )
        bind = getattr(canvas, "bind_preview_image", None)
        if callable(bind):
            bind(image_uid)
        has_original = bool(getattr(canvas, "has_original_pixmap", lambda: False)())
        if preview is not None:
            canvas.set_pixmap(
                QPixmap.fromImage(preview),
                title=tr("histology_ihc_preview"),
                body=str(active_image.get("source_name", "")),
                enable_roi_selection=roi_selection_enabled,
                is_overlay=False,
            )
            has_original = True
        elif not has_original:
            canvas.set_pixmap(
                QPixmap.fromImage(panel.source_qimage(image_uid)),
                title=tr("histology_ihc_preview"),
                body=str(active_image.get("source_name", "")),
                enable_roi_selection=roi_selection_enabled,
                is_overlay=False,
            )
            has_original = True
        if measurement_uuid is not None:
            overlay = panel.render_measurement_overlay(
                image_uid=image_uid,
                measurement_uuid=measurement_uuid,
            )
            overlay_image = QImage(
                overlay.data,
                overlay.shape[1],
                overlay.shape[0],
                overlay.strides[0],
                QImage.Format.Format_RGB888,
            ).copy()
            canvas.set_pixmap(
                QPixmap.fromImage(overlay_image),
                title=tr("histology_ihc_analysis_overlay"),
                body=str(active_image.get("source_name", "")),
                enable_roi_selection=roi_selection_enabled,
                is_overlay=True,
            )
            canvas.set_roi_rect(roi_rect)
            self._refresh_ihc_point_selection_state()
            self.status_label.setText(trf("histology_ihc_analyzed", name=active_image.get("source_name", "")))
            return
        clearer = getattr(canvas, "clear_overlay_pixmap", None)
        if callable(clearer) and preview is None:
            clearer()
        canvas.set_roi_rect(roi_rect)
        self._refresh_ihc_point_selection_state()
        self.status_label.setText(trf("histology_ihc_ready", name=active_image.get("source_name", "")))

    def _ihc_roi_rect_from_snapshot(self, roi_snapshot: dict | None) -> QRect | None:
        if not isinstance(roi_snapshot, dict):
            return None
        if str(roi_snapshot.get("mode", "whole_image")) == "whole_image":
            return None
        items = list(roi_snapshot.get("items", []))
        active_uuid = str(roi_snapshot.get("active_item_uuid", "")).strip()
        roi_item = next((dict(item) for item in items if str(item.get("roi_uuid", "")).strip() == active_uuid), None)
        if roi_item is None and items:
            roi_item = dict(items[-1])
        if not roi_item or str(roi_item.get("type", "")) != "rectangle":
            return None
        coords = dict(roi_item.get("coordinates_px", {}))
        return QRect(
            int(coords.get("x", 0)),
            int(coords.get("y", 0)),
            int(coords.get("w", 0)),
            int(coords.get("h", 0)),
        )

    def _refresh_ihc_stats_chart(self, result: dict) -> None:
        layout = self.data_panel.ihc_stats_chart_layout
        for index in reversed(range(layout.count())):
            widget = layout.itemAt(index).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if not result.get("plot_data"):
            return
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

            panel = self._active_ihc_panel()
            figure = build_group_statistics_figure(
                result,
                style=resolve_statistics_plot_style(str(panel.style.get("preset", "Nature"))),
                y_label=str(result.get("metric", "Value")),
            )
            chart = FigureCanvasQTAgg(figure)
            chart.setMinimumHeight(200)
            chart.setMaximumHeight(240)
            chart.setStyleSheet("background: white;")
            layout.addWidget(chart)
            chart.draw()
        except Exception:
            return

    def set_ihc_workflow_step(self, step_index: int) -> None:
        if self.active_module != "ihc":
            return
        panel = self._active_ihc_panel()
        normalized = max(0, min(int(step_index), len(self.data_panel.ihc_step_buttons) - 1))
        panel.data["active_step"] = normalized
        self.data_panel.set_ihc_step(normalized, emit_signal=False)
        self.ihc_workspace.set_step(normalized)
        measurement = self._active_ihc_measurement(panel)
        if normalized == 3:
            if measurement is not None or panel.active_image() is not None:
                self.show_ihc_layout()
                return
            self.ihc_workspace.show_preview()
        self.ihc_workspace.show_preview()
        roi_selection_enabled = normalized == 1 and self.data_panel.ihc_roi_draw_button.isChecked() and not self._ihc_active_example_mode
        self.ihc_workspace.set_roi_selection_enabled(roi_selection_enabled)
        self._refresh_ihc_point_selection_state()
        if normalized > 0 and measurement is not None:
            self._refresh_ihc_preview(panel, measurement_uuid=str(measurement.get("measurement_uuid", "")))
            self._sync_chart_kind_controls()
            return
        self._refresh_ihc_preview(panel)
        self._sync_chart_kind_controls()

    def select_ihc_image(self, image_uid: str) -> None:
        if self.active_module != "ihc":
            return
        if hasattr(self, "analysis_state"):
            self.analysis_state.capture_outgoing("ihc")
        panel = self._active_ihc_panel()
        panel.set_active_image(image_uid)
        self._sync_ihc_controls_from_panel(panel)
        self.data_panel.populate_ihc_import_controls(panel.images(), image_uid)
        self._refresh_ihc_preview(panel)
        measurement = self._active_ihc_measurement(panel)
        self._update_ihc_measurement_status(measurement)
        self._update_ihc_qc_status(measurement)
        self._set_ihc_measurement_summary(measurement)
        self._update_ihc_assisted_ui_state(panel)
        if hasattr(self, "analysis_state"):
            self.analysis_state.restore_incoming("ihc")
        self._refresh_result_freshness_status()

    def select_ihc_measurement_result(self, measurement_uuid: str) -> None:
        if self.active_module != "ihc":
            return
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return
        if hasattr(self, "analysis_state"):
            self.analysis_state.capture_outgoing("ihc")
        panel = self._active_ihc_panel()
        measurement = panel.measurement_by_uuid(target_uuid)
        if measurement is None:
            return
        image_uid = str(measurement.get("image_uid", "")).strip()
        if image_uid:
            panel.set_active_image(image_uid)
            panel.apply_roi_snapshot(image_uid, dict(measurement.get("roi", {})) or None)
            self.data_panel.populate_ihc_import_controls(panel.images(), image_uid)
        self._refresh_ihc_preview(panel, measurement_uuid=target_uuid)
        self._update_ihc_measurement_status(measurement)
        self._update_ihc_qc_status(measurement)
        self._set_ihc_measurement_summary(measurement)
        self._update_ihc_assisted_ui_state(panel)
        if hasattr(self, "analysis_state"):
            self.analysis_state.restore_incoming("ihc")
        self._refresh_result_freshness_status()

    def toggle_ihc_roi_selection(self, enabled: bool) -> None:
        if enabled:
            self._set_ihc_example_click_mode(None)
        self.ihc_workspace.set_roi_selection_enabled(self.active_module == "ihc" and self.ihc_workspace.current_step == 1 and enabled)
        if enabled:
            self.data_panel.ihc_roi_status_label.setText(tr("roi_drag_rectangle"))
            return
        panel = self._active_ihc_panel()
        roi_record = panel.active_roi()
        if roi_record is None:
            self.data_panel.ihc_roi_status_label.setText(tr("roi_whole_image"))
        else:
            coords = dict(roi_record.get("coordinates_px", {}))
            self.data_panel.ihc_roi_status_label.setText(
                trf(
                    "roi_rectangle",
                    x=int(coords.get("x", 0)),
                    y=int(coords.get("y", 0)),
                    w=int(coords.get("w", 0)),
                    h=int(coords.get("h", 0)),
                )
            )

    def clear_ihc_roi_selection(self) -> None:
        panel = self._active_ihc_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        panel.clear_image_roi(str(active_image.get("uid", "")))
        self.data_panel.ihc_roi_draw_button.blockSignals(True)
        self.data_panel.ihc_roi_draw_button.setChecked(False)
        self.data_panel.ihc_roi_draw_button.blockSignals(False)
        self.ihc_workspace.clear_roi_rect()
        self.ihc_workspace.set_roi_selection_enabled(False)
        self.data_panel.ihc_roi_status_label.setText(tr("roi_whole_image"))
        self._refresh_ihc_preview(panel)
        self._refresh_result_freshness_status()

    def set_ihc_roi_from_canvas(self, rect: QRect) -> None:
        if self.active_module != "ihc":
            return
        panel = self._active_ihc_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        panel.set_image_roi(
            str(active_image.get("uid", "")),
            "rectangle",
            {"x": rect.x(), "y": rect.y(), "w": rect.width(), "h": rect.height()},
        )
        self.data_panel.ihc_roi_draw_button.blockSignals(True)
        self.data_panel.ihc_roi_draw_button.setChecked(False)
        self.data_panel.ihc_roi_draw_button.blockSignals(False)
        self.ihc_workspace.set_roi_selection_enabled(False)
        self.data_panel.ihc_roi_status_label.setText(
            trf("roi_rectangle", x=rect.x(), y=rect.y(), w=rect.width(), h=rect.height())
        )
        self._refresh_ihc_preview(panel)
        self._refresh_result_freshness_status()

    def toggle_ihc_positive_example_mode(self, enabled: bool) -> None:
        if enabled:
            self._set_ihc_example_click_mode("positive")
        elif self._ihc_active_example_mode == "positive":
            self._set_ihc_example_click_mode(None)

    def toggle_ihc_negative_example_mode(self, enabled: bool) -> None:
        if enabled:
            self._set_ihc_example_click_mode("negative")
        elif self._ihc_active_example_mode == "negative":
            self._set_ihc_example_click_mode(None)

    def stop_ihc_example_sampling(self) -> None:
        self._set_ihc_example_click_mode(None)

    def toggle_ihc_sampling_off_mode(self, enabled: bool) -> None:
        if enabled:
            self.stop_ihc_example_sampling()

    def delete_selected_ihc_example(self) -> None:
        panel = self._active_ihc_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        image_uid = str(active_image.get("uid", "")).strip()
        samples = list(self._ihc_example_samples_by_image.get(image_uid, []))
        index = self.data_panel.selected_ihc_example_index()
        if not samples or index < 0 or index >= len(samples):
            return
        del samples[index]
        if samples:
            self._ihc_example_samples_by_image[image_uid] = samples
        else:
            self._ihc_example_samples_by_image.pop(image_uid, None)
        measurement = self._active_ihc_measurement(panel)
        self._refresh_ihc_measurement_views(
            panel,
            measurement,
            status_message=tr("ihc_sample_list_updated_status"),
        )

    def apply_ihc_example_click(self, point: QPoint) -> None:
        if self.active_module != "ihc" or not self._ihc_active_example_mode:
            return
        panel = self._active_ihc_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        try:
            image_uid = str(active_image.get("uid", "")).strip()
            if not image_uid:
                return
            samples = list(self._ihc_example_samples_by_image.get(image_uid, []))
            samples.append(
                {
                    "kind": self._ihc_active_example_mode,
                    "x": int(point.x()),
                    "y": int(point.y()),
                    "patch_radius": 6,
                }
            )
            self._ihc_example_samples_by_image[image_uid] = samples
            measurement = self._active_ihc_measurement(panel)
            self._refresh_ihc_measurement_views(
                panel,
                measurement,
                status_message=trf(
                    "ihc_example_added_status",
                    kind=self._ihc_active_example_mode,
                ),
            )
        except Exception as exc:
            self._set_ihc_example_click_mode(None)
            log_path = log_exception("Histology / IHC example capture failed", exc)
            QMessageBox.critical(
                self,
                tr("ihc_example_capture_failed_title"),
                trf("error_with_traceback", error=exc, path=log_path),
            )

    def update_ihc_overlay_opacity(self, value: int) -> None:
        self.data_panel.ihc_overlay_opacity_value_label.setText(f"{int(value)}%")
        panel = self._active_ihc_panel()
        guided_state = dict(panel.data.get("guided_state", {}))
        guided_state["overlay_opacity"] = max(0.10, min(1.00, int(value) / 100.0))
        panel.data["guided_state"] = guided_state
        measurement = self._active_ihc_measurement(panel)
        if self.active_module == "ihc" and measurement is not None:
            self._refresh_ihc_preview(panel, measurement_uuid=str(measurement.get("measurement_uuid", "")))

    def try_ihc_assisted_mode(self) -> None:
        self.data_panel.set_ihc_guided_mode("assisted")

    def _handle_ihc_mode_changed(self) -> None:
        if self._syncing_ihc_mode:
            return
        panel = self._active_ihc_panel()
        new_mode = normalize_ihc_mode(self.data_panel.current_ihc_guided_mode())
        guided_state = dict(panel.data.get("guided_state", {}))
        previous_mode = normalize_ihc_mode(guided_state.get("guided_mode"))
        guided_state["guided_mode"] = new_mode
        panel.data["guided_state"] = guided_state
        if previous_mode != new_mode:
            panel.append_audit(
                "ihc_mode_switch",
                f"old_mode={previous_mode}; new_mode={new_mode}; source=ihc_mode_combo",
            )
        self._update_ihc_assisted_ui_state(panel)
        if hasattr(self, "analysis_state"):
            self.analysis_state.capture_outgoing("ihc")
            self.analysis_state.reload_bar("ihc")

    def _handle_brightfield_mode_changed(self) -> None:
        if self._syncing_brightfield_mode:
            return
        panel = self._active_brightfield_panel()
        new_mode = self._normalized_brightfield_mode(self.data_panel.current_brightfield_guided_mode())
        guided_state = panel._merge_guided_state(panel.data.get("guided_state"))
        previous_mode = self._normalized_brightfield_mode(guided_state.get("guided_mode"))
        guided_state["guided_mode"] = new_mode
        panel.data["guided_state"] = guided_state
        if previous_mode != new_mode:
            panel.append_audit(
                "brightfield_mode_switch",
                f"old_mode={previous_mode}; new_mode={new_mode}; source=brightfield_mode_combo",
            )
        panel._transient_preview_state = {}
        self._cancel_brightfield_guided_preview_debounce()
        self._update_brightfield_batch_threshold_ui_state(panel)
        if hasattr(self, "analysis_state"):
            self.analysis_state.capture_outgoing("brightfield")
            self.analysis_state.reload_bar("brightfield")

    def _handle_brightfield_foolproof_sensitivity_changed(self) -> None:
        if self._syncing_brightfield_mode or self.active_module != "brightfield":
            return
        panel = self._active_brightfield_panel()
        guided_state = panel._merge_guided_state(panel.data.get("guided_state"))
        guided_state["foolproof_threshold_bias"] = self.data_panel.current_brightfield_foolproof_threshold_bias()
        panel.data["guided_state"] = guided_state
        panel._transient_preview_state = {}
        if self._should_debounce_brightfield_guided_preview():
            self._queue_brightfield_guided_preview_debounce()

    def _handle_brightfield_assisted_sensitivity_changed(self) -> None:
        if self._syncing_brightfield_mode or self.active_module != "brightfield":
            return
        panel = self._active_brightfield_panel()
        guided_state = panel._merge_guided_state(panel.data.get("guided_state"))
        guided_state["assisted_threshold_bias"] = self.data_panel.current_brightfield_assisted_threshold_bias()
        panel.data["guided_state"] = guided_state
        panel._transient_preview_state = {}
        if self._should_debounce_brightfield_guided_preview():
            self._queue_brightfield_guided_preview_debounce()

    def _handle_brightfield_assisted_small_region_filter_changed(self) -> None:
        if self._syncing_brightfield_mode or self.active_module != "brightfield":
            return
        panel = self._active_brightfield_panel()
        guided_state = panel._merge_guided_state(panel.data.get("guided_state"))
        guided_state["assisted_small_region_filter_bias"] = (
            self.data_panel.current_brightfield_assisted_small_region_filter_bias()
        )
        panel.data["guided_state"] = guided_state
        panel._transient_preview_state = {}
        if self._should_debounce_brightfield_guided_preview():
            self._queue_brightfield_guided_preview_debounce()

    def _queue_brightfield_guided_preview_debounce(self) -> None:
        self._brightfield_preview_debounce_pending = True
        self._brightfield_preview_debounce_timer.start()
        self.data_panel.brightfield_preview_status_label.setText(
            trf(
                "updating_brightfield_overlay_status",
                delay=BRIGHTFIELD_PREVIEW_DEBOUNCE_MS,
            )
        )

    def _cancel_brightfield_guided_preview_debounce(self, *, reset_label: bool = True) -> None:
        self._brightfield_preview_debounce_pending = False
        if self._brightfield_preview_debounce_timer.isActive():
            self._brightfield_preview_debounce_timer.stop()
        if reset_label:
            self.data_panel.brightfield_preview_status_label.setText(
                tr("brightfield_mask_preview_status")
            )

    def _should_debounce_brightfield_guided_preview(self) -> bool:
        if self.active_module != "brightfield":
            return False
        panel = self._active_brightfield_panel()
        if panel.active_image() is None:
            return False
        mode = self._normalized_brightfield_mode(self.data_panel.current_brightfield_guided_mode())
        stain_type = normalize_brightfield_stain_type(self.data_panel.brightfield_stain_type_combo.currentText() or "H&E")
        return mode in {"foolproof", "assisted"} and stain_type == "H&E"

    def _flush_brightfield_preview_debounce(self) -> None:
        if not self._brightfield_preview_debounce_pending:
            return
        self._brightfield_preview_debounce_pending = False
        if not self._should_debounce_brightfield_guided_preview():
            return
        try:
            self.preview_brightfield_segmentation()
        except Exception:
            return

    def _supported_ihc_overlay_export_formats(self) -> set[str]:
        return {bytes(fmt).decode("ascii", errors="ignore").lower() for fmt in QImageWriter.supportedImageFormats()}

    def _build_ihc_overlay_export_dialog_filter(self) -> str:
        filters = ["PNG (*.png)"]
        supported = self._supported_ihc_overlay_export_formats()
        if "tif" in supported or "tiff" in supported:
            filters.append("TIFF (*.tif *.tiff)")
        return ";;".join(filters)

    def _normalize_ihc_overlay_export_target(self, path: str | Path) -> tuple[Path, str]:
        target = Path(path)
        suffix = target.suffix.lower()
        if not suffix:
            target = target.with_suffix(".png")
            suffix = ".png"
        if suffix == ".png":
            return target, "PNG"
        if suffix in {".tif", ".tiff"}:
            supported = self._supported_ihc_overlay_export_formats()
            if "tif" not in supported and "tiff" not in supported:
                raise ValueError("TIFF export is not supported by this Qt image build.")
            return target, "TIFF"
        raise ValueError(f"Unsupported IHC overlay export format: {target.suffix}")

    def _active_ihc_measurement_for_export(self, panel: IHCPanel, *, action_phrase: str) -> dict:
        measurement = self._active_ihc_measurement(panel)
        if measurement is None:
            raise ValueError(f"Run IHC analysis before {action_phrase}.")
        reliability = dict(measurement.get("reliability", {}))
        reliability_status = str(measurement.get("reliability_status") or reliability.get("status", "")).strip().upper()
        if reliability_status == "FAIL":
            detail = f"Current IHC result failed reliability review and cannot be {action_phrase}."
            reliability_messages = [str(message).strip() for message in reliability.get("messages", []) if str(message).strip()]
            if reliability_messages:
                detail += "\n\nQC notes:\n- " + "\n- ".join(reliability_messages)
            raise ValueError(detail)
        return measurement

    def _render_ihc_overlay_image(self, panel: IHCPanel, measurement: dict) -> QImage:
        overlay = panel.render_measurement_overlay(measurement_uuid=str(measurement.get("measurement_uuid", "")))
        return QImage(
            overlay.data,
            overlay.shape[1],
            overlay.shape[0],
            overlay.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()

    def export_ihc_overlay_image(self) -> None:
        panel = self._active_ihc_panel()
        source = self._choose_export_image_source(
            has_original=panel.active_image() is not None,
            has_overlay=self._active_ihc_measurement(panel) is not None,
            default_overlay=self.ihc_workspace.current_preview_mode() == "overlay",
        )
        if source is None:
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("export_image_title"),
            "",
            self._build_ihc_overlay_export_dialog_filter(),
        )
        if not path:
            return
        normalized_path = Path(path)
        if not normalized_path.suffix:
            if "TIFF" in selected_filter.upper():
                normalized_path = normalized_path.with_suffix(".tif")
            else:
                normalized_path = normalized_path.with_suffix(".png")
        try:
            if source == "overlay":
                target = self.export_ihc_overlay_image_to_path(normalized_path)
            else:
                target = self.export_ihc_original_image_to_path(normalized_path)
        except (ValueError, OSError) as exc:
            QMessageBox.information(self, tr("ihc_export_title"), str(exc))
            return
        self.statusBar().showMessage(
            trf("exported_image_status", name=target.name),
            4000,
        )

    def export_ihc_overlay_png(self) -> None:
        self.export_ihc_overlay_image()

    def export_ihc_overlay_image_to_path(self, path: str | Path) -> Path:
        from ui.panels.ihc_workspace import apply_ihc_native_export_scale

        panel = self._active_ihc_panel()
        measurement = self._active_ihc_measurement_for_export(panel, action_phrase="exporting an overlay")
        overlay_image = self._render_ihc_overlay_image(panel, measurement)
        image_uid = str(measurement.get("image_uid", "")).strip()
        overlay_image = apply_ihc_native_export_scale(
            overlay_image,
            panel,
            image_uid,
            committed_scale=panel.measurement_scale_bar(image_uid),
        )
        target, image_format = self._normalize_ihc_overlay_export_target(path)
        if not overlay_image.save(str(target), image_format):
            raise ValueError(f"Could not export IHC overlay image: {target}")
        return target

    def export_ihc_overlay_png_to_path(self, path: str | Path) -> Path:
        return self.export_ihc_overlay_image_to_path(path)

    def export_ihc_original_image_to_path(self, path: str | Path) -> Path:
        from ui.panels.ihc_workspace import apply_ihc_native_export_scale

        panel = self._active_ihc_panel()
        active = panel.active_image()
        if active is None:
            raise ValueError("Import an IHC image before exporting.")
        image_uid = str(active.get("uid", "")).strip()
        image = panel.source_qimage(image_uid)
        image = apply_ihc_native_export_scale(
            image,
            panel,
            image_uid,
            committed_scale=panel.measurement_scale_bar(image_uid),
        )
        target, image_format = self._normalize_ihc_overlay_export_target(path)
        if not image.save(str(target), image_format):
            raise ValueError(f"Could not export IHC image: {target}")
        return target

    def export_ihc_results_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_ihc_results_csv_title"),
            "",
            "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.export_ihc_results_csv_to_path(path)
        self.statusBar().showMessage(
            trf("exported_ihc_results_csv_status", name=Path(path).name),
            4000,
        )

    def export_ihc_results_csv_to_path(self, path: str | Path) -> Path:
        panel = self._active_ihc_panel()
        target = Path(path)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "Image",
                    "Group",
                    "Measurement UUID",
                    "Positive Area %",
                    "Mean DAB Intensity",
                    "H-score",
                    "Reliability",
                    "Include In Statistics",
                    "Threshold Source",
                    "Positive Threshold",
                    "Overlay Generated",
                    "Reliability Messages",
                    "QC Messages",
                    "Warnings",
                    "Example Guided Metadata",
                ]
            )
            for measurement in panel.analysis.get("ihc_measurements", []):
                results = dict(measurement.get("results", {}))
                reliability = dict(measurement.get("reliability", {}))
                example_guided_result = measurement.get("example_guided_result")
                writer.writerow(
                    [
                        str(measurement.get("image_name", "")),
                        str(measurement.get("group", "")),
                        str(measurement.get("measurement_uuid", "")),
                        f"{float(results.get('positive_area_percent', 0.0)):.4f}",
                        f"{float(results.get('mean_dab_intensity', 0.0)):.6f}",
                        f"{float(results.get('h_score', 0.0)):.4f}",
                        str(reliability.get("status", "")),
                        "yes" if bool(reliability.get("include_in_statistics", True)) else "no",
                        str(measurement.get("threshold_source", "")),
                        f"{float(dict(measurement.get('params', {})).get('positive_threshold', 0.0)):.6f}",
                        "yes" if bool(measurement.get("overlay_generated", True)) else "no",
                        " | ".join(str(message).strip() for message in reliability.get("messages", []) if str(message).strip()),
                        " | ".join(str(message).strip() for message in measurement.get("qc_messages", []) if str(message).strip()),
                        " | ".join(str(message).strip() for message in measurement.get("warnings", []) if str(message).strip()),
                        json.dumps(example_guided_result, ensure_ascii=False, sort_keys=True) if isinstance(example_guided_result, dict) else "",
                    ]
                )
        return target

    def export_ihc_summary_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_ihc_summary_json_title"),
            "",
            "JSON (*.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        target = self.export_ihc_summary_json_to_path(path)
        self.statusBar().showMessage(
            trf("exported_ihc_summary_json_status", name=target.name),
            4000,
        )

    def export_ihc_summary_json_to_path(self, path: str | Path) -> Path:
        panel = self._active_ihc_panel()
        active_image = panel.active_image()
        active_measurement = self._active_ihc_measurement(panel)
        payload = {
            "panel_id": panel.panel_id,
            "panel_uuid": panel.panel_uuid,
            "active_image_uid": str(active_image.get("uid", "")) if isinstance(active_image, dict) else "",
            "active_measurement_uuid": str(active_measurement.get("measurement_uuid", "")) if isinstance(active_measurement, dict) else "",
            "statistics": dict(panel.analysis.get("ihc_statistics", {})),
            "measurements": [],
        }
        for measurement in panel.analysis.get("ihc_measurements", []):
            results = dict(measurement.get("results", {}))
            reliability = dict(measurement.get("reliability", {}))
            payload["measurements"].append(
                {
                    "image_uid": str(measurement.get("image_uid", "")),
                    "image_name": str(measurement.get("image_name", "")),
                    "group": str(measurement.get("group", "")),
                    "measurement_uuid": str(measurement.get("measurement_uuid", "")),
                    "threshold_source": str(measurement.get("threshold_source", "")),
                    "positive_threshold": float(dict(measurement.get("params", {})).get("positive_threshold", 0.0)),
                    "overlay_generated": bool(measurement.get("overlay_generated", True)),
                    "qc_messages": [str(message).strip() for message in measurement.get("qc_messages", []) if str(message).strip()],
                    "warnings": [str(message).strip() for message in measurement.get("warnings", []) if str(message).strip()],
                    "reliability_status": str(measurement.get("reliability_status") or reliability.get("status", "")),
                    "reliability": reliability,
                    "results": results,
                    "example_guided_result": dict(measurement.get("example_guided_result", {}))
                    if isinstance(measurement.get("example_guided_result"), dict)
                    else None,
                }
            )
        target = Path(path)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def _refresh_brightfield_preview(
        self,
        panel: BrightfieldPanel,
        measurement_uuid: str | None = None,
        preview_settings: dict | None = None,
        preview_payload: dict | None = None,
        preview: QImage | None = None,
    ) -> None:
        if hasattr(self,'myotube_controllers'):
            self.myotube_controllers['brightfield'].sync()
        canvas = self.brightfield_workspace.canvas
        active_image = panel.active_image()
        if active_image is None:
            self.data_panel.populate_bright_round_cell_results(None)
            canvas.set_module("brightfield", localized_module_placeholder("brightfield")["canvas"])
            canvas.clear_roi_rect()
            self.data_panel.brightfield_roi_status_label.setText(tr("roi_whole_image"))
            self.status_label.setText(localized_module_placeholder("brightfield")["status"])
            return
        image_uid = str(active_image.get("uid", ""))
        cell_measurement = next((m for m in reversed(panel.analysis.get("bright_round_cell_diameter_measurements", []))
                                 if m.get("image_uid") == image_uid), None)
        self.data_panel.populate_bright_round_cell_results(cell_measurement)
        cell_key = (panel.panel_uuid, image_uid)
        if getattr(self, "_cell_result_image_key", None) != cell_key:
            self.data_panel.set_cell_analysis_settings((cell_measurement or {}).get("params", {}))
            self._cell_result_image_key = cell_key
        measurement = None
        roi_rect = None
        if measurement_uuid is not None:
            measurement = panel.measurement_by_uuid(measurement_uuid)
            if measurement is not None:
                roi_rect = self._brightfield_roi_rect_from_snapshot(dict(measurement.get("roi", {})))
        if roi_rect is None:
            roi_rect = self._brightfield_roi_rect_from_snapshot(dict(active_image.get("roi", {})))
        if roi_rect is not None:
            self.data_panel.brightfield_roi_status_label.setText(
                trf("roi_rectangle", x=roi_rect.x(), y=roi_rect.y(), w=roi_rect.width(), h=roi_rect.height())
            )
        else:
            self.data_panel.brightfield_roi_status_label.setText(tr("roi_whole_image"))
        roi_selection_enabled = self.brightfield_workspace.current_step == 2 and self.data_panel.brightfield_roi_draw_button.isChecked()
        bind = getattr(canvas, "bind_preview_image", None)
        if callable(bind):
            bind(image_uid)
        has_original = bool(getattr(canvas, "has_original_pixmap", lambda: False)())
        if preview_payload is not None:
            overlay = np.asarray(preview_payload["overlay"], dtype=np.uint8)
            overlay_image = QImage(
                overlay.data,
                overlay.shape[1],
                overlay.shape[0],
                overlay.strides[0],
                QImage.Format.Format_RGB888,
            ).copy()
            canvas.set_display_state(
                QPixmap.fromImage(overlay_image),
                title=tr("brightfield_segmentation_overlay"),
                body=str(active_image.get("source_name", "")),
                roi_rect=roi_rect,
                enable_roi_selection=roi_selection_enabled,
                is_overlay=True,
            )
            self.status_label.setText(trf("brightfield_segmentation_preview", name=active_image.get("source_name", "")))
            return
        if preview is not None:
            canvas.set_display_state(
                QPixmap.fromImage(preview),
                title=tr("brightfield_preview"),
                body=str(active_image.get("source_name", "")),
                roi_rect=roi_rect,
                enable_roi_selection=roi_selection_enabled,
                is_overlay=False,
            )
            has_original = True
        elif not has_original:
            canvas.set_display_state(
                QPixmap.fromImage(panel.source_qimage(image_uid)),
                title=tr("brightfield_preview"),
                body=str(active_image.get("source_name", "")),
                roi_rect=roi_rect,
                enable_roi_selection=roi_selection_enabled,
                is_overlay=False,
            )
            has_original = True
        overlay_applied = False
        if preview_settings is not None:
            overlay = panel.render_segmentation_overlay(image_uid=image_uid, settings=preview_settings)
            overlay_image = QImage(
                overlay.data,
                overlay.shape[1],
                overlay.shape[0],
                overlay.strides[0],
                QImage.Format.Format_RGB888,
            ).copy()
            canvas.set_display_state(
                QPixmap.fromImage(overlay_image),
                title=tr("brightfield_segmentation_overlay"),
                body=str(active_image.get("source_name", "")),
                roi_rect=roi_rect,
                enable_roi_selection=roi_selection_enabled,
                is_overlay=True,
            )
            self.status_label.setText(trf("brightfield_segmentation_preview", name=active_image.get("source_name", "")))
            overlay_applied = True
        elif measurement is not None:
            overlay = panel.render_measurement_overlay(image_uid=image_uid, measurement_uuid=measurement_uuid)
            overlay_image = QImage(
                overlay.data,
                overlay.shape[1],
                overlay.shape[0],
                overlay.strides[0],
                QImage.Format.Format_RGB888,
            ).copy()
            canvas.set_display_state(
                QPixmap.fromImage(overlay_image),
                title=tr("brightfield_measurement_overlay"),
                body=str(active_image.get("source_name", "")),
                roi_rect=roi_rect,
                enable_roi_selection=roi_selection_enabled,
                is_overlay=True,
            )
            self.status_label.setText(trf("brightfield_analyzed", name=active_image.get("source_name", "")))
            overlay_applied = True
        elif cell_measurement is not None and getattr(self.data_panel, "brightfield_measure_type_combo", None) is not None and self.data_panel.brightfield_measure_type_combo.currentData() == "cells":
            overlay = np.asarray(panel.render_bright_round_cell_overlay(cell_measurement), dtype=np.uint8)
            qimage = QImage(overlay.data, overlay.shape[1], overlay.shape[0], overlay.strides[0], QImage.Format.Format_RGB888).copy()
            canvas.set_display_state(
                QPixmap.fromImage(qimage), title=tr("bright_round_cell_overlay"),
                body=str(active_image.get("source_name", "")), roi_rect=roi_rect,
                enable_roi_selection=roi_selection_enabled, is_overlay=True,
            )
            overlay_applied = True
        if not overlay_applied:
            clearer = getattr(canvas, "clear_overlay_pixmap", None)
            if callable(clearer):
                clearer()
            self.status_label.setText(trf("brightfield_ready", name=active_image.get("source_name", "")))
        # A cached original needs the new ROI too; no pixmap replacement occurs
        # on this path, so set_display_state cannot synchronize it for us.
        canvas.set_roi_rect(roi_rect)

    def _update_brightfield_measurement_status(self, measurement: dict | None) -> None:
        if measurement is None:
            self.data_panel.brightfield_reliability_banner.clear(tr("reliability_awaiting_brightfield"))
            self.data_panel.brightfield_measure_status_label.setText(f"{tr('measurement_status')}: {tr('awaiting_measurement')}.")
            return
        status = self._measurement_reliability_status(measurement) or "PASS"
        warnings = self._measurement_warning_messages(measurement)
        results = dict(measurement.get("results", {}))
        segmentation_mode = str(dict(measurement.get("results", {})).get("segmentation_mode", "")).strip() or str(
            dict(measurement.get("params", {})).get("segmentation_mode", "")
        ).strip() or "hsv"
        text = f"{tr('measurement_status')}: {status} | {tr('mode_label')}: {segmentation_mode}"
        if warnings:
            text += "\n" + tr("warnings") + ": " + "; ".join(warnings)
        self.data_panel.brightfield_reliability_banner.set_status(
            status,
            warnings,
            metrics=[
                (tr("area_fraction"), self._format_metric_float(results.get("area_fraction_percent"), 2, "%")),
                (BRIGHTFIELD_SEGMENTED_REGIONS_LABEL, self._format_metric_int(results.get("object_count"))),
                (tr("mean_object_area"), self._format_metric_float(results.get("mean_object_area_px"), 2, " px²")),
            ],
        )
        self.data_panel.brightfield_measure_status_label.setText(text)

    def _update_brightfield_qc_status(self, measurement: dict | None) -> None:
        if measurement is None:
            self.data_panel.brightfield_qc_status_label.setText(tr("qc_awaiting_measurement"))
            return
        status = self._measurement_reliability_status(measurement) or "PASS"
        qc_messages = [str(message).strip() for message in measurement.get("qc_messages", []) if str(message).strip()]
        text = f"{tr('qc')}: {status}"
        if qc_messages:
            text += " | " + "; ".join(qc_messages)
        self.data_panel.brightfield_qc_status_label.setText(text)

    def _set_ihc_measurement_summary(self, measurement: dict | None) -> None:
        if measurement is None:
            self.data_panel.ihc_summary_label.setText(tr("no_ihc_measurements"))
            return
        results = dict(measurement.get("results", {}))
        status = self._measurement_reliability_status(measurement) or "PASS"
        self.data_panel.ihc_summary_label.setText(
            trf(
                "latest_ihc_result_summary",
                status=status,
                image=measurement.get("image_name", ""),
                positive_area=float(results.get("positive_area_percent", 0.0)),
                mean_dab=float(results.get("mean_dab_intensity", 0.0)),
                h_score=float(results.get("h_score", 0.0)),
            )
        )

    def _set_brightfield_measurement_summary(self, measurement: dict | None) -> None:
        if measurement is None:
            self.data_panel.brightfield_summary_label.setText(tr("no_brightfield_measurements"))
            return
        results = dict(measurement.get("results", {}))
        status = self._measurement_reliability_status(measurement) or "PASS"
        self.data_panel.brightfield_summary_label.setText(
            trf(
                "latest_brightfield_result_summary",
                status=status,
                image=measurement.get("image_name", ""),
                area_fraction=float(results.get("area_fraction_percent", 0.0)),
                regions_label=BRIGHTFIELD_SEGMENTED_REGIONS_LABEL,
                object_count=int(results.get("object_count", 0)),
                mean_area=float(results.get("mean_object_area_px", 0.0)),
                roi_mean=float(results.get("roi_mean_intensity", 0.0)),
            )
        )

    def _refresh_brightfield_measurement_views(
        self,
        panel: BrightfieldPanel,
        measurement: dict | None = None,
        *,
        status_message: str | None = None,
        preview_payload: dict | None = None,
    ) -> None:
        self._cancel_brightfield_guided_preview_debounce()
        selected_measurement_uuid = str(measurement.get("measurement_uuid", "")).strip() if measurement is not None else None
        self.data_panel.populate_brightfield_measurements(
            list(panel.analysis.get("brightfield_measurements", [])),
            selected_measurement_uuid=selected_measurement_uuid,
        )
        self._sync_brightfield_stats_controls(panel)
        self._refresh_brightfield_ai_views(panel)
        self._restore_brightfield_statistics_view(panel)
        self._refresh_brightfield_preview(
            panel,
            measurement_uuid=selected_measurement_uuid,
            preview_payload=preview_payload,
        )
        self._update_brightfield_measurement_status(measurement)
        self._update_brightfield_qc_status(measurement)
        self._set_brightfield_measurement_summary(measurement)
        self._refresh_result_freshness_status()
        self._update_brightfield_batch_threshold_ui_state(panel, measurement)
        self._update_brightfield_he_fiber_calibration_status(panel)
        if status_message:
            self.statusBar().showMessage(status_message, 3000)

    def _show_brightfield_ai_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def _refresh_brightfield_ai_views(self, panel: BrightfieldPanel, selected_measurement_uuid: str | None = None) -> None:
        measurements = list(panel.analysis.get("brightfield_ai_object_count_measurements", []))
        self.data_panel.populate_brightfield_ai_measurements(measurements, selected_measurement_uuid)
        ai_statistics = summarize_brightfield_ai_object_count_measurements(panel)
        panel.analysis["brightfield_ai_object_count_statistics"] = ai_statistics
        self.data_panel.populate_brightfield_ai_statistics_results(ai_statistics)
        if self.active_module == "brightfield" and self.data_panel.brightfield_stats_plot_source() == "ai_object_count":
            self._restore_brightfield_statistics_view(panel)

    def _refresh_brightfield_after_ai_measurement(self, measurement: dict) -> None:
        if isinstance(measurement, dict) and saved_inputs_from_record(measurement) is None:
            attach_analysis_inputs(measurement, self._current_analysis_inputs("brightfield_fiber"))
        panel = self._active_brightfield_panel()
        self._refresh_brightfield_ai_views(panel,selected_measurement_uuid=str(measurement.get('measurement_uuid','')))
        self._sync_brightfield_stats_controls(panel)
        self._refresh_brightfield_preview(panel, measurement_uuid=str(measurement.get("measurement_uuid", "")))
        self._update_brightfield_he_fiber_calibration_status(panel)
        warnings = list(measurement.get("warnings", []))
        warning_lines = "\n".join(f"- {warning}" for warning in warnings) if warnings else f"- {tr('none')}"
        QMessageBox.information(
            self,
            tr("he_fiber_area"),
            trf(
                "he_fiber_result_summary",
                count=int(measurement.get("results", {}).get("ai_object_count", 0)),
                mean_area=float(
                    measurement.get("results", {}).get("ai_object_mean_area_px", 0.0) or 0.0
                ),
                warnings=warning_lines,
            ),
        )
        self.statusBar().showMessage(tr("he_fiber_complete_status"), 3000)

    def _brightfield_statistics_source(self) -> str:
        return self.data_panel.brightfield_stats_plot_source()

    def _brightfield_stat_measurements_for_source(self, panel: BrightfieldPanel, source: str) -> list[dict]:
        if source == "ai_object_count":
            return list(panel.analysis.get("brightfield_ai_object_count_measurements", []))
        return list(panel.analysis.get("brightfield_measurements", []))

    def _brightfield_statistics_result_for_source(self, panel: BrightfieldPanel, source: str) -> dict:
        if source == "ai_object_count":
            return dict(panel.analysis.get("brightfield_ai_object_count_plot_statistics", {}))
        return dict(panel.analysis.get("brightfield_statistics", {}))

    def _clear_brightfield_statistics_view(self, source: str) -> None:
        self.data_panel.brightfield_stats_result_table.setRowCount(0)
        self.data_panel.brightfield_stats_summary_label.setText(
            "H&E Fiber Area statistics | No statistics yet."
            if source == "ai_object_count"
            else "Traditional Brightfield statistics | No statistics yet."
        )
        self._refresh_brightfield_stats_chart({})

    def _restore_brightfield_statistics_view(self, panel: BrightfieldPanel) -> None:
        source = self._brightfield_statistics_source()
        result = self._brightfield_statistics_result_for_source(panel, source)
        if result:
            self.data_panel.populate_brightfield_stats_results(result)
            self._refresh_brightfield_stats_chart(result)
            return
        self._clear_brightfield_statistics_view(source)

    def _sync_brightfield_stats_controls(self, panel: BrightfieldPanel) -> None:
        source = self._brightfield_statistics_source()
        self.data_panel.brightfield_stats_metric_combo.blockSignals(True)
        if source == "ai_object_count":
            metric_index = self.data_panel.brightfield_stats_metric_combo.findData(BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL)
            if metric_index >= 0:
                self.data_panel.brightfield_stats_metric_combo.setCurrentIndex(metric_index)
        self.data_panel.brightfield_stats_metric_combo.setEnabled(source != "ai_object_count")
        self.data_panel.brightfield_stats_metric_combo.blockSignals(False)
        self.data_panel.populate_brightfield_stats_controls(
            self._brightfield_stat_measurements_for_source(panel, source),
            source=source,
        )

    def refresh_brightfield_stats_plot_source(self, *_args) -> None:
        if self.active_module != "brightfield":
            return
        panel = self._active_brightfield_panel()
        self._sync_brightfield_stats_controls(panel)
        self._restore_brightfield_statistics_view(panel)

    def refresh_brightfield_stats_chart(self, *_args) -> None:
        if self.active_module != "brightfield":
            return
        self._restore_brightfield_statistics_view(self._active_brightfield_panel())

    def _format_brightfield_batch_threshold_status(self, panel: BrightfieldPanel, measurement: dict | None) -> str:
        guided_state = panel._merge_guided_state(panel.data.get("guided_state"))
        batch_state = dict(guided_state.get("batch_threshold_state", {}))
        threshold = batch_state.get("batch_threshold")
        if threshold is None:
            base_text = "Batch threshold: waiting for Lock."
        else:
            base_text = f"Batch threshold: locked {float(threshold):.3f} (LAB a_min)"
            source_uid = str(batch_state.get("batch_threshold_source_image", "")).strip()
            if source_uid:
                source_image = next((image for image in panel.images() if str(image.get("uid", "")).strip() == source_uid), None)
                if source_image is not None:
                    base_text += f" | Seed image: {source_image.get('source_name', source_uid)}"
        if measurement is None:
            return f"{base_text} | Select or run an H&E LAB-mode measurement for the active image."
        if not panel.measurement_supports_batch_threshold(measurement):
            return f"{base_text} | Lock requires an H&E LAB-mode measurement for the active image."
        return base_text

    def _update_brightfield_batch_threshold_ui_state(
        self,
        panel: BrightfieldPanel,
        measurement: dict | None = None,
    ) -> None:
        active_image = panel.active_image()
        active_image_uid = str(active_image.get("uid", "")).strip() if active_image is not None else ""
        target_measurement = measurement
        if target_measurement is None and active_image is not None:
            target_measurement = self._active_brightfield_measurement(panel)
        is_compatible = (
            target_measurement is not None
            and bool(active_image_uid)
            and str(target_measurement.get("image_uid", "")).strip() == active_image_uid
            and panel.measurement_supports_batch_threshold(target_measurement)
        )
        batch_state = dict(panel._merge_guided_state(panel.data.get("guided_state")).get("batch_threshold_state", {}))
        self.data_panel.brightfield_batch_threshold_status_label.setText(
            self._format_brightfield_batch_threshold_status(panel, target_measurement)
        )
        self.data_panel.set_brightfield_foolproof_sensitivity_enabled(batch_state.get("batch_threshold") is None)
        self.data_panel.set_brightfield_assisted_sensitivity_enabled(batch_state.get("batch_threshold") is None)
        self.data_panel.set_brightfield_assisted_small_region_filter_enabled(True)
        self.data_panel.set_brightfield_batch_threshold_action_state(
            can_lock=is_compatible,
            can_unlock=batch_state.get("batch_threshold") is not None,
        )

    def _brightfield_roi_rect_from_snapshot(self, roi_snapshot: dict | None) -> QRect | None:
        if not isinstance(roi_snapshot, dict):
            return None
        if str(roi_snapshot.get("type", "")) == "rectangle" and "coordinates_px" in roi_snapshot:
            coords = dict(roi_snapshot.get("coordinates_px", {}))
            return QRect(
                int(coords.get("x", 0)),
                int(coords.get("y", 0)),
                int(coords.get("w", 0)),
                int(coords.get("h", 0)),
            )
        if str(roi_snapshot.get("mode", "whole_image")) == "whole_image":
            return None
        items = list(roi_snapshot.get("items", []))
        active_uuid = str(roi_snapshot.get("active_item_uuid", "")).strip()
        roi_item = next((dict(item) for item in items if str(item.get("roi_uuid", "")).strip() == active_uuid), None)
        if roi_item is None and items:
            roi_item = dict(items[-1])
        if not roi_item or str(roi_item.get("type", "")) != "rectangle":
            return None
        coords = dict(roi_item.get("coordinates_px", {}))
        return QRect(
            int(coords.get("x", 0)),
            int(coords.get("y", 0)),
            int(coords.get("w", 0)),
            int(coords.get("h", 0)),
        )

    def _brightfield_chart_export_payload(self, panel: BrightfieldPanel, result: dict) -> dict:
        source = self._brightfield_statistics_source()
        chart_mode = str(self.data_panel.brightfield_stats_chart_mode_combo.currentData() or "summary")
        if source == "ai_object_count" and chart_mode in {"frequency_bar", "frequency_line"}:
            distribution_result = self._current_brightfield_ai_distribution_result()
            if not distribution_result.get("series"):
                return {}
            unit = str(distribution_result.get("unit", "px^2") or "px^2")
            return {
                "source": source,
                "chart_mode": chart_mode,
                "frequency_mode": str(self.data_panel.brightfield_stats_frequency_mode_combo.currentData() or "absolute"),
                "distribution_result": distribution_result,
                "metric": "H&E Fiber Area Distribution",
                "unit": unit,
            }
        if not result.get("plot_data"):
            return {}
        return {
            "source": source,
            "chart_mode": "summary",
            "statistics_result": deepcopy(result),
            "metric": str(result.get("metric", "Value")),
        }

    def _build_brightfield_chart_figure(
        self,
        panel: BrightfieldPanel,
        payload: dict,
        chart_kind: str | None = None,
    ):
        style = resolve_statistics_plot_style(str(panel.style.get("preset", "Nature")))
        chart_mode = str(payload.get("chart_mode", "summary"))
        if chart_mode in {"frequency_bar", "frequency_line"}:
            unit = str(payload.get("unit", "px^2") or "px^2")
            return build_distribution_comparison_figure(
                dict(payload.get("distribution_result", {})),
                style=style,
                plot_kind="bar" if chart_mode == "frequency_bar" else "line",
                x_label=f"Fiber area ({display_unit(unit)})",
                frequency_mode=str(payload.get("frequency_mode", "absolute")),
                chart_kind=chart_kind,
            )
        statistics_result = dict(payload.get("statistics_result", {}))
        return build_group_statistics_figure(
            statistics_result,
            style=style,
            y_label=str(payload.get("metric", statistics_result.get("metric", "Value"))),
            chart_kind=chart_kind,
        )

    def _refresh_brightfield_stats_chart(self, result: dict) -> None:
        layout = self.data_panel.brightfield_stats_chart_layout
        for index in reversed(range(layout.count())):
            widget = layout.itemAt(index).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        panel = self._active_brightfield_panel()
        payload = self._brightfield_chart_export_payload(panel, result)
        if not payload:
            return
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

            panel.analysis["brightfield_chart_export"] = deepcopy(payload)
            figure = self._build_brightfield_chart_figure(panel, payload)
            chart = FigureCanvasQTAgg(figure)
            chart.setMinimumHeight(200)
            chart.setMaximumHeight(240)
            chart.setStyleSheet("background: white;")
            layout.addWidget(chart)
            chart.draw()
        except Exception:
            return

    def _current_brightfield_ai_distribution_result(self) -> dict:
        panel = self._active_brightfield_panel()
        rows = collect_brightfield_ai_object_area_distribution_rows(panel)
        if not rows:
            return {}
        assignments = self.data_panel.brightfield_stats_group_assignments()
        series = []
        for row in rows:
            sample_key = str(row.get("sample_key", ""))
            if assignments and sample_key not in assignments:
                continue
            series.append({**row, "group": assignments.get(sample_key, str(row.get("group", "Ungrouped")))})
        unit = str(series[0].get("unit", "px^2") if series else "px^2")
        metric = "he_fiber_area_distribution_um2" if unit == "um^2" else "he_fiber_area_distribution_px"
        return {"source": "brightfield_ai_object_count", "metric": metric, "unit": unit, "series": series}

    def set_brightfield_workflow_step(self, step_index: int) -> None:
        if self.active_module != "brightfield":
            return
        panel = self._active_brightfield_panel()
        normalized = max(0, min(int(step_index), len(self.data_panel.brightfield_step_buttons) - 1))
        panel.data["active_step"] = normalized
        self.data_panel.set_brightfield_step(normalized, emit_signal=False)
        self.brightfield_workspace.set_step(normalized)
        measurement = self._active_brightfield_measurement(panel)
        if normalized == 4:
            if measurement is not None or panel.active_image() is not None:
                self.show_brightfield_layout()
                return
            self.brightfield_workspace.show_preview()
        self.brightfield_workspace.show_preview()
        roi_selection_enabled = normalized == 2 and self.data_panel.brightfield_roi_draw_button.isChecked()
        self.brightfield_workspace.set_roi_selection_enabled(roi_selection_enabled)
        if normalized > 1 and measurement is not None:
            self._refresh_brightfield_preview(panel, measurement_uuid=str(measurement.get("measurement_uuid", "")))
            self._sync_chart_kind_controls()
            return
        self._refresh_brightfield_preview(panel)
        self._sync_chart_kind_controls()

    def select_brightfield_image(self, image_uid: str) -> None:
        if self.active_module != "brightfield":
            return
        if hasattr(self, "analysis_state"):
            self.analysis_state.capture_outgoing("brightfield")
        panel = self._active_brightfield_panel()
        self._cancel_brightfield_guided_preview_debounce()
        image = panel.set_active_image(image_uid)
        panel._transient_preview_state = {}
        self._sync_brightfield_controls_from_panel(panel)
        self.data_panel.populate_brightfield_import_controls(panel.images(), image_uid)
        self.data_panel.brightfield_group_edit.setText(str(image.get("group", "Ungrouped")))
        self.data_panel.brightfield_stain_type_combo.setCurrentText(str(image.get("stain_type", "H&E")))
        self._refresh_brightfield_preview(panel)
        measurement = self._active_brightfield_measurement(panel)
        self._update_brightfield_measurement_status(measurement)
        self._update_brightfield_qc_status(measurement)
        self._set_brightfield_measurement_summary(measurement)
        self._update_brightfield_batch_threshold_ui_state(panel, measurement)
        self._update_brightfield_he_fiber_calibration_status(panel)
        if hasattr(self, "analysis_state"):
            self.analysis_state.restore_incoming("brightfield")
        self._refresh_result_freshness_status()

    def select_brightfield_measurement_result(self, measurement_uuid: str) -> None:
        if self.active_module != "brightfield":
            return
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return
        if hasattr(self, "analysis_state"):
            self.analysis_state.capture_outgoing("brightfield")
        panel = self._active_brightfield_panel()
        measurement = panel.measurement_by_uuid(target_uuid)
        if measurement is None:
            return
        self._sync_brightfield_controls_from_panel(panel)
        image_uid = str(measurement.get("image_uid", "")).strip()
        if image_uid:
            panel.set_active_image(image_uid)
            panel.apply_roi_snapshot(image_uid, dict(measurement.get("roi", {})) or None)
            self.data_panel.populate_brightfield_import_controls(panel.images(), image_uid)
        self._refresh_brightfield_preview(panel, measurement_uuid=target_uuid)
        self._update_brightfield_measurement_status(measurement)
        self._update_brightfield_qc_status(measurement)
        self._set_brightfield_measurement_summary(measurement)
        self._update_brightfield_batch_threshold_ui_state(panel, measurement)
        self.brightfield_workspace.canvas.show_overlay_preview()
        if hasattr(self, "analysis_state"):
            self.analysis_state.restore_incoming("brightfield")

    def select_brightfield_ai_measurement_result(self, measurement_uuid: str) -> None:
        if self.active_module != "brightfield":
            return
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return
        if hasattr(self, "analysis_state"):
            self.analysis_state.capture_outgoing("brightfield")
        panel = self._active_brightfield_panel()
        measurement = panel.measurement_by_uuid(target_uuid)
        if measurement is None:
            return
        image_uid = str(measurement.get("image_uid", "")).strip()
        if image_uid:
            panel.set_active_image(image_uid)
            panel.apply_roi_snapshot(image_uid, dict(measurement.get("roi", {})) or None)
            self.data_panel.populate_brightfield_import_controls(panel.images(), image_uid)
        self._refresh_brightfield_ai_views(panel, selected_measurement_uuid=target_uuid)
        self._refresh_brightfield_preview(panel, measurement_uuid=target_uuid)
        self._update_brightfield_he_fiber_calibration_status(panel)
        self.brightfield_workspace.canvas.show_overlay_preview()
        if hasattr(self, "analysis_state"):
            self.analysis_state.restore_incoming("brightfield")

    def toggle_brightfield_roi_selection(self, enabled: bool) -> None:
        self.brightfield_workspace.set_roi_selection_enabled(
            self.active_module == "brightfield" and self.brightfield_workspace.current_step == 2 and enabled
        )
        if enabled:
            self.data_panel.brightfield_roi_status_label.setText(tr("roi_drag_measurement"))
            return
        panel = self._active_brightfield_panel()
        roi_record = panel.active_roi()
        if roi_record is None:
            self.data_panel.brightfield_roi_status_label.setText(tr("roi_whole_image"))
        else:
            coords = dict(roi_record.get("coordinates_px", {}))
            self.data_panel.brightfield_roi_status_label.setText(
                trf(
                    "roi_rectangle",
                    x=int(coords.get("x", 0)),
                    y=int(coords.get("y", 0)),
                    w=int(coords.get("w", 0)),
                    h=int(coords.get("h", 0)),
                )
            )

    def clear_brightfield_roi_selection(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        self._cancel_brightfield_guided_preview_debounce()
        panel._transient_preview_state = {}
        panel.clear_image_roi(str(active_image.get("uid", "")))
        self.data_panel.brightfield_roi_draw_button.blockSignals(True)
        self.data_panel.brightfield_roi_draw_button.setChecked(False)
        self.data_panel.brightfield_roi_draw_button.blockSignals(False)
        self.brightfield_workspace.clear_roi_rect()
        self.brightfield_workspace.set_roi_selection_enabled(False)
        self.data_panel.brightfield_roi_status_label.setText(tr("roi_whole_image"))
        self._refresh_brightfield_preview(panel)
        self._refresh_result_freshness_status()

    def set_brightfield_roi_from_canvas(self, rect: QRect) -> None:
        if self.active_module != "brightfield":
            return
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        self._cancel_brightfield_guided_preview_debounce()
        panel._transient_preview_state = {}
        panel.set_image_roi(
            str(active_image.get("uid", "")),
            "rectangle",
            {"x": rect.x(), "y": rect.y(), "w": rect.width(), "h": rect.height()},
        )
        self.data_panel.brightfield_roi_draw_button.blockSignals(True)
        self.data_panel.brightfield_roi_draw_button.setChecked(False)
        self.data_panel.brightfield_roi_draw_button.blockSignals(False)
        self.brightfield_workspace.set_roi_selection_enabled(False)
        self.data_panel.brightfield_roi_status_label.setText(
            trf("roi_rectangle", x=rect.x(), y=rect.y(), w=rect.width(), h=rect.height())
        )
        self._refresh_brightfield_preview(panel)
        self._refresh_result_freshness_status()

    def update_brightfield_image_metadata(self) -> None:
        panel = self._active_brightfield_panel()
        active_image = panel.active_image()
        if active_image is None:
            return
        self._cancel_brightfield_guided_preview_debounce()
        panel._transient_preview_state = {}
        image_uid = str(active_image.get("uid", ""))
        panel.set_image_group(image_uid, self.data_panel.brightfield_group_edit.text())
        panel.set_image_stain_type(image_uid, self.data_panel.brightfield_stain_type_combo.currentText())
        self.data_panel.populate_brightfield_import_controls(panel.images(), image_uid)
        self.data_panel.populate_brightfield_measurements(list(panel.analysis.get("brightfield_measurements", [])))
        self._sync_brightfield_stats_controls(panel)
        self._update_brightfield_batch_threshold_ui_state(panel)
        self._update_brightfield_he_fiber_calibration_status(panel)

    def lock_brightfield_batch_threshold(self) -> None:
        panel = self._active_brightfield_panel()
        measurement = self._active_brightfield_measurement(panel)
        if measurement is None or not panel.measurement_supports_batch_threshold(measurement):
            QMessageBox.information(
                self,
                tr("v9_module_brightfield"),
                tr("brightfield_batch_lock_hint"),
            )
            return
        try:
            panel.apply_batch_threshold_from_measurement(str(measurement.get("measurement_uuid", "")), lock=True)
            self._refresh_brightfield_measurement_views(
                panel,
                measurement,
                status_message=tr("brightfield_batch_locked_status"),
            )
        except Exception as exc:
            QMessageBox.information(self, tr("v9_module_brightfield"), str(exc))

    def unlock_brightfield_batch_threshold(self) -> None:
        panel = self._active_brightfield_panel()
        panel.unlock_batch_threshold()
        self._refresh_brightfield_measurement_views(
            panel,
            self._active_brightfield_measurement(panel),
            status_message=tr("brightfield_batch_unlocked_status"),
        )

    def preview_brightfield_segmentation(self) -> None:
        panel = self._active_brightfield_panel()
        if panel.active_image() is None:
            QMessageBox.information(
                self,
                tr("v9_module_brightfield"),
                tr("import_brightfield_before_preview"),
            )
            return
        self._cancel_brightfield_guided_preview_debounce(reset_label=False)
        try:
            mode = self._normalized_brightfield_mode(self.data_panel.current_brightfield_guided_mode())
            stain_type = normalize_brightfield_stain_type(
                self.data_panel.brightfield_stain_type_combo.currentText() or "H&E"
            )
            if mode == "expert":
                self._refresh_brightfield_preview(panel, preview_settings=self.data_panel.brightfield_analysis_settings())
            else:
                preview_settings = (
                    self.data_panel.brightfield_foolproof_analysis_settings()
                    if mode == "foolproof" and stain_type == "H&E"
                    else self.data_panel.brightfield_guided_analysis_settings()
                )
                preview_payload = panel.build_guided_preview(
                    guided_mode=mode,
                    stain_type=stain_type,
                    settings=preview_settings,
                )
                self._refresh_brightfield_preview(panel, preview_payload=preview_payload)
            self.data_panel.brightfield_preview_status_label.setText(tr("brightfield_mask_preview_status"))
        except Exception as exc:
            log_path = log_exception("Brightfield segmentation preview failed", exc)
            QMessageBox.critical(
                self,
                tr("brightfield_preview_failed_title"),
                trf("error_with_traceback", error=exc, path=log_path),
            )

    def send_fluorescence_layout_to_composer(self) -> None:
        source_panel = self._active_fluoro_panel()
        self.fluoro_workspace.persist_layout_edits_from_scene()
        if self.fluoro_workspace.canvas.current_step != 4:
            self.fluoro_workspace.canvas.set_step(4)
        asset_dir = self._generated_asset_dir() / "fluorescence_layouts"
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"fluorescence_layout_{uuid4().hex[:8]}.png"
        plot_items = list(self.fluoro_workspace.canvas.layout_plot_items())
        self._export_fluorescence_figure_scene(
            asset_path,
            FLUORESCENCE_COMPOSER_EXPORT_DPI,
            hidden_items=plot_items,
        )
        panel = ExternalImagePanel(panel_id=f"{source_panel.panel_id}_layout")
        panel.import_image(asset_path)
        image = QImage(str(asset_path))
        if image.isNull():
            bounds = self.fluoro_workspace.canvas.scene.itemsBoundingRect()
            width_pt = max(1.0, bounds.width())
            height_pt = max(1.0, bounds.height())
            source_width_px = None
            source_height_px = None
        else:
            width_pt = image.width() * 72.0 / FLUORESCENCE_COMPOSER_EXPORT_DPI
            height_pt = image.height() * 72.0 / FLUORESCENCE_COMPOSER_EXPORT_DPI
            source_width_px = image.width()
            source_height_px = image.height()
        export_item = fluorescence_export_item_for_scene_bounds(
            source_panel,
            FLUORESCENCE_LAYOUT_EXPORT_KEY,
            width_pt,
            height_pt,
            dpi=FLUORESCENCE_COMPOSER_EXPORT_DPI,
            image_path=asset_path,
            source_format="PNG",
            source_width_px=source_width_px,
            source_height_px=source_height_px,
        )
        apply_fluorescence_export_item_metadata(panel, export_item)
        self.project.add_panel(panel)
        created = 1
        for item in plot_items:
            plot_uuid = str(item.data(1) or "")
            plot_record = self._fluoro_live_layout_plot_record(source_panel, plot_uuid)
            bounds = self._fluoro_scene_item_content_rect(item).adjusted(-4.0, -4.0, 4.0, 4.0)
            if plot_record is None:
                continue
            plot_path = asset_dir / f"fluorescence_plot_{uuid4().hex[:8]}.svg"
            plot_svg = self.fluoro_workspace.canvas.render_layout_plot_record_svg(
                dict(plot_record),
                max(1.0, bounds.width()),
                max(1.0, bounds.height()),
            )
            if not plot_svg:
                continue
            plot_path.write_bytes(plot_svg)
            source_format = "SVG"
            source_width_px = max(1, int(round(bounds.width())))
            source_height_px = max(1, int(round(bounds.height())))
            plot_panel = ExternalImagePanel(panel_id=f"{source_panel.panel_id}_{item.data(0)}")
            plot_panel.import_image(plot_path)
            plot_type = {
                "fluoro_stats_plot": str(item.data(2) or "statistics"),
                "fluoro_profile_plot": "line_profile",
                "fluoro_coloc_plot": "colocalization",
            }.get(str(item.data(0)), "plot")
            plot_type = str(plot_record.get("plot_type") or plot_type)
            export_item = fluorescence_export_item_for_scene_bounds(
                source_panel,
                fluorescence_plot_export_key(plot_uuid),
                bounds.width(),
                bounds.height(),
                dpi=FLUORESCENCE_COMPOSER_EXPORT_DPI,
                image_path=plot_path,
                source_format=source_format,
                source_width_px=source_width_px,
                source_height_px=source_height_px,
                plot_uuid=plot_uuid,
                plot_type=plot_type,
            )
            apply_fluorescence_export_item_metadata(plot_panel, export_item)
            self.project.add_panel(plot_panel)
            created += 1
        self.project.append_audit("fluorescence_layout_to_composer", asset_path.name)
        self.refresh_composer_panel_list()
        self.statusBar().showMessage(
            trf(
                "added_layout_items_status",
                count=created,
                module=tr("v9_module_fluoro"),
            ),
            4000,
        )

    @staticmethod
    def _fluoro_live_layout_plot_record(panel: FluorescencePanel, plot_uuid: str) -> dict[str, Any] | None:
        token = str(plot_uuid or "").strip()
        if not token:
            return None
        for record in list(panel.analysis.get("fluoro_layout_plots", []) or []):
            if isinstance(record, dict) and str(record.get("plot_uuid", "")).strip() == token:
                return deepcopy(record)
        return None

    def _fluoro_plot_item_qimage(self, item, dpi: int) -> QImage:
        plot_uuid = str(item.data(1) or "") if item is not None and hasattr(item, "data") else ""
        record = self._fluoro_live_layout_plot_record(self._active_fluoro_panel(), plot_uuid)
        if record is None:
            return QImage()
        rect = item.rect() if hasattr(item, "rect") else QRectF()
        width_pt = max(1.0, float(rect.width()))
        height_pt = max(1.0, float(rect.height()))
        return self.fluoro_workspace.canvas.render_layout_plot_record_qimage(
            dict(record),
            width_pt,
            height_pt,
            dpi=dpi,
        )

    def _export_fluorescence_figure_scene(self, target_path: str | Path, dpi: int, hidden_items: list | None = None) -> Path:
        scene = self.fluoro_workspace.canvas.scene
        selected_items = list(scene.selectedItems())
        hidden_state = []
        hidden_items = list(hidden_items or [])
        scene.clearSelection()
        try:
            for scene_item in scene.items():
                if scene_item in hidden_items or str(scene_item.data(0) or "") == "fluoro_resize_handle":
                    hidden_state.append((scene_item, scene_item.isVisible()))
                    scene_item.setVisible(False)
            return export_fluoro_figure_scene(scene, target_path, dpi=dpi)
        finally:
            for scene_item, visible in hidden_state:
                if scene_item.scene() is scene:
                    scene_item.setVisible(visible)
            for selected in selected_items:
                if selected.scene() is scene:
                    selected.setSelected(True)

    def _fluoro_scene_item_qimage(self, item, dpi: int) -> QImage:
        if item is None:
            return QImage()
        source_rect = self._fluoro_scene_item_content_rect(item).adjusted(-4.0, -4.0, 4.0, 4.0)
        if source_rect.isNull() or source_rect.width() <= 0 or source_rect.height() <= 0:
            return QImage()
        scale = max(float(dpi), 72.0) / 72.0
        width = max(1, int(round(source_rect.width() * scale)))
        height = max(1, int(round(source_rect.height() * scale)))
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        dots_per_meter = int(round(dpi / 0.0254))
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        image.fill(QColor("white"))
        hidden_items = []
        scene = self.fluoro_workspace.canvas.scene
        selected_items = list(scene.selectedItems())
        scene.clearSelection()
        for scene_item in self.fluoro_workspace.canvas.scene.items():
            if scene_item is item or self._is_scene_descendant(scene_item, item):
                if str(scene_item.data(0) or "") != "fluoro_resize_handle":
                    continue
            if str(scene_item.data(0) or "") == "fluoro_resize_handle":
                hidden_items.append((scene_item, scene_item.isVisible()))
                scene_item.setVisible(False)
                continue
            hidden_items.append((scene_item, scene_item.isVisible()))
            scene_item.setVisible(False)
        painter = QPainter(image)
        try:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            self.fluoro_workspace.canvas.scene.render(painter, QRectF(0, 0, width, height), source_rect)
        finally:
            painter.end()
            for scene_item, visible in hidden_items:
                if scene_item.scene() is scene:
                    scene_item.setVisible(visible)
            for selected in selected_items:
                if selected.scene() is scene:
                    selected.setSelected(True)
        return image

    def _fluoro_scene_item_content_rect(self, item) -> QRectF:
        rect_getter = getattr(item, "layout_scene_rect", None)
        if callable(rect_getter):
            return rect_getter()
        return item.sceneBoundingRect()

    def _is_scene_descendant(self, scene_item, parent_item) -> bool:
        current = scene_item.parentItem()
        while current is not None:
            if current is parent_item:
                return True
            current = current.parentItem()
        return False

    def _generated_asset_dir(self) -> Path:
        configured = os.environ.get("SCIEFIG_ASSET_DIR")
        if configured:
            return Path(configured)
        app_data = os.environ.get("LOCALAPPDATA")
        if app_data:
            return Path(app_data) / "ScieFig" / "generated_assets"
        return Path.home() / ".sciefig" / "generated_assets"

    def refresh_composer_panel_list(self) -> None:
        self.composer_workspace.refresh_assets(self.project)




    def remove_selected_composer_asset(self) -> None:
        if self.active_module != "composer":
            return
        panel_uuid = self._selected_composer_asset_uuid()
        if not panel_uuid:
            return
        panel = self.project.get_panel(str(panel_uuid))
        if not isinstance(panel, ExternalImagePanel) or not panel.data.get("image_path"):
            QMessageBox.information(
                self,
                tr("v9_module_composer"),
                tr("composer_external_only_message"),
            )
            return
        self.project.remove_panel(str(panel_uuid))
        self.composer_canvas.set_project(self.project)
        self.refresh_composer_panel_list()
        self.statusBar().showMessage(
            trf("removed_asset_status", panel_id=panel.panel_id),
            3000,
        )

    def _selected_composer_asset_uuid(self) -> str | None:
        return self.composer_workspace.selected_asset_uuid()

    def _composer_focus_changed(self,focused: bool) -> None:
        self.v9_shell.rail_panel.setVisible(not focused)
















    def import_external_image_for_composer(self) -> None:
        if self.active_module != "composer":
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("import_external_image_title"),
            recent_directory(),
            image_filter("Images and vector files", COMPOSER_IMAGE_SUFFIXES),
        )
        if not path:
            return
        remember_file_directory(path)
        self.import_external_images_to_shelf([path], place_first=True)

    def import_external_images_to_shelf(self, paths: list[str], place_first: bool = False) -> None:
        if self.active_module != "composer":
            return
        imported_panels = []
        failures = []
        for path in paths:
            try:
                panel = ExternalImagePanel(panel_id=f"ExternalImage{len(self.project.panels) + len(imported_panels) + 1}")
                panel.import_image(path)
                self.project.add_panel(panel)
                self.project.append_audit("composer_import_external_image", Path(path).name)
                imported_panels.append(panel)
            except Exception as exc:
                failures.append(f"{Path(path).name}: {exc}")
        if not imported_panels:
            if failures:
                QMessageBox.warning(self, tr("composer_import_failed_title"), "\n".join(failures))
            return
        self.refresh_composer_panel_list()
        shelf = self.composer_workspace.assets
        for index in range(shelf.count()):
            if shelf.item(index).data(Qt.ItemDataRole.UserRole) == imported_panels[-1].panel_uuid:
                shelf.setCurrentRow(index)
                break
        if place_first:
            try:
                self.composer_canvas.place_panel(imported_panels[0].panel_uuid)
                self.project.append_audit("composer_place_imported_image", imported_panels[0].panel_uuid)
                self.statusBar().showMessage(
                    trf("composer_imported_placed_status", name=Path(paths[0]).name),
                    3000,
                )
            except Exception:
                self.statusBar().showMessage(
                    trf("composer_imported_shelf_status", name=Path(paths[0]).name),
                    3000,
                )
        else:
            self.statusBar().showMessage(
                trf("composer_imported_files_status", count=len(imported_panels)),
                3000,
            )
        if failures:
            QMessageBox.warning(self, tr("composer_import_warnings_title"), "\n".join(failures))

    def run_qpcr_analysis(self) -> None:
        try:
            if self.active_module != "qpcr":
                return
            panel = self._active_qpcr_panel()
            if not panel.data.get("raw_rows"):
                return
            reference = self.data_panel.reference_combo.currentText() or None
            control = self.data_panel.control_combo.currentText() or None
            group_overrides, included_samples = self.data_panel.qpcr_group_assignments()
            panel.reanalyze(
                reference_gene=reference,
                control_group=control,
                group_overrides=group_overrides,
                included_samples=included_samples,
            )
            self._refresh_qpcr_ui(panel)
            if self.canvas_stack.currentWidget() is self.qpcr_layout_workspace:
                self.qpcr_layout_workspace.set_panel(panel)
        except Exception as exc:
            log_path = log_exception("qPCR reanalysis failed", exc)
            QMessageBox.critical(
                self,
                tr("qpcr_analysis_failed_title"),
                trf("qpcr_analysis_failed_body", error=exc, path=log_path),
            )

    def show_qpcr_preview(self) -> None:
        if self.active_module != "qpcr":
            return
        self.data_panel.setVisible(False)
        self.qpcr_workflow_panel.setVisible(True)
        self.data_panel.set_qpcr_layout_mode(False)
        if hasattr(self, "qpcr_canvas_tools_group"):
            self.qpcr_canvas_tools_group.setVisible(False)
        if hasattr(self, "qpcr_workflow_group"):
            self.qpcr_workflow_group.setVisible(True)
        self._expand_right_workflow_widget(self.qpcr_workflow_panel)
        self.data_panel.set_qpcr_step(1 if self._active_qpcr_panel().data.get("processed_data") else 0, emit_signal=False)
        self.canvas_stack.setCurrentWidget(self.qpcr_preview_workspace)
        self._refresh_qpcr_ui(self._active_qpcr_panel())
        self._schedule_active_canvas_layout_refresh()
        self._sync_chart_kind_controls()

    def show_qpcr_layout(self) -> None:
        if self.active_module != "qpcr":
            return
        panel = self._active_qpcr_panel()
        self.qpcr_layout_workspace.set_panel(panel)
        self.data_panel.setVisible(False)
        self.qpcr_workflow_panel.setVisible(True)
        self.data_panel.set_qpcr_layout_mode(True)
        if hasattr(self, "qpcr_canvas_tools_group"):
            self.qpcr_canvas_tools_group.setVisible(True)
        if hasattr(self, "qpcr_workflow_group"):
            self.qpcr_workflow_group.setVisible(False)
        self._expand_right_workflow_widget(self.qpcr_workflow_panel)
        self.data_panel.set_qpcr_step(2, emit_signal=False)
        self.canvas_stack.setCurrentWidget(self.qpcr_layout_workspace)
        self._schedule_active_canvas_layout_refresh()
        self._sync_chart_kind_controls()

    def add_qpcr_layout_text(self) -> None:
        if self.active_module != "qpcr":
            return
        if self.canvas_stack.currentWidget() is not self.qpcr_layout_workspace:
            self.show_qpcr_layout()
        self.qpcr_layout_canvas.add_text("Text")
        self.qpcr_layout_canvas.persist_layout_edits()

    def add_qpcr_layout_shape(self) -> None:
        if self.active_module != "qpcr":
            return
        if self.canvas_stack.currentWidget() is not self.qpcr_layout_workspace:
            self.show_qpcr_layout()
        self.qpcr_layout_canvas.add_shape("rectangle", stroke_color="#C0392B", fill_color="#22D7E6")
        self.qpcr_layout_canvas.persist_layout_edits()

    def send_qpcr_plots_to_composer(self) -> None:
        if self.active_module != "qpcr":
            return
        panel = self._active_qpcr_panel()
        if self.canvas_stack.currentWidget() is not self.qpcr_layout_workspace:
            self.qpcr_layout_workspace.set_panel(panel)
        self.qpcr_layout_canvas.persist_layout_edits()
        records = panel.layout_plot_records()
        if not records:
            QMessageBox.information(
                self,
                tr("v9_module_qpcr"),
                tr("qpcr_analysis_required_for_composer"),
            )
            return
        asset_dir = self._generated_asset_dir() / "qpcr_plots"
        asset_dir.mkdir(parents=True, exist_ok=True)
        created = 0
        layout_path = asset_dir / f"qpcr_layout_{uuid4().hex[:8]}.png"
        plot_items = list(self.qpcr_layout_canvas.layout_plot_items())
        hidden_plots = [(item, item.isVisible()) for item in plot_items]
        for item, _visible in hidden_plots:
            item.setVisible(False)
        self.qpcr_layout_canvas.export_figure(layout_path, dpi=QPCR_COMPOSER_EXPORT_DPI)
        for item, visible in hidden_plots:
            item.setVisible(visible)
        layout_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_layout")
        layout_panel.import_image(layout_path)
        layout_image = QImage(str(layout_path))
        if layout_image.isNull():
            bounds = self.qpcr_layout_canvas.scene.itemsBoundingRect()
            width_pt = max(1.0, bounds.width())
            height_pt = max(1.0, bounds.height())
            source_width_px = None
            source_height_px = None
        else:
            width_pt = layout_image.width() * 72.0 / QPCR_COMPOSER_EXPORT_DPI
            height_pt = layout_image.height() * 72.0 / QPCR_COMPOSER_EXPORT_DPI
            source_width_px = layout_image.width()
            source_height_px = layout_image.height()
        export_item = qpcr_export_item_for_scene_bounds(
            panel,
            QPCR_LAYOUT_EXPORT_KEY,
            width_pt,
            height_pt,
            dpi=QPCR_COMPOSER_EXPORT_DPI,
            image_path=layout_path,
            source_format="PNG",
            source_width_px=source_width_px,
            source_height_px=source_height_px,
        )
        apply_qpcr_export_item_metadata(layout_panel, export_item)
        self.project.add_panel(layout_panel)
        created += 1
        for record in records:
            if not record.get("enabled", True):
                continue
            target_name = str(record.get("target", "target"))
            asset_path = asset_dir / f"qpcr_{target_name}_{uuid4().hex[:8]}.svg"
            asset_path.write_bytes(panel.render_layout_plot(record, dpi=QPCR_COMPOSER_EXPORT_DPI, format="svg"))
            asset_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_{target_name}")
            asset_panel.import_image(asset_path)
            width_px = int(record.get("width_px", 520))
            height_px = int(record.get("height_px", 360))
            export_item = qpcr_export_item_for_scene_bounds(
                panel,
                qpcr_plot_export_key(str(record.get("plot_uuid", ""))),
                float(width_px),
                float(height_px),
                dpi=QPCR_COMPOSER_EXPORT_DPI,
                image_path=asset_path,
                source_format="SVG",
                source_width_px=width_px,
                source_height_px=height_px,
                plot_uuid=str(record.get("plot_uuid", "")),
                qpcr_target=target_name,
            )
            apply_qpcr_export_item_metadata(asset_panel, export_item)
            self.project.add_panel(asset_panel)
            created += 1
        self.project.append_audit("qpcr_layout_to_composer", f"{created} item(s)")
        self.refresh_composer_panel_list()
        self.statusBar().showMessage(
            trf(
                "added_layout_items_status",
                count=created,
                module=tr("v9_module_qpcr"),
            ),
            4000,
        )

    def apply_wb_crop(self) -> None:
        if self.active_module != "wb":
            return
        try:
            panel = self._active_wb_panel()
            if not panel.data.get("image_data"):
                QMessageBox.information(
                    self,
                    tr("v9_module_wb"),
                    tr("import_wb_before_crop"),
                )
                return
            before = self._capture_wb_state()
            if self._editing_band_strips(panel):
                index = self._selected_wb_strip_index(panel)
                if index is None:
                    return
                panel.update_band_strip_crop(
                    index,
                    self.data_panel.wb_x_spin.value(),
                    self.data_panel.wb_y_spin.value(),
                    self.data_panel.wb_w_spin.value(),
                    self.data_panel.wb_h_spin.value(),
                    protein=self.data_panel.wb_protein_edit.text().strip() or None,
                )
                panel.update_band_strip_adjustments(
                    index,
                    self.data_panel.wb_brightness_spin.value(),
                    self.data_panel.wb_contrast_spin.value(),
                    self.data_panel.wb_rotation_spin.value() + self.data_panel.wb_fine_rotation_slider.value() / 10.0,
                )
                self.wb_canvas.set_mode("roi")
                self._refresh_wb_ui(panel)
                self._record_wb_change("Crop WB band strip", before)
                self.statusBar().showMessage(tr("wb_strip_crop_recorded_status"), 3000)
                return
            panel.set_display_adjustments(self.data_panel.wb_brightness_spin.value(), self.data_panel.wb_contrast_spin.value())
            panel.set_annotations(
                self.data_panel.wb_lane_labels_for_count(),
                [self.data_panel.wb_protein_edit.text().strip()] if self.data_panel.wb_protein_edit.text().strip() else [],
                self.data_panel.wb_marker_labels(),
            )
            panel.set_crop_roi(
                self.data_panel.wb_x_spin.value(),
                self.data_panel.wb_y_spin.value(),
                self.data_panel.wb_w_spin.value(),
                self.data_panel.wb_h_spin.value(),
                protein=self.data_panel.wb_protein_edit.text().strip(),
            )
            self.wb_canvas.set_mode("roi")
            self._refresh_wb_ui(panel)
            self._record_wb_change("Crop WB band", before)
            self.statusBar().showMessage(tr("wb_crop_recorded_status"), 3000)
        except Exception as exc:
            QMessageBox.warning(self, tr("wb_crop_title"), str(exc))

    def show_wb_full_blot(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        if not panel.data.get("image_data"):
            QMessageBox.information(
                self,
                tr("v9_module_wb"),
                tr("import_wb_full_length_first"),
            )
            return
        panel.data.setdefault("wb_layout", {})["enabled"] = False
        self.wb_canvas.set_mode("roi")
        self._refresh_wb_ui(panel, show_full=True)

    def _refresh_wb_ui(self, panel: WesternBlotPanel, show_full: bool = False) -> None:
        del show_full
        selected_index = self._selected_wb_strip_index(panel)
        if selected_index is not None:
            panel.data["selected_strip_index"] = selected_index
        self.data_panel._wb_placed_strip_ids = list(panel.data.get("placed_strip_ids", []))
        self.data_panel.populate_wb_strips(panel.data.get("band_strips", []), selected_index)
        self.data_panel.populate_wb_bands(self._wb_band_sources_for_ui(panel), panel.data.get("wb_layout", {}))
        self.data_panel.populate_wb_hierarchy(panel.data.get("wb_layout", {}).get("lane_hierarchy", {}), panel.data.get("wb_layout", {}).get("group_gaps", []))
        self.wb_canvas.set_panel(panel)
        if self._editing_band_strips(panel):
            self.sync_wb_strip_controls(panel)
        self.update_wb_context_labels(panel)
        badge = panel.analysis.get("integrity_badge", {})
        warnings = badge.get("warnings", [])
        status = badge.get("label", "Integrity badge unavailable")
        if warnings:
            status += " | " + "; ".join(warnings)
        self.style_sidebar.set_qpcr_statistics(status)
        self.status_label.setText(status)

    def update_wb_context_labels(self, panel: WesternBlotPanel) -> None:
        index = self._selected_wb_strip_index(panel)
        strips = panel.data.get("band_strips", [])
        if index is None or index >= len(strips):
            self.data_panel.wb_image_properties_label.setText(tr("no_strip_selected"))
        else:
            strip = strips[index]
            image_data = panel.data.get("image_data", {})
            roi = strip.get("crop_roi", {})
            self.data_panel.wb_image_properties_label.setText(
                "\n".join(
                    [
                        f"File: {strip.get('original_source', '')}",
                        f"Size: {image_data.get('width_px', '?')} x {image_data.get('height_px', '?')} px",
                        f"Format: {image_data.get('original_format', '?')}",
                        f"Crop: {roi.get('w', '?')} x {roi.get('h', '?')} px",
                    ]
                )
            )
        badge = panel.analysis.get("integrity_badge", {})
        warnings = badge.get("warnings", [])
        self.data_panel.wb_integrity_label.setText((badge.get("label") or "Integrity badge unavailable") + (("\n" + "\n".join(warnings)) if warnings else ""))

    def set_wb_roi_from_canvas(self, rect: QRectF) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        strip_index = self._selected_wb_strip_index(panel)
        if self._editing_band_strips(panel) and strip_index is not None:
            x, y, width, height = self.wb_canvas.scene_roi_to_strip_roi(rect, strip_index)
            source_size = QImage(str(panel.data["band_strips"][strip_index].get("image_path", ""))).size()
            source_width, source_height = source_size.width(), source_size.height()
        else:
            x, y, width, height = self.wb_canvas.scene_roi_to_source_roi(rect)
            image_data = panel.data.get("image_data", {})
            source_width, source_height = int(image_data.get("width_px", 1)), int(image_data.get("height_px", 1))
        for spin in [self.data_panel.wb_x_spin, self.data_panel.wb_y_spin, self.data_panel.wb_w_spin, self.data_panel.wb_h_spin]:
            spin.blockSignals(True)
        # A restored/previous image's spin ranges must not clamp the first drag.
        self.data_panel.configure_wb_image_bounds(source_width, source_height)
        self.data_panel.wb_x_spin.setValue(x)
        self.data_panel.wb_y_spin.setValue(y)
        self.data_panel.wb_w_spin.setValue(width)
        self.data_panel.wb_h_spin.setValue(height)
        for spin in [self.data_panel.wb_x_spin, self.data_panel.wb_y_spin, self.data_panel.wb_w_spin, self.data_panel.wb_h_spin]:
            spin.blockSignals(False)
        self.apply_wb_crop()

    def update_wb_crop_from_canvas(self, index: int, rect: QRectF) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        panel.update_crop_roi(index, int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
        self._refresh_wb_ui(panel)
        self._record_wb_change("Adjust WB crop", before)

    def delete_wb_crop_from_canvas(self, index: int) -> None:
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        try:
            panel.delete_crop_roi(index)
        except IndexError:
            return
        self._refresh_wb_ui(panel)
        self._record_wb_change("Delete WB crop", before)

    def reorder_wb_crop_from_canvas(self, index: int, direction: int) -> None:
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        panel.move_crop_roi(index, direction)
        self._refresh_wb_ui(panel)
        self._record_wb_change("Reorder WB crop", before)

    def select_wb_strip_from_table(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        if not self._editing_band_strips(panel):
            return
        selected = self.data_panel.selected_wb_strip_index()
        if selected is None:
            return
        self._wb_selected_strip_index = selected
        panel.data["selected_strip_index"] = selected
        self.sync_wb_strip_controls(panel)
        self.wb_canvas.set_panel(panel)

    def sync_wb_strip_controls(self, panel: WesternBlotPanel | None = None) -> None:
        if self.active_module != "wb":
            return
        panel = panel or self._active_wb_panel()
        index = self._selected_wb_strip_index(panel)
        if index is None:
            return
        strips = panel.data.get("band_strips", [])
        if index >= len(strips):
            return
        strip = strips[index]
        image = QImage(str(strip.get("image_path", "")))
        roi = dict(strip.get("crop_roi", {}))
        adjustments = dict(strip.get("adjustments", {}))
        widgets = [
            self.data_panel.wb_x_spin,
            self.data_panel.wb_y_spin,
            self.data_panel.wb_w_spin,
            self.data_panel.wb_h_spin,
            self.data_panel.wb_brightness_spin,
            self.data_panel.wb_contrast_spin,
            self.data_panel.wb_rotation_spin,
            self.data_panel.wb_fine_rotation_slider,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        if not image.isNull():
            self.data_panel.configure_wb_image_bounds(image.width(), image.height())
        self.data_panel.wb_x_spin.setValue(int(roi.get("x", 0)))
        self.data_panel.wb_y_spin.setValue(int(roi.get("y", 0)))
        self.data_panel.wb_w_spin.setValue(int(roi.get("w", image.width() if not image.isNull() else 1)))
        self.data_panel.wb_h_spin.setValue(int(roi.get("h", image.height() if not image.isNull() else 1)))
        self.data_panel.wb_brightness_spin.setValue(int(adjustments.get("brightness", 0)))
        self.data_panel.wb_contrast_spin.setValue(int(adjustments.get("contrast", 0)))
        self.data_panel.wb_rotation_spin.setValue(float(adjustments.get("rotation", 0.0)))
        self.data_panel.wb_fine_rotation_slider.setValue(0)
        for widget in widgets:
            widget.blockSignals(False)

    def update_wb_strip_from_table(self, item) -> None:
        if self.active_module != "wb" or item is None or item.column() != 2:
            return
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        try:
            panel.update_band_strip_protein(item.row(), item.text().strip())
        except IndexError:
            return
        self._refresh_wb_ui(panel)
        self._record_wb_change("Rename WB strip protein", before)

    def move_wb_strip(self, direction: int) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        index = self._selected_wb_strip_index(panel)
        if index is None:
            return
        before = self._capture_wb_state()
        panel.move_band_strip(index, direction)
        self._wb_selected_strip_index = max(0, min(index + direction, len(panel.data.get("band_strips", [])) - 1))
        self._refresh_wb_ui(panel)
        self._record_wb_change("Reorder WB strip", before)

    def delete_wb_strip(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        index = self._selected_wb_strip_index(panel)
        if index is None:
            return
        before = self._capture_wb_state()
        try:
            strip_id = str(panel.data.get("band_strips", [])[index].get("strip_id", f"strip_{index + 1}"))
            panel.delete_band_strip(index)
        except IndexError:
            return
        panel.data["placed_strip_ids"] = [item for item in panel.data.get("placed_strip_ids", []) if item != strip_id]
        self._wb_selected_strip_index = max(0, min(index, len(panel.data.get("band_strips", [])) - 1))
        self._refresh_wb_ui(panel)
        self._record_wb_change("Delete WB strip", before)

    def place_wb_strip_from_staging(self, index: int) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        strips = panel.data.get("band_strips", [])
        if index < 0 or index >= len(strips):
            return
        strip_id = str(strips[index].get("strip_id", f"strip_{index + 1}"))
        placed = list(panel.data.get("placed_strip_ids", []))
        if strip_id not in placed:
            before = self._capture_wb_state()
            placed.append(strip_id)
            panel.data["placed_strip_ids"] = placed
            panel.data.setdefault("wb_layout", {})["enabled"] = False
            panel.append_audit("place_wb_strip", strip_id)
            self._refresh_wb_ui(panel)
            self._record_wb_change("Place WB strip", before)
        self.data_panel.set_wb_step(WB_STEP_LAYOUT)

    def handle_wb_strip_action(self, action: str, index: int) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        if index < 0 or index >= len(panel.data.get("band_strips", [])):
            return
        self._wb_selected_strip_index = index
        panel.data["selected_strip_index"] = index
        self.data_panel.wb_strip_table.selectRow(index)
        if action == "crop":
            self.data_panel.set_wb_step(WB_STEP_CROP)
            self.enable_wb_roi_mode()
            return
        if action in {"adjust", "rotate"}:
            self.data_panel.set_wb_step(WB_STEP_IMPORT)
            self.sync_wb_strip_controls(panel)
            return
        if action == "remove":
            self.delete_wb_strip()
            return
        if action == "duplicate":
            before = self._capture_wb_state()
            panel.duplicate_band_strip(index)
            self._wb_selected_strip_index = index + 1
            panel.data["selected_strip_index"] = self._wb_selected_strip_index
            self._refresh_wb_ui(panel)
            self._record_wb_change("Duplicate WB strip", before)

    def set_wb_lane_anchor_from_canvas(self, scene_x: float) -> None:
        panel = self._active_wb_panel()
        lane_x = self.wb_canvas.scene_lane_x_to_source_x(scene_x)
        if self._pending_lane_edge_x is None:
            self._pending_lane_edge_x = lane_x
            self.statusBar().showMessage(tr("first_lane_set_status"), 5000)
            return
        first_x = self._pending_lane_edge_x
        last_x = lane_x
        if last_x < first_x:
            first_x, last_x = last_x, first_x
        before = self._capture_wb_state()
        lane_labels = self.data_panel.wb_lane_labels_for_count()
        panel.set_annotations(lane_labels, [self.data_panel.wb_protein_edit.text().strip()] if self.data_panel.wb_protein_edit.text().strip() else [], self.data_panel.wb_marker_labels())
        panel.set_lane_calibration(first_x, last_x, self.data_panel.wb_lane_count_spin.value())
        self._pending_lane_edge_x = None
        self.wb_canvas.set_mode("roi")
        self._refresh_wb_ui(panel)
        self._record_wb_change("Set WB lane calibration", before)

    def set_wb_marker_anchor_from_canvas(self, scene_y: float) -> None:
        panel = self._active_wb_panel()
        markers = self.data_panel.wb_marker_labels()
        if not markers:
            QMessageBox.information(
                self,
                tr("marker_anchors"),
                tr("enter_marker_labels_first"),
            )
            return
        index = len(panel.data.get("annotations", {}).get("marker_anchors", []))
        if index >= len(markers):
            index = len(markers) - 1
        before = self._capture_wb_state()
        panel.set_annotations(self.data_panel.wb_lane_labels_for_count(), [self.data_panel.wb_protein_edit.text().strip()] if self.data_panel.wb_protein_edit.text().strip() else [], markers)
        panel.set_marker_anchor(index, self.wb_canvas.scene_marker_y_to_source_y(scene_y))
        self._refresh_wb_ui(panel)
        self._record_wb_change("Set WB marker anchor", before)

    def apply_wb_nature_layout(self, preview: bool = False) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        if not self._wb_band_sources_for_ui(panel):
            QMessageBox.information(
                self,
                tr("wb_layout_title"),
                tr("select_band_roi_for_layout"),
            )
            return
        lane_labels = self.data_panel.wb_lane_labels_for_count()
        if len(lane_labels) != self.data_panel.wb_lane_count_spin.value():
            QMessageBox.warning(
                self,
                tr("wb_layout_title"),
                tr("lane_label_count_mismatch"),
            )
            return
        bands = self.data_panel.wb_band_configuration()
        band_sources = self._wb_band_sources_for_ui(panel)
        missing = [index + 1 for index, band in enumerate(bands[: len(band_sources)]) if not band.get("protein")]
        if missing and not preview:
            QMessageBox.warning(
                self,
                tr("wb_layout_title"),
                trf("protein_required_for_bands", bands=missing),
            )
            return
        before = self._capture_wb_state()
        previous_layout = deepcopy(panel.data.get("wb_layout", {}))
        loading_control_index = self.data_panel.wb_loading_control_combo.currentData()
        for band_index, band in enumerate(bands):
            if band.get("is_loading_ctrl"):
                loading_control_index = band_index
                break
        layout = panel.set_band_configuration(
            bands,
            lane_labels,
            loading_control_index=loading_control_index,
            lane_label_position=self.data_panel.wb_lane_position_combo.currentText(),
            protein_label_side=self.data_panel.wb_protein_side_combo.currentText(),
            marker_label_side=self.data_panel.wb_marker_side_combo.currentText(),
            lane_hierarchy=self.data_panel.wb_lane_hierarchy(),
            group_gaps=self.data_panel.wb_group_gaps(),
            strip_gap_px=self.data_panel.wb_strip_gap_spin.value(),
            label_offset_px=self.data_panel.wb_label_offset_spin.value(),
            enabled=True,
        )
        for key in ("stats_plot", "text_styles", "manual_positions", "line_styles"):
            if key in previous_layout:
                layout[key] = deepcopy(previous_layout[key])
        panel.data["wb_layout"] = layout
        layout["preview"] = bool(preview)
        self.wb_canvas.set_mode("move")
        self._refresh_wb_ui(panel)
        self._record_wb_change("Preview WB Nature layout" if preview else "Apply WB Nature layout", before)
        self.statusBar().showMessage(
            tr("wb_layout_preview_generated_status" if preview else "wb_layout_applied_status"),
            4000,
        )

    def apply_wb_layout_selection(self) -> None:
        if self.active_module != "wb":
            return
        if self.selected_wb_line_items():
            self.apply_wb_selected_line_style()
            return
        if self.selected_wb_text_items():
            self.apply_wb_selected_text_style_to_type()
            return
        panel = self._active_wb_panel()
        if panel.data.get("wb_layout", {}).get("stats_plot", {}).get("enabled"):
            self.apply_wb_stats_plot_style()
            return
        self.apply_wb_nature_layout(preview=False)

    def run_wb_densitometry(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        self._sync_wb_labels_for_density(panel)
        if not panel.data.get("band_strips"):
            QMessageBox.information(
                self,
                tr("densitometry_title"),
                tr("import_crop_wb_before_measure"),
            )
            return
        lane_labels = list(panel.data.get("annotations", {}).get("lane_labels", [])) or list(panel.data.get("wb_layout", {}).get("lane_labels", []))
        if not lane_labels:
            QMessageBox.information(
                self,
                tr("densitometry_title"),
                tr("define_lane_labels_before_measure"),
            )
            return
        loading_ctrl_id = self.data_panel.wb_density_loading_ctrl_combo.currentData()
        reference_lane_index = self.data_panel.wb_density_reference_lane_combo.currentData()
        if loading_ctrl_id is None or reference_lane_index is None:
            QMessageBox.information(
                self,
                tr("densitometry_title"),
                tr("select_loading_control_reference"),
            )
            return
        bg_method_map = {
            "Local Edge": "local_edge",
            "Rolling Ball": "rolling_ball",
            "None": "none",
        }
        polarity_map = {
            "Auto": "auto",
            "Dark bands on light bg": "dark_on_light",
            "Bright bands on dark bg": "bright_on_dark",
        }
        try:
            before = self._capture_wb_state()
            result = measure_densitometry(
                panel.data,
                loading_control_strip_id=str(loading_ctrl_id),
                reference_lane_index=int(reference_lane_index),
                background_method=bg_method_map.get(self.data_panel.wb_density_bg_method_combo.currentText(), "local_edge"),
                polarity_mode=polarity_map.get(self.data_panel.wb_density_polarity_combo.currentText(), "auto"),
            )
            panel.data["densitometry"] = result
            runs = list(panel.data.get("densitometry_runs", []))
            runs.append(deepcopy(result))
            panel.data["densitometry_runs"] = runs
            panel.data.pop('wb_statistics',None)
            self.data_panel.populate_wb_stats_controls(runs)
            self.data_panel.populate_wb_stats_results({})
            self.data_panel.wb_stats_summary_label.setText(tr('statistics_recalculate_required'))
            self._refresh_wb_stats_chart({})
            panel.append_audit("run_densitometry", f"ctrl={loading_ctrl_id}, ref_lane={reference_lane_index}")
            self.data_panel.populate_wb_density_results(result)
            self._refresh_wb_density_chart(result)
            self.wb_canvas.set_density_overlay(result)
            self._record_wb_change("Run WB densitometry", before)
            self.statusBar().showMessage(tr("densitometry_complete_status"), 4000)
        except Exception as exc:
            log_path = log_exception("WB densitometry failed", exc)
            QMessageBox.critical(
                self,
                tr("densitometry_failed_title"),
                trf("error_with_traceback", error=exc, path=log_path),
            )

    def copy_wb_densitometry_table(self) -> None:
        panel = self._active_wb_panel()
        result = panel.data.get("densitometry", {})
        measurements = list(result.get("measurements", []))
        if not measurements:
            return
        header = [
            "strip_id",
            "strip_label",
            "lane_index",
            "lane_label",
            "raw_intensity",
            "background",
            "bg_subtracted",
            "normalized",
            "fold",
            "qc_flags",
        ]
        lines = ["\t".join(header)]
        for measurement in measurements:
            for lane in measurement.get("lanes", []):
                normalized = lane.get("normalized")
                fold = lane.get("fold")
                norm_text = f"{float(normalized):.4f}" if isinstance(normalized, (int, float)) and normalized == normalized else "N/A"
                fold_text = f"{float(fold):.4f}" if isinstance(fold, (int, float)) and fold == fold else "N/A"
                lines.append(
                    "\t".join(
                        [
                            str(measurement.get("strip_id", "")),
                            str(measurement.get("strip_label", "")),
                            str(lane.get("lane_index", "")),
                            str(lane.get("lane_label", "")),
                            f"{float(lane.get('raw_intensity', 0.0)):.1f}",
                            f"{float(lane.get('background_value', 0.0)):.1f}",
                            f"{float(lane.get('bg_subtracted', 0.0)):.1f}",
                            norm_text,
                            fold_text,
                            "; ".join(str(flag) for flag in lane.get("qc_flags", [])),
                        ]
                    )
                )
        QApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage(tr("densitometry_table_copied_status"), 2500)

    def run_wb_statistics(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        densitometry_runs = list(panel.data.get("densitometry_runs", []))
        if not densitometry_runs and panel.data.get("densitometry"):
            densitometry_runs = [panel.data["densitometry"]]
        if not densitometry_runs:
            QMessageBox.information(
                self,
                tr("wb_statistics_title"),
                tr("run_densitometry_before_statistics"),
            )
            return
        before = self._capture_wb_state()
        from core.wb_statistics import collect_wb_stat_values, compute_group_statistics

        rows = collect_wb_stat_values(densitometry_runs)
        assignments=self.data_panel.wb_stats_group_assignments()
        rows=[row for row in rows if row.get('lane_label') in assignments]
        from core.statistics_pairing import attach_pair_ids
        rows = attach_pair_ids(panel, 'wb', rows)
        result = compute_group_statistics(
            rows,
            assignments,
            self.data_panel.wb_stats_reference_group_combo.currentText().strip() or self.data_panel.wb_stats_reference_group_combo.currentText(),
            self.data_panel.wb_stats_test_combo.currentText(),
        )
        panel.data["wb_statistics"] = result
        panel.append_audit("run_wb_statistics", f"test={result.get('test')}; reference={result.get('reference_group')}")
        self.data_panel.populate_wb_stats_results(result)
        self._refresh_wb_stats_chart(result)
        self._record_wb_change("Run WB statistics", before)
        self.statusBar().showMessage(tr("wb_statistics_complete_status"), 4000)

    def add_wb_statistics_plot_to_layout(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        stats_result = dict(panel.data.get("wb_statistics", {}))
        if not stats_result.get("plot_data"):
            QMessageBox.information(
                self,
                tr("wb_statistics_title"),
                tr("run_statistics_first"),
            )
            return
        if not panel.data.get("wb_layout", {}).get("enabled") and self._wb_band_sources_for_ui(panel):
            self.apply_wb_nature_layout(preview=True)
            panel = self._active_wb_panel()
        before = self._capture_wb_state()
        layout = dict(panel.data.get("wb_layout", {}))
        plot = dict(layout.get("stats_plot", {}))
        plot.update({"enabled": True, "width_px": 520, "height_px": 360})
        layout["stats_plot"] = plot
        panel.data["wb_layout"] = layout
        panel.append_audit("add_wb_statistics_plot_to_layout", "enabled")
        self._refresh_wb_ui(panel)
        self._record_wb_change("Add WB statistics plot to layout", before)
        self.statusBar().showMessage(tr("wb_statistics_plot_added_status"), 4000)

    def _store_wb_stats_plot_style_from_controls(self, record_history: bool) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        before = self._capture_wb_state() if record_history else None
        layout = dict(panel.data.get("wb_layout", {}))
        stats_plot = dict(layout.get("stats_plot", {}))
        stats_plot["enabled"] = True
        style_payload = dict(self.data_panel.wb_stats_plot_style())
        stats_plot["width_px"] = int(style_payload.pop("width_px", stats_plot.get("width_px", 520)))
        stats_plot["height_px"] = int(style_payload.pop("height_px", stats_plot.get("height_px", 360)))
        current_style = dict(stats_plot.get("style", {}))
        current_style.update(style_payload)
        stats_plot["style"] = current_style
        layout["stats_plot"] = stats_plot
        panel.data["wb_layout"] = layout
        if record_history:
            panel.append_audit("style_wb_statistics_plot", str(current_style))
        self._refresh_wb_ui(panel)
        if record_history and before is not None:
            self._record_wb_change("Style WB statistics plot", before)
            self.statusBar().showMessage(tr("wb_statistics_plot_style_status"), 2500)

    def preview_wb_stats_plot_style_live(self) -> None:
        self._store_wb_stats_plot_style_from_controls(False)

    def apply_wb_stats_plot_style(self) -> None:
        self._store_wb_stats_plot_style_from_controls(True)

    def _refresh_wb_density_chart(self, result: dict) -> None:
        layout = self.data_panel.wb_density_chart_layout
        for index in reversed(range(layout.count())):
            widget = layout.itemAt(index).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

            figure = build_densitometry_figure(
                result,
                preset_name=str(self._active_wb_panel().style.get("preset", "Nature")),
            )
            chart = FigureCanvasQTAgg(figure)
            chart.setFixedHeight(200)
            chart.setStyleSheet("background: white;")
            layout.addWidget(chart)
            chart.draw()
        except Exception:
            return

    def _refresh_wb_stats_chart(self, result: dict) -> None:
        layout = self.data_panel.wb_stats_chart_layout
        for index in reversed(range(layout.count())):
            widget = layout.itemAt(index).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

            panel = self._active_wb_panel()
            stats_plot = dict(panel.data.get("wb_layout", {}).get("stats_plot", {}))
            style = resolve_statistics_plot_style(str(panel.style.get("preset", "Nature")), saved_style=dict(stats_plot.get("style", {})))
            figure = build_wb_statistics_figure(result, style=style)
            chart = FigureCanvasQTAgg(figure)
            chart.setMinimumHeight(260)
            chart.setMaximumHeight(320)
            chart.setStyleSheet("background: white;")
            layout.addWidget(chart)
            chart.draw()
        except Exception:
            return

    def _is_graphics_item_descendant(self, item, ancestor) -> bool:
        current = item
        while current is not None:
            if current is ancestor:
                return True
            current = current.parentItem()
        return False

    def _wb_scene_item_qimage(self, item, dpi: int) -> QImage:
        if item is None:
            return QImage()
        source_rect = self._wb_scene_item_bounds(item)
        if source_rect.isNull() or source_rect.width() <= 0 or source_rect.height() <= 0:
            return QImage()
        scale = max(float(dpi), 72.0) / 72.0
        width = max(1, int(round(source_rect.width() * scale)))
        height = max(1, int(round(source_rect.height() * scale)))
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        dots_per_meter = int(round(dpi / 0.0254))
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        image.fill(QColor("white"))
        hidden_items = []
        allowed_items = set()
        if item in {self.wb_canvas._group, self.wb_canvas._stats_group}:
            allowed_items.update(self.wb_canvas._group_attached_items.get(item, []))
        for scene_item in self.wb_canvas.scene.items():
            if self._is_graphics_item_descendant(scene_item, item):
                continue
            if scene_item in allowed_items:
                continue
            hidden_items.append((scene_item, scene_item.isVisible()))
            scene_item.setVisible(False)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.wb_canvas.scene.render(painter, QRectF(0, 0, width, height), source_rect)
        painter.end()
        for scene_item, visible in hidden_items:
            scene_item.setVisible(visible)
        return image

    def _wb_scene_item_bounds(self, item) -> QRectF:
        if item is None:
            return QRectF()
        source_rect = item.sceneBoundingRect()
        if item in {self.wb_canvas._group, self.wb_canvas._stats_group}:
            for attached in self.wb_canvas._group_attached_items.get(item, []):
                source_rect = source_rect.united(attached.sceneBoundingRect())
        return source_rect.adjusted(-4.0, -4.0, 4.0, 4.0)

    def _export_wb_scene_item_svg(self, item, target_path: Path) -> Path | None:
        if item is None:
            return None
        source_rect = self._wb_scene_item_bounds(item)
        if source_rect.isNull() or source_rect.width() <= 0 or source_rect.height() <= 0:
            return None
        hidden_items = []
        allowed_items = set()
        if item in {self.wb_canvas._group, self.wb_canvas._stats_group}:
            allowed_items.update(self.wb_canvas._group_attached_items.get(item, []))
        for scene_item in self.wb_canvas.scene.items():
            if self._is_graphics_item_descendant(scene_item, item):
                continue
            if scene_item in allowed_items:
                continue
            hidden_items.append((scene_item, scene_item.isVisible()))
            scene_item.setVisible(False)
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            generator = QSvgGenerator()
            generator.setFileName(str(target_path))
            generator.setSize(QSize(max(1, int(source_rect.width())), max(1, int(source_rect.height()))))
            generator.setViewBox(QRectF(0, 0, source_rect.width(), source_rect.height()))
            painter = QPainter(generator)
            painter.fillRect(QRectF(0, 0, source_rect.width(), source_rect.height()), QColor(255, 255, 255))
            self.wb_canvas.scene.render(painter, QRectF(0, 0, source_rect.width(), source_rect.height()), source_rect)
            painter.end()
            return target_path
        finally:
            for scene_item, visible in hidden_items:
                scene_item.setVisible(visible)

    def export_selected_wb_items_to_composer(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        selected_items = self.data_panel.selected_wb_export_items()
        if not selected_items:
            QMessageBox.information(
                self,
                tr("wb_export_title"),
                tr("select_export_item"),
            )
            return
        asset_dir = self._generated_asset_dir() / "wb_export_items"
        asset_dir.mkdir(parents=True, exist_ok=True)
        created = 0
        self.wb_canvas.set_panel(panel)
        for key in selected_items:
            if key == "wb_layout":
                target_path = asset_dir / f"wb_layout_{uuid4().hex[:8]}.svg"
                exported = self._export_wb_scene_item_svg(self.wb_canvas._group, target_path)
                if exported is None or not exported.exists():
                    continue
            elif key == "wb_stats_plot":
                if not panel.data.get("wb_statistics"):
                    continue
                target_path = asset_dir / f"wb_stats_{uuid4().hex[:8]}.svg"
                stats_plot = dict(panel.data.get("wb_layout", {}).get("stats_plot", {}))
                figure = build_wb_statistics_figure(
                    dict(panel.data.get("wb_statistics", {})),
                    style=resolve_statistics_plot_style(
                        str(panel.style.get("preset", "Nature")),
                        saved_style=dict(stats_plot.get("style", {})),
                    ),
                    chart_kind=str(stats_plot.get("chart_kind", "") or "") or None,
                )
                try:
                    from matplotlib import pyplot as plt
                    from matplotlib import rc_context

                    svg_buffer = BytesIO()
                    with rc_context({"svg.fonttype": "none"}):
                        figure.savefig(svg_buffer, format="svg", bbox_inches="tight", facecolor="white")
                    target_path.write_bytes(
                        apply_svg_text_overrides(
                            svg_buffer.getvalue(),
                            dict(stats_plot.get("svg_text_overrides", {})),
                        )
                    )
                finally:
                    plt.close(figure)
            else:
                continue
            asset_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_{key}")
            asset_panel.import_image(target_path)
            if key == "wb_layout":
                bounds = self._wb_scene_item_bounds(self.wb_canvas._group)
                export_item = wb_export_item_for_scene_bounds(
                    panel,
                    key,
                    bounds.width(),
                    bounds.height(),
                    dpi=WB_COMPOSER_EXPORT_DPI,
                    image_path=target_path,
                    source_format="SVG",
                )
            elif key == "wb_stats_plot" and self.wb_canvas._stats_group is not None:
                bounds = self._wb_scene_item_bounds(self.wb_canvas._stats_group)
                export_item = wb_export_item_for_scene_bounds(
                    panel,
                    key,
                    bounds.width(),
                    bounds.height(),
                    dpi=WB_COMPOSER_EXPORT_DPI,
                    image_path=target_path,
                    source_format="SVG",
                )
            else:
                exported_image = QImage(str(target_path))
                if not exported_image.isNull():
                    export_item = wb_export_item_for_scene_bounds(
                        panel,
                        key,
                        exported_image.width() * 72.0 / WB_COMPOSER_EXPORT_DPI,
                        exported_image.height() * 72.0 / WB_COMPOSER_EXPORT_DPI,
                        dpi=WB_COMPOSER_EXPORT_DPI,
                        image_path=target_path,
                        source_format=exported_image.format().name.removeprefix("Format_").upper(),
                    )
                else:
                    continue
            apply_wb_export_item_metadata(asset_panel, export_item)
            self.project.add_panel(asset_panel)
            created += 1
        if created:
            self.refresh_composer_panel_list()
            self.statusBar().showMessage(
                trf(
                    "added_outputs_status",
                    count=created,
                    module=tr("v9_module_wb"),
                ),
                4000,
            )

    def update_wb_layout_text_from_canvas(self, kind: str, index: int, text: str) -> None:
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        layout = dict(panel.data.get("wb_layout", {}))
        item=next((i for i in self.wb_canvas.scene.items() if isinstance(i,EditableMovableTextItem) and i.style_payload()==(kind,index)),None)
        if item is not None:
            layout.setdefault('text_styles',{}).setdefault(kind,{}).setdefault('items',{}).setdefault(str(index),{})['text_payload']=item.to_layout_object().to_dict()['metadata'].get('text',{})
        bands = list(layout.get("bands", []))
        if kind.startswith("stats_"):
            stats_plot = dict(layout.get("stats_plot", {}))
            labels = dict(stats_plot.get("labels", {}))
            kind_labels = dict(labels.get(kind, {}))
            kind_labels[str(index)] = text.strip()
            labels[kind] = kind_labels
            stats_plot["labels"] = labels
            layout["stats_plot"] = stats_plot
        elif kind == "lane":
            labels = list(layout.get("lane_labels", []))
            if 0 <= index < len(labels):
                labels[index] = text.strip()
                layout["lane_labels"] = labels
                panel.data.setdefault("annotations", {})["lane_labels"] = labels
        elif kind == "protein" and 0 <= index < len(bands):
            bands[index]["protein"] = text.strip()
            crops = panel.data.get("crops", [])
            if index < len(crops):
                crops[index]["protein"] = text.strip()
        elif kind == "marker" and 0 <= index < len(bands):
            bands[index]["mw_kda"] = text.strip()
        layout["bands"] = bands
        panel.data["wb_layout"] = layout
        panel.append_audit("edit_wb_layout_text", f"{kind}[{index}] = {text}")
        self._record_wb_change("Edit WB layout text", before)

    def update_wb_layout_item_position(self, kind: str, index: int, x: float, y: float) -> None:
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        layout = dict(panel.data.get("wb_layout", {}))
        if kind.startswith("stats_"):
            stats_plot = dict(layout.get("stats_plot", {}))
            label_offsets = dict(stats_plot.get("label_offsets", {}))
            kind_offsets = dict(label_offsets.get(kind, {}))
            anchor = self.wb_canvas.stats_label_anchor(kind, index)
            if anchor is None:
                kind_offsets[str(index)] = {"dx": 0.0, "dy": 0.0}
            else:
                kind_offsets[str(index)] = {"dx": round(float(x - anchor.x()), 1), "dy": round(float(y - anchor.y()), 1)}
            label_offsets[kind] = kind_offsets
            stats_plot["label_offsets"] = label_offsets
            layout["stats_plot"] = stats_plot
            panel.data["wb_layout"] = layout
            panel.append_audit("move_wb_layout_item", f"{kind}[{index}] -> ({x:.1f}, {y:.1f})")
            self._record_wb_change("Move WB layout item", before)
            return
        positions = dict(layout.get("manual_positions", {}))
        kind_positions = dict(positions.get(kind, {}))
        kind_positions[str(index)] = {"x": float(x), "y": float(y)}
        positions[kind] = kind_positions
        layout["manual_positions"] = positions
        panel.data["wb_layout"] = layout
        panel.append_audit("move_wb_layout_item", f"{kind}[{index}] -> ({x:.1f}, {y:.1f})")
        self._record_wb_change("Move WB layout item", before)

    def update_wb_layout_line_position(self, uid: str, x: float, y: float) -> None:
        panel = self._active_wb_panel()
        layout = dict(panel.data.get("wb_layout", {}))
        line_styles = dict(layout.get("line_styles", {}))
        style = dict(line_styles.get(uid, {}))
        style["pos"] = {"x": float(x), "y": float(y)}
        line_styles[uid] = style
        layout["line_styles"] = line_styles
        panel.data["wb_layout"] = layout
        panel.touch()

    def _store_wb_layout_item_position(self, panel: WesternBlotPanel, kind: str, index: int, x: float, y: float) -> None:
        layout = dict(panel.data.get("wb_layout", {}))
        if kind.startswith("stats_"):
            stats_plot = dict(layout.get("stats_plot", {}))
            label_offsets = dict(stats_plot.get("label_offsets", {}))
            kind_offsets = dict(label_offsets.get(kind, {}))
            anchor = self.wb_canvas.stats_label_anchor(kind, index)
            if anchor is None:
                kind_offsets[str(index)] = {"dx": 0.0, "dy": 0.0}
            else:
                kind_offsets[str(index)] = {"dx": round(float(x - anchor.x()), 1), "dy": round(float(y - anchor.y()), 1)}
            label_offsets[kind] = kind_offsets
            stats_plot["label_offsets"] = label_offsets
            layout["stats_plot"] = stats_plot
        else:
            positions = dict(layout.get("manual_positions", {}))
            kind_positions = dict(positions.get(kind, {}))
            kind_positions[str(index)] = {"x": float(x), "y": float(y)}
            positions[kind] = kind_positions
            layout["manual_positions"] = positions
        panel.data["wb_layout"] = layout
        panel.touch()

    def _store_wb_layout_line_state(
        self,
        panel: WesternBlotPanel,
        uid: str,
        x: float,
        y: float,
        color: str | None = None,
        width: float | None = None,
    ) -> None:
        layout = dict(panel.data.get("wb_layout", {}))
        line_styles = dict(layout.get("line_styles", {}))
        style = dict(line_styles.get(uid, {}))
        style["pos"] = {"x": float(x), "y": float(y)}
        if color is not None:
            style["color"] = color
        if width is not None:
            style["width"] = float(width)
        line_styles[uid] = style
        layout["line_styles"] = line_styles
        panel.data["wb_layout"] = layout
        panel.touch()

    def update_wb_layout_text_style(self, kind: str, index: int, style: dict, apply_all: bool) -> None:
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        if apply_all:
            selected_items = [
                item
                for item in self.wb_canvas.scene.items()
                if isinstance(item, EditableMovableTextItem) and item.style_payload()[0] == kind
            ]
        else:
            selected_items = [
                item
                for item in self.wb_canvas.scene.items()
                if isinstance(item, EditableMovableTextItem) and item.style_payload() == (kind, index)
            ]
        style=dict(style)
        self._apply_text_style_to_items(selected_items, style)
        if len(selected_items)==1:
            style['text_payload']=selected_items[0].to_layout_object().to_dict()['metadata'].get('text',{})
        layout = dict(panel.data.get("wb_layout", {}))
        styles = dict(layout.get("text_styles", {}))
        kind_styles = dict(styles.get(kind, {}))
        if apply_all:
            current_default = dict(kind_styles.get("default", {}))
            current_default.update(style)
            kind_styles["default"] = current_default
            kind_styles["items"] = {}
        else:
            items = dict(kind_styles.get("items", {}))
            item_style = dict(items.get(str(index), {}))
            item_style.update(style)
            items[str(index)] = item_style
            kind_styles["items"] = items
        styles[kind] = kind_styles
        layout["text_styles"] = styles
        panel.data["wb_layout"] = layout
        panel.append_audit("style_wb_layout_text", f"{kind}[{index}] {style} apply_all={apply_all}")
        self._record_wb_change("Style WB layout text", before)

    def _wb_layout_text_items(self) -> list[EditableMovableTextItem]:
        return [item for item in self.wb_canvas.scene.items() if isinstance(item, EditableMovableTextItem)]

    def _wb_line_level_key(self, uid: str) -> str:
        return str(uid).split(":", 1)[0]

    def _wb_layout_line_items_for_level(self, level_key: str):
        return [
            item
            for item in self.wb_canvas.scene.items()
            if item.data(0) == "wb_layout_line" and self._wb_line_level_key(str(item.data(1) or "")) == level_key
        ]

    def _move_text_item_scene_center(self, panel: WesternBlotPanel, item: EditableMovableTextItem, center_x: float | None = None, center_y: float | None = None) -> None:
        rect = item.sceneBoundingRect()
        dx = 0.0 if center_x is None else float(center_x - rect.center().x())
        dy = 0.0 if center_y is None else float(center_y - rect.center().y())
        if dx == 0.0 and dy == 0.0:
            return
        item.set_position_without_callback(float(item.pos().x() + dx), float(item.pos().y() + dy))
        kind, index = item.style_payload()
        relative = self.wb_canvas.relative_position_for_item(item)
        self._store_wb_layout_item_position(panel, kind, index, float(relative.x()), float(relative.y()))

    def align_wb_selected_row(self) -> None:
        if self.active_module != "wb":
            return
        reference = self._current_wb_text_target()
        if reference is None:
            return
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        selected = self.selected_wb_text_items()
        owner = self.wb_canvas._attached_item_groups.get(reference)
        ref_y = reference.sceneBoundingRect().center().y()
        if len(selected) <= 1:
            selected = [
                item
                for item in self._wb_layout_text_items()
                if self.wb_canvas._attached_item_groups.get(item) is owner
                and abs(item.sceneBoundingRect().center().y() - ref_y) <= 18.0
            ]
        for item in selected:
            self._move_text_item_scene_center(panel, item, center_y=ref_y)
        panel.append_audit("align_wb_layout_row", f"{len(selected)} text item(s)")
        self._record_wb_change("Align WB layout row", before)

    def align_wb_selected_column(self) -> None:
        if self.active_module != "wb":
            return
        reference = self._current_wb_text_target()
        if reference is None:
            return
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        selected = self.selected_wb_text_items()
        owner = self.wb_canvas._attached_item_groups.get(reference)
        ref_x = reference.sceneBoundingRect().center().x()
        if len(selected) <= 1:
            selected = [
                item
                for item in self._wb_layout_text_items()
                if self.wb_canvas._attached_item_groups.get(item) is owner
                and abs(item.sceneBoundingRect().center().x() - ref_x) <= 24.0
            ]
        for item in selected:
            self._move_text_item_scene_center(panel, item, center_x=ref_x)
        panel.append_audit("align_wb_layout_column", f"{len(selected)} text item(s)")
        self._record_wb_change("Align WB layout column", before)

    def recenter_wb_group_labels(self, record_history: bool = True) -> None:
        if self.active_module != "wb":
            return
        selected_line = self._current_wb_line_target()
        level_key = self._wb_line_level_key(str(selected_line.data(1))) if selected_line is not None else ""
        panel = self._active_wb_panel()
        before = self._capture_wb_state() if record_history else None
        lines = self._wb_layout_line_items_for_level(level_key) if level_key else [
            item for item in self.wb_canvas.scene.items() if item.data(0) == "wb_layout_line"
        ]
        moved = 0
        for line in lines:
            uid = str(line.data(1) or "")
            if ":" not in uid:
                continue
            kind, raw_index = uid.split(":", 1)
            try:
                index = int(raw_index)
            except ValueError:
                continue
            label = next((item for item in self._wb_layout_text_items() if item.style_payload() == (kind, index)), None)
            if label is None:
                continue
            self._move_text_item_scene_center(panel, label, center_x=line.sceneBoundingRect().center().x())
            moved += 1
        if record_history and before is not None:
            panel.append_audit("recenter_wb_group_labels", f"{moved} label(s)")
            self._record_wb_change("Recenter WB group labels", before)

    def sync_wb_selected_line_level(self) -> None:
        if self.active_module != "wb":
            return
        reference = self._current_wb_line_target()
        if reference is None:
            return
        uid = str(reference.data(1) or "")
        if not uid:
            return
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        level_key = self._wb_line_level_key(uid)
        ref_pen = reference.pen()
        ref_y = float(reference.pos().y())
        color = ref_pen.color().name().upper()
        width = float(ref_pen.widthF())
        lines = self._wb_layout_line_items_for_level(level_key)
        for line in lines:
            line.setPen(QPen(QColor(color), width))
            setter = getattr(line, "set_position_without_callback", None)
            if callable(setter):
                setter(float(line.pos().x()), ref_y)
            else:
                line.setPos(float(line.pos().x()), ref_y)
            line_uid = str(line.data(1) or "")
            relative = self.wb_canvas.relative_position_for_item(line)
            self._store_wb_layout_line_state(panel, line_uid, float(relative.x()), float(relative.y()), color=color, width=width)
        self.recenter_wb_group_labels(record_history=False)
        panel.append_audit("sync_wb_layout_lines", f"{len(lines)} line(s) level={level_key}")
        self._record_wb_change("Sync WB layout lines", before)

    def selected_wb_text_items(self) -> list[EditableMovableTextItem]:
        if not hasattr(self, "wb_canvas"):
            return []
        return [
            item
            for item in self.wb_canvas.scene.items()
            if isinstance(item, EditableMovableTextItem) and item.isSelected()
        ]

    def selected_wb_line_items(self):
        if not hasattr(self, "wb_canvas"):
            return []
        from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsPathItem

        return [
            item
            for item in self.wb_canvas.scene.items()
            if isinstance(item, (QGraphicsLineItem, QGraphicsPathItem)) and item.data(0) == "wb_layout_line" and item.isSelected()
        ]

    def _current_wb_text_target(self) -> EditableMovableTextItem | None:
        selected = self.selected_wb_text_items()
        if len(selected) == 1:
            self._active_wb_text_item = selected[0]
            return selected[0]
        if any(item is self._active_wb_text_item for item in self.wb_canvas.scene.items()):
            return self._active_wb_text_item
        self._active_wb_text_item = None
        return selected[0] if selected else None

    def _current_wb_line_target(self):
        selected = self.selected_wb_line_items()
        if len(selected) == 1:
            self._active_wb_line_item = selected[0]
            return selected[0]
        if any(item is self._active_wb_line_item for item in self.wb_canvas.scene.items()):
            return self._active_wb_line_item
        self._active_wb_line_item = None
        return selected[0] if selected else None

    def sync_wb_text_toolbar_from_selection(self) -> None:
        selected = self.selected_wb_text_items()
        selected_lines = self.selected_wb_line_items()
        enabled = len(selected) >= 1
        self.data_panel.set_wb_text_toolbar_enabled(enabled)
        if len(selected) == 1:
            self._active_wb_text_item = selected[0]
        if len(selected_lines) == 1:
            self._active_wb_line_item = selected_lines[0]
            line = selected_lines[0]
            line_widgets = [
                self.data_panel.wb_line_width_spin,
                self.data_panel.wb_line_color_combo,
                self.data_panel.wb_line_x_spin,
                self.data_panel.wb_line_y_spin,
            ]
            for widget in line_widgets:
                widget.blockSignals(True)
            self.data_panel.wb_line_width_spin.setValue(line.pen().widthF())
            self.data_panel.set_wb_color_combo_value(self.data_panel.wb_line_color_combo, line.pen().color().name().upper())
            self.data_panel.wb_line_x_spin.setValue(float(line.pos().x()))
            self.data_panel.wb_line_y_spin.setValue(float(line.pos().y()))
            for widget in line_widgets:
                widget.blockSignals(False)
        if not enabled or len(selected) != 1:
            return
        item = selected[0]
        font = item.font()
        widgets = [
            self.data_panel.wb_text_font_combo,
            self.data_panel.wb_text_size_spin,
            self.data_panel.wb_text_bold_button,
            self.data_panel.wb_text_italic_button,
            self.data_panel.wb_text_color_combo,
            self.data_panel.wb_text_rotation_spin,
            self.data_panel.wb_text_x_spin,
            self.data_panel.wb_text_y_spin,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        self.data_panel.wb_text_font_combo.setCurrentFont(font)
        self.data_panel.wb_text_size_spin.setValue(font.pointSizeF() if font.pointSizeF() > 0 else 7.0)
        self.data_panel.wb_text_bold_button.setChecked(font.bold())
        self.data_panel.wb_text_italic_button.setChecked(font.italic())
        self.data_panel.set_wb_color_combo_value(self.data_panel.wb_text_color_combo, item.defaultTextColor().name().upper())
        self.data_panel.wb_text_rotation_spin.setValue(float(item.rotation()))
        self.data_panel.wb_text_x_spin.setValue(float(item.pos().x()))
        self.data_panel.wb_text_y_spin.setValue(float(item.pos().y()))
        for widget in widgets:
            widget.blockSignals(False)

    def apply_wb_selected_text_style(self, style: dict) -> None:
        if self.active_module != "wb":
            return
        target = self._current_wb_text_target()
        if target is None:
            return
        selected = self.selected_wb_text_items() or [target]
        panel = self._active_wb_panel()
        before = self._capture_wb_state()
        self._apply_text_style_to_items(selected, style)
        layout = dict(panel.data.get("wb_layout", {}))
        styles = dict(layout.get("text_styles", {}))
        payloads = [item.style_payload() for item in selected]
        for kind, index in payloads:
            kind_styles = dict(styles.get(kind, {}))
            items = dict(kind_styles.get("items", {}))
            item_style = dict(items.get(str(index), {}))
            item_style.update(style)
            target=next(item for item in selected if item.style_payload()==(kind,index))
            item_style['text_payload']=target.to_layout_object().to_dict()['metadata'].get('text',{})
            items[str(index)] = item_style
            kind_styles["items"] = items
            styles[kind] = kind_styles
        layout["text_styles"] = styles
        panel.data["wb_layout"] = layout
        panel.append_audit("style_wb_layout_text", f"{payloads} {style}")
        self._record_wb_change("Style WB layout text", before)

    def apply_wb_selected_text_style_to_type(self) -> None:
        selected = self.selected_wb_text_items()
        if not selected:
            return
        item = selected[0]
        kind, index = item.style_payload()
        style = {
            "font_family": item.font().family(),
            "font_size_pt": item.font().pointSizeF() if item.font().pointSizeF() > 0 else 7.0,
            "bold": item.font().bold(),
            "italic": item.font().italic(),
            "color": item.defaultTextColor().name(),
            "rotation": float(item.rotation()),
        }
        for scene_item in self.wb_canvas.scene.items():
            if isinstance(scene_item, EditableMovableTextItem) and scene_item.style_payload()[0] == kind:
                self._apply_text_style_to_items([scene_item], style)
        self.update_wb_layout_text_style(
            kind,
            index,
            style,
            True,
        )

    def apply_wb_selected_line_style(self) -> None:
        self._apply_wb_selected_line_style(record_history=True)

    def live_wb_selected_line_change(self, width: float | None = None, color: str | None = None) -> None:
        self._apply_wb_selected_line_style(record_history=False, width=width, color=color)

    def _apply_wb_selected_line_style(self, record_history: bool, width: float | None = None, color: str | None = None) -> None:
        if self.active_module != "wb":
            return
        target = self._current_wb_line_target()
        if target is None:
            return
        selected = self.selected_wb_line_items() or [target]
        panel = self._active_wb_panel()
        before = self._capture_wb_state() if record_history else None
        color = self.data_panel.wb_color_combo_value(self.data_panel.wb_line_color_combo) if color is None else color
        width = float(self.data_panel.wb_line_width_spin.value()) if width is None else width
        layout = dict(panel.data.get("wb_layout", {}))
        line_styles = dict(layout.get("line_styles", {}))
        for item in selected:
            uid = str(item.data(1) or "")
            item.setPen(QPen(QColor(color), width))
            if uid:
                style = dict(line_styles.get(uid, {}))
                style.update({"color": color, "width": width})
                line_styles[uid] = style
        layout["line_styles"] = line_styles
        panel.data["wb_layout"] = layout
        if record_history and before is not None:
            panel.append_audit("style_wb_layout_line", f"{len(selected)} line(s) color={color} width={width}")
            self._record_wb_change("Style WB layout line", before)

    def move_wb_selected_text(self, x: float | None = None, y: float | None = None) -> None:
        item = self._current_wb_text_target()
        if item is None:
            return
        new_x = item.pos().x() if x is None else x
        new_y = item.pos().y() if y is None else y
        item.setPos(new_x, new_y)
        kind, index = item.style_payload()
        relative = self.wb_canvas.relative_position_for_item(item)
        self.update_wb_layout_item_position(kind, index, float(relative.x()), float(relative.y()))

    def move_wb_selected_line(self, x: float | None = None, y: float | None = None) -> None:
        item = self._current_wb_line_target()
        if item is None:
            return
        new_x = item.pos().x() if x is None else x
        new_y = item.pos().y() if y is None else y
        item.setPos(new_x, new_y)
        uid = str(item.data(1) or "")
        if uid:
            relative = self.wb_canvas.relative_position_for_item(item)
            self.update_wb_layout_line_position(uid, float(relative.x()), float(relative.y()))

    def _apply_text_style_to_items(self, items: list[EditableMovableTextItem], style: dict) -> None:
        from ui.widgets.shared_layout_controls import apply_selection_style
        for item in items:
            enabled = item._edits.enabled
            item._edits.enabled = False
            try:
                apply_selection_style([item], style)
                if "rotation" in style:item.setRotation(float(style["rotation"]))
                item._fit_text_width()
            finally:
                item._edits.enabled = enabled

    def undo_wb(self) -> None:
        if self.active_module != "wb":
            return
        if self.wb_history.undo():
            self.statusBar().showMessage(tr("wb_undo_status"), 2500)

    def redo_wb(self) -> None:
        if self.active_module != "wb":
            return
        if self.wb_history.redo():
            self.statusBar().showMessage(tr("wb_redo_status"), 2500)

    def reset_wb_to_original(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        if not panel.data.get("image_data"):
            return
        response = QMessageBox.question(
            self,
            tr("reset_wb_title"),
            tr("reset_wb_body"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        panel.reset_to_original()
        self.wb_history.clear()
        self.data_panel.wb_rotation_spin.setValue(0.0)
        self.data_panel.wb_fine_rotation_slider.setValue(0)
        self.data_panel.wb_brightness_spin.setValue(0)
        self.data_panel.wb_contrast_spin.setValue(0)
        self._refresh_wb_ui(panel)
        self.statusBar().showMessage(tr("wb_reset_status"), 3500)

    def enable_wb_lane_anchor_mode(self) -> None:
        self._pending_lane_edge_x = None
        self.wb_canvas.set_mode("lane")
        self.statusBar().showMessage(tr("lane_calibration_status"), 5000)

    def enable_wb_roi_mode(self) -> None:
        self._pending_lane_edge_x = None
        self.wb_canvas.set_mode("roi")
        self.statusBar().showMessage(tr("roi_selection_mode_status"), 5000)

    def enable_wb_move_mode(self) -> None:
        self._pending_lane_edge_x = None
        self.wb_canvas.set_mode("move")
        self.statusBar().showMessage(tr("move_image_mode_status"), 5000)

    def enable_wb_text_box_select_mode(self) -> None:
        self._pending_lane_edge_x = None
        self.wb_canvas.set_mode("text_select")
        self.statusBar().showMessage(tr("box_select_text_mode_status"), 5000)

    def enable_wb_marker_anchor_mode(self) -> None:
        self._pending_lane_edge_x = None
        self.wb_canvas.set_mode("marker")
        self.statusBar().showMessage(tr("marker_anchor_mode_status"), 5000)

    def clear_wb_anchors(self) -> None:
        panel = self._active_wb_panel()
        self._pending_lane_edge_x = None
        before = self._capture_wb_state()
        panel.clear_annotation_anchors()
        self._refresh_wb_ui(panel)
        self._record_wb_change("Clear WB anchors", before)

    def auto_detect_wb_lanes(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        lane_count = self.data_panel.wb_lane_count_spin.value()
        anchors = self.wb_canvas.detect_lane_centers(lane_count)
        if len(anchors) != lane_count:
            QMessageBox.information(
                self,
                tr("auto_detect_lanes_title"),
                tr("auto_detect_lanes_failed"),
            )
            return
        before = self._capture_wb_state()
        panel.set_annotations(
            self.data_panel.wb_lane_labels_for_count(),
            [self.data_panel.wb_protein_edit.text().strip()] if self.data_panel.wb_protein_edit.text().strip() else [],
            self.data_panel.wb_marker_labels(),
        )
        panel.set_detected_lane_anchors(anchors)
        self._refresh_wb_ui(panel)
        self._record_wb_change("Auto detect WB lanes", before)
        self.statusBar().showMessage(
            trf("detected_lane_centers_status", count=len(anchors)),
            5000,
        )

    def update_wb_transform(self) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        if not panel.data.get("image_data"):
            return
        before = self._capture_wb_state()
        if self._editing_band_strips(panel):
            index = self._selected_wb_strip_index(panel)
            if index is None:
                return
            panel.update_band_strip_adjustments(
                index,
                self.data_panel.wb_brightness_spin.value(),
                self.data_panel.wb_contrast_spin.value(),
                self.data_panel.wb_rotation_spin.value() + self.data_panel.wb_fine_rotation_slider.value() / 10.0,
            )
            self._refresh_wb_ui(panel)
            self._record_wb_change("Adjust WB strip display transform", before)
            return
        current = panel.data.get("adjustments", {})
        panel.set_display_adjustments(
            self.data_panel.wb_brightness_spin.value(),
            self.data_panel.wb_contrast_spin.value(),
            rotation=self.data_panel.wb_rotation_spin.value() + self.data_panel.wb_fine_rotation_slider.value() / 10.0,
            flip_horizontal=bool(current.get("flip_horizontal", False)),
            flip_vertical=bool(current.get("flip_vertical", False)),
        )
        self._refresh_wb_ui(panel)
        self._record_wb_change("Adjust WB display transform", before)

    def flip_wb_horizontal(self) -> None:
        self._flip_wb("flip_horizontal")

    def flip_wb_vertical(self) -> None:
        self._flip_wb("flip_vertical")

    def _flip_wb(self, key: str) -> None:
        if self.active_module != "wb":
            return
        panel = self._active_wb_panel()
        if not panel.data.get("image_data"):
            return
        before = self._capture_wb_state()
        if self._editing_band_strips(panel):
            index = self._selected_wb_strip_index(panel)
            if index is None:
                return
            strip = panel.data.get("band_strips", [])[index]
            current = dict(strip.get("adjustments", {}))
            current[key] = not bool(current.get(key, False))
            panel.update_band_strip_adjustments(
                index,
                self.data_panel.wb_brightness_spin.value(),
                self.data_panel.wb_contrast_spin.value(),
                self.data_panel.wb_rotation_spin.value() + self.data_panel.wb_fine_rotation_slider.value() / 10.0,
                flip_horizontal=bool(current.get("flip_horizontal", False)),
                flip_vertical=bool(current.get("flip_vertical", False)),
            )
            self._refresh_wb_ui(panel)
            self._record_wb_change("Flip WB strip image", before)
            return
        current = panel.data.get("adjustments", {})
        current[key] = not bool(current.get(key, False))
        panel.set_display_adjustments(
            self.data_panel.wb_brightness_spin.value(),
            self.data_panel.wb_contrast_spin.value(),
            rotation=self.data_panel.wb_rotation_spin.value() + self.data_panel.wb_fine_rotation_slider.value() / 10.0,
            flip_horizontal=bool(current.get("flip_horizontal", False)),
            flip_vertical=bool(current.get("flip_vertical", False)),
        )
        self._refresh_wb_ui(panel)
        self._record_wb_change("Flip WB image", before)

    def _refresh_qpcr_import_ui(self, panel: QPCRPanel, options: dict) -> None:
        raw_rows = panel.data.get("cleaned_preview_rows", panel.data.get("raw_rows", []))
        summary = str(panel.style.get("analysis_summary", ""))
        self.data_panel.populate_qpcr(
            raw_rows,
            options.get("targets", []),
            options.get("groups", []),
            summary,
            suggested_reference=options.get("suggested_reference_gene"),
        )
        self.preview_canvas.clear_figure()
        bind_i18n(self.preview_canvas.title, "qpcr_imported_title")
        bind_i18n(self.preview_canvas.body, "qpcr_imported_body")
        self.style_sidebar.set_qpcr_statistics("Analysis not run yet.")
        self.status_label.setText(summary)

    def _refresh_qpcr_ui(self, panel: QPCRPanel) -> None:
        raw_rows = panel.data.get("processed_data", {}).get("cleaned_data", panel.data.get("raw_rows", []))
        targets = list(dict.fromkeys([str(row["target"]) for row in panel.data.get("raw_rows", []) if row.get("target")]))
        groups = list(dict.fromkeys([str(row["group"]) for row in raw_rows if row.get("group")]))
        summary = str(panel.style.get("analysis_summary", ""))
        self.data_panel.populate_qpcr(
            raw_rows,
            targets,
            groups,
            summary,
            suggested_reference=panel.analysis.get("reference_gene"),
            selected_control=panel.analysis.get("control_group"),
        )
        plot_rows = panel.data.get("processed_data", {}).get("fold_change", [])
        if plot_rows:
            import pandas as pd

            render_key = (id(panel), repr((plot_rows, panel.analysis.get('statistics', {}), panel.style.get('preset', 'Nature'))))
            self.preview_canvas.set_figure_from_factory(render_key, lambda: build_qpcr_figure(
                pd.DataFrame(plot_rows),
                panel.analysis.get("statistics", {}),
                preset_name=str(panel.style.get("preset", "Nature")),
            ))
        stat_text = self._format_qpcr_stats(panel)
        self.style_sidebar.set_qpcr_statistics(stat_text)
        self.status_label.setText(summary)

    def _format_qpcr_stats(self, panel: QPCRPanel) -> str:
        chunks: list[str] = []
        for target, payload in panel.analysis.get("statistics", {}).get("targets", {}).items():
            comparisons = payload.get("comparisons", [])
            labels = [f"{c.get('group1')} vs {c.get('group2')}: {c.get('label')}" for c in comparisons]
            chunks.append(f"{target}: {payload.get('stat_test')} | " + "; ".join(labels))
        return "\n".join(chunks) if chunks else "No qPCR statistics yet."

    def copy_analysis_summary(self) -> None:
        QApplication.clipboard().setText(self.data_panel.summary.text())
        self.statusBar().showMessage(tr("analysis_summary_copied_status"), 2500)

    def copy_ihc_results_table(self) -> None:
        QApplication.clipboard().setText(self.data_panel.ihc_results_table_tsv())
        self.statusBar().showMessage(tr("full_ihc_results_copied_status"), 2500)

    def _active_ihc_measurement(self, panel: IHCPanel) -> dict | None:
        active_image = panel.active_image()
        if active_image is None:
            return None
        active_image_uid = str(active_image.get("uid", "")).strip()
        selected_uuid = self.data_panel.selected_ihc_measurement_uuid()
        if selected_uuid:
            selected_measurement = panel.measurement_by_uuid(selected_uuid)
            if selected_measurement is not None and str(selected_measurement.get("image_uid", "")).strip() == active_image_uid:
                return selected_measurement
        for measurement in reversed(list(panel.analysis.get("ihc_measurements", []))):
            if str(measurement.get("image_uid", "")).strip() == active_image_uid:
                return measurement
        measurements = list(active_image.get("measurements", []))
        return measurements[-1] if measurements else None

    def _populate_composer_asset_dimensions(self, asset_panel: ExternalImagePanel, image: QImage, dpi: int) -> None:
        if image.isNull():
            return
        asset_panel.data["source_width_px"] = image.width()
        asset_panel.data["source_height_px"] = image.height()
        asset_panel.data["preferred_width_mm"] = image.width() / float(dpi) * 25.4
        asset_panel.data["preferred_height_mm"] = image.height() / float(dpi) * 25.4

    def _attach_measurement_export_metadata(self, asset_panel: ExternalImagePanel, measurement: dict) -> None:
        asset_panel.data["reliability_status"] = self._measurement_reliability_status(measurement)
        asset_panel.data["reliability"] = deepcopy(dict(measurement.get("reliability", {})))
        asset_panel.data["warnings"] = [str(message).strip() for message in measurement.get("warnings", []) if str(message).strip()]
        asset_panel.data["qc_messages"] = [str(message).strip() for message in measurement.get("qc_messages", []) if str(message).strip()]
        asset_panel.data["threshold_source"] = str(measurement.get("threshold_source", "")).strip()

    @staticmethod
    def _composer_output_tracking_record(asset_panel: ExternalImagePanel) -> dict[str, object]:
        return {
            "panel_uuid": asset_panel.panel_uuid,
            "source_module": str(asset_panel.data.get("source_module", "")),
            "image_path": str(asset_panel.data.get("image_path", "")),
            "export_item_key": str(asset_panel.data.get("export_item_key", "")),
            "source_panel_uuid": str(asset_panel.data.get("source_panel_uuid", "")),
        }

    def _append_composer_output_tracking(self, panel, asset_panel: ExternalImagePanel) -> list[dict]:
        composer_outputs = list(panel.data.get("composer_outputs", []))
        composer_outputs.append(MainWindow._composer_output_tracking_record(asset_panel))
        panel.data["composer_outputs"] = composer_outputs
        return composer_outputs

    @staticmethod
    def _layout_output_chart_settings(panel: Any, layout_key: str, export_item_key: str) -> tuple[str, dict[str, str]]:
        data = getattr(panel, "data", {})
        layout = dict(data.get(layout_key, {}) or {}) if isinstance(data, dict) else {}
        for output in list(layout.get("outputs", []) or []):
            if not isinstance(output, dict) or str(output.get("export_item_key", "")) != export_item_key:
                continue
            return (
                str(output.get("chart_kind", "") or ""),
                deepcopy(dict(output.get("svg_text_overrides", {}) or {})),
            )
        return "", {}

    def add_ihc_outputs_to_composer(self) -> list[ExternalImagePanel]:
        panel = self._active_ihc_panel()
        try:
            measurement = self._active_ihc_measurement_for_export(panel, action_phrase="adding outputs to Composer")
        except ValueError as exc:
            QMessageBox.information(self, tr("histology_ihc"), str(exc))
            return []

        asset_dir = self._generated_asset_dir() / "ihc_outputs"
        asset_dir.mkdir(parents=True, exist_ok=True)
        created: list[ExternalImagePanel] = []

        overlay_image = self._render_ihc_overlay_image(panel, measurement)
        overlay_path = asset_dir / f"ihc_overlay_{uuid4().hex[:8]}.png"
        if not overlay_image.save(str(overlay_path), "PNG"):
            raise ValueError(f"Could not export IHC overlay image: {overlay_path}")
        overlay_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_ihc_overlay")
        overlay_panel.import_image(overlay_path)
        overlay_panel.data["source_module"] = "ihc_overlay"
        overlay_panel.data["source_panel_uuid"] = panel.panel_uuid
        overlay_panel.data["source_dpi"] = IHC_COMPOSER_EXPORT_DPI
        overlay_panel.data["measurement_uuid"] = str(measurement.get("measurement_uuid", ""))
        overlay_panel.data["plot_type"] = "measurement_overlay"
        overlay_panel.data["export_item_key"] = "measurement_overlay"
        self._attach_measurement_export_metadata(overlay_panel, measurement)
        self._populate_composer_asset_dimensions(overlay_panel, overlay_image, IHC_COMPOSER_EXPORT_DPI)
        apply_ihc_export_item_metadata(
            overlay_panel,
            ihc_export_item_for_scene_bounds(
                panel,
                IHC_OVERLAY_EXPORT_KEY,
                max(1.0, overlay_image.width() * 72.0 / IHC_COMPOSER_EXPORT_DPI),
                max(1.0, overlay_image.height() * 72.0 / IHC_COMPOSER_EXPORT_DPI),
                dpi=IHC_COMPOSER_EXPORT_DPI,
                image_path=overlay_path,
                source_format="PNG",
                source_width_px=overlay_image.width(),
                source_height_px=overlay_image.height(),
                measurement=measurement,
            ),
        )
        self.project.add_panel(overlay_panel)
        created.append(overlay_panel)

        statistics = dict(panel.analysis.get("ihc_statistics", {}))
        if statistics:
            output_kind, output_overrides = self._layout_output_chart_settings(
                panel,
                "ihc_layout",
                IHC_STATISTICS_EXPORT_KEY,
            )
            chart_path = asset_dir / f"ihc_statistics_{uuid4().hex[:8]}.svg"
            chart_path.write_bytes(
                self._statistics_svg(
                    statistics,
                    str(panel.style.get("preset", "Nature")),
                    output_overrides,
                    chart_kind=output_kind or None,
                )
            )
            chart_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_ihc_statistics")
            chart_panel.import_image(chart_path)
            chart_panel.data["source_module"] = "ihc_statistics"
            chart_panel.data["source_panel_uuid"] = panel.panel_uuid
            chart_panel.data["source_dpi"] = IHC_COMPOSER_EXPORT_DPI
            chart_panel.data["metric"] = str(statistics.get("metric", ""))
            chart_panel.data["plot_type"] = "statistics_chart"
            chart_panel.data["export_item_key"] = "statistics_chart"
            chart_panel.data["chart_kind"] = output_kind
            chart_panel.data["svg_text_overrides"] = deepcopy(output_overrides)
            chart_size = QSvgRenderer(str(chart_path)).defaultSize()
            chart_width = max(1, chart_size.width())
            chart_height = max(1, chart_size.height())
            chart_panel.data["source_width_px"] = chart_width
            chart_panel.data["source_height_px"] = chart_height
            apply_ihc_export_item_metadata(
                chart_panel,
                ihc_export_item_for_scene_bounds(
                    panel,
                    IHC_STATISTICS_EXPORT_KEY,
                    max(1.0, chart_width),
                    max(1.0, chart_height),
                    dpi=IHC_COMPOSER_EXPORT_DPI,
                    image_path=chart_path,
                    source_format="SVG",
                    source_width_px=chart_width,
                    source_height_px=chart_height,
                    metric=str(statistics.get("metric", "")),
                ),
            )
            chart_panel.data["chart_kind"] = output_kind
            chart_panel.data["svg_text_overrides"] = deepcopy(output_overrides)
            self.project.add_panel(chart_panel)
            created.append(chart_panel)

        composer_outputs = list(panel.data.get("composer_outputs", []))
        for asset_panel in created:
            composer_outputs.append(
                {
                    "panel_uuid": asset_panel.panel_uuid,
                    "source_module": str(asset_panel.data.get("source_module", "")),
                    "image_path": str(asset_panel.data.get("image_path", "")),
                    "reliability_status": str(asset_panel.data.get("reliability_status", "")),
                    "warnings": list(asset_panel.data.get("warnings", [])),
                }
            )
        panel.data["composer_outputs"] = composer_outputs
        panel.append_audit("add_ihc_outputs_to_composer", f"{len(created)} item(s)")
        self.data_panel.populate_ihc_output_status(composer_outputs)
        self.refresh_composer_panel_list()
        self.statusBar().showMessage(
            trf(
                "added_outputs_status",
                count=len(created),
                module=tr("v9_module_ihc"),
            ),
            4000,
        )
        return created

    def _statistics_svg(
        self,
        statistics: dict,
        preset: str,
        svg_text_overrides: dict[str, str] | None = None,
        *,
        chart_kind: str | None = None,
    ) -> bytes:
        if not statistics:
            return b""
        from matplotlib import pyplot as plt
        from matplotlib import rc_context

        figure = build_group_statistics_figure(
            statistics,
            style=resolve_statistics_plot_style(str(preset)),
            y_label=str(statistics.get("metric", "Value")),
            chart_kind=chart_kind,
        )
        buffer = BytesIO()
        try:
            with rc_context({"svg.fonttype": "none"}):
                figure.savefig(buffer, format="svg", bbox_inches="tight", facecolor="white")
        finally:
            plt.close(figure)
        return apply_svg_text_overrides(buffer.getvalue(), svg_text_overrides)

    def _brightfield_chart_svg(
        self,
        panel: BrightfieldPanel,
        payload: dict,
        svg_text_overrides: dict[str, str] | None = None,
        *,
        chart_kind: str | None = None,
    ) -> bytes:
        from matplotlib import pyplot as plt
        from matplotlib import rc_context

        if not payload:
            return b""
        figure = self._build_brightfield_chart_figure(panel, payload, chart_kind=chart_kind)
        buffer = BytesIO()
        try:
            with rc_context({"svg.fonttype": "none"}):
                figure.savefig(buffer, format="svg", bbox_inches="tight", facecolor="white")
        finally:
            plt.close(figure)
        return apply_svg_text_overrides(buffer.getvalue(), svg_text_overrides)

    def _ihc_layout_pixmap_provider(self, panel: IHCPanel, measurement: dict | None):
        statistics = dict(panel.analysis.get("ihc_statistics", {}))

        def provider(layout_object):
            if layout_object.asset_key == IHC_SOURCE_IMAGE_KEY:
                return QPixmap.fromImage(panel.display_qimage())
            if layout_object.asset_key == IHC_OVERLAY_EXPORT_KEY:
                if measurement is None:
                    return QPixmap.fromImage(panel.display_qimage())
                return QPixmap.fromImage(self._render_ihc_overlay_image(panel, measurement))
            if layout_object.asset_key == IHC_STATISTICS_EXPORT_KEY:
                return self._statistics_svg(
                    statistics,
                    str(panel.style.get("preset", "Nature")),
                    dict(layout_object.metadata).get("svg_text_overrides", {}),
                    chart_kind=layout_output_chart_kind(panel, layout_object),
                )
            return None

        return provider

    def show_ihc_layout(self) -> None:
        panel = self._active_ihc_panel()
        measurement = self._active_ihc_measurement(panel)
        if measurement is None and panel.active_image() is None:
            QMessageBox.information(
                self,
                tr("histology_ihc"),
                tr("import_ihc_before_layout"),
            )
            return
        panel.data["active_step"] = 3
        if hasattr(self, "data_panel"):
            self.data_panel.set_ihc_step(3, emit_signal=False)
        if hasattr(self.ihc_workspace, "set_step"):
            self.ihc_workspace.set_step(3)
        self.ihc_workspace.set_panel(panel)
        self.ihc_workspace.prepare_layout(measurement=measurement, pixmap_provider=self._ihc_layout_pixmap_provider(panel, measurement))
        self._sync_chart_kind_controls()

    def send_ihc_layout_to_composer(self) -> list[ExternalImagePanel]:
        panel = self._active_ihc_panel()
        measurement = self._active_ihc_measurement(panel)
        if panel.active_image() is None:
            QMessageBox.information(self, tr("histology_ihc"), tr("import_ihc_before_layout"))
            return []
        asset_dir = self._generated_asset_dir() / "ihc_layouts"
        asset_dir.mkdir(parents=True, exist_ok=True)
        self.ihc_workspace.set_panel(panel)
        self.ihc_workspace.prepare_layout(measurement=measurement, pixmap_provider=self._ihc_layout_pixmap_provider(panel, measurement))
        self.ihc_workspace.persist_layout_edits()
        layout_image = self.ihc_workspace.layout_canvas.render_full_layout(dpi=IHC_COMPOSER_EXPORT_DPI)
        layout_path = asset_dir / f"ihc_layout_{uuid4().hex[:8]}.png"
        if not layout_image.save(str(layout_path), "PNG"):
            raise ValueError(f"Could not export IHC layout image: {layout_path}")
        layout_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_ihc_layout")
        layout_panel.import_image(layout_path)
        apply_ihc_export_item_metadata(
            layout_panel,
            ihc_export_item_for_scene_bounds(
                panel,
                IHC_LAYOUT_EXPORT_KEY,
                layout_image.width() * 72.0 / IHC_COMPOSER_EXPORT_DPI,
                layout_image.height() * 72.0 / IHC_COMPOSER_EXPORT_DPI,
                dpi=IHC_COMPOSER_EXPORT_DPI,
                image_path=layout_path,
                source_format="PNG",
                source_width_px=layout_image.width(),
                source_height_px=layout_image.height(),
            ),
        )
        self.project.add_panel(layout_panel)
        self._append_composer_output_tracking(panel, layout_panel)
        created = [layout_panel]
        if measurement is not None:
            created.extend(self.add_ihc_outputs_to_composer())
        self.project.append_audit("ihc_layout_to_composer", f"{len(created)} item(s)")
        self.refresh_composer_panel_list()
        self.statusBar().showMessage(
            trf(
                "added_layout_items_status",
                count=len(created),
                module=tr("v9_module_ihc"),
            ),
            4000,
        )
        return created

    def _edit_statistics_pairs(self, module: str) -> None:
        from ui.dialogs.statistics_pairing_dialog import StatisticsPairingDialog
        from core.statistics_pairing import save_pair_ids
        if module == 'ihc':
            from core.ihc_statistics import collect_ihc_stat_values
            panel = self._active_ihc_panel()
            source = 'ihc_measurement'
            rows = collect_ihc_stat_values(panel, self.data_panel.current_ihc_stats_metric())
            assignments = self.data_panel.ihc_stats_group_assignments()
        elif module == 'brightfield':
            from core.brightfield_statistics import collect_brightfield_stat_values, collect_brightfield_ai_plot_values
            panel = self._active_brightfield_panel()
            is_ai = self._brightfield_statistics_source() == 'ai_object_count'
            source = 'brightfield_ai_object_count' if is_ai else 'brightfield_measurement'
            rows = collect_brightfield_ai_plot_values(panel) if is_ai else collect_brightfield_stat_values(panel, self.data_panel.current_brightfield_stats_metric())
            assignments = self.data_panel.brightfield_stats_group_assignments()
        else:
            from core.wb_statistics import collect_wb_stat_values
            panel = self._active_wb_panel()
            source = 'wb'
            runs = panel.data.get('densitometry_runs') or ([panel.data['densitometry']] if panel.data.get('densitometry') else [])
            rows = collect_wb_stat_values(runs)
            assignments = self.data_panel.wb_stats_group_assignments()
        dialog = StatisticsPairingDialog(rows, assignments, panel.analysis.get('statistics_pair_ids', {}).get(source, {}), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            save_pair_ids(panel, source, dialog.pair_ids())

    def run_ihc_statistics(self) -> None:
        if self.active_module != "ihc":
            return
        panel = self._active_ihc_panel()
        measurements = list(panel.analysis.get("ihc_measurements", []))
        if not measurements:
            QMessageBox.information(
                self,
                tr("ihc_statistics"),
                tr("ihc_analysis_before_statistics"),
            )
            return
        assignments=self.data_panel.ihc_stats_group_assignments()
        if not assignments:
            panel.analysis.pop('ihc_statistics',None)
            self.data_panel.populate_ihc_stats_results({})
            self._refresh_ihc_stats_chart({})
            return
        result = compute_ihc_statistics(
            panel,
            metric=self.data_panel.current_ihc_stats_metric(),
            group_assignments=assignments,
            reference_group=self.data_panel.ihc_stats_reference_group_combo.currentText().strip()
            or self.data_panel.ihc_stats_reference_group_combo.currentText(),
            test_name=self.data_panel.ihc_stats_test_combo.currentText(),
        )
        self.data_panel.populate_ihc_stats_results(result)
        self._refresh_ihc_stats_chart(result)
        self.statusBar().showMessage(tr("ihc_statistics_complete_status"), 4000)

    def copy_ihc_statistics_table(self) -> None:
        QApplication.clipboard().setText(self.data_panel.ihc_statistics_table_tsv())
        self.statusBar().showMessage(tr("ihc_statistics_copied_status"), 2500)

    def export_ihc_statistics_csv(self) -> None:
        result = dict(self._active_ihc_panel().analysis.get("ihc_statistics", {}))
        if not result.get("groups"):
            QMessageBox.information(
                self,
                tr("export_ihc_statistics_csv_title"),
                tr("no_measurements_to_export"),
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_ihc_statistics_csv_title"),
            "",
            "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.export_ihc_statistics_csv_to_path(path)
        self.statusBar().showMessage(
            trf("exported_ihc_statistics_csv_status", name=Path(path).name),
            4000,
        )

    def export_ihc_statistics_csv_to_path(self, path: str | Path) -> Path:
        target = Path(path)
        result = dict(self._active_ihc_panel().analysis.get("ihc_statistics", {}))
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Group", "n", "Mean", "SEM"])
            for group in result.get("groups", []):
                writer.writerow(
                    [
                        str(group.get("group", "")),
                        str(group.get("n", "")),
                        f"{float(group.get('mean', 0.0)):.3f}",
                        f"{float(group.get('sem', 0.0)):.3f}",
                    ]
                )
        return target

    def copy_brightfield_results_table(self) -> None:
        QApplication.clipboard().setText(self.data_panel.brightfield_results_table_tsv())
        self.statusBar().showMessage(tr("full_brightfield_results_copied_status"), 2500)

    def copy_brightfield_ai_results_table(self) -> None:
        QApplication.clipboard().setText(self.data_panel.brightfield_ai_results_table_tsv())
        self.statusBar().showMessage(tr("he_fiber_results_copied_status"), 2500)

    def _active_brightfield_measurement(self, panel: BrightfieldPanel) -> dict | None:
        active_image = panel.active_image()
        if active_image is None:
            return None
        active_image_uid = str(active_image.get("uid", "")).strip()
        selected_uuid = self.data_panel.selected_brightfield_measurement_uuid()
        if selected_uuid:
            selected_measurement = panel.measurement_by_uuid(selected_uuid)
            if selected_measurement is not None and str(selected_measurement.get("image_uid", "")).strip() == active_image_uid:
                return selected_measurement
        for measurement in reversed(list(panel.analysis.get("brightfield_measurements", []))):
            if str(measurement.get("image_uid", "")).strip() == active_image_uid:
                return measurement
        measurements = list(active_image.get("measurements", []))
        if measurements:
            return measurements[-1]
        for measurement in reversed(list(panel.analysis.get("brightfield_ai_object_count_measurements", []))):
            if str(measurement.get("image_uid", "")).strip() == active_image_uid:
                return measurement
        return None

    def add_brightfield_outputs_to_composer(self) -> list[ExternalImagePanel]:
        panel = self._active_brightfield_panel()
        measurement = self._active_brightfield_measurement(panel)
        if measurement is None:
            QMessageBox.information(
                self,
                tr("v9_module_brightfield"),
                tr("brightfield_analysis_before_outputs"),
            )
            return []

        asset_dir = self._generated_asset_dir() / "brightfield_outputs"
        asset_dir.mkdir(parents=True, exist_ok=True)
        created: list[ExternalImagePanel] = []

        overlay = panel.render_measurement_overlay(measurement_uuid=str(measurement.get("measurement_uuid", "")))
        overlay_image = QImage(
            overlay.data,
            overlay.shape[1],
            overlay.shape[0],
            overlay.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()
        overlay_path = asset_dir / f"brightfield_overlay_{uuid4().hex[:8]}.png"
        if not overlay_image.save(str(overlay_path), "PNG"):
            raise ValueError(f"Could not export Brightfield overlay image: {overlay_path}")
        overlay_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_brightfield_overlay")
        overlay_panel.import_image(overlay_path)
        overlay_panel.data["source_module"] = "brightfield_overlay"
        overlay_panel.data["source_panel_uuid"] = panel.panel_uuid
        overlay_panel.data["source_dpi"] = BRIGHTFIELD_COMPOSER_EXPORT_DPI
        overlay_panel.data["measurement_uuid"] = str(measurement.get("measurement_uuid", ""))
        overlay_panel.data["plot_type"] = "measurement_overlay"
        overlay_panel.data["segmentation_mode"] = str(measurement.get("results", {}).get("segmentation_mode", ""))
        overlay_panel.data["stain_type"] = str(measurement.get("stain_type", ""))
        overlay_panel.data["export_item_key"] = "measurement_overlay"
        self._attach_measurement_export_metadata(overlay_panel, measurement)
        self._populate_composer_asset_dimensions(overlay_panel, overlay_image, BRIGHTFIELD_COMPOSER_EXPORT_DPI)
        apply_brightfield_export_item_metadata(
            overlay_panel,
            brightfield_export_item_for_scene_bounds(
                panel,
                BRIGHTFIELD_OVERLAY_EXPORT_KEY,
                max(1.0, overlay_image.width() * 72.0 / BRIGHTFIELD_COMPOSER_EXPORT_DPI),
                max(1.0, overlay_image.height() * 72.0 / BRIGHTFIELD_COMPOSER_EXPORT_DPI),
                dpi=BRIGHTFIELD_COMPOSER_EXPORT_DPI,
                image_path=overlay_path,
                source_format="PNG",
                source_width_px=overlay_image.width(),
                source_height_px=overlay_image.height(),
                measurement=measurement,
            ),
        )
        self.project.add_panel(overlay_panel)
        created.append(overlay_panel)

        chart_payload = dict(panel.analysis.get("brightfield_chart_export", {}))
        if not chart_payload:
            chart_payload = self._brightfield_chart_export_payload(
                panel,
                self._brightfield_statistics_result_for_source(panel, self._brightfield_statistics_source()),
            )
        if chart_payload:
            output_kind, output_overrides = self._layout_output_chart_settings(
                panel,
                "brightfield_layout",
                BRIGHTFIELD_STATISTICS_EXPORT_KEY,
            )
            chart_path = asset_dir / f"brightfield_statistics_{uuid4().hex[:8]}.svg"
            chart_path.write_bytes(
                self._brightfield_chart_svg(
                    panel,
                    chart_payload,
                    output_overrides,
                    chart_kind=output_kind or None,
                )
            )
            chart_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_brightfield_statistics")
            chart_panel.import_image(chart_path)
            chart_panel.data["source_module"] = "brightfield_statistics"
            chart_panel.data["source_panel_uuid"] = panel.panel_uuid
            chart_panel.data["source_dpi"] = BRIGHTFIELD_COMPOSER_EXPORT_DPI
            chart_panel.data["metric"] = str(chart_payload.get("metric", ""))
            chart_panel.data["plot_type"] = "statistics_chart"
            chart_panel.data["chart_mode"] = str(chart_payload.get("chart_mode", "summary"))
            chart_panel.data["frequency_mode"] = str(chart_payload.get("frequency_mode", ""))
            chart_panel.data["chart_kind"] = output_kind
            chart_panel.data["svg_text_overrides"] = deepcopy(output_overrides)
            chart_panel.data["statistic_groups"] = [
                str(group.get("group", ""))
                for group in dict(chart_payload.get("statistics_result", {})).get("groups", [])
            ]
            chart_panel.data["export_item_key"] = "statistics_chart"
            chart_size = QSvgRenderer(str(chart_path)).defaultSize()
            chart_width = max(1, chart_size.width())
            chart_height = max(1, chart_size.height())
            chart_panel.data["source_width_px"] = chart_width
            chart_panel.data["source_height_px"] = chart_height
            apply_brightfield_export_item_metadata(
                chart_panel,
                brightfield_export_item_for_scene_bounds(
                    panel,
                    BRIGHTFIELD_STATISTICS_EXPORT_KEY,
                    max(1.0, chart_width),
                    max(1.0, chart_height),
                    dpi=BRIGHTFIELD_COMPOSER_EXPORT_DPI,
                    image_path=chart_path,
                    source_format="SVG",
                    source_width_px=chart_width,
                    source_height_px=chart_height,
                    metric=str(chart_payload.get("metric", "")),
                ),
            )
            chart_panel.data["chart_kind"] = output_kind
            chart_panel.data["svg_text_overrides"] = deepcopy(output_overrides)
            self.project.add_panel(chart_panel)
            created.append(chart_panel)

        composer_outputs = list(panel.data.get("composer_outputs", []))
        for asset_panel in created:
            composer_outputs.append(
                {
                    "panel_uuid": asset_panel.panel_uuid,
                    "source_module": str(asset_panel.data.get("source_module", "")),
                    "image_path": str(asset_panel.data.get("image_path", "")),
                    "reliability_status": str(asset_panel.data.get("reliability_status", "")),
                    "warnings": list(asset_panel.data.get("warnings", [])),
                }
            )
        panel.data["composer_outputs"] = composer_outputs
        panel.append_audit("add_brightfield_outputs_to_composer", f"{len(created)} item(s)")
        self.data_panel.populate_brightfield_output_status(composer_outputs)
        self.refresh_composer_panel_list()
        self.statusBar().showMessage(
            trf(
                "added_outputs_status",
                count=len(created),
                module=tr("v9_module_brightfield"),
            ),
            4000,
        )
        return created

    def _brightfield_overlay_qimage(self, panel: BrightfieldPanel, measurement: dict) -> QImage:
        overlay = panel.render_measurement_overlay(measurement_uuid=str(measurement.get("measurement_uuid", "")))
        return QImage(
            overlay.data,
            overlay.shape[1],
            overlay.shape[0],
            overlay.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()

    def _overlay_export_dialog_start(self, filename: str = "overlay.png") -> str:
        recent = recent_directory()
        if recent:
            return str(Path(recent) / filename)
        return filename

    def _write_overlay_png(self, overlay_image: QImage, path: str | Path) -> Path:
        from core.utils.rendering import qimage_to_bytes

        target = Path(path)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        if overlay_image.isNull():
            raise ValueError(f"Could not export overlay image: {target}")
        target.write_bytes(qimage_to_bytes(overlay_image, "png"))
        return target

    def _choose_export_image_source(
        self,
        *,
        has_original: bool,
        has_overlay: bool,
        default_overlay: bool,
    ) -> str | None:
        if not has_original and not has_overlay:
            QMessageBox.information(self, tr("export_image_title"), tr("export_image_source_prompt"))
            return None
        box = QMessageBox(self)
        box.setWindowTitle(tr("export_image_title"))
        box.setText(tr("export_image_source_prompt"))
        original_btn = box.addButton(tr("original"), QMessageBox.ButtonRole.AcceptRole)
        overlay_btn = box.addButton(tr("v9_overlay"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        original_btn.setEnabled(bool(has_original))
        overlay_btn.setEnabled(bool(has_overlay))
        if default_overlay and has_overlay:
            box.setDefaultButton(overlay_btn)
        elif has_original:
            box.setDefaultButton(original_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is original_btn and has_original:
            return "original"
        if clicked is overlay_btn and has_overlay:
            return "overlay"
        return None

    def export_brightfield_image(self) -> None:
        panel = self._active_brightfield_panel()
        source = self._choose_export_image_source(
            has_original=panel.active_image() is not None,
            has_overlay=self._active_brightfield_measurement(panel) is not None,
            default_overlay=self.brightfield_workspace.current_preview_mode() == "overlay",
        )
        if source is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_image_title"),
            self._overlay_export_dialog_start(),
            "PNG (*.png)",
        )
        if not path:
            return
        try:
            if source == "overlay":
                target = self.export_brightfield_overlay_image_to_path(path)
            else:
                target = self.export_brightfield_original_image_to_path(path)
        except (ValueError, OSError) as exc:
            QMessageBox.information(self, tr("v9_module_brightfield"), str(exc))
            return
        remember_file_directory(target)
        self.statusBar().showMessage(trf("exported_image_status", name=target.name), 4000)

    def export_brightfield_overlay_image(self) -> None:
        self.export_brightfield_image()

    def export_brightfield_original_image_to_path(self, path: str | Path) -> Path:
        from ui.panels.brightfield_workspace import apply_brightfield_native_export_scale

        panel = self._active_brightfield_panel()
        active = panel.active_image()
        if active is None:
            raise ValueError("Import a Brightfield image before exporting.")
        image_uid = str(active.get("uid", "")).strip()
        image = panel.source_qimage(image_uid)
        image = apply_brightfield_native_export_scale(
            image,
            panel,
            image_uid,
            committed_scale=panel.measurement_scale_bar(image_uid),
        )
        return self._write_overlay_png(image, path)

    def export_brightfield_overlay_image_to_path(self, path: str | Path) -> Path:
        from ui.panels.brightfield_workspace import apply_brightfield_native_export_scale

        panel = self._active_brightfield_panel()
        measurement = self._active_brightfield_measurement(panel)
        if measurement is None:
            raise ValueError("Run Brightfield analysis before exporting an overlay.")
        overlay_image = self._brightfield_overlay_qimage(panel, measurement)
        image_uid = str(measurement.get("image_uid", "")).strip()
        overlay_image = apply_brightfield_native_export_scale(
            overlay_image,
            panel,
            image_uid,
            committed_scale=panel.measurement_scale_bar(image_uid),
        )
        return self._write_overlay_png(overlay_image, path)

    def export_fluorescence_image(self) -> None:
        from ui.panels.fluorescence_image_export import prompt_and_export_fluorescence_image

        panel = self._active_fluoro_panel()
        image = panel.active_image() if panel is not None else None
        overlay_button = getattr(self.fluoro_workspace, "ai_overlay_preview_button", None)
        prompt_and_export_fluorescence_image(
            self,
            panel,
            image,
            default_overlay=bool(overlay_button is not None and overlay_button.isChecked()),
        )

    def export_fluorescence_overlay_image(self) -> None:
        self.export_fluorescence_image()

    def export_fluorescence_original_image_to_path(self, path: str | Path) -> Path:
        from ui.panels.fluorescence_image_export import (
            COMPOSITION_MERGE,
            SOURCE_ORIGINAL,
            render_fluorescence_export_payloads,
            write_fluorescence_png,
        )

        panel = self._active_fluoro_panel()
        image = panel.active_image()
        if image is None:
            raise ValueError("Import a fluorescence image before exporting.")
        payloads = render_fluorescence_export_payloads(
            panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_MERGE
        )
        return write_fluorescence_png(payloads[0][1], path)

    def _fluorescence_raw_native_image(self, panel, image_record) -> QImage:
        return panel._render_image_pixels(image_record)

    def _fluorescence_non_layout_scale(self, panel, image_record, image: QImage) -> QImage:
        from ui.panels.brightfield_workspace import paint_committed_measurement_scale

        rendered = QImage(image)
        browser = image_record.get("browser_scale_bar")
        browser_visible = isinstance(browser, dict) and bool(browser.get("visible"))
        if not browser_visible:
            rendered = panel._draw_scale_bar_on_image(rendered, image_record)
        rendered = panel._draw_browser_scale_bar_on_image(rendered, image_record)
        return paint_committed_measurement_scale(rendered, dict(image_record.get("measurement_scale_bar") or {}))

    def _apply_fluorescence_native_export_scale(self, panel, image_record, image: QImage) -> QImage:
        from ui.panels.fluorescence_workspace import apply_fluorescence_native_export_scale

        return apply_fluorescence_native_export_scale(
            image,
            image_record,
            fallback_painter=lambda img, _panel=panel, _record=image_record: self._fluorescence_non_layout_scale(
                _panel,
                _record,
                img,
            ),
        )

    def _render_fluorescence_original_image(self, panel, image_record) -> QImage:
        raw = self._fluorescence_raw_native_image(panel, image_record)
        return self._apply_fluorescence_native_export_scale(panel, image_record, raw)

    def _fluorescence_has_measurement_overlay(self, panel) -> bool:
        image = panel.active_image()
        if image is None:
            return False
        records = [
            dict(record)
            for record in list(image.get("fluorescence_ai_assisted_measurements", []))
            if isinstance(record, dict)
        ]
        return any(
            int(record.get("ai_object_count", 0) or 0) > 0
            or any(isinstance(item, dict) for item in list(record.get("object_table", [])))
            for record in records
        )

    def export_fluorescence_overlay_image_to_path(self, path: str | Path) -> Path:
        from ui.panels.fluorescence_image_export import (
            COMPOSITION_MERGE,
            SOURCE_OVERLAY,
            render_fluorescence_export_payloads,
            write_fluorescence_png,
        )

        panel = self._active_fluoro_panel()
        image = panel.active_image()
        if image is None:
            raise ValueError("Run fluorescence measurement before exporting an overlay.")
        payloads = render_fluorescence_export_payloads(
            panel, image, source=SOURCE_OVERLAY, composition=COMPOSITION_MERGE
        )
        return write_fluorescence_png(payloads[0][1], path)

    def _render_fluorescence_overlay_image(self, panel) -> QImage:
        image = panel.active_image()
        if image is None:
            raise ValueError("Run fluorescence measurement before exporting an overlay.")
        records = [
            dict(record)
            for record in list(image.get("fluorescence_ai_assisted_measurements", []))
            if isinstance(record, dict)
        ]
        has_objects = any(
            int(record.get("ai_object_count", 0) or 0) > 0
            or any(isinstance(item, dict) for item in list(record.get("object_table", [])))
            for record in records
        )
        if not has_objects:
            raise ValueError("Run fluorescence measurement before exporting an overlay.")
        overlay_image = self._paint_fluorescence_ai_overlay(
            self._fluorescence_raw_native_image(panel, image),
            records,
        )
        return self._apply_fluorescence_native_export_scale(panel, image, overlay_image)

    @staticmethod
    def _overlay_image_point(point) -> tuple[float, float] | None:
        from ui.panels.fluorescence_image_export import overlay_image_point

        return overlay_image_point(point)

    def _paint_fluorescence_ai_overlay(self, overlay_image: QImage, records: list[dict]) -> QImage:
        from ui.panels.fluorescence_image_export import paint_fluorescence_ai_overlay

        return paint_fluorescence_ai_overlay(overlay_image, records)

    def _brightfield_layout_pixmap_provider(self, panel: BrightfieldPanel, measurement: dict | None):
        chart_payload = dict(panel.analysis.get("brightfield_chart_export", {}))
        if not chart_payload:
            chart_payload = self._brightfield_chart_export_payload(
                panel,
                self._brightfield_statistics_result_for_source(panel, self._brightfield_statistics_source()),
            )

        def provider(layout_object):
            if layout_object.asset_key == BRIGHTFIELD_SOURCE_IMAGE_KEY:
                return QPixmap.fromImage(panel.display_qimage())
            if layout_object.asset_key == BRIGHTFIELD_OVERLAY_EXPORT_KEY:
                if measurement is None:
                    return QPixmap.fromImage(panel.display_qimage())
                return QPixmap.fromImage(self._brightfield_overlay_qimage(panel, measurement))
            if layout_object.asset_key == BRIGHTFIELD_STATISTICS_EXPORT_KEY:
                return self._brightfield_chart_svg(
                    panel,
                    chart_payload,
                    dict(layout_object.metadata).get("svg_text_overrides", {}),
                    chart_kind=layout_output_chart_kind(panel, layout_object),
                )
            return None

        return provider

    def show_brightfield_layout(self) -> None:
        panel = self._active_brightfield_panel()
        measurement = self._active_brightfield_measurement(panel)
        if measurement is None and panel.active_image() is None:
            QMessageBox.information(
                self,
                tr("v9_module_brightfield"),
                tr("import_brightfield_before_layout"),
            )
            return
        panel.data["active_step"] = 4
        if hasattr(self, "data_panel"):
            self.data_panel.set_brightfield_step(4, emit_signal=False)
        if hasattr(self.brightfield_workspace, "set_step"):
            self.brightfield_workspace.set_step(4)
        self.brightfield_workspace.set_panel(panel)
        self.brightfield_workspace.prepare_layout(measurement=measurement, pixmap_provider=self._brightfield_layout_pixmap_provider(panel, measurement))
        self._sync_chart_kind_controls()

    def send_brightfield_layout_to_composer(self) -> list[ExternalImagePanel]:
        panel = self._active_brightfield_panel()
        measurement = self._active_brightfield_measurement(panel)
        if panel.active_image() is None:
            QMessageBox.information(self, tr("v9_module_brightfield"), tr("import_brightfield_before_layout"))
            return []
        asset_dir = self._generated_asset_dir() / "brightfield_layouts"
        asset_dir.mkdir(parents=True, exist_ok=True)
        self.brightfield_workspace.set_panel(panel)
        self.brightfield_workspace.prepare_layout(measurement=measurement, pixmap_provider=self._brightfield_layout_pixmap_provider(panel, measurement))
        self.brightfield_workspace.persist_layout_edits()
        layout_image = self.brightfield_workspace.layout_canvas.render_full_layout(dpi=BRIGHTFIELD_COMPOSER_EXPORT_DPI)
        layout_path = asset_dir / f"brightfield_layout_{uuid4().hex[:8]}.png"
        if not layout_image.save(str(layout_path), "PNG"):
            raise ValueError(f"Could not export Brightfield layout image: {layout_path}")
        layout_panel = ExternalImagePanel(panel_id=f"{panel.panel_id}_brightfield_layout")
        layout_panel.import_image(layout_path)
        apply_brightfield_export_item_metadata(
            layout_panel,
            brightfield_export_item_for_scene_bounds(
                panel,
                BRIGHTFIELD_LAYOUT_EXPORT_KEY,
                layout_image.width() * 72.0 / BRIGHTFIELD_COMPOSER_EXPORT_DPI,
                layout_image.height() * 72.0 / BRIGHTFIELD_COMPOSER_EXPORT_DPI,
                dpi=BRIGHTFIELD_COMPOSER_EXPORT_DPI,
                image_path=layout_path,
                source_format="PNG",
                source_width_px=layout_image.width(),
                source_height_px=layout_image.height(),
            ),
        )
        self.project.add_panel(layout_panel)
        self._append_composer_output_tracking(panel, layout_panel)
        created = [layout_panel]
        if measurement is not None:
            created.extend(self.add_brightfield_outputs_to_composer())
        self.project.append_audit("brightfield_layout_to_composer", f"{len(created)} item(s)")
        self.refresh_composer_panel_list()
        self.statusBar().showMessage(
            trf(
                "added_layout_items_status",
                count=len(created),
                module=tr("v9_module_brightfield"),
            ),
            4000,
        )
        return created

    def run_brightfield_statistics(self) -> None:
        if self.active_module != "brightfield":
            return
        panel = self._active_brightfield_panel()
        source = self._brightfield_statistics_source()
        measurements = self._brightfield_stat_measurements_for_source(panel, source)
        if not measurements:
            QMessageBox.information(
                self,
                tr("brightfield_statistics"),
                tr(
                    "he_fiber_statistics_required"
                    if source == "ai_object_count"
                    else "brightfield_statistics_required"
                ),
            )
            return
        reference_group = self.data_panel.brightfield_stats_reference_group_combo.currentText().strip() or self.data_panel.brightfield_stats_reference_group_combo.currentText()
        group_assignments = self.data_panel.brightfield_stats_group_assignments()
        if not group_assignments:
            panel.analysis.pop('brightfield_ai_object_count_plot_statistics' if source=='ai_object_count' else 'brightfield_statistics',None)
            self._clear_brightfield_statistics_view(source)
            return
        test_name = self.data_panel.brightfield_stats_test_combo.currentText()
        if source == "ai_object_count":
            result = compute_brightfield_ai_object_count_statistics(
                panel,
                group_assignments=group_assignments,
                reference_group=reference_group,
                test_name=test_name,
            )
        else:
            result = compute_brightfield_statistics(
                panel,
                metric=self.data_panel.current_brightfield_stats_metric(),
                group_assignments=group_assignments,
                reference_group=reference_group,
                test_name=test_name,
            )
        self.data_panel.populate_brightfield_stats_results(result)
        self._refresh_brightfield_stats_chart(result)
        self.statusBar().showMessage(
            tr(
                "he_fiber_statistics_complete_status"
                if source == "ai_object_count"
                else "traditional_brightfield_statistics_complete_status"
            ),
            4000,
        )

    def copy_brightfield_statistics_table(self) -> None:
        QApplication.clipboard().setText(
            self._brightfield_statistics_result_tsv(dict(self._active_brightfield_panel().analysis.get("brightfield_statistics", {})))
        )
        self.statusBar().showMessage(
            tr("traditional_brightfield_statistics_copied_status"),
            2500,
        )

    def copy_brightfield_ai_statistics_table(self) -> None:
        panel = self._active_brightfield_panel()
        text = self._brightfield_ai_statistics_tsv(panel)
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(tr("he_fiber_statistics_copied_status"), 2500)

    def export_brightfield_statistics_csv(self) -> None:
        result = dict(self._active_brightfield_panel().analysis.get("brightfield_statistics", {}))
        if not result.get("groups"):
            QMessageBox.information(
                self,
                tr("export_traditional_brightfield_statistics_title"),
                tr("no_measurements_to_export"),
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_traditional_brightfield_statistics_title"),
            "",
            "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.export_brightfield_statistics_csv_to_path(path)
        self.statusBar().showMessage(
            trf("exported_traditional_brightfield_statistics_status", name=Path(path).name),
            4000,
        )

    def export_brightfield_statistics_csv_to_path(self, path: str | Path) -> Path:
        target = Path(path)
        result = dict(self._active_brightfield_panel().analysis.get("brightfield_statistics", {}))
        self._write_brightfield_statistics_csv(target, result)
        return target

    def export_brightfield_ai_statistics_csv_to_path(self, path: str | Path) -> Path:
        target = Path(path)
        panel = self._active_brightfield_panel()
        plot_result = self._brightfield_ai_plot_statistics_result(panel)
        if plot_result.get("groups"):
            self._write_brightfield_statistics_csv(target, plot_result)
        else:
            self._write_brightfield_ai_summary_statistics_csv(target, self._brightfield_ai_summary_statistics_result(panel))
        return target

    def export_brightfield_current_chart(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_brightfield_chart_title"),
            "",
            "PNG (*.png)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        self.export_brightfield_current_chart_to_path(target)
        self.statusBar().showMessage(
            trf("exported_brightfield_chart_status", name=target.name),
            4000,
        )

    def export_brightfield_current_chart_to_path(self, path: str | Path) -> Path:
        from matplotlib import pyplot as plt

        panel = self._active_brightfield_panel()
        result = self._brightfield_statistics_result_for_source(panel, self._brightfield_statistics_source())
        payload = self._brightfield_chart_export_payload(panel, result)
        if not payload:
            raise ValueError("Run statistics or select a valid H&E fiber-area frequency chart before exporting.")
        target = Path(path)
        figure = self._build_brightfield_chart_figure(panel, payload)
        try:
            figure.savefig(target, dpi=BRIGHTFIELD_COMPOSER_EXPORT_DPI, bbox_inches="tight", facecolor="white")
        finally:
            plt.close(figure)
        return target

    def _write_brightfield_statistics_csv(self, target: Path, result: dict) -> None:
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Group", "n", "Mean", "SEM"])
            for group in result.get("groups", []):
                writer.writerow(
                    [
                        str(group.get("group", "")),
                        str(group.get("n", "")),
                        f"{float(group.get('mean', 0.0)):.3f}",
                        f"{float(group.get('sem', 0.0)):.3f}",
                    ]
                )

    def _write_brightfield_ai_summary_statistics_csv(self, target: Path, result: dict) -> None:
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Group", "n", "Mean Count", "Median Count", "Mean Area px", "Median Area px"])
            for group in result.get("groups", []):
                writer.writerow(
                    [
                        str(group.get("group", "")),
                        str(group.get("n_measurements", "")),
                        f"{float(group.get('mean_ai_object_count', 0.0)):.3f}",
                        f"{float(group.get('median_ai_object_count', 0.0)):.3f}",
                        f"{float(group.get('mean_ai_object_area_px', 0.0)):.3f}",
                        f"{float(group.get('median_ai_object_area_px', 0.0)):.3f}",
                    ]
                )

    def _brightfield_ai_plot_statistics_result(self, panel: BrightfieldPanel) -> dict:
        return dict(panel.analysis.get("brightfield_ai_object_count_plot_statistics", {}))

    def _brightfield_ai_summary_statistics_result(self, panel: BrightfieldPanel) -> dict:
        result = dict(panel.analysis.get("brightfield_ai_object_count_statistics", {}))
        if result:
            return result
        result = summarize_brightfield_ai_object_count_measurements(panel)
        panel.analysis["brightfield_ai_object_count_statistics"] = result
        return result

    def _brightfield_ai_summary_statistics_tsv(self, result: dict) -> str:
        lines = ["Group\tn\tMean Count\tMedian Count\tMean Area px\tMedian Area px"]
        for group in result.get("groups", []):
            lines.append(
                "\t".join(
                    [
                        str(group.get("group", "")),
                        str(group.get("n_measurements", "")),
                        f"{float(group.get('mean_ai_object_count', 0.0)):.3f}",
                        f"{float(group.get('median_ai_object_count', 0.0)):.3f}",
                        f"{float(group.get('mean_ai_object_area_px', 0.0)):.3f}",
                        f"{float(group.get('median_ai_object_area_px', 0.0)):.3f}",
                    ]
                )
            )
        return "\n".join(lines)

    def _brightfield_ai_statistics_tsv(self, panel: BrightfieldPanel) -> str:
        plot_result = self._brightfield_ai_plot_statistics_result(panel)
        if plot_result.get("groups"):
            return self._brightfield_statistics_result_tsv(plot_result)
        return self._brightfield_ai_summary_statistics_tsv(self._brightfield_ai_summary_statistics_result(panel))

    def export_brightfield_ai_statistics_csv(self) -> None:
        panel = self._active_brightfield_panel()
        plot_result = self._brightfield_ai_plot_statistics_result(panel)
        summary = self._brightfield_ai_summary_statistics_result(panel)
        if not plot_result.get("groups") and not summary.get("n_measurements"):
            QMessageBox.information(
                self,
                tr("export_he_fiber_statistics_title"),
                tr("no_measurements_to_export"),
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_he_fiber_statistics_title"),
            "",
            "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.export_brightfield_ai_statistics_csv_to_path(path)
        self.statusBar().showMessage(
            trf("exported_he_fiber_statistics_status", name=Path(path).name),
            4000,
        )

    def _brightfield_statistics_result_tsv(self, result: dict) -> str:
        lines = ["Group\tn\tMean\tSEM"]
        for group in result.get("groups", []):
            lines.append(
                "\t".join(
                    [
                        str(group.get("group", "")),
                        str(group.get("n", "")),
                        f"{float(group.get('mean', 0.0)):.3f}",
                        f"{float(group.get('sem', 0.0)):.3f}",
                    ]
                )
            )
        return "\n".join(lines)

    def _on_browser_workspace_state_changed(self) -> None:
        self._store_browser_workspace_state()

    def _store_browser_workspace_state(self) -> None:
        self.project.browser_workspace_state = deepcopy(
            self.image_browser_workspace.serialize_browser_scale_states()
        )

    def _load_browser_workspace_state(self) -> None:
        self.image_browser_workspace.restore_browser_scale_states(
            getattr(self.project, "browser_workspace_state", {}) or {}
        )

    def save_project(self) -> bool:
        from ui.controllers.project_save_controller import save_project
        return save_project(self)

    def save_project_as(self) -> bool:
        from ui.controllers.project_save_controller import save_project
        return save_project(self, save_as=True)

    def open_project(self) -> None:
        if not self._confirm_destructive_action(tr("action_open_project")):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("open_project_title"),
            "",
            "ScieFig Project (*.sfig)",
        )
        if not path:
            return
        from ui.controllers.project_open_controller import open_project_path
        open_project_path(self, path, confirm_unsaved=False)

    def _export_menu_callbacks(self) -> dict[str, object]:
        return {
            ACTION_IMAGE: self._export_active_image,
            ACTION_FIGURE: self.export_active_figure,
            ACTION_TABLE_EXCEL: self._export_active_excel,
            ACTION_TABLE_RESULTS_CSV: lambda: self._click_enabled_export_button(
                self.data_panel.ihc_export_results_csv_button
            ),
            ACTION_TABLE_STATS_CSV: self._export_active_stats_csv,
            ACTION_TABLE_AI_STATS_CSV: lambda: self._click_enabled_export_button(
                self.data_panel.brightfield_export_ai_stats_csv_button
            ),
            ACTION_SUMMARY_JSON: lambda: self._click_enabled_export_button(
                self.data_panel.ihc_export_summary_json_button
            ),
        }

    def _click_enabled_export_button(self, button, empty_key: str = "no_measurements_to_export") -> None:
        if button is None or not button.isEnabled():
            QMessageBox.information(self, tr("export_table"), tr(empty_key))
            return
        button.click()

    def _export_active_excel(self) -> None:
        if self.active_module == "fluoro":
            button = getattr(self.fluoro_workspace, "fluoro_measurement_excel_button", None)
        elif self.active_module == "ihc":
            button = self.data_panel.ihc_measurement_excel_button
        elif self.active_module == "brightfield":
            button = self.data_panel.brightfield_measurement_excel_button
        else:
            button = None
        self._click_enabled_export_button(button)

    def _export_active_stats_csv(self) -> None:
        if self.active_module == "ihc":
            self._click_enabled_export_button(self.data_panel.ihc_export_stats_csv_button)
            return
        if self.active_module == "brightfield":
            self._click_enabled_export_button(self.data_panel.brightfield_export_stats_csv_button)

    def _export_active_image(self) -> None:
        if self.active_module == "ihc":
            self.export_ihc_overlay_image()
            return
        if self.active_module == "brightfield":
            self.export_brightfield_image()
            return
        if self.active_module == "fluoro":
            self.export_fluorescence_image()
            return
        if self.active_module == "viewer":
            self.export_viewer_native_image()

    def _report_export_failure(self, title: str, error: BaseException) -> None:
        message = str(error).strip() or tr("viewer_export_failed")
        if isinstance(error, OSError):
            QMessageBox.warning(self, title, message)
            return
        QMessageBox.information(self, title, message)

    def export_viewer_native_image(self) -> None:
        if getattr(self.image_browser_workspace, "_cached_image", None) is None:
            QMessageBox.information(self, tr("export"), tr("viewer_export_failed"))
            return
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("viewer_native_png"),
            suggested_export_path("snapshot.png"),
            "PNG (*.png)",
        )
        if not path:
            return
        path = apply_native_png_suffix(path)
        try:
            ok = self.image_browser_workspace.export_snapshot(path)
        except (ValueError, OSError) as exc:
            self._report_export_failure(tr("viewer_native_png"), exc)
            return
        if not ok:
            QMessageBox.information(self, tr("export"), tr("viewer_export_failed"))
            return
        remember_file_directory(path)

    def export_active_figure(self) -> None:
        if self.active_module == "composer":
            self._export_composer_figure()
            return
        if self.active_module == "wb":
            self._export_wb_figure()
            return
        if self.active_module == "fluoro":
            self._export_fluorescence_figure()
            return
        if self.active_module == "qpcr":
            self._export_qpcr_figure()
            return
        if self.active_module == "ihc":
            self._export_ihc_layout_figure()
            return
        if self.active_module == "brightfield":
            self._export_brightfield_layout_figure()

    def _export_composer_figure(self) -> None:
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("export_composer_figure_title"),
            suggested_export_path("figure.pdf"),
            FIGURE_DIALOG_FILTER,
        )
        if not path:
            return
        path = apply_selected_figure_suffix(path, selected_filter, ".pdf")
        try:
            target = self.composer_canvas.export_figure(
                path,
                dpi=self.composer_workspace.export_dpi(),
                include_source_data=self.composer_workspace.export_include_source(),
            )
        except (ValueError, OSError, RuntimeError) as exc:
            self._report_export_failure(tr("export_composer_figure_title"), exc)
            return
        remember_file_directory(target)
        self.statusBar().showMessage(
            trf("exported_composer_figure_status", name=target.name),
            4000,
        )

    def _figure_initial_filter(self, selected_format: str) -> tuple[str, str]:
        suffix = ".tiff" if selected_format == "tiff" else f".{selected_format}"
        initial_filter = {
            "pdf": "PDF (*.pdf)",
            "svg": "SVG (*.svg)",
            "tiff": "TIFF (*.tiff)",
            "png": "PNG (*.png)",
        }.get(selected_format, "PDF (*.pdf)")
        return suffix, initial_filter

    def _export_wb_figure(self) -> None:
        panel = self._active_wb_panel()
        if not panel.data.get("image_data"):
            QMessageBox.information(
                self,
                tr("export_wb_figure"),
                tr("import_wb_before_export"),
            )
            return
        suffix, initial_filter = self._figure_initial_filter(self.data_panel.wb_export_format())
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("export_wb_figure"),
            suggested_export_path(f"wb_figure{suffix}"),
            FIGURE_DIALOG_FILTER,
            initial_filter,
        )
        if not path:
            return
        path = apply_selected_figure_suffix(path, selected_filter, suffix)
        try:
            target = export_wb_figure_scene(self.wb_canvas.scene, path, dpi=self.data_panel.wb_export_dpi())
            if self.data_panel.wb_export_include_source():
                export_wb_source_folder(panel, target)
        except (ValueError, OSError) as exc:
            self._report_export_failure(tr("export_wb_figure"), exc)
            return
        panel.append_audit("export_wb_figure", f"{target.name}; source={self.data_panel.wb_export_include_source()}")
        self.project.append_audit("export_wb_figure", target.name)
        remember_file_directory(target)
        self.statusBar().showMessage(
            trf("exported_wb_figure_status", name=target.name),
            4000,
        )

    def _export_fluorescence_figure(self) -> None:
        panel = self._active_fluoro_panel()
        if not panel.active_image():
            QMessageBox.information(
                self,
                tr("export_fluorescence_figure_title"),
                tr("import_fluorescence_before_export"),
            )
            return
        self.fluoro_workspace.persist_layout_edits_from_scene()
        if self.fluoro_workspace.canvas.current_step != 4:
            self.fluoro_workspace.canvas.set_step(4)
        suffix, initial_filter = self._figure_initial_filter(self.fluoro_workspace.export_format())
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("export_fluorescence_figure_title"),
            suggested_export_path(f"fluorescence{suffix}"),
            FIGURE_DIALOG_FILTER,
            initial_filter,
        )
        if not path:
            return
        path = apply_selected_figure_suffix(path, selected_filter, suffix)
        try:
            target = self._export_fluorescence_figure_scene(path, self.fluoro_workspace.export_dpi())
            if self.fluoro_workspace.include_metadata():
                export_fluoro_metadata(panel, target)
            if self.fluoro_workspace.include_measurements():
                export_fluoro_measurements(panel, target)
            if self.fluoro_workspace.include_source():
                export_fluoro_source_folder(panel, target)
        except (ValueError, OSError) as exc:
            self._report_export_failure(tr("export_fluorescence_figure_title"), exc)
            return
        panel.append_audit("export_fluorescence_figure", target.name)
        self.project.append_audit("export_fluorescence_figure", target.name)
        remember_file_directory(target)
        self.statusBar().showMessage(
            trf("exported_fluorescence_figure_status", name=target.name),
            4000,
        )

    def _prepare_ihc_layout_for_export(self) -> bool:
        panel = self._active_ihc_panel()
        if panel.active_image() is None:
            QMessageBox.information(self, tr("export_figure"), tr("import_ihc_before_layout"))
            return False
        measurement = self._active_ihc_measurement(panel)
        self.ihc_workspace.set_panel(panel)
        self.ihc_workspace.prepare_layout(
            measurement=measurement,
            pixmap_provider=self._ihc_layout_pixmap_provider(panel, measurement),
        )
        self.ihc_workspace.persist_layout_edits()
        canvas = self.ihc_workspace.layout_canvas
        if canvas.canvas_spec is None or not canvas.layout_items():
            QMessageBox.information(self, tr("export_figure"), tr("export_layout_empty"))
            return False
        return True

    def _prepare_brightfield_layout_for_export(self) -> bool:
        panel = self._active_brightfield_panel()
        if panel.active_image() is None:
            QMessageBox.information(self, tr("export_figure"), tr("import_brightfield_before_layout"))
            return False
        measurement = self._active_brightfield_measurement(panel)
        self.brightfield_workspace.set_panel(panel)
        self.brightfield_workspace.prepare_layout(
            measurement=measurement,
            pixmap_provider=self._brightfield_layout_pixmap_provider(panel, measurement),
        )
        self.brightfield_workspace.persist_layout_edits()
        canvas = self.brightfield_workspace.layout_canvas
        if canvas.canvas_spec is None or not canvas.layout_items():
            QMessageBox.information(self, tr("export_figure"), tr("export_layout_empty"))
            return False
        return True

    def _save_layout_raster_image(self, image: QImage, path: str | Path, selected_filter: str) -> Path:
        target = Path(path)
        if not target.suffix:
            if "TIF" in (selected_filter or "").upper():
                target = target.with_suffix(".tif")
            else:
                target = target.with_suffix(".png")
        target, image_format = self._normalize_ihc_overlay_export_target(target)
        if image.isNull() or not image.save(str(target), image_format):
            raise ValueError(f"Could not export layout figure: {target}")
        return target

    def export_ihc_layout_figure_to_path(self, path: str | Path) -> Path:
        if not self._prepare_ihc_layout_for_export():
            raise ValueError(tr("export_layout_empty"))
        layout_image = self.ihc_workspace.layout_canvas.render_full_layout(dpi=IHC_COMPOSER_EXPORT_DPI)
        return self._save_layout_raster_image(layout_image, path, "")

    def export_brightfield_layout_figure_to_path(self, path: str | Path) -> Path:
        if not self._prepare_brightfield_layout_for_export():
            raise ValueError(tr("export_layout_empty"))
        layout_image = self.brightfield_workspace.layout_canvas.render_full_layout(dpi=BRIGHTFIELD_COMPOSER_EXPORT_DPI)
        return self._save_layout_raster_image(layout_image, path, "")

    def _export_ihc_layout_figure(self) -> None:
        if not self._prepare_ihc_layout_for_export():
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("export_figure"),
            suggested_export_path("ihc_layout.png"),
            self._build_ihc_overlay_export_dialog_filter(),
        )
        if not path:
            return
        try:
            layout_image = self.ihc_workspace.layout_canvas.render_full_layout(dpi=IHC_COMPOSER_EXPORT_DPI)
            target = self._save_layout_raster_image(layout_image, path, selected_filter)
        except (ValueError, OSError, RuntimeError) as exc:
            self._report_export_failure(tr("export_figure"), exc)
            return
        remember_file_directory(target)
        self.statusBar().showMessage(trf("exported_file_status", name=target.name), 4000)

    def _export_brightfield_layout_figure(self) -> None:
        if not self._prepare_brightfield_layout_for_export():
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("export_figure"),
            suggested_export_path("brightfield_layout.png"),
            self._build_ihc_overlay_export_dialog_filter(),
        )
        if not path:
            return
        try:
            layout_image = self.brightfield_workspace.layout_canvas.render_full_layout(dpi=BRIGHTFIELD_COMPOSER_EXPORT_DPI)
            target = self._save_layout_raster_image(layout_image, path, selected_filter)
        except (ValueError, OSError, RuntimeError) as exc:
            self._report_export_failure(tr("export_figure"), exc)
            return
        remember_file_directory(target)
        self.statusBar().showMessage(trf("exported_file_status", name=target.name), 4000)

    def _export_qpcr_figure(self) -> None:
        panel = self._active_qpcr_panel()
        if not panel.data.get("processed_data", {}).get("fold_change"):
            QMessageBox.information(
                self,
                tr("export_qpcr_title"),
                tr("import_qpcr_before_export"),
            )
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            tr("export_qpcr_figure_title"),
            suggested_export_path("qpcr_figure.pdf"),
            FIGURE_DIALOG_FILTER,
        )
        if not path:
            return
        path = apply_selected_figure_suffix(path, selected_filter, ".pdf")
        try:
            if self.canvas_stack.currentWidget() is self.qpcr_layout_workspace:
                target = self.qpcr_layout_canvas.export_figure(
                    path,
                    dpi=int(panel.export.get("dpi", QPCR_COMPOSER_EXPORT_DPI)),
                )
                panel.append_audit("export_qpcr_layout", target.name)
            else:
                target = export_qpcr_panel(panel, path)
        except (ValueError, OSError) as exc:
            self._report_export_failure(tr("export_qpcr_figure_title"), exc)
            return
        self.project.append_audit("export_qpcr_panel", target.name)
        remember_file_directory(target)
        self.statusBar().showMessage(
            trf("exported_file_status", name=target.name),
            4000,
        )

    def export_active_module(self) -> None:
        if self.active_module == "stylelab" or not menu_entries_for_module(self.active_module):
            QMessageBox.information(self, tr("export"), tr("export_stylelab_empty"))
            return
        menu = build_export_menu(self, self.active_module, self._export_menu_callbacks())
        button = None
        if hasattr(self, "v9_shell"):
            button = self.v9_shell._action_buttons.get("export")
        if button is None:
            button = self.findChild(QPushButton, "V9ExportButton")
        if button is not None:
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
            return
        menu.exec()

