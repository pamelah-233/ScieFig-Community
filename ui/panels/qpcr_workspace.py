from __future__ import annotations

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFontComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.models import QPCRPanel
from core.utils.i18n import tr
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas


def _populate_color_combo(combo: QComboBox, colors: list[str]) -> None:
    combo.setEditable(True)
    for color in colors:
        combo.addItem(color, color)


def _combo_color(combo: QComboBox, fallback: str = "#111111") -> str:
    match = re.search(r"#[0-9A-Fa-f]{6}", combo.currentText())
    if match:
        return match.group(0).upper()
    data = combo.currentData()
    if isinstance(data, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", data):
        return data.upper()
    return fallback


class QPCRLayoutWorkspace(QWidget):
    preview_requested = pyqtSignal()
    add_text_requested = pyqtSignal()
    add_shape_requested = pyqtSignal()
    same_size_requested = pyqtSignal()
    align_row_requested = pyqtSignal()
    align_column_requested = pyqtSignal()
    distribute_h_requested = pyqtSignal()
    distribute_v_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    to_composer_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._form_i18n_labels: list[QLabel] = []
        self._group_i18n_widgets: list[QGroupBox] = []
        self.canvas = QPCRLayoutCanvas()
        self.canvas.scene.selectionChanged.connect(self.sync_controls_from_selection)
        self.tools_panel: QScrollArea | QWidget | None = None
        self.tools_content: QWidget | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.canvas, 1)
        self._build_tools_panel()

    def _build_tools_panel(self) -> QWidget:
        self.tools_panel = QScrollArea()
        self.tools_panel.setMinimumWidth(0)
        self.tools_panel.setMaximumWidth(16777215)
        self.tools_panel.setWidgetResizable(True)
        self.tools_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tools_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content = QWidget()
        self.tools_content = content
        self.tools_panel.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.preview_button = QPushButton("Preview Layout")
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("PrimaryButton")
        self.add_text_button = QPushButton("Add Text")
        self.add_shape_button = QPushButton("Add Shape")
        self.same_size_button = QPushButton("Same Size")
        self.align_row_button = QPushButton("Align Row")
        self.align_column_button = QPushButton("Align Column")
        self.distribute_h_button = QPushButton("Dist H")
        self.distribute_v_button = QPushButton("Dist V")
        self.delete_button = QPushButton("Delete")
        self.to_composer_button = QPushButton("Add qPCR Plots")

        layout.addWidget(self.preview_button)
        layout.addWidget(self.apply_button)
        # Annotation editing now lives in the shared canvas tools. Keep the
        # existing programmatic controls while removing duplicate visible forms.
        for group in [self._group("objects", [self.add_text_button, self.add_shape_button, self.delete_button]),
                      self._group("arrange", [self.same_size_button, self.align_row_button, self.align_column_button, self.distribute_h_button, self.distribute_v_button]),
                      self._build_text_group(),self._build_shape_group()]:
            layout.addWidget(group);group.hide()
        layout.addWidget(self._build_plot_group())
        layout.addWidget(self._group("v9_module_composer", [self.to_composer_button]))
        layout.addStretch(1)

        self.preview_button.clicked.connect(self.preview_requested.emit)
        self.apply_button.clicked.connect(self.apply_selected_style)
        self.add_text_button.clicked.connect(self.add_text_requested.emit)
        self.add_shape_button.clicked.connect(self.add_shape_requested.emit)
        self.same_size_button.clicked.connect(self.same_size_requested.emit)
        self.align_row_button.clicked.connect(self.align_row_requested.emit)
        self.align_column_button.clicked.connect(self.align_column_requested.emit)
        self.distribute_h_button.clicked.connect(self.distribute_h_requested.emit)
        self.distribute_v_button.clicked.connect(self.distribute_v_requested.emit)
        self.delete_button.clicked.connect(self.delete_requested.emit)
        self.to_composer_button.clicked.connect(self.to_composer_requested.emit)
        self.retranslate()
        return self.tools_panel

    def take_tools_panel(self) -> QWidget:
        layout = self.layout()
        if layout is not None and self.tools_panel is not None:
            layout.removeWidget(self.tools_panel)
        content = self.tools_content
        if isinstance(self.tools_panel, QScrollArea):
            self.tools_panel.takeWidget()
        if content is None:
            return QWidget()
        content.setParent(None)
        self.tools_panel = content
        return content

    def _build_text_group(self) -> QGroupBox:
        self.text_font_combo = QFontComboBox()
        self.text_size_spin = QDoubleSpinBox()
        self.text_size_spin.setRange(4.0, 24.0)
        self.text_size_spin.setSingleStep(0.5)
        self.text_size_spin.setValue(10.0)
        self.text_bold_button = QPushButton("B")
        self.text_bold_button.setCheckable(True)
        self.text_italic_button = QPushButton("I")
        self.text_italic_button.setCheckable(True)
        self.text_color_combo = QComboBox()
        _populate_color_combo(self.text_color_combo, ["#111111", "#555555", "#C0392B", "#1F4E79", "#2E8B57", "#7F8C8D", "#8E44AD"])
        self.text_x_spin = QDoubleSpinBox()
        self.text_y_spin = QDoubleSpinBox()
        self.text_rotation_spin = QDoubleSpinBox()
        self.text_rotation_spin.setRange(-180.0, 180.0)
        for spin in [self.text_x_spin, self.text_y_spin]:
            spin.setRange(-10000.0, 10000.0)
        style_row = QHBoxLayout()
        style_row.addWidget(self.text_bold_button)
        style_row.addWidget(self.text_italic_button)
        style_row.addWidget(self.text_color_combo)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._add_i18n_form_row(form, "font", self.text_font_combo)
        self._add_i18n_form_row(form, "size", self.text_size_spin)
        self._add_i18n_form_row(form, "style", style_row)
        self._add_i18n_form_row(form, "x_position", self.text_x_spin)
        self._add_i18n_form_row(form, "y_position", self.text_y_spin)
        self._add_i18n_form_row(form, "rotation", self.text_rotation_spin)
        group = self._i18n_group("text_formatting")
        group.setLayout(form)
        return group

    def _build_shape_group(self) -> QGroupBox:
        self.shape_stroke_width_spin = QDoubleSpinBox()
        self.shape_stroke_width_spin.setRange(0.1, 12.0)
        self.shape_stroke_width_spin.setSingleStep(0.25)
        self.shape_stroke_width_spin.setValue(2.0)
        self.shape_stroke_color_combo = QComboBox()
        self.shape_fill_color_combo = QComboBox()
        _populate_color_combo(self.shape_stroke_color_combo, ["#111111", "#333333", "#C0392B", "#1F4E79", "#2E8B57", "#7F8C8D"])
        _populate_color_combo(self.shape_fill_color_combo, ["#FFFFFF", "#22D7E6", "#D97757", "#999999", "#6B7FB8", "#2E8B57", "#C0392B"])
        self.shape_x_spin = QDoubleSpinBox()
        self.shape_y_spin = QDoubleSpinBox()
        for spin in [self.shape_x_spin, self.shape_y_spin]:
            spin.setRange(-10000.0, 10000.0)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._add_i18n_form_row(form, "line_width", self.shape_stroke_width_spin)
        self._add_i18n_form_row(form, "line_color", self.shape_stroke_color_combo)
        self._add_i18n_form_row(form, "fill", self.shape_fill_color_combo)
        self._add_i18n_form_row(form, "x_position", self.shape_x_spin)
        self._add_i18n_form_row(form, "y_position", self.shape_y_spin)
        group = self._i18n_group("shape_formatting")
        group.setLayout(form)
        return group

    def _build_plot_group(self) -> QGroupBox:
        self.plot_font_combo = QFontComboBox()
        self.plot_font_size_spin = QDoubleSpinBox()
        self.plot_font_size_spin.setRange(4.0, 24.0)
        self.plot_font_size_spin.setSingleStep(0.5)
        self.plot_font_size_spin.setValue(7.0)
        self.plot_width_spin = QSpinBox()
        self.plot_width_spin.setRange(180, 1600)
        self.plot_width_spin.setSingleStep(20)
        self.plot_width_spin.setValue(520)
        self.plot_height_spin = QSpinBox()
        self.plot_height_spin.setRange(140, 1200)
        self.plot_height_spin.setSingleStep(20)
        self.plot_height_spin.setValue(360)
        self.bar_fill_combo = QComboBox()
        self.bar_edge_combo = QComboBox()
        self.point_color_combo = QComboBox()
        _populate_color_combo(self.bar_fill_combo, ["#FFFFFF", "#D97757", "#999999", "#6B7FB8", "#2E8B57", "#C0392B"])
        _populate_color_combo(self.bar_edge_combo, ["#333333", "#111111", "#1F4E79", "#C0392B", "#2E8B57", "#7F8C8D"])
        _populate_color_combo(self.point_color_combo, ["#999999", "#D97757", "#6B7FB8", "#C0392B", "#2E8B57", "#0B7285"])
        self.bar_width_spin = QDoubleSpinBox()
        self.bar_width_spin.setRange(0.1, 0.95)
        self.bar_width_spin.setSingleStep(0.05)
        self.bar_width_spin.setValue(0.58)
        self.bar_line_width_spin = QDoubleSpinBox()
        self.bar_line_width_spin.setRange(0.1, 5.0)
        self.bar_line_width_spin.setSingleStep(0.1)
        self.bar_line_width_spin.setValue(0.9)
        self.axis_line_width_spin = QDoubleSpinBox()
        self.axis_line_width_spin.setRange(0.1, 5.0)
        self.axis_line_width_spin.setSingleStep(0.1)
        self.axis_line_width_spin.setValue(0.6)
        self.point_shape_combo = QComboBox()
        self.point_shape_combo.addItems(["circle", "square", "diamond"])
        self.point_size_spin = QDoubleSpinBox()
        self.point_size_spin.setRange(2.0, 18.0)
        self.point_size_spin.setSingleStep(0.5)
        self.point_size_spin.setValue(5.0)
        for widget in [
            self.plot_font_combo,
            self.plot_font_size_spin,
            self.plot_width_spin,
            self.plot_height_spin,
            self.bar_fill_combo,
            self.bar_edge_combo,
            self.bar_width_spin,
            self.bar_line_width_spin,
            self.axis_line_width_spin,
            self.point_shape_combo,
            self.point_size_spin,
            self.point_color_combo,
        ]:
            widget.setMinimumWidth(96)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._add_i18n_form_row(form, "font", self.plot_font_combo)
        self._add_i18n_form_row(form, "font_size", self.plot_font_size_spin)
        self._add_i18n_form_row(form, "plot_width", self.plot_width_spin)
        self._add_i18n_form_row(form, "plot_height", self.plot_height_spin)
        self._add_i18n_form_row(form, "bar_fill", self.bar_fill_combo)
        self._add_i18n_form_row(form, "bar_edge", self.bar_edge_combo)
        self._add_i18n_form_row(form, "bar_width", self.bar_width_spin)
        self._add_i18n_form_row(form, "bar_line", self.bar_line_width_spin)
        self._add_i18n_form_row(form, "axis_line", self.axis_line_width_spin)
        self._add_i18n_form_row(form, "point_shape", self.point_shape_combo)
        self._add_i18n_form_row(form, "point_size", self.point_size_spin)
        self._add_i18n_form_row(form, "point_color", self.point_color_combo)
        group = self._i18n_group("statistics_plot")
        group.setLayout(form)
        return group

    def _i18n_group(self, key: str) -> QGroupBox:
        group = QGroupBox(tr(key))
        group.setProperty("i18nKey", key)
        self._group_i18n_widgets.append(group)
        return group

    def _add_i18n_form_row(self, form: QFormLayout, key: str, field) -> None:
        label = QLabel(tr(key))
        label.setProperty("i18nKey", key)
        self._form_i18n_labels.append(label)
        form.addRow(label, field)

    def _group(self, title_key: str, buttons: list[QWidget]) -> QGroupBox:
        group = self._i18n_group(title_key)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        for button in buttons:
            layout.addWidget(button)
        return group

    def retranslate(self) -> None:
        self.canvas.retranslate()
        for label in self._form_i18n_labels:
            key = label.property("i18nKey")
            if isinstance(key, str):
                label.setText(tr(key))
        for group in self._group_i18n_widgets:
            key = group.property("i18nKey")
            if isinstance(key, str):
                group.setTitle(tr(key))
        self.preview_button.setText(tr("preview_layout"))
        self.apply_button.setText(tr("apply"))
        self.add_text_button.setText(tr("add_text"))
        self.add_shape_button.setText(tr("add_shape"))
        self.same_size_button.setText(tr("same_size"))
        self.align_row_button.setText(tr("align_row"))
        self.align_column_button.setText(tr("align_column"))
        self.distribute_h_button.setText(tr("distribute_h"))
        self.distribute_v_button.setText(tr("distribute_v"))
        self.delete_button.setText(tr("delete"))
        self.to_composer_button.setText(tr("add_qpcr_plots"))

    def set_panel(self, panel: QPCRPanel) -> None:
        self.canvas.set_panel(panel)
        self.sync_controls_from_selection()

    def plot_style(self) -> dict:
        return {
            "font_family": self.plot_font_combo.currentFont().family(),
            "font_size_pt": float(self.plot_font_size_spin.value()),
            "bar_fill_color": _combo_color(self.bar_fill_combo, "#FFFFFF"),
            "bar_edge_color": _combo_color(self.bar_edge_combo, "#333333"),
            "bar_width_fraction": float(self.bar_width_spin.value()),
            "bar_line_width": float(self.bar_line_width_spin.value()),
            "axis_line_width": float(self.axis_line_width_spin.value()),
            "point_shape": self.point_shape_combo.currentText(),
            "point_size": float(self.point_size_spin.value()) ** 2,
            "point_color": _combo_color(self.point_color_combo, "#999999"),
        }

    def apply_selected_style(self) -> None:
        markers = {str(item.data(0) or "") for item in self.canvas.selected_layout_items()}
        if not markers or "qpcr_layout_plot" in markers:
            self.apply_plot_style()

    def apply_plot_style(self) -> None:
        self.canvas.apply_plot_style_to_selection(
            self.plot_style(),
            width_px=int(self.plot_width_spin.value()),
            height_px=int(self.plot_height_spin.value()),
        )

    def apply_text_style(self) -> None:
        self.canvas.apply_text_style_to_selection(
            {
                "font_family": self.text_font_combo.currentFont().family(),
                "font_size_pt": float(self.text_size_spin.value()),
                "bold": bool(self.text_bold_button.isChecked()),
                "italic": bool(self.text_italic_button.isChecked()),
                "color": _combo_color(self.text_color_combo, "#111111"),
                "x": float(self.text_x_spin.value()),
                "y": float(self.text_y_spin.value()),
                "rotation_deg": float(self.text_rotation_spin.value()),
            }
        )

    def apply_shape_style(self) -> None:
        self.canvas.apply_shape_style_to_selection(
            {
                "stroke_width": float(self.shape_stroke_width_spin.value()),
                "stroke_color": _combo_color(self.shape_stroke_color_combo, "#111111"),
                "fill_color": _combo_color(self.shape_fill_color_combo, "#FFFFFF"),
                "x": float(self.shape_x_spin.value()),
                "y": float(self.shape_y_spin.value()),
            }
        )

    def sync_controls_from_selection(self) -> None:
        selected = self.canvas.selected_layout_items()
        if len(selected) != 1:
            return
        item = selected[0]
        marker = str(item.data(0) or "")
        if marker == "qpcr_layout_plot":
            self.plot_width_spin.setValue(int(round(item.rect().width())) if hasattr(item, "rect") else 520)
            self.plot_height_spin.setValue(int(round(item.rect().height())) if hasattr(item, "rect") else 360)
        elif marker == "qpcr_layout_text":
            font = item.font()
            self.text_font_combo.setCurrentFont(font)
            self.text_size_spin.setValue(font.pointSizeF() if font.pointSizeF() > 0 else 10.0)
            self.text_bold_button.setChecked(font.bold())
            self.text_italic_button.setChecked(font.italic())
            self.text_color_combo.setCurrentText(item.defaultTextColor().name().upper())
            self.text_x_spin.setValue(float(item.pos().x()))
            self.text_y_spin.setValue(float(item.pos().y()))
            self.text_rotation_spin.setValue(float(item.rotation()))
        elif marker == "qpcr_layout_shape":
            self.shape_stroke_width_spin.setValue(float(item.layout_object.style.stroke_width))
            self.shape_x_spin.setValue(float(item.pos().x()))
            self.shape_y_spin.setValue(float(item.pos().y()))
            for combo,color in ((self.shape_stroke_color_combo,item.layout_object.style.stroke_color),
                                (self.shape_fill_color_combo,item.layout_object.style.fill_color)):
                combo.setCurrentText(color.upper())
