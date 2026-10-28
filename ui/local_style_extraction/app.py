from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .chart_types import CHART_LABELS, CHARTS
from .engine_bind import DEMO_DIR, bind_engine
from .source_canvas import SourceBoard, normalize_rect
from ui.widgets.canvas_presentation import content_frame_rect, paint_canvas_paper, paint_canvas_surround
from ui.widgets.canvas_graphics_adapter import as_viewport_point
from ui.widgets.canvas_view_controller import CanvasNavigationFilter, SceneCanvasOwnerMixin, attach_canvas_controller
from ui.widgets.canvas_zoom import CanvasZoomControls, FIT_VIEWPORT_MARGIN_PX, ZOOM_STEP, actual_zoom_factor, next_manual_zoom
from .token_editor import MIN_TOKEN_KEYS, TokenEditor, format_key_list
from .ui_theme import stylesheet
from .workers import EngineJobThread, Job
from .runtime import CACHE_DIR
from core.utils.recent_directory import recent_directory, remember_file_directory

IMAGE_FILTER = "PDF 与图像 (*.pdf *.png *.jpg *.jpeg *.tif *.tiff);;PDF (*.pdf);;图像 (*.png *.jpg *.jpeg *.tif *.tiff)"
JSON_FILTER = "样式稿 JSON (*.json)"


def _as_abs(value: Any, base: Path) -> str:
    if not value:
        return ""
    path = Path(str(value))
    return str(path if path.is_absolute() else base / path)


def new_run_dir(kind: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CACHE_DIR / "runs" / f"{stamp}_{uuid.uuid4().hex[:8]}_{kind}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def same_rect(a: Any, b: Any) -> bool:
    if not a or not b:
        return False
    try:
        na = normalize_rect(a)
        nb = normalize_rect(b)
        return all(abs(float(x) - float(y)) < 1e-3 for x, y in zip(na, nb))
    except (TypeError, ValueError):
        return False


def same_source_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except Exception:
        return os.path.normcase(a.replace("/", "\\")) == os.path.normcase(b.replace("/", "\\"))


def infer_origin_status(token: dict[str, Any]) -> str:
    status = str(token.get("status") or "")
    if status in {"detected", "estimated", "unknown"}:
        return status
    method = str(token.get("method") or "")
    if method in {"pdf_native", "pdf_rendered"}:
        return "detected"
    if "raster" in method or method in {"pdf_geometry", "local_ocr_match"}:
        return "estimated"
    if method in {"none", "fallback"}:
        return "unknown"
    return "unknown"


def pick_figure_candidate(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    rows = item.get("figure_candidates")
    if not isinstance(rows, list):
        return None
    chosen: dict[str, Any] | None = None
    for row in rows:
        if isinstance(row, dict) and "native_vector" in str(row.get("note") or "").lower():
            chosen = row
            break
    if chosen is None:
        for row in rows:
            if isinstance(row, dict):
                chosen = row
                break
    if chosen is None:
        return None
    try:
        page_index = int(chosen.get("page_index") or 0)
    except (TypeError, ValueError):
        page_index = 0
    return {
        "page_index": page_index,
        "rect": normalize_rect(chosen.get("rect")),
        "note": str(chosen.get("note") or ""),
    }


def dialog_options() -> QFileDialog.Option:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return QFileDialog.Option.DontUseNativeDialog
    return QFileDialog.Option(0)


class ScaledViewBackend:
    def __init__(self, view):
        self.view = view

    def host_widget(self):
        return self.view

    def viewport_center(self):
        return self.view.rect().center()

    def capture_anchor(self, anchor):
        point = as_viewport_point(anchor if anchor is not None else self.viewport_center())
        rect = self.view.image_rect()
        return point, QPointF((point.x()-rect.x())/max(1,rect.width()), (point.y()-rect.y())/max(1,rect.height()))

    def apply_scale(self, factor):
        return self.restore_scale(next_manual_zoom(self.view._view_zoom, factor))

    def apply_fit(self, margin_px):
        pix = self.view._pix
        if pix.isNull():
            return 1.0
        self.view._pan = QPointF()
        frame = content_frame_rect(QRectF(pix.rect()), margin=12 if sys.platform in {'darwin', 'win32'} else None)
        return self.restore_scale(min(max(1,self.view.width()-2*margin_px)/frame.width(),
                                      max(1,self.view.height()-2*margin_px)/frame.height()))

    def restore_scale(self, factor):
        self.view._view_zoom = actual_zoom_factor(factor)
        self.view.update()
        return self.view._view_zoom

    def keep_anchor(self, captured):
        point, norm = captured
        rect = self.view.image_rect()
        delta = QPointF(point)-QPointF(rect.x()+norm.x()*rect.width(), rect.y()+norm.y()*rect.height())
        self.view._pan = QPointF(self.view._pan)+delta
        self.view.update()

    def prepare_pan(self):
        pass

    def pan_by(self, dx, dy):
        self.view._pan = QPointF(self.view._pan)+QPointF(dx,dy)
        self.view.update()


class ScaledView(SceneCanvasOwnerMixin, QWidget):
    clicked = pyqtSignal()
    viewChanged = pyqtSignal(float)

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pix = QPixmap()
        self._placeholder = placeholder
        self._view_zoom = 1.0
        self._pan = QPoint(0, 0)

        self.setMinimumSize(80, 80)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        attach_canvas_controller(self, ScaledViewBackend(self))
        self._navigation = CanvasNavigationFilter(self._view_controller, self)
        self.installEventFilter(self._navigation)
        self._view_controller.view_changed.connect(self.viewChanged.emit)

    def sizeHint(self) -> QSize:
        return QSize(320, 240)

    def pixmap_is_null(self) -> bool:
        return self._pix.isNull()

    def source_pixmap(self) -> QPixmap:
        return self._pix



    def set_path(self, path: str | None) -> None:
        old_size = self._pix.size()
        self._pix = QPixmap()
        if path:
            image = QImage(path)
            if not image.isNull():
                self._pix = QPixmap.fromImage(image)
        if old_size != self._pix.size():
            self.fit_to_canvas()
        else:
            self._view_controller.apply_after_content_change()

    def image_rect(self):
        width = self._pix.width()*self._view_zoom
        height = self._pix.height()*self._view_zoom
        return QRectF((self.width()-width)/2+self._pan.x(),(self.height()-height)/2+self._pan.y(),width,height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_view_controller"):
            self._view_controller.handle_resize()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001

        if event.button() == Qt.MouseButton.LeftButton and not self._pix.isNull():
            self.clicked.emit()
        super().mousePressEvent(event)



    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        paint_canvas_surround(painter,self.rect())
        if self._pix.isNull():
            painter.setPen(QColor("#8a7d72"))
            painter.setFont(self.font() if sys.platform in {'darwin', 'win32'} else QFont("Microsoft YaHei UI", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return
        rect = self.image_rect()
        frame = content_frame_rect(QRectF(self._pix.rect()), margin=12 if sys.platform in {'darwin', 'win32'} else None)
        screen_frame = QRectF(rect.x()+frame.x()*self._view_zoom,rect.y()+frame.y()*self._view_zoom,
                              frame.width()*self._view_zoom,frame.height()*self._view_zoom)
        paint_canvas_paper(painter,screen_frame)
        painter.drawPixmap(rect, self._pix, QRectF(self._pix.rect()))


class PreviewBoard(QFrame):
    chartChanged = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Pane")
        self._kind = "A"
        self._applied: list[str] = []
        self._unsupported: list[str] = []
        self._substitutions: dict[str, str] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        title = QLabel("基线 / 样式预览（固定示例数据）")
        title.setObjectName("TitleLabel")
        root.addWidget(title)
        hint = QLabel("使用同一组固定示例样本，配色按组顺序应用。完整配色见色带。编辑只重绘预览；真实数据从对应模块导入。点击图可放大。")
        hint.setObjectName("DimLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("示例图"))
        self.chart_combo = QComboBox()
        for key in CHARTS:
            self.chart_combo.addItem(CHART_LABELS[key], userData=key)
        self.chart_combo.currentIndexChanged.connect(self._chart)
        row.addWidget(self.chart_combo, 1)
        self.btn_enlarge = QPushButton("放大预览")
        self.btn_enlarge.clicked.connect(self._enlarge)
        row.addWidget(self.btn_enlarge)
        root.addLayout(row)
        self.baseline = ScaledView("无基线")
        self.styled = ScaledView("无样式预览")
        self.baseline.clicked.connect(lambda: self._enlarge_from(self.baseline))
        self.styled.clicked.connect(lambda: self._enlarge_from(self.styled))
        self.view_bar = QWidget()
        view_row = QHBoxLayout(self.view_bar)
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(6)
        self.btn_view_styled = QPushButton("当前样式")
        self.btn_view_base = QPushButton("基线")
        self.btn_view_compare = QPushButton("对照")
        for btn, idx in (
            (self.btn_view_styled, 0),
            (self.btn_view_base, 1),
            (self.btn_view_compare, 2),
        ):
            btn.setCheckable(True)
            btn.setObjectName("StageButton")
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.clicked.connect(lambda _checked=False, i=idx: self._set_view(i))
            view_row.addWidget(btn)
        view_row.addStretch(1)
        root.addWidget(self.view_bar)
        self.zoom_controls = CanvasZoomControls()
        self.zoom_controls.fit_requested.connect(self._fit_active_views)
        self.zoom_controls.zoom_in_requested.connect(lambda: self._zoom_active_views(ZOOM_STEP))
        self.zoom_controls.zoom_out_requested.connect(lambda: self._zoom_active_views(1 / ZOOM_STEP))
        root.addWidget(self.zoom_controls)
        self.baseline.viewChanged.connect(lambda zoom: self._view_zoom_changed(self.baseline, zoom))
        self.styled.viewChanged.connect(lambda zoom: self._view_zoom_changed(self.styled, zoom))
        self.view_host = QWidget()
        self.view_host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.view_host.setMinimumSize(80, 80)
        self.view_layout = QVBoxLayout(self.view_host)
        self.view_layout.setContentsMargins(0, 0, 0, 0)
        self.view_layout.setSpacing(6)
        root.addWidget(self.view_host, 1)
        self._view_index = 0
        self.meta = QLabel("尚未渲染")
        self.meta.setObjectName("DimLabel")
        self.meta.setWordWrap(True)
        root.addWidget(self.meta)
        self.details_btn = QToolButton()
        self.details_btn.setObjectName("LinkButton")
        self.details_btn.setCheckable(True)
        self.details_btn.setText("技术细节")
        self.details_btn.setVisible(False)
        self.details_btn.toggled.connect(self._toggle_details)
        root.addWidget(self.details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.raw_meta = QLabel("")
        self.raw_meta.setObjectName("DimLabel")
        self.raw_meta.setWordWrap(True)
        self.raw_meta.setVisible(False)
        self.raw_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.raw_meta)
        self.set_layout_kind("A")

    def chart(self) -> str:
        return str(self.chart_combo.currentData() or "multipanel")

    def _active_scaled_views(self) -> list[ScaledView]:
        if self._kind == "B" or self._view_index == 2:
            return [self.baseline, self.styled]
        if self._view_index == 1:
            return [self.baseline]
        return [self.styled]

    def _fit_active_views(self) -> None:
        for view in self._active_scaled_views():
            view.fit_to_canvas()
        self.zoom_controls.set_zoom_factor(self._active_scaled_views()[0]._zoom_factor)

    def _view_zoom_changed(self, view, zoom):
        if view in self._active_scaled_views():
            self.zoom_controls.set_zoom_factor(zoom)

    def _zoom_active_views(self, factor: float) -> None:
        for view in self._active_scaled_views():
            view.zoom_by(factor)
        self.zoom_controls.set_zoom_factor(self._active_scaled_views()[0]._zoom_factor)

    def set_layout_kind(self, kind: str) -> None:
        self._kind = "B" if kind == "B" else "A"
        self.view_bar.setVisible(self._kind == "A")
        if self._kind == "A":
            self._set_view(self._view_index)
        else:
            self._set_view(-1)

    def _clear_view_layout(self) -> None:
        self.baseline.setParent(None)
        self.styled.setParent(None)
        while self.view_layout.count():
            item = self.view_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in {self.baseline, self.styled}:
                widget.deleteLater()

    def _set_view(self, index: int) -> None:
        if index >= 0:
            self._view_index = index
        self._clear_view_layout()
        self.zoom_controls.set_zoom_factor(self._active_scaled_views()[0]._zoom_factor)
        self.btn_view_styled.setChecked(self._kind == "A" and self._view_index == 0)
        self.btn_view_base.setChecked(self._kind == "A" and self._view_index == 1)
        self.btn_view_compare.setChecked(self._kind == "A" and self._view_index == 2)
        if self._kind == "B":
            pair = QWidget()
            pair.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            row = QHBoxLayout(pair)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            left = QVBoxLayout()
            left.addWidget(QLabel("基线（中性默认）"))
            left.addWidget(self.baseline, 1)
            right = QVBoxLayout()
            right.addWidget(QLabel("当前样式"))
            right.addWidget(self.styled, 1)
            row.addLayout(left, 1)
            row.addLayout(right, 1)
            self.view_layout.addWidget(pair, 1)
        elif self._view_index == 1:
            self.view_layout.addWidget(self.baseline, 1)
        elif self._view_index == 2:
            self.view_layout.addWidget(QLabel("基线（中性默认）"))
            self.view_layout.addWidget(self.baseline, 1)
            self.view_layout.addWidget(QLabel("当前样式"))
            self.view_layout.addWidget(self.styled, 1)
        else:
            self.view_layout.addWidget(self.styled, 1)
        self.baseline.update()
        self.styled.update()

    def set_images(
        self,
        baseline: str | None,
        styled: str | None,
        applied: list[str],
        unsupported: list[str],
        substitutions: dict[str, str] | None = None,
    ) -> None:
        self.baseline.set_path(baseline)
        self.styled.set_path(styled)
        self._applied = list(applied)
        self._unsupported = list(unsupported)
        self._substitutions = dict(substitutions or {})
        bits = []
        if applied:
            bits.append("已应用：" + format_key_list(applied))
        else:
            bits.append("已应用：无")
        if unsupported:
            bits.append("产品样式不含：" + format_key_list(unsupported, 8))
        extra = []
        for requested, actual in self._substitutions.items():
            if requested and actual and requested != actual:
                extra.append(f"预览字体 {actual}（稿中 {requested}）")
        if extra:
            bits.append(" · ".join(extra))
        self.meta.setText("。".join(bits) + "。")
        raw = []
        if applied:
            raw.append("applied_keys=" + ", ".join(applied))
        if unsupported:
            raw.append("unsupported_keys=" + ", ".join(unsupported))
        if self._substitutions:
            raw.append("font_substitutions=" + json.dumps(self._substitutions, ensure_ascii=False))
        self.raw_meta.setText("\n".join(raw))
        self.details_btn.setVisible(bool(raw))
        if not raw:
            self.details_btn.setChecked(False)
            self.raw_meta.setVisible(False)

    def clear(self) -> None:
        self.baseline.set_path(None)
        self.styled.set_path(None)
        self.meta.setText("尚未渲染")
        self.raw_meta.setText("")
        self.details_btn.setVisible(False)
        self.details_btn.setChecked(False)
        self.raw_meta.setVisible(False)
        self._applied = []
        self._unsupported = []
        self._substitutions = {}

    def _toggle_details(self, checked: bool) -> None:
        self.raw_meta.setVisible(checked)
        self.details_btn.setText("收起细节" if checked else "技术细节")

    def _chart(self) -> None:
        self.chartChanged.emit(self.chart())

    def _enlarge(self) -> None:
        view = self.styled
        if self._kind == "A" and self._view_index == 1:
            view = self.baseline
        self._enlarge_from(view)

    def _enlarge_from(self, view: ScaledView) -> None:
        pix = view.source_pixmap()
        if pix.isNull():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("放大预览")
        lay = QVBoxLayout(dlg)
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setPixmap(pix)
        label.resize(pix.size())
        scroll.setWidget(label)
        lay.addWidget(scroll)
        dlg.resize(min(1100, pix.width() + 48), min(800, pix.height() + 48))
        dlg.exec()


class LocalStyleExtractionWindow(QMainWindow):
    sourceLoaded = pyqtSignal()
    saveToLibraryRequested = pyqtSignal(dict, bool)

    def __init__(self) -> None:
        super().__init__()
        self.api = bind_engine()
        self.theme = "dark"
        self.layout_mode = "A"
        self.stage_index = 0
        self.source_path = ""
        self.page_index = 0
        self.page_count = 0
        self.crop = [0.0, 0.0, 1.0, 1.0]
        self.draft: dict[str, Any] | None = None
        self.origin_status: dict[str, str] = {}
        self.preview_paths: dict[str, str] = {}
        self.unsupported_keys: list[str] = []
        self.font_substitutions: dict[str, str] = {}
        self.view_gen = 0
        self.extract_gen = 0
        self.preview_gen = 0
        self._jobs: dict[str, dict[str, Any]] = {}
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._run_preview)
        self.worker = EngineJobThread()
        self.worker.job_ok.connect(self._on_job_ok)
        self.worker.job_err.connect(self._on_job_err)
        self.worker.start()
        self.setWindowTitle("ScieFig 本地样式提取")
        self.resize(1280, 800)
        self.setMinimumSize(QSize(0, 0))
        self._build()
        self.studio_ui = None
        if sys.platform in {'darwin', 'win32'}:
            from .studio_workbench import StudioStyleWorkbench
            self.studio_ui = StudioStyleWorkbench(self)
        self._apply_theme("dark")
        self._apply_arrangement("A" if self.studio_ui else "B")
        self._refresh_enabled()
        self._set_status('不上传原文件' if self.studio_ui else self.api.status_line())

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.top = QFrame()
        self.top.setObjectName("TopBar")
        self.top.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        top = QVBoxLayout(self.top)
        top.setContentsMargins(8, 4, 8, 4)
        top.setSpacing(4)
        brand_row = QHBoxLayout()
        brand = QLabel("ScieFig 样式提取")
        brand.setObjectName("BrandLabel")
        brand_row.addWidget(brand)
        experimental = QLabel("选择来源 → 提取与编辑 → 保存到样式库")
        experimental.setObjectName("DimLabel")
        brand_row.addWidget(experimental)
        brand_row.addStretch(1)
        privacy = QLabel("本地分析 · 不上传")
        privacy.setToolTip("本地运行，无遥测，不调用远程字体或资源。")
        privacy.setObjectName("SectionLabel")
        brand_row.addWidget(privacy)
        self.toolbar_toggle = QToolButton()
        self.toolbar_toggle.setObjectName("ToolbarCollapseButton")
        self.toolbar_toggle.setCheckable(True)
        self.toolbar_toggle.setChecked(True)
        self.toolbar_toggle.setText("收起工具")
        self.toolbar_toggle.setArrowType(Qt.ArrowType.UpArrow)
        self.toolbar_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar_toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toolbar_toggle.setAccessibleName("展开或收起样式工具")
        self.toolbar_toggle.toggled.connect(self._set_toolbar_expanded)
        brand_row.addWidget(self.toolbar_toggle)
        top.addLayout(brand_row)
        self.toolbar_body = QFrame()
        self.toolbar_body.setObjectName("ExtractionToolbarBody")
        controls = QVBoxLayout(self.toolbar_body)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        top.addWidget(self.toolbar_body)
        self.btn_open = QPushButton("打开…")
        self.btn_open.clicked.connect(self._open_dialog)
        self.btn_examples = QPushButton("语料示例")
        self.btn_extract = QPushButton("提取所选区域")
        self.btn_extract.setObjectName("PrimaryButton")
        self.btn_extract.clicked.connect(self._extract)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("样式名称")
        self.name_edit.setText("未命名本地样式")
        self.name_edit.setMaximumWidth(180)
        self.name_edit.textEdited.connect(self._name_edited)
        self.btn_save = QPushButton("保存样式")
        self.btn_save.clicked.connect(self._save_json)
        self.btn_library = QPushButton("保存到样式库")
        self.btn_library.clicked.connect(lambda: self._save_library(False))
        self.btn_apply = QPushButton("保存并应用")
        self.btn_apply.clicked.connect(lambda: self._save_library(True))
        self.btn_load = QPushButton("打开样式")
        self.btn_saved = QPushButton("已保存样式")
        self.btn_load.clicked.connect(self._load_json)
        self.btn_export = QPushButton("导出")
        export_menu = QMenu(self.btn_export)
        export_menu.addAction("导出 PNG", lambda: self._export("png"))
        export_menu.addAction("导出 SVG", lambda: self._export("svg"))
        self.btn_export.setMenu(export_menu)
        self.btn_cancel = QPushButton("丢弃待处理")
        self.btn_cancel.clicked.connect(self._cancel_pending)
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        for widget in (
            self.btn_open,
            self.btn_extract,
            self.name_edit,
            self.btn_library,
            self.btn_apply,
            self.btn_load,
            self.btn_saved,
        ):
            row1.addWidget(widget)
        row1.addStretch(1)
        controls.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        row2.addWidget(self.btn_export)
        self.btn_save.setText("导出样式 JSON")
        row2.addWidget(self.btn_save)
        self.btn_examples.hide()
        row2.addWidget(self.btn_cancel)
        row2.addStretch(1)
        controls.addLayout(row2)
        self.action_msg = QLabel("")
        self.action_msg.setObjectName("ErrorLabel")
        self.action_msg.setWordWrap(True)
        self.action_msg.hide()
        top.addWidget(self.action_msg)
        self.mismatch = QLabel("")
        self.mismatch.setObjectName("WarnLabel")
        self.mismatch.setWordWrap(True)
        self.mismatch.hide()
        top.addWidget(self.mismatch)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(8)
        top.addWidget(self.progress)
        root.addWidget(self.top)
        self.source_board = SourceBoard()
        self.token_editor = TokenEditor()
        self.preview_board = PreviewBoard()
        self.source_board.pageChangeRequested.connect(self._change_page)
        self.source_board.cropCommitted.connect(self._crop_committed)
        self.source_board.regionChosen.connect(self._region_chosen)
        self.token_editor.tokenSelected.connect(self._token_selected)
        self.token_editor.tokenEdited.connect(self._token_edited)
        self.token_editor.tokenReset.connect(self._token_reset)
        self.preview_board.chartChanged.connect(self._chart_changed)
        self.body_stack = QStackedWidget()
        self.page_a = QWidget()
        lay_a = QVBoxLayout(self.page_a)
        lay_a.setContentsMargins(8, 8, 8, 8)
        self.split_a = QSplitter()
        self.split_a.setChildrenCollapsible(False)
        lay_a.addWidget(self.split_a)
        self.page_b = QWidget()
        lay_b = QVBoxLayout(self.page_b)
        lay_b.setContentsMargins(8, 8, 8, 8)
        self.stage_header = QFrame()
        self.stage_header.setObjectName("StageHeader")
        stage_row = QHBoxLayout(self.stage_header)
        stage_row.setContentsMargins(8, 4, 8, 4)
        self.btn_stage1 = QPushButton("1 选择来源")
        self.btn_stage2 = QPushButton("2 编辑与预览")
        self.btn_stage1.setCheckable(True)
        self.btn_stage2.setCheckable(True)
        self.btn_stage1.setObjectName("StageButton")
        self.btn_stage2.setObjectName("StageButton")
        self.btn_stage1.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_stage2.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_stage1.clicked.connect(lambda: self._set_stage(0))
        self.btn_stage2.clicked.connect(lambda: self._set_stage(1))
        self.btn_prev_stage = QPushButton("上一步")
        self.btn_next_stage = QPushButton("下一步")
        self.btn_prev_stage.clicked.connect(lambda: self._set_stage(max(0, self.stage_index - 1)))
        self.btn_next_stage.clicked.connect(lambda: self._set_stage(min(1, self.stage_index + 1)))
        stage_row.addWidget(self.btn_stage1)
        stage_row.addWidget(self.btn_stage2)
        stage_row.addStretch(1)
        stage_row.addWidget(self.btn_prev_stage)
        stage_row.addWidget(self.btn_next_stage)
        lay_b.addWidget(self.stage_header)
        self.stage_stack = QStackedWidget()
        self.stage_source = QWidget()
        self.stage_source_layout = QVBoxLayout(self.stage_source)
        self.stage_source_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_edit = QWidget()
        self.split_b = QSplitter()
        self.split_b.setChildrenCollapsible(False)
        edit_layout = QVBoxLayout(self.stage_edit)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.addWidget(self.split_b)
        self.stage_stack.addWidget(self.stage_source)
        self.stage_stack.addWidget(self.stage_edit)
        lay_b.addWidget(self.stage_stack, 1)
        self.body_stack.addWidget(self.page_a)
        self.body_stack.addWidget(self.page_b)
        root.addWidget(self.body_stack, 1)
        self.bottom = QFrame()
        self.bottom.setObjectName("BottomBar")
        bottom = QHBoxLayout(self.bottom)
        bottom.setContentsMargins(10, 4, 10, 4)
        switch_hint = QLabel("演示布局（不拦截编辑框方向键）")
        switch_hint.setObjectName("DimLabel")
        bottom.addWidget(switch_hint)
        self.btn_a = QPushButton("三栏视图")
        self.btn_b = QPushButton("分步视图")
        self.btn_a.setCheckable(True)
        self.btn_b.setCheckable(True)
        self.btn_a.setObjectName("LayoutButton")
        self.btn_b.setObjectName("LayoutButton")
        self.btn_a.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_a.clicked.connect(lambda: self._apply_arrangement("A"))
        self.btn_b.clicked.connect(lambda: self._apply_arrangement("B"))
        bottom.addWidget(self.btn_a)
        bottom.addWidget(self.btn_b)
        bottom.addStretch(1)
        self.btn_dark = QPushButton("深色")
        self.btn_light = QPushButton("浅色")
        self.btn_dark.setCheckable(True)
        self.btn_light.setCheckable(True)
        self.btn_dark.setObjectName("ThemeButton")
        self.btn_light.setObjectName("ThemeButton")
        self.btn_dark.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_light.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_dark.clicked.connect(lambda: self._apply_theme("dark"))
        self.btn_light.clicked.connect(lambda: self._apply_theme("light"))
        bottom.addWidget(self.btn_dark)
        bottom.addWidget(self.btn_light)
        root.addWidget(self.bottom)
        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("")
        status.addWidget(self.status_label, 1)
        self._build_examples_menu()

    def _build_examples_menu(self) -> None:
        self.btn_examples.hide()

    def _apply_theme(self, theme: str) -> None:
        self.theme = theme
        self.setStyleSheet(stylesheet(theme))
        self.btn_dark.setChecked(theme == "dark")
        self.btn_light.setChecked(theme == "light")

    def _apply_arrangement(self, mode: str) -> None:
        self.layout_mode = mode
        self.btn_a.setChecked(mode == "A")
        self.btn_b.setChecked(mode == "B")
        if self.studio_ui:
            self.studio_ui.arrange(mode)
            return
        self.source_board.setParent(None)
        self.token_editor.setParent(None)
        self.preview_board.setParent(None)
        if mode == "A":
            self.split_a.addWidget(self.source_board)
            self.split_a.addWidget(self.token_editor)
            self.split_a.addWidget(self.preview_board)
            self.split_a.setSizes([420, 380, 460])
            self.split_a.setStretchFactor(0, 3)
            self.split_a.setStretchFactor(1, 3)
            self.split_a.setStretchFactor(2, 4)
            self.body_stack.setCurrentWidget(self.page_a)
            self.preview_board.set_layout_kind("A")
        else:
            self.stage_source_layout.addWidget(self.source_board)
            self.split_b.addWidget(self.token_editor)
            self.split_b.addWidget(self.preview_board)
            self.split_b.setSizes([420, 520])
            self.body_stack.setCurrentWidget(self.page_b)
            self.preview_board.set_layout_kind("B")
            self._set_stage(self.stage_index)

    def _set_stage(self, index: int) -> None:
        self.stage_index = 0 if index <= 0 else 1
        self.stage_stack.setCurrentIndex(self.stage_index)
        self.btn_stage1.setChecked(self.stage_index == 0)
        self.btn_stage2.setChecked(self.stage_index == 1)
        self.btn_prev_stage.setEnabled(self.stage_index > 0)
        self.btn_next_stage.setEnabled(self.stage_index < 1)
        if self.studio_ui:
            self.studio_ui.stage_changed()

    def _set_toolbar_expanded(self, expanded: bool) -> None:
        self.toolbar_body.setVisible(expanded)
        self.toolbar_toggle.setText("收起工具" if expanded else "展开工具")
        self.toolbar_toggle.setArrowType(Qt.ArrowType.UpArrow if expanded else Qt.ArrowType.DownArrow)

    def _set_action_error(self, text: str) -> None:
        self.action_msg.setObjectName("ErrorLabel")
        self.action_msg.style().unpolish(self.action_msg)
        self.action_msg.style().polish(self.action_msg)
        self.action_msg.setText(text)
        self.action_msg.setToolTip(text)
        self.action_msg.setVisible(bool(text.strip()))

    def _set_action_note(self, text: str) -> None:
        self.action_msg.setObjectName("WarnLabel")
        self.action_msg.style().unpolish(self.action_msg)
        self.action_msg.style().polish(self.action_msg)
        self.action_msg.setText(text)
        self.action_msg.setToolTip(text)
        self.action_msg.setVisible(bool(text.strip()))

    def _set_status(self, extra: str = "") -> None:
        bits = ["本地分析"]
        if self.source_path:
            bits.append(Path(self.source_path).name)
            bits.append(f"页 {self.page_index + 1}/{max(1, self.page_count)}")
        if extra:
            bits.append(extra)
        self.status_label.setText(" · ".join(bits))
        self.status_label.setToolTip(self.status_label.text())

    def _refresh_enabled(self) -> None:
        has_source = bool(self.source_path)
        self.btn_extract.setEnabled(has_source and self.api.can_extract)
        self.btn_save.setEnabled(bool(self.draft) and self.api.can_save)
        self.btn_library.setEnabled(bool(self.draft))
        self.btn_apply.setEnabled(bool(self.draft))
        self.btn_load.setEnabled(self.api.can_load)
        self.btn_export.setEnabled(bool(self.preview_paths.get("styled_png") or self.preview_paths.get("styled_svg")))
        self.btn_cancel.setEnabled(bool(self._jobs))
        self.progress.setVisible(bool(self._jobs))
        self._update_mismatch()
        if self.studio_ui:
            self.studio_ui.refresh()

    def _update_mismatch(self) -> None:
        if not self.draft:
            self.mismatch.setText("")
            self.mismatch.hide()
            return
        src = self.draft.get("source") or {}
        path_ok = same_source_path(str(src.get("path") or ""), self.source_path)
        page_ok = int(src.get("page_index") or 0) == int(self.page_index)
        rect_ok = same_rect(src.get("rect"), self.crop)
        if path_ok and page_ok and rect_ok:
            self.mismatch.setText("")
            self.mismatch.hide()
            return
        self.mismatch.setText("当前稿与来源页/裁剪不一致。再次提取需确认后才会替换现有稿，编辑不会自动重提取。")
        self.mismatch.show()

    def _confirm_replace(self, what: str) -> bool:
        if not self.draft:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("确认替换样式稿")
        box.setText(f"{what}将替换当前样式稿（含用户修改）。是否继续？")
        box.setInformativeText("取消则保留当前稿与上次有效结果。")
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    def _submit(self, job: Job) -> None:
        self._jobs[job.job_id] = {
            "kind": job.kind,
            "view_gen": self.view_gen,
            "extract_gen": self.extract_gen,
            "preview_gen": self.preview_gen,
            "meta": dict(job.meta),
        }
        self.worker.submit(job)
        self._refresh_enabled()

    def _invalidate_kinds(self, kinds: set[str]) -> None:
        drop = [jid for jid, info in self._jobs.items() if info["kind"] in kinds]
        for jid in drop:
            self.worker.discard_job(jid)
            self._jobs.pop(jid, None)

    def _open_dialog(self) -> None:
        self.api = bind_engine()
        if not self.api.can_open:
            self._set_action_error(self.api.explain() + "。未使用替身提取。")
            self._refresh_enabled()
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开本地 PDF 或图像", recent_directory(), IMAGE_FILTER, options=dialog_options())
        if path:
            self._open_path(path)

    def _open_example(self, item: dict[str, Any]) -> None:
        path = str(item.get("resolved_path") or "")
        picked = pick_figure_candidate(item)
        page_index = 0 if picked is None else int(picked["page_index"])
        crop = None if picked is None else list(picked["rect"])
        self._open_source(path, page_index=page_index, example_crop=crop)

    def _open_path(self, path: str) -> None:
        remember_file_directory(path)
        self._open_source(path, page_index=0, example_crop=None)

    def _open_source(self, path: str, page_index: int = 0, example_crop: list[float] | None = None) -> None:
        self.api = bind_engine()
        if not self.api.can_open:
            self._set_action_error(self.api.explain() + "。未使用替身提取。")
            self._refresh_enabled()
            return
        if not Path(path).is_file():
            self._set_action_error("文件不存在：" + path)
            return
        if not self._confirm_replace("打开新来源"):
            return
        self.view_gen += 1
        self.extract_gen += 1
        self.preview_gen += 1
        self._invalidate_kinds({"open", "page", "thumb", "extract", "preview"})
        out_dir = new_run_dir("open")
        meta: dict[str, Any] = {
            "api": self.api,
            "path": str(Path(path).resolve()),
            "page_index": int(page_index),
            "out_dir": str(out_dir),
        }
        if example_crop is not None:
            meta["example_crop"] = list(normalize_rect(example_crop))
        job = Job(
            kind="open",
            generation=self.view_gen,
            func=None,
            meta=meta,
        )
        self._set_action_note("正在打开本地文件…")
        self._submit(job)

    def _change_page(self, index: int) -> None:
        if not self.source_path or index == self.page_index:
            return
        if index < 0 or index >= max(1, self.page_count):
            return
        self.api = bind_engine()
        if not self.api.can_open:
            self._set_action_error(self.api.explain())
            return
        self.page_index = index
        self.source_board.set_current_page(index)
        self.view_gen += 1
        self.extract_gen += 1
        self._invalidate_kinds({"open", "page", "extract"})
        out_dir = new_run_dir("page")
        job = Job(
            kind="page",
            generation=self.view_gen,
            func=None,
            meta={"api": self.api, "path": self.source_path, "page_index": index, "out_dir": str(out_dir)},
        )
        self._set_action_note("正在渲染页面…")
        self._submit(job)
        self._update_mismatch()

    def _crop_committed(self, rect: list[float]) -> None:
        self.crop = normalize_rect(rect)
        self.extract_gen += 1
        self._invalidate_kinds({"extract"})
        self._update_mismatch()
        self._set_status("已更新裁剪，需手动提取")

    def _region_chosen(self, rect: list[float], region_id: str) -> None:
        self.crop = normalize_rect(rect)
        self.extract_gen += 1
        self._invalidate_kinds({"extract"})
        self._update_mismatch()
        self._set_status("已采用建议区域 " + (region_id or ""))

    def _extract(self) -> None:
        self.api = bind_engine()
        if not self.api.can_extract:
            self._set_action_error(self.api.explain() + "。不会伪造成功提取。")
            self._refresh_enabled()
            return
        if not self.source_path:
            self._set_action_error("请先打开来源并框选图区。")
            return
        if not self._confirm_replace("重新提取"):
            return
        self.extract_gen += 1
        self.preview_gen += 1
        self._invalidate_kinds({"extract", "preview"})
        out_dir = new_run_dir("extract")
        job = Job(
            kind="extract",
            generation=self.extract_gen,
            func=self.api.extract_style,
            args=(self.source_path, self.page_index, list(self.crop), str(out_dir)),
            meta={"run_dir": str(out_dir), "extract_gen": self.extract_gen, "source_path": self.source_path, "page_index": self.page_index, "crop": list(self.crop)},
        )
        self._set_action_note("正在本地提取…")
        self._submit(job)

    def _name_edited(self, text: str) -> None:
        if not self.draft:
            return
        self.draft = dict(self.draft)
        self.draft["name"] = text

    def _token_selected(self, key: str) -> None:
        boxes = self.token_editor.evidence_for(self.draft, key)
        self.source_board.set_evidence(boxes)

    def _token_edited(self, key: str, value: Any) -> None:
        if not self.draft:
            return
        self.api = bind_engine()
        if not self.api.can_edit:
            self._set_action_error("edit_token 不可用，未改写稿件。")
            self.token_editor.set_draft(self.draft, self.unsupported_keys, self.font_substitutions)
            return
        previous = self.draft
        try:
            updated = self.api.edit_token(previous, key, value)
        except Exception as exc:
            self._set_action_error(f"编辑失败，已保留原稿：{exc}")
            self.token_editor.set_draft(previous, self.unsupported_keys, self.font_substitutions)
            return
        if not isinstance(updated, dict):
            self._set_action_error("edit_token 返回无效稿件，已保留原稿。")
            self.token_editor.set_draft(previous, self.unsupported_keys, self.font_substitutions)
            return
        self.draft = updated
        self.token_editor.set_draft(self.draft, self.unsupported_keys, self.font_substitutions)
        self._set_action_note("")
        self._schedule_preview()

    def _token_reset(self, key: str) -> None:
        if not self.draft:
            return
        tokens = self.draft.get("tokens") or {}
        if key not in tokens:
            return
        restored = copy.deepcopy(self.draft)
        token = restored["tokens"][key]
        token["value"] = token.get("original_value")
        token["status"] = self.origin_status.get(key) or infer_origin_status(token)
        if token["status"] == "user_edited":
            token["status"] = infer_origin_status(token)
        resolved = dict(restored.get("resolved_style") or {})
        if token["value"] is not None:
            resolved[key] = token["value"]
        restored["resolved_style"] = resolved
        self.draft = restored
        self.token_editor.set_draft(self.draft, self.unsupported_keys, self.font_substitutions)
        self._token_selected(key)
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._preview_timer.start(280)

    def _chart_changed(self, _chart: str) -> None:
        if self.draft:
            self._run_preview()

    def _run_preview(self) -> None:
        if not self.draft:
            return
        self.api = bind_engine()
        if not self.api.can_preview:
            self._set_action_error(self.api.explain() + "。预览未渲染。")
            return
        self.preview_gen += 1
        self._invalidate_kinds({"preview"})
        out_dir = new_run_dir("preview")
        job = Job(
            kind="preview",
            generation=self.preview_gen,
            func=self.api.render_comparison,
            args=(copy.deepcopy(self.draft), str(out_dir), self.preview_board.chart()),
            meta={"run_dir": str(out_dir), "preview_gen": self.preview_gen},
        )
        self._submit(job)

    def _save_json(self) -> None:
        if not self.draft:
            self._set_action_error("没有可保存的稿件。")
            return
        self.api = bind_engine()
        if not self.api.can_save:
            self._set_action_error(self.api.explain())
            return
        styles_dir = CACHE_DIR.parent / "style_drafts"
        styles_dir.mkdir(parents=True, exist_ok=True)
        default_name = (self.name_edit.text().strip() or "local-style") + ".json"
        path, _ = QFileDialog.getSaveFileName(self, "保存样式", str(styles_dir / default_name), JSON_FILTER, options=dialog_options())
        if not path:
            return
        name = self.name_edit.text().strip() or "未命名本地样式"
        draft = dict(self.draft)
        draft["name"] = name
        previous = self.draft
        try:
            self.api.save_style(draft, name, path)
        except Exception as exc:
            self.draft = previous
            self._set_action_error(f"保存失败，已保留内存中的稿：{exc}")
            return
        self.draft = draft
        self._set_action_note("已保存到 " + path)

    def _load_json(self) -> None:
        self.api = bind_engine()
        if not self.api.can_load:
            self._set_action_error(self.api.explain())
            return
        if not self._confirm_replace("打开样式稿"):
            return
        styles_dir = CACHE_DIR.parent / "style_drafts"
        styles_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "打开样式", str(styles_dir), JSON_FILTER, options=dialog_options())
        if not path:
            return
        previous = self.draft
        try:
            loaded = self.api.load_style(path)
        except Exception as exc:
            self.draft = previous
            self._set_action_error(f"打开失败，已保留原稿：{exc}")
            return
        if not isinstance(loaded, dict):
            self.draft = previous
            self._set_action_error("load_style 返回无效稿件，已保留原稿。")
            return
        self.draft = loaded
        self._remember_origin(loaded)
        self.name_edit.setText(str(loaded.get("name") or "未命名本地样式"))
        self.token_editor.set_draft(self.draft, self.unsupported_keys, self.font_substitutions)
        self._set_action_note("已打开 " + path)
        self._schedule_preview()
        self._refresh_enabled()
        src = loaded.get("source") or {}
        src_path = str(src.get("path") or "")
        if src_path and Path(src_path).is_file() and self.api.can_open:
            self._reload_source_visual(src_path, int(src.get("page_index") or 0), list(src.get("rect") or self.crop))

    def _reload_source_visual(self, path: str, page_index: int, rect: list[float]) -> None:
        self.source_path = str(Path(path).resolve())
        self.page_index = page_index
        self.crop = normalize_rect(rect)
        self.view_gen += 1
        self._invalidate_kinds({"open", "page", "thumb"})
        out_dir = new_run_dir("reload")
        job = Job(
            kind="open",
            generation=self.view_gen,
            func=None,
            meta={"api": self.api, "path": self.source_path, "page_index": page_index, "out_dir": str(out_dir), "keep_draft": True, "crop": list(self.crop)},
        )
        self._submit(job)

    def _export(self, kind: str) -> None:
        key = "styled_png" if kind == "png" else "styled_svg"
        src = self.preview_paths.get(key)
        if not src or not Path(src).is_file():
            self._set_action_error("没有可导出的预览文件。请先成功渲染预览。")
            return
        suffix = ".png" if kind == "png" else ".svg"
        filt = "PNG (*.png)" if kind == "png" else "SVG (*.svg)"
        default = Path(recent_directory()) / ("styled" + suffix)
        path, _ = QFileDialog.getSaveFileName(self, "导出预览", str(default), filt, options=dialog_options())
        if not path:
            return
        try:
            shutil.copyfile(src, path)
        except Exception as exc:
            self._set_action_error(f"导出失败：{exc}")
            return
        self._set_action_note("已复制预览原文件到 " + path)

    def _cancel_pending(self) -> None:
        for jid in list(self._jobs):
            self.worker.discard_job(jid)
        self.worker.drain_pending()
        self._jobs.clear()
        self.preview_gen += 1
        self.extract_gen += 1
        self._set_action_note("已丢弃待处理结果。底层库若无法中断，计算仍可能结束，但不会套用到当前界面。")
        self._refresh_enabled()

    def _remember_origin(self, draft: dict[str, Any]) -> None:
        self.origin_status = {}
        for key, token in (draft.get("tokens") or {}).items():
            if isinstance(token, dict):
                self.origin_status[str(key)] = infer_origin_status(token)

    def _on_job_ok(self, job_id: str, kind: str, generation: int, payload: object) -> None:
        info = self._jobs.pop(job_id, None)
        if info is None:
            return
        try:
            if kind in {"open", "page", "thumb"} and generation != self.view_gen:
                return
            if kind == "open":
                self._apply_open(info, payload)
            elif kind == "page":
                self._apply_page(info, payload)
            elif kind == "thumb":
                self._apply_thumb(payload)
            elif kind == "extract":
                if generation != self.extract_gen:
                    return
                self._apply_extract(payload)
            elif kind == "preview":
                if generation != self.preview_gen:
                    return
                self._apply_preview(info, payload)
        finally:
            self._refresh_enabled()
            self._set_status()

    def _on_job_err(self, job_id: str, kind: str, generation: int, message: str) -> None:
        if job_id not in self._jobs:
            return
        self._jobs.pop(job_id, None)
        labels = {
            "open": "打开",
            "page": "翻页渲染",
            "thumb": "缩略图",
            "extract": "提取",
            "preview": "预览",
        }
        self._set_action_error(f"{labels.get(kind, kind)}失败，已保留上次有效结果：{message}")
        self._refresh_enabled()

    def _apply_open(self, info: dict[str, Any], payload: object) -> None:
        if not isinstance(payload, dict):
            self._set_action_error("打开结果无效，已保留上次来源。")
            return
        if info.get("view_gen") != self.view_gen and "view_gen" in info:
            pass
        keep_draft = bool((info.get("meta") or {}).get("keep_draft"))
        path = str(payload.get("path") or "")
        info_doc = payload.get("info") or {}
        page_count = int(info_doc.get("page_count") or 1)
        page_index = int(payload.get("page_index") or 0)
        png = str(payload.get("png") or "")
        regions = payload.get("regions") or []
        self.source_path = path
        self.page_count = max(1, page_count)
        self.page_index = page_index
        self.source_board.set_page_count(self.page_count, self.page_index)
        self.source_board.set_page_image(png if Path(png).is_file() else None)
        self.source_board.set_regions(regions if isinstance(regions, list) else [])
        example_crop = (info.get("meta") or {}).get("example_crop") if "example_crop" in (info.get("meta") or {}) else None
        if keep_draft:
            crop = (info.get("meta") or {}).get("crop") or self.crop
            self.crop = normalize_rect(crop)
            self.source_board.set_crop(self.crop, as_manual=True)
        elif example_crop is not None:
            self.crop = normalize_rect(example_crop)
            self.source_board.set_crop(self.crop, as_manual=True)
        elif isinstance(regions, list) and regions and isinstance(regions[0], dict) and regions[0].get("rect"):
            self.crop = normalize_rect(regions[0].get("rect"))
            self.source_board.set_crop(self.crop)
            self.source_board.select_region_id(str(regions[0].get("id") or ""))
        else:
            self.crop = [0.0, 0.0, 1.0, 1.0]
            self.source_board.set_crop(self.crop, as_manual=True)
        if png and Path(png).is_file():
            self.source_board.set_thumb(page_index, png)
        if not keep_draft:
            self.draft = None
            self.origin_status = {}
            self.preview_paths = {}
            self.unsupported_keys = []
            self.font_substitutions = {}
            self.preview_board.clear()
            self.token_editor.set_draft(None)
            self.name_edit.setText("未命名本地样式")
            self.source_board.set_evidence([])
        if example_crop is not None:
            self._set_action_note(f"示例候选：第 {self.page_index + 1} 页已框选（未提取）。可改框后手动提取。")
        else:
            self._set_action_note("")
        self._enqueue_thumbs(path, self.page_count, page_index)
        self.sourceLoaded.emit()

    def _apply_page(self, info: dict[str, Any], payload: object) -> None:
        if not isinstance(payload, dict):
            self._set_action_error("翻页结果无效。")
            return
        png = str(payload.get("png") or "")
        regions = payload.get("regions") or []
        page_index = int(payload.get("page_index") or self.page_index)
        self.page_index = page_index
        self.source_board.set_current_page(page_index)
        self.source_board.set_page_image(png if Path(png).is_file() else None)
        self.source_board.set_regions(regions if isinstance(regions, list) else [])
        if isinstance(regions, list) and regions and isinstance(regions[0], dict) and regions[0].get("rect"):
            self.crop = normalize_rect(regions[0].get("rect"))
            self.source_board.set_crop(self.crop)
            self.source_board.select_region_id(str(regions[0].get("id") or ""))
        else:
            self.crop = [0.0, 0.0, 1.0, 1.0]
            self.source_board.set_crop(self.crop, as_manual=True)
        if png and Path(png).is_file():
            self.source_board.set_thumb(page_index, png)
        self.source_board.set_evidence([])
        self._set_action_note("")

    def _apply_thumb(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        png = str(payload.get("png") or "")
        index = int(payload.get("page_index") or 0)
        if png and Path(png).is_file():
            self.source_board.set_thumb(index, png)

    def _apply_extract(self, payload: object) -> None:
        if not isinstance(payload, dict):
            self._set_action_error("提取结果无效，已保留原稿。")
            return
        self.draft = payload
        self._remember_origin(payload)
        name = str(payload.get("name") or "未命名本地样式")
        if self.studio_ui and name.strip() in {'Untitled local style', 'Untitled local Style'}:
            name = '未命名本地样式'
        self.name_edit.setText(name)
        self.token_editor.set_draft(self.draft, self.unsupported_keys, self.font_substitutions)
        src = payload.get("source") or {}
        if src.get("rect"):
            self.crop = normalize_rect(src.get("rect"))
            self.source_board.set_crop(self.crop)
        self._set_action_note("")
        self._schedule_preview()
        selected = self.token_editor.selected_key() or MIN_TOKEN_KEYS[0]
        self._token_selected(selected)

    def _apply_preview(self, info: dict[str, Any], payload: object) -> None:
        if not isinstance(payload, dict):
            self._set_action_error("预览结果无效，已保留上次预览。")
            return
        run_dir = Path(str((info.get("meta") or {}).get("run_dir") or "."))
        baseline = _as_abs(payload.get("baseline_png"), run_dir)
        styled = _as_abs(payload.get("styled_png"), run_dir)
        svg = _as_abs(payload.get("styled_svg"), run_dir)
        applied = [str(k) for k in (payload.get("applied_keys") or [])]
        unsupported = [str(k) for k in (payload.get("unsupported_keys") or [])]
        substitutions = payload.get("font_substitutions") or {}
        if not isinstance(substitutions, dict):
            substitutions = {}
        self.unsupported_keys = unsupported
        self.font_substitutions = {str(k): str(v) for k, v in substitutions.items()}
        self.preview_paths = {"baseline_png": baseline, "styled_png": styled, "styled_svg": svg}
        self.preview_board.set_images(
            baseline or None,
            styled or None,
            applied,
            unsupported,
            self.font_substitutions,
        )
        if self.draft:
            self.token_editor.set_draft(self.draft, unsupported, self.font_substitutions)
        self._set_action_note("")

    def _enqueue_thumbs(self, path: str, page_count: int, current: int) -> None:
        if not self.api.can_open:
            return
        limit = min(page_count, 16)
        out_dir = new_run_dir("thumbs")
        for index in range(limit):
            if index == current:
                continue
            job = Job(
                kind="thumb",
                generation=self.view_gen,
                func=None,
                meta={"api": self.api, "path": path, "page_index": index, "out_dir": str(out_dir)},
            )
            self._submit(job)

    def _save_library(self, activate: bool) -> None:
        if self.draft is None:
            self._set_action_error("请先提取或打开样式。")
            return
        self.draft["name"] = self.name_edit.text().strip() or "提取样式"
        self.saveToLibraryRequested.emit(copy.deepcopy(self.draft), activate)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._cancel_pending()
        self.worker.shutdown()
        self.worker.wait(2500)
        event.accept()

