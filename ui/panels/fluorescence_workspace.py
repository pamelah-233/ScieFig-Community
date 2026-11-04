from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QSignalBlocker, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QFontComboBox,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from core.measurement_freshness import attach_analysis_inputs, build_analysis_inputs, record_image_uid
from core.models import FluorescencePanel
from ui.widgets.model_selection_hint import ModelSelectionHint
from core.layout_scale_bar import apply_native_image_scale_overlay, fluorescence_layout_scale_target_ids, layout_scale_manual_um_per_px, normalize_scale_bar_template
from core.fluoro_statistics import FLUOR_STAT_METRICS, FLUOR_STAT_SOURCES, STAT_TESTS, collect_fluoro_distribution_rows, collect_fluoro_stat_values, compute_fluoro_statistics, physical_area_text_and_excel
from core.plotting.statistics_plot import build_distribution_comparison_figure, build_group_statistics_figure, resolve_statistics_plot_style
from core.plotting.fluoro_render import (
    COMBINED_INTENSITY_NAME,
    FORCED_RGB_SPLIT_MODE,
    RGB_COMPOSITE_MODE,
    render_fluorescence_qimage,
    render_single_qimage,
    rgb_interpretation_mode,
)
from core.utils.i18n import bind_i18n, retranslate_bound_widgets, retranslate_combo, tr, trf
from core.utils.commands import SnapshotCommand, UndoManager
from core.utils.text_formatting import display_unit
from ui.widgets.fluoro_canvas import FluorescenceCanvas, fluorescence_dropped_paths
from ui.widgets.measurement_excel_export import EXCEL_VALUE_ROLE, measurement_excel_button
from ui.widgets.workflow_presentation import WorkflowDisclosure
from ui.widgets.current_page_stack import CurrentPageStackedWidget

def format_optional_fixed(value: object, decimals: int) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number != number:
        return ""
    if decimals <= 0:
        return str(int(number)) if float(number) == int(number) else f"{number:.0f}"
    return f"{number:.{decimals}f}"


COLOR_OPTIONS = [
    ("#0066FF (蓝色 / DAPI)", "#0066FF"),
    ("#22D7E6 (Nature cyan / fluorescence)", "#22D7E6"),
    ("#00D26A (绿色 / FITC, GFP)", "#00D26A"),
    ("#FF3333 (红色 / RFP, mCherry)", "#FF3333"),
    ("#FF40FF (洋红 / Cy3, Cy5替代)", "#FF40FF"),
    ("#FF2AD4 (Nature magenta / fluorescence)", "#FF2AD4"),
    ("#FFD13B (黄色 / 合并辅助)", "#FFD13B"),
    ("#FFFFFF (白色 / 灰度)", "#FFFFFF"),
]


def apply_fluorescence_native_export_scale(image: QImage, image_record: dict, *, target_id: str | None = None, fallback_painter=None) -> QImage:
    record = dict(image_record or {})
    resolved_target = str(target_id or "")
    if not resolved_target:
        targets = fluorescence_layout_scale_target_ids(record)
        resolved_target = targets[0] if targets else str(record.get("uid", ""))
    return apply_native_image_scale_overlay(
        image,
        target_id=resolved_target,
        native_width_px=image.width(),
        native_height_px=image.height(),
        source_metadata=dict(record.get("metadata") or {}),
        measurement_scale=dict(record.get("measurement_scale_bar") or {}),
        template=record.get("layout_scale_bar_template"),
        states=record.get("layout_scale_bars"),
        fallback_painter=fallback_painter,
    )


def _make_black_balance_scope_combo() -> QComboBox:
    combo = QComboBox()
    combo.addItem(tr("all_channels"), "all_channels")
    combo.addItem(tr("selected_channel"), "selected_channel")
    return combo


class CountPreviewDialog(QDialog):
    def __init__(self, rows: list[dict], review_image: QImage | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("count_preview_title"))
        self.setModal(False)
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        self.reliability_label = bind_i18n(QLabel(), "count_preview_review")
        self.reliability_label.setWordWrap(True)
        layout.addWidget(self.reliability_label)
        self.review_image_label = QLabel()
        self.review_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if review_image is None or review_image.isNull():
            review_image = QImage(720, 480, QImage.Format.Format_ARGB32)
            review_image.fill(QColor("#111111"))
        pixmap = QPixmap.fromImage(review_image).scaled(
            720,
            500,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.review_image_label.setPixmap(pixmap)
        layout.addWidget(self.review_image_label, 1)
        close_button = bind_i18n(QPushButton(), "close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)
        self._close_button = close_button

    def retranslate(self) -> None:
        self.setWindowTitle(tr("count_preview_title"))
        self.reliability_label.setText(tr("count_preview_review"))
        self._close_button.setText(tr("close"))


class FluorescenceWorkspace(QWidget):
    measurement_selected = pyqtSignal(object)
    analysis_completed = pyqtSignal()
    CHANNEL_REFRESH_DEBOUNCE_MS = 40

    files_dropped = pyqtSignal(list)
    export_requested = pyqtSignal()
    export_overlay_requested = pyqtSignal()
    layout_to_composer_requested = pyqtSignal()
    merge_selected_requested = pyqtSignal(list)
    save_active_image_requested = pyqtSignal()
    force_split_rgb_requested = pyqtSignal()
    analysis_context_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.panel: FluorescencePanel | None = None
        self._roi_clear_history = UndoManager(max_steps=50)
        self.step_buttons: list[QPushButton] = []
        self._workflow_i18n_labels: list[QLabel] = []
        self._form_i18n_labels: list[QLabel] = []
        self._channel_form_i18n_labels: list[QLabel] = []
        self._channel_widgets: list[tuple[QCheckBox, QDoubleSpinBox, QDoubleSpinBox, QComboBox]] = []
        self.last_count_preview_dialog: CountPreviewDialog | None = None
        self._ai_roi_type: str | None = None
        self._ai_roi_geometry: dict | None = None
        self._analysis_runner: Callable[..., bool] | None = None
        self._busy_analysis_operations: set[str] = set()
        self._analysis_controls_before_busy: dict[QWidget, bool] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.canvas = FluorescenceCanvas()
        from ui.widgets.fluoro_channel_toolbar import FluoroChannelToolbar
        self.preview_channels = FluoroChannelToolbar()
        preview_row = self.canvas.zoom_controls.layout()
        index = preview_row.indexOf(self.canvas.zoom_controls.fit_button) - 1
        preview_row.insertWidget(index, self.preview_channels, 1)
        preview_row.setStretch(index + 1, 0)
        self.preview_channels.selected.connect(self._select_preview_channel)
        self.canvas.scene.selectionChanged.connect(self._select_scale_target_from_canvas)
        self.canvas.files_dropped.connect(self.files_dropped.emit)
        self.canvas.roi_drawn.connect(self._add_drawn_roi)
        from ui.controllers.fluoro_display_balance_controller import FluoroDisplayBalanceController
        self._display_balance = FluoroDisplayBalanceController(self)
        self._channel_refresh_timer = QTimer(self)
        self._channel_refresh_timer.setSingleShot(True)
        self._channel_refresh_timer.setInterval(self.CHANNEL_REFRESH_DEBOUNCE_MS)
        self._channel_refresh_timer.timeout.connect(self.canvas.refresh_channel_pixels)
        root.addWidget(self.canvas, 1)
        self.workflow_panel = self._build_workflow_panel()
        self.canvas.scene.selectionChanged.connect(self._sync_layout_text_controls)
        self.set_step(0)
        self._bind_static_i18n()
        self.retranslate()

    def _bind_static_i18n(self) -> None:
        bindings = (
            (self.import_button, "add_images"),
            (self.remove_button, "remove_selected"),
            (self.merge_selected_button, "merge_selected_channels"),
            (self.mode_label, "mode"),
            (self.force_split_rgb_button, "force_split_rgb"),
            (self.save_active_image_button, "save_active_image_tiff"),
            (self.lock_display_checkbox, "lock_display_range"),
            (self.add_roi_button, "add_roi"),
            (self.add_background_roi_button, "add_background_roi"),
            (self.clear_roi_button, "clear_roi_measurements"),
            (self.roi_black_balance_button, "black_balance_roi"),
            (self.draw_square_button, "rectangle"),
            (self.draw_circle_button, "ellipse"),
            (self.draw_freehand_button, "freehand"),
            (self.apply_threshold_button, "apply_threshold"),
            (self.black_balance_button, "black_balance_roi"),
            (self.count_button, "count_spots"),
            (self.sample_spot_button, "sample_spot_tool"),
            (self.overlap_button, "plot_intensity_profile"),
            (self.profile_line_button, "profile_line_tool"),
            (self.coloc_draw_roi_button, "draw_roi"),
            (self.ai_draw_scale_line_button, "draw_scale"),
            (self.ai_apply_scale_button, "apply_scale"),
            (self.ai_refresh_results_button, "refresh_results"),
            (self.ai_reset_scale_button, "reset"),
            (self.ai_allow_restricted_checkbox, "allow_restricted_model"),
            (self.ai_draw_roi_button, "draw_ai_roi"),
            (self.ai_clear_roi_button, "use_full_image"),
            (self.ai_run_button, "run_cell_area_detection"),
            (self.open_roi_results_button, "open_full_results"),
            (self.export_roi_csv_button, "export_csv"),
            (self.delete_selected_measurement_button, "delete_selected"),
            (self.delete_selected_count_button, "delete_selected"),
            (self.clear_counts_button, "clear_counts"),
            (self.clear_profile_button, "clear_profile"),
            (self.clear_colocalization_button, "clear_colocalization"),
            (self.clear_ai_results_button, "clear_ai_results"),
            (self.fluoro_stats_chart_toggle, "chart_preview"),
            (self.run_fluoro_stats_button, "run_statistics"),
            (self.fluoro_stats_add_to_layout_button, "add_plot_to_layout"),
            (self.preview_layout_button, "preview_layout"),
            (self.send_layout_to_composer_button, "send_layout_to_composer"),
            (self.layout_add_text_edit, "text_label", "placeholder"),
            (self.layout_add_text_button, "add_text"),
            (self.layout_text_select_button, "box_select_text"),
            (self.layout_text_bold, "bold"),
            (self.layout_text_italic, "italic"),
            (self.layout_apply_text_button, "apply_selected_text"),
            (self.layout_add_shape_button, "add_shape"),
            (self.layout_apply_shape_button, "apply_selected_shape"),
            (self.fluoro_stats_apply_style_button, "apply_statistics_style"),
            (self.layout_apply_button, "apply"),
            (self.export_metadata_checkbox, "include_analysis_metadata"),
            (self.export_measurements_checkbox, "export_measurements_csv"),
            (self.export_source_checkbox, "include_source_data"),
            (self.export_button, "export_figure"),
        )
        for binding in bindings:
            widget, key, *attribute = binding
            bind_i18n(widget, key, attribute[0] if attribute else "text")

    def dragEnterEvent(self, event) -> None:
        if fluorescence_dropped_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if fluorescence_dropped_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = fluorescence_dropped_paths(event.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _build_workflow_panel(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setMinimumWidth(0)
        wrapper.setMaximumWidth(16777215)
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setObjectName("FluorescenceWorkflowRightScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        scroll.setWidget(content)
        self.step_nav = self._build_step_nav()
        self.active_image_panel = self._build_active_image_selector()
        self.context_panel = self._build_context_panel()
        self.status_row = self._build_status_row()
        step_group = self._workflow_group("workflow_steps")
        step_group.layout().addWidget(self.step_nav)
        step_group.layout().addWidget(self.status_row)
        content_layout.addWidget(step_group, 0)

        self.canvas_tools_group = self._workflow_group("canvas_tools")
        self.canvas_tools_group.layout().addWidget(self.layout_tools_panel)
        self.canvas_tools_group.setVisible(False)
        content_layout.addWidget(self.canvas_tools_group, 0)

        source_group = self._workflow_group("active_image_selector")
        source_group.layout().addWidget(self.active_image_panel)
        self.active_image_group = source_group
        source_group.setVisible(False)
        content_layout.addWidget(source_group, 0)
        content_layout.addWidget(self.scale_appearance_group, 0)

        workflow_group = self._workflow_group("workflow")
        workflow_group.layout().addWidget(self.context_panel, 1)
        content_layout.addWidget(workflow_group, 1)
        content_layout.addStretch(1)
        layout.addWidget(scroll, 1)
        return wrapper

    def _workflow_group(self, title_key: str) -> QFrame:
        group = QFrame()
        group.setObjectName("V9RightWorkbenchGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        label = QLabel(tr(title_key))
        label.setProperty("i18nKey", title_key)
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        self._workflow_i18n_labels.append(label)
        return group

    def _add_i18n_form_row(self, form: QFormLayout, label_key: str, field, collection: list[QLabel] | None = None) -> None:
        label = bind_i18n(QLabel(), label_key)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        (collection if collection is not None else self._form_i18n_labels).append(label)
        form.addRow(label, field)

    def _build_step_nav(self) -> QWidget:
        wrapper = QWidget()
        layout = QGridLayout(wrapper)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)
        for index, label in enumerate(self._step_labels()):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, step=index: self.set_step(step))
            self.step_buttons.append(button)
            row = index // 2
            col = (index % 2) * 2
            layout.addWidget(button, row, col)
            if index < 5:
                arrow = QLabel("->")
                arrow.setObjectName("DimLabel")
                next_row = (index + 1) // 2
                if next_row == row:
                    layout.addWidget(arrow, row, col + 1)
        for column in (0, 2):
            layout.setColumnStretch(column, 1)
        return wrapper

    def _build_active_image_selector(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setMinimumWidth(0)
        wrapper.setMaximumWidth(16777215)
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(10, 10, 10, 10)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("assisted"), "assisted")
        self.mode_combo.addItem(tr("one_click"), "one_click")
        self.mode_combo.addItem(tr("expert"), "expert")
        self.active_image_combo = QComboBox()
        self.active_image_combo.setObjectName("FluorescenceActiveImageCombo")
        self.active_image_combo.currentIndexChanged.connect(self._select_image_from_combo)
        wrapper.setMaximumHeight(150)
        self.mode_label = QLabel()
        layout.addWidget(self.mode_label)
        layout.addWidget(self.mode_combo)
        self.active_image_label = bind_i18n(QLabel(), "active_image")
        layout.addWidget(self.active_image_label)
        layout.addWidget(self.active_image_combo)
        return wrapper

    def _build_context_panel(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setMinimumWidth(0)
        wrapper.setMaximumWidth(16777215)
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(10, 10, 10, 10)
        self.context_stack = CurrentPageStackedWidget(compact=True)
        self.context_stack.addWidget(self._build_import_page())
        self.context_stack.addWidget(self._build_channels_page())
        self.context_stack.addWidget(self._build_measure_page())
        self.context_stack.addWidget(self._build_stats_page())
        self.context_stack.addWidget(self._build_layout_page())
        self.context_stack.addWidget(self._build_export_page())
        layout.addWidget(self.context_stack, 1)
        return wrapper

    def _build_import_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.import_button = QPushButton("+ Add Images")
        self.import_button.setObjectName("PrimaryButton")
        self.remove_button = QPushButton("Remove Selected")
        self.merge_selected_button = QPushButton("Merge Selected as Channels")
        self.merge_selected_button.setObjectName("PrimaryButton")
        self.merge_selected_button.setEnabled(False)
        layout.addWidget(self.import_button)
        layout.addWidget(self.remove_button)
        self.merge_image_list = QListWidget()
        self.merge_image_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.merge_image_list.itemSelectionChanged.connect(self._sync_merge_selected_button)
        self.merge_image_list.itemDoubleClicked.connect(lambda _item: self.merge_selected_images())
        self.merge_hint_label = bind_i18n(QLabel(), "merge_hint")
        self.merge_hint_label.setObjectName("DimLabel")
        self.merge_hint_label.setWordWrap(True)
        self.merge_channels_label = bind_i18n(QLabel(), "merge_channels")
        layout.addWidget(self.merge_channels_label)
        layout.addWidget(self.merge_image_list)
        layout.addWidget(self.merge_hint_label)
        layout.addWidget(self.merge_selected_button)
        self.merge_selected_button.clicked.connect(self.merge_selected_images)
        group = bind_i18n(QGroupBox(), "metadata", "title")
        form = QFormLayout(group)
        self.metadata_format = QLabel("-")
        self.metadata_dimensions = QLabel("-")
        self.metadata_channels = QLabel("-")
        self.metadata_bit_depth = QLabel("-")
        self.metadata_pixel_size = QLabel("-")
        self.metadata_objective = QLabel("-")
        self._add_i18n_form_row(form, "format", self.metadata_format)
        self._add_i18n_form_row(form, "dimensions", self.metadata_dimensions)
        self._add_i18n_form_row(form, "channels", self.metadata_channels)
        self._add_i18n_form_row(form, "bit_depth", self.metadata_bit_depth)
        self._add_i18n_form_row(form, "pixel_size", self.metadata_pixel_size)
        self._add_i18n_form_row(form, "objective", self.metadata_objective)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_channels_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.view_mode_combo = QComboBox()
        for value in ("merge", "split", "single"):
            self.view_mode_combo.addItem(value, value)
        self.view_mode_combo.currentIndexChanged.connect(lambda _index: self._set_view_mode(self.view_mode_combo.currentData()))
        self.lock_display_checkbox = QCheckBox("Lock Display Range")
        self.lock_display_checkbox.toggled.connect(self._set_channel_controls_locked)
        layout.addWidget(bind_i18n(QLabel(), "view_mode"))
        layout.addWidget(self.view_mode_combo)
        layout.addWidget(self.lock_display_checkbox)
        self.force_split_rgb_button = QPushButton("Force Split RGB Channels")
        self.force_split_rgb_button.setToolTip(
            "Show the original RGB sample planes when automatic single-signal detection is incorrect."
        )
        self.force_split_rgb_button.clicked.connect(self.force_split_rgb_requested.emit)
        layout.addWidget(self.force_split_rgb_button)
        self.save_active_image_button = QPushButton("Save Active Image TIFF")
        self.save_active_image_button.clicked.connect(self.save_active_image_requested.emit)
        layout.addWidget(self.save_active_image_button)
        self.channel_controls = QWidget()
        self.channel_layout = QVBoxLayout(self.channel_controls)
        self.channel_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.channel_controls)
        layout.addStretch(1)
        return page

    def _build_measure_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.measure_mode = "roi_intensity"
        self.measure_mode_buttons: dict[str, QPushButton] = {}

        from ui.widgets.measurement_task_selector import MeasurementTaskSelector
        self.measure_task_combo = MeasurementTaskSelector()
        self.measure_task_combo.setMinimumWidth(0)
        for index, (label, mode) in enumerate(
            [
                (tr("roi_intensity"), "roi_intensity"),
                (tr("spot_count"), "spot_count"),
                (tr("line_profile"), "line_profile"),
                (tr("colocalization"), "colocalization"),
                (tr("ai_assist"), "ai_assisted"),
            ]
        ):
            button = QPushButton(label)
            bind_i18n(button, {"roi_intensity": "roi_intensity", "spot_count": "spot_count",
                "line_profile": "line_profile", "colocalization": "colocalization", "ai_assisted": "ai_assist"}[mode])
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if index == 0:
                button.setChecked(True)
            button.clicked.connect(lambda checked=False, name=mode: self._set_measure_mode(name))
            self.measure_mode_buttons[mode] = button
            self.measure_task_combo.addItem(tr('workflow_task_' + mode), mode,button)
        layout.addWidget(self.measure_task_combo)

        self.measure_params_stack = CurrentPageStackedWidget(compact=True)
        self.measure_summary_stack = CurrentPageStackedWidget(compact=True)

        self.roi_intensity_params_panel = QWidget()
        roi_layout = QVBoxLayout(self.roi_intensity_params_panel)
        self.roi_channel_combo = QComboBox()
        self.roi_background_method_combo = QComboBox()
        self.roi_background_method_combo.addItems(["None", "Local background ROI", "Fixed value per channel"])
        for index in range(self.roi_background_method_combo.count()):
            self.roi_background_method_combo.setItemData(index, self.roi_background_method_combo.itemText(index))
        self.roi_black_balance_scope_combo = _make_black_balance_scope_combo()
        self.black_balance_scope_combo = self.roi_black_balance_scope_combo
        self.roi_black_balance_status_label = bind_i18n(QLabel(), "black_balance_not_set")
        self.roi_black_balance_status_label.setObjectName("DimLabel")
        self.black_balance_status_label = self.roi_black_balance_status_label
        self.roi_fixed_background_spin = QDoubleSpinBox()
        self.roi_fixed_background_spin.setRange(0, 1_000_000_000)
        self.roi_fixed_background_spin.setDecimals(3)
        roi_group = QGroupBox()
        roi_group.setObjectName('WorkflowEssentialParameters')
        roi_form = QFormLayout(roi_group)
        roi_advanced = QWidget()
        advanced_form = QFormLayout(roi_advanced)
        advanced_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        advanced_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._add_i18n_form_row(roi_form, "workflow_measure_channel", self.roi_channel_combo)
        self._add_i18n_form_row(roi_form, "workflow_background_method", self.roi_background_method_combo)
        self._add_i18n_form_row(advanced_form, "black_balance_scope", self.roi_black_balance_scope_combo)
        advanced_form.addRow(self.roi_black_balance_status_label)
        self._add_i18n_form_row(advanced_form, "fixed_background", self.roi_fixed_background_spin)
        self.roi_x = QSpinBox()
        self.roi_y = QSpinBox()
        self.roi_w = QSpinBox()
        self.roi_h = QSpinBox()
        for spin in [self.roi_x, self.roi_y, self.roi_w, self.roi_h]:
            spin.setRange(0, 100000)
        self.roi_w.setValue(100)
        self.roi_h.setValue(100)
        self.add_roi_button = QPushButton("+ Add ROI")
        self.add_roi_button.setObjectName("PrimaryButton")
        self.add_roi_button.clicked.connect(self._add_roi)
        self.add_background_roi_button = QPushButton("+ Add Background ROI")
        self.clear_roi_button = QPushButton("Clear ROI Measurements")
        self.clear_roi_button.clicked.connect(self._clear_roi_measurements)
        self.roi_black_balance_button = QPushButton("Black Balance ROI")
        self.roi_black_balance_button.clicked.connect(lambda: self._set_roi_tool("black_balance"))
        roi_buttons = QWidget()
        roi_buttons_layout = QHBoxLayout(roi_buttons)
        roi_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.draw_square_button = QPushButton("Rect")
        self.draw_circle_button = QPushButton("Ellipse")
        self.draw_freehand_button = QPushButton("Freehand")
        self.draw_square_button.clicked.connect(lambda: self._set_roi_tool("rectangle"))
        self.draw_circle_button.clicked.connect(lambda: self._set_roi_tool("ellipse"))
        self.draw_freehand_button.clicked.connect(lambda: self._set_roi_tool("freehand"))
        roi_buttons_layout.addWidget(self.draw_square_button)
        roi_buttons_layout.addWidget(self.draw_circle_button)
        roi_buttons_layout.addWidget(self.draw_freehand_button)
        self._add_i18n_form_row(roi_form, "workflow_roi_shape", roi_buttons)
        advanced_form.addRow("X", self.roi_x)
        advanced_form.addRow("Y", self.roi_y)
        advanced_form.addRow("W", self.roi_w)
        advanced_form.addRow("H", self.roi_h)
        advanced_form.addRow(self.add_roi_button)
        advanced_form.addRow(self.add_background_roi_button)
        advanced_form.addRow(self.roi_black_balance_button)
        roi_layout.addWidget(roi_group)
        self.roi_advanced_section = WorkflowDisclosure('workflow_advanced', roi_advanced)
        roi_layout.addWidget(self.roi_advanced_section)

        self.spot_count_params_panel = QWidget()
        count_layout = QVBoxLayout(self.spot_count_params_panel)
        self.measure_channel_combo = QComboBox()
        self.threshold_method_combo = QComboBox()
        for method in ("manual", "otsu", "triangle", "li"):
            self.threshold_method_combo.addItem(method, method)
        self.threshold_value_spin = QDoubleSpinBox()
        self.threshold_value_spin.setRange(0, 1_000_000_000)
        self.threshold_value_spin.setDecimals(3)
        self.apply_threshold_button = QPushButton("Apply Threshold")
        self.apply_threshold_button.setObjectName("PrimaryButton")
        self.apply_threshold_button.clicked.connect(self._apply_threshold)
        self.black_balance_button = QPushButton("Black Balance ROI")
        self.black_balance_button.clicked.connect(lambda: self._set_roi_tool("black_balance"))
        self.count_black_balance_scope_combo = _make_black_balance_scope_combo()
        self.count_black_balance_status_label = bind_i18n(QLabel(), "black_balance_not_set")
        self.count_black_balance_status_label.setObjectName("DimLabel")
        threshold = bind_i18n(QGroupBox(), "threshold", "title")
        threshold_form = QFormLayout(threshold)
        self._add_i18n_form_row(threshold_form, "measurement_channel", self.measure_channel_combo)
        self._add_i18n_form_row(threshold_form, "method", self.threshold_method_combo)
        self._add_i18n_form_row(threshold_form, "value", self.threshold_value_spin)
        self._add_i18n_form_row(threshold_form, "black_balance_scope", self.count_black_balance_scope_combo)
        threshold_form.addRow(self.apply_threshold_button)
        threshold_form.addRow(self.black_balance_button)
        threshold_form.addRow(self.count_black_balance_status_label)
        self.count_threshold_section = WorkflowDisclosure('workflow_advanced', threshold)

        count_group = bind_i18n(QGroupBox(), "spot_cell_counting", "title")
        count_group_layout = QVBoxLayout(count_group)
        self.count_channel_list = QListWidget()
        self.count_channel_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.count_channel_list.setMinimumHeight(96)
        self.count_channel_list.setMaximumHeight(104)
        self.count_channel_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.count_min_area_spin = QSpinBox()
        self.count_min_area_spin.setRange(1, 100000)
        self.count_min_area_spin.setValue(5)
        self.count_button = QPushButton("Count Spots")
        self.count_button.setObjectName("PrimaryButton")
        self.count_button.clicked.connect(self._count_spots)
        self.sample_spot_button = QPushButton("Sample Spot Tool")
        self.sample_spot_button.clicked.connect(lambda: self._set_roi_tool("sample_spot"))
        self.count_algorithm_combo = QComboBox()
        for method in ('traditional','cellpose_cyto3','log','cellpose_nuclei'):
            self.count_algorithm_combo.addItem(tr('spot_method_'+method),method)
        self.count_diameter_spin=QDoubleSpinBox()
        self.count_diameter_spin.setRange(2,1000)
        self.count_diameter_spin.setValue(40)
        self.count_diameter_spin.setSuffix(' px')
        self.count_sensitivity_spin=QDoubleSpinBox()
        self.count_sensitivity_spin.setRange(.1,5)
        self.count_sensitivity_spin.setSingleStep(.1)
        self.count_sensitivity_spin.setValue(1)
        self.count_algorithm_hint=bind_i18n(QLabel(),'spot_method_hint')
        self.count_algorithm_hint.setWordWrap(True)
        self.count_algorithm_hint.setObjectName('DimLabel')
        count_form = QFormLayout()
        count_method_form = QFormLayout()
        self._add_i18n_form_row(count_method_form,'spot_count_method',self.count_algorithm_combo)
        self.count_model_hint=ModelSelectionHint(self.count_algorithm_combo,dict(cellpose_cyto3='cyto3',cellpose_nuclei='nuclei'))
        count_method_form.addRow(self.count_model_hint)
        count_group_layout.addLayout(count_method_form)
        self._add_i18n_form_row(count_form,'spot_count_diameter',self.count_diameter_spin)
        self._add_i18n_form_row(count_form,'spot_count_sensitivity',self.count_sensitivity_spin)
        self._add_i18n_form_row(count_form, "min_area_px", self.count_min_area_spin)
        count_group_layout.addWidget(bind_i18n(QLabel(), "channels"))
        count_group_layout.addWidget(self.count_channel_list)
        count_group_layout.addLayout(count_form)
        count_group_layout.addWidget(self.count_algorithm_hint)
        count_group_layout.addWidget(self.sample_spot_button)
        count_group_layout.addWidget(self.count_button)
        self.count_table = QTableWidget(0, 8)
        self.count_table.setHorizontalHeaderLabels(["Channel", "Count", "Area px", "Mean area", "Mean intensity",tr('spot_count_method'),tr('spot_count_parameters'),tr('spot_count_image')])
        self.count_table.setMinimumHeight(150)
        self.count_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.count_table.setColumnWidth(5,170)
        self.count_table.setColumnWidth(6,300)
        self.count_table.setColumnWidth(7,200)
        self.count_algorithm_combo.currentIndexChanged.connect(self._sync_count_algorithm)
        self.count_diameter_spin.valueChanged.connect(self._count_settings_changed)
        self.count_sensitivity_spin.valueChanged.connect(self._count_settings_changed)
        self._sync_count_algorithm()
        self.count_table.itemSelectionChanged.connect(self._select_count_from_results)
        self.count_table.currentCellChanged.connect(self._select_count_from_results)
        count_layout.addWidget(count_group)
        count_layout.addWidget(self.count_threshold_section)

        self.line_profile_params_panel = QWidget()
        line_layout = QVBoxLayout(self.line_profile_params_panel)
        overlap_group = bind_i18n(QGroupBox(), "line_profile", "title")
        overlap_layout = QVBoxLayout(overlap_group)
        self.overlap_channel_list = QListWidget()
        self.overlap_channel_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.overlap_channel_list.setMinimumHeight(96)
        self.overlap_channel_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.overlap_button = QPushButton("Plot Intensity Profile")
        self.overlap_button.setObjectName("PrimaryButton")
        self.overlap_button.clicked.connect(self._plot_overlap)
        self.profile_line_button = QPushButton("Profile Line Tool")
        self.profile_line_button.clicked.connect(lambda: self._set_roi_tool("profile_line"))
        overlap_layout.addWidget(bind_i18n(QLabel(), "compare_channels"))
        overlap_layout.addWidget(self.overlap_channel_list)
        overlap_layout.addWidget(self.profile_line_button)
        overlap_layout.addWidget(self.overlap_button)
        line_layout.addWidget(overlap_group)

        self.colocalization_params_panel = QWidget()
        coloc_layout = QVBoxLayout(self.colocalization_params_panel)
        coloc_group = bind_i18n(QGroupBox(), "colocalization", "title")
        coloc_group_layout = QVBoxLayout(coloc_group)
        warning = bind_i18n(QLabel(), "colocalization_warning")
        warning.setWordWrap(True)
        coloc_group_layout.addWidget(warning)
        self.coloc_draw_roi_button = QPushButton("Draw ROI")
        self.coloc_draw_roi_button.clicked.connect(lambda: self._set_roi_tool("rectangle"))
        coloc_group_layout.addWidget(self.coloc_draw_roi_button)
        coloc_layout.addWidget(coloc_group)

        self.ai_assisted_params_panel = QWidget()
        ai_layout = QVBoxLayout(self.ai_assisted_params_panel)
        self.ai_assisted_group = bind_i18n(QGroupBox(), "ai_assisted_cell_area", "title")
        ai_group_layout = QVBoxLayout(self.ai_assisted_group)
        ai_group_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.ai_status_label = QLabel(
            "AI-assisted cell area detection runs locally in this build. Approved external model execution remains unavailable."
        )
        self.ai_status_label.setObjectName("DimLabel")
        self.ai_status_label.setWordWrap(True)
        self.ai_channel_list = QListWidget()
        self.ai_channel_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.ai_channel_list.setMinimumHeight(80)
        self.ai_channel_list.setMaximumHeight(112)
        self.ai_channel_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ai_scale_status_label = bind_i18n(QLabel(), "scale_no_image_selected")
        self.ai_scale_status_label.setObjectName("DimLabel")
        self.ai_scale_status_label.setWordWrap(True)
        self.ai_scale_length_spin = QDoubleSpinBox()
        self.ai_scale_length_spin.setRange(0.1, 100000.0)
        self.ai_scale_length_spin.setValue(50.0)
        self.ai_scale_um_per_px_spin = QDoubleSpinBox()
        self.ai_scale_um_per_px_spin.setRange(0.0, 100000.0)
        self.ai_scale_um_per_px_spin.setDecimals(6)
        self.ai_scale_um_per_px_spin.setSpecialValueText("Use image / assumed")
        self.ai_draw_scale_line_button = QPushButton("Draw Scale Line")
        self.ai_draw_scale_line_button.clicked.connect(self._draw_ai_scale_line)
        self.ai_apply_scale_button = QPushButton("Apply Measurement Scale")
        self.ai_apply_scale_button.clicked.connect(self._apply_ai_measurement_scale)
        self.ai_refresh_results_button = QPushButton("Refresh Results")
        self.ai_refresh_results_button.clicked.connect(self._refresh_ai_measurement_scale_results)
        self.ai_reset_scale_button = QPushButton("Reset Scale")
        self.ai_reset_scale_button.clicked.connect(self._reset_ai_measurement_scale)
        self.ai_min_area_spin = QSpinBox()
        self.ai_min_area_spin.setRange(1, 100000)
        self.ai_min_area_spin.setValue(5)
        self.ai_max_area_spin = QSpinBox()
        self.ai_max_area_spin.setRange(0, 100000000)
        self.ai_max_area_spin.setValue(0)
        self.ai_sensitivity_spin = QDoubleSpinBox()
        self.ai_sensitivity_spin.setRange(0.1, 3.0)
        self.ai_sensitivity_spin.setSingleStep(0.1)
        self.ai_sensitivity_spin.setValue(1.0)
        self.ai_backend_combo = QComboBox()
        self.ai_backend_combo.addItem(tr('local_fluoro_segmentation'), 'local_heuristic')
        self.ai_backend_combo.addItem(tr('cellpose_area_method'), 'cellpose_cyto3')
        self.ai_backend_combo.addItem(tr('membrane_laminin_method'), 'cellpose_cyto3_laminin_final_v1')
        self.ai_backend_combo.setMinimumWidth(0)
        self.ai_backend_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.ai_backend_combo.setMinimumContentsLength(1)
        self.ai_cellpose_diameter_spin = QDoubleSpinBox()
        self.ai_cellpose_diameter_spin.setRange(1., 2000.)
        self.ai_cellpose_diameter_spin.setValue(40.)
        self.ai_cellpose_diameter_spin.setSuffix(' px')
        bind_i18n(self.ai_cellpose_diameter_spin, 'cellpose_diameter_hint', 'toolTip')
        self.ai_membrane_preset_combo = QComboBox()
        self.ai_membrane_preset_combo.setMinimumWidth(0)
        self.ai_membrane_preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.ai_membrane_preset_combo.setMinimumContentsLength(1)
        for diameter in (100,180,70):
            self.ai_membrane_preset_combo.addItem(tr('membrane_reference_size').format(d=diameter),diameter)
        self.ai_membrane_preset_row = QWidget()
        membrane_row = QHBoxLayout(self.ai_membrane_preset_row)
        membrane_row.setContentsMargins(0,0,0,0)
        membrane_row.addWidget(self.ai_membrane_preset_combo,1)
        self.ai_membrane_preset_combo.activated.connect(self._load_membrane_reference)
        bind_i18n(self.ai_membrane_preset_combo,'membrane_reference_hint','toolTip')
        self.ai_allow_restricted_checkbox = bind_i18n(QCheckBox(), "allow_restricted_model")
        self.ai_scope_label = bind_i18n(QLabel(), "scope_full_image")
        self.ai_scope_label.setObjectName("DimLabel")
        self.ai_draw_roi_button = QPushButton("Draw AI ROI")
        self.ai_draw_roi_button.clicked.connect(self._draw_ai_roi)
        self.ai_clear_roi_button = QPushButton("Use Full Image")
        self.ai_clear_roi_button.clicked.connect(self._clear_ai_roi)
        self.ai_run_button = QPushButton("Run Cell Area Detection")
        self.ai_run_button.setObjectName("PrimaryButton")
        self.ai_run_button.clicked.connect(self._run_ai_assisted)
        ai_form = QFormLayout()
        self.ai_form = ai_form
        ai_backend_form = QFormLayout()
        self._add_i18n_form_row(ai_backend_form, 'segmentation_backend', self.ai_backend_combo)
        self.ai_model_hint=ModelSelectionHint(self.ai_backend_combo,dict(cellpose_cyto3='cyto3',
            cellpose_cyto3_laminin_final_v1='cyto3_laminin_final_v1'))
        ai_backend_form.addRow(self.ai_model_hint)
        ai_group_layout.addLayout(ai_backend_form)
        self._add_i18n_form_row(ai_form, 'cellpose_object_diameter', self.ai_cellpose_diameter_spin)
        ai_form.addRow(self.ai_membrane_preset_row)
        self._add_i18n_form_row(ai_form, "min_area_px", self.ai_min_area_spin)
        self._add_i18n_form_row(ai_form, "max_area_px_none", self.ai_max_area_spin)
        self._add_i18n_form_row(ai_form, "detection_sensitivity", self.ai_sensitivity_spin)
        ai_form.addRow(self.ai_allow_restricted_checkbox)
        self.ai_backend_combo.currentIndexChanged.connect(self._sync_ai_backend_controls)
        ai_group_layout.addWidget(self.ai_status_label)
        ai_group_layout.addWidget(bind_i18n(QLabel(), "channels"))
        ai_group_layout.addWidget(self.ai_channel_list)
        from ui.widgets.calibration_controls import CalibrationControls
        self.calibration_controls = CalibrationControls(length_spin=self.ai_scale_length_spin,
            x_spin=self.ai_scale_um_per_px_spin, status=self.ai_scale_status_label,
            actions=(self.ai_draw_scale_line_button, self.ai_apply_scale_button,
                     self.ai_refresh_results_button, self.ai_reset_scale_button))
        self.calibration_controls.calibration_applied.connect(self._apply_calibration_preset)
        layout.addWidget(self.calibration_controls)
        ai_group_layout.addLayout(ai_form)
        self._sync_ai_backend_controls()
        ai_group_layout.addWidget(self.ai_scope_label)
        ai_group_layout.addWidget(self.ai_draw_roi_button)
        ai_group_layout.addWidget(self.ai_clear_roi_button)
        ai_group_layout.addWidget(self.ai_run_button)
        ai_layout.addWidget(self.ai_assisted_group)

        for widget in [
            self.roi_intensity_params_panel,
            self.spot_count_params_panel,
            self.line_profile_params_panel,
            self.colocalization_params_panel,
            self.ai_assisted_params_panel,
        ]:
            self.measure_params_stack.addWidget(widget)
        self.measure_params_stack._sync_current_page_geometry()
        layout.addWidget(self.measure_params_stack)

        self.roi_intensity_summary_panel = QWidget()
        roi_summary_layout = QVBoxLayout(self.roi_intensity_summary_panel)
        self.roi_summary_label = bind_i18n(QLabel(), "roi_summary")
        self.roi_summary_label.setWordWrap(True)
        self.roi_freshness_status_label = QLabel(tr("result_status_not_calculated"))
        self.roi_freshness_status_label.setObjectName("DimLabel")
        self.roi_freshness_status_label.setWordWrap(True)
        self.open_roi_results_button = QPushButton("Open Full Results")
        self.open_roi_results_button.clicked.connect(self._open_current_results)
        self.export_roi_csv_button = QPushButton("Export CSV")
        self.delete_selected_measurement_button = QPushButton("Delete Selected")
        self.delete_selected_measurement_button.clicked.connect(self._delete_selected_measurement)
        self.clear_roi_measurements_button = QPushButton("Clear ROI Measurements")
        self.clear_roi_measurements_button.clicked.connect(self._clear_roi_measurements)
        roi_summary_layout.addWidget(self.roi_summary_label)
        roi_summary_layout.addWidget(self.roi_freshness_status_label)
        roi_summary_layout.addWidget(self.open_roi_results_button)
        roi_summary_layout.addWidget(self.clear_roi_button)
        roi_summary_layout.addWidget(self.delete_selected_measurement_button)
        self.clear_roi_measurements_button.hide()  # same action is already in ROI parameters
        self.export_roi_csv_button.hide()  # unified measurement export below
        # ROI-to-Composer remains disabled until fluorescence statistics are connected.

        self.spot_count_summary_panel = QWidget()
        spot_summary_layout = QVBoxLayout(self.spot_count_summary_panel)
        self.spot_count_summary_label = bind_i18n(QLabel(), "count_summary")
        self.spot_count_summary_label.setWordWrap(True)
        self.count_freshness_status_label = QLabel(tr("result_status_not_calculated"))
        self.count_freshness_status_label.setObjectName("DimLabel")
        self.count_freshness_status_label.setWordWrap(True)
        self.delete_selected_count_button = QPushButton("Delete Selected")
        self.delete_selected_count_button.clicked.connect(self._delete_selected_count)
        self.clear_counts_button = QPushButton("Clear Counts")
        self.clear_counts_button.clicked.connect(self._clear_counts)
        spot_summary_layout.addWidget(self.spot_count_summary_label)
        spot_summary_layout.addWidget(self.count_freshness_status_label)
        spot_summary_layout.addWidget(self.count_table)
        spot_summary_layout.addWidget(self.delete_selected_count_button)
        spot_summary_layout.addWidget(self.clear_counts_button)

        self.line_profile_summary_panel = QWidget()
        line_summary_layout = QVBoxLayout(self.line_profile_summary_panel)
        self.line_profile_summary_label = bind_i18n(QLabel(), "profile_summary")
        self.line_profile_summary_label.setWordWrap(True)
        self.clear_profile_button = QPushButton("Clear Profile")
        self.clear_profile_button.clicked.connect(self._clear_line_profile)
        line_summary_layout.addWidget(self.line_profile_summary_label)
        line_summary_layout.addWidget(self.clear_profile_button)

        self.colocalization_summary_panel = QWidget()
        coloc_summary_layout = QVBoxLayout(self.colocalization_summary_panel)
        self.coloc_summary_label = bind_i18n(QLabel(), "coloc_summary")
        self.coloc_summary_label.setWordWrap(True)
        self.overlap_table = QTableWidget(0, 5)
        self.overlap_table.setHorizontalHeaderLabels(["Base", "Compare", "Overlap px", "% Base", "% Compare"])
        self.overlap_table.setMinimumHeight(150)
        self.overlap_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.clear_colocalization_button = QPushButton("Clear Colocalization")
        self.clear_colocalization_button.clicked.connect(self._clear_channel_overlap)
        coloc_summary_layout.addWidget(self.coloc_summary_label)
        coloc_summary_layout.addWidget(self.overlap_table)
        coloc_summary_layout.addWidget(self.clear_colocalization_button)

        self.ai_assisted_summary_panel = QWidget()
        ai_summary_layout = QVBoxLayout(self.ai_assisted_summary_panel)
        self.ai_summary_label = bind_i18n(QLabel(), "ai_summary")
        self.ai_summary_label.setWordWrap(True)
        self.ai_freshness_status_label = QLabel(tr("result_status_not_calculated"))
        self.ai_freshness_status_label.setObjectName("DimLabel")
        self.ai_freshness_status_label.setWordWrap(True)
        self.ai_preview_mode_group = QButtonGroup(self.ai_assisted_summary_panel)
        self.ai_preview_mode_group.setExclusive(True)
        self.ai_original_preview_button = bind_i18n(QPushButton(), "original")
        self.ai_original_preview_button.setCheckable(True)
        self.ai_original_preview_button.setEnabled(False)
        self.ai_original_preview_button.clicked.connect(lambda: self._set_ai_overlay_visible(False))
        self.ai_overlay_preview_button = bind_i18n(QPushButton(), "v9_overlay")
        self.ai_overlay_preview_button.setCheckable(True)
        self.ai_overlay_preview_button.setEnabled(False)
        self.ai_overlay_preview_button.clicked.connect(lambda: self._set_ai_overlay_visible(True))
        self.ai_preview_mode_group.addButton(self.ai_original_preview_button)
        self.ai_preview_mode_group.addButton(self.ai_overlay_preview_button)
        self._preferred_measurement_overlay = True
        self.canvas.measurement_overlay_changed.connect(lambda available: self._sync_ai_overlay_buttons(
            has_overlay=available, show_overlay=self._preferred_measurement_overlay))
        self.export_image_button = bind_i18n(QPushButton(), "export_image")
        self.export_image_button.setObjectName("FluorescenceExportImageButton")
        self.export_image_button.setEnabled(False)
        self.export_image_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.export_image_button.clicked.connect(self.export_active_display_image)
        ai_preview_row = QHBoxLayout()
        ai_preview_row.addWidget(self.ai_original_preview_button)
        ai_preview_row.addWidget(self.ai_overlay_preview_button)
        ai_preview_row.addWidget(self.export_image_button)
        self.ai_results_table = QTableWidget(0, 8)
        self.ai_results_table.setHorizontalHeaderLabels([
            "Object ID",
            "Channel",
            "Area px",
            "Area um²",
            "Centroid X",
            "Centroid Y",
            "Mean intensity",
            "Integrated intensity",
        ])
        self.ai_results_table.setMinimumHeight(190)
        self.ai_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ai_results_table.itemSelectionChanged.connect(self._select_ai_assisted_result_from_table)
        self.ai_results_table.currentCellChanged.connect(self._select_ai_assisted_result_from_table)
        self.ai_summary_stats_table = QTableWidget(0, 10)
        self.ai_summary_stats_table.setHorizontalHeaderLabels([
            "Channel",
            "Count",
            "Total area px",
            "Total area um²",
            "Mean area px",
            "Mean area um²",
            "Median area px",
            "Median area um²",
            "Mean intensity",
            "Run ID",
        ])
        self.ai_summary_stats_table.setMinimumHeight(120)
        self.ai_summary_stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ai_summary_stats_table.itemSelectionChanged.connect(self._select_ai_assisted_result_from_table)
        self.ai_summary_stats_table.currentCellChanged.connect(self._select_ai_assisted_result_from_table)
        self.clear_ai_results_button = QPushButton("Clear AI Results")
        self.clear_ai_results_button.clicked.connect(self._clear_ai_assisted_results)
        ai_summary_layout.addWidget(self.ai_summary_label)
        ai_summary_layout.addWidget(self.ai_freshness_status_label)
        ai_summary_layout.addLayout(ai_preview_row)
        ai_summary_layout.addWidget(bind_i18n(QLabel(), "cell_area_detail"))
        ai_summary_layout.addWidget(self.ai_results_table)
        ai_summary_layout.addWidget(bind_i18n(QLabel(), "summary_statistics"))
        ai_summary_layout.addWidget(self.ai_summary_stats_table)
        ai_summary_layout.addWidget(self.clear_ai_results_button)

        for widget in [
            self.roi_intensity_summary_panel,
            self.spot_count_summary_panel,
            self.line_profile_summary_panel,
            self.colocalization_summary_panel,
            self.ai_assisted_summary_panel,
        ]:
            self.measure_summary_stack.addWidget(widget)
        self.measure_summary_stack._sync_current_page_geometry()
        layout.addWidget(self.measure_summary_stack)

        self.results_drawer = bind_i18n(QGroupBox(), "full_results", "title")
        self.results_drawer.setCheckable(True)
        self.results_drawer.setChecked(False)
        drawer_layout = QVBoxLayout(self.results_drawer)
        self.measurement_table = QTableWidget(0, 6)
        self.measurement_table.setHorizontalHeaderLabels(["ROI", "Type", "Channel", "Area px", "Mean", "IntDen"])
        self.measurement_table.setMinimumHeight(150)
        self.measurement_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.measurement_table.itemSelectionChanged.connect(self._select_measurement_from_results)
        self.measurement_table.currentCellChanged.connect(self._select_measurement_from_results)
        drawer_layout.addWidget(self.measurement_table)
        layout.addWidget(self.results_drawer)
        self.fluoro_measurement_excel_button = measurement_excel_button(self, [
            ("ROI", self.measurement_table, (0, 3, 4, 5)),
            ("Counts", self.count_table, (1, 2, 3, 4)),
            ("Colocalization", self.overlap_table, (2, 3, 4)),
            ("Cell areas", self.ai_results_table, (0, 2, 3, 4, 5, 6, 7)),
            ("Cell summary", self.ai_summary_stats_table, (1, 2, 3, 4, 5, 6, 7, 8)),
        ], "Fluorescence_measurements.xlsx")
        from core.measurement_provenance import measurement_provenance_rows
        self.fluoro_measurement_excel_button.provenance_provider = lambda: measurement_provenance_rows(self.panel)
        layout.addWidget(self.fluoro_measurement_excel_button)
        layout.addStretch(1)
        self._set_measure_mode("roi_intensity")
        return page

    def _build_stats_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = bind_i18n(QLabel(), "assign_samples_hint")
        hint.setObjectName("DimLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.fluoro_stats_source_combo = QComboBox()
        for label, value in FLUOR_STAT_SOURCES:
            self.fluoro_stats_source_combo.addItem(label, value)
        self.fluoro_stats_metric_combo = QComboBox()
        self.fluoro_stats_source_combo.currentIndexChanged.connect(self._refresh_fluoro_stats_metric_options)
        self.fluoro_stats_metric_combo.currentIndexChanged.connect(self._populate_fluoro_stats_controls)

        self.fluoro_stats_group_table = QTableWidget(0, 3)
        self.fluoro_stats_group_table.setHorizontalHeaderLabels(["Sample", "Group", "Include"])
        self.fluoro_stats_group_table.setMinimumHeight(180)
        self.fluoro_stats_group_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.fluoro_stats_reference_group_combo = QComboBox()
        self.fluoro_stats_reference_group_combo.setEditable(True)
        self.fluoro_stats_test_combo = QComboBox()
        self.fluoro_stats_test_combo.addItems(STAT_TESTS)
        self.fluoro_stats_chart_mode_combo = QComboBox()
        self.fluoro_stats_chart_mode_combo.addItem("Summary", "summary")
        self.fluoro_stats_chart_mode_combo.addItem("Frequency Bar", "frequency_bar")
        self.fluoro_stats_chart_mode_combo.addItem("Frequency Line", "frequency_line")
        self.fluoro_stats_chart_mode_combo.currentIndexChanged.connect(lambda *_args: self._populate_fluoro_stats_controls())
        self.fluoro_stats_frequency_mode_combo = QComboBox()
        self.fluoro_stats_frequency_mode_combo.addItem("Absolute Count", "absolute")
        self.fluoro_stats_frequency_mode_combo.addItem("Percent of Objects", "percent")
        self.fluoro_stats_frequency_mode_combo.currentIndexChanged.connect(
            lambda *_args: self._refresh_fluoro_stats_chart(dict(self.panel.analysis.get("fluoro_statistics", {})) if self.panel is not None else {})
        )
        self.run_fluoro_stats_button = QPushButton("Run Statistics")
        self.run_fluoro_stats_button.setObjectName("PrimaryButton")
        self.run_fluoro_stats_button.clicked.connect(self._run_fluoro_statistics)
        self.fluoro_stats_result_table = QTableWidget(0, 4)
        self.fluoro_stats_result_table.setHorizontalHeaderLabels(["Group", "n", "Mean", "SEM"])
        self.fluoro_stats_result_table.setMinimumHeight(140)
        self.fluoro_stats_result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.fluoro_stats_summary_label = QLabel("No statistics yet.")
        self.fluoro_stats_summary_label.setObjectName("DimLabel")
        self.fluoro_stats_summary_label.setWordWrap(True)
        self.fluoro_stats_add_to_layout_button = QPushButton("Add Plot to Layout")
        self.fluoro_stats_add_to_layout_button.clicked.connect(self._add_fluoro_stats_plot_to_layout)
        self.fluoro_add_distribution_bar_plot_button = QPushButton("Add Frequency Bar Plot to Layout")
        self.fluoro_add_distribution_bar_plot_button.clicked.connect(self._add_fluoro_distribution_bar_plot_to_layout)
        self.fluoro_add_distribution_line_plot_button = QPushButton("Add Frequency Line Plot to Layout")
        self.fluoro_add_distribution_line_plot_button.clicked.connect(self._add_fluoro_distribution_line_plot_to_layout)
        self.fluoro_add_line_profile_plot_button = QPushButton("Add Line Profile to Layout")
        self.fluoro_add_line_profile_plot_button.clicked.connect(self._add_line_profile_plot_to_layout)
        self.fluoro_add_colocalization_plot_button = QPushButton("Add Colocalization Plot to Layout")
        self.fluoro_add_colocalization_plot_button.clicked.connect(self._add_colocalization_plot_to_layout)
        self.fluoro_stats_chart_toggle = QToolButton()
        self.fluoro_stats_chart_toggle.setText(tr("chart_preview"))
        self.fluoro_stats_chart_toggle.setCheckable(True)
        self.fluoro_stats_chart_toggle.setChecked(True)
        self.fluoro_stats_chart_container = QWidget()
        self.fluoro_stats_chart_layout = QVBoxLayout(self.fluoro_stats_chart_container)
        self.fluoro_stats_chart_layout.setContentsMargins(0, 0, 0, 0)
        self.fluoro_stats_chart_container.setMinimumHeight(260)
        self.fluoro_stats_chart_toggle.toggled.connect(self.fluoro_stats_chart_container.setVisible)

        form = QFormLayout()
        self._add_i18n_form_row(form, "source", self.fluoro_stats_source_combo)
        self._add_i18n_form_row(form, "metric", self.fluoro_stats_metric_combo)
        self._add_i18n_form_row(form, "reference_group", self.fluoro_stats_reference_group_combo)
        self._add_i18n_form_row(form, "test", self.fluoro_stats_test_combo)
        self._add_i18n_form_row(form, "chart", self.fluoro_stats_chart_mode_combo)
        self._add_i18n_form_row(form, "frequency_scale", self.fluoro_stats_frequency_mode_combo)
        layout.addLayout(form)
        layout.addWidget(self.fluoro_stats_chart_toggle)
        layout.addWidget(self.fluoro_stats_chart_container)
        layout.addWidget(self.fluoro_stats_group_table)
        layout.addWidget(self.run_fluoro_stats_button)
        from ui.dialogs.statistics_pairing_dialog import pairing_button
        self.fluoro_pairing_button = pairing_button(self, lambda: (
            self.panel, str(self.fluoro_stats_source_combo.currentData() or 'roi_intensity'),
            self._fluoro_stats_source_rows(), self._fluoro_stats_group_assignments()))
        layout.addWidget(self.fluoro_pairing_button)
        layout.addWidget(self.fluoro_stats_result_table)
        layout.addWidget(self.fluoro_stats_summary_label)
        layout.addWidget(self.fluoro_stats_add_to_layout_button)
        layout.addWidget(self.fluoro_add_distribution_bar_plot_button)
        layout.addWidget(self.fluoro_add_distribution_line_plot_button)
        layout.addWidget(self.fluoro_add_line_profile_plot_button)
        layout.addWidget(self.fluoro_add_colocalization_plot_button)
        layout.addStretch(1)
        self._refresh_fluoro_stats_metric_options()
        return page

    def _build_layout_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.layout_tools_panel = QWidget()
        toolbar = QGridLayout(self.layout_tools_panel)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        self.layout_undo_button = QPushButton("Undo")
        self.layout_redo_button = QPushButton("Redo")
        self.layout_align_row_button = QPushButton("Align Row")
        self.layout_align_column_button = QPushButton("Align Column")
        self.layout_same_size_button = QPushButton("Same Size")
        self.layout_distribute_h_button = QPushButton("Dist H")
        self.layout_distribute_v_button = QPushButton("Dist V")
        self.layout_sync_lines_button = QPushButton("Sync Lines")
        self.layout_delete_selected_button = QPushButton("Delete")
        self.layout_recenter_button = QPushButton("Recenter")
        self.layout_undo_button.clicked.connect(lambda: self.canvas.undo_layout_edit())
        self.layout_redo_button.clicked.connect(lambda: self.canvas.redo_layout_edit())
        self.layout_align_row_button.clicked.connect(lambda: self.canvas.align_selected_layout_items("row"))
        self.layout_align_column_button.clicked.connect(lambda: self.canvas.align_selected_layout_items("column"))
        self.layout_same_size_button.clicked.connect(self.canvas.make_selected_layout_items_same_size)
        self.layout_distribute_h_button.clicked.connect(lambda: self.canvas.distribute_selected_layout_items("horizontal"))
        self.layout_distribute_v_button.clicked.connect(lambda: self.canvas.distribute_selected_layout_items("vertical"))
        self.layout_sync_lines_button.clicked.connect(self._sync_selected_layout_shapes)
        self.layout_delete_selected_button.clicked.connect(self.canvas.delete_selected_layout_items)
        self.layout_recenter_button.clicked.connect(self.canvas.fit_to_canvas)
        layout_buttons = [
            self.layout_undo_button,
            self.layout_redo_button,
            self.layout_align_row_button,
            self.layout_align_column_button,
            self.layout_same_size_button,
            self.layout_distribute_h_button,
            self.layout_distribute_v_button,
            self.layout_sync_lines_button,
            self.layout_delete_selected_button,
            self.layout_recenter_button,
        ]
        for index, button in enumerate(layout_buttons):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            toolbar.addWidget(button, index // 2, index % 2)
        toolbar.setColumnStretch(0, 1)
        toolbar.setColumnStretch(1, 1)
        self.layout_mode_combo = QComboBox()
        for value in ("merge_split", "merge_only", "batch_grid", "custom"):
            self.layout_mode_combo.addItem(value, value)
        self.layout_mode_combo.currentIndexChanged.connect(lambda _index: self._set_layout_mode(self.layout_mode_combo.currentData()))
        self.scale_length_spin = QDoubleSpinBox()
        self.scale_length_spin.setRange(0.1, 100000.0)
        self.scale_length_spin.setValue(50.0)
        self.scale_bar_length_spin = self.scale_length_spin
        self.scale_show_text = QCheckBox("Show scale text")
        self.scale_show_text.setChecked(True)
        self.scale_bar_manual_um_per_px_spin = QDoubleSpinBox()
        self.scale_bar_manual_um_per_px_spin.setRange(0.0, 100000.0)
        self.scale_bar_manual_um_per_px_spin.setDecimals(6)
        self.scale_bar_manual_um_per_px_spin.setSpecialValueText("Embedded / assumed")
        self.scale_bar_font_combo = QFontComboBox()
        self.scale_bar_font_size_spin = QDoubleSpinBox()
        self.scale_bar_font_size_spin.setRange(4.0, 72.0)
        self.scale_bar_font_size_spin.setValue(10.0)
        self.scale_bar_line_width_spin = QDoubleSpinBox()
        self.scale_bar_line_width_spin.setRange(0.1, 48.0)
        self.scale_bar_line_width_spin.setValue(4.0)
        self.scale_bar_text_position_combo = QComboBox()
        self.scale_bar_text_position_combo.addItem("Above", "above")
        self.scale_bar_text_position_combo.addItem("Below", "below")
        self.scale_bar_line_color_combo = QComboBox()
        self.scale_bar_text_color_combo = QComboBox()
        self.scale_bar_apply_current_button = QPushButton("Apply Scale Bar")
        self.scale_bar_apply_all_button = QPushButton("Apply to All Layout Items")
        self.scale_bar_remove_current_button = QPushButton("Remove Scale Bar")
        self.scale_bar_remove_all_button = QPushButton("Remove from All Layout Items")
        self.scale_bar_apply_current_button.clicked.connect(lambda: self._apply_layout_scale_bar(current_only=True))
        self.scale_bar_apply_all_button.clicked.connect(lambda: self._apply_layout_scale_bar(current_only=False))
        self.scale_bar_remove_current_button.clicked.connect(lambda: self._remove_layout_scale_bar(current_only=True))
        self.scale_bar_remove_all_button.clicked.connect(lambda: self._remove_layout_scale_bar(current_only=False))
        self.preview_layout_button = QPushButton("Preview Layout")
        self.preview_layout_button.setObjectName("PrimaryButton")
        self.preview_layout_button.clicked.connect(self._preview_layout)
        self.send_layout_to_composer_button = QPushButton("Send Layout to Composer")
        self.send_layout_to_composer_button.setObjectName("PrimaryButton")
        self.send_layout_to_composer_button.clicked.connect(self._request_layout_panel)
        self.layout_add_text_edit = QLineEdit()
        self.layout_add_text_edit.setPlaceholderText(tr("text_label"))
        self.layout_add_text_button = QPushButton("+ Add Text")
        self.layout_add_text_button.clicked.connect(self._add_layout_text)
        self.layout_text_select_button = QPushButton("Box Select Text")
        self.layout_text_select_button.clicked.connect(lambda: self._set_roi_tool("text_select"))
        self.layout_font_combo = QFontComboBox()
        self.layout_font_combo.currentFontChanged.connect(lambda font: self._apply_layout_text_style({'font_family':font.family()}))
        self.layout_text_size_spin = QDoubleSpinBox()
        self.layout_text_size_spin.setRange(0.0, 144.0)
        self.layout_text_size_spin.setSpecialValueText('—')
        self.layout_text_size_spin.setValue(10.0)
        self.layout_text_size_spin.valueChanged.connect(lambda value: self._apply_layout_text_style({'font_size':value}) if value>0 else None)
        self.layout_text_bold = QCheckBox("Bold")
        self.layout_text_italic = QCheckBox("Italic")
        self.layout_text_bold.stateChanged.connect(lambda state: self._apply_layout_text_style({'bold':state==Qt.CheckState.Checked.value}) if state!=Qt.CheckState.PartiallyChecked.value else None)
        self.layout_text_italic.stateChanged.connect(lambda state: self._apply_layout_text_style({'italic':state==Qt.CheckState.Checked.value}) if state!=Qt.CheckState.PartiallyChecked.value else None)
        self.layout_text_color_combo = QComboBox()
        for label, value in COLOR_OPTIONS:
            self.layout_text_color_combo.addItem(label, value)
        self.layout_text_color_combo.setPlaceholderText('—')
        self.layout_text_color_combo.currentIndexChanged.connect(lambda index: self._apply_layout_text_style({'color':self.layout_text_color_combo.itemData(index)}) if index>=0 else None)
        self.layout_apply_text_button = QPushButton("Apply Selected Text")
        self.layout_apply_text_button.setObjectName("PrimaryButton")
        self.layout_apply_text_button.clicked.connect(self._apply_layout_text_style)
        self.layout_apply_text_button.setVisible(False)
        self.layout_shape_combo = QComboBox()
        self.layout_shape_combo.addItems(["Line", "Rectangle", "Ellipse"])
        self.layout_shape_stroke_width_spin = QDoubleSpinBox()
        self.layout_shape_stroke_width_spin.setRange(0.1, 24.0)
        self.layout_shape_stroke_width_spin.setValue(2.0)
        self.layout_shape_stroke_width_spin.valueChanged.connect(lambda _value: self._sync_selected_layout_shapes())
        self.layout_shape_stroke_color_combo = QComboBox()
        self.layout_shape_fill_combo = QComboBox()
        for label, value in COLOR_OPTIONS:
            self.layout_shape_stroke_color_combo.addItem(label, value)
            self.layout_shape_fill_combo.addItem(label, value)
        self.layout_shape_stroke_color_combo.currentIndexChanged.connect(lambda _index: self._sync_selected_layout_shapes())
        self.layout_shape_fill_combo.currentIndexChanged.connect(lambda _index: self._sync_selected_layout_shapes())
        self.layout_add_shape_button = QPushButton("+ Add Shape")
        self.layout_add_shape_button.clicked.connect(self._add_layout_shape)
        self.layout_apply_shape_button = QPushButton("Apply Selected Shape")
        self.layout_apply_shape_button.clicked.connect(self._sync_selected_layout_shapes)
        self.layout_apply_shape_button.setVisible(False)
        self.fluoro_stats_plot_width_spin = QSpinBox()
        self.fluoro_stats_plot_width_spin.setRange(180, 1600)
        self.fluoro_stats_plot_width_spin.setValue(300)
        self.fluoro_stats_plot_height_spin = QSpinBox()
        self.fluoro_stats_plot_height_spin.setRange(140, 1200)
        self.fluoro_stats_plot_height_spin.setValue(220)
        self.fluoro_stats_font_combo = QFontComboBox()
        self.fluoro_stats_font_size_spin = QDoubleSpinBox()
        self.fluoro_stats_font_size_spin.setRange(4.0, 24.0)
        self.fluoro_stats_font_size_spin.setValue(7.0)
        self.fluoro_stats_bar_fill_combo = QComboBox()
        self.fluoro_stats_bar_width_spin = QDoubleSpinBox()
        self.fluoro_stats_bar_width_spin.setRange(0.1, 0.95)
        self.fluoro_stats_bar_width_spin.setValue(0.58)
        self.fluoro_stats_bar_line_width_spin = QDoubleSpinBox()
        self.fluoro_stats_bar_line_width_spin.setRange(0.1, 5.0)
        self.fluoro_stats_bar_line_width_spin.setValue(0.9)
        self.fluoro_stats_point_shape_combo = QComboBox()
        self.fluoro_stats_point_shape_combo.addItems(["circle", "square", "diamond"])
        self.fluoro_stats_point_size_spin = QDoubleSpinBox()
        self.fluoro_stats_point_size_spin.setRange(2.0, 36.0)
        self.fluoro_stats_point_size_spin.setValue(16.0)
        self.fluoro_stats_axis_line_width_spin = QDoubleSpinBox()
        self.fluoro_stats_axis_line_width_spin.setRange(0.1, 5.0)
        self.fluoro_stats_axis_line_width_spin.setValue(0.6)
        self.fluoro_stats_point_color_combo = QComboBox()
        self.fluoro_stats_bar_edge_combo = QComboBox()
        for label, value in COLOR_OPTIONS:
            self.scale_bar_line_color_combo.addItem(label, value)
            self.scale_bar_text_color_combo.addItem(label, value)
            self.fluoro_stats_bar_fill_combo.addItem(label, value)
            self.fluoro_stats_point_color_combo.addItem(label, value)
            self.fluoro_stats_bar_edge_combo.addItem(label, value)
        self.fluoro_stats_apply_style_button = QPushButton("Apply Statistics Style")
        self.fluoro_stats_apply_style_button.clicked.connect(self._apply_selected_fluoro_plot_style)
        self.fluoro_stats_apply_style_button.setVisible(False)
        for combo in [
            self.layout_text_color_combo,
            self.layout_shape_stroke_color_combo,
            self.layout_shape_fill_combo,
            self.scale_bar_line_color_combo,
            self.scale_bar_text_color_combo,
            self.fluoro_stats_bar_fill_combo,
            self.fluoro_stats_point_color_combo,
            self.fluoro_stats_bar_edge_combo,
        ]:
            combo.setMinimumContentsLength(8)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.layout_apply_button = QPushButton("Apply")
        self.layout_apply_button.setObjectName("PrimaryButton")
        self.layout_apply_button.clicked.connect(self._apply_layout_selection)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._add_i18n_form_row(form, "arrangement", self.layout_mode_combo)
        from ui.widgets.calibration_controls import add_scale_appearance_rows
        add_scale_appearance_rows(self, form)
        self._add_i18n_form_row(form, "stats_width", self.fluoro_stats_plot_width_spin)
        self._add_i18n_form_row(form, "stats_height", self.fluoro_stats_plot_height_spin)
        self._add_i18n_form_row(form, "stats_font", self.fluoro_stats_font_combo)
        self._add_i18n_form_row(form, "stats_font_size", self.fluoro_stats_font_size_spin)
        self._add_i18n_form_row(form, "bar_fill", self.fluoro_stats_bar_fill_combo)
        self._add_i18n_form_row(form, "bar_width", self.fluoro_stats_bar_width_spin)
        self._add_i18n_form_row(form, "bar_line", self.fluoro_stats_bar_line_width_spin)
        self._add_i18n_form_row(form, "stats_point", self.fluoro_stats_point_color_combo)
        self._add_i18n_form_row(form, "stats_edge", self.fluoro_stats_bar_edge_combo)
        self._add_i18n_form_row(form, "point_shape", self.fluoro_stats_point_shape_combo)
        self._add_i18n_form_row(form, "point_size", self.fluoro_stats_point_size_spin)
        self._add_i18n_form_row(form, "axis_line", self.fluoro_stats_axis_line_width_spin)
        form.addRow(self.fluoro_stats_apply_style_button)
        form.addRow(self.layout_apply_button)
        layout.addLayout(form)
        layout.addWidget(self.preview_layout_button)
        layout.addWidget(self.send_layout_to_composer_button)
        layout.addStretch(1)
        return page

    def _build_export_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.export_pdf_radio = QRadioButton("PDF (vector)")
        self.export_svg_radio = QRadioButton("SVG (editable)")
        self.export_tiff_radio = QRadioButton("TIFF")
        self.export_png_radio = QRadioButton("PNG")
        self.export_pdf_radio.setChecked(True)
        self.export_group = QButtonGroup(self)
        for button in [self.export_pdf_radio, self.export_svg_radio, self.export_tiff_radio, self.export_png_radio]:
            self.export_group.addButton(button)
            layout.addWidget(button)
        self.export_dpi_combo = QComboBox()
        self.export_dpi_combo.addItems(["300", "600", "1200"])
        self.export_dpi_combo.setCurrentText("1200")
        self.export_metadata_checkbox = QCheckBox("Include analysis metadata")
        self.export_metadata_checkbox.setChecked(True)
        self.export_measurements_checkbox = QCheckBox("Export measurements CSV")
        self.export_source_checkbox = QCheckBox("Include source data")
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("PrimaryButton")
        self.export_button.clicked.connect(self.export_requested.emit)
        layout.addWidget(QLabel("DPI"))
        layout.addWidget(self.export_dpi_combo)
        layout.addWidget(self.export_metadata_checkbox)
        layout.addWidget(self.export_measurements_checkbox)
        layout.addWidget(self.export_source_checkbox)
        layout.addStretch(1)
        layout.addWidget(self.export_button)
        return page

    def _build_status_row(self) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(10, 4, 10, 4)
        self.step_hint = QLabel("Step 1/6: Import fluorescence images.")
        self.step_hint.setObjectName("DimLabel")
        layout.addWidget(self.step_hint, 1)
        return wrapper

    def set_panel(self, panel: FluorescencePanel, *, refresh: bool | None = None) -> None:
        self._display_balance.cancel()
        self._cancel_pending_channel_refresh()
        panel_changed = self.panel is not panel or self.canvas.panel is not panel
        if panel_changed:
            self._roi_clear_history.clear()
        self.panel = panel
        self.canvas.set_panel(panel, refresh=False)
        should_refresh = panel_changed if refresh is None else refresh
        if should_refresh:
            self.refresh()
        else:
            self._sync_layout_scale_appearance_controls()

    def set_analysis_runner(self, runner: Callable[..., bool] | None) -> None:
        self._analysis_runner = runner

    def set_analysis_busy(self, operation: str, busy: bool) -> None:
        normalized = str(operation).strip()
        if not normalized:
            return
        if busy:
            if not self._busy_analysis_operations:
                controls = self._analysis_action_controls()
                self._analysis_controls_before_busy = {control: control.isEnabled() for control in controls}
                for control in controls:
                    control.setEnabled(False)
            self._busy_analysis_operations.add(normalized)
            return
        self._busy_analysis_operations.discard(normalized)
        if self._busy_analysis_operations:
            return
        for control, enabled in self._analysis_controls_before_busy.items():
            try:
                control.setEnabled(enabled)
            except RuntimeError:
                continue
        self._analysis_controls_before_busy.clear()

    def _analysis_action_controls(self) -> list[QWidget]:
        names = [
            "apply_threshold_button",
            "add_roi_button",
            "draw_square_button",
            "draw_circle_button",
            "draw_freehand_button",
            "count_button",
            "count_algorithm_combo",
            "count_diameter_spin",
            "count_sensitivity_spin",
            "sample_spot_button",
            "overlap_button",
            "profile_line_button",
            "ai_run_button",
            "ai_draw_roi_button",
        ]
        return [getattr(self, name) for name in names if hasattr(self, name)]

    def _run_analysis_operation(
        self,
        operation: str,
        work: Callable[[FluorescencePanel], Any],
        on_success: Callable[[Any], None],
        is_settings_current: Callable[[], bool],
    ) -> bool:
        if self.panel is None or self._busy_analysis_operations:
            return False
        captured = self._current_analysis_inputs_for(operation)

        def wrapped_success(output: Any) -> None:
            self._attach_captured_inputs(output, captured)
            on_success(output)
            self.analysis_completed.emit()

        if self._analysis_runner is None:
            wrapped_success(work(self.panel))
            return True
        return bool(
            self._analysis_runner(
                operation=operation,
                panel=self.panel,
                work=work,
                on_success=wrapped_success,
                is_settings_current=lambda:bool(is_settings_current()) and self._current_analysis_inputs_for(operation)==captured,
            )
        )

    def import_files(self, paths: list[str]) -> None:
        if self.panel is None:
            return
        for path in paths:
            self.panel.import_image(path)
        self.refresh(refresh_canvas=False)
        if str(self.mode_combo.currentData() or "").lower() == "one_click":
            self.set_step(4, refresh=False)
        else:
            self.set_step(1, refresh=False)
        self._refresh_canvas()

    def refresh(self, *, refresh_canvas: bool = True) -> None:
        self._display_balance.cancel()
        self._cancel_pending_channel_refresh()
        self._populate_active_image_selector()
        self._populate_metadata()
        self._populate_channel_controls()
        self._populate_measure_controls()
        self._populate_measurements()
        self._populate_count_results()
        self._populate_ai_assisted_results()
        self._populate_overlap_results()
        self._populate_fluoro_stats_controls()
        self._populate_black_balance_status()
        self._sync_layout_scale_appearance_controls()
        self._sync_preview_channels()
        if refresh_canvas:
            self._refresh_canvas()

        self.analysis_context_changed.emit()

    def set_step(self, index: int, *, refresh: bool = True) -> None:
        self._display_balance.cancel()
        self._cancel_pending_channel_refresh()
        if hasattr(self, "context_stack") and self.context_stack.currentIndex() == 4:
            self.persist_layout_edits_from_scene()
        self.context_stack.setCurrentIndex(index)
        if self.panel is not None:
            self.panel.data["active_step"] = int(index)
        self.context_stack.updateGeometry()
        self.updateGeometry()
        self.canvas.set_step(index, refresh=False)
        self.canvas.zoom_controls.object_controls.setVisible(index == 4)
        self._sync_preview_channels()
        labels = self._step_labels()
        hints = self._step_hints()
        for button_index, button in enumerate(self.step_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.setText(labels[button_index])
            button.blockSignals(False)
        self.step_hint.setText(hints[index])
        if hasattr(self, "canvas_tools_group"):
            self.canvas_tools_group.setVisible(index == 4)
        if hasattr(self, "active_image_group"):
            has_images = self.panel is not None and bool(self.panel.images())
            self.active_image_group.setVisible(has_images)
        if index == 2 and hasattr(self, "measure_mode"):
            self._set_measure_mode(self.measure_mode)
        if index == 3:
            self._populate_fluoro_stats_controls()
        if refresh:
            self._refresh_canvas()

    def _refresh_canvas(self) -> None:
        self.canvas.refresh()
        self._restore_ai_overlay_after_canvas_refresh()

    def _cancel_pending_channel_refresh(self) -> None:
        if self._channel_refresh_timer.isActive():
            self._channel_refresh_timer.stop()

    def _schedule_channel_refresh(self) -> None:
        self._channel_refresh_timer.start()

    def _step_labels(self) -> list[str]:
        return [tr("import"), tr("v9_channels"), tr("measure"), tr("stats"), tr("layout"), tr("export")]

    def _step_hints(self) -> list[str]:
        return [
            tr("step_fluoro_import_hint"),
            tr("step_fluoro_channels_hint"),
            tr("step_fluoro_measure_hint"),
            tr("step_fluoro_stats_hint"),
            tr("step_fluoro_layout_hint"),
            tr("step_fluoro_export_hint"),
        ]

    def _retranslate_mode_combo(self) -> None:
        current_data = self.mode_combo.currentData()
        self.mode_combo.blockSignals(True)
        labels = {
            "assisted": tr("assisted"),
            "one_click": tr("one_click"),
            "expert": tr("expert"),
        }
        for index in range(self.mode_combo.count()):
            data = str(self.mode_combo.itemData(index) or "")
            if data in labels:
                self.mode_combo.setItemText(index, labels[data])
        target_index = self.mode_combo.findData(current_data)
        if target_index >= 0:
            self.mode_combo.setCurrentIndex(target_index)
        self.mode_combo.blockSignals(False)

    def _retranslate_measurement_headers(self) -> None:
        tables = (
            (self.count_table, ("measurement_channel", "measurement_count", "area_px", "measurement_mean_area", "mean_intensity", "spot_count_method", "spot_count_parameters", "spot_count_image")),
            (self.ai_results_table, ("measurement_object_id", "measurement_channel", "area_px", "area_um2", "measurement_centroid_x", "measurement_centroid_y", "mean_intensity", "integrated_intensity")),
            (self.ai_summary_stats_table, ("measurement_channel", "measurement_count", "measurement_total_area_px", "measurement_total_area_um2", "mean_area_px", "measurement_mean_area_um2", "median_area_px", "measurement_median_area_um2", "mean_intensity", "measurement_run_id")),
            (self.measurement_table, ("ROI", "type", "measurement_channel", "area_px", "mean", "integrated_density")),
            (self.overlap_table, ("measurement_base_channel", "measurement_compare_channel", "measurement_overlap_px", "measurement_overlap_base", "measurement_overlap_compare")),
            (self.fluoro_stats_group_table, ("sample", "group", "include")),
            (self.fluoro_stats_result_table, ("group", "n", "mean", "sem")),
        )
        for table, keys in tables:
            labels = [tr(key) for key in keys]
            table.setHorizontalHeaderLabels(labels)
            if table in (self.ai_results_table, self.ai_summary_stats_table):
                header = table.horizontalHeader()
                header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
                for column, label in enumerate(labels):
                    width = header.fontMetrics().horizontalAdvance(label) + 24
                    table.setColumnWidth(column, max(80, width, table.columnWidth(column)))

    def _measurement_mode_hint(self) -> str:
        keys = {"roi_intensity": "fluoro_roi_instruction", "spot_count": "fluoro_count_instruction",
                "line_profile": "fluoro_profile_instruction", "colocalization": "fluoro_coloc_instruction",
                "ai_assisted": "fluoro_ai_instruction"}
        return tr(keys[self.measure_mode]) if self.measure_mode in keys else tr('task_'+self.measure_mode)

    def retranslate(self) -> None:
        self._sync_preview_channels()
        if hasattr(self, 'measure_task_combo'):
            retranslate_combo(self.measure_task_combo, {mode: tr('workflow_task_' + mode)
                              for mode in self.measure_mode_buttons})
            self.roi_advanced_section.retranslate()
            self.count_threshold_section.retranslate()
        retranslate_bound_widgets(self)
        retranslate_bound_widgets(self.workflow_panel)
        for label in (*self._workflow_i18n_labels, *self._form_i18n_labels, *self._channel_form_i18n_labels):
            key = label.property("i18nKey")
            if isinstance(key, str):
                label.setText(tr(key))
        self.calibration_controls.retranslate()
        for index, button in enumerate(self.step_buttons):
            button.setText(self._step_labels()[index])
        if hasattr(self, "context_stack"):
            self.step_hint.setText(self._step_hints()[self.context_stack.currentIndex()])
        self._retranslate_mode_combo()
        retranslate_combo(self.roi_black_balance_scope_combo, {"all_channels": tr("all_channels"), "selected_channel": tr("selected_channel")})
        retranslate_combo(self.count_black_balance_scope_combo, {"all_channels": tr("all_channels"), "selected_channel": tr("selected_channel")})
        retranslate_combo(self.count_algorithm_combo,{m:tr('spot_method_'+m) for m in ('traditional','cellpose_cyto3','log','cellpose_nuclei')})
        retranslate_combo(self.ai_backend_combo, {'local_heuristic': tr('local_fluoro_segmentation'),
            'cellpose_cyto3': tr('cellpose_area_method'),
            'cellpose_cyto3_laminin_final_v1': tr('membrane_laminin_method')})
        self._sync_ai_backend_controls()
        retranslate_combo(self.ai_membrane_preset_combo,{d:tr('membrane_reference_size').format(d=d) for d in (100,180,70)})
        self._retranslate_measurement_headers()
        retranslate_combo(self.roi_background_method_combo, {"None": tr("none"), "Local background ROI": tr("local_background_roi"), "Fixed value per channel": tr("fixed_value_per_channel")})
        retranslate_combo(self.scale_bar_text_position_combo, {"above": tr("above"), "below": tr("below")})
        from ui.widgets.calibration_controls import retranslate_scale_colors
        retranslate_scale_colors(self)
        retranslate_combo(self.view_mode_combo, {value: tr("fluoro_view_" + value) for value in ("merge", "split", "single")})
        retranslate_combo(self.layout_mode_combo, {value: tr("fluoro_layout_" + value) for value in ("merge_split", "merge_only", "batch_grid", "custom")})
        self._sync_layout_mode_combo(self._active_image())
        retranslate_combo(self.fluoro_stats_source_combo, {"roi_intensity": tr("roi_intensity").replace("\n", " "),
            "object_count": tr("object_count"), "ai_assisted": tr("ai_assisted_cell_area")})
        retranslate_combo(self.fluoro_stats_chart_mode_combo, {"summary": tr("summary"),
            "frequency_bar": tr("frequency_bar"), "frequency_line": tr("frequency_line")})
        retranslate_combo(self.fluoro_stats_frequency_mode_combo, {"absolute": tr("absolute_count"), "percent": tr("percent_of_objects")})
        retranslate_combo(self.fluoro_stats_metric_combo, self._localized_fluoro_metric_labels())
        if self.panel is None:
            self.fluoro_stats_summary_label.setText(tr("no_statistics_yet"))
        else:
            self._populate_fluoro_stats_results(dict(self.panel.analysis.get("fluoro_statistics", {})))
        self.import_button.setText("+ " + tr("add_images"))
        self.remove_button.setText(tr("remove_selected"))
        self.merge_selected_button.setText(tr("merge_selected_channels"))
        self.save_active_image_button.setText(tr("save_active_image_tiff"))
        self.force_split_rgb_button.setText(tr("force_split_rgb"))
        self.mode_label.setText(tr("mode"))
        self.add_roi_button.setText("+ " + tr("add_roi"))
        self.clear_roi_button.setText(tr("clear_roi_measurements"))
        self.apply_threshold_button.setText(tr("apply_threshold"))
        self.count_button.setText(tr("count_spots"))
        self.run_fluoro_stats_button.setText(tr("run_statistics"))
        self.fluoro_stats_chart_toggle.setText(tr("chart_preview"))
        self.fluoro_stats_add_to_layout_button.setText(tr("add_plot_to_layout"))
        self.layout_undo_button.setText(tr("v9_undo"))
        self.layout_redo_button.setText(tr("redo"))
        self.layout_align_row_button.setText(tr("align_row"))
        self.layout_align_column_button.setText(tr("align_column"))
        self.layout_same_size_button.setText(tr("same_size"))
        self.layout_distribute_h_button.setText(tr("distribute_h"))
        self.layout_distribute_v_button.setText(tr("distribute_v"))
        self.layout_sync_lines_button.setText(tr("sync_lines"))
        self.layout_delete_selected_button.setText(tr("delete"))
        self.layout_recenter_button.setText(tr("recenter"))
        self.preview_layout_button.setText(tr("preview_layout"))
        self.send_layout_to_composer_button.setText(tr("send_layout_to_composer"))
        self.layout_add_text_button.setText("+ " + tr("add_text"))
        self.layout_add_shape_button.setText("+ " + tr("add_shape"))
        self.scale_show_text.setText(tr("show_scale_text"))
        self.layout_add_text_edit.setPlaceholderText(tr("text_label"))
        self.layout_text_select_button.setText(tr("box_select_text"))
        self.layout_text_bold.setText(tr("bold"))
        self.layout_text_italic.setText(tr("italic"))
        self.layout_apply_text_button.setText(tr("apply_selected_text"))
        self.layout_apply_shape_button.setText(tr("apply_selected_shape"))
        self.fluoro_stats_apply_style_button.setText(tr("apply_statistics_style"))
        self.layout_apply_button.setText(tr("apply"))
        self.export_metadata_checkbox.setText(tr("include_analysis_metadata"))
        self.export_measurements_checkbox.setText(tr("export_measurements_csv"))
        self.export_source_checkbox.setText(tr("include_source_data"))
        self.export_button.setText(tr("export_figure"))
        self.mode_label.setText(tr("mode"))
        self.merge_hint_label.setText(tr("merge_hint"))
        self._populate_count_results()
        self._populate_ai_assisted_results()
        if self.context_stack.currentIndex() == 2:
            self.step_hint.setText(self._measurement_mode_hint())
        if self.last_count_preview_dialog is not None:
            self.last_count_preview_dialog.retranslate()
        self.canvas.retranslate()
        self._restore_ai_overlay_after_canvas_refresh()

    def export_active_display_image(self):
        from ui.panels.fluorescence_image_export import prompt_and_export_fluorescence_image

        image = self._active_image()
        if self.panel is None or image is None:
            return None
        overlay_button = getattr(self, "ai_overlay_preview_button", None)
        return prompt_and_export_fluorescence_image(
            self,
            self.panel,
            image,
            default_overlay=bool(overlay_button is not None and overlay_button.isChecked()),
        )

    def export_format(self) -> str:
        if self.export_svg_radio.isChecked():
            return "svg"
        if self.export_tiff_radio.isChecked():
            return "tiff"
        if self.export_png_radio.isChecked():
            return "png"
        return "pdf"

    def export_dpi(self) -> int:
        return int(self.export_dpi_combo.currentText())

    def include_metadata(self) -> bool:
        return self.export_metadata_checkbox.isChecked()

    def include_measurements(self) -> bool:
        return self.export_measurements_checkbox.isChecked()

    def include_source(self) -> bool:
        return self.export_source_checkbox.isChecked()

    def _active_image(self) -> dict | None:
        return self.panel.active_image() if self.panel is not None else None

    def _populate_active_image_selector(self) -> None:
        if self.panel is None:
            return
        current_uid = self.panel.data.get("active_image_uid")
        self.active_image_combo.blockSignals(True)
        self.active_image_combo.clear()
        self.merge_image_list.blockSignals(True)
        self.merge_image_list.clear()
        for image in self.panel.images():
            image_uid = str(image.get("uid", ""))
            image_name = str(image.get("source_name", "image"))
            self.active_image_combo.addItem(image_name, image_uid)
            item = QListWidgetItem(image_name)
            item.setData(Qt.ItemDataRole.UserRole, image_uid)
            item.setToolTip(image_name)
            self.merge_image_list.addItem(item)
            if image_uid == current_uid:
                self.active_image_combo.setCurrentIndex(self.active_image_combo.count() - 1)
        self.active_image_combo.setEnabled(self.active_image_combo.count() > 0)
        self.active_image_combo.blockSignals(False)
        self.merge_image_list.blockSignals(False)
        self.merge_image_list.setEnabled(self.merge_image_list.count() > 1)
        self.merge_image_list.setMaximumHeight(128)
        self._sync_merge_selected_button()
        if hasattr(self, "active_image_group"):
            self.active_image_group.setVisible(self.active_image_combo.count() > 0)

    def selected_merge_image_uids(self) -> list[str]:
        return [
            str(item.data(Qt.ItemDataRole.UserRole) or "")
            for item in self.merge_image_list.selectedItems()
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        ]

    def _sync_merge_selected_button(self) -> None:
        if not hasattr(self, "merge_selected_button"):
            return
        selected_count = len(self.selected_merge_image_uids())
        self.merge_selected_button.setEnabled(selected_count >= 2)
        if hasattr(self, "merge_hint_label"):
            if selected_count >= 2:
                self.merge_hint_label.setText(trf("merge_selected_hint", count=selected_count))
            else:
                self.merge_hint_label.setText(tr("merge_hint"))

    def merge_selected_images(self) -> None:
        selected = self.selected_merge_image_uids()
        if len(selected) < 2:
            self._sync_merge_selected_button()
            return
        self.merge_selected_requested.emit(selected)

    def _populate_metadata(self) -> None:
        image = self._active_image()
        metadata = dict(image.get("metadata", {})) if image else {}
        self.metadata_format.setText(str(metadata.get("source_format", "-")))
        self.metadata_dimensions.setText(f"{metadata.get('width_px', '-')} x {metadata.get('height_px', '-')}")
        self.metadata_channels.setText(str(metadata.get("n_channels", "-")))
        self.metadata_bit_depth.setText(f"{metadata.get('bit_depth', '-')} bit")
        pixel_size = metadata.get("pixel_size_um")
        self.metadata_pixel_size.setText(f"{pixel_size:g} um/px" if pixel_size else tr("not_found"))
        if hasattr(self, "ai_scale_status_label"):
            self._sync_ai_measurement_scale_controls(refresh=False)
        self.metadata_objective.setText(str(metadata.get("objective") or "-"))

    def _populate_channel_controls(self) -> None:
        self._channel_form_i18n_labels.clear()
        while self.channel_layout.count():
            item = self.channel_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._channel_widgets.clear()
        self._display_processing_controls = []
        image = self._active_image()
        if not image:
            self.save_active_image_button.setEnabled(False)
            self.force_split_rgb_button.setEnabled(False)
            self.view_mode_combo.setEnabled(False)
            self.lock_display_checkbox.setEnabled(True)
            self._sync_layout_mode_combo(None)
            hint = bind_i18n(QLabel(), "import_image_channels_hint")
            hint.setWordWrap(True)
            self.channel_layout.addWidget(hint)
            return
        self.save_active_image_button.setEnabled(True)
        interpretation_mode = rgb_interpretation_mode(image)
        auto_single_signal = interpretation_mode == "auto_single_signal"
        rgb_composite = interpretation_mode == RGB_COMPOSITE_MODE
        forced_rgb_split = interpretation_mode == "forced_rgb_split"
        self.force_split_rgb_button.setEnabled(auto_single_signal or forced_rgb_split or rgb_composite)
        self.force_split_rgb_button.setText(tr("restore_auto_detection") if forced_rgb_split else tr("force_split_rgb"))
        self.force_split_rgb_button.setToolTip(
            tr("restore_auto_detection_tooltip")
            if forced_rgb_split
            else tr("force_split_rgb_tooltip")
        )
        self.view_mode_combo.setEnabled(not auto_single_signal and not rgb_composite)
        self.lock_display_checkbox.setEnabled(not rgb_composite)
        self._sync_layout_mode_combo(image)
        if rgb_composite:
            hint = bind_i18n(QLabel(), "rgb_composite_mode_hint")
            hint.setWordWrap(True)
            self.channel_layout.addWidget(hint)
            self.channel_layout.addStretch(1)
            self.view_mode_combo.blockSignals(True)
            self.view_mode_combo.setCurrentIndex(self.view_mode_combo.findData(str(image.get("view_mode", "merge"))))
            self.view_mode_combo.blockSignals(False)
            return
        for channel in image.get("channels", []):
            channel_name = str(channel.get("name", f"Ch{channel.get('index', 0) + 1}"))
            if channel_name == COMBINED_INTENSITY_NAME:
                channel_name = tr("combined_intensity")
            row = QGroupBox(channel_name)
            form = QFormLayout(row)
            visible = bind_i18n(QCheckBox(), "visible")
            visible.setChecked(bool(channel.get("visible", True)))
            min_spin = QDoubleSpinBox()
            max_spin = QDoubleSpinBox()
            for spin in [min_spin, max_spin]:
                spin.setRange(0, 1_000_000_000)
                spin.setDecimals(3)
            min_spin.setValue(float(channel.get("display_min", 0)))
            max_spin.setValue(float(channel.get("display_max", 1)))
            color_combo = QComboBox()
            for label, value in COLOR_OPTIONS:
                color_combo.addItem(label, value)
            color_index = color_combo.findData(str(channel.get("pseudocolor", "#FFFFFF")))
            if color_index < 0:
                color_value = str(channel.get('pseudocolor', '#FFFFFF'))
                color_combo.addItem(color_value, color_value)
                color_index = color_combo.count() - 1
            color_combo.setCurrentIndex(color_index)
            index = int(channel.get("index", 0))
            visible.toggled.connect(lambda checked, i=index: self._update_channel(i, visible=checked))
            min_spin.valueChanged.connect(lambda value, i=index: self._update_channel(i, display_min=value))
            max_spin.valueChanged.connect(lambda value, i=index: self._update_channel(i, display_max=value))
            color_combo.currentIndexChanged.connect(lambda _row, combo=color_combo, i=index: self._update_channel(i, pseudocolor=str(combo.currentData())))
            for widget in [visible, min_spin, max_spin, color_combo]:
                widget.setEnabled(not self.lock_display_checkbox.isChecked())
            form.addRow(visible)
            self._add_i18n_form_row(form, "min", min_spin, self._channel_form_i18n_labels)
            self._add_i18n_form_row(form, "max", max_spin, self._channel_form_i18n_labels)
            self._add_i18n_form_row(form, "color", color_combo, self._channel_form_i18n_labels)
            for field in (min_spin, max_spin, color_combo):
                form.labelForField(field).setMinimumHeight(field.sizeHint().height())
            from ui.widgets.fluoro_display_controls import FluoroDisplayControls
            processing = FluoroDisplayControls(channel.get('display_processing'))
            processing.settings_changed.connect(lambda settings,i=index: self._display_balance.update_processing(i,settings))
            processing.point_requested.connect(lambda point,i=index: self._display_balance.begin(i,point))
            processing.setEnabled(not self.lock_display_checkbox.isChecked())
            form.addRow(processing)
            self._display_processing_controls.append(processing)
            self.channel_layout.addWidget(row)
            self._channel_widgets.append((visible, min_spin, max_spin, color_combo))
        self.channel_layout.addStretch(1)
        self.view_mode_combo.blockSignals(True)
        self.view_mode_combo.setCurrentIndex(self.view_mode_combo.findData(str(image.get("view_mode", "merge"))))
        self.view_mode_combo.blockSignals(False)

    def _populate_measure_controls(self) -> None:
        image = self._active_image()
        current = self.measure_channel_combo.currentData()
        roi_current = self.roi_channel_combo.currentData() if hasattr(self, "roi_channel_combo") else None
        self.roi_channel_combo.blockSignals(True)
        self.roi_channel_combo.clear()
        self.measure_channel_combo.blockSignals(True)
        self.measure_channel_combo.clear()
        selected_count = set(self._selected_channel_indices(self.count_channel_list)) if hasattr(self, "count_channel_list") else set()
        selected_overlap = set(self._selected_channel_indices(self.overlap_channel_list)) if hasattr(self, "overlap_channel_list") else set()
        selected_ai = set(self._selected_channel_indices(self.ai_channel_list)) if hasattr(self, "ai_channel_list") else set()
        if hasattr(self, "count_channel_list"):
            self.count_channel_list.clear()
            self.overlap_channel_list.clear()
            self.ai_channel_list.clear()
        if image:
            for channel in image.get("channels", []):
                channel_index = int(channel.get("index", 0))
                channel_name = str(channel.get("name", f"Ch{channel_index + 1}"))
                if channel_name == COMBINED_INTENSITY_NAME:
                    channel_name = tr("combined_intensity")
                self.roi_channel_combo.addItem(channel_name, channel_index)
                self.measure_channel_combo.addItem(channel_name, channel_index)
                if hasattr(self, "count_channel_list"):
                    count_item = QListWidgetItem(channel_name)
                    count_item.setData(Qt.ItemDataRole.UserRole, channel_index)
                    count_item.setSelected(channel_index in selected_count or not selected_count)
                    self.count_channel_list.addItem(count_item)
                    overlap_item = QListWidgetItem(channel_name)
                    overlap_item.setData(Qt.ItemDataRole.UserRole, channel_index)
                    overlap_item.setSelected(channel_index in selected_overlap or (not selected_overlap and channel_index < 3))
                    self.overlap_channel_list.addItem(overlap_item)
                    ai_item = QListWidgetItem(channel_name)
                    ai_item.setData(Qt.ItemDataRole.UserRole, channel_index)
                    self.ai_channel_list.addItem(ai_item)
                    ai_item.setSelected(channel_index in selected_ai or (not selected_ai and bool(channel.get("visible", True))))
            if current is not None:
                index = self.measure_channel_combo.findData(current)
                if index >= 0:
                    self.measure_channel_combo.setCurrentIndex(index)
            if roi_current is not None:
                index = self.roi_channel_combo.findData(roi_current)
                if index >= 0:
                    self.roi_channel_combo.setCurrentIndex(index)
        self.measure_channel_combo.blockSignals(False)
        self.roi_channel_combo.blockSignals(False)

    def _populate_measurements(self) -> None:
        image = self._active_image()
        rows = list(image.get("measurements", [])) if image else []
        table_rows = []
        for measurement in rows:
            if measurement.get("type") == "roi_intensity":
                for result in measurement.get("results_table", []):
                    table_rows.append((measurement, result))
            else:
                table_rows.append((measurement, measurement))
        self.measurement_table.blockSignals(True)
        self.measurement_table.setRowCount(len(table_rows))
        for row, (measurement, result) in enumerate(table_rows):
            values = [
                str(row + 1),
                str(measurement.get("roi_or_geometry", {}).get("type", measurement.get("roi_type", ""))),
                str(result.get("channel", result.get("channel_index", ""))),
                "" if result.get("area_px") is None and "area_px" in result else str(result.get("area_px", "")),
                format_optional_fixed(result.get("mean", 0.0), 3),
                format_optional_fixed(result.get("integrated_density", 0.0), 3),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, measurement.get("measurement_uuid"))
                self.measurement_table.setItem(row, column, item)
            for col, key in ((3, "area_px"), (4, "mean"), (5, "integrated_density")):
                self.measurement_table.item(row, col).setData(EXCEL_VALUE_ROLE, result.get(key))
        self.measurement_table.blockSignals(False)
        if hasattr(self, "roi_summary_label"):
            roi_measurements = [measurement for measurement in rows if measurement.get("type") == "roi_intensity"]
            last = roi_measurements[-1] if roi_measurements else None
            if last:
                channels = ", ".join(last.get("results_summary", {}).get("channels", []))
                first_row = (last.get("results_table") or [{}])[0]
                last_mean_text = format_optional_fixed(first_row.get("mean", 0.0), 3)
                last_intden_text = format_optional_fixed(first_row.get("integrated_density", 0.0), 3)
                self.roi_summary_label.setText(
                    trf(
                        "roi_runtime_summary",
                        roi_count=len(roi_measurements),
                        channels=channels,
                        last_mean=float(last_mean_text) if last_mean_text else 0.0,
                        last_intden=float(last_intden_text) if last_intden_text else 0.0,
                    )
                )
            else:
                self.roi_summary_label.setText(tr("roi_summary"))

    def _populate_count_results(self) -> None:
        from core.image_analysis.fluorescence_counting import count_provenance
        image = self._active_image()
        rows = list(image.get("object_counts", [])) if image else []
        self.count_table.blockSignals(True)
        self.count_table.setRowCount(len(rows))
        for row, result in enumerate(rows):
            method_name,parameters=count_provenance(result)
            if result.get('algorithm') not in ('log', 'cellpose_cyto3', 'cellpose_nuclei'):
                method = str(result.get('method') or 'unspecified')
                method_name = (tr('count_method_sample_display') if method == 'sample_like'
                               else trf('count_method_traditional_display', method=method))
                if result.get('spot_enhancement'):
                    method_name += tr('count_method_legacy_enhanced')
            values = [
                str(result.get("channel_name", "")),
                str(result.get("count", "")),
                '' if result.get('total_positive_area_px') is None else str(result['total_positive_area_px']),
                '' if result.get('mean_object_area_px') is None else f"{float(result['mean_object_area_px']):.2f}",
                '' if result.get('mean_intensity') is None else f"{float(result['mean_intensity']):.2f}",
                method_name,
                json.dumps({**parameters,'background_correction':result.get('background_correction',{})},ensure_ascii=False,sort_keys=True),
                str(result.get('image_name') or image.get('source_name','')),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, result.get("count_uuid"))
                self.count_table.setItem(row, column, item)
            for col, key in enumerate(("count", "total_positive_area_px", "mean_object_area_px", "mean_intensity"), 1):
                self.count_table.item(row, col).setData(EXCEL_VALUE_ROLE, result.get(key))
        self.count_table.blockSignals(False)
        if hasattr(self, "spot_count_summary_label"):
            self.spot_count_summary_label.setText(
                trf("count_history_summary", runs=len(rows))
            )

    def _populate_ai_assisted_results(self) -> None:
        if not hasattr(self, "ai_results_table"):
            return
        with QSignalBlocker(self.ai_results_table), QSignalBlocker(getattr(self, 'ai_summary_stats_table', self.ai_results_table)):
            self._fill_ai_assisted_results()

    def _fill_ai_assisted_results(self) -> None:
        image = self._active_image()
        rows = list(image.get("fluorescence_ai_assisted_measurements", [])) if image else []
        detail_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for result in rows:
            for detected_object in result.get("object_table", []):
                if isinstance(detected_object, dict):
                    detail_rows.append((result, detected_object))

        self.ai_results_table.setRowCount(len(detail_rows))
        for row, (result, detected_object) in enumerate(detail_rows):
            has_snapshot = "measurement_scale_bar" in result
            scale = result.get("measurement_scale_bar") if has_snapshot else None
            um_text, um_excel = physical_area_text_and_excel(
                detected_object.get("area_um2", 0.0) if not has_snapshot else detected_object.get("area_um2"),
                scale,
                has_snapshot=has_snapshot,
            )
            area_px = detected_object.get("area_px", 0)
            values = [
                str(detected_object.get("object_id", row + 1)),
                str(result.get("channel_name", "")),
                str(int(area_px)) if format_optional_fixed(area_px, 0) else "",
                um_text,
                format_optional_fixed(detected_object.get("centroid_x", 0.0), 2),
                format_optional_fixed(detected_object.get("centroid_y", 0.0), 2),
                format_optional_fixed(detected_object.get("mean_intensity", 0.0), 2),
                format_optional_fixed(detected_object.get("integrated_intensity", 0.0), 2),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, result.get("ai_measurement_uuid"))
                self.ai_results_table.setItem(row, column, item)
            for col, key in enumerate(("area_px", "area_um2", "centroid_x", "centroid_y", "mean_intensity", "integrated_intensity"), 2):
                excel_value = um_excel if key == "area_um2" else detected_object.get(key)
                self.ai_results_table.item(row, col).setData(EXCEL_VALUE_ROLE, excel_value)

        if hasattr(self, "ai_summary_stats_table"):
            self.ai_summary_stats_table.setRowCount(len(rows))
            for row, result in enumerate(rows):
                has_snapshot = "measurement_scale_bar" in result
                scale = result.get("measurement_scale_bar") if has_snapshot else None
                total_text, total_excel = physical_area_text_and_excel(
                    result.get("total_object_area_um2", 0.0) if not has_snapshot else result.get("total_object_area_um2"),
                    scale,
                    has_snapshot=has_snapshot,
                )
                mean_text, mean_excel = physical_area_text_and_excel(
                    result.get("mean_object_area_um2", 0.0) if not has_snapshot else result.get("mean_object_area_um2"),
                    scale,
                    has_snapshot=has_snapshot,
                )
                median_text, median_excel = physical_area_text_and_excel(
                    result.get("median_object_area_um2", 0.0) if not has_snapshot else result.get("median_object_area_um2"),
                    scale,
                    has_snapshot=has_snapshot,
                )
                values = [
                    str(result.get("channel_name", "")),
                    str(result.get("ai_object_count", "")),
                    str(result.get("total_object_area_px", "")),
                    total_text,
                    format_optional_fixed(result.get("mean_object_area_px", 0.0), 2),
                    mean_text,
                    format_optional_fixed(result.get("median_object_area_px", 0.0), 2),
                    median_text,
                    format_optional_fixed(result.get("mean_intensity", 0.0), 2),
                    str(result.get("ai_measurement_uuid", ""))[:8],
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, result.get("ai_measurement_uuid"))
                    self.ai_summary_stats_table.setItem(row, column, item)
                excel_by_col = {
                    1: result.get("ai_object_count"),
                    2: result.get("total_object_area_px"),
                    3: total_excel,
                    4: result.get("mean_object_area_px"),
                    5: mean_excel,
                    6: result.get("median_object_area_px"),
                    7: median_excel,
                    8: result.get("mean_intensity"),
                }
                for col, excel_value in excel_by_col.items():
                    self.ai_summary_stats_table.item(row, col).setData(EXCEL_VALUE_ROLE, excel_value)
        total = sum(int(result.get("ai_object_count", 0)) for result in rows)
        self.ai_summary_label.setText(
            trf("ai_runtime_summary", runs=len(rows), total=total)
        )

    def _set_ai_overlay_visible(self, visible: bool, *, remember: bool = True) -> None:
        if not hasattr(self, "ai_overlay_preview_button"):
            return
        if remember:
            self._preferred_measurement_overlay = bool(visible)
        self.canvas.set_measurement_overlay_visible(bool(visible))
        self.ai_original_preview_button.blockSignals(True)
        self.ai_overlay_preview_button.blockSignals(True)
        self.ai_original_preview_button.setChecked(not bool(visible))
        self.ai_overlay_preview_button.setChecked(bool(visible))
        self.ai_original_preview_button.blockSignals(False)
        self.ai_overlay_preview_button.blockSignals(False)

    def _sync_ai_overlay_buttons(self, *, has_overlay: bool, show_overlay: bool = True) -> None:
        if not hasattr(self, "ai_overlay_preview_button"):
            return
        self.ai_original_preview_button.setEnabled(bool(has_overlay))
        self.ai_overlay_preview_button.setEnabled(bool(has_overlay))
        if hasattr(self, "export_image_button"):
            self.export_image_button.setEnabled(self._active_image() is not None)
        self._set_ai_overlay_visible(bool(show_overlay and has_overlay and self._preferred_measurement_overlay), remember=False)

    def _restore_ai_overlay_after_canvas_refresh(self) -> None:
        if hasattr(self, 'measurement_preview'):
            self.measurement_preview.sync()
            return
        if getattr(self, "measure_mode", "") != "ai_assisted" or not hasattr(self, "ai_overlay_preview_button"):
            return
        image = self._active_image()
        rows = list(image.get("fluorescence_ai_assisted_measurements", [])) if image else []
        total = sum(int(result.get("ai_object_count", 0)) for result in rows)
        if not rows or total <= 0:
            self._sync_ai_overlay_buttons(has_overlay=False, show_overlay=False)
            return
        show_overlay = self.ai_overlay_preview_button.isChecked()
        self.canvas.show_ai_assisted_overlay(rows)
        self._sync_ai_overlay_buttons(has_overlay=True, show_overlay=show_overlay)

    def _populate_black_balance_status(self) -> None:
        if not hasattr(self, "roi_black_balance_status_label"):
            return
        image = self._active_image()
        correction = dict(image.get("background_correction", {})) if image else {}
        if correction.get("mode") == "black_balance":
            backgrounds = dict(correction.get("channel_backgrounds", {}))
            summary = ", ".join(f"Ch{int(index) + 1}: {float(value):.1f}" for index, value in sorted(backgrounds.items(), key=lambda item: int(item[0])))
            scope = tr("selected_channel") if correction.get("scope") == "selected_channel" else tr("all_channels")
            text = f"{tr('black_balance_active')} - {scope} ({summary})"
        else:
            text = tr("black_balance_not_set")
        self.roi_black_balance_status_label.setText(text)
        self.count_black_balance_status_label.setText(text)

    def _populate_overlap_results(self) -> None:
        image = self._active_image()
        rows = list(image.get("channel_overlap", [])) if image else []
        self.overlap_table.setRowCount(len(rows))
        for row, result in enumerate(rows):
            values = [
                str(result.get("base_channel_name", "")),
                str(result.get("compare_channel_name", "")),
                str(result.get("overlap_px", "")),
                f"{float(result.get('overlap_percent_of_base', 0.0)):.2f}",
                f"{float(result.get('overlap_percent_of_compare', 0.0)):.2f}",
            ]
            for column, value in enumerate(values):
                self.overlap_table.setItem(row, column, QTableWidgetItem(value))
            for col, key in enumerate(("overlap_px", "overlap_percent_of_base", "overlap_percent_of_compare"), 2):
                self.overlap_table.item(row, col).setData(EXCEL_VALUE_ROLE, result.get(key))

    def _localized_fluoro_metric_labels(self) -> dict[str, str]:
        metric_keys = {"mean": "mean_intensity", "integrated_density": "integrated_density", "area_px": "area_px",
            "count": "measurement_count", "mean_object_area_px": "measurement_mean_area", "mean_intensity": "mean_intensity",
            "ai_object_count": "measurement_count", "smart_object_count": "measurement_count",
            "total_object_area_px": "measurement_total_area_px", "total_object_area_um2": "measurement_total_area_um2",
            "mean_object_area_um2": "measurement_mean_area_um2", "integrated_intensity": "integrated_intensity"}
        return {value: tr(key) for value, key in metric_keys.items()}

    def _refresh_fluoro_stats_metric_options(self, *_args) -> None:
        if not hasattr(self, "fluoro_stats_metric_combo"):
            return
        source = str(self.fluoro_stats_source_combo.currentData() or "roi_intensity")
        current = self.fluoro_stats_metric_combo.currentData()
        self.fluoro_stats_metric_combo.blockSignals(True)
        self.fluoro_stats_metric_combo.clear()
        labels = self._localized_fluoro_metric_labels()
        for label, value in FLUOR_STAT_METRICS.get(source, []):
            self.fluoro_stats_metric_combo.addItem(labels.get(value, label), value)
        if current is not None:
            index = self.fluoro_stats_metric_combo.findData(current)
            if index >= 0:
                self.fluoro_stats_metric_combo.setCurrentIndex(index)
        self.fluoro_stats_metric_combo.blockSignals(False)
        self._populate_fluoro_stats_controls()

    def _populate_fluoro_stats_controls(self, *_args) -> None:
        if self.panel is None or not hasattr(self, "fluoro_stats_group_table"):
            return
        existing_groups: dict[str, str] = {}
        existing_includes: dict[str, bool] = {}
        for row in range(self.fluoro_stats_group_table.rowCount()):
            label_item = self.fluoro_stats_group_table.item(row, 0)
            group_item = self.fluoro_stats_group_table.item(row, 1)
            include_widget = self.fluoro_stats_group_table.cellWidget(row, 2)
            if label_item is None:
                continue
            label = str(label_item.data(Qt.ItemDataRole.UserRole) or label_item.text())
            if group_item is not None:
                existing_groups[label] = group_item.text()
            if isinstance(include_widget, QCheckBox):
                existing_includes[label] = include_widget.isChecked()
        rows = self._fluoro_stats_source_rows()
        self.fluoro_stats_group_table.setRowCount(len(rows))
        self.fluoro_stats_reference_group_combo.blockSignals(True)
        self.fluoro_stats_reference_group_combo.clear()
        for row, result in enumerate(rows):
            label = str(result.get("lane_label", ""))
            sample_key = str(result.get("sample_group_key") or result.get("sample_key", label))
            label_item = QTableWidgetItem(label)
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            label_item.setData(Qt.ItemDataRole.UserRole, sample_key)
            group = existing_groups.get(sample_key, label)
            self.fluoro_stats_group_table.setItem(row, 0, label_item)
            self.fluoro_stats_group_table.setItem(row, 1, QTableWidgetItem(group))
            include = QCheckBox()
            include.setChecked(existing_includes.get(sample_key, True))
            self.fluoro_stats_group_table.setCellWidget(row, 2, include)
            if self.fluoro_stats_reference_group_combo.findText(group) < 0:
                self.fluoro_stats_reference_group_combo.addItem(group)
        self.fluoro_stats_reference_group_combo.blockSignals(False)
        if self.panel.analysis.get("fluoro_statistics"):
            result = dict(self.panel.analysis["fluoro_statistics"])
            self._populate_fluoro_stats_results(result)
            self._refresh_fluoro_stats_chart(result)
        else:
            self._populate_fluoro_stats_results({})
            self._refresh_fluoro_stats_chart({})
        self._refresh_fluoro_distribution_controls()

    def _fluoro_stats_source_rows(self) -> list[dict]:
        if self.panel is None or not hasattr(self, "fluoro_stats_source_combo"):
            return []
        source = str(self.fluoro_stats_source_combo.currentData() or "roi_intensity")
        metric = str(self.fluoro_stats_metric_combo.currentData() or "mean")
        return collect_fluoro_stat_values(self.panel, source, metric)

    def _fluoro_stats_group_assignments(self) -> dict[str, str]:
        assignments: dict[str, str] = {}
        for row in range(self.fluoro_stats_group_table.rowCount()):
            include_widget = self.fluoro_stats_group_table.cellWidget(row, 2)
            if isinstance(include_widget, QCheckBox) and not include_widget.isChecked():
                continue
            label_item = self.fluoro_stats_group_table.item(row, 0)
            group_item = self.fluoro_stats_group_table.item(row, 1)
            if label_item is None or group_item is None:
                continue
            label = str(label_item.data(Qt.ItemDataRole.UserRole) or label_item.text())
            assignments[label] = group_item.text().strip() or label
        return assignments

    def _run_fluoro_statistics(self) -> None:
        if self.panel is None:
            return
        rows = self._fluoro_stats_source_rows()
        if not rows:
            source_label = self.fluoro_stats_source_combo.currentText() or "selected source"
            self.fluoro_stats_result_table.setRowCount(0)
            self.fluoro_stats_summary_label.setText(
                trf("no_source_measurements", source=source_label)
            )
            self.panel.analysis.pop("fluoro_statistics", None)
            self._refresh_fluoro_stats_chart({})
            return
        assignments = self._fluoro_stats_group_assignments()
        if not assignments:
            self.panel.analysis.pop('fluoro_statistics',None)
            self._populate_fluoro_stats_results({})
            self._refresh_fluoro_stats_chart({})
            return
        reference = self.fluoro_stats_reference_group_combo.currentText().strip()
        if not reference and assignments:
            reference = next(iter(assignments.values()))
        result = compute_fluoro_statistics(
            self.panel,
            str(self.fluoro_stats_source_combo.currentData() or "roi_intensity"),
            str(self.fluoro_stats_metric_combo.currentData() or "mean"),
            assignments,
            reference,
            self.fluoro_stats_test_combo.currentText(),
        )
        self.panel.append_audit("run_fluorescence_statistics", f"source={result.get('source')}; metric={result.get('metric')}; test={result.get('test')}")
        self._populate_fluoro_stats_results(result)
        self._refresh_fluoro_stats_chart(result)

    def _refresh_fluoro_distribution_controls(self) -> None:
        if not hasattr(self, "fluoro_stats_chart_mode_combo"):
            return
        distribution_rows = collect_fluoro_distribution_rows(self.panel, source="ai_assisted") if self.panel is not None else []
        ai_enabled = bool(distribution_rows)
        chart_mode = str(self.fluoro_stats_chart_mode_combo.currentData() or "summary")
        for data_value in ("frequency_bar", "frequency_line"):
            index = self.fluoro_stats_chart_mode_combo.findData(data_value)
            if index >= 0:
                item = self.fluoro_stats_chart_mode_combo.model().item(index)
                if item is not None:
                    item.setEnabled(ai_enabled)
        if not ai_enabled and chart_mode != "summary":
            summary_index = self.fluoro_stats_chart_mode_combo.findData("summary")
            if summary_index >= 0:
                self.fluoro_stats_chart_mode_combo.setCurrentIndex(summary_index)
                chart_mode = "summary"
        self.fluoro_add_distribution_bar_plot_button.setEnabled(ai_enabled)
        self.fluoro_add_distribution_line_plot_button.setEnabled(ai_enabled)
        if chart_mode == "frequency_bar":
            self.fluoro_stats_add_to_layout_button.setText(tr("add_frequency_bar_to_layout"))
            self.fluoro_stats_result_table.setVisible(False)
            self.run_fluoro_stats_button.setVisible(False)
        elif chart_mode == "frequency_line":
            self.fluoro_stats_add_to_layout_button.setText(tr("add_frequency_line_to_layout"))
            self.fluoro_stats_result_table.setVisible(False)
            self.run_fluoro_stats_button.setVisible(False)
        else:
            self.fluoro_stats_add_to_layout_button.setText(tr("add_plot_to_layout"))
            self.fluoro_stats_result_table.setVisible(True)
            self.run_fluoro_stats_button.setVisible(True)
        if ai_enabled:
            image_count = len({str(row.get("image_uid", "")) for row in distribution_rows if str(row.get("image_uid", ""))})
            self.fluoro_stats_summary_label.setText(
                trf(
                    "ai_frequency_data_multiple",
                    series=len(distribution_rows),
                    images=image_count,
                )
                if image_count >= 2
                else tr("ai_frequency_data_single")
            )
        if ai_enabled and chart_mode in {"frequency_bar", "frequency_line"}:
            self._refresh_fluoro_stats_chart(dict(self.panel.analysis.get("fluoro_statistics", {})) if self.panel is not None else {})

    def _current_distribution_result(self) -> dict[str, Any]:
        if self.panel is None:
            return {}
        rows = collect_fluoro_distribution_rows(self.panel, source="ai_assisted")
        if not rows:
            return {}
        assignments = self._fluoro_stats_group_assignments() if str(self.fluoro_stats_source_combo.currentData() or "") == "ai_assisted" else {}
        series = []
        for row in rows:
            sample_key = str(row.get("sample_group_key") or row.get("sample_key", row.get("lane_label", "")))
            if assignments and sample_key not in assignments:
                continue
            series.append({**row, "group": assignments.get(sample_key, str(row.get("group", row.get("lane_label", sample_key))))})
        return {
            "source": "ai_assisted",
            "metric": str(series[0].get("metric", "object_area_distribution_px") if series else "object_area_distribution_px"),
            "frequency_mode": str(self.fluoro_stats_frequency_mode_combo.currentData() or "absolute"),
            "unit": str(series[0].get("unit", "px^2") if series else "px^2"),
            "series": series,
        }

    def _populate_fluoro_stats_results(self, result: dict) -> None:
        if not hasattr(self, "fluoro_stats_result_table"):
            return
        groups = list(result.get("groups", []))
        self.fluoro_stats_result_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            values = [
                str(group.get("group", "")),
                str(group.get("n", "")),
                f"{float(group.get('mean', 0.0)):.3f}",
                f"{float(group.get('sem', 0.0)):.3f}",
            ]
            for column, value in enumerate(values):
                self.fluoro_stats_result_table.setItem(row, column, QTableWidgetItem(value))
        labels = [f"{item.get('group1')} vs {item.get('group2')}: {item.get('label')}" for item in result.get("comparisons", [])]
        labels.extend(tr(message) for message in result.get('warnings',[]))
        self.fluoro_stats_summary_label.setText("; ".join(labels) if labels else tr("no_comparison_available"))

    def _fluoro_stats_axis_label(self, result: dict) -> str:
        source = str(result.get("source", self.fluoro_stats_source_combo.currentData() or "roi_intensity"))
        metric = str(result.get("metric", self.fluoro_stats_metric_combo.currentData() or "mean"))
        for label, value in FLUOR_STAT_METRICS.get(source, []):
            if value == metric:
                return label
        return metric.replace("_", " ").title()

    def _fluoro_stats_plot_style(self) -> dict:
        if self.panel is None:
            return resolve_statistics_plot_style("Nature")
        plot_state = dict(self.panel.analysis.get("fluoro_stats_plot", {}))
        return resolve_statistics_plot_style(str(self.panel.style.get("preset", "Nature")), saved_style=dict(plot_state.get("style", {})))

    def _clear_fluoro_stats_chart(self) -> None:
        if not hasattr(self, "fluoro_stats_chart_layout"):
            return
        for index in reversed(range(self.fluoro_stats_chart_layout.count())):
            widget = self.fluoro_stats_chart_layout.itemAt(index).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _refresh_fluoro_stats_chart(self, result: dict) -> None:
        self._clear_fluoro_stats_chart()
        chart_mode = str(self.fluoro_stats_chart_mode_combo.currentData() or "summary") if hasattr(self, "fluoro_stats_chart_mode_combo") else "summary"
        distribution_result = self._current_distribution_result() if chart_mode in {"frequency_bar", "frequency_line"} else {}
        if chart_mode == "summary" and not result.get("plot_data"):
            return
        if chart_mode in {"frequency_bar", "frequency_line"} and not distribution_result.get("series"):
            return
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

            if chart_mode == "frequency_bar":
                figure = build_distribution_comparison_figure(
                    distribution_result,
                    style=self._fluoro_stats_plot_style(),
                    plot_kind="bar",
                    x_label=f"Cell area ({display_unit(str(distribution_result.get('unit', 'px^2')))})",
                    frequency_mode=str(self.fluoro_stats_frequency_mode_combo.currentData() or "absolute"),
                )
            elif chart_mode == "frequency_line":
                figure = build_distribution_comparison_figure(
                    distribution_result,
                    style=self._fluoro_stats_plot_style(),
                    plot_kind="line",
                    x_label=f"Cell area ({display_unit(str(distribution_result.get('unit', 'px^2')))})",
                    frequency_mode=str(self.fluoro_stats_frequency_mode_combo.currentData() or "absolute"),
                )
            else:
                figure = build_group_statistics_figure(result, style=self._fluoro_stats_plot_style(), y_label=self._fluoro_stats_axis_label(result))
            chart = FigureCanvasQTAgg(figure)
            chart.setMinimumHeight(240)
            chart.setMaximumHeight(320)
            chart.setStyleSheet("background: white;")
            self.fluoro_stats_chart_layout.addWidget(chart)
            chart.draw()
        except Exception:
            self._clear_fluoro_stats_chart()

    def _add_fluoro_stats_plot_to_layout(self) -> None:
        if self.panel is None:
            return
        chart_mode = str(self.fluoro_stats_chart_mode_combo.currentData() or "summary")
        if chart_mode == "frequency_bar":
            self._add_fluoro_distribution_plot_to_layout("frequency_bar", "AI cell area frequency")
            return
        if chart_mode == "frequency_line":
            self._add_fluoro_distribution_plot_to_layout("frequency_line", "AI cell area frequency")
            return
        result = dict(self.panel.analysis.get("fluoro_statistics", {}))
        if not result.get("plot_data"):
            self.fluoro_stats_summary_label.setText(tr("run_statistics_first"))
            return
        record = self._new_layout_plot_record(
            "statistics",
            title=self._fluoro_stats_axis_label(result),
            rows=[],
            statistics_result=result,
            source=str(result.get("source", "")),
            metric=str(result.get("metric", "")),
        )
        self.panel.analysis.setdefault("fluoro_layout_plots", []).append(record)
        self.panel.analysis["fluoro_stats_plot"] = record
        self.panel.append_audit("add_fluorescence_statistics_plot_to_layout", record["plot_uuid"])
        self.set_step(4)

    def _add_fluoro_distribution_bar_plot_to_layout(self) -> None:
        self._add_fluoro_distribution_plot_to_layout("frequency_bar", "AI object area frequency")

    def _add_fluoro_distribution_line_plot_to_layout(self) -> None:
        self._add_fluoro_distribution_plot_to_layout("frequency_line", "AI object area frequency")

    def _add_fluoro_distribution_plot_to_layout(self, plot_type: str, title: str) -> None:
        if self.panel is None:
            return
        distribution_result = self._current_distribution_result()
        if not distribution_result.get("series"):
            self.fluoro_stats_summary_label.setText(tr("run_ai_first"))
            return
        if len(distribution_result.get("series", [])) < 2:
            self.fluoro_stats_summary_label.setText(tr("frequency_plot_ready"))
        record = self._new_layout_plot_record(
            plot_type,
            title=title,
            rows=[],
            statistics_result={},
            distribution_result=distribution_result,
            source="ai_assisted",
            metric=str(distribution_result.get("metric", "object_area_distribution_px")),
        )
        self.panel.analysis.setdefault("fluoro_layout_plots", []).append(record)
        self.panel.analysis["fluoro_stats_plot"] = record
        self.panel.append_audit(f"add_{plot_type}_plot_to_layout", record["plot_uuid"])
        self.fluoro_stats_chart_mode_combo.setCurrentIndex(self.fluoro_stats_chart_mode_combo.findData(plot_type))
        self._refresh_fluoro_stats_chart(dict(self.panel.analysis.get("fluoro_statistics", {})))
        self.set_step(4)

    def _add_line_profile_plot_to_layout(self) -> None:
        if self.panel is None:
            return
        rows = list(self.panel.analysis.get("line_profile", []))
        if not rows:
            self.fluoro_stats_summary_label.setText(tr("no_line_profile_result"))
            return
        record = self._new_layout_plot_record("line_profile", title="Line profile", rows=rows)
        self.panel.analysis.setdefault("fluoro_layout_plots", []).append(record)
        self.panel.append_audit("add_fluorescence_line_profile_plot_to_layout", record["plot_uuid"])
        self.set_step(4)

    def _add_colocalization_plot_to_layout(self) -> None:
        if self.panel is None:
            return
        rows = list(self.panel.analysis.get("channel_overlap", []))
        if not rows:
            self.fluoro_stats_summary_label.setText(tr("no_colocalization_result"))
            return
        record = self._new_layout_plot_record("colocalization", title="Colocalization overlap", rows=rows)
        self.panel.analysis.setdefault("fluoro_layout_plots", []).append(record)
        self.panel.append_audit("add_fluorescence_colocalization_plot_to_layout", record["plot_uuid"])
        self.set_step(4)

    def _new_layout_plot_record(
        self,
        plot_type: str,
        title: str,
        rows: list[dict],
        statistics_result: dict | None = None,
        distribution_result: dict | None = None,
        source: str = "",
        metric: str = "",
    ) -> dict:
        return {
            "plot_uuid": uuid4().hex,
            "plot_type": plot_type,
            "enabled": True,
            "title": title,
            "rows": deepcopy(rows),
            "statistics_result": deepcopy(statistics_result or {}),
            "distribution_result": deepcopy(distribution_result or {}),
            "source": source,
            "metric": metric,
            "width_px": int(self.fluoro_stats_plot_width_spin.value()) if hasattr(self, "fluoro_stats_plot_width_spin") else 300,
            "height_px": int(self.fluoro_stats_plot_height_spin.value()) if hasattr(self, "fluoro_stats_plot_height_spin") else 220,
            "style": self._fluoro_stats_control_style() if hasattr(self, "fluoro_stats_point_color_combo") else {},
        }

    def _fluoro_stats_control_style(self) -> dict:
        return {
            "font_family": self.fluoro_stats_font_combo.currentFont().family(),
            "font_size_pt": float(self.fluoro_stats_font_size_spin.value()),
            "bar_fill_color": str(self.fluoro_stats_bar_fill_combo.currentData() or "#FFFFFF"),
            "point_color": str(self.fluoro_stats_point_color_combo.currentData() or "#22D7E6"),
            "bar_edge_color": str(self.fluoro_stats_bar_edge_combo.currentData() or "#FFFFFF"),
            "bar_width_fraction": float(self.fluoro_stats_bar_width_spin.value()),
            "bar_line_width": float(self.fluoro_stats_bar_line_width_spin.value()),
            "point_shape": self.fluoro_stats_point_shape_combo.currentText(),
            "point_size": float(self.fluoro_stats_point_size_spin.value()),
            "axis_line_width": float(self.fluoro_stats_axis_line_width_spin.value()),
        }

    def _selected_layout_plot_uuid(self) -> str:
        for item in self.canvas.scene.selectedItems():
            if str(item.data(0)) in {"fluoro_stats_plot", "fluoro_profile_plot", "fluoro_coloc_plot"}:
                return str(item.data(1) or "")
        plots = list(self.panel.analysis.get("fluoro_layout_plots", [])) if self.panel is not None else []
        return str(plots[-1].get("plot_uuid", "")) if plots else ""

    def _apply_selected_fluoro_plot_style(self) -> None:
        if self.panel is None:
            return
        self.persist_layout_edits_from_scene()
        plot_uuid = self._selected_layout_plot_uuid()
        plots = list(self.panel.analysis.get("fluoro_layout_plots", []))
        for plot in plots:
            if str(plot.get("plot_uuid", "")) != plot_uuid:
                continue
            plot["width_px"] = int(self.fluoro_stats_plot_width_spin.value())
            plot["height_px"] = int(self.fluoro_stats_plot_height_spin.value())
            current_style = dict(plot.get("style", {}))
            current_style.update(self._fluoro_stats_control_style())
            plot["style"] = current_style
            if plot.get("plot_type") == "statistics":
                self.panel.analysis["fluoro_stats_plot"] = dict(plot)
            break
        self.panel.analysis["fluoro_layout_plots"] = plots
        self.canvas.refresh()

    def _apply_layout_selection(self) -> None:
        selected_markers = {str(item.data(0) or "") for item in self.canvas.scene.selectedItems()}
        if selected_markers & {"fluoro_layout_text"}:
            self._apply_layout_text_style()
        if selected_markers & {"fluoro_layout_shape"}:
            self._sync_selected_layout_shapes()
        if selected_markers & {"fluoro_stats_plot", "fluoro_profile_plot", "fluoro_coloc_plot"}:
            self._apply_selected_fluoro_plot_style()
            return
        self.persist_layout_edits_from_scene()

    def persist_layout_edits_from_scene(self) -> None:
        if self.panel is None:
            return
        layout_objects = self.canvas.layout_objects()
        if layout_objects:
            self.canvas.apply_layout_objects(layout_objects, refresh=False, append_audit=False)

    def _add_layout_shape(self) -> None:
        shape_type = self.layout_shape_combo.currentText().lower()
        image = self._active_image()
        if self.panel is not None and image:
            self.persist_layout_edits_from_scene()
            shape_uuid = uuid4().hex
            shape = {
                "shape_uuid": shape_uuid,
                "shape_type": shape_type,
                "x": 40.0,
                "y": 40.0,
                "width": 120.0,
                "height": 70.0,
                "stroke_width": float(self.layout_shape_stroke_width_spin.value()),
                "stroke_color": str(self.layout_shape_stroke_color_combo.currentData() or "#FFFFFF"),
                "fill_color": str(self.layout_shape_fill_combo.currentData() or "#FFFFFF"),
            }
            image.setdefault("layout_shapes", []).append(shape)
            self.panel.append_audit("add_fluorescence_layout_shape", shape["shape_uuid"])
            self.canvas.refresh()
            for item in self.canvas.scene.items():
                if item.data(0) == "fluoro_layout_shape" and item.data(1) == shape_uuid:
                    item.setSelected(True)
            return
        self.canvas.add_layout_shape(
            shape_type,
            stroke_width=float(self.layout_shape_stroke_width_spin.value()),
            stroke_color=str(self.layout_shape_stroke_color_combo.currentData() or "#FFFFFF"),
            fill_color=str(self.layout_shape_fill_combo.currentData() or "#FFFFFF"),
        )

    def _sync_selected_layout_shapes(self) -> None:
        self.canvas.apply_shape_style_to_selection(
            stroke_width=float(self.layout_shape_stroke_width_spin.value()),
            stroke_color=str(self.layout_shape_stroke_color_combo.currentData() or "#FFFFFF"),
            fill_color=str(self.layout_shape_fill_combo.currentData() or "#FFFFFF"),
        )
        self.persist_layout_edits_from_scene()

    def _selected_channel_indices(self, list_widget: QListWidget) -> list[int]:
        return [int(item.data(Qt.ItemDataRole.UserRole)) for item in list_widget.selectedItems()]

    def _effective_ai_channels(self) -> list[int]:
        selected = self._selected_channel_indices(self.ai_channel_list)
        if selected:
            return selected
        image = self._active_image() or {}
        return [
            int(channel.get("index", 0))
            for channel in image.get("channels", [])
            if channel.get("visible", True)
        ]

    def _effective_count_channels(self) -> list[int]:
        selected = self._selected_channel_indices(self.count_channel_list)
        return selected or [int(self.measure_channel_combo.currentData() or 0)]

    def _effective_overlap_channels(self) -> list[int]:
        selected = self._selected_channel_indices(self.overlap_channel_list)
        if len(selected) >= 2:
            return selected
        image = self._active_image() or {}
        return [int(channel.get("index", 0)) for channel in image.get("channels", [])[:3]]

    def _select_image_from_combo(self, _index: int) -> None:
        if self.panel is None:
            return
        image_uid = str(self.active_image_combo.currentData() or "").strip()
        if not image_uid or image_uid == str(self.panel.data.get("active_image_uid", "")):
            return
        controller = getattr(self, "_analysis_state", None)
        if controller is not None:
            controller.capture_outgoing("fluorescence")
        self.panel.set_active_image(image_uid)
        self._clear_ai_roi()
        self.refresh()
        if controller is not None:
            controller.restore_incoming("fluorescence")

    def selected_image_uid(self) -> str:
        image_uid = str(self.active_image_combo.currentData() or "").strip()
        if image_uid:
            return image_uid
        return ""

    def _set_channel_controls_locked(self, locked: bool) -> None:
        if locked:
            self._display_balance.cancel()
        for control in getattr(self, '_display_processing_controls', []):
            control.setEnabled(not locked)
        for widgets in self._channel_widgets:
            for widget in widgets:
                widget.setEnabled(not locked)

    def _update_channel(self, channel_index: int, **updates) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        self.panel.update_channel(str(image.get("uid")), channel_index, **updates)
        self._sync_preview_channels()
        self._schedule_channel_refresh()

    def _set_view_mode(self, view_mode: str) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        self.panel.set_view_mode(str(image.get("uid")), view_mode)
        self._sync_preview_channels()
        self._cancel_pending_channel_refresh()
        self._refresh_canvas()

    def _sync_preview_channels(self) -> None:
        image = self._active_image()
        count = len(image.get('channels', [])) if image else 0
        self.canvas.single_channel_index = max(0, min(self.canvas.single_channel_index, count - 1))
        self.preview_channels.sync(image, self.canvas.single_channel_index, self.canvas.current_step)
        if image and hasattr(self, 'view_mode_combo'):
            with QSignalBlocker(self.view_mode_combo):
                self.view_mode_combo.setCurrentIndex(self.view_mode_combo.findData(image.get('view_mode', 'merge')))

    def _select_preview_channel(self, choice) -> None:
        self._display_balance.cancel()
        image = self._active_image()
        if not image or self.canvas.current_step not in (0, 1, 2):
            return
        if isinstance(choice, int):
            if not 0 <= choice < len(image.get('channels', [])):
                return
            self.canvas.single_channel_index = choice
            self._set_view_mode('single')
        else:
            self._set_view_mode(choice)

    def _set_layout_mode(self, layout_mode: str) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        if self.canvas.current_step == 4:
            self.persist_layout_edits_from_scene()
        try:
            self.panel.set_layout_mode(str(image.get("uid")), layout_mode)
        except ValueError:
            self._sync_layout_mode_combo(image)
            return
        self._cancel_pending_channel_refresh()
        self.canvas.set_step(4)

    def _sync_layout_mode_combo(self, image: dict[str, Any] | None) -> None:
        composite = bool(image) and rgb_interpretation_mode(image) == RGB_COMPOSITE_MODE
        model = self.layout_mode_combo.model()
        for row in range(self.layout_mode_combo.count()):
            value = self.layout_mode_combo.itemData(row)
            item = model.item(row) if hasattr(model, "item") else None
            if item is not None:
                item.setEnabled(not (composite and value == "merge_split"))
        if not image:
            return
        self.layout_mode_combo.blockSignals(True)
        index = self.layout_mode_combo.findData(str(image.get("layout_mode", "merge_only")))
        if index >= 0:
            self.layout_mode_combo.setCurrentIndex(index)
        self.layout_mode_combo.blockSignals(False)

    def _select_scale_target_from_canvas(self):
        if not hasattr(self, "scale_target_combo") or self.panel is None:
            return
        for item in self.canvas.scene.selectedItems():
            marker = item.data(0)
            if marker == "fluoro_scale_bar":
                target = (str(item.data(2)), str(item.data(1)))
            elif marker in {"fluoro_image_channel", "fluoro_image_merge"}:
                uid = str(item.data(3) or (self._active_image() or {}).get("uid", ""))
                key = item.data(1)
                if not isinstance(key, str):
                    key = f"merge_split:channel:{key}" if marker == "fluoro_image_channel" else f"{(self._active_image() or {}).get('layout_mode', 'merge_split')}:merge"
                target = (uid, key)
            else:
                continue
            for index in range(self.scale_target_combo.count()):
                if self.scale_target_combo.itemData(index) == target:
                    if self.scale_target_combo.currentIndex()==index:self._sync_layout_scale_appearance_controls()
                    else:self.scale_target_combo.setCurrentIndex(index)
                    return

    def _scale_target_record(self):
        selected = self.scale_target_combo.currentData()
        uid = selected[0] if selected else None
        return next((image for image in self.panel.images() if image.get("uid") == uid), self._active_image()) if self.panel else None

    def _scale_target_changed(self):
        self._sync_layout_scale_appearance_controls()

    def _sync_layout_scale_appearance_controls(self) -> None:
        if self.panel is None or not hasattr(self, "scale_target_combo"):
            return
        from ui.widgets.calibration_controls import set_scale_targets, sync_scale_appearance_controls
        active_uid = (self._active_image() or {}).get("uid")
        entries = []
        for image in self.panel.images():
            name = str(image.get("source_name") or image.get("uid", ""))
            channels = {int(c.get("index", 0)): c.get("name", str(c.get("index", 0))) for c in image.get("channels", [])}
            for target in self._layout_scale_bar_target_ids(image, False):
                label = tr("export_merged_image")
                if ":channel:" in target:
                    label = str(channels.get(int(target.rsplit(":", 1)[1]), target))
                entries.append((f"{name} · {label}", (str(image.get("uid")), target)))
        preferred = None
        if active_uid != getattr(self, "_scale_active_uid", None):
            preferred = next((target for _, target in entries if target[0] == active_uid), None)
        self._scale_active_uid = active_uid
        set_scale_targets(self, entries, preferred)
        image = self._scale_target_record() or {}
        target = self.scale_target_combo.currentData()
        state = next((s for s in image.get("layout_scale_bars", []) if target and s.get("target_id") == target[1]), {})
        template = {**dict(image.get("layout_scale_bar_template") or {}), **dict(state.get("template") or {})}
        sync_scale_appearance_controls(self, normalize_scale_bar_template(template),
                                      manual_um_per_px=state.get("manual_um_per_px", 0))

    def _update_scale_bar(self) -> None:
        # Live length/text edits affect the selected presentation only.
        if self.panel is not None and hasattr(self, "scale_target_combo"):
            self._apply_layout_scale_bar(current_only=True)

    def _layout_scale_bar_template_from_controls(self) -> dict[str, object]:
        image=self._scale_target_record() or {}
        target=self.scale_target_combo.currentData()
        state=next((s for s in image.get('layout_scale_bars',[]) if target and s.get('target_id')==target[1]),{})
        return normalize_scale_bar_template(
            {
                **dict(image.get('layout_scale_bar_template') or {}),
                **dict(state.get('template') or {}),
                "label_length_um": self.scale_bar_length_spin.value(),
                "show_text": self.scale_show_text.isChecked(),
                "font_family": self.scale_bar_font_combo.currentFont().family(),
                "font_size": self.scale_bar_font_size_spin.value(),
                "line_thickness": self.scale_bar_line_width_spin.value(),
                "line_color": str(self.scale_bar_line_color_combo.currentData() or "#FFFFFF"),
                "text_color": str(self.scale_bar_text_color_combo.currentData() or "#FFFFFF"),
                "text_position": self.scale_bar_text_position_combo.currentData(),
            }
        )

    def _layout_scale_bar_target_ids(self, image: dict, current_only: bool) -> list[str]:
        mode = str(image.get("layout_mode", "merge_split"))
        targets = [f"{mode}:merge"]
        if rgb_interpretation_mode(image) != RGB_COMPOSITE_MODE:
            targets += [f"merge_split:channel:{int(c.get('index', 0))}" for c in image.get("channels", []) if c.get("visible", True)]
        if current_only:
            selected = self.scale_target_combo.currentData()
            return [selected[1]] if selected and selected[0] == image.get("uid") else targets[:1]
        return targets

    def _apply_layout_scale_bar(self, *, current_only: bool) -> None:
        if self.panel is None or not self._active_image():
            return
        template = self._layout_scale_bar_template_from_controls()
        manual = self.scale_bar_manual_um_per_px_spin.value() if self.canvas.current_step != 0 else 0
        from ui.widgets.calibration_controls import validate_import_scale
        images = [self._scale_target_record()] if current_only else self.panel.images()
        if not validate_import_scale(self, [image for image in images if image]):
            return
        if self.canvas.current_step == 4:
            self.persist_layout_edits_from_scene()
            self.canvas._record_layout_snapshot()
        from ui.widgets.calibration_controls import apply_target_scale_style
        images = [self._scale_target_record()] if current_only else self.panel.images()
        for image in images:
            if image:
                apply_target_scale_style(image, "layout_scale_bars", self._layout_scale_bar_target_ids(image, current_only), template, manual)
        self.canvas.refresh()
        self._sync_layout_scale_appearance_controls()

    def _remove_layout_scale_bar(self, *, current_only: bool) -> None:
        if self.panel is None:
            return
        if self.canvas.current_step == 4:
            self.persist_layout_edits_from_scene()
            self.canvas._record_layout_snapshot()
        images = [self._scale_target_record()] if current_only else self.panel.images()
        for image in images:
            if not image:
                continue
            existing = {str(s.get("target_id", "")): dict(s) for s in image.get("layout_scale_bars", [])}
            for target in self._layout_scale_bar_target_ids(image, current_only):
                existing.setdefault(target, {"target_id": target})["enabled"] = False
            image["layout_scale_bars"] = list(existing.values())
        self.canvas.refresh()

    def _apply_threshold(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        image_uid = str(image.get("uid"))
        channel = int(self.measure_channel_combo.currentData() or 0)
        method = self.threshold_method_combo.currentText()
        manual_value = self.threshold_value_spin.value()

        def work(panel: FluorescencePanel) -> None:
            panel.apply_threshold(image_uid, channel, method, manual_value)

        def on_success(_result: None) -> None:
            threshold = self._latest_threshold(image_uid, channel)
            if threshold is not None:
                self.threshold_value_spin.blockSignals(True)
                self.threshold_value_spin.setValue(float(threshold))
                self.threshold_value_spin.blockSignals(False)
            self.refresh()

        self._run_analysis_operation(
            "threshold",
            work,
            on_success,
            lambda: (
                int(self.measure_channel_combo.currentData() or 0) == channel
                and self.threshold_method_combo.currentText() == method
                and self.threshold_value_spin.value() == manual_value
            ),
        )

    def _delete_selected_measurement(self) -> None:
        if self.panel is None:
            return
        items = self.measurement_table.selectedItems()
        if not items:
            return
        measurement_uuid = str(items[0].data(Qt.ItemDataRole.UserRole) or "")
        if not measurement_uuid:
            return
        if self.panel.delete_measurement(measurement_uuid):
            self.refresh()
            self._open_current_results()

    def _clear_roi_measurements(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        rows = [
            (index, deepcopy(row)) for index, row in enumerate(image.get("measurements", []))
            if row.get("type") == "roi_intensity"
        ]
        if not rows:
            return
        if QMessageBox.question(
            self, tr("clear_roi_measurements"), tr("clear_roi_measurements_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        measurement_ids = {row["measurement_uuid"] for _, row in rows}
        before = {
            "image_uid": str(image.get("uid")),
            "measurement_ids": measurement_ids,
            "image_rows": rows,
            "analysis_rows": [
                (index, deepcopy(row)) for index, row in enumerate(self.panel.analysis.get("measurements", []))
                if row.get("measurement_uuid") in measurement_ids
            ],
        }
        if self.panel.clear_measurements(before["image_uid"], measurement_type="roi_intensity"):
            after = {**before, "image_rows": [], "analysis_rows": []}
            self._roi_clear_history.record(SnapshotCommand(
                tr("clear_roi_measurements"), before, after, self._restore_roi_clear,
            ))
            self.refresh()

    def _restore_roi_clear(self, state: dict) -> None:
        if self.panel is None:
            return
        image = next((item for item in self.panel.images() if item.get("uid") == state["image_uid"]), None)
        if image is None:
            return
        def restore_rows(current, saved):
            remaining = [row for row in current if row.get("measurement_uuid") not in state["measurement_ids"]]
            for index, row in saved:
                remaining.insert(min(index, len(remaining)), deepcopy(row))
            return remaining
        image["measurements"] = restore_rows(image.get("measurements", []), state["image_rows"])
        self.panel.analysis["measurements"] = restore_rows(self.panel.analysis.get("measurements", []), state["analysis_rows"])
        self.panel._invalidate_fluoro_statistics()
        self.panel.append_audit("undo_roi_clear" if state["image_rows"] else "redo_roi_clear", state["image_uid"])
        self.refresh()

    def undo_roi_clear(self, *, redo: bool = False) -> bool:
        return (self._roi_clear_history.redo if redo else self._roi_clear_history.undo)()

    def _delete_selected_count(self) -> None:
        if self.panel is None:
            return
        items = self.count_table.selectedItems()
        if not items:
            return
        count_uuid = str(items[0].data(Qt.ItemDataRole.UserRole) or "")
        if not count_uuid:
            return
        if self.panel.delete_object_count(count_uuid):
            self.refresh()
            self._set_measure_mode("spot_count")

    def _clear_counts(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        if self.panel.clear_object_counts(str(image.get("uid"))):
            self.refresh()
            self._set_measure_mode("spot_count")

    def _draw_ai_roi(self) -> None:
        self.canvas.set_roi_mode("rectangle")
        self.step_hint.setText(tr("ai_roi_tool_active"))

    def _clear_ai_roi(self) -> None:
        self._ai_roi_type = None
        self._ai_roi_geometry = None
        if hasattr(self, "ai_scope_label"):
            self.ai_scope_label.setText(tr("scope_full_image"))

    def _run_ai_assisted(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        channels = self._effective_ai_channels()
        image_uid = str(image.get("uid"))
        channels = [int(index) for index in channels]
        settings = self.ai_analysis_settings()
        roi_type = self._ai_roi_type
        geometry = deepcopy(self._ai_roi_geometry)

        def work(panel: FluorescencePanel) -> list[dict]:
            return panel.run_ai_assisted_analysis(
                image_uid,
                channel_indices=channels,
                settings=settings,
                roi_type=roi_type,
                geometry=geometry,
            )

        def on_success(records: list[dict]) -> None:
            if records:
                total = sum(int(record.get("ai_object_count", 0)) for record in records)
                self.ai_status_label.setText(
                    trf("ai_cell_area_completed", total=total, channels=len(records))
                )
                self.canvas.show_ai_assisted_overlay(records)
                self._sync_ai_overlay_buttons(has_overlay=total > 0, show_overlay=True)
            else:
                self.ai_status_label.setText(tr("ai_cell_area_empty"))
                self.canvas.clear_measurement_overlay()
                self._sync_ai_overlay_buttons(has_overlay=False, show_overlay=False)
            self._populate_ai_assisted_results()
            self._populate_fluoro_stats_controls()

        def settings_current() -> bool:
            return (
                self._effective_ai_channels() == channels
                and self.ai_analysis_settings() == settings
                and self._ai_roi_type == roi_type
                and self._ai_roi_geometry == geometry
            )

        self._run_analysis_operation("ai", work, on_success, settings_current)

    def _clear_ai_assisted_results(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        self.panel.clear_ai_assisted_measurements(str(image.get("uid")))
        self._populate_ai_assisted_results()
        self.canvas.clear_measurement_overlay()
        self._sync_ai_overlay_buttons(has_overlay=False, show_overlay=False)
        self._populate_fluoro_stats_controls()

    def _clear_line_profile(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        self.panel.clear_line_profile(str(image.get("uid")))
        self.line_profile_summary_label.setText(tr("profile_summary"))
        self.canvas.refresh()
        self._populate_fluoro_stats_controls()

    def _clear_channel_overlap(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        self.panel.clear_channel_overlap(str(image.get("uid")))
        self._populate_overlap_results()
        self.canvas.refresh()
        self._populate_fluoro_stats_controls()

    def _add_roi(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        channel = int(self.roi_channel_combo.currentData() or 0)
        background = None
        fixed_background = None
        method = self.roi_background_method_combo.currentData() if hasattr(self, "roi_background_method_combo") else "None"
        if method == "Fixed value per channel":
            fixed_background = {channel: self.roi_fixed_background_spin.value()}
        image_uid = str(image.get("uid"))
        geometry = {
            "x": self.roi_x.value(),
            "y": self.roi_y.value(),
            "w": self.roi_w.value(),
            "h": self.roi_h.value(),
        }

        def work(panel: FluorescencePanel) -> dict:
            return panel.create_roi_intensity_measurement(
                image_uid,
                "rectangle",
                geometry,
                [channel],
                background_roi=background,
                background_mean_by_channel=fixed_background,
            )

        def on_success(measurement: dict) -> None:
            self.refresh()
            self.canvas.show_measurement_overlay(measurement)

        self._run_analysis_operation(
            "roi-intensity",
            work,
            on_success,
            lambda: (
                int(self.roi_channel_combo.currentData() or 0) == channel
                and self.roi_background_method_combo.currentData() == method
                and self.roi_fixed_background_spin.value()
                == (fixed_background or {}).get(channel, self.roi_fixed_background_spin.value())
                and {
                    "x": self.roi_x.value(),
                    "y": self.roi_y.value(),
                    "w": self.roi_w.value(),
                    "h": self.roi_h.value(),
                }
                == geometry
            ),
        )

    def _count_settings_changed(self, *_args) -> None:
        self.count_algorithm_hint.setText(tr('spot_method_changed'))

    def _show_latest_count_rows(self, rows) -> None:
        self._populate_count_results()
        if rows and self.count_table.rowCount():
            index=max(0,self.count_table.rowCount()-len(rows))
            self.count_table.selectRow(index)
            self.count_table.scrollToItem(self.count_table.item(index,0))

    def _sync_count_algorithm(self, *_args) -> None:
        method=self.count_algorithm_combo.currentData()
        traditional=method=='traditional'
        self.sample_spot_button.setEnabled(traditional)
        self.count_diameter_spin.setEnabled(not traditional)
        self.count_sensitivity_spin.setEnabled(method=='log')
        self.count_min_area_spin.setEnabled(method!='log')
        self.threshold_method_combo.setEnabled(traditional)
        self.threshold_value_spin.setEnabled(traditional)
        self.apply_threshold_button.setEnabled(traditional)
        self.count_algorithm_hint.setText(tr('spot_method_hint'))
        self.canvas.set_roi_mode('')

    def _count_spots(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        channels = self._effective_count_channels()
        image_uid = str(image.get("uid"))
        channels = [int(index) for index in channels]
        method = self.threshold_method_combo.currentText()
        manual_value = self.threshold_value_spin.value()
        min_area = self.count_min_area_spin.value()
        algorithm=self.count_algorithm_combo.currentData()
        diameter=self.count_diameter_spin.value()
        sensitivity=self.count_sensitivity_spin.value()

        def work(panel: FluorescencePanel) -> list[dict]:
            return panel.count_channel_objects(
                image_uid,
                channels,
                method=method,
                manual_value=manual_value,
                min_area_px=min_area,
                algorithm=algorithm,diameter_px=diameter,sensitivity=sensitivity,
            )

        def on_success(_rows: list[dict]) -> None:
            self._show_latest_count_rows(_rows)
            self._populate_overlap_results()
            self._populate_fluoro_stats_controls()

        self._run_analysis_operation(
            "count",
            work,
            on_success,
            lambda: (
                self._effective_count_channels() == channels
                and self.threshold_method_combo.currentText() == method
                and self.threshold_value_spin.value() == manual_value
                and self.count_min_area_spin.value() == min_area
                and self.count_algorithm_combo.currentData()==algorithm
                and self.count_diameter_spin.value()==diameter
                and self.count_sensitivity_spin.value()==sensitivity
            ),
        )

    def _plot_overlap(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        channels = self._effective_overlap_channels()
        if len(channels) < 2:
            return
        image_uid = str(image.get("uid"))
        channels = [int(index) for index in channels]
        method = self.threshold_method_combo.currentText()
        manual_value = self.threshold_value_spin.value()

        def work(panel: FluorescencePanel) -> list[dict]:
            panel.compute_channel_overlap(image_uid, channels, method=method, manual_value=manual_value)
            return panel.line_profile(image_uid, channels)

        def on_success(rows: list[dict]) -> None:
            self._populate_overlap_results()
            self.canvas.draw_line_profile_chart(rows)

        self._run_analysis_operation(
            "overlap-profile",
            work,
            on_success,
            lambda: (
                self._effective_overlap_channels() == channels
                and self.threshold_method_combo.currentText() == method
                and self.threshold_value_spin.value() == manual_value
            ),
        )

    def _set_roi_tool(self, mode: str) -> None:
        self.canvas.set_roi_mode(mode)
        self.step_hint.setText(trf("roi_tool_active", mode=mode))

    def _sync_ai_measurement_scale_controls(self, *, refresh: bool = True) -> None:
        if self.panel is None or not hasattr(self, "ai_scale_status_label"):
            return
        image = self._active_image()
        if not image:
            self.calibration_controls.set_scale({})
            self.ai_scale_status_label.setText(tr("scale_no_image_selected"))
            return
        scale = self.panel.measurement_scale_bar(str(image.get("uid", "")))
        self.calibration_controls.set_scale(scale, metadata=image.get('metadata', {}))
        source = str(scale.get("calibration_source", "assumed"))
        length_um = float(scale.get("length_um", 50.0) or 50.0)
        um_per_px = float(scale.get("um_per_px", 0.0) or 0.0)
        self.ai_scale_length_spin.blockSignals(True)
        self.ai_scale_um_per_px_spin.blockSignals(True)
        self.ai_scale_length_spin.setValue(length_um)
        self.ai_scale_um_per_px_spin.setValue(um_per_px if source != "embedded" else 0.0)
        self.ai_scale_length_spin.blockSignals(False)
        self.ai_scale_um_per_px_spin.blockSignals(False)
        label = trf("scale_status", um_per_px=um_per_px, length_um=length_um, source=tr('scale_source_'+source))
        if source == "assumed":
            label += tr("scale_assumed_suffix")
        self.ai_scale_status_label.setText(label)
        if refresh:
            self._refresh_canvas()

    def _draw_ai_scale_line(self) -> None:
        if self.panel is None or not self._active_image():
            return
        self.canvas.set_roi_mode("scale_line")
        self.step_hint.setText(tr("known_scale_line_hint"))

    def _apply_ai_measurement_scale(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        image_uid = str(image.get("uid", ""))
        updates = self.calibration_controls.updates_for(self.panel.measurement_scale_bar(image_uid))
        self.panel.update_measurement_scale_bar(image_uid, **updates)
        self._refresh_current_scale_preview()

    def _reset_ai_measurement_scale(self) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        self.panel.update_measurement_scale_bar(str(image.get("uid", "")), length_um=50.0, um_per_px=0.0, line_px={}, preset_id=None, preset_name=None)
        self._refresh_current_scale_preview()

    def _apply_calibration_preset(self, updates) -> None:
        image = self._active_image()
        if self.panel is None or not image:
            return
        self.panel.update_measurement_scale_bar(str(image['uid']), **updates)
        self._refresh_current_scale_preview()

    def _commit_fluorescence_display_line_move(self, image_uid: str, line: dict) -> None:
        from ui.main_window import MainWindow
        from ui.panels.brightfield_workspace import measurement_preview_line_px

        if self.panel is None:
            return
        scale = dict(self.panel.measurement_scale_bar(image_uid))
        image = self._active_image() or {}
        metadata = dict(image.get("metadata") or {})
        moved = MainWindow._length_preserving_line(
            measurement_preview_line_px(scale) or dict(scale.get("line_px") or {}),
            dict(line),
            int(metadata.get("width_px") or metadata.get("width") or 0),
            int(metadata.get("height_px") or metadata.get("height") or 0),
        )
        updates: dict[str, object] = {"display_line_px": dict(moved)}
        if str(scale.get("calibration_source") or "") == "manual_line":
            updates["line_px"] = dict(moved)
            updates["calibration_source"] = "manual_line"
        self.panel.update_measurement_scale_bar(image_uid, **updates)

    def _refresh_current_scale_preview(self) -> None:
        self._sync_ai_measurement_scale_controls()
        history = tr("scale_history_uses_previous_calibration")
        current = str(self.ai_scale_status_label.text() or "").strip()
        if history not in current:
            self.ai_scale_status_label.setText(f"{current} {history}".strip() if current else history)

    def _refresh_ai_measurement_scale_results(self) -> None:
        if self.panel is None:
            return
        image = self._active_image()
        if not image:
            return
        image_uid = str(image.get("uid", ""))
        self.panel.refresh_ai_assisted_measurements_for_scale(image_uid)
        self._sync_ai_measurement_scale_controls(refresh=False)
        self._populate_ai_assisted_results()
        self._populate_fluoro_stats_controls()
        self._refresh_fluoro_stats_chart(dict(self.panel.analysis.get("fluoro_statistics", {})))
        self._refresh_canvas()

    def _add_drawn_roi(self, roi_type: str, geometry: object) -> None:
        if self._display_balance.consume(roi_type, geometry):
            return
        image = self._active_image()
        if self.panel is None or not image or not isinstance(geometry, dict):
            return
        if self._busy_analysis_operations:
            self.canvas.set_roi_mode("")
            return
        if roi_type == "scale_line":
            clean_geometry = dict(geometry)
            move_existing = bool(clean_geometry.pop("move_existing", False))
            clean_geometry.pop("source_channel_index", None)
            if move_existing:
                self._commit_fluorescence_display_line_move(str(image.get("uid", "")), clean_geometry)
                self.canvas.set_roi_mode("")
                self._sync_ai_measurement_scale_controls()
                return
            self.panel.update_measurement_scale_bar(
                str(image.get("uid", "")),
                length_um=self.ai_scale_length_spin.value(),
                line_px=clean_geometry,
                calibration_source="manual_line",
            )
            self.canvas.set_roi_mode("")
            self._sync_ai_measurement_scale_controls()
            return
        source_channel = geometry.get("source_channel_index")
        channel = int(source_channel if source_channel is not None else (self.roi_channel_combo.currentData() or 0))
        if self.measure_mode == "ai_assisted":
            clean_geometry = dict(geometry)
            clean_geometry.pop("source_channel_index", None)
            self._ai_roi_type = roi_type
            self._ai_roi_geometry = clean_geometry
            self.ai_scope_label.setText(f"{tr('scope')}: {roi_type} ROI")
            self.ai_status_label.setText(tr("ai_roi_selected"))
            self.canvas.set_roi_mode("")
            return
        if roi_type == "black_balance":
            self.panel.set_black_balance_roi(str(image.get("uid")), dict(geometry), scope=self._current_black_balance_scope())
            self.canvas.set_roi_mode("")
            self.refresh()
            self.step_hint.setText(tr("black_balance_active"))
            return
        if roi_type == "sample_spot":
            selected_channels = self._selected_channel_indices(self.count_channel_list)
            channels = list(selected_channels)
            if not channels:
                channels = [channel]
            if source_channel is not None:
                channels = [channel]
            image_uid = str(image.get("uid"))
            channels = [int(index) for index in channels]
            sample_geometry = deepcopy(geometry)
            if self.count_algorithm_combo.currentData()!='traditional':
                self.canvas.set_roi_mode('')
                return

            def work(panel: FluorescencePanel) -> tuple[list[dict], QImage | None]:
                rows = panel.count_like_sample(
                    image_uid,
                    channels,
                    sample_geometry,
                    sample_roi_type="rectangle",
                )
                return rows, FluorescenceWorkspace._count_review_image_for_panel(panel, image_uid, rows)

            def on_success(result: tuple[list[dict], QImage | None]) -> None:
                rows, review_image = result
                self._show_latest_count_rows(rows)
                self._populate_fluoro_stats_controls()
                self._show_count_preview_dialog(rows, review_image=review_image, build_review_if_missing=False)

            self.canvas.set_roi_mode("")
            self._run_analysis_operation(
                "sample-count",
                work,
                on_success,
                lambda: self.count_algorithm_combo.currentData() == 'traditional' and channels
                == (
                    [channel]
                    if source_channel is not None
                    else self._selected_channel_indices(self.count_channel_list)
                    or [int(self.roi_channel_combo.currentData() or 0)]
                ),
            )
            return
        if roi_type == "profile_line":
            channels = self._effective_overlap_channels()
            image_uid = str(image.get("uid"))
            channels = [int(index) for index in channels]
            line_geometry = deepcopy(geometry)

            def work(panel: FluorescencePanel) -> list[dict]:
                return panel.line_profile(image_uid, channels, line_geometry)

            def on_success(rows: list[dict]) -> None:
                self._populate_overlap_results()
                self.canvas.draw_line_profile_chart(rows)

            self.canvas.set_roi_mode("")
            self._run_analysis_operation(
                "line-profile",
                work,
                on_success,
                lambda: self._effective_overlap_channels() == channels,
            )
            return
        clean_geometry = dict(geometry)
        clean_geometry.pop("source_channel_index", None)
        image_uid = str(image.get("uid"))

        def work(panel: FluorescencePanel) -> dict:
            return panel.create_roi_intensity_measurement(image_uid, roi_type, clean_geometry, [channel])

        def on_success(measurement: dict) -> None:
            self.refresh()
            self.canvas.show_measurement_overlay(measurement)

        self.canvas.set_roi_mode("")
        self._run_analysis_operation(
            "roi-intensity",
            work,
            on_success,
            lambda: source_channel is not None or int(self.roi_channel_combo.currentData() or 0) == channel,
        )

    def _set_measure_mode(self, mode: str) -> None:
        modes = ["roi_intensity", "spot_count", "line_profile", "colocalization", "ai_assisted"]
        task_index = self.measure_task_combo.findData(mode) if hasattr(self, 'measure_task_combo') else -1
        if mode not in modes and task_index < 0:
            return
        controller = getattr(self, "_analysis_state", None)
        if controller is not None and not controller._restoring and mode != getattr(self, "measure_mode", ""):
            controller.capture_outgoing("fluorescence")
        self.measure_mode = mode
        if hasattr(self, 'measure_task_combo'):
            self.measure_task_combo.setCurrentIndex(task_index)
        if mode not in modes:
            self.canvas.clear_measurement_overlay()
            self.canvas.set_roi_mode('')
            self.analysis_context_changed.emit()
            return
        index = modes.index(mode)
        if hasattr(self, "measure_params_stack"):
            self.measure_params_stack.setCurrentIndex(index)
            self.measure_summary_stack.setCurrentIndex(index)
            self.measure_params_stack._sync_current_page_geometry()
            self.measure_summary_stack._sync_current_page_geometry()
        if hasattr(self, "measure_mode_buttons"):
            for name, button in self.measure_mode_buttons.items():
                button.blockSignals(True)
                button.setChecked(name == mode)
                button.blockSignals(False)
        canvas_modes = {
            "roi_intensity": "rectangle",
            "spot_count": "",
            "line_profile": "profile_line",
            "colocalization": "rectangle",
            "ai_assisted": "",
        }
        if mode != "roi_intensity":
            self.canvas.clear_measurement_overlay()
        self.canvas.set_roi_mode(canvas_modes[mode])
        if mode=='ai_assisted':
            self._restore_ai_overlay_after_canvas_refresh()
        if hasattr(self, "step_hint"):
            self.step_hint.setText(self._measurement_mode_hint())
        controller = getattr(self, "_analysis_state", None)
        if controller is not None and not controller._restoring:
            controller.reload_bar("fluorescence")
        self.analysis_context_changed.emit()

    def _open_current_results(self) -> None:
        section = getattr(self, 'full_results_section', None)
        if section is not None:
            section.setExpanded(True)
        elif hasattr(self, "results_drawer"):
            self.results_drawer.setChecked(True)

    def _show_count_preview_dialog(
        self,
        rows: list[dict],
        *,
        review_image: QImage | None = None,
        build_review_if_missing: bool = True,
    ) -> None:
        if self.last_count_preview_dialog is not None:
            self.last_count_preview_dialog.close()
        if review_image is None and build_review_if_missing:
            review_image = self._count_review_image(rows)
        self.last_count_preview_dialog = CountPreviewDialog(rows, review_image, self)
        self.last_count_preview_dialog.show()

    def _current_black_balance_scope(self) -> str:
        if getattr(self, "measure_mode", "") == "spot_count" and hasattr(self, "count_black_balance_scope_combo"):
            return str(self.count_black_balance_scope_combo.currentData() or "all_channels")
        if hasattr(self, "roi_black_balance_scope_combo"):
            return str(self.roi_black_balance_scope_combo.currentData() or "all_channels")
        return "all_channels"

    def _count_review_image(self, rows: list[dict]) -> QImage | None:
        image = self._active_image()
        if self.panel is None or not image or not rows:
            return None
        return self._count_review_image_for_panel(self.panel, str(image.get("uid")), rows)

    @staticmethod
    def _count_review_image_for_panel(
        panel: FluorescencePanel,
        image_uid: str,
        rows: list[dict],
    ) -> QImage | None:
        image = next((item for item in panel.images() if str(item.get("uid", "")) == str(image_uid)), None)
        if image is None or not rows:
            return None
        try:
            data = panel.black_balanced_image_data(str(image_uid))
        except FileNotFoundError:
            return None
        channel_index = int(rows[0].get("channel_index", 0))
        channel = next(
            (dict(item) for item in image.get("channels", []) if int(item.get("index", 0)) == channel_index),
            {"index": channel_index, "name": f"Ch{channel_index + 1}", "pseudocolor": "#FFFFFF", "display_min": 0.0, "display_max": 1.0},
        )
        if rgb_interpretation_mode(image) == RGB_COMPOSITE_MODE:
            review = render_fluorescence_qimage(data, image, list(image.get("channels", [])), view_mode="merge")
        else:
            review = render_single_qimage(data, channel)
        review = review.convertToFormat(QImage.Format.Format_ARGB32)
        painter = QPainter(review)
        pen_width = max(2, int(round(max(review.width(), review.height()) * 0.006)))
        painter.setPen(QPen(QColor("#FFD13B"), pen_width))
        for row in rows:
            for spot in row.get("detected_spots", []):
                painter.drawRect(
                    int(spot.get("x", 0)),
                    int(spot.get("y", 0)),
                    max(1, int(spot.get("w", 1))),
                    max(1, int(spot.get("h", 1))),
                )
        sample = image.get("sample_spot", {}).get("geometry", {})
        if sample:
            painter.setPen(QPen(QColor("#d97757"), pen_width))
            painter.drawRect(
                int(float(sample.get("x", 0))),
                int(float(sample.get("y", 0))),
                max(1, int(float(sample.get("w", 1)))),
                max(1, int(float(sample.get("h", 1)))),
            )
        painter.end()
        return review

    def _request_layout_panel(self) -> None:
        self.persist_layout_edits_from_scene()
        if self.canvas.current_step != 4:
            self.canvas.set_step(4)
        self.layout_to_composer_requested.emit()

    def _preview_layout(self) -> None:
        self.persist_layout_edits_from_scene()
        self.canvas.set_step(4)

    def _select_measurement_from_results(self) -> None:
        if self.panel is None:
            return
        items = self.measurement_table.selectedItems()
        if not items:
            return
        current = self.measurement_table.currentItem()
        item = current if current is not None and current.isSelected() else items[0]
        measurement_uuid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not measurement_uuid:
            return
        measurement = self._measurement_by_uuid(measurement_uuid)
        if measurement is None:
            return
        self._activate_record_image(measurement, measurement_uuid)
        self._open_current_results()
        self.canvas.show_measurement_overlay(measurement)
        self.measurement_selected.emit(measurement)

    def _select_count_from_results(self) -> None:
        if self.panel is None:
            return
        items = self.count_table.selectedItems()
        if not items:
            return
        current = self.count_table.currentItem()
        item = current if current is not None and current.isSelected() else items[0]
        count_uuid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not count_uuid:
            return
        result = self._count_result_by_uuid(count_uuid)
        if result is None:
            return
        self._activate_record_image(result, count_uuid)
        self.canvas.show_count_overlay(result)
        self.measurement_selected.emit(result)

    def _select_ai_assisted_result_from_table(self) -> None:
        if self.panel is None:
            return
        sender = self.sender()
        table = sender if isinstance(sender, QTableWidget) else None
        if table is None:
            return
        items = table.selectedItems()
        if not items:
            return
        current = table.currentItem()
        item = current if current is not None and current.isSelected() else items[0]
        measurement_uuid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not measurement_uuid:
            return
        result = self._ai_result_by_uuid(measurement_uuid)
        if result is None:
            return
        self._activate_record_image(result, measurement_uuid)
        self.measure_mode = "ai_assisted"
        self.canvas.show_ai_assisted_overlay([result])
        self._sync_ai_overlay_buttons(has_overlay=int(result.get("ai_object_count", 0) or 0) > 0, show_overlay=True)
        self.measurement_selected.emit(result)

    def _activate_record_image(self, record: dict[str, Any], identity: str) -> None:
        if self.panel is None:
            return
        uid = record_image_uid(record)
        if not uid or uid == str(self.panel.data.get("active_image_uid", "")):
            return
        self.panel.set_active_image(uid)
        self.refresh()
        target = str(identity or "")
        for table in (self.count_table, self.ai_results_table, self.ai_summary_stats_table, self.measurement_table):
            table.blockSignals(True)
            matched = False
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == target:
                    table.selectRow(row)
                    matched = True
                    break
            if not matched:
                table.clearSelection()
            table.blockSignals(False)

    def _measurement_by_uuid(self, measurement_uuid: str) -> dict | None:
        if self.panel is None:
            return None
        for measurement in self.panel.analysis.get("measurements", []):
            if str(measurement.get("measurement_uuid")) == measurement_uuid:
                return measurement
        for image in self.panel.images():
            for measurement in image.get("measurements", []):
                if str(measurement.get("measurement_uuid")) == measurement_uuid:
                    return measurement
        return None

    def _count_result_by_uuid(self, count_uuid: str) -> dict | None:
        if self.panel is None:
            return None
        for result in self.panel.analysis.get("object_counts", []):
            if str(result.get("count_uuid")) == count_uuid:
                return result
        for image in self.panel.images():
            for result in image.get("object_counts", []):
                if str(result.get("count_uuid")) == count_uuid:
                    return result
        return None

    def _ai_result_by_uuid(self, measurement_uuid: str) -> dict | None:
        if self.panel is None:
            return None
        for record in self.panel.analysis.get("fluorescence_ai_assisted_measurements", []):
            if str(record.get("ai_measurement_uuid", "")) == measurement_uuid:
                return record
        for image in self.panel.images():
            for record in image.get("fluorescence_ai_assisted_measurements", []):
                if str(record.get("ai_measurement_uuid", "")) == measurement_uuid:
                    return record
        return None

    def _attach_captured_inputs(self, output: Any, captured: dict[str, Any] | None) -> None:
        if not captured:
            return
        records = output if isinstance(output, list) else [output]
        for record in records:
            if isinstance(record, dict):
                attach_analysis_inputs(record, captured)

    def _current_analysis_inputs_for(self, operation: str) -> dict[str, Any] | None:
        image = self._active_image() or {}
        interpretation = rgb_interpretation_mode(image)
        rgb_context = ({"rgb_interpretation": interpretation}
                       if interpretation in {RGB_COMPOSITE_MODE, FORCED_RGB_SPLIT_MODE, "auto_single_signal", "rgb_samples"} else {})
        image_uid = str(image.get("uid", "")).strip()
        calibration = self.panel.measurement_scale_bar(image_uid) if self.panel is not None and image_uid else None
        if operation in {"count", "sample-count"}:
            return build_analysis_inputs(
                method="fluoro_count",
                settings={
                    'segmentation_input':self.panel.segmentation_input_context(image_uid) if self.panel and image_uid else {'mode':'raw'},
                    **rgb_context,
                    "method": self.threshold_method_combo.currentData() or self.threshold_method_combo.currentText(),
                    "manual_value": self.threshold_value_spin.value(),
                    "min_area_px": self.count_min_area_spin.value(),
                    "algorithm": self.count_algorithm_combo.currentData(),
                    "diameter_px": self.count_diameter_spin.value(),
                    "sensitivity": self.count_sensitivity_spin.value(),
                },
                image_uid=image_uid,
                calibration=calibration if isinstance(calibration, dict) else None,
                channel_indices=self._effective_count_channels(),
            )
        if operation == "ai":
            return build_analysis_inputs(
                method="fluoro_ai",
                settings={
                    'segmentation_input':self.panel.segmentation_input_context(image_uid) if self.panel and image_uid else {'mode':'raw'},
                    **rgb_context,
                    **self.ai_analysis_settings(),
                    "roi_type": self._ai_roi_type,
                    "geometry": deepcopy(self._ai_roi_geometry),
                },
                image_uid=image_uid,
                calibration=calibration if isinstance(calibration, dict) else None,
                channel_indices=self._effective_ai_channels(),
            )
        if operation == "roi-intensity":
            return build_analysis_inputs(
                method="fluoro_roi",
                settings={
                    **rgb_context,
                    "channel": int(self.roi_channel_combo.currentData() or 0),
                    "background_method": self.roi_background_method_combo.currentData() if hasattr(self, "roi_background_method_combo") else "None",
                    "fixed_background": self.roi_fixed_background_spin.value(),
                    "geometry": {
                        "x": self.roi_x.value(),
                        "y": self.roi_y.value(),
                        "w": self.roi_w.value(),
                        "h": self.roi_h.value(),
                    },
                },
                image_uid=image_uid,
                calibration=calibration if isinstance(calibration, dict) else None,
            )
        return None

    def count_analysis_settings(self) -> dict:
        return {
            "method": str(self.threshold_method_combo.currentData() or self.threshold_method_combo.currentText() or "manual"),
            "manual_value": float(self.threshold_value_spin.value()),
            "min_area_px": int(self.count_min_area_spin.value()),
            "algorithm": self.count_algorithm_combo.currentData(),
            "diameter_px": float(self.count_diameter_spin.value()),
            "sensitivity": float(self.count_sensitivity_spin.value()),
        }

    def set_count_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        if "method" in payload:
            index = self.threshold_method_combo.findData(payload["method"])
            if index < 0:
                index = self.threshold_method_combo.findText(str(payload["method"]))
            if index >= 0:
                self.threshold_method_combo.setCurrentIndex(index)
        if "manual_value" in payload and payload["manual_value"] is not None:
            self.threshold_value_spin.setValue(float(payload["manual_value"]))
        if "min_area_px" in payload and payload["min_area_px"] is not None:
            self.count_min_area_spin.setValue(int(payload["min_area_px"]))
        if "algorithm" in payload:
            index = self.count_algorithm_combo.findData(payload["algorithm"])
            if index >= 0:
                self.count_algorithm_combo.setCurrentIndex(index)
        if "diameter_px" in payload and payload["diameter_px"] is not None:
            self.count_diameter_spin.setValue(float(payload["diameter_px"]))
        if "sensitivity" in payload and payload["sensitivity"] is not None:
            self.count_sensitivity_spin.setValue(float(payload["sensitivity"]))

    def ai_analysis_settings(self) -> dict:
        return {
            'backend': str(self.ai_backend_combo.currentData()),
            'cellpose_diameter_px': self.ai_cellpose_diameter_spin.value(),
            "detection_sensitivity": float(self.ai_sensitivity_spin.value()),
            "min_object_area_px": int(self.ai_min_area_spin.value()),
            "max_object_area_px": int(self.ai_max_area_spin.value()),
            "allow_restricted_model": bool(self.ai_allow_restricted_checkbox.isChecked()),
        }

    def set_ai_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        index = self.ai_backend_combo.findData(payload.get('backend', 'local_heuristic'))
        self.ai_backend_combo.setCurrentIndex(max(0, index))
        self.ai_cellpose_diameter_spin.setValue(float(payload.get('cellpose_diameter_px', 40.)))
        if "detection_sensitivity" in payload and payload["detection_sensitivity"] is not None:
            self.ai_sensitivity_spin.setValue(float(payload["detection_sensitivity"]))
        if "min_object_area_px" in payload and payload["min_object_area_px"] is not None:
            self.ai_min_area_spin.setValue(int(payload["min_object_area_px"]))
        if "max_object_area_px" in payload and payload["max_object_area_px"] is not None:
            self.ai_max_area_spin.setValue(int(payload["max_object_area_px"]))
        if "allow_restricted_model" in payload:
            self.ai_allow_restricted_checkbox.setChecked(bool(payload["allow_restricted_model"]))

    def _sync_ai_backend_controls(self) -> None:
        from core.image_analysis.fluorescence_ai import CELLPOSE_FLUORESCENCE_MODELS
        selected = self.ai_backend_combo.currentData()
        cellpose = selected in CELLPOSE_FLUORESCENCE_MODELS
        self.ai_form.setRowVisible(self.ai_cellpose_diameter_spin, cellpose)
        self.ai_form.setRowVisible(self.ai_membrane_preset_row, cellpose)
        self.ai_cellpose_diameter_spin.setEnabled(cellpose)
        self.ai_sensitivity_spin.setEnabled(not cellpose)
        self.ai_allow_restricted_checkbox.setVisible(not cellpose)
        hint = 'membrane_laminin_hint' if selected == 'cellpose_cyto3_laminin_final_v1' else ('cellpose_area_hint' if cellpose else 'local_area_hint')
        self.ai_status_label.setText(tr(hint))

    def _load_membrane_reference(self) -> None:
        # An explicit size selection fills the reference; changing models or
        # restoring a project must preserve the user's current parameters.
        selected = self.ai_backend_combo.currentData()
        laminin = selected == 'cellpose_cyto3_laminin_final_v1'
        self.set_ai_analysis_settings({**self.ai_analysis_settings(),'backend':selected if laminin else 'cellpose_cyto3',
            'cellpose_diameter_px':self.ai_membrane_preset_combo.currentData(),
            'min_object_area_px':60 if laminin else 80,'max_object_area_px':0})

    def roi_analysis_settings(self) -> dict:
        background = self.roi_background_method_combo.currentData() if hasattr(self, "roi_background_method_combo") else "None"
        return {
            "channel": int(self.roi_channel_combo.currentData() or 0),
            "background_method": background if background is not None else "None",
            "fixed_background": float(self.roi_fixed_background_spin.value()),
        }

    def set_roi_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        if "channel" in payload:
            index = self.roi_channel_combo.findData(int(payload["channel"]))
            if index >= 0:
                self.roi_channel_combo.setCurrentIndex(index)
        if "background_method" in payload and hasattr(self, "roi_background_method_combo"):
            index = self.roi_background_method_combo.findData(payload["background_method"])
            if index < 0:
                index = self.roi_background_method_combo.findText(str(payload["background_method"]))
            if index >= 0:
                self.roi_background_method_combo.setCurrentIndex(index)
        if "fixed_background" in payload and payload["fixed_background"] is not None:
            self.roi_fixed_background_spin.setValue(float(payload["fixed_background"]))

    def selected_result_identity(self) -> tuple[str, str] | None:
        for table, key in (
            (self.count_table, "count_uuid"),
            (self.ai_results_table, "ai_measurement_uuid"),
            (self.ai_summary_stats_table, "ai_measurement_uuid"),
            (self.measurement_table, "measurement_uuid"),
        ):
            items = table.selectedItems()
            if not items:
                continue
            uuid = str(items[0].data(Qt.ItemDataRole.UserRole) or "")
            if uuid:
                return key, uuid
        return None

    def _select_result_row(self, table, target: str) -> bool:
        matched = False
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == target:
                table.selectRow(row)
                matched = True
                break
        if not matched:
            table.clearSelection()
        table.blockSignals(False)
        return matched

    def select_result_identity(self, kind: str = "", uuid: str = "") -> bool:
        target = str(uuid or "").strip()
        if not target:
            return False
        kind = str(kind or "").strip()
        if kind in {"", "count_uuid"}:
            result = self._count_result_by_uuid(target)
            if result is not None:
                self._set_measure_mode("spot_count")
                self._activate_record_image(result, target)
                self._select_result_row(self.count_table, target)
                self.canvas.show_count_overlay(result)
                self.measurement_selected.emit(result)
                return True
        if kind in {"", "ai_measurement_uuid"}:
            result = self._ai_result_by_uuid(target)
            if result is not None:
                self._set_measure_mode("ai_assisted")
                self._activate_record_image(result, target)
                self._select_result_row(self.ai_results_table, target)
                self._select_result_row(self.ai_summary_stats_table, target)
                self.canvas.show_ai_assisted_overlay([result])
                self._sync_ai_overlay_buttons(
                    has_overlay=int(result.get("ai_object_count", 0) or 0) > 0,
                    show_overlay=True,
                )
                self.measurement_selected.emit(result)
                return True
        if kind in {"", "measurement_uuid"}:
            measurement = self._measurement_by_uuid(target)
            if measurement is not None:
                self._set_measure_mode("roi_intensity")
                self._activate_record_image(measurement, target)
                self._select_result_row(self.measurement_table, target)
                self.canvas.show_measurement_overlay(measurement)
                self.measurement_selected.emit(measurement)
                return True
        return False

    def _latest_threshold(self, image_uid: str, channel_index: int) -> float | None:
        image = next((item for item in self.panel.images() if item.get("uid") == image_uid), None) if self.panel else None
        if not image:
            return None
        for threshold in reversed(image.get("thresholds", [])):
            if int(threshold.get("channel_index", -1)) == int(channel_index):
                return float(threshold.get("value", 0.0))
        return None

    def _add_layout_text(self) -> None:
        text = self.layout_add_text_edit.text().strip() or "Text"
        image = self._active_image()
        if self.panel is not None and image:
            self.persist_layout_edits_from_scene()
            text_uuid = uuid4().hex
            rect = self.canvas.scene.itemsBoundingRect()
            record = {
                "text_uuid": text_uuid,
                "text": text,
                "x": float(rect.left() + 20 if not rect.isNull() else 20),
                "y": float(rect.top() + 20 if not rect.isNull() else 20),
                "font_family": self.layout_font_combo.currentFont().family(),
                "font_size": float(self.layout_text_size_spin.value()),
                "bold": bool(self.layout_text_bold.isChecked()),
                "italic": bool(self.layout_text_italic.isChecked()),
                "color": str(self.layout_text_color_combo.currentData() or "#FFFFFF"),
            }
            image.setdefault("layout_texts", []).append(record)
            self.panel.append_audit("add_fluorescence_layout_text", text_uuid)
            self.canvas.refresh()
            for item in self.canvas.scene.items():
                if item.data(0) == "fluoro_layout_text" and item.data(1) == text_uuid:
                    item.setSelected(True)
            return
        self.canvas.add_layout_text(text)

    def _sync_layout_text_controls(self) -> None:
        """Selection is read-only; neither defaults nor mixed values are applied."""
        if not hasattr(self,'layout_font_combo') or sip.isdeleted(self.layout_font_combo):
            return
        selected=self.canvas.selected_text_items()
        self.layout_apply_text_button.setEnabled(bool(selected))
        if not selected:
            return
        widgets=(self.layout_font_combo,self.layout_text_size_spin,self.layout_text_bold,
                 self.layout_text_italic,self.layout_text_color_combo)
        blockers=[QSignalBlocker(widget) for widget in widgets]
        try:
            styles=[item.layout_object.style for item in selected]
            families={style.font_family for style in styles}
            if len(families)==1:self.layout_font_combo.setCurrentFont(QFont(next(iter(families))))
            else:self.layout_font_combo.setCurrentIndex(-1)
            sizes={getattr(item,'display_font_size_pt',item.layout_object.style.font_size_pt) for item in selected}
            self.layout_text_size_spin.setValue(next(iter(sizes)) if len(sizes)==1 else 0)
            for key,widget in (('bold',self.layout_text_bold),('italic',self.layout_text_italic)):
                values={bool(getattr(style,key)) for style in styles}
                widget.setTristate(len(values)>1)
                widget.setCheckState(Qt.CheckState.PartiallyChecked if len(values)>1 else
                                     Qt.CheckState.Checked if True in values else Qt.CheckState.Unchecked)
            colors={QColor(style.text_color).name() for style in styles}
            if len(colors)==1:
                color=next(iter(colors))
                index=next((i for i in range(self.layout_text_color_combo.count())
                            if str(self.layout_text_color_combo.itemData(i)).lower()==color),-1)
                if index<0:
                    self.layout_text_color_combo.addItem(color.upper(),color)
                    index=self.layout_text_color_combo.count()-1
                self.layout_text_color_combo.setCurrentIndex(index)
            else:self.layout_text_color_combo.setCurrentIndex(-1)
        finally:
            del blockers

    def _apply_layout_text_style(self, changes=None) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None or sip.isdeleted(canvas):
            return
        scene = getattr(canvas, "scene", None)
        if scene is None or sip.isdeleted(scene):
            return
        if not self.canvas.selected_text_items():
            return
        if not isinstance(changes,dict):
            changes={}
            if self.layout_font_combo.currentIndex()>=0:changes['font_family']=self.layout_font_combo.currentFont().family()
            if self.layout_text_size_spin.value()>0:changes['font_size']=self.layout_text_size_spin.value()
            for key,widget in (('bold',self.layout_text_bold),('italic',self.layout_text_italic)):
                if widget.checkState()!=Qt.CheckState.PartiallyChecked:changes[key]=widget.isChecked()
            if self.layout_text_color_combo.currentData() is not None:changes['color']=self.layout_text_color_combo.currentData()
        self.canvas.apply_text_style_to_selection(**changes)
        self.persist_layout_edits_from_scene()
        self._sync_layout_text_controls()

