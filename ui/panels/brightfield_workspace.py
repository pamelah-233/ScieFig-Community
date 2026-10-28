from __future__ import annotations

from uuid import uuid4

from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QButtonGroup, QComboBox, QDoubleSpinBox, QFontComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from core.layout_scale_bar import apply_native_image_scale_overlay, layout_scale_manual_um_per_px, normalize_scale_bar_template
from core.models import BrightfieldPanel
from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from core.utils.image_formats import BRIGHTFIELD_IMAGE_SUFFIXES, dropped_image_paths
from core.utils.i18n import bind_i18n, retranslate_bound_widgets, retranslate_combo, tr
from ui.layout_adapters.brightfield_layout_adapter import apply_brightfield_layout_objects, brightfield_canvas_spec, brightfield_layout_objects
from ui.panels.placeholder_panel import STEP6_IMPORT_LIMITATION_TEXT
from ui.widgets.layout_editor_canvas import LayoutEditorCanvas
from ui.widgets.preview_canvas import PreviewCanvas, SelectableImageLabel

SCALE_BAR_COLOR_OPTIONS = [
    ("Black", "#111111"),
    ("Dark gray", "#222222"),
    ("White", "#FFFFFF"),
    ("Yellow", "#FFD13B"),
]


def measurement_preview_line_px(scale_bar: dict | None) -> dict:
    scale = dict(scale_bar or {})
    if "display_line_px" in scale:
        raw = scale.get("display_line_px")
        return dict(raw) if isinstance(raw, dict) else {}
    raw = scale.get("line_px")
    return dict(raw) if isinstance(raw, dict) else {}


def measurement_preview_length_label(scale_bar: dict | None) -> str:
    scale = dict(scale_bar or {})
    if bool(scale.get("assumed")) or str(scale.get("calibration_source") or "") == "assumed":
        return ""
    if "calibrated" in scale and not scale.get("calibrated"):
        return ""
    try:
        um_per_px = float(scale.get("um_per_px") or scale.get("um_per_px_x") or 0.0)
    except (TypeError, ValueError):
        um_per_px = 0.0
    if um_per_px <= 0:
        return ""
    try:
        length_um = float(scale.get("length_um", 50.0))
    except (TypeError, ValueError):
        return ""
    return f"{length_um:g} um"


def paint_committed_measurement_scale(image: QImage, scale_bar: dict | None) -> QImage:
    line = measurement_preview_line_px(scale_bar)
    if not line or not {"x1", "y1", "x2", "y2"}.issubset(line):
        return image
    painted = QImage(image)
    painter = QPainter(painted)
    pen = QPen(QColor("#FFD13B"), 3)
    painter.setPen(pen)
    x1 = float(line.get("x1", 0.0))
    y1 = float(line.get("y1", 0.0))
    x2 = float(line.get("x2", 0.0))
    y2 = float(line.get("y2", 0.0))
    painter.drawLine(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))
    label = measurement_preview_length_label(scale_bar)
    if label:
        painter.setPen(QPen(QColor("#FFFFFF"), 1))
        painter.drawText(int(round(min(x1, x2))), int(round(min(y1, y2) - 6)), label)
    painter.end()
    return painted


def apply_brightfield_native_export_scale(image: QImage, panel: BrightfieldPanel, image_uid: str, committed_scale: dict | None = None) -> QImage:
    image_record = {}
    for item in panel.images():
        if str(item.get("uid", "")) == str(image_uid):
            image_record = item
            break
    if not image_record:
        image_record = panel.active_image() or {}
    layout = dict(panel.data.get("brightfield_layout") or {})
    return apply_native_image_scale_overlay(
        image,
        target_id=str(image_uid),
        native_width_px=image.width(),
        native_height_px=image.height(),
        source_metadata=dict(image_record.get("metadata") or {}),
        measurement_scale=dict(image_record.get("measurement_scale_bar") or {}),
        template=layout.get("scale_bar_template"),
        states=layout.get("scale_bars"),
        fallback_painter=(lambda img: paint_committed_measurement_scale(img, committed_scale)),
    )


def brightfield_dropped_paths(mime_data) -> list[str]:
    return dropped_image_paths(mime_data, BRIGHTFIELD_IMAGE_SUFFIXES)


class BrightfieldPreviewCanvas(PreviewCanvas):
    preview_mode_changed = pyqtSignal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._roi_rect_px: QRect | None = None
        self._roi_selection_enabled = False
        self._scaled_base_cache: dict[tuple[int, int, int], QPixmap] = {}
        self._original_pixmap: QPixmap | None = None
        self._overlay_pixmap: QPixmap | None = None
        self._original_title = ""
        self._original_body = ""
        self._overlay_title = ""
        self._overlay_body = ""
        self._preview_mode = "original"
        self._measurement_scale_bar: dict[str, object] = {}
        self._preview_image_uid = ""

    def bind_preview_image(self, image_uid: str) -> None:
        uid = str(image_uid or "")
        if self._preview_image_uid == uid:
            return
        self._preview_image_uid = uid
        self._view_controller.set_content_identity(("image",uid))
        self._original_pixmap = None
        self._overlay_pixmap = None
        self._original_title = ""
        self._original_body = ""
        self._overlay_title = ""
        self._overlay_body = ""
        self._preview_mode = "original"

    def current_preview_mode(self) -> str:
        return self._preview_mode

    def has_original_pixmap(self) -> bool:
        return self._original_pixmap is not None and not self._original_pixmap.isNull()

    def has_overlay_pixmap(self) -> bool:
        return self._overlay_pixmap is not None and not self._overlay_pixmap.isNull()

    def show_original_preview(self) -> None:
        self._apply_preview_mode("original")

    def show_overlay_preview(self) -> None:
        self._apply_preview_mode("overlay")

    def _is_overlay_title(self, title: str) -> bool:
        lowered = title.strip().lower()
        return "overlay" in lowered or "segmentation" in lowered

    def _cached_pixmap_for_mode(self, mode: str) -> tuple[QPixmap | None, str, str, str]:
        if mode == "overlay" and self.has_overlay_pixmap():
            return self._overlay_pixmap, self._overlay_title, self._overlay_body, "overlay"
        if self.has_original_pixmap():
            return self._original_pixmap, self._original_title, self._original_body, "original"
        if self.has_overlay_pixmap():
            return self._overlay_pixmap, self._overlay_title, self._overlay_body, "overlay"
        return None, "", "", "original"

    def _apply_preview_mode(self, mode: str) -> None:
        pixmap, title, body, resolved_mode = self._cached_pixmap_for_mode(mode)
        if pixmap is None:
            self._preview_mode = "original"
            self.preview_mode_changed.emit(self._preview_mode, self.has_overlay_pixmap())
            return
        self._ensure_image_label()
        self._source_pixmap = QPixmap(pixmap)
        if resolved_mode=='original' and hasattr(self,'display_transform'):
            self._source_pixmap=self.display_transform(self._source_pixmap)
        self._preview_mode = resolved_mode
        if title:
            self.title.setText(title)
        if body:
            self.body.setText(body)
        self._refresh_image_display()
        self.preview_mode_changed.emit(self._preview_mode, self.has_overlay_pixmap())

    def _ensure_image_label(self) -> SelectableImageLabel:
        if self.figure_canvas is not None:
            self.figure_canvas.setParent(None)
            self.figure_canvas.deleteLater()
            self.figure_canvas = None
        if self.image_label is None:
            self.image_label = SelectableImageLabel()
            self.image_label.roi_selected.connect(self.roi_selected)
            self.image_label.point_selected.connect(self.point_selected)
            self.image_label.line_selected.connect(self.line_selected)
            self.image_label.line_dragged.connect(self.line_dragged)
            self.paper_layout.addWidget(self.image_label, 1)
            self.image_label.installEventFilter(self)
        return self.image_label

    def set_roi_selection_enabled(self, enabled: bool) -> None:
        self._roi_selection_enabled = bool(enabled)
        if self.image_label is not None:
            self.image_label.selection_enabled = self._roi_selection_enabled

    def set_line_selection_enabled(self, enabled: bool) -> None:
        if self.image_label is not None:
            self.image_label.set_line_selection_enabled(bool(enabled))

    def set_roi_rect(self, rect: QRect | None) -> None:
        self._roi_rect_px = QRect(rect) if rect is not None else None
        self._refresh_image_display()

    def current_roi_rect(self) -> QRect | None:
        return QRect(self._roi_rect_px) if self._roi_rect_px is not None else None

    def clear_roi_rect(self) -> None:
        self.set_roi_rect(None)

    def set_measurement_scale_bar(self, scale_bar: dict[str, object] | None) -> None:
        self._measurement_scale_bar = dict(scale_bar or {})
        self._refresh_image_display()

    def set_pixmap(self, pixmap: QPixmap, title: str = "", body: str = "", enable_roi_selection: bool = False) -> None:
        self.set_display_state(
            pixmap,
            title=title,
            body=body,
            roi_rect=self._roi_rect_px,
            enable_roi_selection=enable_roi_selection,
        )

    def set_display_state(
        self,
        pixmap: QPixmap,
        *,
        title: str = "",
        body: str = "",
        roi_rect: QRect | None = None,
        enable_roi_selection: bool = False,
        is_overlay: bool | None = None,
    ) -> None:
        self.placeholder.hide()
        incoming = QPixmap(pixmap)
        if is_overlay if is_overlay is not None else self._is_overlay_title(title):
            self._overlay_pixmap = incoming
            self._overlay_title = title
            self._overlay_body = body
            next_mode = "overlay"
        else:
            self._original_pixmap = incoming
            self._original_title = title
            self._original_body = body
            self._overlay_pixmap = None
            self._overlay_title = ""
            self._overlay_body = ""
            next_mode = "original"
        self._roi_rect_px = QRect(roi_rect) if roi_rect is not None else None
        image_label = self._ensure_image_label()
        image_label.selection_enabled = bool(enable_roi_selection)
        self._roi_selection_enabled = bool(enable_roi_selection)
        self._apply_preview_mode(next_mode)

    def clear_overlay_pixmap(self) -> None:
        self._overlay_pixmap = None
        self._overlay_title = ""
        self._overlay_body = ""
        if self._preview_mode == "overlay":
            self._apply_preview_mode("original")
        else:
            self.preview_mode_changed.emit(self._preview_mode, False)

    def clear_figure(self) -> None:
        super().clear_figure()
        self._scaled_base_cache.clear()
        self._original_pixmap = None
        self._overlay_pixmap = None
        self._original_title = ""
        self._original_body = ""
        self._overlay_title = ""
        self._overlay_body = ""
        self._preview_mode = "original"
        self._preview_image_uid = ""
        self.set_roi_selection_enabled(False)
        self.preview_mode_changed.emit(self._preview_mode, False)

    def _scaled_base_pixmap(self, display_size) -> QPixmap:
        cache_key = (
            int(self._source_pixmap.cacheKey()) if self._source_pixmap is not None else 0,
            int(display_size.width()),
            int(display_size.height()),
        )
        cached = self._scaled_base_cache.get(cache_key)
        if cached is not None:
            return QPixmap(cached)
        scaled = self._source_pixmap.scaled(
            display_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if len(self._scaled_base_cache) >= 8:
            self._scaled_base_cache.pop(next(iter(self._scaled_base_cache)))
        self._scaled_base_cache[cache_key] = QPixmap(scaled)
        return scaled



    def _display_rect_for_roi(self, rect: QRect, display_pixmap: QPixmap) -> QRect:
        if display_pixmap.isNull() or self._source_pixmap is None or self._source_pixmap.isNull():
            return QRect()
        scale_x = display_pixmap.width() / max(1, self._source_pixmap.width())
        scale_y = display_pixmap.height() / max(1, self._source_pixmap.height())
        left = int(round(rect.x() * scale_x))
        top = int(round(rect.y() * scale_y))
        right = int(round((rect.x() + rect.width()) * scale_x))
        bottom = int(round((rect.y() + rect.height()) * scale_y))
        return QRect(left, top, max(1, right - left), max(1, bottom - top))

    def roi_display_rect(self) -> QRect | None:
        if self._roi_rect_px is None or self.image_label is None:
            return None
        pixmap = self.image_label.pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        rect = self._display_rect_for_roi(self._roi_rect_px, pixmap)
        rect.translate(self.image_label._pixmap_rect().topLeft())
        return rect

    def _refresh_image_display(self) -> None:
        if self.image_label is None or self._source_pixmap is None or self._source_pixmap.isNull():
            return
        display_size = self._image_display_size()
        display = self._scaled_base_pixmap(display_size)
        if self._roi_rect_px is not None and not self._roi_rect_px.isNull():
            overlay = QPixmap(display)
            painter = QPainter(overlay)
            pen = QPen(QColor("#FF4FB5"), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            display_rect = self._display_rect_for_roi(self._roi_rect_px, overlay)
            painter.drawRect(
                display_rect.x(),
                display_rect.y(),
                max(0, display_rect.width() - 1),
                max(0, display_rect.height() - 1),
            )
            painter.end()
            display = overlay
        scale_painter = getattr(self, 'scale_overlay_painter', None)
        styled = scale_painter(display, self._source_pixmap) if scale_painter else None
        if styled is not None:
            display = styled
        if self._measurement_scale_bar and styled is None:
            line = measurement_preview_line_px(self._measurement_scale_bar)
            if line and self._source_pixmap is not None and not self._source_pixmap.isNull():
                overlay = QPixmap(display)
                painter = QPainter(overlay)
                scale_x = overlay.width() / max(1, self._source_pixmap.width())
                scale_y = overlay.height() / max(1, self._source_pixmap.height())
                x1 = float(line.get("x1", 0.0)) * scale_x
                y1 = float(line.get("y1", 0.0)) * scale_y
                x2 = float(line.get("x2", 0.0)) * scale_x
                y2 = float(line.get("y2", 0.0)) * scale_y
                pen = QPen(QColor("#FFD13B"), 3)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLine(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))
                label = measurement_preview_length_label(self._measurement_scale_bar)
                if label:
                    painter.setPen(QPen(QColor("#FFFFFF"), 1))
                    painter.drawText(int(round(min(x1, x2))), int(round(min(y1, y2) - 6)), label)
                painter.end()
                display = overlay
        source_line = measurement_preview_line_px(self._measurement_scale_bar) if styled is None else {}
        if not getattr(self, 'scale_editing_enabled', True):
            source_line = {}
        self.image_label.set_source_line(source_line)
        self.image_label.set_source_pixmap(self._source_pixmap, display)
        self.image_label.selection_enabled = self._roi_selection_enabled


class BrightfieldWorkspace(QWidget):
    files_dropped = pyqtSignal(list)
    import_requested = pyqtSignal()
    remove_requested = pyqtSignal()
    layout_requested = pyqtSignal()
    layout_to_composer_requested = pyqtSignal()
    export_overlay_requested = pyqtSignal()

    def __init__(self, brightfield_context_panel: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.current_step = 0
        self.panel: BrightfieldPanel | None = None
        self._workflow_i18n_labels: list[QLabel] = []
        self.canvas = BrightfieldPreviewCanvas()
        self.canvas.setAcceptDrops(False)
        self.canvas.files_dropped.connect(self.files_dropped.emit)
        self.layout_canvas = LayoutEditorCanvas()
        self.layout_canvas.scene.selectionChanged.connect(self.sync_text_controls_from_selection)
        self.layout_canvas.scene.selectionChanged.connect(self._sync_scale_appearance_controls)
        self._layout_pixmap_provider = None
        self._layout_measurement = None
        self.import_button = QPushButton("+ Add Brightfield Images")
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.setToolTip(f"Import Brightfield raster images.\n{STEP6_IMPORT_LIMITATION_TEXT}")
        self.import_button.clicked.connect(self.import_requested.emit)
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.clicked.connect(self.remove_requested.emit)
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self.show_preview)
        self.preview_button.hide()
        self.layout_button = QPushButton("Layout")
        self.layout_button.clicked.connect(self.layout_requested.emit)
        self.layout_button.hide()
        self.original_preview_button = QPushButton("Original")
        self.original_preview_button.setObjectName("BrightfieldOriginalPreviewButton")
        self.original_preview_button.setCheckable(True)
        self.original_preview_button.setChecked(True)
        self.original_preview_button.clicked.connect(self.show_original_preview)
        self.overlay_preview_button = QPushButton("Overlay")
        self.overlay_preview_button.setObjectName("BrightfieldOverlayPreviewButton")
        self.overlay_preview_button.setCheckable(True)
        self.overlay_preview_button.setEnabled(False)
        self.overlay_preview_button.clicked.connect(self.show_overlay_preview)
        self.export_image_button = QPushButton("Export image")
        self.export_image_button.setObjectName("BrightfieldExportImageButton")
        self.export_image_button.setEnabled(False)
        self.export_image_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.export_image_button.clicked.connect(self.export_overlay_requested.emit)
        self.preview_mode_group = QButtonGroup(self)
        self.preview_mode_group.setExclusive(True)
        self.preview_mode_group.addButton(self.original_preview_button)
        self.preview_mode_group.addButton(self.overlay_preview_button)
        self.canvas.preview_mode_changed.connect(self._sync_preview_toggle_buttons)
        self.add_text_button = QPushButton("Add Text")
        self.add_text_button.clicked.connect(self.add_layout_text)
        self.add_shape_button = QPushButton("Add Shape")
        self.add_shape_button.clicked.connect(self.add_layout_shape)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete_selected_layout_items)
        self.same_size_button = QPushButton("Same Size")
        self.same_size_button.clicked.connect(lambda: self._apply_layout_operation("same_size"))
        self.align_row_button = QPushButton("Align Row")
        self.align_row_button.clicked.connect(lambda: self._apply_layout_operation("align_row"))
        self.align_column_button = QPushButton("Align Column")
        self.align_column_button.clicked.connect(lambda: self._apply_layout_operation("align_column"))
        self.distribute_h_button = QPushButton("Distribute H")
        self.distribute_h_button.clicked.connect(lambda: self._apply_layout_operation("distribute_h"))
        self.distribute_v_button = QPushButton("Distribute V")
        self.distribute_v_button.clicked.connect(lambda: self._apply_layout_operation("distribute_v"))
        self.send_layout_to_composer_button = QPushButton("Send Layout to Composer")
        self.send_layout_to_composer_button.setObjectName("PrimaryButton")
        self.send_layout_to_composer_button.clicked.connect(self._request_layout_to_composer)
        self._build_text_format_controls()
        self._build_scale_bar_controls()
        self.layout_tool_buttons = [
            self.add_text_button,
            self.add_shape_button,
            self.delete_button,
            self.same_size_button,
            self.align_row_button,
            self.align_column_button,
            self.distribute_h_button,
            self.distribute_v_button,
            self.send_layout_to_composer_button,
        ]

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.canvas_stack = QStackedWidget()
        self.canvas_stack.addWidget(self.canvas)
        self.canvas_stack.addWidget(self.layout_canvas)
        self.workflow_panel = self._build_side_panel(brightfield_context_panel)
        self.side_panel = self.workflow_panel
        root.addWidget(self.canvas_stack, 1)
        self._bind_static_i18n()
        self.retranslate()
        self._set_layout_tools_visible(False)

    def _bind_static_i18n(self) -> None:
        for widget, key in (
            (self.import_button, "add_brightfield_images"),
            (self.remove_button, "remove_selected"),
            (self.preview_button, "v9_preview"),
            (self.layout_button, "layout"),
            (self.original_preview_button, "original"),
            (self.overlay_preview_button, "v9_overlay"),
            (self.export_image_button, "export_image"),
            (self.add_text_button, "add_text"),
            (self.add_shape_button, "add_shape"),
            (self.delete_button, "delete"),
            (self.same_size_button, "same_size"),
            (self.align_row_button, "align_row"),
            (self.align_column_button, "align_column"),
            (self.distribute_h_button, "distribute_h"),
            (self.distribute_v_button, "distribute_v"),
            (self.send_layout_to_composer_button, "send_layout_to_composer"),
            (self.text_apply_button, "apply"),
            (self.scale_bar_apply_current_button, "scale_bar_apply_current"),
            (self.scale_bar_apply_all_button, "scale_bar_apply_all_images"),
            (self.scale_bar_remove_current_button, "scale_bar_remove_current"),
            (self.scale_bar_remove_all_button, "scale_bar_remove_all_images"),
        ):
            bind_i18n(widget, key)

    def _build_side_panel(self, brightfield_context_panel: QWidget) -> QWidget:
        side_panel = QWidget()
        side_panel.setMinimumWidth(0)
        side_panel.setMaximumWidth(16777215)

        layout = QVBoxLayout(side_panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setObjectName("BrightfieldWorkflowRightScroll")
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

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.title_label = QLabel()
        self.title_label.setObjectName("SectionLabel")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("DimLabel")
        self.subtitle_label.setWordWrap(True)

        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        active_image_combo = self._take_context_child(brightfield_context_panel, "BrightfieldActiveImageCombo")
        if active_image_combo is not None:
            header_layout.addWidget(active_image_combo)
        header_layout.addWidget(self.import_button)
        header_layout.addWidget(self.remove_button)

        self.preview_toggle_panel = QWidget()
        preview_toggle_layout = QHBoxLayout(self.preview_toggle_panel)
        preview_toggle_layout.setContentsMargins(0, 0, 0, 0)
        preview_toggle_layout.setSpacing(6)
        preview_toggle_layout.addWidget(self.original_preview_button)
        preview_toggle_layout.addWidget(self.overlay_preview_button)
        preview_toggle_layout.addWidget(self.export_image_button)
        self.preview_toggle_panel.setVisible(False)

        step_nav = self._take_context_child(brightfield_context_panel, "BrightfieldWorkflowStepNav")
        step_group = self._workflow_group("workflow_steps")
        if step_nav is not None:
            step_group.layout().addWidget(step_nav)
        content_layout.addWidget(step_group, 0)

        source_group = self._workflow_group("active_image_selector")
        source_group.layout().addWidget(header)
        self.active_image_group = source_group
        content_layout.addWidget(source_group, 0)
        from ui.controllers.histology_white_balance_controller import HistologyWhiteBalanceController
        self.white_balance=HistologyWhiteBalanceController(self)
        content_layout.addWidget(self.white_balance.widget,0)
        content_layout.addWidget(self.preview_toggle_panel, 0)

        self.layout_tools_panel = QWidget()
        layout_tools_layout = QVBoxLayout(self.layout_tools_panel)
        layout_tools_layout.setContentsMargins(0, 0, 0, 0)
        layout_tools_layout.setSpacing(6)
        for button in self.layout_tool_buttons:
            layout_tools_layout.addWidget(button)
        layout_tools_layout.addWidget(self.text_format_controls_panel)
        content_layout.addWidget(self.scale_bar_controls_panel, 0)
        self.canvas_tools_group = self._workflow_group("canvas_tools")
        self.canvas_tools_group.layout().addWidget(self.layout_tools_panel)
        self.canvas_tools_group.setVisible(False)
        content_layout.addWidget(self.canvas_tools_group, 0)

        workflow_group = self._workflow_group("workflow")
        workflow_group.layout().addWidget(brightfield_context_panel, 1)
        self.workflow_group = workflow_group
        content_layout.addWidget(workflow_group, 1)
        content_layout.addStretch(1)
        layout.addWidget(scroll, 1)
        return side_panel

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

    def _take_context_child(self, parent: QWidget, object_name: str) -> QWidget | None:
        child = parent.findChild(QWidget, object_name)
        if child is None:
            return None
        parent_layout = parent.layout()
        if parent_layout is not None:
            parent_layout.removeWidget(child)
        child.setParent(None)
        return child

    def retranslate(self) -> None:
        retranslate_bound_widgets(self)
        retranslate_bound_widgets(self.workflow_panel)
        retranslate_combo(self.scale_bar_text_position_combo, {"above": tr("above"), "below": tr("below")})
        from ui.widgets.calibration_controls import retranslate_scale_colors
        retranslate_scale_colors(self)
        self.import_button.setToolTip(tr("brightfield_import_instructions") + "\n" + tr("histology_import_formats"))
        for label in self._workflow_i18n_labels:
            key = label.property("i18nKey")
            if isinstance(key, str):
                label.setText(tr(key))
        self.import_button.setText("+ " + tr("add_brightfield_images"))
        self.remove_button.setText(tr("remove_selected"))
        self.preview_button.setText(tr("v9_preview"))
        self.layout_button.setText(tr("layout"))
        self.original_preview_button.setText(tr("original"))
        self.overlay_preview_button.setText(tr("v9_overlay"))
        self.export_image_button.setText(tr("export_image"))
        self.add_text_button.setText(tr("add_text"))
        self.add_shape_button.setText(tr("add_shape"))
        self.delete_button.setText(tr("delete"))
        self.same_size_button.setText(tr("same_size"))
        self.align_row_button.setText(tr("align_row"))
        self.align_column_button.setText(tr("align_column"))
        self.distribute_h_button.setText(tr("distribute_h"))
        self.distribute_v_button.setText(tr("distribute_v"))
        self.send_layout_to_composer_button.setText(tr("send_layout_to_composer"))
        self.text_apply_button.setText(tr("apply"))
        self.scale_bar_apply_current_button.setText(tr("scale_bar_apply_current"))
        self.scale_bar_apply_all_button.setText(tr("scale_bar_apply_all_images"))
        self.scale_bar_remove_current_button.setText(tr("scale_bar_remove_current"))
        self.scale_bar_remove_all_button.setText(tr("scale_bar_remove_all_images"))
        self.title_label.setText(tr("v9_module_brightfield"))
        self.subtitle_label.setText(tr("brightfield_workflow_subtitle"))
        if hasattr(self.canvas, "zoom_controls"):
            self.canvas.zoom_controls.retranslate()
        if hasattr(self.layout_canvas, "zoom_controls"):
            self.layout_canvas.zoom_controls.retranslate()

    def set_panel(self, panel: BrightfieldPanel) -> None:
        self.panel = panel
        self.white_balance.sync()
        self._sync_scale_appearance_controls()

    def _scale_target_changed(self):
        self._sync_scale_appearance_controls()

    def _sync_scale_appearance_controls(self) -> None:
        if self.panel is None or not hasattr(self, "scale_target_combo"):
            return
        from ui.widgets.calibration_controls import set_scale_targets, sync_scale_appearance_controls
        active_uid = (self.panel.active_image() or {}).get("uid")
        preferred = active_uid if active_uid != getattr(self, "_scale_active_uid", None) else None
        self._scale_active_uid = active_uid
        set_scale_targets(self, [(str(i.get("source_name") or i.get("uid")), str(i.get("uid"))) for i in self.panel.images()], preferred)
        layout = self.panel.data.get("brightfield_layout", {})
        target = self.scale_target_combo.currentData()
        state = next((s for s in layout.get("scale_bars", []) if s.get("target_id") == target), {})
        template = {**dict(layout.get("scale_bar_template") or {}), **dict(state.get("template") or {})}
        sync_scale_appearance_controls(self, normalize_scale_bar_template(template), manual_um_per_px=state.get("manual_um_per_px", 0))

    def set_step(self, step_index: int) -> None:
        self.current_step = max(0, int(step_index))
        self._sync_preview_controls_visibility()
        if hasattr(self, "workflow_group"):
            self.workflow_group.setVisible(True)
        if self.current_step != 2:
            self.canvas.set_roi_selection_enabled(False)

    def show_preview(self) -> None:
        self.canvas_stack.setCurrentWidget(self.canvas)
        self._set_layout_tools_visible(False)

    def show_original_preview(self) -> None:
        self.show_preview()
        self.canvas.show_original_preview()

    def show_overlay_preview(self) -> None:
        self.show_preview()
        self.canvas.show_overlay_preview()

    def current_preview_mode(self) -> str:
        return self.canvas.current_preview_mode()

    def _sync_preview_toggle_buttons(self, mode: str, has_overlay: bool) -> None:
        self.original_preview_button.blockSignals(True)
        self.overlay_preview_button.blockSignals(True)
        self.overlay_preview_button.setEnabled(bool(has_overlay))
        self.export_image_button.setEnabled(self.canvas.has_original_pixmap() or bool(has_overlay))
        self.original_preview_button.setChecked(mode != "overlay")
        self.overlay_preview_button.setChecked(mode == "overlay" and bool(has_overlay))
        self.overlay_preview_button.blockSignals(False)
        self.original_preview_button.blockSignals(False)
        self._sync_preview_controls_visibility()

    def _sync_preview_controls_visibility(self) -> None:
        if hasattr(self, "active_image_group"):
            self.active_image_group.setVisible(True)
        if hasattr(self, "preview_toggle_panel"):
            self.preview_toggle_panel.setVisible(self.canvas.has_original_pixmap())

    def prepare_layout(self, measurement: dict | None = None, pixmap_provider=None) -> None:
        if self.panel is None:
            return
        self._layout_measurement = measurement
        self._layout_pixmap_provider = pixmap_provider
        self.layout_canvas.set_layout_objects(
            brightfield_canvas_spec(self.panel),
            brightfield_layout_objects(self.panel, measurement=measurement),
            pixmap_provider,
            svg_text_changed_callback=self.persist_layout_edits,
        )
        self.canvas_stack.setCurrentWidget(self.layout_canvas)
        self._set_layout_tools_visible(True)
        self.sync_text_controls_from_selection()
        self._sync_scale_appearance_controls()

    def _build_text_format_controls(self) -> None:
        self.text_format_controls_panel = QFrame()
        self.text_format_controls_panel.setObjectName("V9RightWorkbenchGroup")
        root = QVBoxLayout(self.text_format_controls_panel)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)
        title = bind_i18n(QLabel(), "text_formatting")
        title.setObjectName("SectionLabel")
        root.addWidget(title)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.text_font_combo = QFontComboBox()
        self.text_font_combo.setCurrentFont(QFont("Arial"))
        self.text_size_spin = QDoubleSpinBox()
        self.text_size_spin.setRange(4.0, 72.0)
        self.text_size_spin.setValue(10.0)
        self.text_bold_button = QPushButton("B")
        self.text_bold_button.setCheckable(True)
        self.text_bold_button.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.text_italic_button = QPushButton("I")
        self.text_italic_button.setCheckable(True)
        italic_font = QFont("Arial", 10)
        italic_font.setItalic(True)
        self.text_italic_button.setFont(italic_font)
        self.text_color_combo = QComboBox()
        for label, value in SCALE_BAR_COLOR_OPTIONS:
            self.text_color_combo.addItem(label, value)
        self.text_x_spin = QDoubleSpinBox()
        self.text_y_spin = QDoubleSpinBox()
        self.text_rotation_spin = QDoubleSpinBox()
        self.text_rotation_spin.setRange(-180.0, 180.0)
        for spin in (self.text_x_spin, self.text_y_spin):
            spin.setRange(-100000.0, 100000.0)
        style_row = QHBoxLayout()
        style_row.addWidget(self.text_bold_button)
        style_row.addWidget(self.text_italic_button)
        style_row.addWidget(self.text_color_combo, 1)
        form.addRow(bind_i18n(QLabel(), "font"), self.text_font_combo)
        form.addRow(bind_i18n(QLabel(), "size"), self.text_size_spin)
        form.addRow(bind_i18n(QLabel(), "style"), style_row)
        form.addRow("X", self.text_x_spin)
        form.addRow("Y", self.text_y_spin)
        form.addRow(bind_i18n(QLabel(), "rotation"), self.text_rotation_spin)
        root.addLayout(form)
        self.text_apply_button = QPushButton("Apply")
        self.text_apply_button.setObjectName("PrimaryButton")
        self.text_apply_button.clicked.connect(self.apply_text_style_to_selected)
        root.addWidget(self.text_apply_button)
        self.text_format_controls_panel.setEnabled(False)

    def sync_text_controls_from_selection(self) -> None:
        selected = self.layout_canvas.selected_text_items()
        self.text_format_controls_panel.setEnabled(bool(selected))
        if len(selected) != 1:
            return
        item = selected[0]
        font = item.font()
        widgets = [
            self.text_font_combo,
            self.text_size_spin,
            self.text_bold_button,
            self.text_italic_button,
            self.text_color_combo,
            self.text_x_spin,
            self.text_y_spin,
            self.text_rotation_spin,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        self.text_font_combo.setCurrentFont(font)
        self.text_size_spin.setValue(float(font.pointSizeF() if font.pointSizeF() > 0 else 10.0))
        self.text_bold_button.setChecked(font.bold())
        self.text_italic_button.setChecked(font.italic())
        color = item.defaultTextColor().name().upper()
        index = self.text_color_combo.findData(color)
        if index >= 0:
            self.text_color_combo.setCurrentIndex(index)
        self.text_x_spin.setValue(float(item.pos().x()))
        self.text_y_spin.setValue(float(item.pos().y()))
        self.text_rotation_spin.setValue(float(item.rotation()))
        for widget in widgets:
            widget.blockSignals(False)

    def apply_text_style_to_selected(self) -> None:
        changed = self.layout_canvas.apply_text_style_to_selected(
            {
                "font_family": self.text_font_combo.currentFont().family(),
                "font_size_pt": float(self.text_size_spin.value()),
                "bold": bool(self.text_bold_button.isChecked()),
                "italic": bool(self.text_italic_button.isChecked()),
                "text_color": str(self.text_color_combo.currentData() or "#111111"),
                "x": float(self.text_x_spin.value()),
                "y": float(self.text_y_spin.value()),
                "rotation_deg": float(self.text_rotation_spin.value()),
            }
        )
        if changed:
            self.persist_layout_edits()

    def _build_scale_bar_controls(self) -> None:
        self.scale_bar_controls_panel = QWidget()
        form = QFormLayout(self.scale_bar_controls_panel)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)
        self.scale_bar_length_spin = QDoubleSpinBox()
        self.scale_bar_length_spin.setRange(0.1, 100000.0)
        self.scale_bar_length_spin.setValue(50.0)
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
        self.scale_bar_line_color_combo = QComboBox()
        self.scale_bar_text_color_combo = QComboBox()
        for label, value in SCALE_BAR_COLOR_OPTIONS:
            self.scale_bar_line_color_combo.addItem(label, value)
            self.scale_bar_text_color_combo.addItem(label, value)
        self.scale_bar_text_position_combo = QComboBox()
        self.scale_bar_text_position_combo.addItem("Above", "above")
        self.scale_bar_text_position_combo.addItem("Below", "below")
        self.scale_bar_apply_current_button = QPushButton("Apply Scale Bar")
        self.scale_bar_apply_all_button = QPushButton("Apply to All Images")
        self.scale_bar_remove_current_button = QPushButton("Remove Scale Bar")
        self.scale_bar_remove_all_button = QPushButton("Remove from All Images")
        self.scale_bar_apply_current_button.clicked.connect(lambda: self._apply_scale_bar(current_only=True))
        self.scale_bar_apply_all_button.clicked.connect(lambda: self._apply_scale_bar(current_only=False))
        self.scale_bar_remove_current_button.clicked.connect(lambda: self._remove_scale_bar(current_only=True))
        self.scale_bar_remove_all_button.clicked.connect(lambda: self._remove_scale_bar(current_only=False))
        from ui.widgets.calibration_controls import add_scale_appearance_rows
        add_scale_appearance_rows(self, form)

    def _scale_bar_template_from_controls(self) -> dict[str, object]:
        layout=self.panel.data.get("brightfield_layout",{}) if self.panel is not None else {}
        target=self.scale_target_combo.currentData()
        state=next((s for s in layout.get('scale_bars',[]) if s.get('target_id')==target),{})
        return normalize_scale_bar_template(
            {
                **dict(layout.get('scale_bar_template') or {}),
                **dict(state.get('template') or {}),
                "label_length_um": self.scale_bar_length_spin.value(),
                "font_family": self.scale_bar_font_combo.currentFont().family(),
                "font_size": self.scale_bar_font_size_spin.value(),
                "line_thickness": self.scale_bar_line_width_spin.value(),
                "line_color": str(self.scale_bar_line_color_combo.currentData() or "#111111"),
                "text_color": str(self.scale_bar_text_color_combo.currentData() or "#111111"),
                "text_position": self.scale_bar_text_position_combo.currentData(),
            }
        )

    def _scale_bar_target_ids(self, current_only: bool) -> list[str]:
        if self.panel is None:
            return []
        if current_only:
            target = self.scale_target_combo.currentData()
            return [str(target)] if target else []
        return [str(image["uid"]) for image in self.panel.images()]

    def _apply_scale_bar(self, *, current_only: bool) -> None:
        if self.panel is None:
            return
        from ui.widgets.calibration_controls import apply_target_scale_style, validate_import_scale
        targets = self._scale_bar_target_ids(current_only)
        images = [image for image in self.panel.images() if str(image['uid']) in targets]
        if not validate_import_scale(self, images):
            return
        self.persist_layout_edits()
        template = self._scale_bar_template_from_controls()
        manual = self.scale_bar_manual_um_per_px_spin.value() if self.current_step != 0 else 0
        layout = self.panel.data.setdefault("brightfield_layout", {})
        apply_target_scale_style(layout, "scale_bars", self._scale_bar_target_ids(current_only), template, manual)
        if self.current_step != 0 and self.layout_canvas.canvas_spec is None:
            self.layout_requested.emit()
        self._refresh_layout_after_scale_bar_change()
        self._sync_scale_appearance_controls()

    def _remove_scale_bar(self, *, current_only: bool) -> None:
        if self.panel is None:
            return
        layout = self.panel.data.setdefault("brightfield_layout", {})
        existing = {str(item.get("target_id", "")): dict(item) for item in layout.get("scale_bars", []) if isinstance(item, dict)}
        for target_id in self._scale_bar_target_ids(current_only):
            state = dict(existing.get(target_id, {"target_id": target_id}))
            state["enabled"] = False
            existing[target_id] = state
        layout["scale_bars"] = list(existing.values())
        self._refresh_layout_after_scale_bar_change()

    def _refresh_layout_after_scale_bar_change(self) -> None:
        self.canvas._refresh_image_display()
        if self.panel is None or self.layout_canvas.canvas_spec is None:
            return
        self.layout_canvas.set_layout_objects(
            brightfield_canvas_spec(self.panel),
            brightfield_layout_objects(self.panel, measurement=self._layout_measurement),
            self._layout_pixmap_provider,
            svg_text_changed_callback=self.persist_layout_edits,
        )

    def _set_layout_tools_visible(self, visible: bool) -> None:
        if hasattr(self, "canvas_tools_group"):
            self.canvas_tools_group.setVisible(visible)
        if hasattr(self, "workflow_group"):
            self.workflow_group.setVisible(not visible)
        for button in self.layout_tool_buttons:
            button.setVisible(visible and button is self.send_layout_to_composer_button)
        self.text_format_controls_panel.hide()

    def persist_layout_edits(self) -> None:
        if self.panel is None or self.layout_canvas.canvas_spec is None:
            return
        apply_brightfield_layout_objects(self.panel, self.layout_canvas.layout_objects(), append_audit=False)

    def add_layout_text(self) -> None:
        if self.panel is None:
            return
        if self.layout_canvas.canvas_spec is None:
            self.layout_requested.emit()
            if self.layout_canvas.canvas_spec is None:
                self.prepare_layout()
        spec = self.layout_canvas.canvas_spec
        if spec is None:
            return
        objects = self.layout_canvas.layout_objects() if self.layout_canvas.layout_items() else brightfield_layout_objects(self.panel)
        text_uuid = f"brightfield_text_{uuid4().hex}"
        objects.append(
            LayoutObject(
                object_id=f"{self.panel.panel_uuid}:brightfield_text:{text_uuid}",
                object_type="text",
                x=24.0,
                y=24.0,
                width=140.0,
                height=24.0,
                z_order=300,
                style=LayoutObjectStyle(text_color="#111111", font_family="Arial", font_size_pt=10.0),
                text="Text",
                metadata={"brightfield_kind": "layout_text", "text_uuid": text_uuid, "text_record": {}},
            )
        )
        self.layout_canvas.set_layout_objects(
            spec,
            objects,
            self._layout_pixmap_provider,
            svg_text_changed_callback=self.persist_layout_edits,
        )
        self.persist_layout_edits()

    def add_layout_shape(self) -> None:
        if self.panel is None:
            return
        if self.layout_canvas.canvas_spec is None:
            self.layout_requested.emit()
            if self.layout_canvas.canvas_spec is None:
                self.prepare_layout()
        spec = self.layout_canvas.canvas_spec
        if spec is None:
            return
        objects = self.layout_canvas.layout_objects() if self.layout_canvas.layout_items() else brightfield_layout_objects(self.panel)
        shape_uuid = f"brightfield_shape_{uuid4().hex}"
        objects.append(
            LayoutObject(
                object_id=f"{self.panel.panel_uuid}:brightfield_shape:{shape_uuid}",
                object_type="shape",
                x=48.0,
                y=48.0,
                width=120.0,
                height=70.0,
                z_order=260,
                style=LayoutObjectStyle(stroke_color="#C0392B", fill_color="#FFFFFF", stroke_width=2.0),
                metadata={"brightfield_kind": "layout_shape", "shape_uuid": shape_uuid, "shape_type": "rectangle", "shape": {}},
            )
        )
        self.layout_canvas.set_layout_objects(
            spec,
            objects,
            self._layout_pixmap_provider,
            svg_text_changed_callback=self.persist_layout_edits,
        )
        self.persist_layout_edits()

    def delete_selected_layout_items(self) -> None:
        if self.layout_canvas.delete_selected():
            self.persist_layout_edits()

    def _apply_layout_operation(self, operation: str) -> None:
        changed = False
        if operation == "same_size":
            changed = self.layout_canvas.make_selected_same_size()
        elif operation == "align_row":
            changed = self.layout_canvas.align_selected("vcenter")
        elif operation == "align_column":
            changed = self.layout_canvas.align_selected("hcenter")
        elif operation == "distribute_h":
            changed = self.layout_canvas.distribute_selected("horizontal")
        elif operation == "distribute_v":
            changed = self.layout_canvas.distribute_selected("vertical")
        if changed:
            self.persist_layout_edits()

    def _request_layout_to_composer(self) -> None:
        self.persist_layout_edits()
        self.layout_to_composer_requested.emit()

    def set_roi_selection_enabled(self, enabled: bool) -> None:
        self.canvas.set_roi_selection_enabled(enabled)

    def set_roi_rect(self, rect: QRect | None) -> None:
        self.canvas.set_roi_rect(rect)

    def set_current_roi(self, rect: QRect | None) -> None:
        self.set_roi_rect(rect)

    def current_roi(self) -> QRect | None:
        return self.canvas.current_roi_rect()

    def clear_roi_rect(self) -> None:
        self.canvas.clear_roi_rect()

    def dragEnterEvent(self, event) -> None:
        if brightfield_dropped_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if brightfield_dropped_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = brightfield_dropped_paths(event.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
