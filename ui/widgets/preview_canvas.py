from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QRubberBand, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
if TYPE_CHECKING:
    from ui.widgets.figure_preview import FigurePreview as FigureCanvas

from core.utils.image_formats import WB_IMAGE_SUFFIXES, compound_suffix
from core.utils.i18n import bind_i18n, retranslate_bound_widgets
from ui.widgets.canvas_presentation import (PAPER_INSET, REFERENCE_PAPER_SIZE, configure_canvas_layout,
                                            configure_canvas_paper, configure_canvas_viewport)
from ui.widgets.canvas_graphics_adapter import as_viewport_point
from ui.widgets.canvas_view_controller import CanvasNavigationFilter, SceneCanvasOwnerMixin, attach_canvas_controller
from ui.widgets.canvas_zoom import CanvasZoomControls, FIT_VIEWPORT_MARGIN_PX, actual_zoom_factor, next_manual_zoom
from ui.widgets.scale_line_drawing import constrained_scale_line_end, scale_line_is_valid, paint_scale_line_preview


SUPPORTED_PREVIEW_DROP_SUFFIXES = WB_IMAGE_SUFFIXES


def dropped_preview_file_paths(mime_data) -> list[str]:
    if not mime_data.hasUrls():
        return []
    paths = []
    for url in mime_data.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if compound_suffix(path) in SUPPORTED_PREVIEW_DROP_SUFFIXES:
            paths.append(str(path))
    return paths


class SelectableImageLabel(QLabel):
    roi_selected = pyqtSignal(QRect)
    point_selected = pyqtSignal(QPoint)
    line_selected = pyqtSignal(dict)
    line_dragged = pyqtSignal(dict)
    line_selection_cancelled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.selection_enabled = False
        self.point_selection_enabled = False
        self.line_selection_enabled = False
        self._press_pos: QPoint | None = None
        self._line_press_pos: QPoint | None = None
        self._draft_source_line: dict[str, float] = {}
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._source_size = QSize()
        self._source_roi_rect = QRect()
        self._source_line: dict[str, float] = {}
        self._line_drag_role = ""
        self._line_drag_start_pos: QPoint | None = None
        self._line_drag_start_line: dict[str, float] = {}

    def set_source_pixmap(self, source_pixmap: QPixmap, display_pixmap: QPixmap) -> None:
        self._source_size = source_pixmap.size()
        self.setPixmap(display_pixmap)
        self._sync_rubber_band_to_source_roi()

    def set_source_roi_rect(self, rect: QRect | None) -> None:
        self._source_roi_rect = QRect(rect) if rect is not None else QRect()
        self._sync_rubber_band_to_source_roi()

    def set_source_line(self, line: dict | None) -> None:
        self._source_line = {
            key: float(value)
            for key, value in dict(line or {}).items()
            if key in {"x1", "y1", "x2", "y2"} and isinstance(value, (int, float))
        }

    def set_line_selection_enabled(self, enabled: bool) -> None:
        self.line_selection_enabled = bool(enabled)
        if not self.line_selection_enabled:
            self.clear_scale_line_draft()

    def clear_scale_line_draft(self) -> bool:
        had_draft = bool(self._draft_source_line) or self._line_press_pos is not None
        self._line_press_pos = None
        self._draft_source_line = {}
        if had_draft:
            self.update()
        return had_draft

    def clear_source_roi_rect(self) -> None:
        self._source_roi_rect = QRect()
        self._rubber_band.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.selection_enabled and event.button() == Qt.MouseButton.LeftButton and self.pixmap() is not None:
            self._press_pos = event.position().toPoint()
            self._rubber_band.setGeometry(QRect(self._press_pos, QSize()))
            self._rubber_band.show()
            event.accept()
            return
        if self.point_selection_enabled and event.button() == Qt.MouseButton.LeftButton and self.pixmap() is not None:
            event.accept()
            return
        if self.line_selection_enabled and event.button() == Qt.MouseButton.LeftButton and self.pixmap() is not None:
            self._line_press_pos = event.position().toPoint()
            self._draft_source_line = {}
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.pixmap() is not None:
            role = self._hit_test_source_line(event.position().toPoint())
            if role:
                self._line_drag_role = role
                self._line_drag_start_pos = event.position().toPoint()
                self._line_drag_start_line = dict(self._source_line)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.selection_enabled and self._press_pos is not None:
            self._rubber_band.setGeometry(QRect(self._press_pos, event.position().toPoint()).normalized())
            event.accept()
            return
        if self.line_selection_enabled and self._line_press_pos is not None:
            self._draft_source_line = self._mapped_scale_line(
                self._line_press_pos,
                event.position().toPoint(),
                event.modifiers(),
            ) or {}
            self.update()
            event.accept()
            return
        if self._line_drag_role and self._line_drag_start_pos is not None:
            line = self._dragged_source_line(event.position().toPoint())
            if line:
                self._source_line = line
                self.line_dragged.emit(dict(line))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.selection_enabled and self._press_pos is not None and event.button() == Qt.MouseButton.LeftButton:
            selected = QRect(self._press_pos, event.position().toPoint()).normalized()
            self._press_pos = None
            mapped = self._map_label_rect_to_source(selected)
            if mapped.width() > 1 and mapped.height() > 1:
                self._source_roi_rect = QRect(mapped)
                self._rubber_band.setGeometry(selected.intersected(self._pixmap_rect()))
                self._rubber_band.show()
                self.roi_selected.emit(mapped)
            else:
                self._rubber_band.hide()
            event.accept()
            return
        if self.point_selection_enabled and event.button() == Qt.MouseButton.LeftButton:
            mapped_point = self._map_label_point_to_source(event.position().toPoint())
            if mapped_point is not None:
                self.point_selected.emit(mapped_point)
            event.accept()
            return
        if self.line_selection_enabled and self._line_press_pos is not None and event.button() == Qt.MouseButton.LeftButton:
            line = self._mapped_scale_line(
                self._line_press_pos,
                event.position().toPoint(),
                event.modifiers(),
            )
            self._line_press_pos = None
            self._draft_source_line = {}
            self.update()
            if line is not None and scale_line_is_valid(line):
                self._source_line = dict(line)
                self.line_selected.emit(line)
            event.accept()
            return
        if self._line_drag_role and event.button() == Qt.MouseButton.LeftButton:
            line = self._dragged_source_line(event.position().toPoint())
            self._line_drag_role = ""
            self._line_drag_start_pos = None
            self._line_drag_start_line = {}
            if line:
                self._source_line = line
                self.line_selected.emit(dict(line))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and (self.line_selection_enabled or self._draft_source_line or self._line_press_pos is not None):
            self.clear_scale_line_draft()
            self.line_selection_cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._draft_source_line:
            return
        p1 = self._map_source_point_to_label(self._draft_source_line["x1"], self._draft_source_line["y1"])
        p2 = self._map_source_point_to_label(self._draft_source_line["x2"], self._draft_source_line["y2"])
        if p1 is None or p2 is None:
            return
        painter = QPainter(self)
        paint_scale_line_preview(painter,p1,p2)
        painter.end()

    def _clamped_label_point(self, point: QPoint) -> QPoint | None:
        rect = self._pixmap_rect()
        if rect.isEmpty():
            return None
        return QPoint(
            min(max(point.x(), rect.left()), rect.right()),
            min(max(point.y(), rect.top()), rect.bottom()),
        )

    def _map_label_point_to_source_clamped(self, point: QPoint) -> QPoint | None:
        mapped = self._map_label_point_to_source(point)
        if mapped is not None:
            return mapped
        clamped = self._clamped_label_point(point)
        if clamped is None:
            return None
        return self._map_label_point_to_source(clamped)

    def _mapped_scale_line(self, start: QPoint, end: QPoint, modifiers) -> dict[str, float] | None:
        mapped_start = self._map_label_point_to_source_clamped(start)
        mapped_end = self._map_label_point_to_source_clamped(end)
        if mapped_start is None or mapped_end is None:
            return None
        x2, y2 = constrained_scale_line_end(
            float(mapped_start.x()),
            float(mapped_start.y()),
            float(mapped_end.x()),
            float(mapped_end.y()),
            free_angle=bool(modifiers & Qt.KeyboardModifier.ShiftModifier),
        )
        return {
            "x1": float(mapped_start.x()),
            "y1": float(mapped_start.y()),
            "x2": x2,
            "y2": y2,
        }

    def _map_label_rect_to_source(self, selected: QRect) -> QRect:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull() or self._source_size.isEmpty():
            return QRect()
        pixmap_rect = self._pixmap_rect()
        clipped = selected.intersected(pixmap_rect)
        if clipped.isEmpty():
            return QRect()
        scale_x = self._source_size.width() / pixmap_rect.width()
        scale_y = self._source_size.height() / pixmap_rect.height()
        x = int((clipped.x() - pixmap_rect.x()) * scale_x)
        y = int((clipped.y() - pixmap_rect.y()) * scale_y)
        width = int(clipped.width() * scale_x)
        height = int(clipped.height() * scale_y)
        return QRect(x, y, width, height)

    def _map_source_rect_to_label(self, source_rect: QRect) -> QRect:
        pixmap_rect = self._pixmap_rect()
        if pixmap_rect.isEmpty() or self._source_size.isEmpty() or source_rect.isEmpty():
            return QRect()
        scale_x = pixmap_rect.width() / max(1, self._source_size.width())
        scale_y = pixmap_rect.height() / max(1, self._source_size.height())
        x = pixmap_rect.x() + int(round(source_rect.x() * scale_x))
        y = pixmap_rect.y() + int(round(source_rect.y() * scale_y))
        width = max(1, int(round(source_rect.width() * scale_x)))
        height = max(1, int(round(source_rect.height() * scale_y)))
        return QRect(x, y, width, height).intersected(pixmap_rect)

    def _sync_rubber_band_to_source_roi(self) -> None:
        if self._source_roi_rect.isEmpty() or self.pixmap() is None or self.pixmap().isNull():
            if self._press_pos is None:
                self._rubber_band.hide()
            return
        display_rect = self._map_source_rect_to_label(self._source_roi_rect)
        if display_rect.width() > 1 and display_rect.height() > 1:
            self._rubber_band.setGeometry(display_rect)
            self._rubber_band.show()
        else:
            self._rubber_band.hide()

    def _map_label_point_to_source(self, point: QPoint) -> QPoint | None:
        pixmap_rect = self._pixmap_rect()
        if pixmap_rect.isEmpty() or not pixmap_rect.contains(point):
            return None
        if self._source_size.isEmpty():
            return None
        scale_x = self._source_size.width() / pixmap_rect.width()
        scale_y = self._source_size.height() / pixmap_rect.height()
        x = int((point.x() - pixmap_rect.x()) * scale_x)
        y = int((point.y() - pixmap_rect.y()) * scale_y)
        x = min(max(x, 0), max(0, self._source_size.width() - 1))
        y = min(max(y, 0), max(0, self._source_size.height() - 1))
        return QPoint(x, y)

    def _map_source_point_to_label(self, x: float, y: float) -> QPoint | None:
        pixmap_rect = self._pixmap_rect()
        if pixmap_rect.isEmpty() or self._source_size.isEmpty():
            return None
        scale_x = pixmap_rect.width() / max(1, self._source_size.width())
        scale_y = pixmap_rect.height() / max(1, self._source_size.height())
        return QPoint(
            pixmap_rect.x() + int(round(float(x) * scale_x)),
            pixmap_rect.y() + int(round(float(y) * scale_y)),
        )

    def _source_line_label_points(self) -> tuple[QPoint, QPoint] | None:
        if not {"x1", "y1", "x2", "y2"}.issubset(self._source_line):
            return None
        p1 = self._map_source_point_to_label(self._source_line["x1"], self._source_line["y1"])
        p2 = self._map_source_point_to_label(self._source_line["x2"], self._source_line["y2"])
        if p1 is None or p2 is None:
            return None
        return p1, p2

    def _hit_test_source_line(self, point: QPoint) -> str:
        points = self._source_line_label_points()
        if points is None:
            return ""
        p1, p2 = points
        threshold = 10.0
        if self._distance(point, p1) <= threshold:
            return "start"
        if self._distance(point, p2) <= threshold:
            return "end"
        if self._distance_to_segment(point, p1, p2) <= threshold:
            return "move"
        return ""

    def _dragged_source_line(self, point: QPoint) -> dict[str, float]:
        if not self._line_drag_role or self._line_drag_start_pos is None or not self._line_drag_start_line:
            return {}
        mapped = self._map_label_point_to_source(point)
        if mapped is None:
            return {}
        line = dict(self._line_drag_start_line)
        if self._line_drag_role == "start":
            line["x1"] = float(mapped.x())
            line["y1"] = float(mapped.y())
        elif self._line_drag_role == "end":
            line["x2"] = float(mapped.x())
            line["y2"] = float(mapped.y())
        else:
            start_mapped = self._map_label_point_to_source(self._line_drag_start_pos)
            if start_mapped is None:
                return {}
            dx = float(mapped.x() - start_mapped.x())
            dy = float(mapped.y() - start_mapped.y())
            line["x1"] = self._clamp_source_x(float(self._line_drag_start_line.get("x1", 0.0)) + dx)
            line["y1"] = self._clamp_source_y(float(self._line_drag_start_line.get("y1", 0.0)) + dy)
            line["x2"] = self._clamp_source_x(float(self._line_drag_start_line.get("x2", 0.0)) + dx)
            line["y2"] = self._clamp_source_y(float(self._line_drag_start_line.get("y2", 0.0)) + dy)
        return line

    def _clamp_source_x(self, value: float) -> float:
        return max(0.0, min(float(value), float(max(0, self._source_size.width() - 1))))

    def _clamp_source_y(self, value: float) -> float:
        return max(0.0, min(float(value), float(max(0, self._source_size.height() - 1))))

    @staticmethod
    def _distance(a: QPoint, b: QPoint) -> float:
        dx = float(a.x() - b.x())
        dy = float(a.y() - b.y())
        return (dx * dx + dy * dy) ** 0.5

    @classmethod
    def _distance_to_segment(cls, point: QPoint, start: QPoint, end: QPoint) -> float:
        px, py = float(point.x()), float(point.y())
        x1, y1 = float(start.x()), float(start.y())
        x2, y2 = float(end.x()), float(end.y())
        dx = x2 - x1
        dy = y2 - y1
        if dx == 0.0 and dy == 0.0:
            return cls._distance(point, start)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        nearest = QPoint(int(round(x1 + t * dx)), int(round(y1 + t * dy)))
        return cls._distance(point, nearest)

    def _pixmap_rect(self) -> QRect:
        pixmap = self.pixmap()
        if pixmap is None:
            return QRect()
        x = max((self.width() - pixmap.width()) // 2, 0)
        y = max((self.height() - pixmap.height()) // 2, 0)
        return QRect(x, y, pixmap.width(), pixmap.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_rubber_band_to_source_roi()


class PreviewPaperBackend:
    def __init__(self, canvas: "PreviewCanvas") -> None:
        self.canvas = canvas

    def host_widget(self):
        return self.canvas

    def viewport_center(self):
        return self.canvas.scroll_area.viewport().rect().center()

    def capture_anchor(self, anchor):
        point = as_viewport_point(anchor if anchor is not None else self.viewport_center())
        widget, rect = self.canvas._content_geometry()
        local = widget.mapFromGlobal(self.canvas.scroll_area.viewport().mapToGlobal(point))
        normalized = QPointF((local.x()-rect.x())/max(1,rect.width()),
                             (local.y()-rect.y())/max(1,rect.height()))
        return point, normalized

    def apply_scale(self, factor):
        target = next_manual_zoom(self.canvas._zoom_factor, factor)
        self.canvas.apply_paper_zoom_factor(target)
        return target

    def apply_fit(self, margin_px):
        viewport = self.canvas.scroll_area.viewport().size()
        base = self.canvas._base_paper_size
        zoom = min(max(1,viewport.width()-2*margin_px)/base.width(),
                   max(1,viewport.height()-2*margin_px)/base.height())
        self.canvas.apply_paper_zoom_factor(zoom)
        self.keep_anchor((self.viewport_center(), QPointF(.5,.5)))
        return zoom

    def restore_scale(self, factor):
        anchor = self.capture_anchor(None)
        self.canvas.apply_paper_zoom_factor(factor)
        self.keep_anchor(anchor)
        return factor

    def keep_anchor(self, captured):
        point, normalized = captured
        widget, rect = self.canvas._content_geometry()
        local = QPoint(round(rect.x()+normalized.x()*rect.width()),
                       round(rect.y()+normalized.y()*rect.height()))
        viewport = self.canvas.scroll_area.viewport()
        mapped = viewport.mapFromGlobal(widget.mapToGlobal(local))
        self.pan_by(point.x()-mapped.x(), point.y()-mapped.y())

    def prepare_pan(self):
        pass

    def pan_by(self, dx, dy):
        area = self.canvas.scroll_area
        area.horizontalScrollBar().setValue(area.horizontalScrollBar().value()-dx)
        area.verticalScrollBar().setValue(area.verticalScrollBar().value()-dy)


class PreviewCanvas(SceneCanvasOwnerMixin, QWidget):
    files_dropped = pyqtSignal(list)
    roi_selected = pyqtSignal(QRect)
    point_selected = pyqtSignal(QPoint)
    line_selected = pyqtSignal(dict)
    line_dragged = pyqtSignal(dict)
    line_selection_cancelled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasArea")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._base_paper_size = QSize(*REFERENCE_PAPER_SIZE)
        self._source_pixmap: QPixmap | None = None

        self.paper = QFrame()
        self.paper.setObjectName("PaperCanvas")
        configure_canvas_paper(self.paper)
        self.paper.setMinimumSize(240, 160)
        self.paper.setFixedSize(self._base_paper_size)

        self.title = bind_i18n(QLabel(), "module_qpcr_canvas")
        self.title.setObjectName("CanvasTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = bind_i18n(QLabel(), "preview_initial_body")
        self.body.setObjectName("CanvasBody")
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)

        self.placeholder = QWidget()
        self.placeholder.setObjectName("PaperPlaceholder")
        self.placeholder.setStyleSheet("background: #ffffff;")
        placeholder_layout = QVBoxLayout(self.placeholder)
        placeholder_layout.setContentsMargins(0, 0, 0, 0)
        placeholder_layout.addStretch(1)
        placeholder_layout.addWidget(self.title)
        placeholder_layout.addWidget(self.body)
        placeholder_layout.addStretch(1)

        paper_layout = QVBoxLayout(self.paper)
        paper_layout.setContentsMargins(PAPER_INSET, PAPER_INSET, PAPER_INSET, PAPER_INSET)
        paper_layout.setSpacing(0)
        paper_layout.addWidget(self.placeholder, 1)
        self.paper_layout = paper_layout
        self.figure_canvas: FigureCanvas | None = None
        self.image_label: SelectableImageLabel | None = None
        self.current_module = "qpcr"

        self.zoom_controls = CanvasZoomControls()
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("CanvasScrollContent")
        self.paper.setParent(self.scroll_content)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("CanvasScrollArea")
        configure_canvas_viewport(self.scroll_area)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.scroll_content)

        layout = QVBoxLayout(self)
        configure_canvas_layout(layout)
        layout.addWidget(self.zoom_controls)
        layout.addWidget(self.scroll_area, 1)
        attach_canvas_controller(
            self,
            PreviewPaperBackend(self),
            zoom_controls=self.zoom_controls,
            fit_delays=(0, 80),
            viewport_margin=FIT_VIEWPORT_MARGIN_PX,
        )
        self._navigation = CanvasNavigationFilter(self._view_controller, self.scroll_area.viewport())
        self.scroll_area.viewport().installEventFilter(self)
        self.scroll_content.installEventFilter(self)
        self.installEventFilter(self)
        self.paper.installEventFilter(self)
        self.fit_to_canvas()
        self._view_controller.schedule_fit()
        self._bind_empty_copy()

    def _bind_empty_copy(self) -> None:
        bind_i18n(self.title, f'module_{self.current_module}_canvas')
        key = 'viewer_empty' if self.current_module == 'viewer' else f'canvas_{self.current_module}_empty'
        bind_i18n(self.body, key)
        self.body.show()

    def set_module(self, module_id: str, canvas_text: str) -> None:
        if getattr(self, 'current_module', None) == module_id:
            self._bind_empty_copy()
            return
        self.clear_figure()
        self._base_paper_size = QSize(*REFERENCE_PAPER_SIZE)
        self.current_module = module_id
        self._bind_empty_copy()
        self._apply_paper_zoom()
        self.schedule_fit_after_layout()

    def retranslate(self) -> None:
        retranslate_bound_widgets(self)
        self.zoom_controls.retranslate()

    def set_figure(self, figure) -> None:
        from ui.widgets.figure_preview import FigurePreview as FigureCanvas

        self.clear_figure()
        self._source_pixmap = None
        self.placeholder.hide()
        self._base_paper_size = QSize(*REFERENCE_PAPER_SIZE)
        self.figure_canvas = FigureCanvas(figure)
        self.figure_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.figure_canvas.setStyleSheet("background: white;")
        self.paper_layout.addWidget(self.figure_canvas, 1)
        self.figure_canvas.installEventFilter(self)
        self._apply_paper_zoom()
        self.figure_canvas.draw()
        self.schedule_fit_after_layout()

    def set_figure_from_factory(self, render_key, factory) -> None:
        if self.figure_canvas is not None and getattr(self, '_figure_render_key', None) == render_key:
            return
        self.set_figure(factory())
        self._figure_render_key = render_key

    def _make_image_label(self) -> SelectableImageLabel:
        return SelectableImageLabel()

    def set_pixmap(self, pixmap: QPixmap, title: str = "", body: str = "", enable_roi_selection: bool = False) -> None:
        self.clear_figure()
        self.placeholder.hide()
        self._source_pixmap = QPixmap(pixmap)
        self.image_label = self._make_image_label()
        self.image_label.selection_enabled = enable_roi_selection
        self.image_label.roi_selected.connect(self.roi_selected)
        self.image_label.point_selected.connect(self.point_selected)
        self.image_label.line_selected.connect(self.line_selected)
        self.image_label.line_dragged.connect(self.line_dragged)
        self.image_label.line_selection_cancelled.connect(self.line_selection_cancelled)
        self.paper_layout.addWidget(self.image_label, 1)
        self.image_label.show()
        self.image_label.installEventFilter(self)
        self._refresh_image_display()
        if title:
            self.title.setText(title)
        if body:
            self.body.setText(body)

    def set_point_selection_enabled(self, enabled: bool) -> None:
        if self.image_label is not None:
            self.image_label.point_selection_enabled = bool(enabled)

    def set_line_selection_enabled(self, enabled: bool) -> None:
        if self.image_label is not None:
            self.image_label.set_line_selection_enabled(bool(enabled))

    def set_source_roi_rect(self, rect: QRect | None) -> None:
        if self.image_label is not None:
            self.image_label.set_source_roi_rect(rect)

    def clear_figure(self) -> None:
        self._figure_render_key = None
        if self.figure_canvas is not None:
            self.figure_canvas.setParent(None)
            self.figure_canvas.deleteLater()
            self.figure_canvas = None
        if self.image_label is not None:
            self.image_label.setParent(None)
            self.image_label.deleteLater()
            self.image_label = None
        self._source_pixmap = None
        self._bind_empty_copy()
        self.placeholder.show()
        self._apply_paper_zoom()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_view_controller"):
            if self._fit_mode:
                self._view_controller.handle_resize()
            else:
                self._view_controller.apply_after_content_change()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._view_controller.schedule_fit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if self._navigation.eventFilter(watched, event):
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.image_label is not None:
            if self.image_label.line_selection_enabled:
                self.image_label.clear_scale_line_draft()
                self.line_selection_cancelled.emit()
                event.accept()
                return
            if self.image_label.clear_scale_line_draft():
                event.accept()
                return
        if self._view_controller.handle_zoom_shortcut(event):
            return
        super().keyPressEvent(event)

    def schedule_fit_after_layout(self) -> None:
        self._view_controller.schedule_fit()

    def apply_paper_zoom_factor(self, factor: float) -> None:
        self._resize_paper(factor)
        self._refresh_image_display()

    def _resize_paper(self, factor: float) -> None:
        factor = actual_zoom_factor(factor)
        paper_size = QSize(
            max(1, int(round(self._base_paper_size.width() * factor))),
            max(1, int(round(self._base_paper_size.height() * factor))),
        )
        self.paper.setFixedSize(paper_size)
        # View-only space around the paper allows panning even while fitted.
        viewport = self.scroll_area.viewport().size()
        self.scroll_content.setFixedSize(paper_size.width()+2*viewport.width(), paper_size.height()+2*viewport.height())
        self.paper.move(viewport.width(), viewport.height())
        margin = max(0, round(PAPER_INSET*factor)-self.paper.frameWidth())
        self.paper_layout.setContentsMargins(margin, margin, margin, margin)
        self.paper_layout.activate()
        self.zoom_controls.set_zoom_factor(factor)

    def _content_geometry(self):
        if self.image_label is not None and self.image_label.pixmap() is not None:
            return self.image_label, QRectF(self.image_label._pixmap_rect())
        if self.figure_canvas is not None:
            return self.figure_canvas, self.figure_canvas.content_rect()
        return self.paper, QRectF(self.paper.rect())

    def _apply_paper_zoom(self) -> None:
        self.apply_paper_zoom_factor(self._zoom_factor)

    def _image_display_size(self) -> QSize:
        paper_size = self.paper.contentsRect().size()
        margins = self.paper_layout.contentsMargins()
        return QSize(max(1,paper_size.width()-margins.left()-margins.right()),
                     max(1,paper_size.height()-margins.top()-margins.bottom()))

    def _refresh_image_display(self) -> None:
        if self.image_label is None or self._source_pixmap is None or self._source_pixmap.isNull():
            return
        self.image_label.set_source_pixmap(
            self._source_pixmap,
            self._source_pixmap.scaled(self._image_display_size(),
                                      Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation),
        )
        # setPixmap changes the label's size hint. Anchor calculations must see
        # the final label geometry, not its pre-layout position after replacement.
        self.paper_layout.activate()

    def dragEnterEvent(self, event) -> None:
        if self.current_module == "wb" and dropped_preview_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self.current_module == "wb" and dropped_preview_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = dropped_preview_file_paths(event.mimeData())
        if self.current_module == "wb" and paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
