from __future__ import annotations

from typing import Any
import sys
from .demo_common import EXTRA_KEYS
import sys

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

MIN_TOKEN_KEYS = (
    "series_colors",
    "background_color",
    "label_color",
    "font_family",
    "font_size_pt",
    "axis_label_size_pt",
    "tick_label_size_pt",
    "legend_size_pt",
    "line_width",
    "axis_line_width",
    "point_size",
    "point_shape",
    "show_top_spine",
    "show_right_spine",
    "gridline_visible",
    "gridline_color",
)
MIN_TOKEN_KEYS += EXTRA_KEYS

TOKEN_LABELS = {
    "series_colors": "系列配色",
    "background_color": "背景色",
    "label_color": "文字/标签色",
    "font_family": "字体",
    "font_size_pt": "正文字号 (pt)",
    "axis_label_size_pt": "轴标题字号 (pt)",
    "tick_label_size_pt": "刻度字号 (pt)",
    "legend_size_pt": "图例字号 (pt)",
    "line_width": "线条宽度",
    "axis_line_width": "轴线宽度",
    "point_size": "点大小",
    "point_shape": "点形状",
    "show_top_spine": "显示上边框",
    "show_right_spine": "显示右边框",
    "gridline_visible": "显示网格",
    "gridline_color": "网格颜色",
}

TOKEN_KINDS = {
    "series_colors": "colors",
    "background_color": "color",
    "label_color": "color",
    "font_family": "font",
    "font_size_pt": "float",
    "axis_label_size_pt": "float",
    "tick_label_size_pt": "float",
    "legend_size_pt": "float",
    "line_width": "float",
    "axis_line_width": "float",
    "point_size": "float",
    "point_shape": "shape",
    "show_top_spine": "bool",
    "show_right_spine": "bool",
    "gridline_visible": "bool",
    "gridline_color": "color",
}
TOKEN_LABELS.update(font_weight="字体粗细", bar_fill_color="柱体填充色", bar_edge_color="柱体边线色",
                    bar_line_width="柱体边线 (pt)", bar_use_series_colors="柱体使用系列色",
                    bar_width_fraction="柱宽/组间距", point_line_width="散点边线 (pt)",
                    sem_line_width="误差线宽 (pt)", sem_cap_size="误差线端帽半宽 (pt)",
                    sem_cap_thick="端帽线宽 (pt)", bracket_line_width="显著性括号线宽 (pt)",
                    tick_width="刻度线宽 (pt)", tick_length="Y 刻度长度 (pt)", point_size="散点直径 (pt)",
                    x_tick_length="X 刻度长度 (pt)", axes_box_aspect="绘图区高/宽", x_tick_rotation="X 标签角度 (°)")
TOKEN_KINDS.update({key:"float" for key in EXTRA_KEYS})
TOKEN_KINDS.update(font_weight="weight", bar_fill_color="color", bar_edge_color="color", bar_use_series_colors="bool")

SHAPE_OPTIONS = ("circle", "square", "diamond")
UNKNOWN_SENTINEL = -1.0


def is_anonymous_font_family(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    compact = text.replace(" ", "")
    lower = compact.lower()
    if lower.startswith("adv") and compact.isalnum():
        return True
    if lower.startswith("vcbdpf"):
        return True
    return len(compact) <= 2


def font_candidates(token: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(token, dict):
        return []
    raw = token.get("candidates")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        family = str(item.get("family") or "").strip()
        if not family or family in seen:
            continue
        seen.add(family)
        out.append(
            {
                "family": family,
                "score": item.get("score"),
                "method": str(item.get("method") or ""),
            }
        )
    return out


def candidate_font_label(item: dict[str, Any]) -> str:
    family = str(item.get("family") or "")
    score = item.get("score")
    text = family + "（候选"
    if score is not None and str(score) != "":
        try:
            text += f" 分数 {float(score):.3g}"
        except (TypeError, ValueError):
            text += f" 分数 {score}"
    return text + "）"

METHOD_ZH = {
    "pdf_geometry": "PDF 几何推定",
    "pdf_rendered": "PDF 可见颜色",
    "pdf_native": "PDF 原生",
    "raster_estimate": "光栅估计",
    "none": "无",
    "fallback": "回退",
}

REASON_ZH_CONTAINS = (
    ("native pdf primitives", "来自所选区域内的 PDF 矢量/文字"),
    ("legend color patches", "图例色块顺序（先上后下，再左到右）"),
    ("crop-relative", "光栅线宽按裁剪相对比例估算（未假定 DPI）"),
    ("100 pt width", "线宽按 100 pt 参考宽度换算"),
    ("no identifiable markers", "所选区域未识别到标记点"),
    ("marker shape not identifiable", "未能识别点形状"),
    ("user override", "用户改写，原始证据保留"),
    ("preview defaults", "预览默认值不是检出证据"),
    ("font identity unknown", "光栅文字无法识别字体"),
    ("raster typography", "光栅文字仅有相对字号，字体身份未知"),
    ("no legend patches", "未找到图例色块"),
    ("vertical mass", "系列色按光栅位置自上而下排序"),
    ("photo/micrograph", "照片/显微裁剪：不把像素色当作图表样式"),
    ("text-only crop", "纯文本裁剪：不从正文编造图表样式"),
    ("尚无稿件", "尚无稿件"),
    ("稿中无此标记", "稿中无此标记"),
)

WARNING_ZH_CONTAINS = (
    ("raster typography", "光栅文字仅有相对字号，字体身份未知"),
    ("font identity unknown", "光栅文字无法识别字体"),
    ("legend patches", "未找到图例色块，系列色按光栅位置排序"),
    ("raster series ordered", "系列色按光栅位置排序"),
    ("raster axis/line", "部分线宽为光栅估算（相对裁剪，未假定 DPI）"),
    ("crop-relative", "部分线宽为光栅估算（相对裁剪，未假定 DPI）"),
    ("preview defaults", "未检出项使用预览默认，不是证据"),
    ("photo/micrograph", "照片/显微裁剪未提取图表样式"),
    ("text-only crop", "纯文本裁剪未提取图表样式"),
)


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[Any] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:  # noqa: ANN001
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: ANN001
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: ANN001
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_h = 0
        space = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + space
            if next_x - space > effective.right() + 1 and line_h > 0:
                x = effective.x()
                y = y + line_h + space
                next_x = x + hint.width() + space
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


def status_kind(token: dict[str, Any], key: str, fallback_keys: list[str]) -> str:
    status = str(token.get("status") or "unknown")
    if status == "user_edited":
        return "edited"
    if status == "detected":
        return "detected"
    if status == "estimated":
        return "estimated"
    if key in fallback_keys or status in {"fallback", "default"}:
        return "fallback"
    return "unknown"


def status_text(kind: str) -> str:
    return {
        "detected": "检出",
        "estimated": "估计",
        "unknown": "未知",
        "fallback": "默认回退",
        "edited": "用户修改",
    }.get(kind, "未知")


def status_object_name(kind: str) -> str:
    return {
        "detected": "StatusDetected",
        "estimated": "StatusEstimated",
        "unknown": "StatusUnknown",
        "fallback": "StatusFallback",
        "edited": "StatusEdited",
    }.get(kind, "StatusUnknown")


def zh_lookup(text: str, table: tuple[tuple[str, str], ...]) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    for needle, zh in table:
        if needle in low or needle == raw:
            return zh
    return ""


def zh_reason(reason: str) -> str:
    return zh_lookup(reason, REASON_ZH_CONTAINS)


def zh_warning(text: str) -> str:
    return zh_lookup(text, WARNING_ZH_CONTAINS)


def token_method(tokens: dict[str, Any], key: str) -> str:
    item = tokens.get(key) or {}
    return str(item.get("method") or "") if isinstance(item, dict) else ""


def token_status(tokens: dict[str, Any], key: str) -> str:
    item = tokens.get(key) or {}
    return str(item.get("status") or "") if isinstance(item, dict) else ""


def is_native_token(tokens: dict[str, Any], key: str) -> bool:
    return token_method(tokens, key) == "pdf_native" or token_status(tokens, key) == "detected"


def filter_draft_warnings(draft: dict[str, Any], warnings: list[Any]) -> list[str]:
    tokens = draft.get("tokens") or {}
    kept: list[str] = []
    for raw in warnings:
        text = str(raw or "").strip()
        if not text:
            continue
        low = text.lower()
        if "font identity" in low or "raster typography" in low:
            if is_native_token(tokens, "font_family"):
                continue
        if "legend patches" in low or "raster series ordered" in low:
            if is_native_token(tokens, "series_colors"):
                continue
        if "raster axis/line" in low or ("crop-relative" in low and "width" in low):
            if is_native_token(tokens, "line_width") and is_native_token(tokens, "axis_line_width"):
                continue
        kept.append(text)
    return kept


def format_key_list(keys: list[str], limit: int = 6) -> str:
    labels = [TOKEN_LABELS.get(str(k), str(k)) for k in keys]
    if not labels:
        return "无"
    if len(labels) <= limit:
        return "、".join(labels)
    return "、".join(labels[:limit]) + f" 等{len(labels)}项"


class ColorButton(QPushButton):
    colorPicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hex = ""
        self.setFixedHeight(28 if sys.platform in {'darwin', 'win32'} else 24)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(56)
        self.clicked.connect(self._pick)

    def set_hex(self, value: str | None) -> None:
        self._hex = str(value or "").strip()
        if self._hex:
            self.setText(self._hex)
            color = QColor(self._hex)
            channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                        for value in (color.redF(), color.greenF(), color.blueF())]
            luminance = sum(channel * weight for channel, weight in zip(channels, (.2126, .7152, .0722)))
            ink = '#ffffff' if luminance < .18 else '#111111'
            self.setStyleSheet(f"background:{self._hex}; color:{ink}; min-width:56px;")
            self.setToolTip(self._hex + ' · 点击修改颜色')
        else:
            self.setText("未检出")
            self.setStyleSheet("min-width:56px;")

    def _pick(self) -> None:
        initial = QColor(self._hex) if self._hex else QColor("#808080")
        color = QColorDialog.getColor(initial, self, "选择颜色")
        if not color.isValid():
            return
        hex_value = color.name().upper()
        self.set_hex(hex_value)
        self.colorPicked.emit(hex_value)


class TokenRow(QFrame):
    selected = pyqtSignal(str)
    valueEdited = pyqtSignal(str, object)
    resetRequested = pyqtSignal(str)

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TokenRow")
        self.key = key
        self._updating = False
        self.setProperty("selected", False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = QLabel(TOKEN_LABELS.get(key, key))
        self.title.setObjectName("TitleLabel")
        self.title.setWordWrap(True)
        self.status = QLabel("未知")
        self.status.setObjectName("StatusUnknown")
        self.reset_btn = QPushButton("还原证据")
        self.reset_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.reset_btn.clicked.connect(lambda: self.resetRequested.emit(self.key))
        top.addWidget(self.title, 1)
        top.addWidget(self.status)
        top.addWidget(self.reset_btn)
        layout.addLayout(top)
        self.editor = self._make_editor()
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.editor)
        self.reason = QLabel("")
        self.reason.setObjectName("DimLabel")
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.sub = QLabel("")
        self.sub.setObjectName("DimLabel")
        self.sub.setWordWrap(True)
        self.sub.setVisible(False)
        layout.addWidget(self.sub)
        self.details_btn = QToolButton()
        self.details_btn.setObjectName("LinkButton")
        self.details_btn.setCheckable(True)
        self.details_btn.setText("技术细节")
        self.details_btn.toggled.connect(self._toggle_details)
        layout.addWidget(self.details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.details = QLabel("")
        self.details.setObjectName("DimLabel")
        self.details.setWordWrap(True)
        self.details.setVisible(False)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)

    def _make_editor(self) -> QWidget:
        kind = TOKEN_KINDS.get(self.key, "float")
        if kind == "color":
            btn = ColorButton()
            btn.colorPicked.connect(lambda value: self._emit_value(value))
            return btn
        if kind == "colors":
            box = QWidget()
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            flow = FlowLayout(spacing=4)
            box.setLayout(flow)
            return box
        if kind == "font":
            combo = QComboBox()
            combo.setEditable(False)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
            combo.setMinimumWidth(80)
            combo.addItem("未识别（不编造字体身份）", userData="")
            for family in QFontDatabase.families():
                combo.addItem(family, userData=family)
            combo.currentIndexChanged.connect(self._font_changed)
            return combo
        if kind in {"shape", "weight"}:
            combo = QComboBox()
            combo.setMinimumWidth(80)
            combo.addItem("未知", userData="")
            if kind == "weight":
                combo.addItem("常规", userData="normal")
                combo.addItem("粗体", userData="bold")
            else:
                combo.addItem("圆形 circle", userData="circle")
                combo.addItem("方形 square", userData="square")
                combo.addItem("菱形 diamond", userData="diamond")
            combo.currentIndexChanged.connect(self._shape_changed)
            return combo
        if kind == "bool":
            box = QCheckBox("启用")
            box.setTristate(True)
            box.stateChanged.connect(self._bool_changed)
            return box
        spin = QDoubleSpinBox()
        spin.setRange(-181.0, 180.0) if self.key == 'x_tick_rotation' else spin.setRange(UNKNOWN_SENTINEL, 96.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSpecialValueText("未知")
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(72)
        spin.valueChanged.connect(self._float_changed)
        return spin

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        self.selected.emit(self.key)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def _toggle_details(self, checked: bool) -> None:
        self.details.setVisible(checked)
        self.details_btn.setText("收起细节" if checked else "技术细节")
        if self.property('studioCompact'):
            self.reason.setVisible(checked)
            self.details_btn.setText('收起' if checked else '依据')

    def _ensure_font_choice(self, family: str) -> int:
        combo = self.editor
        idx = combo.findData(family)
        if idx >= 0:
            return idx
        if not family:
            return 0
        combo.insertItem(1, family, userData=family)
        return 1

    def set_token(
        self,
        token: dict[str, Any],
        fallback_keys: list[str],
        unsupported: bool,
        font_substitutions: dict[str, str] | None = None,
    ) -> None:
        self._updating = True
        kind = status_kind(token, self.key, fallback_keys)
        value = token.get("value")
        if self.key == "font_family" and is_anonymous_font_family(str(value or "")) and kind == "detected":
            kind = "unknown"
        self.status.setText(status_text(kind))
        self.status.setObjectName(status_object_name(kind))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        reason = str(token.get("reason") or "").strip()
        method = str(token.get("method") or "")
        summary_bits: list[str] = []
        zh = zh_reason(reason)
        if zh:
            summary_bits.append(zh)
        elif reason:
            summary_bits.append("见技术细节")
        method_zh = METHOD_ZH.get(method, "")
        if method_zh and method not in {"none", ""}:
            summary_bits.append(method_zh)
        if unsupported:
            summary_bits.append("演示可预览；产品样式不含此项")
        if self.key == "font_family":
            cands = font_candidates(token)
            if cands:
                summary_bits.append("候选按字形相似度分数列出，不是概率")
            if kind == "estimated" or (cands and kind not in {"detected", "edited"}):
                summary_bits.append("候选估计，非检出身份")
            if is_anonymous_font_family(str(value or "")):
                summary_bits.append("匿名编码名不是可靠字体族")
            if kind in {"unknown", "fallback"}:
                summary_bits.append("不编造字体身份")
        self.reason.setText(" · ".join(summary_bits) if summary_bits else "无附加说明")
        raw_lines = []
        if reason:
            raw_lines.append(reason)
        if method:
            raw_lines.append("method=" + method)
        evidence = token.get("evidence") or []
        if evidence:
            raw_lines.append(f"evidence={len(evidence)}")
        if self.key == "font_family":
            for item in font_candidates(token):
                raw_lines.append(f"candidate {item['family']} score={item['score']} method={item['method']}")
        self.details.setText("\n".join(raw_lines))
        compact = bool(self.property('studioCompact'))
        has_details = bool(raw_lines or summary_bits) if compact else bool(raw_lines) and not zh
        self.details_btn.setVisible(has_details)
        if not has_details:
            self.details_btn.setChecked(False)
            self.details.setVisible(False)
        self._set_editor_value(value, token)
        self._set_font_substitute(value, font_substitutions or {}, token)
        if compact:
            self.reason.setVisible(self.details_btn.isChecked())
            self.details.setVisible(self.details_btn.isChecked() and bool(raw_lines))
            self.details_btn.setText('收起' if self.details_btn.isChecked() else '依据')
        self._updating = False

    def _set_font_substitute(self, value: Any, substitutions: dict[str, str], token: dict[str, Any] | None = None) -> None:
        if self.key != "font_family":
            self.sub.setText("")
            self.sub.setVisible(False)
            return
        token = token or {}
        status = str(token.get("status") or "")
        requested = str(value or "").strip()
        actual = str(substitutions.get(requested) or "").strip()
        anon = is_anonymous_font_family(requested)
        cands = font_candidates(token)
        if requested and actual and actual != requested:
            if anon:
                self.sub.setText(f"原始名：{requested} · 预览实际：{actual}（匿名编码不是可靠字体族）")
            elif status == "estimated" or cands:
                self.sub.setText(f"候选：{requested} · 预览实际：{actual}（候选不是检出身份）")
            elif status == "detected":
                self.sub.setText(f"检出：{requested} · 预览实际：{actual}（本机未安装原稿字体）")
            else:
                self.sub.setText(f"稿中：{requested} · 预览实际：{actual}（本机未安装原稿字体）")
            self.sub.setVisible(True)
            return
        if requested:
            if anon:
                self.sub.setText(f"原始名：{requested}（匿名编码不是可靠字体族）")
            elif status == "estimated" or cands:
                self.sub.setText(f"候选：{requested}（估计，非检出身份）")
            elif status == "detected":
                self.sub.setText(f"检出：{requested}")
            else:
                self.sub.setText("")
                self.sub.setVisible(False)
                return
            self.sub.setVisible(True)
            return
        self.sub.setText("")
        self.sub.setVisible(False)

    def _populate_font_combo(self, value: str, candidates: list[dict[str, Any]]) -> None:
        combo = self.editor
        combo.blockSignals(True)
        while combo.count() > 1:
            combo.removeItem(1)
        seen: set[str] = set()
        for item in candidates:
            family = str(item.get("family") or "")
            if not family or family in seen:
                continue
            seen.add(family)
            combo.addItem(candidate_font_label(item), userData=family)
        if value and value not in seen:
            if is_anonymous_font_family(value):
                combo.insertItem(1, f"{value}（匿名编码，非字体族）", userData=value)
            else:
                combo.insertItem(1, value, userData=value)
            seen.add(value)
        for family in QFontDatabase.families():
            fam = str(family)
            if fam in seen:
                continue
            seen.add(fam)
            combo.addItem(fam, userData=fam)
        if value:
            idx = combo.findData(value)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _set_editor_value(self, value: Any, token: dict[str, Any] | None = None) -> None:
        kind = TOKEN_KINDS.get(self.key, "float")
        if kind == "color":
            self.editor.set_hex(value if isinstance(value, str) else None)
            return
        if kind == "colors":
            colors = value if isinstance(value, list) else []
            layout = self.editor.layout()
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            if not colors:
                empty = QLabel("无检出系列色")
                empty.setObjectName("DimLabel")
                layout.addWidget(empty)
            for idx, color in enumerate(colors):
                btn = ColorButton()
                btn.set_hex(str(color) if color else None)
                btn.colorPicked.connect(lambda hex_value, i=idx, current=list(colors): self._color_at(current, i, hex_value))
                layout.addWidget(btn)
            add = QPushButton("添加")
            add.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            add.clicked.connect(lambda: self._add_color(list(colors)))
            layout.addWidget(add)
            if colors:
                remove = QPushButton("删除末色")
                remove.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                remove.clicked.connect(lambda: self._emit_value(list(colors[:-1])))
                layout.addWidget(remove)
            return
        if kind == "font":
            self._populate_font_combo(str(value or ""), font_candidates(token or {}))
            return
        if kind in {"shape", "weight"}:
            text = str(value or "")
            idx = self.editor.findData(text)
            self.editor.setCurrentIndex(idx if idx >= 0 else 0)
            return
        if kind == "bool":
            if value is None:
                self.editor.setCheckState(Qt.CheckState.PartiallyChecked)
            elif value:
                self.editor.setCheckState(Qt.CheckState.Checked)
            else:
                self.editor.setCheckState(Qt.CheckState.Unchecked)
            return
        if value is None:
            self.editor.setValue(self.editor.minimum())
        else:
            try:
                self.editor.setValue(float(value))
            except (TypeError, ValueError):
                self.editor.setValue(UNKNOWN_SENTINEL)

    def _emit_value(self, value: Any) -> None:
        if self._updating:
            return
        self.valueEdited.emit(self.key, value)

    def _font_changed(self) -> None:
        if self._updating:
            return
        data = self.editor.currentData()
        self._emit_value(data or None)

    def _shape_changed(self) -> None:
        if self._updating:
            return
        data = self.editor.currentData()
        self._emit_value(data or None)

    def _bool_changed(self, state: int) -> None:
        if self._updating:
            return
        if state == Qt.CheckState.PartiallyChecked.value:
            return
        self._emit_value(state == Qt.CheckState.Checked.value)

    def _float_changed(self, value: float) -> None:
        if self._updating:
            return
        if value <= self.editor.minimum() + 0.0001:
            return
        self._emit_value(float(value))

    def _color_at(self, colors: list[Any], index: int, hex_value: str) -> None:
        next_colors = [str(c) for c in colors]
        if 0 <= index < len(next_colors):
            next_colors[index] = hex_value
        self._emit_value(next_colors)

    def _add_color(self, colors: list[Any]) -> None:
        initial = QColor(str(colors[-1]) if colors else "#4C78A8")
        color = QColorDialog.getColor(initial, self, "添加系列色")
        if not color.isValid():
            return
        self._emit_value([str(c) for c in colors] + [color.name().upper()])


class TokenEditor(QFrame):
    tokenSelected = pyqtSignal(str)
    tokenEdited = pyqtSignal(str, object)
    tokenReset = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Pane")
        self.rows: dict[str, TokenRow] = {}
        self._selected = ""
        self._font_substitutions: dict[str, str] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        title = QLabel("可编辑样式标记")
        title.setObjectName("TitleLabel")
        root.addWidget(title)
        self.summary = QLabel("尚未提取。打开来源并框选图区后点击提取。")
        self.summary.setObjectName("DimLabel")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.coverage = QLabel("")
        self.coverage.setObjectName("WarnLabel")
        self.coverage.setWordWrap(True)
        root.addWidget(self.coverage)
        self.details_btn = QToolButton()
        self.details_btn.setObjectName("LinkButton")
        self.details_btn.setCheckable(True)
        self.details_btn.setText("技术细节")
        self.details_btn.toggled.connect(self._toggle_details)
        self.details_btn.setVisible(False)
        root.addWidget(self.details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.raw_box = QLabel("")
        self.raw_box.setObjectName("DimLabel")
        self.raw_box.setWordWrap(True)
        self.raw_box.setVisible(False)
        self.raw_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.raw_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        inner = QWidget()
        inner.setObjectName("PaneInner")
        inner.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        self.inner_layout = QVBoxLayout(inner)
        self.inner_layout.setContentsMargins(0, 0, 0, 0)
        self.inner_layout.setSpacing(6)
        for key in MIN_TOKEN_KEYS:
            row = TokenRow(key)
            row.selected.connect(self._select)
            row.valueEdited.connect(self.tokenEdited.emit)
            row.resetRequested.connect(self.tokenReset.emit)
            self.rows[key] = row
            self.inner_layout.addWidget(row)
        self.inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    def _toggle_details(self, checked: bool) -> None:
        self.raw_box.setVisible(checked)
        self.details_btn.setText("收起细节" if checked else "技术细节")

    def _select(self, key: str) -> None:
        self._selected = key
        for name, row in self.rows.items():
            row.set_selected(name == key)
        self.tokenSelected.emit(key)

    def selected_key(self) -> str:
        return self._selected

    def set_draft(
        self,
        draft: dict[str, Any] | None,
        unsupported_keys: list[str] | None = None,
        font_substitutions: dict[str, str] | None = None,
    ) -> None:
        self._font_substitutions = dict(font_substitutions or {})
        if not draft:
            self.summary.setText("尚未提取。打开来源并框选图区后点击提取。")
            self.coverage.setText("")
            self.raw_box.setText("")
            self.details_btn.setVisible(False)
            self.details_btn.setChecked(False)
            self.raw_box.setVisible(False)
            for row in self.rows.values():
                row.set_token(
                    {
                        "value": None,
                        "status": "unknown",
                        "reason": "尚无稿件",
                        "method": "none",
                        "evidence": [],
                    },
                    [],
                    False,
                    {},
                )
            return
        name = str(draft.get("name") or "未命名")
        if name.strip() in {"Untitled local style", "Untitled local Style"}:
            name = "未命名本地样式"
        tokens = draft.get("tokens") or {}
        counts = {"detected": 0, "estimated": 0, "unknown": 0, "user_edited": 0}
        for key in MIN_TOKEN_KEYS:
            status = token_status(tokens, key)
            if status in counts:
                counts[status] += 1
            elif status:
                counts["unknown"] += 1
        filtered = filter_draft_warnings(draft, list(draft.get("warnings") or []))
        zh_warns = []
        seen: set[str] = set()
        for item in filtered:
            text = zh_warning(item) or "有未翻译说明"
            if text not in seen:
                seen.add(text)
                zh_warns.append(text)
        summary = f"{name} · 检出{counts['detected']} · 估计{counts['estimated']} · 未知{counts['unknown']}"
        if counts["user_edited"]:
            summary += f" · 已改{counts['user_edited']}"
        if zh_warns:
            summary += " · " + "；".join(zh_warns[:2])
        self.summary.setText(summary)
        self.raw_box.setText("\n".join(filtered))
        self.details_btn.setVisible(bool(filtered))
        if not filtered:
            self.details_btn.setChecked(False)
            self.raw_box.setVisible(False)
        coverage = draft.get("coverage") or {}
        source_class = str(coverage.get("source_class") or "unknown")
        class_map = {
            "plot": "图类：统计图",
            "mixed": "图类：混合面板",
            "photo": "图类：照片/显微（调色板受限）",
            "text": "图类：文本页（无可用图）",
            "unknown": "图类：未知",
        }
        extra = []
        if coverage.get("native_text"):
            extra.append("含原生文字")
        if coverage.get("native_vectors"):
            extra.append("含原生矢量")
        cov = class_map.get(source_class, "图类：" + source_class)
        if extra:
            cov += " · " + "、".join(extra)
        self.coverage.setText(cov)
        fallback_keys = [str(k) for k in (draft.get("fallback_keys") or [])]
        unsupported = set(unsupported_keys or [])
        for key, row in self.rows.items():
            token = tokens.get(key) or {
                "value": None,
                "status": "unknown",
                "reason": "稿中无此标记",
                "method": "none",
                "evidence": [],
            }
            row.set_token(token, fallback_keys, key in unsupported, self._font_substitutions)

    def evidence_for(self, draft: dict[str, Any] | None, key: str) -> list[Any]:
        if not draft:
            return []
        token = (draft.get("tokens") or {}).get(key) or {}
        return list(token.get("evidence") or [])

