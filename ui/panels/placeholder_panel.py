from __future__ import annotations

from pathlib import Path
import re
import sys
from core.image_analysis.he_fiber_area import HE_FIBER_CELLPOSE_MODELS, HE_FIBER_METHOD_BOUNDARY
from ui.widgets.model_selection_hint import ModelSelectionHint

from PyQt6.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QFontComboBox,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.constants import WB_STEP_EXPORT, WB_STEP_IMPORT
from core.brightfield_statistics import (
    BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
    BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL,
    BRIGHTFIELD_SEGMENTED_REGIONS_LABEL,
    BRIGHTFIELD_STAT_METRICS,
    physical_area_text_and_excel,
)
from core.ihc_statistics import IHC_STAT_METRICS
from core.models.brightfield_panel import BRIGHTFIELD_STAIN_TYPES, normalize_brightfield_stain_type
from core.plotting.style_presets import journal_preset_names
from core.statistics import STAT_TESTS
from core.utils.i18n import bind_i18n, retranslate_bound_widgets, retranslate_combo, tr, trf
from core.utils.image_formats import COMPOSER_IMAGE_SUFFIXES, WB_IMAGE_SUFFIXES, dropped_image_paths
from ui.widgets.reliability_banner import ReliabilityBanner
from ui.widgets.measurement_excel_export import EXCEL_VALUE_ROLE, measurement_excel_button
from ui.widgets.current_page_stack import CurrentPageStackedWidget
from ui.widgets.calibration_controls import action_grid

WB_STRIP_MIME = "application/x-sciefig-wb-strip-index"


WB_COLOR_NAMES = {
    "#FFFFFF": "白色",
    "#D97757": "陶土橙",
    "#999999": "中灰",
    "#6B7FB8": "灰蓝",
    "#2E8B57": "海绿色",
    "#C7A008": "金黄",
    "#3A3A98": "靛蓝",
    "#E67E22": "橙色",
    "#2C3E50": "深蓝灰",
    "#C0392B": "暗红",
    "#333333": "深灰",
    "#111111": "黑色",
    "#1F4E79": "深蓝",
    "#555555": "灰色",
    "#7F8C8D": "冷灰",
    "#8E44AD": "紫色",
    "#0B7285": "青蓝",
    "#34495E": "蓝灰",
}


def _normalized_hex_color(value: object, fallback: str = "#111111") -> str:
    text = str(value or "").strip()
    match = re.search(r"#[0-9A-Fa-f]{6}", text)
    if match:
        return match.group(0).upper()
    return fallback


def _wb_color_label(hex_color: str) -> str:
    normalized = _normalized_hex_color(hex_color)
    return f"{WB_COLOR_NAMES.get(normalized, '自定义颜色')} ({normalized})"


def _populate_wb_color_combo(combo: QComboBox, colors: list[str]) -> None:
    combo.setEditable(True)
    for color in colors:
        normalized = _normalized_hex_color(color)
        combo.addItem(_wb_color_label(normalized), normalized)


def dropped_file_paths(mime_data: QMimeData, suffixes: set[str] | None = None) -> list[str]:
    return dropped_image_paths(mime_data, suffixes or COMPOSER_IMAGE_SUFFIXES)


MODULE_PLACEHOLDERS = {
    "qpcr": {
        "title": "qPCR data input",
        "detail": "Import Ct tables, choose a reference gene and control group, then calculate fold changes, compare groups, and export figures.",
        "canvas": "qPCR figure preview",
        "status": "No qPCR data imported | Panel Object ready",
    },
    "wb": {
        "title": "Western Blot image input",
        "detail": "Import full blots, store non-destructive crop ROIs, annotate lanes and proteins, and export layouts with source data.",
        "canvas": "Western Blot layout",
        "status": "No blot image imported | Panel Object ready",
    },
    "fluoro": {
        "title": "Fluorescence image input",
        "detail": "Import channels, measure ROI intensity, count spots or cells, review detected objects, and export calibrated results.",
        "canvas": "Fluorescence image preview",
        "status": "No fluorescence images imported | Panel Object ready",
    },
    "ihc": {
        "title": "Histology / IHC image input",
        "detail": "Import histology or IHC raster images, tune DAB thresholds, and review positive area, mean intensity, and H-score.",
        "canvas": "Histology / IHC image preview",
        "status": "No Histology / IHC images imported | Panel Object ready",
    },
    "brightfield": {
        "title": "Brightfield image input",
        "detail": "Import H&E, special stains, crystal violet, Oil Red O, and other high-contrast stained brightfield images; segment stain-positive regions, measure ROI-level area and object metrics, and compare groups.",
        "canvas": "Brightfield image preview",
        "status": "No Brightfield images imported | Panel Object ready",
    },
    "composer": {
        "title": "Composer layout input",
        "detail": "Import panels/images into the shelf, then drag or add them to the grid.",
        "canvas": "Composer figure canvas",
        "status": "Layout: empty | 0 panels placed",
    },
}

MODULE_PLACEHOLDER_I18N_KEYS = {
    "qpcr": ("module_qpcr_title", "module_qpcr_detail", "module_qpcr_canvas", "no_qpcr_data"),
    "wb": ("module_wb_title", "module_wb_detail", "module_wb_canvas", "no_blot_image"),
    "fluoro": ("module_fluoro_title", "module_fluoro_detail", "module_fluoro_canvas", "no_fluorescence_images"),
    "ihc": ("module_ihc_title", "module_ihc_detail", "module_ihc_canvas", "no_ihc_images"),
    "brightfield": ("module_brightfield_title", "module_brightfield_detail", "module_brightfield_canvas", "no_brightfield_images"),
    "composer": ("module_composer_title", "module_composer_detail", "module_composer_canvas", "layout_empty"),
}


def localized_module_placeholder(module_id: str) -> dict[str, str]:
    content = dict(MODULE_PLACEHOLDERS[module_id])
    keys = MODULE_PLACEHOLDER_I18N_KEYS.get(module_id)
    if keys is None:
        return content
    for field, key in zip(("title", "detail", "canvas", "status"), keys):
        content[field] = tr(key)
    return content

STEP6_IMPORT_LIMITATION_TEXT = (
    "Supported import: TIFF, OME-TIFF, and common raster formats (PNG, JPG/JPEG, BMP, WEBP).\n"
    "For CZI or ND2, export to OME-TIFF first in ZEN or Nikon software, then import the exported file."
)






class WBStripTable(QTableWidget):
    files_dropped = pyqtSignal(list)
    strip_action_requested = pyqtSignal(str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(0, 3, parent)
        self.retranslate()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setMaximumWidth(16777215)
        self.setMinimumWidth(0)
        self.setIconSize(QSize(120, 42))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    def retranslate(self) -> None:
        self.setHorizontalHeaderLabels([tr("status"), tr("image"), tr("protein")])

    def dragEnterEvent(self, event) -> None:
        if dropped_file_paths(event.mimeData(), WB_IMAGE_SUFFIXES):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if dropped_file_paths(event.mimeData(), WB_IMAGE_SUFFIXES):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = dropped_file_paths(event.mimeData(), WB_IMAGE_SUFFIXES)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def startDrag(self, supported_actions) -> None:
        rows = self.selectionModel().selectedRows() if self.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        mime = QMimeData()
        mime.setData(WB_STRIP_MIME, str(row).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        image_item = self.item(row, 1)
        if image_item is not None and not image_item.icon().isNull():
            drag.setPixmap(image_item.icon().pixmap(self.iconSize()))
        drag.exec(Qt.DropAction.CopyAction)

    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        if item is None:
            return
        row = item.row()
        self.selectRow(row)
        context_menu = QMenu(self)
        actions = {
            context_menu.addAction(tr("crop_recrop")): "crop",
            context_menu.addAction(tr("adjust_brightness_contrast")): "adjust",
            context_menu.addAction(tr("rotate")): "rotate",
            context_menu.addAction(tr("duplicate")): "duplicate",
            context_menu.addAction(tr("remove")): "remove",
        }
        action = context_menu.exec(event.globalPos())
        if action in actions:
            self.strip_action_requested.emit(actions[action], row)


class DataInputPanel(QFrame):
    files_dropped = pyqtSignal(list)
    qpcr_step_changed = pyqtSignal(int)
    wb_step_changed = pyqtSignal(int)
    ihc_step_changed = pyqtSignal(int)
    ihc_image_selected = pyqtSignal(str)
    ihc_measurement_selected = pyqtSignal(str)
    brightfield_step_changed = pyqtSignal(int)
    brightfield_image_selected = pyqtSignal(str)
    brightfield_measurement_selected = pyqtSignal(str)
    brightfield_ai_measurement_selected = pyqtSignal(str)
    journal_preset_changed = pyqtSignal(str)


    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DataPanel")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setFixedHeight(112)
        self._qpcr_layout_mode = False
        self.qpcr_layout_tools_panel: QWidget | None = None
        self.current_module = ""
        self.analysis: dict[str, dict] = {}

        self.title = QLabel()
        self.title.setObjectName("SectionLabel")
        self.detail = QLabel()
        self.detail.setObjectName("MutedLabel")
        self.detail.setWordWrap(True)

        self.import_button = QPushButton("Import")
        self.import_button.setObjectName("PrimaryButton")
        self.secondary_button = QPushButton("Create Mock Panel")
        self.instrument_combo = QComboBox()
        self.instrument_combo.addItems(["auto", "applied_biosystems", "roche_lightcycler"])
        self.journal_preset_label = QLabel("Journal preset")
        self.journal_preset_combo = QComboBox()
        self.journal_preset_combo.addItems(journal_preset_names())
        self.journal_preset_combo.setCurrentText("Nature")
        self.journal_preset_combo.currentTextChanged.connect(self.journal_preset_changed.emit)
        self.journal_preset_container = QWidget()
        self.journal_preset_container.setVisible(False)
        journal_preset_layout = QHBoxLayout(self.journal_preset_container)
        journal_preset_layout.setContentsMargins(0, 0, 0, 0)
        journal_preset_layout.setSpacing(6)
        journal_preset_layout.addWidget(self.journal_preset_label)
        journal_preset_layout.addWidget(self.journal_preset_combo)
        self.reference_label = QLabel("Reference")
        self.reference_combo = QComboBox()
        self.control_label = QLabel("Control")
        self.control_combo = QComboBox()
        self.qpcr_group_table = QTableWidget(0, 3)
        self.qpcr_group_table.setHorizontalHeaderLabels(["Sample", "Group", "Include"])
        self.qpcr_group_table.setMaximumHeight(120)
        self.run_analysis_button = QPushButton("Run Analysis")
        self.run_analysis_button.setObjectName("PrimaryButton")
        self.copy_summary_button = QPushButton("Copy Summary")
        self.qpcr_to_composer_button = QPushButton("Add qPCR Plots")
        self.qpcr_preview_button = QPushButton("Preview")
        self.qpcr_layout_button = QPushButton("Layout")
        self.qpcr_add_text_button = QPushButton("Add Text")
        self.qpcr_add_shape_button = QPushButton("Add Shape")
        self.qpcr_same_size_button = QPushButton("Same Size")
        self.qpcr_align_row_button = QPushButton("Align Row")
        self.qpcr_align_column_button = QPushButton("Align Column")
        self.qpcr_distribute_h_button = QPushButton("Dist H")
        self.qpcr_distribute_v_button = QPushButton("Dist V")
        self.qpcr_delete_selected_button = QPushButton("Delete")
        self.qpcr_step_buttons: list[QPushButton] = []
        self.qpcr_step_nav = QWidget()
        self.qpcr_step_nav.setObjectName("QPCRWorkflowStepNav")
        qpcr_step_layout = QGridLayout(self.qpcr_step_nav)
        qpcr_step_layout.setContentsMargins(0, 0, 0, 0)
        qpcr_step_layout.setSpacing(6)
        for index, label in enumerate(self._qpcr_step_labels()):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, step=index: self.set_qpcr_step(step))
            self.qpcr_step_buttons.append(button)
            row = index // 2
            col = (index % 2) * 2
            qpcr_step_layout.addWidget(button, row, col)
            if index < 3:
                arrow = QLabel("->")
                arrow.setObjectName("DimLabel")
                if (index + 1) // 2 == row:
                    qpcr_step_layout.addWidget(arrow, row, col + 1)
        qpcr_step_layout.setColumnStretch(0, 1)
        qpcr_step_layout.setColumnStretch(2, 1)
        self.wb_step_buttons: list[QPushButton] = []
        self.wb_step_nav = QWidget()
        self.wb_step_nav.setObjectName("WBWorkflowStepNav")
        wb_step_layout = QGridLayout(self.wb_step_nav)
        wb_step_layout.setContentsMargins(0, 0, 0, 0)
        wb_step_layout.setSpacing(6)
        for index, label in enumerate(self._wb_step_labels()):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, step=index: self.set_wb_step(step))
            self.wb_step_buttons.append(button)
            row = index // 2
            col = (index % 2) * 2
            wb_step_layout.addWidget(button, row, col)
            if index < 6:
                arrow = QLabel("->")
                arrow.setObjectName("DimLabel")
                if (index + 1) // 2 == row:
                    wb_step_layout.addWidget(arrow, row, col + 1)
        wb_step_layout.setColumnStretch(0, 1)
        wb_step_layout.setColumnStretch(2, 1)
        self.wb_step_hint = QLabel(tr("step_wb_import_hint"))
        self.wb_step_hint.setObjectName("DimLabel")
        self.wb_step_hint.setWordWrap(True)
        self.wb_x_spin = QSpinBox()
        self.wb_y_spin = QSpinBox()
        self.wb_w_spin = QSpinBox()
        self.wb_h_spin = QSpinBox()
        self.wb_brightness_spin = QSpinBox()
        self.wb_contrast_spin = QSpinBox()
        self.wb_rotation_spin = QDoubleSpinBox()
        self.wb_fine_rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.wb_lane_count_spin = QSpinBox()
        for spin in [self.wb_x_spin, self.wb_y_spin, self.wb_w_spin, self.wb_h_spin]:
            spin.setRange(0, 100000)
        for spin in [self.wb_brightness_spin, self.wb_contrast_spin]:
            spin.setRange(-100, 100)
        self.wb_rotation_spin.setRange(-180.0, 180.0)
        self.wb_rotation_spin.setSingleStep(0.1)
        self.wb_rotation_spin.setDecimals(1)
        self.wb_fine_rotation_slider.setRange(-50, 50)
        self.wb_fine_rotation_slider.setSingleStep(1)
        self.wb_fine_rotation_slider.setValue(0)
        self.wb_lane_count_spin.setRange(1, 96)
        self.wb_lane_count_spin.setValue(6)
        self.wb_protein_edit = QLineEdit()
        self.wb_protein_edit.setPlaceholderText(tr("right_protein_placeholder"))
        self.wb_lane_edit = QLineEdit()
        self.wb_lane_edit.setPlaceholderText(tr("lane_labels_placeholder"))
        self.wb_marker_edit = QLineEdit()
        self.wb_marker_edit.setPlaceholderText(tr("marker_labels_placeholder"))
        self.wb_apply_crop_button = QPushButton("Apply Crop")
        self.wb_apply_crop_button.setObjectName("PrimaryButton")
        self.wb_roi_mode_button = QPushButton("Select ROI")
        self.wb_full_blot_button = QPushButton("Full-Length Blot")
        self.wb_move_image_button = QPushButton("Move Image")
        self.wb_box_select_text_button = QPushButton("Box Select Text")
        self.wb_lane_anchor_button = QPushButton("Lane Anchors")
        self.wb_auto_lane_button = QPushButton("Auto Detect Lanes")
        self.wb_marker_anchor_button = QPushButton("Marker Anchors")
        self.wb_clear_anchors_button = QPushButton("Clear Anchors")
        self.wb_flip_h_button = QPushButton("Flip H")
        self.wb_flip_v_button = QPushButton("Flip V")
        self.wb_preview_layout_button = QPushButton("Preview Layout")
        self.wb_apply_layout_button = QPushButton("Apply")
        self.wb_apply_layout_button.setObjectName("PrimaryButton")
        self.wb_reset_button = QPushButton("Reset Original")
        self.wb_undo_button = QPushButton("Undo")
        self.wb_redo_button = QPushButton("Redo")
        self.wb_add_band_row_button = QPushButton("+ Band Row")
        self.wb_add_group_level_button = QPushButton("+ Add Group Level")
        self.wb_add_condition_row_button = QPushButton("+ Add Condition Row")
        self.wb_hierarchy_total_lanes_spin = QSpinBox()
        self.wb_hierarchy_total_lanes_spin.setRange(1, 384)
        self.wb_hierarchy_total_lanes_spin.setValue(6)
        self.wb_group_levels_edit = QPlainTextEdit()
        self.wb_group_levels_edit.setPlaceholderText(tr("group_levels_placeholder"))
        self.wb_group_levels_edit.setMaximumHeight(70)
        self.wb_condition_rows_edit = QPlainTextEdit()
        self.wb_condition_rows_edit.setPlaceholderText(tr("condition_rows_placeholder"))
        self.wb_condition_rows_edit.setMaximumHeight(58)
        self.wb_group_gap_edit = QLineEdit()
        self.wb_group_gap_edit.setPlaceholderText(tr("group_gap_placeholder"))
        self.wb_group_levels_edit.setVisible(False)
        self.wb_condition_rows_edit.setVisible(False)
        self.wb_group_gap_edit.setVisible(False)
        self.wb_hierarchy_total_lanes_spin.setVisible(False)
        self.wb_loading_control_combo = QComboBox()
        self.wb_lane_position_combo = QComboBox()
        self.wb_lane_position_combo.addItems(["top", "bottom", "none"])
        self.wb_protein_side_combo = QComboBox()
        self.wb_protein_side_combo.addItems(["right", "left", "none"])
        self.wb_marker_side_combo = QComboBox()
        self.wb_marker_side_combo.addItems(["left", "right", "none"])
        self.wb_band_table = QTableWidget(0, 3)
        self.wb_band_table.setColumnCount(4)
        self.wb_band_table.setHorizontalHeaderLabels(
            [tr("number_sign"), tr("protein"), tr("mw_kda"), tr("loading_ctrl")]
        )
        self.wb_band_table.setMinimumHeight(150)
        self.wb_band_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.wb_density_hint_label = bind_i18n(QLabel(),'wb_density_instruction')
        self.wb_density_hint_label.setObjectName("DimLabel")
        self.wb_density_hint_label.setWordWrap(True)
        self.wb_density_loading_ctrl_combo = QComboBox()
        self.wb_density_reference_lane_combo = QComboBox()
        self.wb_density_bg_method_combo = QComboBox()
        self.wb_density_bg_method_combo.addItems(["Local Edge", "Rolling Ball", "None"])
        self.wb_density_polarity_combo = QComboBox()
        self.wb_density_polarity_combo.addItems(["Auto", "Dark bands on light bg", "Bright bands on dark bg"])
        self.wb_density_measure_button = QPushButton("Measure")
        self.wb_density_measure_button.setObjectName("PrimaryButton")
        self.wb_density_result_table = QTableWidget(0, 0)
        self.wb_density_result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.wb_density_result_table.setMaximumHeight(160)
        self.wb_density_qc_label = QLabel("No measurements yet.")
        self.wb_density_qc_label.setObjectName("DimLabel")
        self.wb_density_qc_label.setWordWrap(True)
        self.wb_density_copy_button = QPushButton("Copy Full Table")
        self.wb_density_to_composer_button = QPushButton("→ Composer")
        self.wb_density_to_composer_button.setVisible(False)
        self.wb_density_chart_toggle = QToolButton()
        self.wb_density_chart_toggle.setText(tr("chart_preview"))
        self.wb_density_chart_toggle.setCheckable(True)
        self.wb_density_chart_toggle.setChecked(True)
        self.wb_density_chart_container = QWidget()
        self.wb_density_chart_layout = QVBoxLayout(self.wb_density_chart_container)
        self.wb_density_chart_layout.setContentsMargins(0, 0, 0, 0)
        self.wb_stats_group_table = QTableWidget(0, 3)
        self.wb_stats_group_table.setHorizontalHeaderLabels(["Lane", "Group", "Include"])
        self.wb_stats_group_table.setMaximumHeight(170)
        self.wb_stats_reference_group_combo = QComboBox()
        self.wb_stats_reference_group_combo.setEditable(True)
        self.wb_stats_test_combo = QComboBox()
        self.wb_stats_test_combo.addItems(STAT_TESTS)
        self.wb_stats_run_button = QPushButton("Run Statistics")
        self.wb_stats_run_button.setObjectName("PrimaryButton")
        self.wb_stats_result_table = QTableWidget(0, 0)
        self.wb_stats_result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.wb_stats_result_table.setMaximumHeight(150)
        self.wb_stats_summary_label = QLabel("No statistics yet.")
        self.wb_stats_summary_label.setObjectName("DimLabel")
        self.wb_stats_summary_label.setWordWrap(True)
        self.wb_stats_add_to_layout_button = QPushButton("Add Plot to Layout")
        self.wb_stats_chart_toggle = QToolButton()
        self.wb_stats_chart_toggle.setText(tr("chart_preview"))
        self.wb_stats_chart_toggle.setCheckable(True)
        self.wb_stats_chart_toggle.setChecked(True)
        self.wb_stats_chart_container = QWidget()
        self.wb_stats_chart_layout = QVBoxLayout(self.wb_stats_chart_container)
        self.wb_stats_chart_layout.setContentsMargins(0, 0, 0, 0)
        self.wb_stats_font_combo = QFontComboBox()
        self.wb_stats_font_combo.setCurrentFont(QFont("Arial"))
        self.wb_stats_font_size_spin = QDoubleSpinBox()
        self.wb_stats_font_size_spin.setRange(4.0, 24.0)
        self.wb_stats_font_size_spin.setSingleStep(0.5)
        self.wb_stats_font_size_spin.setValue(7.0)
        self.wb_stats_bar_fill_combo = QComboBox()
        _populate_wb_color_combo(
            self.wb_stats_bar_fill_combo,
            ["#FFFFFF", "#D97757", "#999999", "#6B7FB8", "#2E8B57", "#C7A008", "#3A3A98", "#E67E22", "#2C3E50", "#C0392B"],
        )
        self.wb_stats_bar_edge_combo = QComboBox()
        _populate_wb_color_combo(
            self.wb_stats_bar_edge_combo,
            ["#333333", "#111111", "#1F4E79", "#C0392B", "#2E8B57", "#7F8C8D", "#8E44AD", "#E67E22", "#0B7285", "#34495E"],
        )
        self.wb_stats_bar_width_spin = QDoubleSpinBox()
        self.wb_stats_bar_width_spin.setRange(0.1, 0.95)
        self.wb_stats_bar_width_spin.setSingleStep(0.05)
        self.wb_stats_bar_width_spin.setValue(0.58)
        self.wb_stats_bar_line_width_spin = QDoubleSpinBox()
        self.wb_stats_bar_line_width_spin.setRange(0.1, 5.0)
        self.wb_stats_bar_line_width_spin.setSingleStep(0.1)
        self.wb_stats_bar_line_width_spin.setValue(0.9)
        self.wb_stats_axis_line_width_spin = QDoubleSpinBox()
        self.wb_stats_axis_line_width_spin.setRange(0.1, 5.0)
        self.wb_stats_axis_line_width_spin.setSingleStep(0.1)
        self.wb_stats_axis_line_width_spin.setValue(0.6)
        self.wb_stats_plot_width_spin = QSpinBox()
        self.wb_stats_plot_width_spin.setRange(180, 1600)
        self.wb_stats_plot_width_spin.setSingleStep(20)
        self.wb_stats_plot_width_spin.setValue(520)
        self.wb_stats_plot_height_spin = QSpinBox()
        self.wb_stats_plot_height_spin.setRange(140, 1200)
        self.wb_stats_plot_height_spin.setSingleStep(20)
        self.wb_stats_plot_height_spin.setValue(360)
        self.wb_stats_point_shape_combo = QComboBox()
        self.wb_stats_point_shape_combo.addItems(["circle", "square", "diamond"])
        self.wb_stats_point_size_spin = QDoubleSpinBox()
        self.wb_stats_point_size_spin.setRange(2.0, 18.0)
        self.wb_stats_point_size_spin.setSingleStep(0.5)
        self.wb_stats_point_size_spin.setValue(5.0)
        self.wb_stats_point_color_combo = QComboBox()
        _populate_wb_color_combo(
            self.wb_stats_point_color_combo,
            ["#999999", "#D97757", "#6B7FB8", "#C0392B", "#2E8B57", "#7F8C8D", "#8E44AD", "#E67E22", "#0B7285", "#34495E"],
        )
        self.wb_stats_apply_style_button = QPushButton("Apply Statistics Style")
        self.wb_stats_apply_style_button.setVisible(False)
        self.wb_export_item_table = QTableWidget(0, 3)
        self.wb_export_item_table.setHorizontalHeaderLabels(["Export", "Item", "Format"])
        self.wb_export_item_table.setMaximumHeight(120)
        self.wb_export_to_composer_button = QPushButton("Export Selected to Composer")
        self.wb_export_to_composer_button.setObjectName("PrimaryButton")
        self.wb_strip_gap_spin = QSpinBox()
        self.wb_strip_gap_spin.setRange(0, 120)
        self.wb_strip_gap_spin.setValue(25)
        self.wb_label_offset_spin = QSpinBox()
        self.wb_label_offset_spin.setRange(0, 80)
        self.wb_label_offset_spin.setValue(15)
        self.wb_strip_table = WBStripTable()
        self.wb_strip_up_button = QPushButton("Up")
        self.wb_strip_down_button = QPushButton("Down")
        self.wb_strip_delete_button = QPushButton("Remove")
        self.wb_import_strip_button = QPushButton("+ Import")
        self.wb_import_strip_button.setObjectName("PrimaryButton")
        self.wb_text_font_combo = QFontComboBox()
        self.wb_text_font_combo.setCurrentFont(QFont("Arial"))
        self.wb_text_size_spin = QDoubleSpinBox()
        self.wb_text_size_spin.setRange(4.0, 24.0)
        self.wb_text_size_spin.setSingleStep(0.5)
        self.wb_text_size_spin.setValue(7.0)
        self.wb_text_bold_button = QPushButton("B")
        self.wb_text_bold_button.setCheckable(True)
        self.wb_text_bold_button.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.wb_text_italic_button = QPushButton("I")
        self.wb_text_italic_button.setCheckable(True)
        italic_font = QFont("Arial", 10)
        italic_font.setItalic(True)
        self.wb_text_italic_button.setFont(italic_font)
        self.wb_text_color_combo = QComboBox()
        _populate_wb_color_combo(
            self.wb_text_color_combo,
            ["#111111", "#555555", "#C0392B", "#1F4E79", "#2E8B57", "#7F8C8D", "#8E44AD", "#E67E22", "#0B7285", "#34495E"],
        )
        self.wb_text_rotation_spin = QDoubleSpinBox()
        self.wb_text_rotation_spin.setRange(-180.0, 180.0)
        self.wb_text_rotation_spin.setSingleStep(1.0)
        self.wb_text_x_spin = QDoubleSpinBox()
        self.wb_text_y_spin = QDoubleSpinBox()
        for spin in [self.wb_text_x_spin, self.wb_text_y_spin]:
            spin.setRange(-10000.0, 10000.0)
            spin.setSingleStep(1.0)
        self.wb_apply_text_type_button = QPushButton("Apply Type")
        self.wb_apply_text_type_button.setVisible(False)
        self.wb_line_width_spin = QDoubleSpinBox()
        self.wb_line_width_spin.setRange(0.1, 8.0)
        self.wb_line_width_spin.setSingleStep(0.1)
        self.wb_line_width_spin.setValue(0.75)
        self.wb_line_color_combo = QComboBox()
        _populate_wb_color_combo(
            self.wb_line_color_combo,
            ["#111111", "#555555", "#C0392B", "#1F4E79", "#2E8B57", "#7F8C8D", "#8E44AD", "#E67E22", "#0B7285", "#34495E"],
        )
        self.wb_line_x_spin = QDoubleSpinBox()
        self.wb_line_y_spin = QDoubleSpinBox()
        for spin in [self.wb_line_x_spin, self.wb_line_y_spin]:
            spin.setRange(-10000.0, 10000.0)
            spin.setSingleStep(1.0)
        self.wb_apply_line_style_button = QPushButton("Apply Line")
        self.wb_apply_line_style_button.setVisible(False)
        for widget in [
            self.wb_text_font_combo,
            self.wb_text_size_spin,
            self.wb_text_color_combo,
            self.wb_text_rotation_spin,
            self.wb_text_x_spin,
            self.wb_text_y_spin,
            self.wb_line_width_spin,
            self.wb_line_color_combo,
            self.wb_line_x_spin,
            self.wb_line_y_spin,
            self.wb_stats_font_combo,
            self.wb_stats_font_size_spin,
            self.wb_stats_plot_width_spin,
            self.wb_stats_plot_height_spin,
            self.wb_stats_bar_fill_combo,
            self.wb_stats_bar_edge_combo,
            self.wb_stats_bar_width_spin,
            self.wb_stats_bar_line_width_spin,
            self.wb_stats_axis_line_width_spin,
            self.wb_stats_point_shape_combo,
            self.wb_stats_point_size_spin,
            self.wb_stats_point_color_combo,
        ]:
            widget.setMinimumWidth(96)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.wb_export_pdf_radio = QRadioButton("PDF (vector)")
        self.wb_export_svg_radio = QRadioButton("SVG (editable)")
        self.wb_export_tiff_radio = QRadioButton("TIFF")
        self.wb_export_png_radio = QRadioButton("PNG")
        self.wb_export_pdf_radio.setChecked(True)
        self.wb_export_group = QButtonGroup(self)
        for button in [self.wb_export_pdf_radio, self.wb_export_svg_radio, self.wb_export_tiff_radio, self.wb_export_png_radio]:
            self.wb_export_group.addButton(button)
        self.wb_export_dpi_combo = QComboBox()
        self.wb_export_dpi_combo.addItems(["300", "600", "1200"])
        self.wb_export_dpi_combo.setCurrentText("1200")
        self.wb_export_source_checkbox = QCheckBox("Include source data")
        self.wb_export_button = QPushButton("Export")
        self.wb_export_button.setObjectName("PrimaryButton")
        self.wb_image_properties_label = QLabel("No strip selected")
        self.wb_image_properties_label.setWordWrap(True)
        self.wb_integrity_label = QLabel("Integrity badge unavailable")
        self.wb_integrity_label.setWordWrap(True)

        self.preview_table = QTableWidget(0, 4)
        self.preview_table.setHorizontalHeaderLabels(["Sample", "Target", "Ct", "QC"])
        self.preview_table.setMinimumHeight(320)
        self.preview_table.setMaximumHeight(16777215)
        self.preview_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.summary = QLabel("")
        self.summary.setObjectName("DimLabel")
        self.summary.setWordWrap(True)
        self.ihc_mode_combo = QComboBox()
        self.ihc_mode_combo.addItem("Foolproof", "foolproof")
        self.ihc_mode_combo.addItem("Assisted", "assisted")
        self.ihc_mode_combo.addItem("Expert", "expert")
        self.ihc_task_type_combo = QComboBox()
        self.ihc_task_type_combo.addItem("DAB IHC / H-score", "ihc_dab")
        self.ihc_task_type_combo.addItem("Hematoxylin-rich tissue (coming soon)", "hematoxylin_rich")
        self.ihc_task_type_combo.addItem("Other brightfield stain (coming soon)", "other_brightfield")
        self.ihc_quant_goal_combo = QComboBox()
        self.ihc_quant_goal_combo.addItems(list(IHC_STAT_METRICS.keys()))
        for index in range(self.ihc_quant_goal_combo.count()):
            self.ihc_quant_goal_combo.setItemData(index, self.ihc_quant_goal_combo.itemText(index))
        self.ihc_workflow_hint_label = QLabel(
            "One-click DAB analysis with automatic tissue masking, QC messages, and a locked batch threshold."
        )
        self.ihc_workflow_hint_label.setObjectName("DimLabel")
        self.ihc_workflow_hint_label.setWordWrap(True)
        self.ihc_sensitivity_combo = QComboBox()
        self.ihc_sensitivity_combo.addItem("Low", "low")
        self.ihc_sensitivity_combo.addItem("Medium", "medium")
        self.ihc_sensitivity_combo.addItem("High", "high")
        self.ihc_cleanup_combo = QComboBox()
        self.ihc_cleanup_combo.addItem("Off", "off")
        self.ihc_cleanup_combo.addItem("Low", "low")
        self.ihc_cleanup_combo.addItem("Medium", "medium")
        self.ihc_cleanup_combo.addItem("High", "high")
        self.ihc_cleanup_combo.setCurrentIndex(2)
        self.ihc_tissue_correction_checkbox = QCheckBox("Exclude tissue background automatically")
        self.ihc_tissue_correction_checkbox.setChecked(True)
        self.ihc_mark_positive_button = QRadioButton("Positive")
        self.ihc_mark_negative_button = QRadioButton("Negative")
        self.ihc_stop_sampling_button = QRadioButton("Off")
        self.ihc_stop_sampling_button.setChecked(True)
        self.ihc_sampling_group = QButtonGroup(self)
        self.ihc_sampling_group.setExclusive(True)
        self.ihc_sampling_group.addButton(self.ihc_mark_positive_button)
        self.ihc_sampling_group.addButton(self.ihc_mark_negative_button)
        self.ihc_sampling_group.addButton(self.ihc_stop_sampling_button)
        self.ihc_recalibrate_button = QPushButton("Recalibrate")
        self.ihc_revert_auto_button = QPushButton("Revert to Auto")
        self.ihc_delete_example_button = QPushButton("Delete Selected Example")
        self.ihc_overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.ihc_overlay_opacity_slider.setRange(10, 100)
        self.ihc_overlay_opacity_slider.setSingleStep(5)
        self.ihc_overlay_opacity_slider.setPageStep(10)
        self.ihc_overlay_opacity_slider.setValue(35)
        self.ihc_overlay_opacity_value_label = QLabel("35%")
        self.ihc_overlay_opacity_value_label.setObjectName("DimLabel")
        self.ihc_example_status_label = QLabel("Run Auto Analyze, then choose Positive or Negative to collect examples.")
        self.ihc_example_status_label.setObjectName("DimLabel")
        self.ihc_example_status_label.setWordWrap(True)
        self.ihc_examples_list = QListWidget()
        self.ihc_examples_list.setMaximumHeight(92)
        self.ihc_examples_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.ihc_batch_threshold_label = QLabel("Batch threshold: waiting for Auto Analyze.")
        self.ihc_batch_threshold_label.setObjectName("DimLabel")
        self.ihc_batch_threshold_label.setWordWrap(True)
        self.ihc_positive_threshold_spin = QDoubleSpinBox()
        self.ihc_positive_threshold_spin.setRange(0.0, 1.0)
        self.ihc_positive_threshold_spin.setDecimals(2)
        self.ihc_positive_threshold_spin.setSingleStep(0.05)
        self.ihc_positive_threshold_spin.setValue(0.30)
        self.ihc_weak_threshold_spin = QDoubleSpinBox()
        self.ihc_weak_threshold_spin.setRange(0.0, 1.0)
        self.ihc_weak_threshold_spin.setDecimals(2)
        self.ihc_weak_threshold_spin.setSingleStep(0.05)
        self.ihc_weak_threshold_spin.setValue(0.20)
        self.ihc_moderate_threshold_spin = QDoubleSpinBox()
        self.ihc_moderate_threshold_spin.setRange(0.0, 1.0)
        self.ihc_moderate_threshold_spin.setDecimals(2)
        self.ihc_moderate_threshold_spin.setSingleStep(0.05)
        self.ihc_moderate_threshold_spin.setValue(0.50)
        self.ihc_strong_threshold_spin = QDoubleSpinBox()
        self.ihc_strong_threshold_spin.setRange(0.0, 1.0)
        self.ihc_strong_threshold_spin.setDecimals(2)
        self.ihc_strong_threshold_spin.setSingleStep(0.05)
        self.ihc_strong_threshold_spin.setValue(0.80)
        self.ihc_min_area_spin = QSpinBox()
        self.ihc_min_area_spin.setRange(0, 100000)
        self.ihc_min_area_spin.setValue(100)
        self.ihc_run_analysis_button = QPushButton("Run IHC Analysis")
        self.ihc_run_analysis_button.setObjectName("PrimaryButton")
        self.ihc_copy_table_button = QPushButton("Copy Full Table")
        self.ihc_add_outputs_button = QPushButton("Add IHC Outputs")
        self.ihc_roi_draw_button = QPushButton("Draw Rectangle ROI")
        self.ihc_roi_draw_button.setCheckable(True)
        self.ihc_roi_draw_button.setObjectName("PrimaryButton")
        self.ihc_roi_clear_button = QPushButton("Clear ROI")
        self.ihc_roi_status_label = QLabel("ROI: Whole image")
        self.ihc_roi_status_label.setObjectName("DimLabel")
        self.ihc_roi_status_label.setWordWrap(True)
        self.ihc_threshold_status_label = QLabel("Measurement Status: awaiting analysis.")
        self.ihc_threshold_status_label.setObjectName("SectionLabel")
        self.ihc_threshold_status_label.setWordWrap(True)
        self.ihc_reliability_banner = ReliabilityBanner()
        self.ihc_qc_status_label = QLabel("QC: awaiting analysis.")
        self.ihc_qc_status_label.setObjectName("DimLabel")
        self.ihc_qc_status_label.setWordWrap(True)
        self.ihc_results_table = QTableWidget(0, 5)
        self.ihc_results_table.setHorizontalHeaderLabels(
            ["Image", "Group", "Positive Area %", "Mean DAB Intensity", "H-score"]
        )
        self.ihc_results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ihc_results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ihc_results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.ihc_results_table.setMinimumHeight(110)
        self.ihc_results_table.setMaximumHeight(150)
        self.ihc_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ihc_results_table.itemSelectionChanged.connect(self._emit_ihc_measurement_selected)
        self.ihc_summary_label = QLabel("No IHC measurements yet.")
        self.ihc_summary_label.setObjectName("DimLabel")
        self.ihc_summary_label.setWordWrap(True)
        self.ihc_freshness_status_label = QLabel(tr("result_status_not_calculated"))
        self.ihc_freshness_status_label.setObjectName("DimLabel")
        self.ihc_freshness_status_label.setWordWrap(True)
        self.ihc_stats_metric_combo = QComboBox()
        self.ihc_stats_metric_combo.addItems(list(IHC_STAT_METRICS.keys()))
        for index in range(self.ihc_stats_metric_combo.count()):
            self.ihc_stats_metric_combo.setItemData(index, self.ihc_stats_metric_combo.itemText(index))
        self.ihc_stats_group_table = QTableWidget(0, 3)
        self.ihc_stats_group_table.setHorizontalHeaderLabels(["Sample", "Group", "Include"])
        self.ihc_stats_group_table.setMaximumHeight(150)
        self.ihc_stats_group_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ihc_stats_reference_group_combo = QComboBox()
        self.ihc_stats_reference_group_combo.setEditable(True)
        self.ihc_stats_test_combo = QComboBox()
        self.ihc_stats_test_combo.addItems(STAT_TESTS)
        self.ihc_stats_run_button = QPushButton("Run Statistics")
        self.ihc_stats_run_button.setObjectName("PrimaryButton")
        self.ihc_stats_result_table = QTableWidget(0, 4)
        self.ihc_stats_result_table.setHorizontalHeaderLabels(["Group", "n", "Mean", "SEM"])
        self.ihc_stats_result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ihc_stats_result_table.setMaximumHeight(130)
        self.ihc_stats_result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ihc_copy_stats_button = QPushButton("Copy Statistics Table")
        self.ihc_export_stats_csv_button = QPushButton("Export Statistics CSV")
        self.ihc_stats_summary_label = QLabel("No statistics yet.")
        self.ihc_stats_summary_label.setObjectName("DimLabel")
        self.ihc_stats_summary_label.setWordWrap(True)
        self.ihc_stats_chart_container = QWidget()
        self.ihc_stats_chart_layout = QVBoxLayout(self.ihc_stats_chart_container)
        self.ihc_stats_chart_layout.setContentsMargins(0, 0, 0, 0)
        self.ihc_outputs_status_label = QLabel("No Composer outputs yet.")
        self.ihc_outputs_status_label.setObjectName("DimLabel")
        self.ihc_outputs_status_label.setWordWrap(True)
        self.ihc_active_image_label = QLabel("Active image: -")
        self.ihc_active_image_label.setObjectName("DimLabel")
        self.ihc_active_image_label.setWordWrap(True)
        self.ihc_active_image_combo = QComboBox()
        self.ihc_active_image_combo.setObjectName("IHCActiveImageCombo")
        self.ihc_active_image_combo.currentIndexChanged.connect(self._emit_ihc_active_image_combo_selected)
        self.ihc_context_panel = QFrame()
        self.ihc_context_panel.setObjectName("DataPanel")
        ihc_context_layout = QVBoxLayout(self.ihc_context_panel)
        ihc_context_layout.setContentsMargins(12, 12, 12, 12)
        ihc_context_layout.setSpacing(10)
        self.ihc_step_buttons: list[QPushButton] = []
        self.ihc_step_nav = QWidget()
        self.ihc_step_nav.setObjectName("IHCWorkflowStepNav")
        ihc_step_nav_layout = QGridLayout(self.ihc_step_nav)
        ihc_step_nav_layout.setContentsMargins(0, 0, 0, 0)
        ihc_step_nav_layout.setSpacing(6)
        for index, label in enumerate(["Import", "Measure", "Stats", "Layout"]):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, step=index: self.set_ihc_step(step))
            self.ihc_step_buttons.append(button)
            row = index // 2
            col = (index % 2) * 2
            ihc_step_nav_layout.addWidget(button, row, col)
            if index < 3:
                arrow = QLabel("->")
                arrow.setObjectName("DimLabel")
                if (index + 1) // 2 == row:
                    ihc_step_nav_layout.addWidget(arrow, row, col + 1)
        ihc_step_nav_layout.setColumnStretch(0, 1)
        ihc_step_nav_layout.setColumnStretch(2, 1)

        self.ihc_workflow_group = bind_i18n(QGroupBox(), "guided_workflow")
        ihc_workflow_form = QFormLayout(self.ihc_workflow_group)
        ihc_workflow_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        ihc_workflow_form.addRow(self._i18n_form_label("mode"), self.ihc_mode_combo)
        ihc_workflow_form.addRow(self._i18n_form_label("task"), self.ihc_task_type_combo)
        ihc_workflow_form.addRow(self._i18n_form_label("goal"), self.ihc_quant_goal_combo)
        ihc_workflow_form.addRow(self.ihc_workflow_hint_label)

        self.ihc_import_group = bind_i18n(QGroupBox(), "import")
        ihc_import_layout = QVBoxLayout(self.ihc_import_group)
        ihc_import_layout.setContentsMargins(12, 12, 12, 12)
        ihc_import_layout.setSpacing(6)
        self.ihc_import_hint_label = QLabel(
            "Use + Add IHC Images or drag and drop files onto the canvas.\n"
            + STEP6_IMPORT_LIMITATION_TEXT
        )
        self.ihc_import_hint_label.setWordWrap(True)
        self.ihc_import_hint_label.setObjectName("DimLabel")
        ihc_import_layout.addWidget(self.ihc_import_hint_label)

        self.ihc_measure_actions_group = bind_i18n(QGroupBox(), "foolproof_mode")
        ihc_measure_actions_layout = QVBoxLayout(self.ihc_measure_actions_group)
        ihc_measure_actions_layout.setContentsMargins(12, 12, 12, 12)
        ihc_measure_actions_layout.setSpacing(6)
        ihc_measure_action_row = QWidget()
        ihc_measure_action_row_layout = QHBoxLayout(ihc_measure_action_row)
        ihc_measure_action_row_layout.setContentsMargins(0, 0, 0, 0)
        ihc_measure_action_row_layout.setSpacing(6)
        self.ihc_try_assisted_button = QPushButton("Try Assisted Mode")
        ihc_measure_action_row_layout.addWidget(self.ihc_run_analysis_button)
        ihc_measure_action_row_layout.addWidget(self.ihc_try_assisted_button)
        ihc_measure_actions_layout.addWidget(ihc_measure_action_row)
        ihc_measure_actions_layout.addWidget(self.ihc_reliability_banner)
        ihc_measure_actions_layout.addWidget(self.ihc_threshold_status_label)

        self.ihc_guided_controls_group = bind_i18n(QGroupBox(), "assisted_mode")
        ihc_guided_controls_form = QFormLayout(self.ihc_guided_controls_group)
        ihc_guided_controls_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        ihc_sampling_buttons = QWidget()
        ihc_sampling_buttons_layout = QHBoxLayout(ihc_sampling_buttons)
        ihc_sampling_buttons_layout.setContentsMargins(0, 0, 0, 0)
        ihc_sampling_buttons_layout.setSpacing(12)
        ihc_sampling_buttons_layout.addWidget(self.ihc_mark_positive_button)
        ihc_sampling_buttons_layout.addWidget(self.ihc_mark_negative_button)
        ihc_sampling_buttons_layout.addWidget(self.ihc_stop_sampling_button)
        ihc_sampling_buttons_layout.addStretch(1)
        ihc_assisted_actions = QWidget()
        ihc_assisted_actions_layout = QHBoxLayout(ihc_assisted_actions)
        ihc_assisted_actions_layout.setContentsMargins(0, 0, 0, 0)
        ihc_assisted_actions_layout.setSpacing(6)
        ihc_assisted_actions_layout.addWidget(self.ihc_recalibrate_button)
        ihc_assisted_actions_layout.addWidget(self.ihc_revert_auto_button)
        ihc_assisted_actions_layout.addStretch(1)
        ihc_overlay_opacity_row = QWidget()
        ihc_overlay_opacity_layout = QHBoxLayout(ihc_overlay_opacity_row)
        ihc_overlay_opacity_layout.setContentsMargins(0, 0, 0, 0)
        ihc_overlay_opacity_layout.setSpacing(6)
        ihc_overlay_opacity_layout.addWidget(self.ihc_overlay_opacity_slider, 1)
        ihc_overlay_opacity_layout.addWidget(self.ihc_overlay_opacity_value_label, 0)
        ihc_guided_controls_form.addRow(self._i18n_form_label("sampling"), ihc_sampling_buttons)
        ihc_guided_controls_form.addRow(self._i18n_form_label("actions"), ihc_assisted_actions)
        ihc_guided_controls_form.addRow(self._i18n_form_label("example_list"), self.ihc_examples_list)
        ihc_guided_controls_form.addRow(self.ihc_delete_example_button)
        ihc_guided_controls_form.addRow(self.ihc_batch_threshold_label)
        ihc_guided_controls_form.addRow(self._i18n_form_label("sensitivity"), self.ihc_sensitivity_combo)
        ihc_guided_controls_form.addRow(self._i18n_form_label("clean_specks"), self.ihc_cleanup_combo)
        ihc_guided_controls_form.addRow(self._i18n_form_label("overlay_opacity"), ihc_overlay_opacity_row)
        ihc_guided_controls_form.addRow(self.ihc_tissue_correction_checkbox)
        ihc_guided_controls_form.addRow(self.ihc_example_status_label)

        self.ihc_threshold_group = bind_i18n(QGroupBox(), "expert_mode")
        ihc_threshold_form = QFormLayout(self.ihc_threshold_group)
        ihc_threshold_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        ihc_threshold_form.addRow(self._i18n_form_label("positive_threshold"), self.ihc_positive_threshold_spin)
        ihc_threshold_form.addRow(self._i18n_form_label("weak_threshold"), self.ihc_weak_threshold_spin)
        ihc_threshold_form.addRow(self._i18n_form_label("moderate_threshold"), self.ihc_moderate_threshold_spin)
        ihc_threshold_form.addRow(self._i18n_form_label("strong_threshold"), self.ihc_strong_threshold_spin)
        ihc_threshold_form.addRow(self._i18n_form_label("min_area_px"), self.ihc_min_area_spin)
        self.ihc_roi_group = bind_i18n(QGroupBox(), "roi_selection")
        ihc_roi_layout = QVBoxLayout(self.ihc_roi_group)
        ihc_roi_layout.setContentsMargins(12, 12, 12, 12)
        ihc_roi_layout.setSpacing(6)
        ihc_roi_layout.addWidget(action_grid((self.ihc_roi_draw_button,self.ihc_roi_clear_button)))
        ihc_roi_layout.addWidget(self.ihc_roi_status_label)
        self.ihc_results_group = bind_i18n(QGroupBox(), "results")
        ihc_results_layout = QVBoxLayout(self.ihc_results_group)
        ihc_results_layout.setContentsMargins(12, 12, 12, 12)
        ihc_results_layout.setSpacing(6)
        self.ihc_export_overlay_button = QPushButton("Export Overlay Image")
        ihc_results_layout.addWidget(action_grid((self.ihc_copy_table_button,self.ihc_export_overlay_button)))
        ihc_results_layout.addWidget(self.ihc_results_table)
        ihc_results_layout.addWidget(self.ihc_summary_label)
        ihc_results_layout.addWidget(self.ihc_freshness_status_label)
        ihc_results_layout.addWidget(self.ihc_qc_status_label)
        self.ihc_measurement_excel_button = measurement_excel_button(self, [("IHC", self.ihc_results_table, (2, 3, 4))], "IHC_measurements.xlsx")
        ihc_results_layout.addWidget(self.ihc_measurement_excel_button)
        self.ihc_stats_group = bind_i18n(QGroupBox(), "ihc_statistics")
        ihc_stats_layout = QVBoxLayout(self.ihc_stats_group)
        ihc_stats_layout.setSpacing(6)
        ihc_stats_form = QFormLayout()
        ihc_stats_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        ihc_stats_form.addRow(self._i18n_form_label("metric"), self.ihc_stats_metric_combo)
        ihc_stats_form.addRow(self._i18n_form_label("reference_group"), self.ihc_stats_reference_group_combo)
        ihc_stats_form.addRow(self._i18n_form_label("test"), self.ihc_stats_test_combo)
        ihc_stats_layout.addLayout(ihc_stats_form)
        ihc_stats_layout.addWidget(self.ihc_stats_group_table)
        ihc_stats_actions = QVBoxLayout()
        ihc_stats_actions.setContentsMargins(0, 0, 0, 0)
        ihc_stats_actions.setSpacing(6)
        ihc_stats_actions.addWidget(self.ihc_stats_run_button)
        self.ihc_pairing_button = bind_i18n(QPushButton(), 'pairing_settings')
        ihc_stats_actions.addWidget(self.ihc_pairing_button)
        ihc_stats_actions.addWidget(action_grid((self.ihc_copy_stats_button,self.ihc_export_stats_csv_button)))
        ihc_stats_layout.addLayout(ihc_stats_actions)
        ihc_stats_layout.addWidget(self.ihc_stats_result_table)
        ihc_stats_layout.addWidget(self.ihc_stats_summary_label)
        ihc_stats_layout.addWidget(self.ihc_stats_chart_container)
        self.ihc_layout_group = bind_i18n(QGroupBox(), "layout_composer_outputs")
        ihc_layout_group_layout = QVBoxLayout(self.ihc_layout_group)
        ihc_layout_group_layout.setContentsMargins(12, 12, 12, 12)
        ihc_layout_group_layout.setSpacing(6)
        ihc_layout_group_layout.addWidget(self.ihc_add_outputs_button)
        self.ihc_export_results_csv_button = QPushButton("Export Results CSV")
        self.ihc_export_summary_json_button = QPushButton("Export Summary JSON")
        ihc_layout_group_layout.addWidget(self.ihc_export_results_csv_button)
        ihc_layout_group_layout.addWidget(self.ihc_export_summary_json_button)
        ihc_layout_group_layout.addWidget(self.ihc_outputs_status_label)

        self.ihc_context_stack = CurrentPageStackedWidget(compact=True)
        self.ihc_context_stack.addWidget(self._wrap_context_page(self.ihc_import_group))
        self.ihc_context_stack.addWidget(
            self._wrap_context_page(
                self.ihc_measure_actions_group,
                self.ihc_guided_controls_group,
                self.ihc_threshold_group,
                self.ihc_roi_group,
                self.ihc_results_group,
            )
        )
        self.ihc_context_stack.addWidget(self._wrap_context_page(self.ihc_stats_group))
        self.ihc_context_stack.addWidget(self._wrap_context_page(self.ihc_layout_group))
        ihc_context_layout.addWidget(self.ihc_step_nav, 0)
        ihc_context_layout.addWidget(self.ihc_workflow_group, 0)
        ihc_context_layout.addWidget(self.ihc_active_image_combo, 0)
        ihc_context_layout.addWidget(self.ihc_context_stack, 1)
        self.ihc_mode_combo.currentIndexChanged.connect(self._sync_ihc_guided_mode)
        self.ihc_task_type_combo.currentIndexChanged.connect(self._sync_ihc_guided_mode)
        self.ihc_quant_goal_combo.currentIndexChanged.connect(self._sync_ihc_guided_mode)
        self._sync_ihc_guided_mode()
        self.set_ihc_step(0, emit_signal=False)

        self.brightfield_mode_combo = QComboBox()
        self.brightfield_mode_combo.addItem("Foolproof", "foolproof")
        self.brightfield_mode_combo.addItem("Assisted", "assisted")
        self.brightfield_mode_combo.addItem("Expert", "expert")
        self.brightfield_workflow_hint_label = QLabel(
            "H&E images use the guided bridge by default; switch to Expert for raw HSV/LAB/grayscale measurement."
        )
        self.brightfield_workflow_hint_label.setObjectName("DimLabel")
        self.brightfield_workflow_hint_label.setWordWrap(True)
        self.brightfield_sensitivity_combo = QComboBox()
        self.brightfield_sensitivity_combo.addItem("Low", "low")
        self.brightfield_sensitivity_combo.addItem("Medium", "medium")
        self.brightfield_sensitivity_combo.addItem("High", "high")
        self.brightfield_foolproof_sensitivity_combo = QComboBox()
        self.brightfield_foolproof_sensitivity_combo.addItem("-2 Conservative", -2)
        self.brightfield_foolproof_sensitivity_combo.addItem("-1 Slightly conservative", -1)
        self.brightfield_foolproof_sensitivity_combo.addItem("0 Default", 0)
        self.brightfield_foolproof_sensitivity_combo.addItem("+1 Slightly sensitive", 1)
        self.brightfield_foolproof_sensitivity_combo.addItem("+2 Sensitive", 2)
        self.brightfield_assisted_sensitivity_combo = QComboBox()
        self.brightfield_assisted_sensitivity_combo.addItem("-2 Conservative", -2)
        self.brightfield_assisted_sensitivity_combo.addItem("-1 Slightly conservative", -1)
        self.brightfield_assisted_sensitivity_combo.addItem("0 Default", 0)
        self.brightfield_assisted_sensitivity_combo.addItem("+1 Slightly sensitive", 1)
        self.brightfield_assisted_sensitivity_combo.addItem("+2 Sensitive", 2)
        self.brightfield_assisted_small_region_filter_combo = QComboBox()
        self.brightfield_assisted_small_region_filter_combo.addItem("-2 Keep more small regions", -2)
        self.brightfield_assisted_small_region_filter_combo.addItem("-1 Keep slightly more small regions", -1)
        self.brightfield_assisted_small_region_filter_combo.addItem("0 Default", 0)
        self.brightfield_assisted_small_region_filter_combo.addItem("+1 Filter small regions more", 1)
        self.brightfield_assisted_small_region_filter_combo.addItem("+2 Filter small regions strongly", 2)
        self.brightfield_assisted_small_region_filter_combo.setToolTip(
            "Adjusts the minimum retained segmented-region size after thresholding. "
            "This does not change the detection threshold."
        )
        self.brightfield_cleanup_combo = QComboBox()
        self.brightfield_cleanup_combo.addItem("Off", "off")
        self.brightfield_cleanup_combo.addItem("Low", "low")
        self.brightfield_cleanup_combo.addItem("Medium", "medium")
        self.brightfield_cleanup_combo.addItem("High", "high")
        self.brightfield_segmentation_mode_combo = QComboBox()
        self.brightfield_segmentation_mode_combo.addItem("HSV Color Range", "hsv")
        self.brightfield_segmentation_mode_combo.addItem("LAB Color Range", "lab")
        self.brightfield_segmentation_mode_combo.addItem("Grayscale Threshold", "grayscale")
        self.brightfield_h_min_spin = QDoubleSpinBox()
        self.brightfield_h_max_spin = QDoubleSpinBox()
        self.brightfield_s_min_spin = QDoubleSpinBox()
        self.brightfield_s_max_spin = QDoubleSpinBox()
        self.brightfield_v_min_spin = QDoubleSpinBox()
        self.brightfield_v_max_spin = QDoubleSpinBox()
        for spin, value in [
            (self.brightfield_h_min_spin, 0.00),
            (self.brightfield_h_max_spin, 0.20),
            (self.brightfield_s_min_spin, 0.15),
            (self.brightfield_s_max_spin, 1.00),
            (self.brightfield_v_min_spin, 0.00),
            (self.brightfield_v_max_spin, 0.95),
        ]:
            spin.setRange(0.0, 1.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setValue(value)
        self.brightfield_l_min_spin = QDoubleSpinBox()
        self.brightfield_l_max_spin = QDoubleSpinBox()
        self.brightfield_a_min_spin = QDoubleSpinBox()
        self.brightfield_a_max_spin = QDoubleSpinBox()
        self.brightfield_b_min_spin = QDoubleSpinBox()
        self.brightfield_b_max_spin = QDoubleSpinBox()
        for spin, minimum, maximum, value in [
            (self.brightfield_l_min_spin, 0.0, 100.0, 0.0),
            (self.brightfield_l_max_spin, 0.0, 100.0, 95.0),
            (self.brightfield_a_min_spin, -128.0, 127.0, -10.0),
            (self.brightfield_a_max_spin, -128.0, 127.0, 60.0),
            (self.brightfield_b_min_spin, -128.0, 127.0, 0.0),
            (self.brightfield_b_max_spin, -128.0, 127.0, 90.0),
        ]:
            spin.setRange(minimum, maximum)
            spin.setDecimals(1)
            spin.setSingleStep(1.0)
            spin.setValue(value)
        self.brightfield_gray_threshold_spin = QDoubleSpinBox()
        self.brightfield_gray_threshold_spin.setRange(0.0, 1.0)
        self.brightfield_gray_threshold_spin.setDecimals(2)
        self.brightfield_gray_threshold_spin.setSingleStep(0.05)
        self.brightfield_gray_threshold_spin.setValue(0.55)
        self.brightfield_gray_invert_checkbox = QCheckBox("Invert grayscale threshold")
        self.brightfield_fill_holes_checkbox = QCheckBox("Fill holes")
        self.brightfield_fill_holes_checkbox.setChecked(True)
        self.brightfield_cleanup_min_area_spin = QSpinBox()
        self.brightfield_cleanup_min_area_spin.setRange(0, 100000)
        self.brightfield_cleanup_min_area_spin.setValue(0)
        self.brightfield_min_object_area_spin = QSpinBox()
        self.brightfield_min_object_area_spin.setRange(0, 100000)
        self.brightfield_min_object_area_spin.setValue(10)
        self.brightfield_max_object_area_spin = QSpinBox()
        self.brightfield_max_object_area_spin.setRange(0, 1000000)
        self.brightfield_max_object_area_spin.setValue(0)
        self.brightfield_preview_mask_button = QPushButton("Preview Mask Overlay")
        self.brightfield_preview_mask_button.setObjectName("PrimaryButton")
        self.brightfield_preview_status_label = QLabel("Mask preview uses the current segmentation mode and active ROI.")
        self.brightfield_preview_status_label.setObjectName("DimLabel")
        self.brightfield_preview_status_label.setWordWrap(True)
        self.brightfield_run_analysis_button = QPushButton("Run Brightfield Measurement")
        self.brightfield_run_analysis_button.setObjectName("PrimaryButton")
        self.brightfield_reliability_banner = ReliabilityBanner()
        self.brightfield_copy_table_button = QPushButton("Copy Full Table")
        self.brightfield_add_outputs_button = QPushButton("Add Brightfield Outputs")
        self.brightfield_he_fiber_calibrate_button = QPushButton("Calibrate H&E Fiber Area")
        self.brightfield_he_fiber_calibrate_button.setObjectName("PrimaryButton")
        self.brightfield_he_fiber_calibration_status_label = QLabel("H&E Fiber Area: not calibrated.")
        self.brightfield_he_fiber_calibration_status_label.setObjectName("DimLabel")
        self.brightfield_he_fiber_calibration_status_label.setWordWrap(True)
        self.brightfield_he_fiber_scale_status_label = QLabel("Scale: import an H&E image to check pixel size.")
        self.brightfield_he_fiber_scale_status_label.setObjectName("DimLabel")
        self.brightfield_he_fiber_scale_status_label.setWordWrap(True)
        self.brightfield_he_fiber_scale_length_spin = QDoubleSpinBox()
        self.brightfield_he_fiber_scale_length_spin.setRange(0.1, 100000.0)
        self.brightfield_he_fiber_scale_length_spin.setValue(50.0)
        self.brightfield_he_fiber_scale_um_per_px_spin = QDoubleSpinBox()
        self.brightfield_he_fiber_scale_um_per_px_spin.setRange(0.0, 100000.0)
        self.brightfield_he_fiber_scale_um_per_px_spin.setDecimals(6)
        self.brightfield_he_fiber_scale_um_per_px_spin.setSpecialValueText(tr("calibration_from_image"))
        self.brightfield_he_fiber_draw_scale_line_button = QPushButton("Draw Scale")
        bind_i18n(self.brightfield_he_fiber_draw_scale_line_button, "draw_he_scale_tooltip", "toolTip")
        self.brightfield_he_fiber_apply_scale_button = QPushButton("Apply Scale")
        bind_i18n(self.brightfield_he_fiber_apply_scale_button, "apply_he_scale_tooltip", "toolTip")
        self.brightfield_he_fiber_refresh_results_button = QPushButton("Refresh")
        bind_i18n(self.brightfield_he_fiber_refresh_results_button, "refresh_he_scale_tooltip", "toolTip")
        self.brightfield_he_fiber_reset_scale_button = QPushButton("Reset")
        bind_i18n(self.brightfield_he_fiber_reset_scale_button, "reset_he_scale_tooltip", "toolTip")
        self.brightfield_he_fiber_method_combo = QComboBox()
        self.brightfield_he_fiber_method_combo.addItem(tr("he_fiber_method_original"), "original")
        self.brightfield_he_fiber_method_combo.addItem(tr("he_fiber_method_anti_oversegmentation"), "anti_oversegmentation")
        self.brightfield_he_fiber_method_combo.addItem(tr('he_fiber_method_cellpose_cyto3'), 'cellpose_cyto3')
        self.brightfield_he_fiber_method_combo.addItem(tr('he_fiber_method_'+HE_FIBER_METHOD_BOUNDARY), HE_FIBER_METHOD_BOUNDARY)
        self.brightfield_he_fiber_method_combo.setMinimumWidth(0)
        self.brightfield_he_fiber_method_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.brightfield_he_fiber_method_combo.setMinimumContentsLength(12)
        self.brightfield_he_fiber_model_hint=ModelSelectionHint(self.brightfield_he_fiber_method_combo,HE_FIBER_CELLPOSE_MODELS)
        self.brightfield_he_fiber_cellpose_diameter_spin = QDoubleSpinBox()
        self.brightfield_he_fiber_cellpose_diameter_spin.setRange(1., 2000.)
        self.brightfield_he_fiber_cellpose_diameter_spin.setValue(90.)
        self.brightfield_he_fiber_cellpose_diameter_spin.setSuffix(' px')
        bind_i18n(self.brightfield_he_fiber_cellpose_diameter_spin, 'cellpose_diameter_hint', 'toolTip')
        self.brightfield_he_fiber_method_combo.setCurrentIndex(0)
        bind_i18n(self.brightfield_he_fiber_method_combo, "he_fiber_method_tooltip", "toolTip")
        self.brightfield_he_fiber_foreground_combo = QComboBox()
        for preset in (35, 50, 65):
            self.brightfield_he_fiber_foreground_combo.addItem(str(preset), preset)
        self.brightfield_he_fiber_foreground_combo.setCurrentIndex(1)
        bind_i18n(self.brightfield_he_fiber_foreground_combo, "he_fiber_foreground_tooltip", "toolTip")
        self.brightfield_he_fiber_splitting_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightfield_he_fiber_splitting_slider.setRange(0, 100)
        self.brightfield_he_fiber_splitting_slider.setValue(25)
        self.brightfield_he_fiber_splitting_slider.setSingleStep(1)
        self.brightfield_he_fiber_splitting_slider.setPageStep(5)
        bind_i18n(self.brightfield_he_fiber_splitting_slider, "he_fiber_splitting_tooltip", "toolTip")
        self.brightfield_he_fiber_splitting_value_label = QLabel("25")
        self.brightfield_he_fiber_splitting_slider.valueChanged.connect(self._update_he_fiber_splitting_label)
        self.brightfield_he_fiber_method_combo.currentIndexChanged.connect(self._sync_he_fiber_tuning_enabled)
        self.brightfield_bright_round_cell_threshold_spin = QDoubleSpinBox()
        self.brightfield_bright_round_cell_threshold_spin.setRange(0.0, 255.0)
        self.brightfield_bright_round_cell_threshold_spin.setDecimals(1)
        self.brightfield_bright_round_cell_threshold_spin.setSingleStep(1.0)
        self.brightfield_bright_round_cell_threshold_spin.setValue(18.0)
        self.brightfield_bright_round_cell_elongated_contrast_threshold_spin = QDoubleSpinBox()
        self.brightfield_bright_round_cell_elongated_contrast_threshold_spin.setRange(0.0, 255.0)
        self.brightfield_bright_round_cell_elongated_contrast_threshold_spin.setDecimals(1)
        self.brightfield_bright_round_cell_elongated_contrast_threshold_spin.setSingleStep(1.0)
        self.brightfield_bright_round_cell_elongated_contrast_threshold_spin.setValue(16.0)
        self.brightfield_bright_round_cell_min_brightness_spin = QDoubleSpinBox()
        self.brightfield_bright_round_cell_min_brightness_spin.setRange(0.0, 255.0)
        self.brightfield_bright_round_cell_min_brightness_spin.setDecimals(1)
        self.brightfield_bright_round_cell_min_brightness_spin.setSingleStep(5.0)
        self.brightfield_bright_round_cell_min_brightness_spin.setValue(165.0)
        self.brightfield_bright_round_cell_min_circularity_spin = QDoubleSpinBox()
        self.brightfield_bright_round_cell_min_circularity_spin.setRange(0.05, 1.0)
        self.brightfield_bright_round_cell_min_circularity_spin.setDecimals(2)
        self.brightfield_bright_round_cell_min_circularity_spin.setSingleStep(0.05)
        self.brightfield_bright_round_cell_min_circularity_spin.setValue(0.55)
        self.brightfield_bright_round_cell_min_diameter_um_spin = QDoubleSpinBox()
        self.brightfield_bright_round_cell_min_diameter_um_spin.setRange(0.1, 100000.0)
        self.brightfield_bright_round_cell_min_diameter_um_spin.setDecimals(2)
        self.brightfield_bright_round_cell_min_diameter_um_spin.setSingleStep(1.0)
        self.brightfield_bright_round_cell_min_diameter_um_spin.setValue(4.0)
        self.brightfield_bright_round_cell_max_diameter_um_spin = QDoubleSpinBox()
        self.brightfield_bright_round_cell_max_diameter_um_spin.setRange(0.1, 100000.0)
        self.brightfield_bright_round_cell_max_diameter_um_spin.setDecimals(2)
        self.brightfield_bright_round_cell_max_diameter_um_spin.setSingleStep(1.0)
        self.brightfield_bright_round_cell_max_diameter_um_spin.setValue(30.0)
        self.brightfield_cellpose_strategy_combo = QComboBox()
        self._brightfield_cellpose_strategy='general'
        for strategy in ('traditional','general','local_contrast'):
            self.brightfield_cellpose_strategy_combo.addItem(tr('cellpose_cell_strategy_'+strategy),strategy)
        self.brightfield_cellpose_strategy_combo.setMinimumWidth(0)
        self.brightfield_cellpose_strategy_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.brightfield_cellpose_strategy_combo.setMinimumContentsLength(1)
        bind_i18n(self.brightfield_cellpose_strategy_combo,'cellpose_cell_strategy_hint','toolTip')
        self.brightfield_cellpose_reference_combo = QComboBox()
        for diameter in (30,45):
            self.brightfield_cellpose_reference_combo.addItem(f'{diameter} px',diameter)
        self.brightfield_cellpose_reference_row = QWidget()
        cell_reference_layout = QHBoxLayout(self.brightfield_cellpose_reference_row)
        cell_reference_layout.setContentsMargins(0,0,0,0)
        cell_reference_layout.addWidget(self.brightfield_cellpose_reference_combo,1)
        self.brightfield_cellpose_reference_row.setEnabled(False)
        self.brightfield_cellpose_diameter_spin = QDoubleSpinBox()
        self.brightfield_cellpose_diameter_spin.setRange(2,2000)
        self.brightfield_cellpose_diameter_spin.setValue(30)
        self.brightfield_cellpose_diameter_spin.setSuffix(' px')
        self.brightfield_cellpose_reference_combo.activated.connect(self._load_cellpose_cell_reference)
        self.brightfield_cellpose_model_hint=ModelSelectionHint(self.brightfield_cellpose_strategy_combo,
            dict(general='cyto3',local_contrast='cyto3'))
        bind_i18n(self.brightfield_cellpose_diameter_spin,'cellpose_diameter_hint','toolTip')
        self.brightfield_cellpose_min_size_spin = QSpinBox()
        self.brightfield_cellpose_min_size_spin.setRange(1,100000)
        self.brightfield_cellpose_min_size_spin.setValue(60)
        self.brightfield_cellpose_diameter_spin.setEnabled(False)
        self.brightfield_cellpose_min_size_spin.setEnabled(False)
        self.brightfield_cellpose_strategy_combo.currentIndexChanged.connect(self._sync_cellpose_controls)
        self.brightfield_bright_round_cell_measure_button = QPushButton("Measure Bright Round Cells")
        self.brightfield_bright_round_cell_measure_button.setObjectName("PrimaryButton")
        bind_i18n(self.brightfield_bright_round_cell_measure_button, "measure_bright_round_cells")
        self.brightfield_bright_round_cell_summary_label = QLabel(tr("bright_round_cell_no_results"))
        self.brightfield_bright_round_cell_summary_label.setObjectName("DimLabel")
        self.brightfield_bright_round_cell_summary_label.setWordWrap(True)
        self.brightfield_cell_freshness_status_label = QLabel(tr("result_status_not_calculated"))
        self.brightfield_cell_freshness_status_label.setObjectName("DimLabel")
        self.brightfield_cell_freshness_status_label.setWordWrap(True)
        self.brightfield_bright_round_cell_results_table = QTableWidget(0, 12)
        self.brightfield_bright_round_cell_results_table.setHorizontalHeaderLabels(
            [
                tr("bright_round_cell_id"),
                tr("bright_round_cell_shape"),
                tr("bright_round_cell_equivalent_diameter"),
                tr("bright_round_cell_short_diameter"),
                tr("bright_round_cell_feret_diameter"),
                tr("bright_round_cell_circularity"),
                tr("x"),
                tr("y"),
                tr("cell_measurement_status"),
                tr("image"),
                tr('cell_max_transverse_width'),
                tr('cell_contour_area'),
            ]
        )
        self.brightfield_bright_round_cell_results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.brightfield_bright_round_cell_results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.brightfield_bright_round_cell_results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.brightfield_bright_round_cell_results_table.setMinimumHeight(120)
        self.brightfield_bright_round_cell_results_table.setMaximumHeight(220)
        self.brightfield_bright_round_cell_results_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.brightfield_bright_round_cell_results_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.brightfield_bright_round_cell_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.brightfield_bright_round_cell_results_table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((35, 50, 110, 100, 110, 65, 65, 65, 150, 180, 145, 135)):
            self.brightfield_bright_round_cell_results_table.setColumnWidth(index, width)
        cell_header=self.brightfield_bright_round_cell_results_table.horizontalHeader()
        cell_header.moveSection(cell_header.visualIndex(10),2)
        cell_header.moveSection(cell_header.visualIndex(11),3)
        self.brightfield_ai_object_count_button = QPushButton("Run H&E Fiber Area")
        self.brightfield_ai_object_count_button.setObjectName("PrimaryButton")
        bind_i18n(self.brightfield_ai_object_count_button, "run_he_fiber_area_tooltip", "toolTip")
        self.brightfield_ai_results_table = QTableWidget(0, 9)
        self.brightfield_ai_results_table.setHorizontalHeaderLabels(
            [
                "Image",
                "Group",
                "Stain",
                BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
                "Mean Fiber Area px",
                "Median Fiber Area px",
                "Analyzer",
                "Include",
                "Warnings / QC",
            ]
        )
        self.brightfield_ai_results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.brightfield_ai_results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.brightfield_ai_results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.brightfield_ai_results_table.setMinimumHeight(96)
        self.brightfield_ai_results_table.setMaximumHeight(150)
        self.brightfield_ai_results_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.brightfield_ai_results_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.brightfield_ai_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.brightfield_ai_results_table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((150, 100, 80, 120, 150, 160, 170, 80, 130)):
            self.brightfield_ai_results_table.setColumnWidth(index, width)
        self.brightfield_ai_results_table.itemSelectionChanged.connect(self._emit_brightfield_ai_measurement_selected)
        self.brightfield_copy_ai_results_button = QPushButton("Copy H&E Fiber Area Results")
        self.brightfield_ai_fiber_area_table = QTableWidget(0, 5)
        self.brightfield_ai_fiber_area_table.setHorizontalHeaderLabels(["Image", "Group", "Fiber", "Area px²", "Area um²"])
        self.brightfield_ai_fiber_area_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.brightfield_ai_fiber_area_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.brightfield_ai_fiber_area_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.brightfield_ai_fiber_area_table.setMinimumHeight(120)
        self.brightfield_ai_fiber_area_table.setMaximumHeight(200)
        self.brightfield_ai_fiber_area_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.brightfield_ai_summary_label = QLabel("No H&E Fiber Area results yet.")
        self.brightfield_ai_summary_label.setObjectName("DimLabel")
        self.brightfield_ai_summary_label.setWordWrap(True)
        self.brightfield_ai_freshness_status_label = QLabel(tr("result_status_not_calculated"))
        self.brightfield_ai_freshness_status_label.setObjectName("DimLabel")
        self.brightfield_ai_freshness_status_label.setWordWrap(True)
        self.brightfield_roi_draw_button = QPushButton("Draw Rectangle ROI")
        self.brightfield_roi_draw_button.setCheckable(True)
        self.brightfield_roi_draw_button.setObjectName("PrimaryButton")
        self.brightfield_roi_clear_button = QPushButton("Clear ROI")
        self.brightfield_roi_status_label = QLabel("ROI: Whole image")
        self.brightfield_roi_status_label.setObjectName("DimLabel")
        self.brightfield_roi_status_label.setWordWrap(True)
        self.brightfield_measure_status_label = QLabel("Measurement Status: awaiting measurement.")
        self.brightfield_measure_status_label.setObjectName("SectionLabel")
        self.brightfield_measure_status_label.setWordWrap(True)
        self.brightfield_batch_threshold_status_label = QLabel(
            "Batch threshold: run or select an H&E LAB-mode measurement to Lock."
        )
        self.brightfield_batch_threshold_status_label.setObjectName("DimLabel")
        self.brightfield_batch_threshold_status_label.setWordWrap(True)
        self.brightfield_lock_batch_threshold_button = QPushButton("Lock Current")
        self.brightfield_unlock_batch_threshold_button = QPushButton("Unlock")
        self.brightfield_lock_batch_threshold_button.setEnabled(False)
        self.brightfield_unlock_batch_threshold_button.setEnabled(False)
        self.brightfield_qc_status_label = QLabel("QC: awaiting measurement.")
        self.brightfield_qc_status_label.setObjectName("DimLabel")
        self.brightfield_qc_status_label.setWordWrap(True)
        self.brightfield_results_table = QTableWidget(0, 7)
        self.brightfield_results_table.setHorizontalHeaderLabels(
            ["Image", "Group", "Stain", "Mode", "Area Fraction %", BRIGHTFIELD_SEGMENTED_REGIONS_LABEL, "Mean Object Area"]
        )
        self.brightfield_results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.brightfield_results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.brightfield_results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.brightfield_results_table.setMinimumHeight(120)
        self.brightfield_results_table.setMaximumHeight(170)
        self.brightfield_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.brightfield_results_table.itemSelectionChanged.connect(self._emit_brightfield_measurement_selected)
        self.brightfield_summary_label = QLabel("No Brightfield measurements yet.")
        self.brightfield_summary_label.setObjectName("DimLabel")
        self.brightfield_summary_label.setWordWrap(True)
        self.brightfield_freshness_status_label = QLabel(tr("result_status_not_calculated"))
        self.brightfield_freshness_status_label.setObjectName("DimLabel")
        self.brightfield_freshness_status_label.setWordWrap(True)
        self.brightfield_stats_metric_combo = QComboBox()
        self.brightfield_stats_metric_combo.addItems(list(BRIGHTFIELD_STAT_METRICS.keys()))
        for index in range(self.brightfield_stats_metric_combo.count()):
            self.brightfield_stats_metric_combo.setItemData(index, self.brightfield_stats_metric_combo.itemText(index))
        self.brightfield_stats_group_table = QTableWidget(0, 3)
        self.brightfield_stats_group_table.setHorizontalHeaderLabels(["Sample", "Group", "Include"])
        self.brightfield_stats_group_table.setMaximumHeight(150)
        self.brightfield_stats_group_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.brightfield_stats_reference_group_combo = QComboBox()
        self.brightfield_stats_reference_group_combo.setEditable(True)
        self.brightfield_stats_test_combo = QComboBox()
        self.brightfield_stats_test_combo.addItems(STAT_TESTS)
        self.brightfield_stats_plot_source_combo = QComboBox()
        self.brightfield_stats_plot_source_combo.addItem("Traditional Brightfield", "traditional")
        self.brightfield_stats_plot_source_combo.addItem("H&E Fiber Area", "ai_object_count")
        self.brightfield_stats_chart_mode_combo = QComboBox()
        self.brightfield_stats_chart_mode_combo.addItem("Summary", "summary")
        self.brightfield_stats_chart_mode_combo.addItem("Fiber Area Frequency Bar", "frequency_bar")
        self.brightfield_stats_chart_mode_combo.addItem("Fiber Area Frequency Line", "frequency_line")
        self.brightfield_stats_frequency_mode_combo = QComboBox()
        self.brightfield_stats_frequency_mode_combo.addItem("Absolute Count", "absolute")
        self.brightfield_stats_frequency_mode_combo.addItem("Percent of Fibers", "percent")
        self.brightfield_stats_run_button = QPushButton("Run Statistics")
        self.brightfield_stats_run_button.setObjectName("PrimaryButton")
        self.brightfield_stats_result_table = QTableWidget(0, 4)
        self.brightfield_stats_result_table.setHorizontalHeaderLabels(["Group", "n", "Mean", "SEM"])
        self.brightfield_stats_result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.brightfield_stats_result_table.setMaximumHeight(130)
        self.brightfield_stats_result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.brightfield_copy_stats_button = QPushButton("Copy Traditional Stats")
        self.brightfield_export_stats_csv_button = QPushButton("Export Traditional Stats CSV")
        self.brightfield_copy_ai_stats_button = QPushButton("Copy H&E Fiber Stats")
        self.brightfield_export_ai_stats_csv_button = QPushButton("Export H&E Fiber Stats CSV")
        self.brightfield_ai_stats_result_table = QTableWidget(0, 6)
        self.brightfield_ai_stats_result_table.setHorizontalHeaderLabels(
            ["Group", "n", "Mean Count", "Median Count", "Mean Area px", "Median Area px"]
        )
        self.brightfield_ai_stats_result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.brightfield_ai_stats_result_table.setMaximumHeight(130)
        self.brightfield_ai_stats_result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.brightfield_ai_stats_summary_label = QLabel("No H&E Fiber Area statistics yet.")
        self.brightfield_ai_stats_summary_label.setObjectName("DimLabel")
        self.brightfield_ai_stats_summary_label.setWordWrap(True)
        self.brightfield_stats_summary_label = QLabel("No statistics yet.")
        self.brightfield_stats_summary_label.setObjectName("DimLabel")
        self.brightfield_stats_summary_label.setWordWrap(True)
        self.brightfield_stats_chart_container = QWidget()
        self.brightfield_stats_chart_layout = QVBoxLayout(self.brightfield_stats_chart_container)
        self.brightfield_stats_chart_layout.setContentsMargins(0, 0, 0, 0)
        self.brightfield_export_chart_png_button = QPushButton("Export Current Chart PNG")
        self.brightfield_outputs_status_label = QLabel("No Composer outputs yet.")
        self.brightfield_outputs_status_label.setObjectName("DimLabel")
        self.brightfield_outputs_status_label.setWordWrap(True)
        self.brightfield_active_image_label = QLabel("Active image: -")
        self.brightfield_active_image_label.setObjectName("DimLabel")
        self.brightfield_active_image_label.setWordWrap(True)
        self.brightfield_active_image_combo = QComboBox()
        self.brightfield_active_image_combo.setObjectName("BrightfieldActiveImageCombo")
        self.brightfield_active_image_combo.currentIndexChanged.connect(self._emit_brightfield_active_image_combo_selected)
        self.brightfield_group_edit = QLineEdit()
        self.brightfield_stain_type_combo = QComboBox()
        self.brightfield_stain_type_combo.addItems(BRIGHTFIELD_STAIN_TYPES)
        self.brightfield_stain_type_combo.setMinimumHeight(30)
        self.brightfield_stain_type_combo.setMinimumContentsLength(6)
        self.brightfield_stain_type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.brightfield_apply_metadata_button = QPushButton("Apply Image Metadata")
        self.brightfield_context_panel = QFrame()
        self.brightfield_context_panel.setObjectName("DataPanel")
        brightfield_context_layout = QVBoxLayout(self.brightfield_context_panel)
        brightfield_context_layout.setContentsMargins(12, 12, 12, 12)
        brightfield_context_layout.setSpacing(10)
        self.brightfield_step_buttons: list[QPushButton] = []
        self.brightfield_step_nav = QWidget()
        self.brightfield_step_nav.setObjectName("BrightfieldWorkflowStepNav")
        brightfield_step_nav_layout = QGridLayout(self.brightfield_step_nav)
        brightfield_step_nav_layout.setContentsMargins(0, 0, 0, 0)
        brightfield_step_nav_layout.setSpacing(6)
        for index, label in enumerate(["Import", "Preprocess/Segment", "Measure", "Stats", "Layout"]):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, step=index: self.set_brightfield_step(step))
            self.brightfield_step_buttons.append(button)
            row = index // 2
            col = (index % 2) * 2
            brightfield_step_nav_layout.addWidget(button, row, col)
            if index < 4:
                arrow = QLabel("->")
                arrow.setObjectName("DimLabel")
                if (index + 1) // 2 == row:
                    brightfield_step_nav_layout.addWidget(arrow, row, col + 1)
        brightfield_step_nav_layout.setColumnStretch(0, 1)
        brightfield_step_nav_layout.setColumnStretch(2, 1)

        self.brightfield_workflow_group = bind_i18n(QGroupBox(), "guided_workflow")
        brightfield_workflow_form = QFormLayout(self.brightfield_workflow_group)
        brightfield_workflow_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        brightfield_workflow_form.addRow(self._i18n_form_label("mode"), self.brightfield_mode_combo)
        brightfield_workflow_form.addRow(self.brightfield_workflow_hint_label)

        self.brightfield_import_group = bind_i18n(QGroupBox(), "import")
        brightfield_import_layout = QVBoxLayout(self.brightfield_import_group)
        brightfield_import_layout.setContentsMargins(12, 12, 12, 12)
        brightfield_import_layout.setSpacing(6)
        self.brightfield_import_hint_label = QLabel(
            "Use + Add Brightfield Images or drag and drop files onto the canvas.\n"
            + STEP6_IMPORT_LIMITATION_TEXT
        )
        self.brightfield_import_hint_label.setWordWrap(True)
        self.brightfield_import_hint_label.setObjectName("DimLabel")
        brightfield_import_layout.addWidget(self.brightfield_import_hint_label)
        brightfield_metadata_group = bind_i18n(QGroupBox(), "per_image_metadata")
        brightfield_metadata_form = QFormLayout(brightfield_metadata_group)
        brightfield_metadata_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        brightfield_metadata_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        brightfield_metadata_form.addRow(self._i18n_form_label("group"), self.brightfield_group_edit)
        brightfield_metadata_form.addRow(self._i18n_form_label("stain_type"), self.brightfield_stain_type_combo)
        brightfield_metadata_form.addRow(self.brightfield_apply_metadata_button)
        brightfield_import_layout.addWidget(brightfield_metadata_group)

        self.brightfield_segment_group = bind_i18n(QGroupBox(), "preprocess_segment")
        brightfield_segment_layout = QVBoxLayout(self.brightfield_segment_group)
        brightfield_segment_layout.setContentsMargins(12, 12, 12, 12)
        brightfield_segment_layout.setSpacing(6)
        brightfield_segment_form = QFormLayout()
        brightfield_segment_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        brightfield_segment_form.addRow(self._i18n_form_label("mode"), self.brightfield_segmentation_mode_combo)
        brightfield_segment_layout.addLayout(brightfield_segment_form)
        self.brightfield_segment_params_stack = CurrentPageStackedWidget(compact=True)

        brightfield_hsv_page = QWidget()
        brightfield_hsv_form = QFormLayout(brightfield_hsv_page)
        brightfield_hsv_form.addRow(self._i18n_form_label("hue_min"), self.brightfield_h_min_spin)
        brightfield_hsv_form.addRow(self._i18n_form_label("hue_max"), self.brightfield_h_max_spin)
        brightfield_hsv_form.addRow(self._i18n_form_label("sat_min"), self.brightfield_s_min_spin)
        brightfield_hsv_form.addRow(self._i18n_form_label("sat_max"), self.brightfield_s_max_spin)
        brightfield_hsv_form.addRow(self._i18n_form_label("val_min"), self.brightfield_v_min_spin)
        brightfield_hsv_form.addRow(self._i18n_form_label("val_max"), self.brightfield_v_max_spin)
        self.brightfield_segment_params_stack.addWidget(brightfield_hsv_page)

        brightfield_lab_page = QWidget()
        brightfield_lab_form = QFormLayout(brightfield_lab_page)
        self.brightfield_lab_status_label = QLabel("LAB segmentation available via skimage.color.rgb2lab.")
        self.brightfield_lab_status_label.setObjectName("DimLabel")
        self.brightfield_lab_status_label.setWordWrap(True)
        brightfield_lab_form.addRow(self.brightfield_lab_status_label)
        brightfield_lab_form.addRow(self._i18n_form_label("l_min"), self.brightfield_l_min_spin)
        brightfield_lab_form.addRow(self._i18n_form_label("l_max"), self.brightfield_l_max_spin)
        brightfield_lab_form.addRow(self._i18n_form_label("a_min"), self.brightfield_a_min_spin)
        brightfield_lab_form.addRow(self._i18n_form_label("a_max"), self.brightfield_a_max_spin)
        brightfield_lab_form.addRow(self._i18n_form_label("b_min"), self.brightfield_b_min_spin)
        brightfield_lab_form.addRow(self._i18n_form_label("b_max"), self.brightfield_b_max_spin)
        self.brightfield_segment_params_stack.addWidget(brightfield_lab_page)

        brightfield_gray_page = QWidget()
        brightfield_gray_form = QFormLayout(brightfield_gray_page)
        brightfield_gray_form.addRow(self._i18n_form_label("threshold"), self.brightfield_gray_threshold_spin)
        brightfield_gray_form.addRow(self.brightfield_gray_invert_checkbox)
        self.brightfield_segment_params_stack.addWidget(brightfield_gray_page)
        brightfield_segment_layout.addWidget(self.brightfield_segment_params_stack)

        brightfield_cleanup_group = bind_i18n(QGroupBox(), "mask_cleanup_objects")
        brightfield_cleanup_form = QFormLayout(brightfield_cleanup_group)
        brightfield_cleanup_form.addRow(self.brightfield_fill_holes_checkbox)
        brightfield_cleanup_form.addRow(self._i18n_form_label("cleanup_min_area_px"), self.brightfield_cleanup_min_area_spin)
        brightfield_cleanup_form.addRow(self._i18n_form_label("min_object_area_px"), self.brightfield_min_object_area_spin)
        brightfield_cleanup_form.addRow(self._i18n_form_label("max_object_area_off"), self.brightfield_max_object_area_spin)
        brightfield_segment_layout.addWidget(brightfield_cleanup_group)
        brightfield_segment_layout.addWidget(self.brightfield_preview_mask_button)
        brightfield_segment_layout.addWidget(self.brightfield_preview_status_label)

        self.brightfield_measure_group = bind_i18n(QGroupBox(), "foolproof_mode")
        brightfield_measure_layout = QVBoxLayout(self.brightfield_measure_group)
        brightfield_measure_layout.setContentsMargins(12, 12, 12, 12)
        brightfield_measure_layout.setSpacing(6)
        brightfield_measure_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        brightfield_roi_group = bind_i18n(QGroupBox(), "roi_selection")
        brightfield_roi_layout = QVBoxLayout(brightfield_roi_group)
        brightfield_roi_layout.setContentsMargins(12, 12, 12, 12)
        brightfield_roi_layout.setSpacing(6)
        brightfield_roi_actions = QHBoxLayout()
        brightfield_roi_actions.setContentsMargins(0, 0, 0, 0)
        brightfield_roi_actions.setSpacing(6)
        brightfield_roi_actions.addWidget(self.brightfield_roi_draw_button)
        brightfield_roi_actions.addWidget(self.brightfield_roi_clear_button)
        brightfield_roi_layout.addLayout(brightfield_roi_actions)
        brightfield_roi_layout.addWidget(self.brightfield_roi_status_label)
        brightfield_measure_layout.addWidget(brightfield_roi_group)
        self.brightfield_foolproof_controls_group = bind_i18n(QGroupBox(), "foolproof_detection_sensitivity")
        brightfield_foolproof_controls_form = QFormLayout(self.brightfield_foolproof_controls_group)
        brightfield_foolproof_controls_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        brightfield_foolproof_controls_form.addRow(self._i18n_form_label("detection_sensitivity"), self.brightfield_foolproof_sensitivity_combo)
        brightfield_measure_layout.addWidget(self.brightfield_foolproof_controls_group)
        self.brightfield_guided_controls_group = bind_i18n(QGroupBox(), "assisted_mode")
        brightfield_guided_controls_form = QFormLayout(self.brightfield_guided_controls_group)
        brightfield_guided_controls_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        brightfield_guided_controls_form.addRow(self._i18n_form_label("detection_sensitivity"), self.brightfield_assisted_sensitivity_combo)
        brightfield_guided_controls_form.addRow(self._i18n_form_label("small_region_filtering"), self.brightfield_assisted_small_region_filter_combo)
        brightfield_guided_controls_form.addRow(self._i18n_form_label("sensitivity"), self.brightfield_sensitivity_combo)
        brightfield_guided_controls_form.addRow(self._i18n_form_label("clean_specks"), self.brightfield_cleanup_combo)
        brightfield_measure_layout.addWidget(self.brightfield_guided_controls_group)
        brightfield_batch_actions = QWidget()
        brightfield_batch_actions_layout = QHBoxLayout(brightfield_batch_actions)
        brightfield_batch_actions_layout.setContentsMargins(0, 0, 0, 0)
        brightfield_batch_actions_layout.setSpacing(6)
        brightfield_batch_actions_layout.addWidget(self.brightfield_lock_batch_threshold_button)
        brightfield_batch_actions_layout.addWidget(self.brightfield_unlock_batch_threshold_button)
        brightfield_batch_actions_layout.addStretch(1)
        brightfield_measure_layout.addWidget(self.brightfield_batch_threshold_status_label)
        brightfield_measure_layout.addWidget(brightfield_batch_actions)
        brightfield_measure_layout.addWidget(self.brightfield_run_analysis_button)
        self.brightfield_he_fiber_tuning_group = bind_i18n(QGroupBox(), "he_fiber_segmentation_controls")
        he_fiber_tuning_form = QFormLayout(self.brightfield_he_fiber_tuning_group)
        self.he_fiber_tuning_form = he_fiber_tuning_form
        he_fiber_tuning_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        he_fiber_tuning_form.addRow(self._i18n_form_label("he_fiber_segmentation_method"), self.brightfield_he_fiber_method_combo)
        he_fiber_tuning_form.addRow(self._i18n_form_label('cellpose_object_diameter'), self.brightfield_he_fiber_cellpose_diameter_spin)
        he_fiber_tuning_form.addRow(self.brightfield_he_fiber_model_hint)
        he_fiber_tuning_form.addRow(self._i18n_form_label("he_fiber_foreground_sensitivity"), self.brightfield_he_fiber_foreground_combo)
        splitting_row = QWidget()
        splitting_layout = QHBoxLayout(splitting_row)
        splitting_layout.setContentsMargins(0, 0, 0, 0)
        splitting_layout.setSpacing(6)
        splitting_layout.addWidget(self.brightfield_he_fiber_splitting_slider, 1)
        splitting_layout.addWidget(self.brightfield_he_fiber_splitting_value_label)
        he_fiber_tuning_form.addRow(self._i18n_form_label("he_fiber_splitting_sensitivity"), splitting_row)
        self._sync_he_fiber_tuning_enabled()
        brightfield_measure_layout.addWidget(self.brightfield_he_fiber_calibrate_button)
        brightfield_measure_layout.addWidget(self.brightfield_he_fiber_calibration_status_label)
        from ui.widgets.calibration_controls import CalibrationControls
        self.brightfield_calibration_controls = CalibrationControls(
            length_spin=self.brightfield_he_fiber_scale_length_spin,
            x_spin=self.brightfield_he_fiber_scale_um_per_px_spin,
            status=self.brightfield_he_fiber_scale_status_label,
            actions=(
            self.brightfield_he_fiber_draw_scale_line_button,
            self.brightfield_he_fiber_apply_scale_button,
            self.brightfield_he_fiber_refresh_results_button,
            self.brightfield_he_fiber_reset_scale_button,
        ))
        brightfield_measure_layout.addWidget(self.brightfield_calibration_controls)
        self.brightfield_bright_round_cell_group = bind_i18n(QGroupBox(), "bright_round_cell_diameter", "title")
        bright_round_cell_layout = QVBoxLayout(self.brightfield_bright_round_cell_group)
        bright_round_cell_layout.setContentsMargins(12, 12, 12, 12)
        bright_round_cell_layout.setSpacing(6)
        bright_round_cell_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        bright_round_cell_form = QFormLayout()
        bright_round_cell_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        bright_round_cell_form.addRow(self._i18n_form_label('segmentation_backend'),self.brightfield_cellpose_strategy_combo)
        bright_round_cell_form.addRow(self.brightfield_cellpose_model_hint)
        bright_round_cell_form.addRow(self._i18n_form_label('cellpose_cell_reference'),self.brightfield_cellpose_reference_row)
        bright_round_cell_form.addRow(self._i18n_form_label('cellpose_object_diameter'),self.brightfield_cellpose_diameter_spin)
        bright_round_cell_form.addRow(self._i18n_form_label('min_area_px'),self.brightfield_cellpose_min_size_spin)
        bright_round_cell_form.addRow(
            self._i18n_form_label("bright_round_cell_threshold"),
            self.brightfield_bright_round_cell_threshold_spin,
        )
        bright_round_cell_form.addRow(
            self._i18n_form_label("bright_round_cell_elongated_contrast_threshold"),
            self.brightfield_bright_round_cell_elongated_contrast_threshold_spin,
        )
        bright_round_cell_form.addRow(
            self._i18n_form_label("bright_round_cell_min_brightness"),
            self.brightfield_bright_round_cell_min_brightness_spin,
        )
        bright_round_cell_form.addRow(
            self._i18n_form_label("bright_round_cell_min_circularity"),
            self.brightfield_bright_round_cell_min_circularity_spin,
        )
        bright_round_cell_form.addRow(
            self._i18n_form_label("bright_round_cell_min_diameter_um"),
            self.brightfield_bright_round_cell_min_diameter_um_spin,
        )
        bright_round_cell_form.addRow(
            self._i18n_form_label("bright_round_cell_max_diameter_um"),
            self.brightfield_bright_round_cell_max_diameter_um_spin,
        )
        bright_round_cell_layout.addLayout(bright_round_cell_form)
        for row in range(bright_round_cell_form.rowCount()):
            label_item=bright_round_cell_form.itemAt(row,QFormLayout.ItemRole.LabelRole)
            if label_item is None:continue
            label=label_item.widget()
            label.setWordWrap(True)
            label.setMinimumWidth(120)
            label.setMaximumWidth(140)
        bright_round_cell_layout.addWidget(self.brightfield_bright_round_cell_measure_button)
        bright_round_cell_layout.addWidget(self.brightfield_bright_round_cell_summary_label)
        bright_round_cell_layout.addWidget(self.brightfield_cell_freshness_status_label)
        bright_round_cell_layout.addWidget(self.brightfield_bright_round_cell_results_table)
        brightfield_measure_layout.addWidget(self.brightfield_bright_round_cell_group)
        brightfield_measure_layout.addWidget(self.brightfield_ai_object_count_button)
        brightfield_measure_layout.addWidget(self.brightfield_reliability_banner)
        brightfield_measure_layout.addWidget(self.brightfield_measure_status_label)
        brightfield_measure_layout.addWidget(self.brightfield_copy_table_button)
        brightfield_measure_layout.addWidget(self.brightfield_results_table)
        brightfield_measure_layout.addWidget(self.brightfield_summary_label)
        brightfield_measure_layout.addWidget(self.brightfield_freshness_status_label)
        fiber_results_heading = bind_i18n(QLabel(), "run_he_fiber_area")
        brightfield_measure_layout.addWidget(fiber_results_heading)
        brightfield_measure_layout.addWidget(self.brightfield_copy_ai_results_button)
        brightfield_measure_layout.addWidget(self.brightfield_ai_results_table)
        brightfield_measure_layout.addWidget(self.brightfield_ai_summary_label)
        brightfield_measure_layout.addWidget(self.brightfield_ai_freshness_status_label)
        fiber_detail_heading = bind_i18n(QLabel(), "per_fiber_area")
        brightfield_measure_layout.addWidget(fiber_detail_heading)
        brightfield_measure_layout.addWidget(self.brightfield_ai_fiber_area_table)
        brightfield_measure_layout.addWidget(self.brightfield_qc_status_label)

        # Shared ROI/calibration above, one analysis panel at a time, one export below.
        from ui.widgets.measurement_task_selector import MeasurementTaskSelector
        self.brightfield_measure_type_combo = MeasurementTaskSelector()
        for key, value in (("cell_size_mode", "cells"), ("area_measurement_mode", "area"), ("fiber_measurement_mode", "fiber")):
            self.brightfield_measure_type_combo.addItem(tr(key), value)
        brightfield_measure_layout.insertWidget(0,self.brightfield_measure_type_combo)
        self.brightfield_measure_mode_stack = CurrentPageStackedWidget(compact=True)
        self.brightfield_measure_mode_stack.addWidget(self.brightfield_bright_round_cell_group)
        for widgets in (
            (self.brightfield_foolproof_controls_group, self.brightfield_guided_controls_group,
             self.brightfield_batch_threshold_status_label, brightfield_batch_actions,
             self.brightfield_run_analysis_button, self.brightfield_reliability_banner,
              self.brightfield_measure_status_label, self.brightfield_copy_table_button,
              self.brightfield_results_table, self.brightfield_summary_label, self.brightfield_freshness_status_label),
            (self.brightfield_he_fiber_calibrate_button, self.brightfield_he_fiber_calibration_status_label,
              self.brightfield_he_fiber_tuning_group, self.brightfield_ai_object_count_button, fiber_results_heading, self.brightfield_copy_ai_results_button,
              self.brightfield_ai_results_table, self.brightfield_ai_summary_label, self.brightfield_ai_freshness_status_label, fiber_detail_heading,
             self.brightfield_ai_fiber_area_table, self.brightfield_qc_status_label),
        ):
            mode_page = QWidget()
            mode_layout = QVBoxLayout(mode_page)
            mode_layout.setContentsMargins(0, 0, 0, 0)
            mode_layout.setSpacing(6)
            for widget in widgets:
                mode_layout.addWidget(widget)
            self.brightfield_measure_mode_stack.addWidget(mode_page)
        brightfield_measure_layout.addWidget(self.brightfield_measure_mode_stack)
        self.brightfield_measure_type_combo.currentIndexChanged.connect(self._sync_brightfield_measure_type)
        self.brightfield_measurement_excel_button = measurement_excel_button(self, [
            ("Cell sizes", self.brightfield_bright_round_cell_results_table, (0, 2, 3, 4, 5, 6, 7, 10, 11)),
            ("Area", self.brightfield_results_table, (4, 5, 6)),
            ("Fiber summary", self.brightfield_ai_results_table, (3, 4, 5)),
            ("Fiber areas", self.brightfield_ai_fiber_area_table, (2, 3, 4)),
        ], "Brightfield_measurements.xlsx")
        brightfield_measure_layout.addWidget(self.brightfield_measurement_excel_button)

        self.brightfield_stats_group = bind_i18n(QGroupBox(), "brightfield_statistics")
        brightfield_stats_layout = QVBoxLayout(self.brightfield_stats_group)
        brightfield_stats_layout.setSpacing(6)
        brightfield_stats_form = QFormLayout()
        brightfield_stats_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        brightfield_stats_form.addRow(self._i18n_form_label("metric"), self.brightfield_stats_metric_combo)
        brightfield_stats_form.addRow(self._i18n_form_label("reference_group"), self.brightfield_stats_reference_group_combo)
        brightfield_stats_form.addRow(self._i18n_form_label("test"), self.brightfield_stats_test_combo)
        brightfield_stats_form.addRow(self._i18n_form_label("plot_source"), self.brightfield_stats_plot_source_combo)
        brightfield_stats_form.addRow(self._i18n_form_label("chart"), self.brightfield_stats_chart_mode_combo)
        brightfield_stats_form.addRow(self._i18n_form_label("frequency_scale"), self.brightfield_stats_frequency_mode_combo)
        brightfield_stats_layout.addLayout(brightfield_stats_form)
        brightfield_stats_layout.addWidget(self.brightfield_stats_group_table)
        brightfield_stats_actions = QVBoxLayout()
        brightfield_stats_actions.setContentsMargins(0, 0, 0, 0)
        brightfield_stats_actions.setSpacing(6)
        brightfield_stats_actions.addWidget(self.brightfield_stats_run_button)
        self.brightfield_pairing_button = bind_i18n(QPushButton(), 'pairing_settings')
        brightfield_stats_actions.addWidget(self.brightfield_pairing_button)
        brightfield_stats_actions.addWidget(action_grid((self.brightfield_copy_stats_button,self.brightfield_export_stats_csv_button,
                                                        self.brightfield_copy_ai_stats_button,self.brightfield_export_ai_stats_csv_button)))
        brightfield_stats_layout.addLayout(brightfield_stats_actions)
        brightfield_stats_layout.addWidget(self.brightfield_stats_result_table)
        brightfield_stats_layout.addWidget(self.brightfield_stats_summary_label)
        brightfield_stats_layout.addWidget(QLabel("H&E Fiber Area statistics"))
        brightfield_stats_layout.addWidget(self.brightfield_ai_stats_result_table)
        brightfield_stats_layout.addWidget(self.brightfield_ai_stats_summary_label)
        brightfield_stats_layout.addWidget(self.brightfield_export_chart_png_button)
        brightfield_stats_layout.addWidget(self.brightfield_stats_chart_container)

        self.brightfield_layout_group = bind_i18n(QGroupBox(), "layout_composer_outputs")
        brightfield_layout_group_layout = QVBoxLayout(self.brightfield_layout_group)
        brightfield_layout_group_layout.setContentsMargins(12, 12, 12, 12)
        brightfield_layout_group_layout.setSpacing(6)
        brightfield_layout_group_layout.addWidget(self.brightfield_add_outputs_button)
        brightfield_layout_group_layout.addWidget(self.brightfield_outputs_status_label)

        self.brightfield_context_stack = CurrentPageStackedWidget(compact=True)
        self.brightfield_context_stack.addWidget(self._wrap_context_page(self.brightfield_import_group))
        self.brightfield_context_stack.addWidget(self._wrap_context_page(self.brightfield_segment_group))
        self.brightfield_context_stack.addWidget(self._wrap_context_page(self.brightfield_measure_group))
        self.brightfield_context_stack.addWidget(self._wrap_context_page(self.brightfield_stats_group))
        self.brightfield_context_stack.addWidget(self._wrap_context_page(self.brightfield_layout_group))
        brightfield_context_layout.addWidget(self.brightfield_step_nav, 0)
        brightfield_context_layout.addWidget(self.brightfield_workflow_group, 0)
        brightfield_context_layout.addWidget(self.brightfield_active_image_combo, 0)
        brightfield_context_layout.addWidget(self.brightfield_context_stack, 1)
        self.brightfield_stats_plot_source_combo.currentIndexChanged.connect(self._sync_brightfield_stats_plot_source_ui)
        self.brightfield_mode_combo.currentIndexChanged.connect(self._sync_brightfield_guided_mode)
        self.brightfield_stain_type_combo.currentTextChanged.connect(self._sync_brightfield_guided_mode)
        self.brightfield_segmentation_mode_combo.currentIndexChanged.connect(self._sync_brightfield_segmentation_mode)
        self._sync_brightfield_segmentation_mode()
        self._sync_brightfield_stats_plot_source_ui()
        self._sync_brightfield_guided_mode()
        self.set_brightfield_step(0, emit_signal=False)
        self.qpcr_context_panel = self._build_qpcr_context_panel()

        top_row = QHBoxLayout()
        self.top_row_layout = top_row
        top_row.addWidget(self.title)
        top_row.addStretch(1)

        settings_row = QHBoxLayout()

        qpcr_layout_tools_row = QHBoxLayout()
        for widget in [
            self.qpcr_preview_button,
            self.qpcr_layout_button,
            self.qpcr_add_text_button,
            self.qpcr_add_shape_button,
            self.qpcr_same_size_button,
            self.qpcr_align_row_button,
            self.qpcr_align_column_button,
            self.qpcr_distribute_h_button,
            self.qpcr_distribute_v_button,
            self.qpcr_delete_selected_button,
        ]:
            qpcr_layout_tools_row.addWidget(widget)
        qpcr_layout_tools_row.addStretch(1)


        wb_roi_row = QHBoxLayout()
        self.wb_roi_label = QLabel("ROI")
        self.wb_x_label = QLabel("X")
        self.wb_y_label = QLabel("Y")
        self.wb_w_label = QLabel("W")
        self.wb_h_label = QLabel("H")
        self.wb_brightness_label = QLabel("Brightness")
        self.wb_contrast_label = QLabel("Contrast")
        self.wb_rotation_label = QLabel("Rotation")
        wb_roi_row.addWidget(self.wb_roi_label)
        wb_roi_row.addWidget(self.wb_x_label)
        wb_roi_row.addWidget(self.wb_x_spin)
        wb_roi_row.addWidget(self.wb_y_label)
        wb_roi_row.addWidget(self.wb_y_spin)
        wb_roi_row.addWidget(self.wb_w_label)
        wb_roi_row.addWidget(self.wb_w_spin)
        wb_roi_row.addWidget(self.wb_h_label)
        wb_roi_row.addWidget(self.wb_h_spin)
        wb_roi_row.addWidget(self.wb_brightness_label)
        wb_roi_row.addWidget(self.wb_brightness_spin)
        wb_roi_row.addWidget(self.wb_contrast_label)
        wb_roi_row.addWidget(self.wb_contrast_spin)
        wb_roi_row.addWidget(self.wb_rotation_label)
        wb_roi_row.addWidget(self.wb_rotation_spin)
        wb_roi_row.addWidget(self.wb_fine_rotation_slider)

        wb_label_row = QHBoxLayout()
        self.wb_lane_count_label = QLabel("Lane Count")
        wb_label_row.addWidget(self.wb_lane_count_label)
        wb_label_row.addWidget(self.wb_lane_count_spin)
        wb_label_row.addWidget(self.wb_protein_edit)
        wb_label_row.addWidget(self.wb_lane_edit)
        wb_label_row.addWidget(self.wb_marker_edit)
        wb_label_row.addWidget(self.wb_apply_crop_button)
        wb_label_row.addWidget(self.wb_roi_mode_button)
        wb_label_row.addWidget(self.wb_full_blot_button)
        wb_label_row.addWidget(self.wb_move_image_button)
        wb_label_row.addWidget(self.wb_lane_anchor_button)
        wb_label_row.addWidget(self.wb_auto_lane_button)
        wb_label_row.addWidget(self.wb_marker_anchor_button)
        wb_label_row.addWidget(self.wb_clear_anchors_button)
        wb_label_row.addWidget(self.wb_flip_h_button)
        wb_label_row.addWidget(self.wb_flip_v_button)

        wb_config_row = QHBoxLayout()
        self.wb_loading_control_label = QLabel("Loading Control")
        self.wb_lane_position_label = QLabel("Lane")
        self.wb_protein_side_label = QLabel("Protein")
        self.wb_marker_side_label = QLabel("Marker")
        wb_config_row.addWidget(self.wb_loading_control_label)
        wb_config_row.addWidget(self.wb_loading_control_combo)
        wb_config_row.addWidget(self.wb_lane_position_label)
        wb_config_row.addWidget(self.wb_lane_position_combo)
        wb_config_row.addWidget(self.wb_protein_side_label)
        wb_config_row.addWidget(self.wb_protein_side_combo)
        wb_config_row.addWidget(self.wb_marker_side_label)
        wb_config_row.addWidget(self.wb_marker_side_combo)
        wb_config_row.addWidget(self.wb_add_band_row_button)
        wb_config_row.addWidget(self.wb_add_group_level_button)
        wb_config_row.addWidget(self.wb_add_condition_row_button)
        wb_config_row.addWidget(self.wb_preview_layout_button)
        wb_config_row.addWidget(self.wb_apply_layout_button)
        wb_config_row.addWidget(self.wb_undo_button)
        wb_config_row.addWidget(self.wb_redo_button)
        wb_config_row.addWidget(self.wb_reset_button)

        wb_strip_row = QHBoxLayout()
        self.wb_strip_label = QLabel("Band Strips")
        wb_strip_row.addWidget(self.wb_strip_label)
        wb_strip_row.addWidget(self.wb_strip_up_button)
        wb_strip_row.addWidget(self.wb_strip_down_button)
        wb_strip_row.addWidget(self.wb_strip_delete_button)

        wb_hierarchy_row = QHBoxLayout()
        self.wb_total_lanes_label = QLabel("Total lanes")
        self.wb_group_gap_label = QLabel("Gap after lane")
        self.wb_total_lanes_label.setVisible(False)
        self.wb_group_gap_label.setVisible(False)
        wb_hierarchy_row.addWidget(self.wb_total_lanes_label)
        wb_hierarchy_row.addWidget(self.wb_hierarchy_total_lanes_spin)
        wb_hierarchy_row.addWidget(self.wb_group_gap_label)
        wb_hierarchy_row.addWidget(self.wb_group_gap_edit)

        wb_text_row = QHBoxLayout()
        self.wb_text_style_label = QLabel("Text")
        self.wb_text_rotation_label = QLabel("Rot")
        self.wb_text_x_label = QLabel("X")
        self.wb_text_y_label = QLabel("Y")
        wb_text_row.addWidget(self.wb_text_style_label)
        wb_text_row.addWidget(self.wb_text_font_combo)
        wb_text_row.addWidget(self.wb_text_size_spin)
        wb_text_row.addWidget(self.wb_text_bold_button)
        wb_text_row.addWidget(self.wb_text_italic_button)
        wb_text_row.addWidget(self.wb_text_color_combo)
        wb_text_row.addWidget(self.wb_text_rotation_label)
        wb_text_row.addWidget(self.wb_text_rotation_spin)
        wb_text_row.addWidget(self.wb_text_x_label)
        wb_text_row.addWidget(self.wb_text_x_spin)
        wb_text_row.addWidget(self.wb_text_y_label)
        wb_text_row.addWidget(self.wb_text_y_spin)
        wb_text_row.addWidget(self.wb_apply_text_type_button)

        self.wb_staging_panel = QFrame()
        self.wb_staging_panel.setObjectName("DataPanel")
        self.wb_staging_panel.setMinimumWidth(0)
        self.wb_staging_panel.setMaximumWidth(16777215)
        self.wb_staging_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        wb_staging_layout = QVBoxLayout(self.wb_staging_panel)
        wb_staging_layout.setContentsMargins(10, 10, 10, 10)
        wb_staging_layout.setSpacing(8)
        self.wb_strip_label = QLabel("Strip Staging")
        self.wb_strip_label.setObjectName("SectionLabel")
        wb_staging_layout.addWidget(self.wb_strip_label)
        wb_staging_layout.addWidget(self.wb_strip_table, 1)
        wb_staging_buttons = QHBoxLayout()
        wb_staging_buttons.addWidget(self.wb_strip_up_button)
        wb_staging_buttons.addWidget(self.wb_strip_down_button)
        wb_staging_buttons.addWidget(self.wb_strip_delete_button)
        wb_staging_layout.addLayout(wb_staging_buttons)
        wb_staging_layout.addWidget(self.wb_import_strip_button)

        self.wb_context_panel = QFrame()
        self.wb_context_panel.setObjectName("DataPanel")
        self.wb_context_panel.setMinimumWidth(0)
        self.wb_context_panel.setMaximumWidth(16777215)
        self.wb_context_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        wb_context_layout = QVBoxLayout(self.wb_context_panel)
        wb_context_layout.setContentsMargins(12, 12, 12, 12)
        wb_context_layout.setSpacing(8)
        self.wb_context_stack = CurrentPageStackedWidget(compact=True)
        self.wb_context_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        wb_context_layout.addWidget(self.wb_context_stack, 1)

        import_page = QWidget()
        import_layout = QVBoxLayout(import_page)
        import_layout.addWidget(self._wb_group("image_properties", [self.wb_image_properties_label]))
        adjust_form = QFormLayout()
        adjust_form.addRow(self._i18n_form_label("brightness"), self.wb_brightness_spin)
        adjust_form.addRow(self._i18n_form_label("contrast"), self.wb_contrast_spin)
        adjust_form.addRow(self._i18n_form_label("rotation"), self.wb_rotation_spin)
        adjust_form.addRow(self._i18n_form_label("fine"), self.wb_fine_rotation_slider)
        adjust_group = bind_i18n(QGroupBox(), "image_adjustments", "title")
        adjust_group.setLayout(adjust_form)
        import_layout.addWidget(adjust_group)
        flip_row = QHBoxLayout()
        flip_row.addWidget(self.wb_flip_h_button)
        flip_row.addWidget(self.wb_flip_v_button)
        import_layout.addLayout(flip_row)
        import_layout.addStretch(1)

        crop_page = QWidget()
        crop_layout = QVBoxLayout(crop_page)
        crop_form = QFormLayout()
        crop_form.addRow(self._i18n_form_label("x"), self.wb_x_spin)
        crop_form.addRow(self._i18n_form_label("y"), self.wb_y_spin)
        crop_form.addRow(self._i18n_form_label("w"), self.wb_w_spin)
        crop_form.addRow(self._i18n_form_label("h"), self.wb_h_spin)
        crop_group = bind_i18n(QGroupBox(), "crop_roi", "title")
        crop_group.setLayout(crop_form)
        crop_layout.addWidget(crop_group)
        crop_layout.addWidget(self.wb_apply_crop_button)
        crop_layout.addWidget(self.wb_roi_mode_button)
        crop_instruction=bind_i18n(QLabel(),'wb_crop_instruction');crop_instruction.setWordWrap(True)
        crop_layout.addWidget(crop_instruction)
        alignment_group = bind_i18n(QGroupBox(), "alignment", "title")
        alignment_layout = QVBoxLayout(alignment_group)
        alignment_layout.addWidget(self.wb_lane_anchor_button)
        alignment_layout.addWidget(self.wb_auto_lane_button)
        alignment_layout.addWidget(self.wb_marker_anchor_button)
        alignment_layout.addWidget(self.wb_clear_anchors_button)
        alignment_tip = bind_i18n(QLabel(),'wb_anchor_instruction')
        alignment_tip.setWordWrap(True)
        alignment_layout.addWidget(alignment_tip)
        crop_layout.addWidget(alignment_group)
        crop_layout.addStretch(1)

        labels_page = QWidget()
        labels_layout = QVBoxLayout(labels_page)
        labels_layout.addWidget(self._wb_group("strip_metadata", [self.wb_band_table]))
        lane_form = QFormLayout()
        lane_form.addRow(self._i18n_form_label("total_lanes"), self.wb_lane_count_spin)
        lane_form.addRow(self._i18n_form_label("labels"), self.wb_lane_edit)
        lane_group = bind_i18n(QGroupBox(), "lane_setup", "title")
        lane_group.setLayout(lane_form)
        labels_layout.addWidget(lane_group)
        labels_layout.addWidget(action_grid((self.wb_add_group_level_button,self.wb_add_condition_row_button)))
        labels_layout.addWidget(self.wb_group_levels_edit)
        labels_layout.addWidget(self.wb_condition_rows_edit)
        option_form = QFormLayout()
        option_form.addRow(self._i18n_form_label("loading_ctrl"), self.wb_loading_control_combo)
        option_form.addRow(self._i18n_form_label("lane_labels"), self.wb_lane_position_combo)
        option_form.addRow(self._i18n_form_label("protein_side"), self.wb_protein_side_combo)
        option_form.addRow(self._i18n_form_label("marker_side"), self.wb_marker_side_combo)
        option_form.addRow(self._i18n_form_label("strip_gap"), self.wb_strip_gap_spin)
        option_form.addRow(self._i18n_form_label("label_offset"), self.wb_label_offset_spin)
        option_form.addRow(self._i18n_form_label("total_lanes"), self.wb_hierarchy_total_lanes_spin)
        option_form.addRow(self._i18n_form_label("gap_after_lane"), self.wb_group_gap_edit)
        option_group = bind_i18n(QGroupBox(), "options", "title")
        option_group.setLayout(option_form)
        labels_layout.addWidget(option_group)

        density_page = QWidget()
        density_layout = QVBoxLayout(density_page)
        density_layout.addWidget(self.wb_density_hint_label)
        density_form = QFormLayout()
        density_form.addRow(self._i18n_form_label("loading_control"), self.wb_density_loading_ctrl_combo)
        density_form.addRow(self._i18n_form_label("reference_lane"), self.wb_density_reference_lane_combo)
        density_form.addRow(self._i18n_form_label("background_subtraction"), self.wb_density_bg_method_combo)
        density_form.addRow(self._i18n_form_label("band_polarity"), self.wb_density_polarity_combo)
        density_layout.addLayout(density_form)
        density_layout.addWidget(self.wb_density_measure_button)
        density_results_label = bind_i18n(QLabel(),'results')
        density_results_label.setObjectName("SectionLabel")
        density_layout.addWidget(density_results_label)
        density_layout.addWidget(self.wb_density_result_table)
        density_layout.addWidget(self.wb_density_qc_label)
        density_actions = QHBoxLayout()
        density_actions.addWidget(self.wb_density_copy_button)
        density_actions.addWidget(self.wb_density_to_composer_button)
        density_layout.addLayout(density_actions)
        density_layout.addWidget(self.wb_density_chart_toggle)
        density_layout.addWidget(self.wb_density_chart_container)
        density_layout.addStretch(1)

        stats_page = QWidget()
        stats_layout = QVBoxLayout(stats_page)
        stats_hint = bind_i18n(QLabel(),'wb_stats_instruction')
        stats_hint.setObjectName("DimLabel")
        stats_hint.setWordWrap(True)
        stats_layout.addWidget(stats_hint)
        stats_layout.addWidget(self.wb_stats_group_table)
        stats_form = QFormLayout()
        stats_form.addRow(self._i18n_form_label("reference_group"), self.wb_stats_reference_group_combo)
        stats_form.addRow(self._i18n_form_label("test"), self.wb_stats_test_combo)
        stats_layout.addLayout(stats_form)
        stats_layout.addWidget(self.wb_stats_run_button)
        self.wb_pairing_button = bind_i18n(QPushButton(), 'pairing_settings')
        stats_layout.addWidget(self.wb_pairing_button)
        stats_layout.addWidget(self.wb_stats_result_table)
        stats_layout.addWidget(self.wb_stats_summary_label)
        stats_layout.addWidget(self.wb_stats_add_to_layout_button)
        stats_layout.addWidget(self.wb_stats_chart_toggle)
        stats_layout.addWidget(self.wb_stats_chart_container)
        stats_layout.addStretch(1)

        layout_content = QWidget()
        layout_layout = QVBoxLayout(layout_content)
        layout_layout.setContentsMargins(0, 0, 0, 0)
        layout_layout.addWidget(self.wb_preview_layout_button)
        layout_layout.addWidget(self.wb_apply_layout_button)
        layout_layout.addWidget(self.wb_move_image_button)
        layout_layout.addWidget(self.wb_box_select_text_button)
        layout_layout.addWidget(self.wb_full_blot_button)
        text_form = QFormLayout()
        text_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        text_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        text_form.addRow(self._i18n_form_label("font"), self.wb_text_font_combo)
        text_form.addRow(self._i18n_form_label("size"), self.wb_text_size_spin)
        text_buttons = QHBoxLayout()
        text_buttons.addWidget(self.wb_text_bold_button)
        text_buttons.addWidget(self.wb_text_italic_button)
        text_buttons.addWidget(self.wb_text_color_combo)
        text_form.addRow(self._i18n_form_label("style"), text_buttons)
        text_form.addRow(self._i18n_form_label("x"), self.wb_text_x_spin)
        text_form.addRow(self._i18n_form_label("y"), self.wb_text_y_spin)
        text_form.addRow(self._i18n_form_label("rotation"), self.wb_text_rotation_spin)
        text_group = bind_i18n(QGroupBox(), "text_formatting", "title")
        text_group.setLayout(text_form)
        layout_layout.addWidget(text_group)
        layout_layout.addWidget(self.wb_apply_text_type_button)
        line_form = QFormLayout()
        line_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        line_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        line_form.addRow(self._i18n_form_label("line_width"), self.wb_line_width_spin)
        line_form.addRow(self._i18n_form_label("line_color"), self.wb_line_color_combo)
        line_form.addRow(self._i18n_form_label("x"), self.wb_line_x_spin)
        line_form.addRow(self._i18n_form_label("y"), self.wb_line_y_spin)
        line_group = bind_i18n(QGroupBox(), "line_formatting", "title")
        line_group.setLayout(line_form)
        layout_layout.addWidget(line_group)
        layout_layout.addWidget(self.wb_apply_line_style_button)
        stats_style_form = QFormLayout()
        stats_style_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        stats_style_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        stats_style_form.addRow(self._i18n_form_label("font"), self.wb_stats_font_combo)
        stats_style_form.addRow(self._i18n_form_label("font_size"), self.wb_stats_font_size_spin)
        stats_style_form.addRow(self._i18n_form_label("plot_width"), self.wb_stats_plot_width_spin)
        stats_style_form.addRow(self._i18n_form_label("plot_height"), self.wb_stats_plot_height_spin)
        stats_style_form.addRow(self._i18n_form_label("bar_fill"), self.wb_stats_bar_fill_combo)
        stats_style_form.addRow(self._i18n_form_label("bar_edge"), self.wb_stats_bar_edge_combo)
        stats_style_form.addRow(self._i18n_form_label("bar_width"), self.wb_stats_bar_width_spin)
        stats_style_form.addRow(self._i18n_form_label("bar_line"), self.wb_stats_bar_line_width_spin)
        stats_style_form.addRow(self._i18n_form_label("axis_line"), self.wb_stats_axis_line_width_spin)
        stats_style_form.addRow(self._i18n_form_label("point_shape"), self.wb_stats_point_shape_combo)
        stats_style_form.addRow(self._i18n_form_label("point_size"), self.wb_stats_point_size_spin)
        stats_style_form.addRow(self._i18n_form_label("point_color"), self.wb_stats_point_color_combo)
        stats_style_group = bind_i18n(QGroupBox(), "statistics_plot", "title")
        stats_style_group.setLayout(stats_style_form)
        layout_layout.addWidget(stats_style_group)
        layout_layout.addWidget(self.wb_stats_apply_style_button)
        layout_layout.addStretch(1)
        layout_page = layout_content

        export_page = QWidget()
        export_layout = QVBoxLayout(export_page)
        if sys.platform in {'darwin', 'win32'}:
            export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.addWidget(self.wb_export_pdf_radio)
        export_layout.addWidget(self.wb_export_svg_radio)
        export_layout.addWidget(self.wb_export_tiff_radio)
        export_layout.addWidget(self.wb_export_png_radio)
        export_layout.addWidget(QLabel("DPI"))
        export_layout.addWidget(self.wb_export_dpi_combo)
        export_layout.addWidget(self.wb_export_source_checkbox)
        export_layout.addWidget(self._wb_group("integrity", [self.wb_integrity_label]))
        export_items_group = self._wb_group("", [self.wb_export_item_table, self.wb_export_to_composer_button])
        bind_i18n(export_items_group, "export_items", "title")
        if sys.platform in {'darwin', 'win32'}:
            export_items_group.layout().setContentsMargins(0, 8, 0, 0)
        export_layout.addWidget(export_items_group)
        export_layout.addWidget(self.wb_export_button)
        export_layout.addStretch(1)

        for page in [import_page, crop_page, labels_page, density_page, stats_page, layout_page, export_page]:
            self.wb_context_stack.addWidget(page)

        self.wb_bottom_bar = QWidget()
        wb_bottom_layout = QHBoxLayout(self.wb_bottom_bar)
        self.wb_bottom_layout = wb_bottom_layout
        wb_bottom_layout.setContentsMargins(0, 0, 0, 0)
        wb_bottom_layout.addWidget(self.wb_undo_button)
        wb_bottom_layout.addWidget(self.wb_redo_button)
        wb_bottom_layout.addWidget(self.wb_reset_button)
        wb_bottom_layout.addWidget(self.wb_step_hint, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addLayout(top_row)
        layout.addWidget(self.qpcr_step_nav)
        layout.addWidget(self.qpcr_context_panel, 1)
        layout.addWidget(self.detail)
        layout.addLayout(settings_row)
        layout.addLayout(qpcr_layout_tools_row)
        layout.addWidget(self.ihc_context_panel)
        layout.addWidget(self.brightfield_context_panel)
        layout.addWidget(self.wb_step_nav)
        layout.addWidget(self.wb_bottom_bar)
        self.wb_row_widgets = [self.wb_step_nav, self.wb_bottom_bar]
        self.qpcr_layout_tool_widgets = [
            self.qpcr_preview_button,
            self.qpcr_layout_button,
            self.qpcr_add_text_button,
            self.qpcr_add_shape_button,
            self.qpcr_same_size_button,
            self.qpcr_align_row_button,
            self.qpcr_align_column_button,
            self.qpcr_distribute_h_button,
            self.qpcr_distribute_v_button,
            self.qpcr_delete_selected_button,
        ]
        self._bind_static_i18n()
        self.retranslate()
        self.set_module("qpcr")

    def _bind_static_i18n(self) -> None:
        bindings = (
            ("secondary_button", "create_mock_panel", "text"),
            ("journal_preset_label", "journal_preset", "text"),
            ("reference_label", "reference", "text"),
            ("control_label", "control", "text"),
            ("run_analysis_button", "run_analysis", "text"),
            ("copy_summary_button", "copy_summary", "text"),
            ("qpcr_to_composer_button", "add_qpcr_plots", "text"),
            ("qpcr_preview_button", "v9_preview", "text"),
            ("qpcr_layout_button", "layout", "text"),
            ("qpcr_add_text_button", "add_text", "text"),
            ("qpcr_add_shape_button", "add_shape", "text"),
            ("qpcr_same_size_button", "same_size", "text"),
            ("qpcr_align_row_button", "align_row", "text"),
            ("qpcr_align_column_button", "align_column", "text"),
            ("qpcr_distribute_h_button", "distribute_h", "text"),
            ("qpcr_distribute_v_button", "distribute_v", "text"),
            ("qpcr_delete_selected_button", "delete", "text"),
            ("wb_apply_crop_button", "apply_crop", "text"),
            ("wb_roi_mode_button", "select_roi", "text"),
            ("wb_full_blot_button", "full_length_blot", "text"),
            ("wb_move_image_button", "move_image", "text"),
            ("wb_box_select_text_button", "box_select_text", "text"),
            ("wb_lane_anchor_button", "lane_anchors", "text"),
            ("wb_auto_lane_button", "auto_detect_lanes", "text"),
            ("wb_marker_anchor_button", "marker_anchors", "text"),
            ("wb_clear_anchors_button", "clear_anchors", "text"),
            ("wb_flip_h_button", "flip_h", "text"),
            ("wb_flip_v_button", "flip_v", "text"),
            ("wb_preview_layout_button", "preview_layout", "text"),
            ("wb_apply_layout_button", "apply", "text"),
            ("wb_reset_button", "reset_original", "text"),
            ("wb_undo_button", "v9_undo", "text"),
            ("wb_redo_button", "redo", "text"),
            ("wb_density_measure_button", "measure", "text"),
            ("wb_density_copy_button", "copy_full_table", "text"),
            ("wb_density_to_composer_button", "to_composer", "text"),
            ("wb_density_chart_toggle", "chart_preview", "text"),
            ("wb_stats_run_button", "run_statistics", "text"),
            ("wb_stats_add_to_layout_button", "add_plot_to_layout", "text"),
            ("wb_stats_chart_toggle", "chart_preview", "text"),
            ("wb_export_to_composer_button", "export_selected_to_composer", "text"),
            ("wb_export_button", "export_figure", "text"),
            ("wb_export_source_checkbox", "include_source_data", "text"),
            ("wb_strip_up_button", "up", "text"),
            ("wb_strip_down_button", "down", "text"),
            ("wb_strip_delete_button", "remove_selected", "text"),
            ("wb_import_strip_button", "import", "text"),
            ("wb_image_properties_label", "no_strip_selected", "text"),
            ("ihc_tissue_correction_checkbox", "exclude_tissue_background", "text"),
            ("ihc_mark_positive_button", "positive", "text"),
            ("ihc_mark_negative_button", "negative", "text"),
            ("ihc_stop_sampling_button", "off", "text"),
            ("ihc_recalibrate_button", "recalibrate", "text"),
            ("ihc_revert_auto_button", "revert_to_auto", "text"),
            ("ihc_delete_example_button", "delete_selected_example", "text"),
            ("ihc_run_analysis_button", "run_ihc_analysis", "text"),
            ("ihc_copy_table_button", "copy_full_table", "text"),
            ("ihc_add_outputs_button", "add_ihc_outputs", "text"),
            ("ihc_roi_draw_button", "draw_rectangle_roi", "text"),
            ("ihc_roi_clear_button", "clear_roi", "text"),
            ("ihc_try_assisted_button", "try_assisted_mode", "text"),
            ("ihc_export_overlay_button", "export_overlay_image", "text"),
            ("ihc_export_results_csv_button", "export_results_csv", "text"),
            ("ihc_export_summary_json_button", "export_summary_json", "text"),
            ("ihc_stats_run_button", "run_statistics", "text"),
            ("ihc_copy_stats_button", "copy_statistics_table", "text"),
            ("ihc_export_stats_csv_button", "export_statistics_csv", "text"),
            ("brightfield_gray_invert_checkbox", "invert_grayscale_threshold", "text"),
            ("brightfield_fill_holes_checkbox", "fill_holes", "text"),
            ("brightfield_preview_mask_button", "preview_mask_overlay", "text"),
            ("brightfield_run_analysis_button", "run_brightfield_measurement", "text"),
            ("brightfield_copy_table_button", "copy_full_table", "text"),
            ("brightfield_add_outputs_button", "add_brightfield_outputs", "text"),
            ("brightfield_he_fiber_calibrate_button", "calibrate_he_title", "text"),
            ("brightfield_he_fiber_draw_scale_line_button", "draw_scale", "text"),
            ("brightfield_he_fiber_apply_scale_button", "apply_scale", "text"),
            ("brightfield_he_fiber_refresh_results_button", "refresh", "text"),
            ("brightfield_he_fiber_reset_scale_button", "reset", "text"),
            ("brightfield_he_fiber_tuning_group", "he_fiber_segmentation_controls", "title"),
            ("brightfield_ai_object_count_button", "run_he_fiber_area", "text"),
            ("brightfield_copy_ai_results_button", "copy_he_fiber_results", "text"),
            ("brightfield_roi_draw_button", "draw_rectangle_roi", "text"),
            ("brightfield_roi_clear_button", "clear_roi", "text"),
            ("brightfield_lock_batch_threshold_button", "lock_current", "text"),
            ("brightfield_unlock_batch_threshold_button", "unlock", "text"),
            ("brightfield_stats_run_button", "run_statistics", "text"),
            ("brightfield_copy_stats_button", "copy_traditional_stats", "text"),
            ("brightfield_export_stats_csv_button", "export_traditional_stats_csv", "text"),
            ("brightfield_copy_ai_stats_button", "copy_he_fiber_stats", "text"),
            ("brightfield_export_ai_stats_csv_button", "export_he_fiber_stats_csv", "text"),
            ("brightfield_export_chart_png_button", "export_current_chart_png", "text"),
            ("brightfield_apply_metadata_button", "apply_image_metadata", "text"),
            ("wb_roi_label", "roi", "text"),
            ("wb_x_label", "x", "text"),
            ("wb_y_label", "y", "text"),
            ("wb_w_label", "w", "text"),
            ("wb_h_label", "h", "text"),
            ("wb_brightness_label", "brightness", "text"),
            ("wb_contrast_label", "contrast", "text"),
            ("wb_rotation_label", "rotation", "text"),
            ("wb_lane_count_label", "lane_count", "text"),
            ("wb_loading_control_label", "loading_control", "text"),
            ("wb_lane_position_label", "lane", "text"),
            ("wb_protein_side_label", "protein", "text"),
            ("wb_marker_side_label", "marker", "text"),
            ("wb_strip_label", "strip_staging", "text"),
            ("wb_total_lanes_label", "total_lanes", "text"),
            ("wb_group_gap_label", "gap_after_lane", "text"),
            ("wb_text_style_label", "text", "text"),
            ("wb_text_rotation_label", "rot", "text"),
            ("wb_text_x_label", "x", "text"),
            ("wb_text_y_label", "y", "text"),
        )
        for name, key, attribute in bindings:
            widget = getattr(self, name, None)
            if widget is not None:
                bind_i18n(widget, key, attribute)

    def _i18n_form_label(self, key: str) -> QLabel:
        return bind_i18n(QLabel(), key)

    def _retranslate_tables_and_placeholders(self) -> None:
        self.wb_strip_table.retranslate()
        self.qpcr_group_table.setHorizontalHeaderLabels([tr("sample"), tr("group"), tr("include")])
        self.wb_band_table.setHorizontalHeaderLabels(
            [tr("number_sign"), tr("protein"), tr("mw_kda"), tr("loading_ctrl")]
        )
        self.wb_stats_group_table.setHorizontalHeaderLabels([tr("lane"), tr("group"), tr("include")])
        self.wb_export_item_table.setHorizontalHeaderLabels([tr("export"), tr("item"), tr("format")])
        self.preview_table.setHorizontalHeaderLabels([tr("sample"), tr("target"), tr("ct"), tr("qc")])
        self.ihc_results_table.setHorizontalHeaderLabels(
            [tr("image"), tr("group"), tr("positive_area_percent"), tr("mean_intensity"), tr("h_score")]
        )
        self.ihc_stats_group_table.setHorizontalHeaderLabels([tr("sample"), tr("group"), tr("include")])
        self.ihc_stats_result_table.setHorizontalHeaderLabels([tr("group"), "n", tr("mean"), tr("sem")])
        self.brightfield_ai_results_table.setHorizontalHeaderLabels(
            [
                tr("image"),
                tr("group"),
                tr("stain"),
                tr("he_fiber_count"),
                tr("mean_fiber_area_px"),
                tr("median_fiber_area_px"),
                tr("analyzer"),
                tr("include"),
                tr("warnings_qc"),
            ]
        )
        self.brightfield_ai_fiber_area_table.setHorizontalHeaderLabels(
            [tr("image"), tr("group"), tr("fiber"), tr("area_px"), tr("area_um2")]
        )
        self.brightfield_results_table.setHorizontalHeaderLabels(
            [
                tr("image"),
                tr("group"),
                tr("stain"),
                tr("mode_label"),
                tr("area_fraction"),
                tr("segmented_regions"),
                tr("mean_object_area"),
            ]
        )
        self.brightfield_bright_round_cell_results_table.setHorizontalHeaderLabels(
            [
                tr("bright_round_cell_id"),
                tr("bright_round_cell_shape"),
                tr("bright_round_cell_equivalent_diameter"),
                tr("bright_round_cell_short_diameter"),
                tr("bright_round_cell_feret_diameter"),
                tr("bright_round_cell_circularity"),
                tr("x"),
                tr("y"),
                tr("cell_measurement_status"),
                tr("image"),
                tr('cell_max_transverse_width'),
                tr('cell_contour_area'),
            ]
        )
        self.brightfield_stats_group_table.setHorizontalHeaderLabels([tr("sample"), tr("group"), tr("include")])
        self.brightfield_stats_result_table.setHorizontalHeaderLabels([tr("group"), "n", tr("mean"), tr("sem")])
        self.brightfield_ai_stats_result_table.setHorizontalHeaderLabels(
            [tr("group"), "n", tr("mean_count"), tr("median_count"), tr("mean_area_px"), tr("median_area_px")]
        )
        self.wb_protein_edit.setPlaceholderText(tr("right_protein_placeholder"))
        self.wb_lane_edit.setPlaceholderText(tr("lane_labels_placeholder"))
        self.wb_marker_edit.setPlaceholderText(tr("marker_labels_placeholder"))
        self.wb_group_levels_edit.setPlaceholderText(tr("group_levels_placeholder"))
        self.wb_condition_rows_edit.setPlaceholderText(tr("condition_rows_placeholder"))
        self.wb_group_gap_edit.setPlaceholderText(tr("group_gap_placeholder"))
        self.brightfield_he_fiber_scale_um_per_px_spin.setSpecialValueText(tr("calibration_from_image"))

    def _wrap_context_page(self, *widgets: QWidget) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for widget in widgets:
            layout.addWidget(widget, 0)
        layout.addStretch(1)
        return page

    def _emit_ihc_active_image_combo_selected(self, _index: int) -> None:
        image_uid = str(self.ihc_active_image_combo.currentData() or "").strip()
        if image_uid:
            self.ihc_image_selected.emit(image_uid)

    def _emit_ihc_measurement_selected(self) -> None:
        measurement_uuid = self.selected_ihc_measurement_uuid()
        if measurement_uuid:
            self.ihc_measurement_selected.emit(measurement_uuid)

    def _emit_brightfield_active_image_combo_selected(self, _index: int) -> None:
        image_uid = str(self.brightfield_active_image_combo.currentData() or "").strip()
        if image_uid:
            self.brightfield_image_selected.emit(image_uid)

    def _emit_brightfield_measurement_selected(self) -> None:
        measurement_uuid = self.selected_brightfield_measurement_uuid()
        if measurement_uuid:
            self.brightfield_measurement_selected.emit(measurement_uuid)

    def _emit_brightfield_ai_measurement_selected(self) -> None:
        measurement_uuid = self.selected_brightfield_ai_measurement_uuid()
        if measurement_uuid:
            self.brightfield_ai_measurement_selected.emit(measurement_uuid)

    def _sync_brightfield_segmentation_mode(self) -> None:
        if not hasattr(self, "brightfield_segmentation_mode_combo"):
            return
        mode = str(self.brightfield_segmentation_mode_combo.currentData() or "hsv")
        self.brightfield_segment_params_stack.setCurrentIndex({"hsv": 0, "lab": 1, "grayscale": 2}.get(mode, 0))

    def current_ihc_guided_mode(self) -> str:
        return str(self.ihc_mode_combo.currentData() or "foolproof")

    def set_ihc_guided_mode(self, mode: str) -> None:
        index = self.ihc_mode_combo.findData(str(mode).strip().lower())
        if index >= 0:
            self.ihc_mode_combo.setCurrentIndex(index)

    def current_ihc_task_type(self) -> str:
        return str(self.ihc_task_type_combo.currentData() or "ihc_dab")

    def set_ihc_task_type(self, task_type: str) -> None:
        index = self.ihc_task_type_combo.findData(str(task_type).strip().lower())
        if index >= 0:
            self.ihc_task_type_combo.setCurrentIndex(index)

    def current_ihc_quantification_goal(self) -> str:
        return self.ihc_quant_goal_combo.currentData() or self.ihc_quant_goal_combo.currentText() or "Positive Area %"

    def set_ihc_quantification_goal(self, goal: str) -> None:
        index = self.ihc_quant_goal_combo.findData(str(goal))
        if index >= 0:
            self.ihc_quant_goal_combo.setCurrentIndex(index)
        else:
            self._set_combo_text(self.ihc_quant_goal_combo, str(goal))

    def current_ihc_stats_metric(self) -> str:
        return str(self.ihc_stats_metric_combo.currentData() or self.ihc_stats_metric_combo.currentText())

    def current_brightfield_stats_metric(self) -> str:
        return str(self.brightfield_stats_metric_combo.currentData() or self.brightfield_stats_metric_combo.currentText())

    def _sync_ihc_guided_mode(self) -> None:
        mode = self.current_ihc_guided_mode()
        task_type = self.current_ihc_task_type()
        goal = self.current_ihc_quantification_goal()
        self.ihc_guided_controls_group.setVisible(mode == "assisted")
        self.ihc_threshold_group.setVisible(mode == "expert")
        self.ihc_measure_actions_group.setTitle(
            tr({"foolproof": "foolproof_mode", "assisted": "assisted_mode", "expert": "expert_mode"}.get(mode, "foolproof_mode"))
        )
        self.ihc_run_analysis_button.setText(tr("run_expert_analysis" if mode == "expert" else "auto_analyze"))
        self.ihc_try_assisted_button.setVisible(mode == "foolproof")
        if task_type != "ihc_dab":
            message = tr("ihc_task_placeholder_hint")
        elif mode == "expert":
            message = tr("ihc_expert_hint")
        elif mode == "assisted":
            message = tr("ihc_assisted_hint")
        else:
            message = tr("ihc_foolproof_hint")
        self.ihc_workflow_hint_label.setText(f"{message}\n{tr('goal_value').format(goal=goal)}")

    def ihc_foolproof_analysis_settings(self) -> dict[str, str | bool]:
        return {
            "sensitivity": "medium",
            "cleanup_level": "medium",
            "tissue_background_correction": True,
        }

    def ihc_guided_analysis_settings(self) -> dict[str, str | bool]:
        return {
            "sensitivity": str(self.ihc_sensitivity_combo.currentData() or "medium"),
            "cleanup_level": str(self.ihc_cleanup_combo.currentData() or "medium"),
            "tissue_background_correction": bool(self.ihc_tissue_correction_checkbox.isChecked()),
        }

    def set_ihc_guided_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        if "sensitivity" in payload:
            self._set_combo_data(self.ihc_sensitivity_combo, payload["sensitivity"])
        if "cleanup_level" in payload:
            self._set_combo_data(self.ihc_cleanup_combo, payload["cleanup_level"])
        if "tissue_background_correction" in payload:
            self.ihc_tissue_correction_checkbox.setChecked(bool(payload["tissue_background_correction"]))

    def ihc_overlay_opacity(self) -> float:
        return float(self.ihc_overlay_opacity_slider.value()) / 100.0

    def set_ihc_overlay_opacity(self, opacity: float) -> None:
        bounded = max(0.10, min(1.00, float(opacity)))
        slider_value = int(round(bounded * 100.0))
        self.ihc_overlay_opacity_slider.blockSignals(True)
        self.ihc_overlay_opacity_slider.setValue(slider_value)
        self.ihc_overlay_opacity_slider.blockSignals(False)
        self.ihc_overlay_opacity_value_label.setText(f"{slider_value}%")

    def current_ihc_example_click_mode(self) -> str:
        if self.ihc_mark_positive_button.isChecked():
            return "positive"
        if self.ihc_mark_negative_button.isChecked():
            return "negative"
        return ""

    def set_ihc_example_click_mode(self, mode: str | None) -> None:
        normalized = str(mode or "").strip().lower()
        self.ihc_mark_positive_button.blockSignals(True)
        self.ihc_mark_negative_button.blockSignals(True)
        self.ihc_stop_sampling_button.blockSignals(True)
        self.ihc_mark_positive_button.setChecked(normalized == "positive")
        self.ihc_mark_negative_button.setChecked(normalized == "negative")
        self.ihc_stop_sampling_button.setChecked(normalized not in {"positive", "negative"})
        self.ihc_mark_positive_button.blockSignals(False)
        self.ihc_mark_negative_button.blockSignals(False)
        self.ihc_stop_sampling_button.blockSignals(False)
        if normalized == "positive":
            self.ihc_example_status_label.setText(tr("sampling_positive_hint"))
        elif normalized == "negative":
            self.ihc_example_status_label.setText(tr("sampling_negative_hint"))
        else:
            self.ihc_example_status_label.setText(tr("sampling_off_hint"))

    def set_ihc_assisted_action_state(self, *, can_sample: bool, can_recalibrate: bool, can_revert: bool) -> None:
        sample_enabled = bool(can_sample)
        self.ihc_mark_positive_button.setEnabled(sample_enabled)
        self.ihc_mark_negative_button.setEnabled(sample_enabled)
        self.ihc_stop_sampling_button.setEnabled(sample_enabled)
        self.ihc_overlay_opacity_slider.setEnabled(sample_enabled)
        self.ihc_recalibrate_button.setEnabled(bool(can_recalibrate))
        self.ihc_revert_auto_button.setEnabled(bool(can_revert))
        if not sample_enabled:
            self.set_ihc_example_click_mode(None)

    def set_brightfield_batch_threshold_action_state(self, *, can_lock: bool, can_unlock: bool) -> None:
        self.brightfield_lock_batch_threshold_button.setEnabled(bool(can_lock))
        self.brightfield_unlock_batch_threshold_button.setEnabled(bool(can_unlock))

    def populate_ihc_example_samples(self, samples: list[dict]) -> None:
        self.ihc_examples_list.clear()
        for index, sample in enumerate(samples, start=1):
            kind = str(sample.get("kind", "")).strip().lower() or "example"
            x = int(sample.get("x", 0))
            y = int(sample.get("y", 0))
            item = QListWidgetItem(f"{index}. {kind.title()} ({x}, {y})")
            self.ihc_examples_list.addItem(item)
        if self.ihc_examples_list.count() > 0:
            self.ihc_examples_list.setCurrentRow(0)

    def selected_ihc_example_index(self) -> int:
        return self.ihc_examples_list.currentRow()

    def current_brightfield_guided_mode(self) -> str:
        return str(self.brightfield_mode_combo.currentData() or "foolproof")

    def current_brightfield_foolproof_threshold_bias(self) -> int:
        try:
            return int(self.brightfield_foolproof_sensitivity_combo.currentData() or 0)
        except (TypeError, ValueError):
            return 0

    def set_brightfield_foolproof_threshold_bias(self, bias: int) -> None:
        index = self.brightfield_foolproof_sensitivity_combo.findData(int(bias))
        if index >= 0:
            self.brightfield_foolproof_sensitivity_combo.setCurrentIndex(index)

    def current_brightfield_assisted_threshold_bias(self) -> int:
        try:
            return int(self.brightfield_assisted_sensitivity_combo.currentData() or 0)
        except (TypeError, ValueError):
            return 0

    def set_brightfield_assisted_threshold_bias(self, bias: int) -> None:
        index = self.brightfield_assisted_sensitivity_combo.findData(int(bias))
        if index >= 0:
            self.brightfield_assisted_sensitivity_combo.setCurrentIndex(index)

    def current_brightfield_assisted_small_region_filter_bias(self) -> int:
        try:
            return int(self.brightfield_assisted_small_region_filter_combo.currentData() or 0)
        except (TypeError, ValueError):
            return 0

    def set_brightfield_assisted_small_region_filter_bias(self, bias: int) -> None:
        index = self.brightfield_assisted_small_region_filter_combo.findData(int(bias))
        if index >= 0:
            self.brightfield_assisted_small_region_filter_combo.setCurrentIndex(index)

    def set_brightfield_guided_mode(self, mode: str) -> None:
        index = self.brightfield_mode_combo.findData(str(mode).strip().lower())
        if index >= 0:
            self.brightfield_mode_combo.setCurrentIndex(index)

    def set_brightfield_foolproof_sensitivity_enabled(self, enabled: bool) -> None:
        self.brightfield_foolproof_sensitivity_combo.setEnabled(bool(enabled))

    def set_brightfield_assisted_sensitivity_enabled(self, enabled: bool) -> None:
        self.brightfield_assisted_sensitivity_combo.setEnabled(bool(enabled))

    def set_brightfield_assisted_small_region_filter_enabled(self, enabled: bool) -> None:
        self.brightfield_assisted_small_region_filter_combo.setEnabled(bool(enabled))

    def _sync_brightfield_guided_mode(self) -> None:
        mode = self.current_brightfield_guided_mode()
        stain_type = str(self.brightfield_stain_type_combo.currentText() or "H&E")
        normalized_stain_type = normalize_brightfield_stain_type(stain_type)
        self.brightfield_foolproof_controls_group.setVisible(mode == "foolproof" and normalized_stain_type == "H&E")
        self.brightfield_guided_controls_group.setVisible(mode == "assisted" and normalized_stain_type == "H&E")
        self.brightfield_measure_group.setTitle(
            '' if self.brightfield_measure_group.property('measurementTaskBody') else tr("measure")
        )
        if mode == "expert":
            self.brightfield_run_analysis_button.setText(tr("run_expert_measurement"))
            message = tr("bf_expert_hint")
        elif normalized_stain_type == "H&E":
            self.brightfield_run_analysis_button.setText(tr("auto_analyze_he"))
            if mode == "foolproof":
                message = tr("bf_foolproof_hint")
            else:
                message = tr("bf_assisted_hint")
        elif mode == "foolproof":
            self.brightfield_run_analysis_button.setText(tr("run_foolproof_preset"))
            message = tr("bf_stain_foolproof_hint")
        else:
            self.brightfield_run_analysis_button.setText(tr("run_measurement"))
            message = tr("bf_stain_assisted_hint")
        self.brightfield_workflow_hint_label.setText(message)

    def brightfield_guided_analysis_settings(self) -> dict[str, str | int]:
        return {
            "sensitivity": str(self.brightfield_sensitivity_combo.currentData() or "medium"),
            "cleanup_level": str(self.brightfield_cleanup_combo.currentData() or "low"),
            "threshold_bias": self.current_brightfield_assisted_threshold_bias(),
            "small_region_filter_bias": self.current_brightfield_assisted_small_region_filter_bias(),
        }

    def brightfield_foolproof_analysis_settings(self) -> dict[str, int]:
        return {
            "threshold_bias": self.current_brightfield_foolproof_threshold_bias(),
        }

    def set_brightfield_guided_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        if "sensitivity" in payload:
            self._set_combo_data(self.brightfield_sensitivity_combo, payload["sensitivity"])
        if "cleanup_level" in payload:
            self._set_combo_data(self.brightfield_cleanup_combo, payload["cleanup_level"])
        if "threshold_bias" in payload:
            self.set_brightfield_assisted_threshold_bias(int(payload["threshold_bias"]))
        if "small_region_filter_bias" in payload:
            self.set_brightfield_assisted_small_region_filter_bias(int(payload["small_region_filter_bias"]))

    def set_brightfield_foolproof_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        if "threshold_bias" in payload:
            self.set_brightfield_foolproof_threshold_bias(int(payload["threshold_bias"]))

    def _build_qpcr_context_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("DataPanel")
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(16777215)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(8)
        self.qpcr_context_stack = CurrentPageStackedWidget(compact=True)
        panel_layout.addWidget(self.qpcr_context_stack, 1)

        import_page = QWidget()
        self.qpcr_import_layout = QVBoxLayout(import_page)
        self.qpcr_import_layout.setContentsMargins(0, 0, 0, 0)
        self.qpcr_import_layout.setSpacing(8)
        import_form = QFormLayout()
        import_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.qpcr_instrument_label = QLabel(tr("instrument"))
        import_form.addRow(self.qpcr_instrument_label, self.instrument_combo)
        self.qpcr_import_group = QGroupBox(tr("import"))
        self.qpcr_import_group.setLayout(import_form)
        self.qpcr_import_layout.addWidget(self.qpcr_import_group)
        self.qpcr_import_layout.addWidget(self.import_button)
        self.qpcr_import_layout.addWidget(self.secondary_button)
        self.qpcr_preview_label = QLabel(tr("v9_preview"))
        self.qpcr_preview_label.setObjectName("SectionLabel")
        self.qpcr_import_layout.addWidget(self.qpcr_preview_label)
        self.qpcr_import_layout.addWidget(self.preview_table, 1)
        self.qpcr_import_layout.addStretch(1)

        analyze_page = QWidget()
        analyze_layout = QVBoxLayout(analyze_page)
        analyze_layout.setContentsMargins(0, 0, 0, 0)
        analyze_layout.setSpacing(8)
        analyze_form = QFormLayout()
        analyze_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        analyze_form.addRow(self.reference_label, self.reference_combo)
        analyze_form.addRow(self.control_label, self.control_combo)
        self.qpcr_analysis_group = QGroupBox(tr("analysis"))
        self.qpcr_analysis_group.setLayout(analyze_form)
        analyze_layout.addWidget(self.qpcr_analysis_group)
        self.qpcr_group_label = QLabel(tr("groups"))
        self.qpcr_group_label.setObjectName("SectionLabel")
        analyze_layout.addWidget(self.qpcr_group_label)
        self.qpcr_group_table.setMinimumHeight(180)
        self.qpcr_group_table.setMaximumHeight(100000)
        analyze_layout.addWidget(self.qpcr_group_table, 1)
        analyze_layout.addWidget(self.run_analysis_button)
        analyze_layout.addWidget(self.copy_summary_button)
        analyze_layout.addWidget(self.summary)

        export_page = QWidget()
        export_layout = QVBoxLayout(export_page)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(8)
        self.qpcr_export_hint = QLabel(tr("export_qpcr_hint"))
        self.qpcr_export_hint.setObjectName("DimLabel")
        self.qpcr_export_hint.setWordWrap(True)
        export_layout.addWidget(self.qpcr_export_hint)
        export_layout.addWidget(self.qpcr_to_composer_button)
        export_layout.addStretch(1)

        for page in [import_page, analyze_page, export_page]:
            self.qpcr_context_stack.addWidget(page)
        return panel
        self.set_qpcr_step(0, emit_signal=False)
        self.set_wb_step(WB_STEP_IMPORT, emit_signal=False)
        self.set_wb_text_toolbar_enabled(False)

    def _wb_group(self, title: str, widgets: list[QWidget]) -> QGroupBox:
        group = QGroupBox()
        if title:bind_i18n(group,title,'title')
        layout = QVBoxLayout(group)
        for widget in widgets:
            layout.addWidget(widget)
        return group

    def _qpcr_step_labels(self) -> list[str]:
        return [tr("import"), tr("analyze"), tr("layout"), tr("export")]

    def _wb_step_labels(self) -> list[str]:
        return [tr("import"), tr("crop"), tr("labels"), tr("v9_density"), tr("stats"), tr("layout"), tr("export")]

    def _ihc_step_labels(self) -> list[str]:
        return [tr("import"), tr("measure"), tr("stats"), tr("layout")]

    def _brightfield_step_labels(self) -> list[str]:
        return [tr("import"), tr("preprocess_segment"), tr("measure"), tr("stats"), tr("layout")]

    def _wb_step_hints(self) -> list[str]:
        return [
            tr("step_wb_import_hint"),
            tr("step_wb_crop_hint"),
            tr("step_wb_labels_hint"),
            tr("step_wb_density_hint"),
            tr("step_wb_stats_hint"),
            tr("step_wb_layout_hint"),
            tr("step_wb_export_hint"),
        ]

    def _set_qpcr_expanding_height(self) -> None:
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def _sync_qpcr_export_action_visibility(self, step_index: int | None = None) -> None:
        if not hasattr(self, "qpcr_to_composer_button"):
            return
        if step_index is None and hasattr(self, "qpcr_context_stack"):
            step_index = 3 if self.qpcr_context_stack.currentIndex() == 2 else self.qpcr_context_stack.currentIndex()
        self.qpcr_to_composer_button.setVisible(self.current_module == "qpcr" and not self._qpcr_layout_mode and step_index == 3)

    def retranslate(self) -> None:
        retranslate_bound_widgets(self)
        retranslate_bound_widgets(self.wb_context_stack)
        retranslate_bound_widgets(self.ihc_context_stack)
        retranslate_bound_widgets(self.brightfield_context_stack)
        for label,key,defaults in (
            (self.wb_density_qc_label,'no_measurements_yet',('No measurements yet.','尚无测量结果。')),
            (self.wb_stats_summary_label,'no_statistics_yet',('No statistics yet.','尚无统计结果。')),
            (self.wb_integrity_label,'wb_integrity_unavailable',('Integrity badge unavailable','完整性状态暂不可用')),
        ):
            if label.text() in defaults:label.setText(tr(key))
        mode_labels = {key: tr(key) for key in ("foolproof", "assisted", "expert")}
        retranslate_combo(self.ihc_mode_combo, mode_labels)
        retranslate_combo(self.ihc_task_type_combo, {"hematoxylin_rich": tr("ihc_task_hematoxylin_unavailable"),
            "other_brightfield": tr("ihc_task_other_unavailable")})
        retranslate_combo(self.brightfield_mode_combo, mode_labels)
        retranslate_combo(self.brightfield_segmentation_mode_combo,
            {key: tr("segmentation_" + key) for key in ("hsv", "lab", "grayscale")})
        for combo in (self.ihc_quant_goal_combo, self.ihc_stats_metric_combo):
            retranslate_combo(combo, {"Positive Area %": tr("positive_area_percent"),
                "Mean DAB Intensity": tr("mean_dab_intensity"), "H-score": tr("h_score")})
        retranslate_combo(self.brightfield_stats_metric_combo, {
            "Area Fraction %": tr("area_fraction"), BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL: tr("segmented_regions"),
            "Mean Object Area": tr("mean_object_area"), "ROI Mean Intensity": tr("roi_mean_intensity")})
        for combo in (self.ihc_sensitivity_combo, self.brightfield_sensitivity_combo):
            retranslate_combo(combo, {key: tr(key) for key in ("low", "medium", "high")})
        for combo in (self.ihc_cleanup_combo, self.brightfield_cleanup_combo):
            retranslate_combo(combo, {key: tr(key) for key in ("off", "low", "medium", "high")})
        retranslate_combo(self.brightfield_stats_plot_source_combo,
            {"traditional": tr("traditional_brightfield"), "ai_object_count": tr("he_fiber_area")})
        retranslate_combo(self.brightfield_stats_chart_mode_combo, {"summary": tr("summary"),
            "frequency_bar": tr("fiber_area_frequency_bar"), "frequency_line": tr("fiber_area_frequency_line")})
        retranslate_combo(self.brightfield_stats_frequency_mode_combo,
            {"absolute": tr("absolute_count"), "percent": tr("percent_of_fibers")})
        retranslate_combo(
            self.brightfield_he_fiber_method_combo,
            {
                "original": tr("he_fiber_method_original"),
                "anti_oversegmentation": tr("he_fiber_method_anti_oversegmentation"),
                'cellpose_cyto3': tr('he_fiber_method_cellpose_cyto3'),
                HE_FIBER_METHOD_BOUNDARY: tr('he_fiber_method_'+HE_FIBER_METHOD_BOUNDARY),
            },
        )
        self.ihc_import_hint_label.setText(tr("ihc_import_instructions") + "\n" + tr("histology_import_formats"))
        self.brightfield_import_hint_label.setText(tr("brightfield_import_instructions") + "\n" + tr("histology_import_formats"))
        self.brightfield_calibration_controls.retranslate()
        retranslate_combo(self.brightfield_cellpose_strategy_combo,
            {strategy:tr('cellpose_cell_strategy_'+strategy) for strategy in ('traditional','general','local_contrast')})
        for index, key in enumerate(("cell_size_mode", "area_measurement_mode", "fiber_measurement_mode")):
            self.brightfield_measure_type_combo.setItemText(index, tr(key))
        self._retranslate_tables_and_placeholders()
        if self.current_module:
            content = localized_module_placeholder(self.current_module)
            self.title.setText(content["title"].upper())
            self.detail.setText(content["detail"])
        self.brightfield_he_fiber_draw_scale_line_button.setToolTip(tr("draw_he_scale_tooltip"))
        self.brightfield_he_fiber_apply_scale_button.setToolTip(tr("apply_he_scale_tooltip"))
        self.brightfield_he_fiber_refresh_results_button.setToolTip(tr("refresh_he_scale_tooltip"))
        self.brightfield_he_fiber_reset_scale_button.setToolTip(tr("reset_he_scale_tooltip"))
        self._sync_he_fiber_tuning_enabled()
        self.brightfield_ai_object_count_button.setToolTip(tr("run_he_fiber_area_tooltip"))
        self.import_button.setText(tr("import"))
        self.reference_label.setText(tr("reference"))
        self.control_label.setText(tr("control"))
        self.run_analysis_button.setText(tr("run_analysis"))
        self.copy_summary_button.setText(tr("copy_summary"))
        self.ihc_add_outputs_button.setText(tr("add_ihc_outputs"))
        self.brightfield_add_outputs_button.setText(tr("add_brightfield_outputs"))
        self.qpcr_to_composer_button.setText(tr("add_qpcr_plots"))
        self.qpcr_preview_button.setText(tr("v9_preview"))
        self.qpcr_layout_button.setText(tr("layout"))
        self.qpcr_add_text_button.setText(tr("add_text"))
        self.qpcr_add_shape_button.setText(tr("add_shape"))
        self.qpcr_same_size_button.setText(tr("same_size"))
        self.qpcr_align_row_button.setText(tr("align_row"))
        self.qpcr_align_column_button.setText(tr("align_column"))
        self.qpcr_distribute_h_button.setText(tr("distribute_h"))
        self.qpcr_distribute_v_button.setText(tr("distribute_v"))
        self.qpcr_delete_selected_button.setText(tr("delete"))
        if hasattr(self, "qpcr_instrument_label"):
            self.qpcr_instrument_label.setText(tr("instrument"))
            self.qpcr_import_group.setTitle(tr("import"))
            self.qpcr_preview_label.setText(tr("v9_preview"))
            self.qpcr_analysis_group.setTitle(tr("analysis"))
            self.qpcr_group_label.setText(tr("groups"))
            self.qpcr_export_hint.setText(tr("export_qpcr_hint"))
        if hasattr(self, "qpcr_context_stack"):
            qpcr_step = 3 if self.qpcr_context_stack.currentIndex() == 2 else self.qpcr_context_stack.currentIndex()
            self.set_qpcr_step(qpcr_step, emit_signal=False)
        if hasattr(self, "wb_context_stack"):
            self.set_wb_step(self.wb_context_stack.currentIndex(), emit_signal=False)
        if hasattr(self, "ihc_context_stack"):
            self.set_ihc_step(self.ihc_context_stack.currentIndex(), emit_signal=False)
        if hasattr(self, "brightfield_context_stack"):
            self.set_brightfield_step(self.brightfield_context_stack.currentIndex(), emit_signal=False)
        self.wb_apply_crop_button.setText(tr("apply_crop"))
        self.wb_roi_mode_button.setText(tr("select_roi"))
        self.wb_full_blot_button.setText(tr("full_length_blot"))
        self.wb_move_image_button.setText(tr("move_image"))
        self.wb_box_select_text_button.setText(tr("box_select_text"))
        self.wb_lane_anchor_button.setText(tr("lane_anchors"))
        self.wb_auto_lane_button.setText(tr("auto_detect_lanes"))
        self.wb_marker_anchor_button.setText(tr("marker_anchors"))
        self.wb_clear_anchors_button.setText(tr("clear_anchors"))
        self.wb_flip_h_button.setText(tr("flip_h"))
        self.wb_flip_v_button.setText(tr("flip_v"))
        self.wb_preview_layout_button.setText(tr("preview_layout"))
        self.wb_apply_layout_button.setText(tr("apply"))
        self.wb_reset_button.setText(tr("reset_original"))
        self.wb_undo_button.setText(tr("v9_undo"))
        self.wb_redo_button.setText(tr("redo"))
        self.wb_add_band_row_button.setText("+ " + tr("band_row"))
        self.wb_add_group_level_button.setText("+ " + tr("add_group_level"))
        self.wb_add_condition_row_button.setText("+ " + tr("add_condition_row"))
        self.wb_density_measure_button.setText(tr("measure"))
        self.wb_density_copy_button.setText(tr("copy_full_table"))
        self.wb_density_to_composer_button.setText(tr("to_composer"))
        self.wb_density_chart_toggle.setText(tr("chart_preview"))
        self.wb_stats_run_button.setText(tr("run_statistics"))
        self.wb_stats_add_to_layout_button.setText(tr("add_plot_to_layout"))
        self.wb_stats_chart_toggle.setText(tr("chart_preview"))
        self.wb_stats_apply_style_button.setText(tr("apply"))
        self.wb_export_to_composer_button.setText(tr("export_selected_to_composer"))
        self.wb_export_button.setText(tr("export_figure"))
        self.wb_export_source_checkbox.setText(tr("include_source_data"))
        self.wb_strip_up_button.setText(tr("up"))
        self.wb_strip_down_button.setText(tr("down"))
        self.wb_strip_delete_button.setText(tr("remove_selected"))
        self.wb_import_strip_button.setText("+ " + tr("import"))
        MODULE_PLACEHOLDERS["composer"]["detail"] = tr("composer_detail")

    def set_journal_preset(self, preset_name: str) -> None:
        if not hasattr(self, "journal_preset_combo"):
            return
        self.journal_preset_combo.blockSignals(True)
        if self.journal_preset_combo.findText(preset_name) >= 0:
            self.journal_preset_combo.setCurrentText(preset_name)
        else:
            self.journal_preset_combo.setCurrentText("Nature")
        self.journal_preset_combo.blockSignals(False)

    def current_journal_preset(self) -> str:
        return self.journal_preset_combo.currentText() or "Nature"

    def _move_journal_preset_controls(self, module_id: str) -> None:
        for target_layout in [self.top_row_layout, self.wb_bottom_layout, self.qpcr_import_layout]:
            index = target_layout.indexOf(self.journal_preset_container)
            if index >= 0:
                target_layout.takeAt(index)
        self.journal_preset_container.setVisible(False)

    def set_qpcr_step(self, index: int, emit_signal: bool = True) -> None:
        index = max(0, min(index, len(self.qpcr_step_buttons) - 1))
        labels = self._qpcr_step_labels()
        for button_index, button in enumerate(self.qpcr_step_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            prefix = "✓ " if button_index < index else ("• " if button_index == index else "○ ")
            button.setText(prefix + labels[button_index])
            button.blockSignals(False)
        if hasattr(self, "qpcr_context_stack"):
            self.qpcr_context_stack.setCurrentIndex(0 if index == 0 else (2 if index == 3 else 1))
        self._sync_qpcr_export_action_visibility(index)
        if emit_signal:
            self.qpcr_step_changed.emit(index)

    def set_ihc_step(self, index: int, emit_signal: bool = True) -> None:
        index = max(0, min(index, len(self.ihc_step_buttons) - 1))
        labels = self._ihc_step_labels()
        for button_index, button in enumerate(self.ihc_step_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.setText(labels[button_index])
            button.blockSignals(False)
        self.ihc_workflow_group.setVisible(index == 1)
        self.ihc_context_stack.setCurrentIndex(index)
        self._sync_ihc_guided_mode()
        if emit_signal:
            self.ihc_step_changed.emit(index)

    def set_brightfield_step(self, index: int, emit_signal: bool = True) -> None:
        index = max(0, min(index, len(self.brightfield_step_buttons) - 1))
        labels = self._brightfield_step_labels()
        for button_index, button in enumerate(self.brightfield_step_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.setText(labels[button_index])
            button.blockSignals(False)
        self.brightfield_context_stack.setCurrentIndex(index)
        self._sync_brightfield_guided_mode()
        self._sync_brightfield_measure_type()
        if emit_signal:
            self.brightfield_step_changed.emit(index)

    def _sync_brightfield_measure_type(self, *_args) -> None:
        index = self.brightfield_measure_type_combo.currentIndex()
        if index<3:self.brightfield_measure_mode_stack.setCurrentIndex(index)
        self.brightfield_workflow_group.setVisible(self.brightfield_context_stack.currentIndex() == 2 and index == 1)
        self._sync_he_fiber_tuning_visibility()

    def _sync_he_fiber_tuning_visibility(self) -> None:
        visible = self.current_module == "brightfield" and str(self.brightfield_measure_type_combo.currentData() or "") == "fiber"
        self.brightfield_he_fiber_tuning_group.setVisible(visible)

    def set_wb_step(self, index: int, emit_signal: bool = True) -> None:
        index = max(0, min(index, WB_STEP_EXPORT))
        labels = self._wb_step_labels()
        hints = self._wb_step_hints()
        self.wb_context_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.wb_step_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            prefix = "✓ " if button_index < index else ("● " if button_index == index else "○ ")
            button.setText(prefix + labels[button_index])
            button.blockSignals(False)
        self.wb_step_hint.setText(hints[index])
        if emit_signal:
            self.wb_step_changed.emit(index)

    def wb_export_format(self) -> str:
        if self.wb_export_svg_radio.isChecked():
            return "svg"
        if self.wb_export_tiff_radio.isChecked():
            return "tiff"
        if self.wb_export_png_radio.isChecked():
            return "png"
        return "pdf"

    def wb_export_dpi(self) -> int:
        return int(self.wb_export_dpi_combo.currentText())

    def wb_export_include_source(self) -> bool:
        return self.wb_export_source_checkbox.isChecked()




    def set_module(self, module_id: str) -> None:
        content = localized_module_placeholder(module_id)
        self.title.setText(content["title"].upper())
        self.detail.setText(content["detail"])
        is_qpcr = module_id == "qpcr"
        is_wb = module_id == "wb"
        is_ihc = module_id == "ihc"
        is_brightfield = module_id == "brightfield"
        self.current_module = module_id
        self._move_journal_preset_controls(module_id)
        is_composer = module_id == "composer"
        self.title.setVisible(not is_wb and not is_qpcr and not is_composer)
        self.detail.setVisible(not is_wb and not is_qpcr and not is_composer)
        self.import_button.setVisible(is_qpcr)
        self.secondary_button.setVisible(is_qpcr)
        self.instrument_combo.setVisible(is_qpcr)
        self.journal_preset_container.setVisible(False)
        self.journal_preset_label.setVisible(False)
        self.journal_preset_combo.setVisible(False)
        self.qpcr_step_nav.setVisible(is_qpcr)
        self.reference_label.setVisible(is_qpcr)
        self.reference_combo.setVisible(is_qpcr)
        self.control_label.setVisible(is_qpcr)
        self.control_combo.setVisible(is_qpcr)
        self.copy_summary_button.setVisible(is_qpcr)
        self.qpcr_to_composer_button.setVisible(False)
        self.run_analysis_button.setVisible(is_qpcr)
        self.qpcr_context_panel.setVisible(is_qpcr)
        for widget in self.qpcr_layout_tool_widgets:
            widget.setVisible(False)
        self.preview_table.setVisible(is_qpcr)
        self.qpcr_group_table.setVisible(is_qpcr)
        self.summary.setVisible(is_qpcr)
        self.ihc_context_panel.setVisible(is_ihc)
        self.brightfield_context_panel.setVisible(is_brightfield)
        self.ihc_positive_threshold_spin.setVisible(is_ihc)
        self.ihc_weak_threshold_spin.setVisible(is_ihc)
        self.ihc_moderate_threshold_spin.setVisible(is_ihc)
        self.ihc_strong_threshold_spin.setVisible(is_ihc)
        self.ihc_min_area_spin.setVisible(is_ihc)
        self.ihc_run_analysis_button.setVisible(is_ihc)
        self.ihc_copy_table_button.setVisible(is_ihc)
        self.ihc_add_outputs_button.setVisible(is_ihc)
        self.ihc_results_table.setVisible(is_ihc)
        self.ihc_summary_label.setVisible(is_ihc)
        self.ihc_stats_metric_combo.setVisible(is_ihc)
        self.ihc_stats_group_table.setVisible(is_ihc)
        self.ihc_stats_reference_group_combo.setVisible(is_ihc)
        self.ihc_stats_test_combo.setVisible(is_ihc)
        self.ihc_stats_run_button.setVisible(is_ihc)
        self.ihc_stats_result_table.setVisible(is_ihc)
        self.ihc_copy_stats_button.setVisible(is_ihc)
        self.ihc_export_stats_csv_button.setVisible(is_ihc)
        self.ihc_stats_summary_label.setVisible(is_ihc)
        self.ihc_stats_chart_container.setVisible(is_ihc)
        self.brightfield_segmentation_mode_combo.setVisible(is_brightfield)
        self.brightfield_preview_mask_button.setVisible(is_brightfield)
        self.brightfield_run_analysis_button.setVisible(is_brightfield)
        self.brightfield_he_fiber_calibrate_button.setVisible(is_brightfield)
        self.brightfield_he_fiber_calibration_status_label.setVisible(is_brightfield)
        self.brightfield_he_fiber_scale_status_label.setVisible(is_brightfield)
        self.brightfield_he_fiber_scale_length_spin.setVisible(is_brightfield)
        self.brightfield_he_fiber_scale_um_per_px_spin.setVisible(is_brightfield)
        self.brightfield_he_fiber_draw_scale_line_button.setVisible(is_brightfield)
        self.brightfield_he_fiber_apply_scale_button.setVisible(is_brightfield)
        self.brightfield_he_fiber_refresh_results_button.setVisible(is_brightfield)
        self.brightfield_he_fiber_reset_scale_button.setVisible(is_brightfield)
        self._sync_he_fiber_tuning_visibility()
        # This is a stacked measurement page. Its stack owns visibility;
        # showing it during a module refresh paints it over the active task.
        self.brightfield_ai_object_count_button.setVisible(is_brightfield)
        self.brightfield_ai_results_table.setVisible(is_brightfield)
        self.brightfield_ai_fiber_area_table.setVisible(is_brightfield)
        self.brightfield_copy_ai_results_button.setVisible(is_brightfield)
        self.brightfield_ai_summary_label.setVisible(is_brightfield)
        self.brightfield_copy_table_button.setVisible(is_brightfield)
        self.brightfield_add_outputs_button.setVisible(is_brightfield)
        self.brightfield_results_table.setVisible(is_brightfield)
        self.brightfield_summary_label.setVisible(is_brightfield)
        self.brightfield_stats_metric_combo.setVisible(is_brightfield)
        self.brightfield_stats_group_table.setVisible(is_brightfield)
        self.brightfield_stats_reference_group_combo.setVisible(is_brightfield)
        self.brightfield_stats_test_combo.setVisible(is_brightfield)
        self.brightfield_stats_plot_source_combo.setVisible(is_brightfield)
        self.brightfield_stats_run_button.setVisible(is_brightfield)
        self.brightfield_stats_result_table.setVisible(is_brightfield)
        self.brightfield_copy_stats_button.setVisible(is_brightfield)
        self.brightfield_copy_ai_stats_button.setVisible(is_brightfield)
        self.brightfield_export_ai_stats_csv_button.setVisible(is_brightfield)
        self.brightfield_ai_stats_result_table.setVisible(is_brightfield)
        self.brightfield_ai_stats_summary_label.setVisible(is_brightfield)
        self.brightfield_export_stats_csv_button.setVisible(is_brightfield)
        self.brightfield_stats_summary_label.setVisible(is_brightfield)
        self.brightfield_stats_chart_container.setVisible(is_brightfield)
        for widget in self.wb_row_widgets:
            widget.setVisible(module_id == "wb")
        if module_id == "wb":
            self.setFixedHeight(92)
        elif is_qpcr:
            self._set_qpcr_expanding_height()
            self.set_qpcr_layout_mode(False)
        elif is_ihc or is_brightfield:
            self.setFixedHeight(430)
        else:
            self.setFixedHeight(112)

    def take_ihc_context_panel(self) -> QWidget:
        layout = self.layout()
        if layout is not None:
            layout.removeWidget(self.ihc_context_panel)
        self.ihc_context_panel.setParent(None)
        return self.ihc_context_panel

    def take_brightfield_context_panel(self) -> QWidget:
        layout = self.layout()
        if layout is not None:
            layout.removeWidget(self.brightfield_context_panel)
        self.brightfield_context_panel.setParent(None)
        return self.brightfield_context_panel


    def set_qpcr_layout_tools_panel(self, panel: QWidget) -> None:
        layout = self.layout()
        if layout is None:
            return
        if self.qpcr_layout_tools_panel is not None:
            layout.removeWidget(self.qpcr_layout_tools_panel)
            self.qpcr_layout_tools_panel.setParent(None)
        self.qpcr_layout_tools_panel = panel
        panel.setParent(self)
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(16777215)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        context_index = layout.indexOf(self.qpcr_context_panel)
        layout.insertWidget(context_index + 1 if context_index >= 0 else 0, panel, 1)
        panel.setVisible(False)

    def set_qpcr_layout_mode(self, enabled: bool) -> None:
        if self.current_module != "qpcr":
            return
        self._qpcr_layout_mode = bool(enabled)
        self.qpcr_context_panel.setVisible(not enabled)
        if self.qpcr_layout_tools_panel is not None:
            self.qpcr_layout_tools_panel.setVisible(enabled)
        compact_hidden_widgets = [
            self.title,
            self.detail,
            self.import_button,
            self.secondary_button,
            self.instrument_combo,
            self.journal_preset_container,
            self.reference_label,
            self.reference_combo,
            self.control_label,
            self.control_combo,
            self.run_analysis_button,
            self.copy_summary_button,
            self.qpcr_to_composer_button,
            self.qpcr_preview_button,
            self.qpcr_layout_button,
            self.qpcr_add_text_button,
            self.qpcr_add_shape_button,
            self.qpcr_same_size_button,
            self.qpcr_align_row_button,
            self.qpcr_align_column_button,
            self.qpcr_distribute_h_button,
            self.qpcr_distribute_v_button,
            self.qpcr_delete_selected_button,
            self.preview_table,
            self.qpcr_group_table,
            self.summary,
        ]
        if enabled:
            for widget in compact_hidden_widgets:
                widget.setVisible(False)
            self.qpcr_step_nav.setVisible(True)
            self._set_qpcr_expanding_height()
            return
        self.title.setVisible(False)
        self.detail.setVisible(False)
        self.import_button.setVisible(True)
        self.secondary_button.setVisible(True)
        self.instrument_combo.setVisible(True)
        self.journal_preset_container.setVisible(False)
        self.reference_label.setVisible(True)
        self.reference_combo.setVisible(True)
        self.control_label.setVisible(True)
        self.control_combo.setVisible(True)
        self.run_analysis_button.setVisible(True)
        self.copy_summary_button.setVisible(True)
        self._sync_qpcr_export_action_visibility()
        self.qpcr_preview_button.setVisible(False)
        self.qpcr_layout_button.setVisible(False)
        self.qpcr_add_text_button.setVisible(False)
        self.qpcr_add_shape_button.setVisible(False)
        self.qpcr_same_size_button.setVisible(False)
        self.qpcr_align_row_button.setVisible(False)
        self.qpcr_align_column_button.setVisible(False)
        self.qpcr_distribute_h_button.setVisible(False)
        self.qpcr_distribute_v_button.setVisible(False)
        self.qpcr_delete_selected_button.setVisible(False)
        self.preview_table.setVisible(True)
        self.qpcr_group_table.setVisible(True)
        self.summary.setVisible(True)
        self.qpcr_step_nav.setVisible(True)
        self._set_qpcr_expanding_height()



    def populate_qpcr(
        self,
        raw_rows: list[dict],
        targets: list[str],
        groups: list[str],
        summary: str,
        suggested_reference: str | None = None,
        selected_control: str | None = None,
    ) -> None:
        self.reference_combo.blockSignals(True)
        self.control_combo.blockSignals(True)
        current_reference = self.reference_combo.currentText()
        current_control = self.control_combo.currentText()
        self.reference_combo.clear()
        self.control_combo.clear()
        self.reference_combo.addItems(targets)
        self.control_combo.addItems(groups)
        if suggested_reference in targets:
            self.reference_combo.setCurrentText(str(suggested_reference))
        elif current_reference in targets:
            self.reference_combo.setCurrentText(current_reference)
        if selected_control in groups:
            self.control_combo.setCurrentText(str(selected_control))
        elif current_control in groups:
            self.control_combo.setCurrentText(current_control)
        self.reference_combo.blockSignals(False)
        self.control_combo.blockSignals(False)

        rows = raw_rows[:8]
        self.preview_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("sample", ""),
                row.get("target", ""),
                row.get("ct", ""),
                row.get("qc_flag", ""),
            ]
            for column_index, value in enumerate(values):
                self.preview_table.setItem(row_index, column_index, QTableWidgetItem("" if value is None else str(value)))
        self.summary.setText(summary)
        self.populate_qpcr_group_table(raw_rows)

    def populate_qpcr_group_table(self, rows: list[dict]) -> None:
        existing_groups, existing_included = self.qpcr_group_assignments()
        samples: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            sample = str(row.get("sample", "")).strip()
            group = str(row.get("group", "")).strip() or sample
            if sample and sample not in seen:
                samples.append((sample, group))
                seen.add(sample)
        self.qpcr_group_table.blockSignals(True)
        self.qpcr_group_table.setRowCount(len(samples))
        for row_index, (sample, group) in enumerate(samples):
            sample_item = QTableWidgetItem(sample)
            sample_item.setFlags(sample_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.qpcr_group_table.setItem(row_index, 0, sample_item)
            self.qpcr_group_table.setItem(row_index, 1, QTableWidgetItem(existing_groups.get(sample, group)))
            include = QCheckBox()
            include.setChecked(sample in existing_included if existing_included else True)
            self.qpcr_group_table.setCellWidget(row_index, 2, include)
        self.qpcr_group_table.blockSignals(False)

    def qpcr_group_assignments(self) -> tuple[dict[str, str], set[str]]:
        groups: dict[str, str] = {}
        included: set[str] = set()
        if not hasattr(self, "qpcr_group_table"):
            return groups, included
        for row in range(self.qpcr_group_table.rowCount()):
            sample_item = self.qpcr_group_table.item(row, 0)
            group_item = self.qpcr_group_table.item(row, 1)
            if sample_item is None:
                continue
            sample = sample_item.text()
            groups[sample] = group_item.text().strip() if group_item is not None and group_item.text().strip() else sample
            include_widget = self.qpcr_group_table.cellWidget(row, 2)
            if not isinstance(include_widget, QCheckBox) or include_widget.isChecked():
                included.add(sample)
        return groups, included

    def ihc_analysis_settings(self) -> dict[str, float | int | bool]:
        return {
            "positive_threshold": float(self.ihc_positive_threshold_spin.value()),
            "weak_threshold": float(self.ihc_weak_threshold_spin.value()),
            "moderate_threshold": float(self.ihc_moderate_threshold_spin.value()),
            "strong_threshold": float(self.ihc_strong_threshold_spin.value()),
            "min_tissue_area_px": int(self.ihc_min_area_spin.value()),
            "tissue_mask_enabled": False,
        }

    def set_ihc_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        spins = {
            "positive_threshold": self.ihc_positive_threshold_spin,
            "weak_threshold": self.ihc_weak_threshold_spin,
            "moderate_threshold": self.ihc_moderate_threshold_spin,
            "strong_threshold": self.ihc_strong_threshold_spin,
        }
        for key, spin in spins.items():
            if key in payload and payload[key] is not None:
                spin.setValue(float(payload[key]))
        if "min_tissue_area_px" in payload and payload["min_tissue_area_px"] is not None:
            self.ihc_min_area_spin.setValue(int(payload["min_tissue_area_px"]))

    def brightfield_analysis_settings(self) -> dict[str, float | int | bool | str]:
        return {
            "segmentation_mode": str(self.brightfield_segmentation_mode_combo.currentData() or "hsv"),
            "h_min": float(self.brightfield_h_min_spin.value()),
            "h_max": float(self.brightfield_h_max_spin.value()),
            "s_min": float(self.brightfield_s_min_spin.value()),
            "s_max": float(self.brightfield_s_max_spin.value()),
            "v_min": float(self.brightfield_v_min_spin.value()),
            "v_max": float(self.brightfield_v_max_spin.value()),
            "l_min": float(self.brightfield_l_min_spin.value()),
            "l_max": float(self.brightfield_l_max_spin.value()),
            "a_min": float(self.brightfield_a_min_spin.value()),
            "a_max": float(self.brightfield_a_max_spin.value()),
            "b_min": float(self.brightfield_b_min_spin.value()),
            "b_max": float(self.brightfield_b_max_spin.value()),
            "gray_threshold": float(self.brightfield_gray_threshold_spin.value()),
            "gray_invert": bool(self.brightfield_gray_invert_checkbox.isChecked()),
            "fill_holes": bool(self.brightfield_fill_holes_checkbox.isChecked()),
            "cleanup_min_area_px": int(self.brightfield_cleanup_min_area_spin.value()),
            "min_object_area_px": int(self.brightfield_min_object_area_spin.value()),
            "max_object_area_px": int(self.brightfield_max_object_area_spin.value()),
            "he_fiber_foreground_sensitivity": int(self.brightfield_he_fiber_foreground_combo.currentData() or 50),
            "he_fiber_splitting_sensitivity": int(self.brightfield_he_fiber_splitting_slider.value()),
            "he_fiber_segmentation_method": str(self.brightfield_he_fiber_method_combo.currentData() or "original"),
            'he_fiber_cellpose_diameter_px': self.brightfield_he_fiber_cellpose_diameter_spin.value(),
        }

    def set_brightfield_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        if "segmentation_mode" in payload:
            self._set_combo_data(self.brightfield_segmentation_mode_combo, payload["segmentation_mode"])
            self._sync_brightfield_segmentation_mode()
        numeric = {
            "h_min": self.brightfield_h_min_spin,
            "h_max": self.brightfield_h_max_spin,
            "s_min": self.brightfield_s_min_spin,
            "s_max": self.brightfield_s_max_spin,
            "v_min": self.brightfield_v_min_spin,
            "v_max": self.brightfield_v_max_spin,
            "l_min": self.brightfield_l_min_spin,
            "l_max": self.brightfield_l_max_spin,
            "a_min": self.brightfield_a_min_spin,
            "a_max": self.brightfield_a_max_spin,
            "b_min": self.brightfield_b_min_spin,
            "b_max": self.brightfield_b_max_spin,
            "gray_threshold": self.brightfield_gray_threshold_spin,
        }
        for key, spin in numeric.items():
            if key in payload and payload[key] is not None:
                spin.setValue(float(payload[key]))
        if "gray_invert" in payload:
            self.brightfield_gray_invert_checkbox.setChecked(bool(payload["gray_invert"]))
        if "fill_holes" in payload:
            self.brightfield_fill_holes_checkbox.setChecked(bool(payload["fill_holes"]))
        if "cleanup_min_area_px" in payload and payload["cleanup_min_area_px"] is not None:
            self.brightfield_cleanup_min_area_spin.setValue(int(payload["cleanup_min_area_px"]))
        if "min_object_area_px" in payload and payload["min_object_area_px"] is not None:
            self.brightfield_min_object_area_spin.setValue(int(payload["min_object_area_px"]))
        if "max_object_area_px" in payload and payload["max_object_area_px"] is not None:
            self.brightfield_max_object_area_spin.setValue(int(payload["max_object_area_px"]))
        self.set_brightfield_he_fiber_analysis_settings(payload)

    def _update_he_fiber_splitting_label(self, value: int) -> None:
        self.brightfield_he_fiber_splitting_value_label.setText(str(int(value)))

    def set_brightfield_he_fiber_analysis_settings(self, settings: dict) -> None:
        payload = dict(settings or {})
        method = str(payload.get("he_fiber_segmentation_method") or "original")
        if method not in {"original", "anti_oversegmentation", *HE_FIBER_CELLPOSE_MODELS}:
            method = "original"
        method_index = self.brightfield_he_fiber_method_combo.findData(method)
        self.brightfield_he_fiber_method_combo.blockSignals(True)
        if method_index >= 0:
            self.brightfield_he_fiber_method_combo.setCurrentIndex(method_index)
        self.brightfield_he_fiber_method_combo.blockSignals(False)
        self.brightfield_he_fiber_cellpose_diameter_spin.blockSignals(True)
        self.brightfield_he_fiber_cellpose_diameter_spin.setValue(float(payload.get('he_fiber_cellpose_diameter_px', 90.)))
        self.brightfield_he_fiber_cellpose_diameter_spin.blockSignals(False)
        foreground_raw = payload.get("he_fiber_foreground_sensitivity", 50)
        foreground = 50 if foreground_raw is None else int(foreground_raw)
        nearest = min((35, 50, 65), key=lambda item: abs(item - foreground))
        index = self.brightfield_he_fiber_foreground_combo.findData(nearest)
        self.brightfield_he_fiber_foreground_combo.blockSignals(True)
        if index >= 0:
            self.brightfield_he_fiber_foreground_combo.setCurrentIndex(index)
        self.brightfield_he_fiber_foreground_combo.blockSignals(False)
        splitting_raw = payload.get("he_fiber_splitting_sensitivity", 25)
        splitting = 25 if splitting_raw is None else int(splitting_raw)
        splitting = max(0, min(100, splitting))
        self.brightfield_he_fiber_splitting_slider.blockSignals(True)
        self.brightfield_he_fiber_splitting_slider.setValue(splitting)
        self.brightfield_he_fiber_splitting_slider.blockSignals(False)
        self._update_he_fiber_splitting_label(splitting)
        self._sync_he_fiber_tuning_enabled()

    def _sync_he_fiber_tuning_enabled(self) -> None:
        self.brightfield_he_fiber_model_hint.refresh()
        anti = str(self.brightfield_he_fiber_method_combo.currentData() or "original") == "anti_oversegmentation"
        cellpose = self.brightfield_he_fiber_method_combo.currentData() in HE_FIBER_CELLPOSE_MODELS
        self.he_fiber_tuning_form.setRowVisible(self.brightfield_he_fiber_cellpose_diameter_spin, cellpose)
        self.brightfield_he_fiber_cellpose_diameter_spin.setEnabled(cellpose)
        self.brightfield_he_fiber_method_combo.setEnabled(True)
        self.brightfield_he_fiber_foreground_combo.setEnabled(anti)
        self.brightfield_he_fiber_splitting_slider.setEnabled(anti)
        self.brightfield_he_fiber_method_combo.setToolTip(tr('he_fiber_boundary_hint' if self.brightfield_he_fiber_method_combo.currentData()==HE_FIBER_METHOD_BOUNDARY else "he_fiber_method_tooltip"))
        if anti:
            self.brightfield_he_fiber_foreground_combo.setToolTip(tr("he_fiber_foreground_tooltip"))
            self.brightfield_he_fiber_splitting_slider.setToolTip(tr("he_fiber_splitting_tooltip"))
            self.brightfield_he_fiber_tuning_group.setToolTip(tr("he_fiber_anti_ignores_manual_tooltip"))
        else:
            original_tip = tr("he_fiber_original_tuning_disabled_tooltip")
            self.brightfield_he_fiber_foreground_combo.setToolTip(original_tip)
            self.brightfield_he_fiber_splitting_slider.setToolTip(original_tip)
            self.brightfield_he_fiber_tuning_group.setToolTip(tr("he_fiber_method_tooltip"))

    def _load_he_fiber_reference(self) -> None:
        method = self.brightfield_he_fiber_method_combo.currentData()
        if method not in HE_FIBER_CELLPOSE_MODELS:
            return
        self.brightfield_he_fiber_cellpose_diameter_spin.setValue(50. if method==HE_FIBER_METHOD_BOUNDARY else 90.)
        self.brightfield_min_object_area_spin.setValue(60)
        self.brightfield_max_object_area_spin.setValue(0)

    def _sync_cellpose_controls(self, *_args) -> None:
        strategy=self.brightfield_cellpose_strategy_combo.currentData()
        enabled=strategy!='traditional'
        if enabled:self._brightfield_cellpose_strategy=strategy
        self.brightfield_cellpose_reference_row.setEnabled(enabled)
        self.brightfield_cellpose_diameter_spin.setEnabled(enabled)
        self.brightfield_cellpose_min_size_spin.setEnabled(enabled)
        for name in ('threshold', 'elongated_contrast_threshold', 'min_brightness', 'min_circularity'):
            getattr(self, f'brightfield_bright_round_cell_{name}_spin').setEnabled(not enabled)

    def _load_cellpose_cell_reference(self) -> None:
        self.brightfield_cellpose_diameter_spin.setValue(float(self.brightfield_cellpose_reference_combo.currentData()))

    def bright_round_cell_analysis_settings(self) -> dict:
        return {
            "backend": "traditional" if self.brightfield_cellpose_strategy_combo.currentData()=='traditional' else "cellpose",
            "model_diameter_px": self.brightfield_cellpose_diameter_spin.value(),
            "model_min_size_px": self.brightfield_cellpose_min_size_spin.value(),
            "cellpose_strategy": self._brightfield_cellpose_strategy,
            "residual_threshold": float(self.brightfield_bright_round_cell_threshold_spin.value()),
            "elongated_contrast_threshold": float(
                self.brightfield_bright_round_cell_elongated_contrast_threshold_spin.value()
            ),
            "min_mean_intensity": float(self.brightfield_bright_round_cell_min_brightness_spin.value()),
            "min_circularity": float(self.brightfield_bright_round_cell_min_circularity_spin.value()),
            "min_diameter_um": float(self.brightfield_bright_round_cell_min_diameter_um_spin.value()),
            "max_diameter_um": float(self.brightfield_bright_round_cell_max_diameter_um_spin.value()),
        }

    def set_cell_analysis_settings(self, settings: dict) -> None:
        self.brightfield_cellpose_diameter_spin.setValue(float(settings.get('model_diameter_px',30)))
        self.brightfield_cellpose_min_size_spin.setValue(int(settings.get('model_min_size_px',60)))
        self._brightfield_cellpose_strategy=settings.get('cellpose_strategy','general')
        if self._brightfield_cellpose_strategy not in ('general','local_contrast'):self._brightfield_cellpose_strategy='general'
        choice=self._brightfield_cellpose_strategy if settings.get('backend')=='cellpose' else 'traditional'
        self.brightfield_cellpose_strategy_combo.setCurrentIndex(self.brightfield_cellpose_strategy_combo.findData(choice))
        self._sync_cellpose_controls()
        controls = {
            "residual_threshold": ("threshold", 18.0),
            "elongated_contrast_threshold": ("elongated_contrast_threshold", 16.0),
            "min_mean_intensity": ("min_brightness", 165.0),
            "min_circularity": ("min_circularity", 0.55),
            "min_diameter_um": ("min_diameter_um", 4.0),
            "max_diameter_um": ("max_diameter_um", 30.0),
        }
        for key, (name, default) in controls.items():
            value = settings.get(key)
            getattr(self, f"brightfield_bright_round_cell_{name}_spin").setValue(float(default if value is None else value))

    def populate_bright_round_cell_results(self, measurement: dict | None) -> None:
        table = self.brightfield_bright_round_cell_results_table
        rows = measurement.get("results", []) if measurement else []
        table.setRowCount(len(rows))
        if not rows:
            self.brightfield_bright_round_cell_summary_label.setText(tr("bright_round_cell_no_results"))
            return
        for index, row in enumerate(rows):
            from core.measurement_scale import apply_length_scale
            area=row.get('area_um2')
            if area is None:
                saved_scale=measurement.get('measurement_scale_bar',{})
                sx=apply_length_scale(1,0,saved_scale);sy=apply_length_scale(0,1,saved_scale)
                area=row.get('area_px',0)*sx*sy if sx>0 and sy>0 else None
            numeric = {
                0: row["id"], 2: row["equivalent_diameter_um"],
                3: row["min_feret_um"], 4: row["max_feret_um"],
                5: row["circularity"], 6: row["center_x_px"], 7: row["center_y_px"],
                10: row.get('max_width_um'), 11: area,
            }
            values = [
                str(row["id"]), tr("bright_round_cell_shape_" + row["shape_class"]),
                *[(f'{numeric[col]:.{decimals}f}' if numeric[col] is not None else '')
                  for col, decimals in ((2,2),(3,2),(4,2),(5,3),(6,1),(7,1))],
                tr("cell_border_truncated" if row.get("border_truncated") else "cell_complete"),
                str(measurement.get("image_name", "")),
                '' if numeric[10] is None else f'{numeric[10]:.2f}',
                '' if numeric[11] is None else f'{numeric[11]:.2f}',
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in numeric:
                    item.setData(EXCEL_VALUE_ROLE, numeric[col])
                table.setItem(index, col, item)
        summary = measurement["summary"]
        if summary.get('reviewed_count') or summary.get('median_equivalent_diameter_um') is None:
            median = summary.get('median_equivalent_diameter_um')
            description=trf('reviewed_cell_summary',
                count=len(rows), reviewed=summary.get('reviewed_count',0),
                median=f'{median:.2f} µm' if median is not None else tr('not_calibrated'))
        else:
            description=str(measurement.get("image_name", "")) + "\n" + trf(
                "bright_round_cell_summary_with_shape",
                count=len(rows), round_count=summary["round_count"],
                elongated_count=summary["elongated_count"],
                border_count=summary.get("border_truncated_count", 0),
                median=summary["median_equivalent_diameter_um"],
            )
        if summary.get('mean_max_width_um') is not None:
            description+='\n'+trf('cell_width_area_summary',n=summary.get('complete_count',0),
                width=f"{summary['mean_max_width_um']:.2f}",
                area=f"{summary['mean_area_um2']:.2f}" if summary.get('mean_area_um2') is not None else '—')
        self.brightfield_bright_round_cell_summary_label.setText(description)

    def populate_ihc_import_controls(self, images: list[dict], active_image_uid: str | None = None) -> None:
        self.ihc_active_image_combo.blockSignals(True)
        self.ihc_active_image_combo.clear()
        active_uid = str(active_image_uid or "").strip()
        active_name = "-"
        for image in images:
            image_uid = str(image.get("uid", "")).strip()
            image_name = str(image.get("source_name", image_uid) or image_uid)
            group_name = str(image.get("group", "")).strip() or "Ungrouped"
            self.ihc_active_image_combo.addItem(f"{image_name}  [{group_name}]", image_uid)
            if image_uid and image_uid == active_uid:
                self.ihc_active_image_combo.setCurrentIndex(self.ihc_active_image_combo.count() - 1)
                active_name = image_name
        if not active_uid and self.ihc_active_image_combo.count() > 0:
            self.ihc_active_image_combo.setCurrentIndex(0)
            active_name = str(self.ihc_active_image_combo.currentText()).split("  [", 1)[0]
        self.ihc_active_image_label.setText(trf("active_image_value", name=active_name))
        self.ihc_active_image_combo.setEnabled(self.ihc_active_image_combo.count() > 0)
        self.ihc_active_image_combo.blockSignals(False)

    def populate_ihc_output_status(self, outputs: list[dict]) -> None:
        if not outputs:
            self.ihc_outputs_status_label.setText(tr("no_composer_outputs"))
            return
        labels = [str(item.get("source_module", item.get("panel_uuid", "output"))) for item in outputs]
        self.ihc_outputs_status_label.setText(
            trf("composer_outputs_count", count=len(outputs)) + "\n" + "\n".join(labels[-4:])
        )

    def populate_brightfield_import_controls(self, images: list[dict], active_image_uid: str | None = None) -> None:
        self.brightfield_active_image_combo.blockSignals(True)
        self.brightfield_active_image_combo.clear()
        active_uid = str(active_image_uid or "").strip()
        active_name = "-"
        active_group = "Ungrouped"
        active_stain = "H&E"
        for image in images:
            image_uid = str(image.get("uid", "")).strip()
            image_name = str(image.get("source_name", image_uid) or image_uid)
            group_name = str(image.get("group", "")).strip() or "Ungrouped"
            stain_type = normalize_brightfield_stain_type(str(image.get("stain_type", "")).strip() or "H&E")
            self.brightfield_active_image_combo.addItem(f"{image_name}  [{group_name}]  ({stain_type})", image_uid)
            if image_uid and image_uid == active_uid:
                self.brightfield_active_image_combo.setCurrentIndex(self.brightfield_active_image_combo.count() - 1)
                active_name = image_name
                active_group = group_name
                active_stain = stain_type
        if not active_uid and self.brightfield_active_image_combo.count() > 0:
            self.brightfield_active_image_combo.setCurrentIndex(0)
            active_name = str(self.brightfield_active_image_combo.currentText()).split("  [", 1)[0]
            if images:
                active_group = str(images[0].get("group", "Ungrouped") or "Ungrouped")
                active_stain = normalize_brightfield_stain_type(str(images[0].get("stain_type", "H&E") or "H&E"))
        self.brightfield_active_image_label.setText(trf("active_image_value", name=active_name))
        self.brightfield_active_image_combo.setEnabled(self.brightfield_active_image_combo.count() > 0)
        self.brightfield_group_edit.setText(active_group)
        if self.brightfield_stain_type_combo.findText(active_stain) >= 0:
            self.brightfield_stain_type_combo.setCurrentText(active_stain)
        self.brightfield_active_image_combo.blockSignals(False)

    def populate_brightfield_output_status(self, outputs: list[dict]) -> None:
        if not outputs:
            self.brightfield_outputs_status_label.setText(tr("no_composer_outputs"))
            return
        labels = [str(item.get("source_module", item.get("panel_uuid", "output"))) for item in outputs]
        self.brightfield_outputs_status_label.setText(
            trf("composer_outputs_count", count=len(outputs)) + "\n" + "\n".join(labels[-4:])
        )

    def populate_ihc_measurements(self, measurements: list[dict], selected_measurement_uuid: str | None = None) -> None:
        target_selection = str(selected_measurement_uuid or "").strip()
        self.ihc_results_table.blockSignals(True)
        self.ihc_results_table.setRowCount(len(measurements))
        for row_index, measurement in enumerate(measurements):
            results = dict(measurement.get("results", {}))
            values = [
                str(measurement.get("image_name", "")),
                str(measurement.get("group", "")),
                f"{float(results.get('positive_area_percent', 0.0)):.2f}",
                f"{float(results.get('mean_dab_intensity', 0.0)):.3f}",
                f"{float(results.get('h_score', 0.0)):.2f}",
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(measurement.get("measurement_uuid", "")).strip())
                self.ihc_results_table.setItem(row_index, column_index, item)
            for col, key in ((2, "positive_area_percent"), (3, "mean_dab_intensity"), (4, "h_score")):
                self.ihc_results_table.item(row_index, col).setData(EXCEL_VALUE_ROLE, results.get(key))
        self.ihc_results_table.blockSignals(False)
        if target_selection:
            self.select_ihc_measurement(target_selection)
        else:
            self.ihc_results_table.setCurrentItem(None)
            self.ihc_results_table.clearSelection()
        if measurements:
            latest = measurements[-1]
            latest_results = dict(latest.get("results", {}))
            latest_reliability = dict(latest.get("reliability", {}))
            latest_status = str(latest.get("reliability_status") or latest_reliability.get("status", "PASS")).strip().upper() or "PASS"
            self.ihc_summary_label.setText(
                trf(
                    "latest_ihc_result_summary",
                    status=latest_status,
                    image=latest.get("image_name", ""),
                    positive_area=float(latest_results.get("positive_area_percent", 0.0)),
                    mean_dab=float(latest_results.get("mean_dab_intensity", 0.0)),
                    h_score=float(latest_results.get("h_score", 0.0)),
                )
            )
        else:
            self.ihc_summary_label.setText(tr("no_ihc_measurements"))

    def populate_brightfield_measurements(self, measurements: list[dict], selected_measurement_uuid: str | None = None) -> None:
        target_selection = str(selected_measurement_uuid or "").strip()
        self.brightfield_results_table.blockSignals(True)
        self.brightfield_results_table.setRowCount(len(measurements))
        for row_index, measurement in enumerate(measurements):
            results = dict(measurement.get("results", {}))
            values = [
                str(measurement.get("image_name", "")),
                str(measurement.get("group", "")),
                str(measurement.get("stain_type", "")),
                str(results.get("segmentation_mode", "")),
                f"{float(results.get('area_fraction_percent', 0.0)):.2f}",
                f"{int(results.get('object_count', 0))}",
                f"{float(results.get('mean_object_area_px', 0.0)):.2f}",
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(measurement.get("measurement_uuid", "")).strip())
                self.brightfield_results_table.setItem(row_index, column_index, item)
            for col, key in ((4, "area_fraction_percent"), (5, "object_count"), (6, "mean_object_area_px")):
                self.brightfield_results_table.item(row_index, col).setData(EXCEL_VALUE_ROLE, results.get(key))
        self.brightfield_results_table.blockSignals(False)
        if target_selection:
            self.select_brightfield_measurement(target_selection)
        else:
            self.brightfield_results_table.setCurrentItem(None)
            self.brightfield_results_table.clearSelection()
        if measurements:
            latest = measurements[-1]
            latest_results = dict(latest.get("results", {}))
            latest_reliability = dict(latest.get("reliability", {}))
            latest_status = str(latest.get("reliability_status") or latest_reliability.get("status", "PASS")).strip().upper() or "PASS"
            self.brightfield_summary_label.setText(
                trf(
                    "latest_brightfield_result_summary",
                    status=latest_status,
                    image=latest.get("image_name", ""),
                    area_fraction=float(latest_results.get("area_fraction_percent", 0.0)),
                    regions_label=BRIGHTFIELD_SEGMENTED_REGIONS_LABEL,
                    object_count=int(latest_results.get("object_count", 0)),
                    mean_area=float(latest_results.get("mean_object_area_px", 0.0)),
                    roi_mean=float(latest_results.get("roi_mean_intensity", 0.0)),
                )
            )
        else:
            self.brightfield_summary_label.setText(tr("no_brightfield_measurements"))

    def populate_brightfield_ai_measurements(self, measurements: list[dict], selected_measurement_uuid: str | None = None) -> None:
        def _median(values: list[float]) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            midpoint = len(ordered) // 2
            if len(ordered) % 2:
                return ordered[midpoint]
            return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0

        target_selection = str(selected_measurement_uuid or "").strip()
        self.brightfield_ai_results_table.blockSignals(True)
        self.brightfield_ai_results_table.setRowCount(len(measurements))
        for row_index, measurement in enumerate(measurements):
            results = dict(measurement.get("results", {}))
            areas = [
                float(area)
                for area in results.get("ai_object_areas_px", [])
                if isinstance(area, (int, float))
            ]
            median_area = _median(areas)
            warnings = list(measurement.get("warnings", []))
            model_name = str(measurement.get("model_name", ""))
            model_hash = str(measurement.get("model_hash", ""))
            if model_hash.startswith("sha256:") and len(model_hash) >= 19:
                model_name = f"{model_name} ({model_hash[7:19]})"
            values = [
                str(measurement.get("image_name", "")),
                str(measurement.get("group", "")),
                str(measurement.get("stain_type", "")),
                str(int(results.get("ai_object_count", 0) or 0)),
                f"{float(results.get('ai_object_mean_area_px', 0.0) or 0.0):.2f}",
                "" if median_area is None else f"{median_area:.2f}",
                model_name,
                "Yes" if bool(measurement.get("include_in_statistics", False)) else "No",
                "WARN" if warnings else "OK",
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(measurement.get("measurement_uuid", "")).strip())
                if column_index == 8 and warnings:
                    item.setToolTip("; ".join(str(warning) for warning in warnings))
                self.brightfield_ai_results_table.setItem(row_index, column_index, item)
            for col, number in ((3, results.get("ai_object_count")), (4, results.get("ai_object_mean_area_px")), (5, median_area)):
                self.brightfield_ai_results_table.item(row_index, col).setData(EXCEL_VALUE_ROLE, number)
        self.brightfield_ai_results_table.blockSignals(False)
        target_measurement = next(
            (item for item in measurements if str(item.get("measurement_uuid", "")).strip() == target_selection),
            measurements[-1] if measurements else None,
        )
        self.populate_brightfield_ai_fiber_areas(target_measurement)
        if measurements:
            latest = measurements[-1]
            latest_results = dict(latest.get("results", {}))
            latest_scale = dict(latest.get("measurement_scale_bar", {}))
            scale_source = str(latest_scale.get("calibration_source", "unknown") or "unknown")
            warning_count = len(list(latest.get("warnings", [])))
            self.brightfield_ai_summary_label.setText(
                trf(
                    "latest_he_fiber_result_summary",
                    image=latest.get("image_name", ""),
                    count_label=BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
                    count=int(latest_results.get("ai_object_count", 0) or 0),
                    mean_area_px=float(latest_results.get("ai_object_mean_area_px", 0.0) or 0.0),
                    mean_area_um2=float(latest_results.get("ai_object_mean_area_um2", 0.0) or 0.0),
                    scale=scale_source,
                    include=tr("yes" if bool(latest.get("include_in_statistics", False)) else "no"),
                    warnings=warning_count,
                )
            )
        else:
            self.brightfield_ai_summary_label.setText(tr("no_he_fiber_results"))

    def populate_brightfield_ai_fiber_areas(self, measurement: dict | None) -> None:
        self.brightfield_ai_fiber_area_table.setRowCount(0)
        if not isinstance(measurement, dict):
            return
        results = dict(measurement.get("results", {}))
        areas = [
            float(area)
            for area in results.get("ai_object_areas_px", [])
            if isinstance(area, (int, float))
        ]
        areas_um2 = [
            float(area)
            for area in results.get("ai_object_areas_um2", [])
            if isinstance(area, (int, float))
        ]
        measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
        image_name = str(measurement.get("image_name", ""))
        group_name = str(measurement.get("group", "Ungrouped")) or "Ungrouped"
        has_snapshot = "measurement_scale_bar" in measurement
        scale = measurement.get("measurement_scale_bar") if has_snapshot else None
        self.brightfield_ai_fiber_area_table.setRowCount(len(areas))
        for row_index, area in enumerate(areas, start=1):
            raw_um2 = areas_um2[row_index - 1] if row_index - 1 < len(areas_um2) else (0.0 if not has_snapshot else None)
            um_text, um_excel = physical_area_text_and_excel(raw_um2, scale, has_snapshot=has_snapshot)
            values = [image_name, group_name, str(row_index), f"{area:.0f}", um_text]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, measurement_uuid)
                self.brightfield_ai_fiber_area_table.setItem(row_index - 1, column_index, item)
            self.brightfield_ai_fiber_area_table.item(row_index - 1, 3).setData(EXCEL_VALUE_ROLE, area)
            self.brightfield_ai_fiber_area_table.item(row_index - 1, 4).setData(EXCEL_VALUE_ROLE, um_excel)

    def measurement_uuid_for_row(self, row: int) -> str:
        item = self.ihc_results_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""

    def selected_ihc_measurement_uuid(self) -> str:
        row = self.ihc_results_table.currentRow()
        if row < 0:
            return ""
        return self.measurement_uuid_for_row(row)

    def brightfield_measurement_uuid_for_row(self, row: int) -> str:
        item = self.brightfield_results_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""

    def selected_brightfield_measurement_uuid(self) -> str:
        row = self.brightfield_results_table.currentRow()
        if row < 0:
            return ""
        return self.brightfield_measurement_uuid_for_row(row)

    def brightfield_ai_measurement_uuid_for_row(self, row: int) -> str:
        item = self.brightfield_ai_results_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""

    def selected_brightfield_ai_measurement_uuid(self) -> str:
        row = self.brightfield_ai_results_table.currentRow()
        if row < 0:
            return ""
        return self.brightfield_ai_measurement_uuid_for_row(row)

    def select_ihc_measurement(self, measurement_uuid: str) -> bool:
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return False
        for row in range(self.ihc_results_table.rowCount()):
            if self.measurement_uuid_for_row(row) != target_uuid:
                continue
            self.ihc_results_table.blockSignals(True)
            self.ihc_results_table.selectRow(row)
            self.ihc_results_table.setCurrentCell(row, 0)
            self.ihc_results_table.blockSignals(False)
            self.ihc_measurement_selected.emit(target_uuid)
            return True
        return False

    def select_brightfield_measurement(self, measurement_uuid: str) -> bool:
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return False
        for row in range(self.brightfield_results_table.rowCount()):
            if self.brightfield_measurement_uuid_for_row(row) != target_uuid:
                continue
            self.brightfield_results_table.blockSignals(True)
            self.brightfield_results_table.selectRow(row)
            self.brightfield_results_table.setCurrentCell(row, 0)
            self.brightfield_results_table.blockSignals(False)
            self.brightfield_measurement_selected.emit(target_uuid)
            return True
        return False

    def select_brightfield_ai_measurement(self, measurement_uuid: str) -> bool:
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return False
        for row in range(self.brightfield_ai_results_table.rowCount()):
            if self.brightfield_ai_measurement_uuid_for_row(row) != target_uuid:
                continue
            self.brightfield_ai_results_table.blockSignals(True)
            self.brightfield_ai_results_table.selectRow(row)
            self.brightfield_ai_results_table.setCurrentCell(row, 0)
            self.brightfield_ai_results_table.blockSignals(False)
            self.brightfield_ai_measurement_selected.emit(target_uuid)
            return True
        return False

    def populate_ihc_stats_controls(self, measurements: list[dict]) -> None:
        from core.measurement_history import latest_measurements,measurement_scope_key
        existing_groups: dict[str, str] = {}
        existing_includes: dict[str, bool] = {}
        for row in range(self.ihc_stats_group_table.rowCount()):
            sample_item = self.ihc_stats_group_table.item(row, 0)
            group_item = self.ihc_stats_group_table.item(row, 1)
            include_widget = self.ihc_stats_group_table.cellWidget(row, 2)
            if sample_item is None:
                continue
            sample_name = sample_item.text()
            if group_item is not None:
                sample_key = str(sample_item.data(Qt.ItemDataRole.UserRole) or sample_name)
                existing_groups[sample_key] = group_item.text().strip() or sample_name
            if isinstance(include_widget, QCheckBox):
                sample_key = str(sample_item.data(Qt.ItemDataRole.UserRole) or sample_name)
                existing_includes[sample_key] = include_widget.isChecked()
        for choices in (existing_groups,existing_includes):
            by_scope={measurement_scope_key(m):choices[m['measurement_uuid']] for m in measurements if m.get('measurement_uuid') in choices}
            for m in latest_measurements(measurements):
                scope=measurement_scope_key(m)
                if scope in by_scope:choices[str(m.get('measurement_uuid') or m.get('image_uid',''))]=by_scope[scope]
        measurements=latest_measurements(measurements)
        samples = [
            (
                str(measurement.get("image_name", "")).strip(),
                str(measurement.get("measurement_uuid", "")).strip() or str(measurement.get("image_uid", "")).strip(),
                str(measurement.get("group", "")).strip() or "Ungrouped",
                bool(dict(measurement.get("reliability", {})).get("include_in_statistics", True)),
            )
            for measurement in measurements
            if str(measurement.get("image_name", "")).strip()
        ]
        self.ihc_stats_group_table.setRowCount(len(samples))
        self.ihc_stats_reference_group_combo.blockSignals(True)
        current_reference = self.ihc_stats_reference_group_combo.currentText().strip()
        self.ihc_stats_reference_group_combo.clear()
        reference_options: list[str] = []
        for row_index, (sample_name, sample_key, default_group, default_include) in enumerate(samples):
            sample_item = QTableWidgetItem(sample_name)
            sample_item.setFlags(sample_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            sample_item.setData(Qt.ItemDataRole.UserRole, sample_key)
            group_name = existing_groups.get(sample_key, default_group)
            self.ihc_stats_group_table.setItem(row_index, 0, sample_item)
            self.ihc_stats_group_table.setItem(row_index, 1, QTableWidgetItem(group_name))
            include = QCheckBox()
            include.setChecked(existing_includes.get(sample_key, default_include) if default_include else False)
            self.ihc_stats_group_table.setCellWidget(row_index, 2, include)
            if group_name not in reference_options:
                reference_options.append(group_name)
        for group_name in reference_options:
            self.ihc_stats_reference_group_combo.addItem(group_name)
        if current_reference and current_reference in reference_options:
            self.ihc_stats_reference_group_combo.setCurrentText(current_reference)
        elif reference_options:
            self.ihc_stats_reference_group_combo.setCurrentText(reference_options[0])
        self.ihc_stats_reference_group_combo.blockSignals(False)

    def populate_brightfield_stats_controls(self, measurements: list[dict], source: str = "traditional") -> None:
        from core.measurement_history import latest_measurements,measurement_scope_key
        existing_groups: dict[str, str] = {}
        existing_includes: dict[str, bool] = {}
        for row in range(self.brightfield_stats_group_table.rowCount()):
            sample_item = self.brightfield_stats_group_table.item(row, 0)
            group_item = self.brightfield_stats_group_table.item(row, 1)
            include_widget = self.brightfield_stats_group_table.cellWidget(row, 2)
            if sample_item is None:
                continue
            sample_name = sample_item.text()
            if group_item is not None:
                sample_key = str(sample_item.data(Qt.ItemDataRole.UserRole) or sample_name)
                existing_groups[sample_key] = group_item.text().strip() or sample_name
            if isinstance(include_widget, QCheckBox):
                sample_key = str(sample_item.data(Qt.ItemDataRole.UserRole) or sample_name)
                existing_includes[sample_key] = include_widget.isChecked()
        for choices in (existing_groups,existing_includes):
            by_scope={measurement_scope_key(m):choices[m['measurement_uuid']] for m in measurements if m.get('measurement_uuid') in choices}
            for m in latest_measurements(measurements):
                scope=measurement_scope_key(m)
                if scope in by_scope:choices[str(m.get('measurement_uuid') or m.get('image_uid',''))]=by_scope[scope]
        measurements=latest_measurements(measurements)
        samples: list[tuple[str, str, str, bool]] = []
        seen_sample_keys: set[str] = set()
        for measurement in measurements:
            default_group = str(measurement.get("group", "")).strip() or "Ungrouped"
            if source == "ai_object_count":
                sample_name = str(measurement.get("image_name", "")).strip() or str(measurement.get("measurement_uuid", "")).strip()
                sample_key = (
                    str(measurement.get("measurement_uuid", "")).strip()
                    or str(measurement.get("image_uid", "")).strip()
                )
                default_include = bool(measurement.get("include_in_statistics", False))
                if not sample_name or not sample_key:
                    continue
                samples.append((sample_name, sample_key, default_group, default_include))
                continue
            sample_name = str(measurement.get("image_name", "")).strip()
            sample_key = str(measurement.get("image_uid", "")).strip()
            if not sample_name or not sample_key or sample_key in seen_sample_keys:
                continue
            seen_sample_keys.add(sample_key)
            samples.append((sample_name, sample_key, default_group, True))
        self.brightfield_stats_group_table.setRowCount(len(samples))
        self.brightfield_stats_reference_group_combo.blockSignals(True)
        current_reference = self.brightfield_stats_reference_group_combo.currentText().strip()
        self.brightfield_stats_reference_group_combo.clear()
        reference_options: list[str] = []
        for row_index, (sample_name, sample_key, default_group, default_include) in enumerate(samples):
            sample_item = QTableWidgetItem(sample_name)
            sample_item.setFlags(sample_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            sample_item.setData(Qt.ItemDataRole.UserRole, sample_key)
            group_name = existing_groups.get(sample_key, default_group)
            self.brightfield_stats_group_table.setItem(row_index, 0, sample_item)
            self.brightfield_stats_group_table.setItem(row_index, 1, QTableWidgetItem(group_name))
            include = QCheckBox()
            include.setChecked(existing_includes.get(sample_key, default_include))
            self.brightfield_stats_group_table.setCellWidget(row_index, 2, include)
            if group_name not in reference_options:
                reference_options.append(group_name)
        for group_name in reference_options:
            self.brightfield_stats_reference_group_combo.addItem(group_name)
        if current_reference and current_reference in reference_options:
            self.brightfield_stats_reference_group_combo.setCurrentText(current_reference)
        elif reference_options:
            self.brightfield_stats_reference_group_combo.setCurrentText(reference_options[0])
        self.brightfield_stats_reference_group_combo.blockSignals(False)

    def ihc_stats_group_assignments(self) -> dict[str, str]:
        assignments: dict[str, str] = {}
        for row in range(self.ihc_stats_group_table.rowCount()):
            include_widget = self.ihc_stats_group_table.cellWidget(row, 2)
            if isinstance(include_widget, QCheckBox) and not include_widget.isChecked():
                continue
            sample_item = self.ihc_stats_group_table.item(row, 0)
            group_item = self.ihc_stats_group_table.item(row, 1)
            if sample_item is None:
                continue
            sample_key = str(sample_item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not sample_key:
                continue
            group_name = group_item.text().strip() if group_item is not None else ""
            assignments[sample_key] = group_name or sample_item.text().strip() or sample_key
        return assignments

    def brightfield_stats_group_assignments(self) -> dict[str, str]:
        assignments: dict[str, str] = {}
        for row in range(self.brightfield_stats_group_table.rowCount()):
            include_widget = self.brightfield_stats_group_table.cellWidget(row, 2)
            if isinstance(include_widget, QCheckBox) and not include_widget.isChecked():
                continue
            sample_item = self.brightfield_stats_group_table.item(row, 0)
            group_item = self.brightfield_stats_group_table.item(row, 1)
            if sample_item is None:
                continue
            sample_key = str(sample_item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not sample_key:
                continue
            group_name = group_item.text().strip() if group_item is not None else ""
            assignments[sample_key] = group_name or sample_item.text().strip() or sample_key
        return assignments

    def brightfield_stats_plot_source(self) -> str:
        return str(self.brightfield_stats_plot_source_combo.currentData() or "traditional")

    def _sync_brightfield_stats_plot_source_ui(self, *_args) -> None:
        is_ai_source = self.brightfield_stats_plot_source() == "ai_object_count"
        if is_ai_source:
            metric_index = self.brightfield_stats_metric_combo.findData(BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL)
            if metric_index >= 0:
                self.brightfield_stats_metric_combo.setCurrentIndex(metric_index)
        self.brightfield_stats_metric_combo.setEnabled(not is_ai_source)
        self.brightfield_stats_frequency_mode_combo.setEnabled(is_ai_source)
        for data_value in ("frequency_bar", "frequency_line"):
            index = self.brightfield_stats_chart_mode_combo.findData(data_value)
            if index >= 0:
                item = self.brightfield_stats_chart_mode_combo.model().item(index)
                if item is not None:
                    item.setEnabled(is_ai_source)
        if not is_ai_source and self.brightfield_stats_chart_mode_combo.currentData() != "summary":
            self.brightfield_stats_chart_mode_combo.setCurrentIndex(0)

    def populate_ihc_stats_results(self, result: dict) -> None:
        groups = list(result.get("groups", []))
        self.analysis["ihc_statistics"] = dict(result)
        self.ihc_stats_result_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            self.ihc_stats_result_table.setItem(row, 0, QTableWidgetItem(str(group.get("group", ""))))
            self.ihc_stats_result_table.setItem(row, 1, QTableWidgetItem(str(group.get("n", ""))))
            self.ihc_stats_result_table.setItem(row, 2, QTableWidgetItem(f"{float(group.get('mean', 0.0)):.3f}"))
            self.ihc_stats_result_table.setItem(row, 3, QTableWidgetItem(f"{float(group.get('sem', 0.0)):.3f}"))
        labels = [f"{item.get('group1')} vs {item.get('group2')}: {item.get('label')}" for item in result.get("comparisons", [])]
        labels.extend(tr(message) for message in result.get('warnings',[]))
        self.ihc_stats_summary_label.setText("; ".join(labels) if labels else tr("no_comparison_available"))

    def populate_brightfield_stats_results(self, result: dict) -> None:
        groups = list(result.get("groups", []))
        self.analysis["brightfield_statistics"] = dict(result)
        self.brightfield_stats_result_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            self.brightfield_stats_result_table.setItem(row, 0, QTableWidgetItem(str(group.get("group", ""))))
            self.brightfield_stats_result_table.setItem(row, 1, QTableWidgetItem(str(group.get("n", ""))))
            self.brightfield_stats_result_table.setItem(row, 2, QTableWidgetItem(f"{float(group.get('mean', 0.0)):.3f}"))
            self.brightfield_stats_result_table.setItem(row, 3, QTableWidgetItem(f"{float(group.get('sem', 0.0)):.3f}"))
        labels = [f"{item.get('group1')} vs {item.get('group2')}: {item.get('label')}" for item in result.get("comparisons", [])]
        labels.extend(tr(message) for message in result.get('warnings',[]))
        source = str(result.get("source", ""))
        source_label = (
            tr("he_fiber_statistics_label")
            if source == "brightfield_ai_object_count"
            else tr("traditional_brightfield_statistics_label")
        )
        summary = "; ".join(labels) if labels else tr("no_comparison_available")
        self.brightfield_stats_summary_label.setText(
            trf("summary_with_detail", source=source_label, summary=summary)
        )

    def populate_brightfield_ai_statistics_results(self, result: dict) -> None:
        groups = list(result.get("groups", []))
        self.brightfield_ai_stats_result_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            self.brightfield_ai_stats_result_table.setItem(row, 0, QTableWidgetItem(str(group.get("group", ""))))
            self.brightfield_ai_stats_result_table.setItem(row, 1, QTableWidgetItem(str(group.get("n_measurements", ""))))
            self.brightfield_ai_stats_result_table.setItem(row, 2, QTableWidgetItem(f"{float(group.get('mean_ai_object_count', 0.0)):.3f}"))
            self.brightfield_ai_stats_result_table.setItem(row, 3, QTableWidgetItem(f"{float(group.get('median_ai_object_count', 0.0)):.3f}"))
            self.brightfield_ai_stats_result_table.setItem(row, 4, QTableWidgetItem(f"{float(group.get('mean_ai_object_area_px', 0.0)):.3f}"))
            self.brightfield_ai_stats_result_table.setItem(row, 5, QTableWidgetItem(f"{float(group.get('median_ai_object_area_px', 0.0)):.3f}"))
        if int(result.get("n_measurements", 0) or 0) > 0:
            self.brightfield_ai_stats_summary_label.setText(
                trf(
                    "he_fiber_statistics_summary",
                    n=int(result.get("n_measurements", 0)),
                    mean_count=float(result.get("mean_ai_object_count", 0.0)),
                    median_count=float(result.get("median_ai_object_count", 0.0)),
                    mean_area=float(result.get("mean_ai_object_area_px", 0.0)),
                    median_area=float(result.get("median_ai_object_area_px", 0.0)),
                    total_area=float(result.get("total_ai_object_area_px", 0.0)),
                )
            )
        else:
            self.brightfield_ai_stats_summary_label.setText(tr("no_he_fiber_stats"))

    def ihc_results_table_tsv(self) -> str:
        headers = [
            self.ihc_results_table.horizontalHeaderItem(column).text()
            for column in range(self.ihc_results_table.columnCount())
        ]
        lines = ["\t".join(headers)]
        for row in range(self.ihc_results_table.rowCount()):
            lines.append(
                "\t".join(
                    self.ihc_results_table.item(row, column).text()
                    if self.ihc_results_table.item(row, column) is not None
                    else ""
                    for column in range(self.ihc_results_table.columnCount())
                )
            )
        return "\n".join(lines)

    def brightfield_results_table_tsv(self) -> str:
        headers = [
            self.brightfield_results_table.horizontalHeaderItem(column).text()
            for column in range(self.brightfield_results_table.columnCount())
        ]
        lines = ["\t".join(headers)]
        for row in range(self.brightfield_results_table.rowCount()):
            lines.append(
                "\t".join(
                    self.brightfield_results_table.item(row, column).text()
                    if self.brightfield_results_table.item(row, column) is not None
                    else ""
                    for column in range(self.brightfield_results_table.columnCount())
                )
            )
        return "\n".join(lines)

    def brightfield_ai_results_table_tsv(self) -> str:
        headers = [
            self.brightfield_ai_results_table.horizontalHeaderItem(column).text()
            for column in range(self.brightfield_ai_results_table.columnCount())
        ]
        lines = ["\t".join(headers)]
        for row in range(self.brightfield_ai_results_table.rowCount()):
            lines.append(
                "\t".join(
                    self.brightfield_ai_results_table.item(row, column).text()
                    if self.brightfield_ai_results_table.item(row, column) is not None
                    else ""
                    for column in range(self.brightfield_ai_results_table.columnCount())
                )
            )
        return "\n".join(lines)

    def ihc_statistics_table_tsv(self) -> str:
        headers = [
            self.ihc_stats_result_table.horizontalHeaderItem(column).text()
            for column in range(self.ihc_stats_result_table.columnCount())
        ]
        lines = ["\t".join(headers)]
        for row in range(self.ihc_stats_result_table.rowCount()):
            lines.append(
                "\t".join(
                    self.ihc_stats_result_table.item(row, column).text()
                    if self.ihc_stats_result_table.item(row, column) is not None
                    else ""
                    for column in range(self.ihc_stats_result_table.columnCount())
                )
            )
        return "\n".join(lines)

    def brightfield_statistics_table_tsv(self) -> str:
        headers = [
            self.brightfield_stats_result_table.horizontalHeaderItem(column).text()
            for column in range(self.brightfield_stats_result_table.columnCount())
        ]
        lines = ["\t".join(headers)]
        for row in range(self.brightfield_stats_result_table.rowCount()):
            lines.append(
                "\t".join(
                    self.brightfield_stats_result_table.item(row, column).text()
                    if self.brightfield_stats_result_table.item(row, column) is not None
                    else ""
                    for column in range(self.brightfield_stats_result_table.columnCount())
                )
            )
        return "\n".join(lines)

    def brightfield_ai_statistics_table_tsv(self) -> str:
        headers = [
            self.brightfield_ai_stats_result_table.horizontalHeaderItem(column).text()
            for column in range(self.brightfield_ai_stats_result_table.columnCount())
        ]
        lines = ["\t".join(headers)]
        for row in range(self.brightfield_ai_stats_result_table.rowCount()):
            lines.append(
                "\t".join(
                    self.brightfield_ai_stats_result_table.item(row, column).text()
                    if self.brightfield_ai_stats_result_table.item(row, column) is not None
                    else ""
                    for column in range(self.brightfield_ai_stats_result_table.columnCount())
                )
            )
        return "\n".join(lines)


    def dragEnterEvent(self, event) -> None:
        suffixes = WB_IMAGE_SUFFIXES if self.current_module == "wb" else COMPOSER_IMAGE_SUFFIXES
        if self.current_module in {"composer", "wb"} and dropped_file_paths(event.mimeData(), suffixes):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        suffixes = WB_IMAGE_SUFFIXES if self.current_module == "wb" else COMPOSER_IMAGE_SUFFIXES
        if self.current_module in {"composer", "wb"} and dropped_file_paths(event.mimeData(), suffixes):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        suffixes = WB_IMAGE_SUFFIXES if self.current_module == "wb" else COMPOSER_IMAGE_SUFFIXES
        paths = dropped_file_paths(event.mimeData(), suffixes)
        if self.current_module in {"composer", "wb"} and paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def configure_wb_image_bounds(self, width: int, height: int) -> None:
        self.wb_x_spin.setRange(0, max(width - 1, 0))
        self.wb_y_spin.setRange(0, max(height - 1, 0))
        self.wb_w_spin.setRange(1, max(width, 1))
        self.wb_h_spin.setRange(1, max(height, 1))
        self.wb_x_spin.setValue(0)
        self.wb_y_spin.setValue(0)
        self.wb_w_spin.setValue(max(width, 1))
        self.wb_h_spin.setValue(max(height, 1))

    def wb_lane_labels(self) -> list[str]:
        return self._split_label_text(self.wb_lane_edit.text())

    def wb_lane_labels_for_count(self) -> list[str]:
        lane_count = self.wb_lane_count_spin.value()
        labels = self.wb_lane_labels()
        if not labels:
            return [str(index + 1) for index in range(lane_count)]
        if len(labels) >= lane_count:
            return labels[:lane_count]
        return labels + [str(index + 1) for index in range(len(labels), lane_count)]

    def wb_marker_labels(self) -> list[str]:
        return self._split_label_text(self.wb_marker_edit.text())

    def _split_label_text(self, text: str) -> list[str]:
        return [item.strip() for item in re.split(r"[,，、;\t\r\n]+", text) if item.strip()]

    def populate_wb_bands(self, crops: list[dict], layout: dict | None = None) -> None:
        layout = layout or {}
        bands = layout.get("bands", [])
        loading_control_index = layout.get("loading_control_index")
        self.wb_band_table.blockSignals(True)
        self.wb_band_table.setColumnCount(4)
        self.wb_band_table.setHorizontalHeaderLabels(
            [tr("number_sign"), tr("protein"), tr("mw_kda"), tr("loading_ctrl")]
        )
        self.wb_band_table.setRowCount(max(len(crops), len(bands)))
        for row in range(self.wb_band_table.rowCount()):
            crop = crops[row] if row < len(crops) else {}
            band = bands[row] if row < len(bands) else {}
            band_id = str(crop.get("crop_id", band.get("crop_id", f"crop_{row + 1}")))
            protein = str(band.get("protein", crop.get("protein", "")))
            mw = str(band.get("mw_kda", ""))
            band_item = QTableWidgetItem(str(row + 1))
            band_item.setToolTip(band_id)
            band_item.setFlags(band_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.wb_band_table.setItem(row, 0, band_item)
            self.wb_band_table.setItem(row, 1, QTableWidgetItem(protein))
            self.wb_band_table.setItem(row, 2, QTableWidgetItem(mw))
            checkbox = QCheckBox()
            checkbox.setChecked(loading_control_index == row or bool(band.get("is_loading_ctrl", False)))
            self.wb_band_table.setCellWidget(row, 3, checkbox)
        self.wb_band_table.blockSignals(False)
        self.wb_loading_control_combo.blockSignals(True)
        current = self.wb_loading_control_combo.currentIndex()
        self.wb_loading_control_combo.clear()
        self.wb_loading_control_combo.addItem("None", None)
        for row in range(self.wb_band_table.rowCount()):
            label = self.wb_band_table.item(row, 1).text() if self.wb_band_table.item(row, 1) else ""
            self.wb_loading_control_combo.addItem(f"Band {row + 1}" + (f" - {label}" if label else ""), row)
        configured_loading = layout.get("loading_control_index")
        if configured_loading is not None:
            index = self.wb_loading_control_combo.findData(configured_loading)
            self.wb_loading_control_combo.setCurrentIndex(max(index, 0))
        elif 0 <= current < self.wb_loading_control_combo.count():
            self.wb_loading_control_combo.setCurrentIndex(current)
        self.wb_loading_control_combo.blockSignals(False)
        self._set_combo_text(self.wb_lane_position_combo, str(layout.get("lane_label_position", "top")))
        self._set_combo_text(self.wb_protein_side_combo, str(layout.get("protein_label_side", "right")))
        self._set_combo_text(self.wb_marker_side_combo, str(layout.get("marker_label_side", "left")))

    def populate_wb_strips(self, strips: list[dict], selected_index: int | None = None) -> None:
        self.wb_strip_table.blockSignals(True)
        self.wb_strip_table.setRowCount(len(strips))
        placed = set(getattr(self, "_wb_placed_strip_ids", []))
        for row, strip in enumerate(strips):
            strip_id = str(strip.get("strip_id", f"strip_{row + 1}"))
            image_name = str(strip.get("original_source") or Path(str(strip.get("image_path", ""))).name)
            protein = str(strip.get("protein_name", ""))
            roi = dict(strip.get("crop_roi", {}))
            cropped = bool(roi and int(roi.get("w", 0)) > 0 and int(roi.get("h", 0)) > 0)
            status = ("★ " if strip_id in placed else "") + ("✓" if cropped else "")
            strip_item = QTableWidgetItem(status or "-")
            strip_item.setFlags(strip_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            image_item = QTableWidgetItem(image_name)
            image_item.setFlags(image_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            pixmap = QPixmap(str(strip.get("image_path", "")))
            if not pixmap.isNull():
                if cropped:
                    pixmap = pixmap.copy(int(roi.get("x", 0)), int(roi.get("y", 0)), int(roi.get("w", pixmap.width())), int(roi.get("h", pixmap.height())))
                image_item.setIcon(QIcon(pixmap.scaled(QSize(120, 42), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
            self.wb_strip_table.setItem(row, 0, strip_item)
            self.wb_strip_table.setItem(row, 1, image_item)
            self.wb_strip_table.setItem(row, 2, QTableWidgetItem(protein))
        if selected_index is not None and 0 <= selected_index < len(strips):
            self.wb_strip_table.selectRow(selected_index)
        self.wb_strip_table.blockSignals(False)

    def populate_wb_density_controls(self, strips: list[dict], lane_labels: list[str]) -> None:
        current_ctrl = self.wb_density_loading_ctrl_combo.currentData()
        current_lane = self.wb_density_reference_lane_combo.currentData()
        self.wb_density_loading_ctrl_combo.blockSignals(True)
        self.wb_density_reference_lane_combo.blockSignals(True)
        self.wb_density_loading_ctrl_combo.clear()
        for strip in strips:
            strip_id = str(strip.get("strip_id", ""))
            label = str(strip.get("protein_name") or strip_id)
            self.wb_density_loading_ctrl_combo.addItem(f"{label}  ({strip_id})", strip_id)
        self.wb_density_reference_lane_combo.clear()
        for index, lane_label in enumerate(lane_labels):
            self.wb_density_reference_lane_combo.addItem(f"{lane_label}  (Lane {index + 1})", index)
        ctrl_index = self.wb_density_loading_ctrl_combo.findData(current_ctrl)
        if ctrl_index >= 0:
            self.wb_density_loading_ctrl_combo.setCurrentIndex(ctrl_index)
        lane_index = self.wb_density_reference_lane_combo.findData(current_lane)
        if lane_index >= 0:
            self.wb_density_reference_lane_combo.setCurrentIndex(lane_index)
        self.wb_density_loading_ctrl_combo.blockSignals(False)
        self.wb_density_reference_lane_combo.blockSignals(False)

    def populate_wb_density_results(self, densitometry_result: dict) -> None:
        measurements = list(densitometry_result.get("measurements", []))
        if not measurements:
            self.wb_density_result_table.setRowCount(0)
            self.wb_density_result_table.setColumnCount(0)
            self.wb_density_qc_label.setText(tr("no_measurements_yet"))
            self.wb_density_qc_label.setStyleSheet("")
            return
        lane_labels = [str(lane.get("lane_label", "")) for lane in measurements[0].get("lanes", [])]
        loading_ctrl_id = str(densitometry_result.get("loading_control_strip_id", ""))
        self.wb_density_result_table.setColumnCount(len(lane_labels))
        self.wb_density_result_table.setHorizontalHeaderLabels(lane_labels)
        self.wb_density_result_table.setRowCount(len(measurements))
        self.wb_density_result_table.setVerticalHeaderLabels([str(item.get("strip_label", "")) for item in measurements])
        qc_messages = []
        for row, measurement in enumerate(measurements):
            for column, lane in enumerate(measurement.get("lanes", [])):
                fold = lane.get("fold")
                text = "N/A"
                if isinstance(fold, (int, float)) and fold == fold:
                    text = f"{float(fold):.2f}"
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                qc_flags = list(lane.get("qc_flags", []))
                if qc_flags:
                    item.setBackground(QColor(180, 120, 60))
                    item.setToolTip(", ".join(qc_flags))
                    qc_messages.append(f"Lane {column + 1} ({measurement.get('strip_label', '')}): {', '.join(qc_flags)}")
                elif str(measurement.get("strip_id", "")) == loading_ctrl_id:
                    item.setBackground(QColor(80, 80, 80))
                self.wb_density_result_table.setItem(row, column, item)
        if qc_messages:
            self.wb_density_qc_label.setStyleSheet("color: #E8A040;")
            self.wb_density_qc_label.setText(trf("qc_issues_prefix", messages="\n".join(qc_messages)))
        else:
            self.wb_density_qc_label.setStyleSheet("color: #80C080;")
            self.wb_density_qc_label.setText(tr("no_qc_issues"))

    def populate_wb_stats_controls(self, densitometry_runs: list[dict]) -> None:
        existing_groups: dict[str, str] = {}
        existing_includes: dict[str, bool] = {}
        for row in range(self.wb_stats_group_table.rowCount()):
            lane_item = self.wb_stats_group_table.item(row, 0)
            group_item = self.wb_stats_group_table.item(row, 1)
            include_widget = self.wb_stats_group_table.cellWidget(row, 2)
            if lane_item is None:
                continue
            lane = lane_item.text()
            if group_item is not None:
                existing_groups[lane] = group_item.text()
            if isinstance(include_widget, QCheckBox):
                existing_includes[lane] = include_widget.isChecked()
        lane_labels: list[str] = []
        from core.wb_statistics import collect_wb_stat_values
        for lane in collect_wb_stat_values(densitometry_runs):
            label=str(lane.get('lane_label',''))
            if label and label not in lane_labels:lane_labels.append(label)
        self.wb_stats_group_table.setRowCount(len(lane_labels))
        current_reference=self.wb_stats_reference_group_combo.currentText()
        self.wb_stats_reference_group_combo.clear()
        for row, label in enumerate(lane_labels):
            lane_item = QTableWidgetItem(label)
            lane_item.setFlags(lane_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.wb_stats_group_table.setItem(row, 0, lane_item)
            self.wb_stats_group_table.setItem(row, 1, QTableWidgetItem(existing_groups.get(label, label)))
            include = QCheckBox()
            include.setChecked(existing_includes.get(label, True))
            self.wb_stats_group_table.setCellWidget(row, 2, include)
            self.wb_stats_reference_group_combo.addItem(existing_groups.get(label, label))
        if self.wb_stats_reference_group_combo.findText(current_reference)>=0:
            self.wb_stats_reference_group_combo.setCurrentText(current_reference)

    def wb_stats_group_assignments(self) -> dict[str, str]:
        assignments: dict[str, str] = {}
        for row in range(self.wb_stats_group_table.rowCount()):
            include_widget = self.wb_stats_group_table.cellWidget(row, 2)
            if isinstance(include_widget, QCheckBox) and not include_widget.isChecked():
                continue
            lane_item = self.wb_stats_group_table.item(row, 0)
            group_item = self.wb_stats_group_table.item(row, 1)
            if lane_item is None or group_item is None:
                continue
            assignments[lane_item.text()] = group_item.text().strip() or lane_item.text()
        return assignments

    def wb_color_combo_value(self, combo: QComboBox, fallback: str = "#111111") -> str:
        text_match = re.search(r"#[0-9A-Fa-f]{6}", combo.currentText())
        if text_match:
            return text_match.group(0).upper()
        data = combo.currentData()
        if data is not None:
            return _normalized_hex_color(data, fallback)
        return _normalized_hex_color(combo.currentText(), fallback)

    def set_wb_color_combo_value(self, combo: QComboBox, color: object, fallback: str = "#111111") -> None:
        normalized = _normalized_hex_color(color, fallback)
        index = combo.findData(normalized)
        if index >= 0:
            combo.setCurrentIndex(index)
            return
        combo.setCurrentText(normalized)

    def wb_stats_plot_style(self) -> dict:
        return {
            "font_family": self.wb_stats_font_combo.currentFont().family(),
            "font_size_pt": float(self.wb_stats_font_size_spin.value()),
            "width_px": int(self.wb_stats_plot_width_spin.value()),
            "height_px": int(self.wb_stats_plot_height_spin.value()),
            "bar_fill_color": self.wb_color_combo_value(self.wb_stats_bar_fill_combo, "#FFFFFF"),
            "bar_edge_color": self.wb_color_combo_value(self.wb_stats_bar_edge_combo, "#333333"),
            "bar_width_fraction": float(self.wb_stats_bar_width_spin.value()),
            "bar_line_width": float(self.wb_stats_bar_line_width_spin.value()),
            "axis_line_width": float(self.wb_stats_axis_line_width_spin.value()),
            "point_shape": self.wb_stats_point_shape_combo.currentText(),
            "point_size": float(self.wb_stats_point_size_spin.value()),
            "point_color": self.wb_color_combo_value(self.wb_stats_point_color_combo, "#999999"),
        }

    def populate_wb_stats_results(self, result: dict) -> None:
        groups = list(result.get("groups", []))
        self.wb_stats_result_table.setColumnCount(4)
        self.wb_stats_result_table.setHorizontalHeaderLabels([tr("group"), "n", tr("mean"), tr("sem")])
        self.wb_stats_result_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            self.wb_stats_result_table.setItem(row, 0, QTableWidgetItem(str(group.get("group", ""))))
            self.wb_stats_result_table.setItem(row, 1, QTableWidgetItem(str(group.get("n", ""))))
            self.wb_stats_result_table.setItem(row, 2, QTableWidgetItem(f"{float(group.get('mean', 0.0)):.3f}"))
            self.wb_stats_result_table.setItem(row, 3, QTableWidgetItem(f"{float(group.get('sem', 0.0)):.3f}"))
        labels = [f"{item.get('group1')} vs {item.get('group2')}: {item.get('label')}" for item in result.get("comparisons", [])]
        labels.extend(tr(message) for message in result.get('warnings',[]))
        self.wb_stats_summary_label.setText("; ".join(labels) if labels else tr("no_comparison_available"))

    def populate_wb_export_items(self, panel_data: dict) -> None:
        items = [("wb_layout", "WB layout", "PNG 1200 DPI")]
        if panel_data.get("wb_layout", {}).get("stats_plot", {}).get("enabled") or panel_data.get("wb_statistics"):
            items.append(("wb_stats_plot", "WB statistics plot", "PNG 1200 DPI"))
        self.wb_export_item_table.setRowCount(len(items))
        for row, (key, label, fmt) in enumerate(items):
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            self.wb_export_item_table.setCellWidget(row, 0, checkbox)
            label_item = QTableWidgetItem(label)
            label_item.setData(Qt.ItemDataRole.UserRole, key)
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.wb_export_item_table.setItem(row, 1, label_item)
            fmt_item = QTableWidgetItem(fmt)
            fmt_item.setFlags(fmt_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.wb_export_item_table.setItem(row, 2, fmt_item)

    def set_wb_export_item_checked(self, key: str, checked: bool) -> None:
        for row in range(self.wb_export_item_table.rowCount()):
            item = self.wb_export_item_table.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                checkbox = self.wb_export_item_table.cellWidget(row, 0)
                if isinstance(checkbox, QCheckBox):
                    checkbox.setChecked(checked)

    def selected_wb_export_items(self) -> list[str]:
        selected = []
        for row in range(self.wb_export_item_table.rowCount()):
            item = self.wb_export_item_table.item(row, 1)
            checkbox = self.wb_export_item_table.cellWidget(row, 0)
            if item is not None and isinstance(checkbox, QCheckBox) and checkbox.isChecked():
                selected.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def selected_wb_strip_index(self) -> int | None:
        rows = self.wb_strip_table.selectionModel().selectedRows() if self.wb_strip_table.selectionModel() else []
        if not rows:
            return None
        return rows[0].row()

    def wb_band_configuration(self) -> list[dict]:
        bands = []
        for row in range(self.wb_band_table.rowCount()):
            protein_item = self.wb_band_table.item(row, 1)
            mw_item = self.wb_band_table.item(row, 2)
            loading_widget = self.wb_band_table.cellWidget(row, 3)
            bands.append(
                {
                    "protein": protein_item.text().strip() if protein_item is not None else "",
                    "mw_kda": mw_item.text().strip() if mw_item is not None else "",
                    "is_loading_ctrl": bool(loading_widget.isChecked()) if isinstance(loading_widget, QCheckBox) else False,
                }
            )
        return bands

    def add_wb_band_config_row(self) -> None:
        row = self.wb_band_table.rowCount()
        self.wb_band_table.insertRow(row)
        band_item = QTableWidgetItem(str(row + 1))
        band_item.setFlags(band_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.wb_band_table.setItem(row, 0, band_item)
        self.wb_band_table.setItem(row, 1, QTableWidgetItem(""))
        self.wb_band_table.setItem(row, 2, QTableWidgetItem(""))
        self.wb_band_table.setCellWidget(row, 3, QCheckBox())

    def show_wb_group_level_editor(self) -> None:
        existing_lines = [line for line in self.wb_group_levels_edit.toPlainText().splitlines() if line.strip()]
        next_level = len(existing_lines) + 1
        new_line = f"Level {next_level}: Group {next_level}A=1-{max(1, self.wb_lane_count_spin.value() // 2)}; Group {next_level}B={max(2, self.wb_lane_count_spin.value() // 2 + 1)}-{self.wb_lane_count_spin.value()}"
        self.wb_group_levels_edit.setPlainText("\n".join(existing_lines + [new_line]))
        self._show_wb_hierarchy_controls(group_levels=True)

    def show_wb_condition_row_editor(self) -> None:
        existing_lines = [line for line in self.wb_condition_rows_edit.toPlainText().splitlines() if line.strip()]
        values = ", ".join(["-" if index % 2 == 0 else "+" for index in range(self.wb_lane_count_spin.value())])
        self.wb_condition_rows_edit.setPlainText("\n".join(existing_lines + [f"Condition {len(existing_lines) + 1}: {values}"]))
        self._show_wb_hierarchy_controls(condition_rows=True)

    def wb_lane_hierarchy(self) -> dict:
        total_lanes = self.wb_hierarchy_total_lanes_spin.value() if self.wb_hierarchy_total_lanes_spin.isVisible() else self.wb_lane_count_spin.value()
        levels = []
        for level_index, line in enumerate([line.strip() for line in self.wb_group_levels_edit.toPlainText().splitlines() if line.strip()], start=1):
            label_part = line
            if ":" in line:
                prefix, label_part = line.split(":", 1)
                digits = "".join(ch for ch in prefix if ch.isdigit())
                if digits:
                    level_index = int(digits)
            groups = []
            for chunk in label_part.split(";"):
                if "=" not in chunk:
                    continue
                label, span = chunk.split("=", 1)
                if "-" not in span:
                    continue
                start, end = span.split("-", 1)
                try:
                    groups.append(
                        {
                            "label": label.strip(),
                            "lane_start": int(start.strip()),
                            "lane_end": int(end.strip()),
                            "show_bracket": True,
                        }
                    )
                except ValueError:
                    continue
            if groups:
                levels.append({"level": level_index, "groups": groups})
        for row_index, line in enumerate([line.strip() for line in self.wb_condition_rows_edit.toPlainText().splitlines() if line.strip()], start=len(levels) + 1):
            if ":" not in line:
                continue
            row_label, values = line.split(":", 1)
            entries = self._split_label_text(values)
            if entries:
                levels.append(
                    {
                        "level": row_index,
                        "type": "condition_row",
                        "row_label": row_label.strip(),
                        "values": entries,
                    }
                )
        return {"total_lanes": total_lanes, "levels": levels} if levels else {}

    def wb_group_gaps(self) -> list[dict]:
        gaps = []
        text = self.wb_group_gap_edit.text().strip()
        if not text:
            return gaps
        for chunk in text.split(";"):
            if ":" not in chunk:
                continue
            lane, width = chunk.split(":", 1)
            try:
                gaps.append({"after_lane": int(lane.strip()), "gap_width_pt": float(width.strip())})
            except ValueError:
                continue
        return gaps

    def populate_wb_hierarchy(self, hierarchy: dict | None, group_gaps: list[dict] | None = None) -> None:
        hierarchy = hierarchy or {}
        group_gaps = group_gaps or []
        levels = hierarchy.get("levels", [])
        group_lines = []
        condition_lines = []
        for level in levels:
            if level.get("type") == "condition_row":
                condition_lines.append(f"{level.get('row_label', '')}: {', '.join(str(value) for value in level.get('values', []))}")
                continue
            groups = []
            for group in level.get("groups", []):
                groups.append(f"{group.get('label', '')}={group.get('lane_start', 1)}-{group.get('lane_end', 1)}")
            if groups:
                group_lines.append(f"Level {level.get('level', len(group_lines) + 1)}: " + "; ".join(groups))
        if hierarchy:
            self._show_wb_hierarchy_controls(group_levels=bool(group_lines), condition_rows=bool(condition_lines))
            self.wb_hierarchy_total_lanes_spin.setValue(int(hierarchy.get("total_lanes", self.wb_lane_count_spin.value())))
            self.wb_group_levels_edit.setPlainText("\n".join(group_lines))
            self.wb_condition_rows_edit.setPlainText("\n".join(condition_lines))
        if group_gaps:
            self.wb_group_gap_edit.setText("; ".join(f"{gap.get('after_lane')}:{gap.get('gap_width_pt')}" for gap in group_gaps))
            self._show_wb_hierarchy_controls(group_levels=True)

    def _show_wb_hierarchy_controls(self, group_levels: bool = False, condition_rows: bool = False) -> None:
        self.wb_total_lanes_label.setVisible(False)
        self.wb_hierarchy_total_lanes_spin.setVisible(True)
        self.wb_group_gap_label.setVisible(False)
        self.wb_group_gap_edit.setVisible(True)
        if group_levels:
            self.wb_group_levels_edit.setVisible(True)
        if condition_rows:
            self.wb_condition_rows_edit.setVisible(True)
    def set_wb_text_toolbar_enabled(self, enabled: bool) -> None:
        for widget in [
            self.wb_text_font_combo,
            self.wb_text_size_spin,
            self.wb_text_bold_button,
            self.wb_text_italic_button,
            self.wb_text_color_combo,
            self.wb_text_rotation_spin,
            self.wb_text_x_spin,
            self.wb_text_y_spin,
            self.wb_apply_text_type_button,
        ]:
            widget.setEnabled(enabled)

    def _set_combo_text(self, combo: QComboBox, text: str) -> None:
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_combo_data(self, combo: QComboBox, value) -> None:
        if combo is None or value is None:
            return
        index = combo.findData(value)
        if index < 0:
            index = combo.findData(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)


class PlaceholderCenter(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.data_panel = DataInputPanel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.data_panel)

