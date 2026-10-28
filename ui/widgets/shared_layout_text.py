"""Module-neutral text editing and rendering for layout objects.

The shared text item deliberately keeps scene coordinates opaque.  Layout
adapters decide whether a scene is measured in pixels, points, or millimetres;
the only unit owned here is the persisted font size, which is always points.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsTextItem, QStyleOptionGraphicsItem

from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from ui.widgets.shared_edit_notifications import EditNotifications


TEXT_METADATA_KEY = "text"
TEXT_HTML_KEY = "html"
TEXT_RUNS_KEY = "runs"
TEXT_ALIGNMENT_KEY = "alignment"
TEXT_LINE_SPACING_KEY = "line_spacing"

_TEXT_MARKER = "layout_editor_item"
_ALIGNMENTS = {"left", "center", "right", "justify"}
_VERTICAL_ALIGNMENTS = {"normal", "superscript", "subscript"}


@dataclass(frozen=True)
class SharedTextDocument:
    """JSON-facing rich-text value object used by adapters and controls."""

    plain_text: str
    html: str = ""
    runs: tuple[Mapping[str, Any], ...] = ()
    alignment: str = "left"
    line_spacing: float = 1.0

    @classmethod
    def from_layout_object(cls, layout_object: LayoutObject) -> "SharedTextDocument":
        payload = normalize_text_payload(layout_object)
        return cls(
            plain_text=str(payload["plain_text"]),
            html=str(payload.get(TEXT_HTML_KEY, "")),
            runs=tuple(dict(run) for run in payload.get(TEXT_RUNS_KEY, [])),
            alignment=str(payload.get(TEXT_ALIGNMENT_KEY, "left")),
            line_spacing=float(payload.get(TEXT_LINE_SPACING_KEY, 1.0)),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "plain_text": self.plain_text,
            TEXT_HTML_KEY: self.html,
            TEXT_RUNS_KEY: [dict(run) for run in self.runs],
            TEXT_ALIGNMENT_KEY: self.alignment,
            TEXT_LINE_SPACING_KEY: float(self.line_spacing),
        }


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clamp_range(start: Any, length: Any, text_length: int) -> tuple[int, int]:
    start_value = max(0, min(text_length, int(start or 0)))
    length_value = max(0, int(length if length is not None else 0))
    return start_value, min(length_value, text_length - start_value)


def _plain_text_from_html(value: str) -> str:
    if not value:
        return ""
    document = QTextDocument()
    document.setHtml(value)
    return document.toPlainText()


def _legacy_text_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Read both the nested shared payload and early adapter spellings."""

    nested = _as_mapping(metadata.get(TEXT_METADATA_KEY))
    legacy: dict[str, Any] = nested if nested else {}
    for key in (
        TEXT_HTML_KEY,
        TEXT_RUNS_KEY,
        TEXT_ALIGNMENT_KEY,
        TEXT_LINE_SPACING_KEY,
        "underline",
    ):
        if key in metadata and key not in legacy:
            legacy[key] = metadata[key]
    if "text_html" in metadata and TEXT_HTML_KEY not in legacy:
        legacy[TEXT_HTML_KEY] = metadata["text_html"]
    if "text_runs" in metadata and TEXT_RUNS_KEY not in legacy:
        legacy[TEXT_RUNS_KEY] = metadata["text_runs"]
    if "text_html" in nested and TEXT_HTML_KEY not in legacy:
        legacy[TEXT_HTML_KEY] = nested["text_html"]
    if "text_runs" in nested and TEXT_RUNS_KEY not in legacy:
        legacy[TEXT_RUNS_KEY] = nested["text_runs"]
    return legacy


def _normalise_run(run: Mapping[str, Any], plain_text: str) -> dict[str, Any] | None:
    start, length = _clamp_range(run.get("start", 0), run.get("length"), len(plain_text))
    if length <= 0:
        return None
    segment = str(run.get("text", plain_text[start : start + length]))
    # Persisting the source substring avoids a stale rich-text payload after a
    # plain-text edit while keeping old payloads readable.
    if len(segment) != length:
        segment = plain_text[start : start + length]
    result: dict[str, Any] = {"start": start, "length": length, "text": segment}
    if "font_family" in run:
        result["font_family"] = str(run["font_family"])
    if "font_size_pt" in run:
        result["font_size_pt"] = float(run["font_size_pt"])
    for key in ("bold", "italic", "underline"):
        if key in run:
            result[key] = bool(run[key])
    if "color" in run:
        result["color"] = str(run["color"])
    vertical_align = str(run.get("vertical_align", "normal"))
    if vertical_align not in _VERTICAL_ALIGNMENTS:
        vertical_align = "normal"
    if vertical_align != "normal":
        result["vertical_align"] = vertical_align
    return result


def normalize_text_payload(payload: LayoutObject | Mapping[str, Any] | str) -> dict[str, Any]:
    """Return the canonical, JSON-compatible shared text payload.

    ``LayoutObject.text`` remains the compatibility plain-text field.  Rich
    state is kept under ``metadata["text"]`` and is intentionally optional.
    ``text_html``/``text_runs`` at metadata level are accepted for migration
    from older adapters.
    """

    layout_object: LayoutObject | None = payload if isinstance(payload, LayoutObject) else None
    if layout_object is not None:
        plain_text = str(layout_object.text)
        metadata = _as_mapping(layout_object.metadata)
        source = _legacy_text_metadata(metadata)
    elif isinstance(payload, str):
        plain_text = payload
        source = {}
    else:
        payload_mapping = _as_mapping(payload)
        plain_text = str(payload_mapping.get("plain_text", payload_mapping.get("text", "")))
        metadata = _as_mapping(payload_mapping.get("metadata"))
        source = (
            payload_mapping
            if "plain_text" in payload_mapping
            else _as_mapping(payload_mapping.get(TEXT_METADATA_KEY)) or _legacy_text_metadata(metadata)
        )
    source = dict(source)
    if "text_html" in source and TEXT_HTML_KEY not in source:
        source[TEXT_HTML_KEY] = source["text_html"]
    if "text_runs" in source and TEXT_RUNS_KEY not in source:
        source[TEXT_RUNS_KEY] = source["text_runs"]

    if not plain_text and source.get(TEXT_HTML_KEY):
        plain_text = _plain_text_from_html(str(source[TEXT_HTML_KEY]))
    alignment = str(source.get(TEXT_ALIGNMENT_KEY, "left")).lower()
    if alignment not in _ALIGNMENTS:
        alignment = "left"
    line_spacing = max(1.0, float(source.get(TEXT_LINE_SPACING_KEY, 1.0)))
    runs: list[dict[str, Any]] = []
    raw_runs = source.get(TEXT_RUNS_KEY, [])
    if isinstance(raw_runs, (list, tuple)):
        for raw_run in raw_runs:
            if isinstance(raw_run, Mapping):
                run = _normalise_run(raw_run, plain_text)
                if run is not None:
                    runs.append(run)
    runs.sort(key=lambda run: (int(run["start"]), int(run["length"])))
    result: dict[str, Any] = {
        "plain_text": plain_text,
        TEXT_HTML_KEY: str(source.get(TEXT_HTML_KEY, "")),
        TEXT_RUNS_KEY: runs,
        TEXT_ALIGNMENT_KEY: alignment,
        TEXT_LINE_SPACING_KEY: line_spacing,
    }
    if "underline" in source:
        result["underline"] = bool(source["underline"])
    return result


def text_payload_from_layout(layout_object: LayoutObject) -> dict[str, Any]:
    return normalize_text_payload(layout_object)


def text_payload_from_item(item: Any) -> dict[str, Any]:
    if isinstance(item, LayoutObject):
        return text_payload_from_layout(item)
    to_layout_object = getattr(item, "to_layout_object", None)
    if callable(to_layout_object):
        return text_payload_from_layout(to_layout_object())
    layout_object = getattr(item, "layout_object", None)
    if isinstance(layout_object, LayoutObject):
        return text_payload_from_layout(layout_object)
    raise TypeError("item must be a LayoutObject or shared text item")


serialize_text_payload = normalize_text_payload


def _font_for_style(style: LayoutObjectStyle) -> QFont:
    font = QFont(str(style.font_family))
    font.setPointSizeF(max(0.1, float(style.font_size_pt)))
    font.setBold(bool(style.bold))
    font.setItalic(bool(style.italic))
    return font


def _alignment_flag(alignment: str) -> Qt.AlignmentFlag:
    return {
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
        "justify": Qt.AlignmentFlag.AlignJustify,
    }.get(alignment, Qt.AlignmentFlag.AlignLeft)


def _apply_document_paragraph_format(document: QTextDocument, payload: Mapping[str, Any]) -> None:
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    block_format = QTextBlockFormat()
    block_format.setAlignment(_alignment_flag(str(payload.get(TEXT_ALIGNMENT_KEY, "left"))))
    line_spacing = max(1.0, float(payload.get(TEXT_LINE_SPACING_KEY, 1.0)))
    block_format.setLineHeight(line_spacing * 100.0, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
    cursor.mergeBlockFormat(block_format)


def _char_format_for_run(run: Mapping[str, Any], base_style: LayoutObjectStyle) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setFontFamily(str(run.get("font_family", base_style.font_family)))
    fmt.setFontPointSize(float(run.get("font_size_pt", base_style.font_size_pt)))
    fmt.setFontWeight(QFont.Weight.Bold if bool(run.get("bold", base_style.bold)) else QFont.Weight.Normal)
    fmt.setFontItalic(bool(run.get("italic", base_style.italic)))
    fmt.setFontUnderline(bool(run.get("underline", False)))
    color = QColor(str(run.get("color", base_style.text_color)))
    if color.isValid():
        fmt.setForeground(color)
    vertical_align = str(run.get("vertical_align", "normal"))
    fmt.setVerticalAlignment(
        {
            "superscript": QTextCharFormat.VerticalAlignment.AlignSuperScript,
            "subscript": QTextCharFormat.VerticalAlignment.AlignSubScript,
        }.get(vertical_align, QTextCharFormat.VerticalAlignment.AlignNormal)
    )
    return fmt


def _apply_runs(document: QTextDocument, payload: Mapping[str, Any], base_style: LayoutObjectStyle) -> None:
    plain_text = document.toPlainText()
    for raw_run in payload.get(TEXT_RUNS_KEY, []):
        if not isinstance(raw_run, Mapping):
            continue
        start, length = _clamp_range(raw_run.get("start", 0), raw_run.get("length"), len(plain_text))
        if length <= 0:
            continue
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(_char_format_for_run(raw_run, base_style))


def _apply_base_character_format(document: QTextDocument, payload: Mapping[str, Any], style: LayoutObjectStyle) -> None:
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    fmt = QTextCharFormat()
    fmt.setFontFamily(str(style.font_family))
    fmt.setFontPointSize(float(style.font_size_pt))
    fmt.setFontWeight(QFont.Weight.Bold if style.bold else QFont.Weight.Normal)
    fmt.setFontItalic(bool(style.italic))
    fmt.setFontUnderline(bool(payload.get("underline", False)))
    color = QColor(str(style.text_color))
    if color.isValid():
        fmt.setForeground(color)
    fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignNormal)
    cursor.mergeCharFormat(fmt)


class SharedTextLayout:
    """Single document construction/painting path used by preview and export."""

    @staticmethod
    def document_for(layout_object: LayoutObject) -> QTextDocument:
        payload = normalize_text_payload(layout_object)
        document = QTextDocument()
        document.setDocumentMargin(0.0)
        document.setDefaultFont(_font_for_style(layout_object.style))
        document.setTextWidth(max(1.0, float(layout_object.width)))
        html = str(payload.get(TEXT_HTML_KEY, ""))
        if html and not payload.get(TEXT_RUNS_KEY):
            document.setDefaultStyleSheet(f"body {{ color: {QColor(layout_object.style.text_color).name()}; }}")
            document.setHtml(html)
        else:
            document.setPlainText(str(payload.get("plain_text", "")))
            _apply_base_character_format(document, payload, layout_object.style)
            _apply_runs(document, payload, layout_object.style)
        _apply_document_paragraph_format(document, payload)
        return document

    @staticmethod
    def payload_for_document(
        document: QTextDocument,
        style: LayoutObjectStyle,
        previous_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = normalize_text_payload(previous_payload or document.toPlainText())
        plain_text = document.toPlainText()
        runs: list[dict[str, Any]] = []
        # QTextFragment ranges follow actual editing (including insertions).
        # Read fragments rather than repeatedly constructing per-character cursors.
        encoded=plain_text.encode('utf-16-le')
        base={'font_family':style.font_family,'font_size_pt':float(style.font_size_pt),
              'bold':style.bold,'italic':style.italic,'underline':bool(previous.get('underline',False)),
              'color':QColor(style.text_color).name(),'vertical_align':'normal'}
        block=document.begin()
        while block.isValid():
            iterator=block.begin()
            while not iterator.atEnd():
                fragment=iterator.fragment()
                if fragment.isValid():
                    fmt=fragment.charFormat()
                    families=fmt.fontFamilies()
                    prop=QTextFormat.Property
                    values={'font_family':families[0] if families else style.font_family,
                        'font_size_pt':fmt.fontPointSize() or float(style.font_size_pt),
                        'bold':fmt.fontWeight()>=QFont.Weight.Bold.value if fmt.hasProperty(prop.FontWeight) else style.bold,
                        'italic':fmt.fontItalic() if fmt.hasProperty(prop.FontItalic) else style.italic,
                        'underline':fmt.fontUnderline() if fmt.hasProperty(prop.TextUnderlineStyle) else base['underline'],
                        'color':fmt.foreground().color().name() if fmt.foreground().style()!=Qt.BrushStyle.NoBrush else base['color'],
                        'vertical_align':{QTextCharFormat.VerticalAlignment.AlignSuperScript:'superscript',
                            QTextCharFormat.VerticalAlignment.AlignSubScript:'subscript'}.get(fmt.verticalAlignment(),'normal')}
                    delta={k:v for k,v in values.items() if v!=base[k]}
                    if delta:
                        start=len(encoded[:fragment.position()*2].decode('utf-16-le'))
                        runs.append({'start':start,'length':len(fragment.text()),'text':fragment.text(),**delta})
                iterator+=1
            block=block.next()
        block_format=document.begin().blockFormat()
        alignment=block_format.alignment()
        actual_alignment=('justify' if alignment & Qt.AlignmentFlag.AlignJustify else
                          'center' if alignment & Qt.AlignmentFlag.AlignHCenter else
                          'right' if alignment & Qt.AlignmentFlag.AlignRight else 'left')
        actual_spacing=(block_format.lineHeight()/100 if block_format.lineHeightType()==QTextBlockFormat.LineHeightTypes.ProportionalHeight.value else 1.0)
        payload = {
            "plain_text": plain_text,
            TEXT_HTML_KEY: document.toHtml(),
            TEXT_RUNS_KEY: runs,
            TEXT_ALIGNMENT_KEY: actual_alignment,
            TEXT_LINE_SPACING_KEY: max(1.0,actual_spacing),
        }
        if previous.get("underline"):
            payload["underline"] = True
        return payload

    @staticmethod
    def render(
        painter: QPainter,
        layout_object: LayoutObject,
        target: QRectF | None = None,
    ) -> None:
        document = SharedTextLayout.document_for(layout_object)
        document.drawContents(painter, target or QRectF(0.0, 0.0, float(layout_object.width), float(layout_object.height)))

    @staticmethod
    def render_to_image(layout_object: LayoutObject, width: int | None = None, height: int | None = None):
        from PyQt6.QtGui import QImage

        image_width = max(1, int(round(width if width is not None else layout_object.width)))
        image_height = max(1, int(round(height if height is not None else layout_object.height)))
        image = QImage(image_width, image_height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            SharedTextLayout.render(painter, layout_object, QRectF(0.0, 0.0, image_width, image_height))
        finally:
            painter.end()
        return image


TextChangedCallback = Callable[["SharedTextItem", str, Mapping[str, Any]], None]
GeometryChangedCallback = Callable[["SharedTextItem", QRectF], None]
StyleChangedCallback = Callable[["SharedTextItem", Mapping[str, Any]], None]


class SharedTextItem(QGraphicsTextItem):
    """Editable/movable text item backed by a :class:`LayoutObject`."""

    format_selection_changed = pyqtSignal()

    def __init__(
        self,
        layout_object: LayoutObject,
        text_changed_callback: TextChangedCallback | None = None,
        geometry_changed_callback: GeometryChangedCallback | None = None,
        style_changed_callback: StyleChangedCallback | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.layout_object = layout_object
        self._text_payload = normalize_text_payload(layout_object)
        self._text_changed_callback = text_changed_callback
        self._geometry_changed_callback = geometry_changed_callback
        self._style_changed_callback = style_changed_callback
        self._editing = False
        self.retained_text_selection = None
        self._suspend_callbacks = False
        self._loading_document = False
        self._edits = EditNotifications()
        self.document().setDocumentMargin(0.0)
        self.setData(0, _TEXT_MARKER)
        self.setData(1, layout_object.object_id)
        self.setData(2, layout_object.object_type)
        self.setPos(float(layout_object.x), float(layout_object.y))
        self.setTextWidth(max(1.0, float(layout_object.width)))
        self.setRotation(float(layout_object.rotation_deg))
        self.setZValue(float(layout_object.z_order))
        self.setVisible(not layout_object.hidden)
        self.set_flags_for_locked(layout_object.locked)
        self._load_document()
        from ui.widgets.shared_layout_shapes import SharedResizeHandle
        self.handles={role:SharedResizeHandle(self,role) for role in ('nw','ne','se','sw')}
        self.resize_handles_enabled=True
        self.update_handles()
        self._edits.enabled = True

    def update_handles(self):
        if not hasattr(self,'handles'):return
        width=max(1.0,self.textWidth())
        height=max(self.boundingRect().height(),self.layout_object.height)
        for role,point in {'nw':QPointF(0,0),'ne':QPointF(width,0),'se':QPointF(width,height),'sw':QPointF(0,height)}.items():
            self.handles[role].setPos(point)
            self.handles[role].setVisible(self.resize_handles_enabled and self.isSelected() and not self.is_editing and not self.is_read_only)

    @property
    def is_editing(self) -> bool:
        return bool(self._editing)

    def boundingRect(self):
        rect=super().boundingRect()
        if hasattr(self,'layout_object'):
            rect=rect.united(QRectF(0,0,max(1.0,self.textWidth()),self.layout_object.height))
        return rect

    @property
    def is_read_only(self) -> bool:
        return bool(self.layout_object.locked)

    def _load_document(self) -> None:
        self._loading_document = True
        source_document = SharedTextLayout.document_for(self.layout_object)
        self.setDocument(source_document)
        self.setTextWidth(max(1.0, float(self.layout_object.width)))
        self.setDefaultTextColor(QColor(str(self.layout_object.style.text_color)))
        self.setOpacity(float(self.layout_object.style.opacity))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._editing = False
        self._loading_document = False

    def setFont(self, font: QFont) -> None:  # noqa: N802
        super().setFont(font)
        if not hasattr(self, "layout_object") or self._loading_document:
            return
        previous_style = self.layout_object.style
        payload = self.layout_object.to_dict()
        style = self.layout_object.style.to_dict()
        style.update(
            {
                "font_family": font.family(),
                "font_size_pt": float(font.pointSizeF() if font.pointSizeF() > 0 else font.pointSize()),
                "bold": bool(font.bold()),
                "italic": bool(font.italic()),
            }
        )
        payload["style"] = style
        self.layout_object = LayoutObject.from_dict(payload)
        self._sync_document_base_style(previous_style)

    def setDefaultTextColor(self, color: QColor) -> None:  # noqa: N802
        super().setDefaultTextColor(color)
        if not hasattr(self, "layout_object") or self._loading_document:
            return
        previous_style = self.layout_object.style
        payload = self.layout_object.to_dict()
        style = self.layout_object.style.to_dict()
        style["text_color"] = color.name() if color.isValid() else self.layout_object.style.text_color
        payload["style"] = style
        self.layout_object = LayoutObject.from_dict(payload)
        self._sync_document_base_style(previous_style)

    def _sync_document_base_style(self, previous_style: LayoutObjectStyle) -> None:
        """Keep Qt's document and the painted model consistent, preserving rich runs."""
        payload = SharedTextLayout.payload_for_document(self.document(), previous_style, self._text_payload)
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        try:
            _apply_base_character_format(self.document(), payload, self.layout_object.style)
            _apply_runs(self.document(), payload, self.layout_object.style)
        finally:
            cursor.endEditBlock()
        self._text_payload = payload
        self._sync_layout_object_from_document()

    def begin_edit_transaction(self) -> None:
        self._edits.begin()

    def end_edit_transaction(self) -> None:
        self._edits.end()

    def _notify_text_changed(self) -> None:
        self._edits.emit("text", self._text_changed_callback, self, self.toPlainText(), self._text_payload)

    def _notify_geometry_changed(self) -> None:
        self._edits.emit("geometry", self._geometry_changed_callback, self, self.boundingRect())

    def _notify_style_changed(self, delta: Mapping[str, Any]) -> None:
        self.format_selection_changed.emit()
        self._edits.emit("style", self._style_changed_callback, self, dict(delta))

    def set_flags_for_locked(self, locked: bool) -> None:
        payload = self.layout_object.to_dict()
        payload["locked"] = bool(locked)
        self.layout_object = LayoutObject.from_dict(payload)
        flags = (
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        if not locked:
            flags |= QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        self.setFlags(flags)
        if locked and self._editing:
            self.finish_edit(commit=False)

    def set_hidden(self, hidden: bool) -> None:
        payload = self.layout_object.to_dict()
        payload["hidden"] = bool(hidden)
        self.layout_object = LayoutObject.from_dict(payload)
        self.setVisible(not hidden)

    def load_text_payload(self,payload):
        data=self.layout_object.to_dict()
        rich=normalize_text_payload(payload)
        if rich['plain_text']!=self.toPlainText():
            rich.update(plain_text=self.toPlainText(),html='',runs=[])
        data['metadata']['text']=rich
        data['text']=self.toPlainText()
        self.layout_object=LayoutObject.from_dict(data)
        self._text_payload=rich
        self._load_document()

    def apply_style(self, style: LayoutObjectStyle) -> None:
        payload = self.layout_object.to_dict()
        payload["style"] = style.to_dict()
        self.layout_object = LayoutObject.from_dict(payload)
        # Keep the current position untouched: style mutation and geometry
        # mutation are separate transactions by design.
        self._text_payload = normalize_text_payload(self.layout_object)
        self._load_document()
        self._notify_style_changed(style.to_dict())

    def apply_text_style(self, style: Mapping[str, Any]) -> None:
        self._sync_layout_object_from_document()
        current = self.layout_object.style.to_dict()
        for key in ("font_family", "font_size_pt", "bold", "italic", "text_color", "opacity"):
            if key in style:
                current[key] = style[key]
        if "color" in style and "text_color" not in style:
            current["text_color"] = style["color"]
        updated = self.layout_object.to_dict()
        updated["style"] = LayoutObjectStyle.from_dict(current).to_dict()
        metadata = dict(updated.get("metadata", {}))
        text_payload = normalize_text_payload(self.layout_object)
        text_payload[TEXT_HTML_KEY]=''
        for run in text_payload.get(TEXT_RUNS_KEY,[]):
            for key in ('font_family','font_size_pt','bold','italic','underline'):
                if key in style:run[key]=style[key]
            if 'text_color' in style:run['color']=style['text_color']
            elif 'color' in style:run['color']=style['color']
        for key in ("alignment", "line_spacing"):
            if key in style:
                text_payload[key] = style[key]
        if "underline" in style:
            text_payload["underline"] = bool(style["underline"])
        text_payload["plain_text"] = self.toPlainText()
        metadata[TEXT_METADATA_KEY] = text_payload
        updated["metadata"] = metadata
        self.layout_object = LayoutObject.from_dict(updated)
        self._text_payload = normalize_text_payload(self.layout_object)
        self._load_document()
        self._notify_style_changed(style)

    update_text_style = apply_text_style

    def apply_paragraph_style(self,style):
        self._text_payload.update({key:style[key] for key in ('alignment','line_spacing') if key in style})
        _apply_document_paragraph_format(self.document(),self._text_payload)
        self._sync_layout_object_from_document()
        self._notify_style_changed(style)

    def apply_text_range_style(self, start: int, length: int, style: Mapping[str, Any]) -> None:
        """Apply rich formatting to a range and persist it as one canonical run."""

        plain_text = self.toPlainText()
        start_value, length_value = _clamp_range(start, length, len(plain_text))
        if length_value <= 0:
            return
        run: dict[str, Any] = {
            "start": start_value,
            "length": length_value,
            "text": plain_text[start_value : start_value + length_value],
        }
        for key in ("font_family", "font_size_pt", "bold", "italic", "underline", "color", "vertical_align"):
            if key in style:
                run[key] = style[key]
        if "text_color" in style and "color" not in run:
            run["color"] = style["text_color"]
        if "vertical_align" in run and str(run["vertical_align"]) not in _VERTICAL_ALIGNMENTS:
            run["vertical_align"] = "normal"
        cursor = QTextCursor(self.document())
        cursor.setPosition(len(plain_text[:start_value].encode('utf-16-le'))//2)
        cursor.setPosition(len(plain_text[:start_value+length_value].encode('utf-16-le'))//2, QTextCursor.MoveMode.KeepAnchor)
        fmt=QTextCharFormat()
        if 'font_family' in run:fmt.setFontFamilies([run['font_family']])
        if 'font_size_pt' in run:fmt.setFontPointSize(float(run['font_size_pt']))
        if 'bold' in run:fmt.setFontWeight(QFont.Weight.Bold if run['bold'] else QFont.Weight.Normal)
        if 'italic' in run:fmt.setFontItalic(bool(run['italic']))
        if 'underline' in run:fmt.setFontUnderline(bool(run['underline']))
        if 'color' in run:fmt.setForeground(QColor(run['color']))
        if 'vertical_align' in run:
            fmt.setVerticalAlignment({'superscript':QTextCharFormat.VerticalAlignment.AlignSuperScript,
                'subscript':QTextCharFormat.VerticalAlignment.AlignSubScript}.get(run['vertical_align'],QTextCharFormat.VerticalAlignment.AlignNormal))
        cursor.mergeCharFormat(fmt)
        self._text_payload=SharedTextLayout.payload_for_document(self.document(),self.layout_object.style,self._text_payload)
        payload = self.layout_object.to_dict()
        metadata = dict(payload.get("metadata", {}))
        metadata[TEXT_METADATA_KEY] = self._text_payload
        payload["metadata"] = metadata
        self.layout_object = LayoutObject.from_dict(payload)
        self._notify_style_changed({key: value for key, value in style.items() if key not in {"x", "y", "width", "height"}})
        self.update()

    def update_geometry(
        self,
        x: float,
        y: float,
        width: float,
        height: float | None = None,
        *,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> None:
        del modifiers
        with self._edits.transaction():
            self.prepareGeometryChange()
            self.setPos(float(x), float(y))
            self.setTextWidth(max(1.0, float(width)))
            payload = self.layout_object.to_dict()
            payload['width'] = max(1.0,float(width))
            if height is not None:
                payload["height"] = max(0.0, float(height))
            self.layout_object = LayoutObject.from_dict(payload)
            self._notify_geometry_changed()
            self.update_handles()

    def set_position_without_callback(self, x: float, y: float) -> None:
        self._suspend_callbacks = True
        self.setPos(float(x), float(y))
        self._suspend_callbacks = False


    def finish_edit(self, *, commit: bool = True) -> None:
        if not self._editing:
            return
        if commit:
            self._sync_layout_object_from_document()
            self._notify_text_changed()
        self._editing = False
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not self.layout_object.locked)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.update_handles()

    def begin_edit(self) -> bool:
        if self.layout_object.locked or not self.isVisible():
            return False
        self._editing = True
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update_handles()
        return True

    def _sync_layout_object_from_document(self) -> None:
        self._text_payload = SharedTextLayout.payload_for_document(
            self.document(), self.layout_object.style, previous_payload=self._text_payload
        )
        payload = self.layout_object.to_dict()
        payload["text"] = self._text_payload["plain_text"]
        metadata = dict(payload.get("metadata", {}))
        metadata[TEXT_METADATA_KEY] = self._text_payload
        payload["metadata"] = metadata
        self.layout_object = LayoutObject.from_dict(payload)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem | None, widget=None) -> None:  # noqa: ANN001, D102
        if self.is_editing and option is not None:
            # The live QTextDocument supplies cursor/selection and uncommitted text.
            super().paint(painter, option, widget)
            return
        # Preview/export and the on-canvas item intentionally share this exact
        # document construction path.  QGraphicsTextItem's default paint path
        # can apply version-dependent margins and integer font rounding.
        payload = self.layout_object.to_dict()
        payload["text"] = self.toPlainText()
        metadata = dict(payload.get("metadata", {}))
        current_text = dict(self._text_payload)
        current_text["plain_text"] = self.toPlainText()
        metadata[TEXT_METADATA_KEY] = current_text
        payload["metadata"] = metadata
        render_object = LayoutObject.from_dict(payload)
        SharedTextLayout.render(
            painter,
            render_object,
            QRectF(0.0, 0.0, max(1.0, float(self.textWidth())), max(1.0, float(self.layout_object.height))),
        )
        if self.isSelected():
            painter.save()
            painter.setPen(QPen(QColor("#2F6BFF"), 0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect())
            painter.restore()

    def to_layout_object(self) -> LayoutObject:
        self._sync_layout_object_from_document()
        payload = self.layout_object.to_dict()
        payload.update(
            {
                "x": float(self.pos().x()),
                "y": float(self.pos().y()),
                "width": float(self.textWidth()),
                "rotation_deg": float(self.rotation()),
                "z_order": int(self.zValue()),
                "hidden": not self.isVisible(),
                "locked": bool(self.layout_object.locked),
                "text": self.toPlainText(),
            }
        )
        return LayoutObject.from_dict(payload)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        cursor = self.textCursor()
        retained = (cursor.selectionStart(), cursor.selectionEnd()-cursor.selectionStart()) if self.is_editing and cursor.hasSelection() else None
        self.finish_edit(commit=True)
        self.retained_text_selection = retained
        super().focusOutEvent(event)
        self.format_selection_changed.emit()

    def setTextCursor(self, cursor) -> None:  # noqa: N802
        super().setTextCursor(cursor)
        self.format_selection_changed.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.format_selection_changed.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.retained_text_selection = None
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.begin_edit():
            super().mouseDoubleClickEvent(event)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.boundingRect().adjusted(-6.0, -4.0, 6.0, 4.0))
        return path

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self._suspend_callbacks:
            self._notify_geometry_changed()
        if change==QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update_handles()
        return super().itemChange(change, value)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.is_editing:
            modifiers = event.modifiers()
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and modifiers & Qt.KeyboardModifier.ControlModifier:
                self.finish_edit(commit=True)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                self.finish_edit(commit=False)
                event.accept()
                return
            # QGraphicsTextItem handles newline, delete/backspace, select all,
            # copy, and paste only while text editor interaction is active.
            super().keyPressEvent(event)
            self.format_selection_changed.emit()
            return
        if event.key() == Qt.Key.Key_F2 and not self.layout_object.locked:
            self.begin_edit()
            event.accept()
            return
        step = 5.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        delta = {
            Qt.Key.Key_Left: QPointF(-step, 0.0),
            Qt.Key.Key_Right: QPointF(step, 0.0),
            Qt.Key.Key_Up: QPointF(0.0, -step),
            Qt.Key.Key_Down: QPointF(0.0, step),
        }.get(event.key())
        if delta is not None and not self.layout_object.locked:
            self.setPos(self.pos() + delta)
            event.accept()
            return
        event.ignore()


def apply_text_style(layout_object: LayoutObject, style: Mapping[str, Any]) -> LayoutObject:
    """Pure style mutation helper; x/y/width/height are never changed."""

    item = SharedTextItem(layout_object)
    item.apply_text_style(style)
    payload = item.to_layout_object().to_dict()
    payload.update(
        {
            "x": float(layout_object.x),
            "y": float(layout_object.y),
            "width": float(layout_object.width),
            "height": float(layout_object.height),
            "rotation_deg": float(layout_object.rotation_deg),
        }
    )
    return LayoutObject.from_dict(payload)


update_text_style = apply_text_style
