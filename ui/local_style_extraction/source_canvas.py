from __future__ import annotations

from typing import Any
import sys

from ui.widgets.canvas_presentation import content_frame_rect, paint_canvas_paper, paint_canvas_surround
from ui.widgets.canvas_graphics_adapter import as_viewport_point
from ui.widgets.canvas_view_controller import CanvasNavigationFilter, SceneCanvasOwnerMixin, attach_canvas_controller
from ui.widgets.canvas_zoom import CanvasZoomControls, FIT_VIEWPORT_MARGIN_PX, actual_zoom_factor, next_manual_zoom

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def normalize_rect(rect: list[float] | tuple[float, ...] | None) -> list[float]:
    if not rect or len(rect) < 4:
        return [0.0, 0.0, 1.0, 1.0]
    x, y, w, h = [float(v) for v in rect[:4]]
    if w < 0:
        x += w
        w = -w
    if h < 0:
        y += h
        h = -h
    x = clamp01(x)
    y = clamp01(y)
    w = max(0.01, min(1.0 - x, w))
    h = max(0.01, min(1.0 - y, h))
    return [x, y, w, h]


def parse_bbox(item: Any) -> list[float] | None:
    if isinstance(item, dict):
        if "bbox" in item:
            return parse_bbox(item.get("bbox"))
        if all(k in item for k in ("x", "y", "w", "h")):
            return normalize_rect([item["x"], item["y"], item["w"], item["h"]])
        if all(k in item for k in ("x", "y", "width", "height")):
            return normalize_rect([item["x"], item["y"], item["width"], item["height"]])
        return None
    if isinstance(item, (list, tuple)) and len(item) >= 4:
        return normalize_rect(item)
    return None


class SourceCanvasBackend:
    def __init__(self, canvas: "SourceCanvas") -> None:
        self.canvas = canvas

    def host_widget(self):
        return self.canvas

    def viewport_center(self) -> QPoint:
        return self.canvas.rect().center()

    def capture_anchor(self, anchor) -> object:
        point = as_viewport_point(anchor if anchor is not None else self.viewport_center())
        return point, self.canvas._page_point_at(QPointF(point))

    def apply_scale(self, factor: float) -> float:
        current = self.canvas._zoom_factor
        target = next_manual_zoom(current, factor)
        self.canvas.set_view_zoom(target)
        return target

    def apply_fit(self, margin_px: int) -> float:
        pix = self.canvas._pixmap
        if pix.isNull():
            return 1.0
        rect = self.canvas._content_rect()
        frame = content_frame_rect(QRectF(pix.rect()), margin=12 if sys.platform in {'darwin', 'win32'} else None)
        zoom = min(rect.width()/frame.width(), rect.height()/frame.height())
        self.canvas.set_view_zoom(zoom, center=(.5,.5))
        return zoom

    def restore_scale(self, factor: float) -> float:
        zoom = actual_zoom_factor(factor)
        self.canvas.set_view_zoom(zoom)
        return zoom

    def keep_anchor(self, captured) -> None:
        point, page = captured
        self.canvas.keep_page_point_at(page, point)

    def prepare_pan(self) -> None:
        return

    def pan_by(self, dx: int, dy: int) -> None:
        self.canvas.pan_view(dx, dy)


class SourceCanvas(SceneCanvasOwnerMixin, QWidget):
    cropCommitted = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pixmap = QPixmap()
        self._page_image_path = None
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self._evidence: list[list[float]] = []
        self._drag_origin: QPointF | None = None
        self._drag_rect: list[float] | None = None
        self._page_rect = QRectF()
        self._view_norm = [0.0, 0.0, 1.0, 1.0]
        self._view_window = [0.0, 0.0, 1.0, 1.0]

        self.zoom_controls = CanvasZoomControls()
        attach_canvas_controller(self, SourceCanvasBackend(self), zoom_controls=self.zoom_controls)
        self._navigation = CanvasNavigationFilter(self._view_controller, self)
        self.installEventFilter(self._navigation)

    def set_page_image(self, path: str | None) -> None:
        previous_path, previous_size = self._page_image_path, self._pixmap.size()
        self._page_image_path = path
        self._pixmap = QPixmap()
        if path:
            image = QImage(path)
            if not image.isNull():
                self._pixmap = QPixmap.fromImage(image)
        if previous_path != path or previous_size != self._pixmap.size():
            self.fit_to_canvas()
        else:
            self._view_controller.apply_after_content_change()

    def set_crop(self, rect: list[float] | None) -> None:
        self._crop = normalize_rect(rect)
        self._drag_rect = None
        self.update()

    def crop(self) -> list[float]:
        return list(self._crop)

    def set_evidence(self, boxes: list[Any] | None) -> None:
        parsed: list[list[float]] = []
        for item in boxes or []:
            bbox = parse_bbox(item)
            if bbox:
                parsed.append(bbox)
        self._evidence = parsed
        self.update()

    def clear(self) -> None:
        self._pixmap = QPixmap()
        self._page_image_path = None
        self._crop = [0.0, 0.0, 1.0, 1.0]
        self._evidence = []
        self._drag_origin = None
        self._drag_rect = None
        self._view_window = [0.0, 0.0, 1.0, 1.0]
        self.fit_to_canvas()
        self.update()

    def _visible_norm(self) -> list[float]:
        # The viewport may extend outside the image; extraction crops may not.
        return list(self._view_window)

    def set_view_zoom(self, zoom: float, center=None) -> None:
        if self._pixmap.isNull():
            return
        zoom = actual_zoom_factor(zoom)
        if center is None:
            center = (self._view_window[0]+self._view_window[2]/2,
                      self._view_window[1]+self._view_window[3]/2)
        rect = self._content_rect()
        width = rect.width() / (self._pixmap.width()*zoom)
        height = rect.height() / (self._pixmap.height()*zoom)
        self._view_window = [center[0]-width/2, center[1]-height/2, width, height]
        self.update()

    def keep_page_point_at(self, page, widget_pos) -> None:
        rect = self._content_rect()
        width, height = self._view_window[2:]
        x = page[0] - (widget_pos.x()-rect.x())/max(1,rect.width())*width
        y = page[1] - (widget_pos.y()-rect.y())/max(1,rect.height())*height
        self._view_window = [x,y,width,height]
        self.update()

    def pan_view(self, dx, dy) -> None:
        rect = self._content_rect()
        x,y,width,height = self._view_window
        self._view_window = [x-dx/max(1,rect.width())*width,
                             y-dy/max(1,rect.height())*height,width,height]
        self.update()

    def _content_rect(self) -> QRectF:
        margin = FIT_VIEWPORT_MARGIN_PX
        return QRectF(margin,margin,max(1,self.width()-2*margin),max(1,self.height()-2*margin))

    def _page_point_at(self, pos):
        rect = self._content_rect()
        vis = self._view_window
        return (vis[0]+(pos.x()-rect.x())/rect.width()*vis[2],
                vis[1]+(pos.y()-rect.y())/rect.height()*vis[3])

    def _norm_from_pos(self, pos):
        x,y = self._page_point_at(pos)
        return clamp01(x),clamp01(y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_view_controller"):
            if self._fit_mode:
                self._view_controller.handle_resize()
            else:
                self._view_controller.apply_after_content_change()

    def _rect_from_norm(self, norm: list[float]) -> QRectF:
        x, y, w, h = normalize_rect(norm)
        vis = self._view_norm
        if vis[2] <= 0 or vis[3] <= 0:
            return QRectF()
        r = self._page_rect
        rx = (x - vis[0]) / vis[2]
        ry = (y - vis[1]) / vis[3]
        rw = w / vis[2]
        rh = h / vis[3]
        return QRectF(r.x() + rx * r.width(), r.y() + ry * r.height(), rw * r.width(), rh * r.height())

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        paint_canvas_surround(painter,self.rect())
        self._view_norm = self._visible_norm()
        self._page_rect = self._content_rect()
        if self._pixmap.isNull() or self._page_rect.isEmpty():
            painter.setPen(QColor("#8a7d72"))
            painter.setFont(self.font() if sys.platform in {'darwin', 'win32'} else QFont("Microsoft YaHei UI", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "未打开来源\n请打开本地 PDF 或图像")
            return
        vis = self._view_norm
        r = self._page_rect
        image_rect = QRectF(r.x()-vis[0]/vis[2]*r.width(), r.y()-vis[1]/vis[3]*r.height(),
                            r.width()/vis[2], r.height()/vis[3])
        frame = content_frame_rect(QRectF(self._pixmap.rect()), margin=12 if sys.platform in {'darwin', 'win32'} else None)
        scale = image_rect.width()/self._pixmap.width()
        screen_frame = QRectF(image_rect.x()+frame.x()*scale, image_rect.y()+frame.y()*scale,
                              frame.width()*scale,frame.height()*scale)
        paint_canvas_paper(painter,screen_frame)
        painter.setClipRect(self.rect())
        painter.drawPixmap(image_rect, self._pixmap, QRectF(self._pixmap.rect()))
        crop = self._drag_rect if self._drag_rect is not None else self._crop
        crop_rect = self._rect_from_norm(crop)
        shade = QPainterPath()
        shade.addRect(image_rect.intersected(QRectF(self.rect())))
        clear = QPainterPath()
        clear.addRect(crop_rect)
        painter.fillPath(shade.subtracted(clear), QColor(0,0,0,72))
        pen = QPen(QColor("#d97757"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(crop_rect)
        epen = QPen(QColor("#fbbf24"))
        epen.setWidth(2)
        painter.setPen(epen)
        for box in self._evidence:
            painter.drawRect(self._rect_from_norm(box))



    def mousePressEvent(self, event) -> None:  # noqa: ANN001

        if event.button() != Qt.MouseButton.LeftButton or self._pixmap.isNull():
            return
        self._drag_origin = event.position()
        x, y = self._norm_from_pos(event.position())
        self._drag_rect = [x, y, 0.01, 0.01]
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001

        if self._drag_origin is None:
            return
        x0, y0 = self._norm_from_pos(self._drag_origin)
        x1, y1 = self._norm_from_pos(event.position())
        self._drag_rect = normalize_rect([min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)])
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001

        if self._drag_origin is None:
            return
        x0, y0 = self._norm_from_pos(self._drag_origin)
        x1, y1 = self._norm_from_pos(event.position())
        self._drag_origin = None
        if abs(x1 - x0) < 0.012 and abs(y1 - y0) < 0.012:
            self._drag_rect = None
            self.update()
            return
        self._crop = normalize_rect([min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)])
        self._drag_rect = None
        self.update()
        self.cropCommitted.emit(list(self._crop))


class PageStrip(QListWidget):
    pageChosen = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setIconSize(QSize(44, 56))
        self.setMaximumHeight(78)
        self.setMinimumHeight(0)
        self.setSpacing(4)
        self.itemClicked.connect(self._on_item)

    def set_page_count(self, count: int) -> None:
        self.blockSignals(True)
        self.clear()
        for index in range(max(0, count)):
            item = QListWidgetItem(f"第{index + 1}页")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.addItem(item)
        self.blockSignals(False)

    def set_current_page(self, index: int) -> None:
        self.blockSignals(True)
        if 0 <= index < self.count():
            self.setCurrentRow(index)
        self.blockSignals(False)

    def set_thumb(self, index: int, path: str) -> None:
        if not (0 <= index < self.count()):
            return
        pix = QPixmap(path)
        if pix.isNull():
            return
        item = self.item(index)
        item.setIcon(QIcon(pix))

    def _on_item(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(index, int):
            self.pageChosen.emit(index)


class SourceBoard(QFrame):
    pageChangeRequested = pyqtSignal(int)
    cropCommitted = pyqtSignal(list)
    regionChosen = pyqtSignal(list, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Pane")
        self._regions: list[dict[str, Any]] = []
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        title = QLabel("来源与裁剪")
        title.setObjectName("TitleLabel")
        root.addWidget(title)
        hint = QLabel("在页面上拖拽框选图区。建议框仅供参考，不会自动提取。")
        hint.setObjectName("DimLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("页"))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setEnabled(False)
        self.page_spin.valueChanged.connect(self._spin_page)
        row.addWidget(self.page_spin)
        row.addWidget(QLabel("建议区域"))
        self.region_combo = QComboBox()
        self.region_combo.setMinimumWidth(72)
        self.region_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.region_combo.addItem("手动裁剪", userData="")
        self.region_combo.currentIndexChanged.connect(self._combo_region)
        row.addWidget(self.region_combo, 1)
        root.addLayout(row)
        self.thumbs = PageStrip()
        self.thumbs.pageChosen.connect(self.pageChangeRequested.emit)
        self.thumbs.setVisible(False)
        root.addWidget(self.thumbs)
        self.canvas = SourceCanvas()
        self.canvas.cropCommitted.connect(self._on_crop)
        root.addWidget(self.canvas.zoom_controls)
        root.addWidget(self.canvas, 1)
        self.crop_label = QLabel("裁剪：未选择")
        self.crop_label.setObjectName("CropLabel")
        root.addWidget(self.crop_label)

    def set_page_count(self, count: int, current: int) -> None:
        self._updating = True
        count = max(1, int(count))
        self.page_spin.setMaximum(count)
        self.page_spin.setMinimum(1)
        self.page_spin.setEnabled(count >= 1)
        self.page_spin.setValue(current + 1)
        self.thumbs.set_page_count(count)
        self.thumbs.set_current_page(current)
        self.thumbs.setVisible(count > 1)
        self._updating = False

    def set_current_page(self, index: int) -> None:
        self._updating = True
        self.page_spin.setValue(index + 1)
        self.thumbs.set_current_page(index)
        self._updating = False

    def set_page_image(self, path: str | None) -> None:
        self.canvas.set_page_image(path)

    def set_thumb(self, index: int, path: str) -> None:
        self.thumbs.set_thumb(index, path)

    def set_crop(self, rect: list[float], as_manual: bool = False) -> None:
        self.canvas.set_crop(rect)
        self._set_crop_label(rect)
        if as_manual:
            self._updating = True
            self.region_combo.setCurrentIndex(0)
            self._updating = False

    def crop(self) -> list[float]:
        return self.canvas.crop()

    def set_evidence(self, boxes: list[Any] | None) -> None:
        self.canvas.set_evidence(boxes)

    def set_regions(self, regions: list[dict[str, Any]] | None) -> None:
        self._regions = list(regions or [])
        self._updating = True
        self.region_combo.clear()
        self.region_combo.addItem("手动裁剪", userData="")
        for item in self._regions:
            label = str(item.get("label") or item.get("id") or "区域")
            reason = str(item.get("reason") or "")
            text = label if not reason else f"{label} — {reason}"
            self.region_combo.addItem(text, userData=str(item.get("id") or ""))
        self._updating = False

    def select_region_id(self, region_id: str) -> None:
        self._updating = True
        idx = self.region_combo.findData(region_id)
        self.region_combo.setCurrentIndex(max(0, idx))
        self._updating = False

    def clear(self) -> None:
        self.canvas.clear()
        self.set_regions([])
        self.thumbs.set_page_count(0)
        self.thumbs.setVisible(False)
        self.page_spin.setEnabled(False)
        self.crop_label.setText("裁剪：未选择")

    def _set_crop_label(self, rect: list[float]) -> None:
        x, y, w, h = normalize_rect(rect)
        self.crop_label.setText(f"裁剪（页归一化）：x={x:.3f} y={y:.3f} w={w:.3f} h={h:.3f}")

    def _on_crop(self, rect: list[float]) -> None:
        self._set_crop_label(rect)
        self._updating = True
        self.region_combo.setCurrentIndex(0)
        self._updating = False
        self.cropCommitted.emit(list(rect))

    def _spin_page(self, value: int) -> None:
        if self._updating:
            return
        self.pageChangeRequested.emit(value - 1)

    def _combo_region(self, index: int) -> None:
        if self._updating:
            return
        if index <= 0:
            return
        item = self._regions[index - 1]
        rect = normalize_rect(item.get("rect"))
        self.canvas.set_crop(rect)
        self._set_crop_label(rect)
        self.regionChosen.emit(rect, str(item.get("id") or ""))

