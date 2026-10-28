from __future__ import annotations

import weakref

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap,QFont
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QGraphicsItem, QGraphicsScene, QInputDialog, QMenu

from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from core.utils.i18n import tr
from ui.widgets.text_digit_transform import add_digit_transform_actions, apply_digit_transform_action
from ui.widgets.shared_layout_text import SharedTextItem


def _noop_style_callback(_kind: str, _index: int, _style: dict, _apply_all: bool) -> None:
    return


def graphics_item_is_alive(item: QGraphicsItem | None) -> bool:
    return item is not None and not sip.isdeleted(item)


def detach_graphics_item(item: QGraphicsItem | None) -> None:
    if not graphics_item_is_alive(item):
        return
    try:
        item.setVisible(False)
        parent = item.parentItem()
        if graphics_item_is_alive(parent):
            item.setParentItem(None)
        scene = item.scene()
        if scene is not None:
            scene.removeItem(item)
    except RuntimeError:
        return


def svg_bytes_to_pixmap(svg_bytes: bytes) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if not renderer.isValid():
        return QPixmap()
    size = renderer.defaultSize()
    width = max(1, int(size.width()))
    height = max(1, int(size.height()))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QPixmap.fromImage(image)


_REGISTERED_GRAPHICS_SCENES: weakref.WeakSet[QGraphicsScene] = weakref.WeakSet()


def register_graphics_scene_for_cleanup(scene: QGraphicsScene) -> None:
    _REGISTERED_GRAPHICS_SCENES.add(scene)


def clear_graphics_scene_for_cleanup(scene: QGraphicsScene | None) -> None:
    if scene is None or sip.isdeleted(scene):
        return
    try:
        for item in list(scene.items()):
            release = getattr(item, "release_scene_resources", None)
            if callable(release):
                release()
        scene.clear()
        _REGISTERED_GRAPHICS_SCENES.discard(scene)
    except RuntimeError:
        return


def cleanup_registered_graphics_scenes() -> None:
    for scene in list(_REGISTERED_GRAPHICS_SCENES):
        clear_graphics_scene_for_cleanup(scene)


class EditableMovableTextItem(SharedTextItem):
    def __init__(self, text: str, text_callback, moved_callback, style_callback=None, payload: tuple[str, int] = ("text", 0), parent=None) -> None:
        self._text_callback = text_callback
        self._moved_callback = moved_callback
        self._style_callback = style_callback or _noop_style_callback
        self._payload = payload
        self._track_position = False
        self._suspend_callback = False
        layout_object = LayoutObject(
            object_id=f"legacy-text-{id(self)}",
            object_type="text",
            x=0.0,
            y=0.0,
            width=320.0,
            height=80.0,
            text=str(text),
            style=LayoutObjectStyle(font_family="Arial", font_size_pt=10.0, text_color="#000000"),
        )
        super().__init__(layout_object, parent=parent)
        self._text_changed_callback = self._shared_text_changed
        self._geometry_changed_callback = self._shared_geometry_changed
        self._style_changed_callback = self._shared_style_changed
        self.document().setDocumentMargin(0)
        self.document().setDefaultStyleSheet("* { background: transparent; }")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(100)
        kind, _index = payload
        self.label_type = {"lane": "lane_label", "marker": "mw_marker", "protein": "protein_name"}.get(str(kind), str(kind))
        self.setDefaultTextColor(QColor(0, 0, 0))
        self._auto_width=True
        self._fit_text_width()

    def _fit_text_width(self):
        if not getattr(self,'_auto_width',False):return
        self.document().setTextWidth(-1)
        width=max(1.0,self.document().idealWidth())
        self.setTextWidth(width)
        payload=self.layout_object.to_dict()
        payload['width']=width
        payload['height']=self.document().size().height()
        self.layout_object=LayoutObject.from_dict(payload)

    def setFont(self,font: QFont):
        super().setFont(font)
        self._fit_text_width()

    def _shared_style_changed(self, _item: SharedTextItem, delta: dict[str, object]) -> None:
        self._fit_text_width()
        kind, index = self._payload
        style=dict(delta)
        if 'text_color' in style:style['color']=style.pop('text_color')
        self._style_callback(kind, index, style, False)

    def _shared_text_changed(self, _item: SharedTextItem, text: str, _payload: dict) -> None:
        self._fit_text_width()
        self._text_callback(*self._payload, text)

    def _shared_geometry_changed(self, _item: SharedTextItem, _rect) -> None:  # noqa: ANN001
        if self._track_position and not self._suspend_callback:
            self._moved_callback(self, *self._payload, float(self.pos().x()), float(self.pos().y()))

    def enable_position_tracking(self) -> None:
        self._track_position = True

    def hoverEnterEvent(self, event) -> None:
        if self.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverEnterEvent(event)

    def style_payload(self) -> tuple[str, int]:
        return self._payload

    def set_position_without_callback(self, x: float, y: float) -> None:
        self._suspend_callback = True
        self.setPos(x, y)
        self._suspend_callback = False

    def contextMenuEvent(self, event) -> None:
        kind, index = self._payload
        menu = QMenu()
        font_menu = menu.addMenu("Font")
        font_actions = {}
        for family in ["Arial", "Helvetica", "Times New Roman", "Calibri"]:
            font_actions[font_menu.addAction(family)] = family
        size_menu = menu.addMenu("Size")
        size_actions = {}
        for size in [4, 5, 6, 7, 8, 9, 10, 12, 14, 18, 24]:
            size_actions[size_menu.addAction(f"{size} pt")] = size
        custom_size_action = size_menu.addAction("Custom...")
        bold_action = menu.addAction("Toggle Bold")
        italic_action = menu.addAction("Toggle Italic")
        color_menu = menu.addMenu("Color")
        color_actions = {
            color_menu.addAction("黑色 (#111111)"): "#111111",
            color_menu.addAction("灰色 (#555555)"): "#555555",
            color_menu.addAction("暗红 (#C0392B)"): "#C0392B",
            color_menu.addAction("深蓝 (#1F4E79)"): "#1F4E79",
        }
        rotate_action = menu.addAction("Set Rotation...")
        apply_all_action = menu.addAction(f"Apply current style to all {kind} labels")
        digit_actions = add_digit_transform_actions(
            menu,
            self,
            lambda: self._text_callback(*self._payload, self.toPlainText()),
        )
        action = menu.exec(event.screenPos())
        if apply_digit_transform_action(action, digit_actions):
            return
        if action in font_actions:
            self._style_callback(kind, index, {"font_family": font_actions[action]}, False)
            return
        if action in size_actions:
            self._style_callback(kind, index, {"font_size_pt": size_actions[action]}, False)
            return
        if action == custom_size_action:
            value, ok = QInputDialog.getDouble(
                None,
                tr("text_size_title"),
                tr("font_size_pt_prompt"),
                self.font().pointSizeF() or 6.0,
                4.0,
                24.0,
                1,
            )
            if ok:
                self._style_callback(kind, index, {"font_size_pt": float(value)}, False)
            return
        if action == bold_action:
            self._style_callback(kind, index, {"bold": not self.font().bold()}, False)
            return
        if action == italic_action:
            self._style_callback(kind, index, {"italic": not self.font().italic()}, False)
            return
        if action in color_actions:
            self._style_callback(kind, index, {"color": color_actions[action]}, False)
            return
        if action == rotate_action:
            value, ok = QInputDialog.getDouble(
                None,
                tr("text_rotation_title"),
                tr("rotation_angle_prompt"),
                self.rotation(),
                -180.0,
                180.0,
                1,
            )
            if ok:
                self._style_callback(kind, index, {"rotation": float(value)}, False)
            return
        if action == apply_all_action:
            self._style_callback(
                kind,
                index,
                {
                    "font_family": self.font().family(),
                    "font_size_pt": self.font().pointSizeF() or 6.0,
                    "bold": self.font().bold(),
                    "italic": self.font().italic(),
                    "color": self.defaultTextColor().name(),
                    "rotation": float(self.rotation()),
                },
                True,
            )
