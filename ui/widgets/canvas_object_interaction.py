"""Selection and gestures shared by layout canvases; document history stays with owners."""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsView, QWidget

from ui.widgets.shared_layout_text import SharedTextItem

LEFT = Qt.MouseButton.LeftButton
SHIFT = Qt.KeyboardModifier.ShiftModifier
CONTROL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier


def alive(item):
    return item is not None and not sip.isdeleted(item)


def locked(item):
    model = getattr(item, 'layout_object', None) or getattr(item, 'ref', None) or getattr(item, 'annotation', None)
    return bool(getattr(model, 'locked', False))


class _SelectionOverlay(QWidget):
    """Viewport-only affordances never become exported scene content."""
    def __init__(self, interaction):
        super().__init__(interaction.view.viewport())
        self.interaction = interaction
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.show()

    def paintEvent(self, event):
        interaction = self.interaction
        if not interaction.enabled() or interaction.editing():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor('#2F6BFF'), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        selected = interaction.selected()
        for item in selected:
            painter.drawPolygon(interaction.view.mapFromScene(item.mapToScene(item.boundingRect())))
        if len(selected) > 1:
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(interaction.view.mapFromScene(interaction.bounds(selected)).boundingRect().adjusted(-3, -3, 3, 3))
        if interaction.marquee is not None:
            painter.setBrush(QColor(47, 107, 255, 25))
            painter.drawRect(interaction.view.mapFromScene(interaction.marquee).boundingRect())
        for axis, coordinate in interaction.guides:
            point = interaction.view.mapFromScene(QPointF(coordinate, coordinate))
            if axis == 'x':
                painter.drawLine(point.x(), 0, point.x(), self.height())
            else:
                painter.drawLine(0, point.y(), self.width(), point.y())


class CanvasObjectInteraction(QObject):
    gesture_started = pyqtSignal()
    gesture_finished = pyqtSignal(bool)  # cancelled

    def __init__(self, view, enabled):
        super().__init__(view)
        self.view = view
        self._enabled = enabled
        self.command_handler = None
        self.editing_tools = None
        self.force_marquee = False
        self.contains_only = False
        self.snap_enabled = False
        self.mode = None
        self.marquee = None
        self.guides = []
        self.starts = {}
        self.selection_before = []
        self.overlay = _SelectionOverlay(self)
        self.overlay.setGeometry(view.viewport().rect())
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._edge_pan)
        self.finish_timer = QTimer(self)
        self.finish_timer.setSingleShot(True)
        self.finish_timer.timeout.connect(lambda: self.gesture_finished.emit(False))
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        view.scene().selectionChanged.connect(self.overlay.update)
        view.scene().changed.connect(self.overlay.update)
        view.horizontalScrollBar().valueChanged.connect(self.overlay.update)
        view.verticalScrollBar().valueChanged.connect(self.overlay.update)

    def enabled(self):
        return (alive(self.view) and bool(self._enabled() if callable(self._enabled) else self._enabled)
                and not self.view.property('layoutDrawingActive')
                and self.view.dragMode() != QGraphicsView.DragMode.ScrollHandDrag)

    def editing(self):
        item = self.view.scene().focusItem()
        return item if isinstance(item, SharedTextItem) and item.is_editing else None

    def items(self):
        return [item for item in self.view.scene().items() if item.isVisible()
                and item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                and not str(getattr(getattr(item, 'layout_object', None), 'object_type', '')).endswith('_layout_root')
                and 'resize' not in str(item.data(0) or '')]

    def selected(self):
        return [item for item in self.items() if item.isSelected()]

    @staticmethod
    def bounds(items):
        result = QRectF()
        for item in items:
            result = result.united(item.sceneBoundingRect())
        return result

    def _hits(self, pos):
        candidates = self.items()
        result = []
        for hit in self.view.items(QRectF(pos.x()-5, pos.y()-5, 10, 10).toRect()):
            while hit is not None and hit not in candidates:
                hit = hit.parentItem()
            if hit is not None and hit not in result and not locked(hit):
                result.append(hit)
        return result

    def _start(self, event):
        self.view.setFocus(Qt.FocusReason.MouseFocusReason)
        self.start_view = event.position().toPoint()
        self.last_view = QPoint(self.start_view)
        self.start_scene = self.view.mapToScene(self.start_view)
        self.selection_before = self.selected()
        self.dragged = False
        self.toggle_off = []
        self.gesture_started.emit()
        exact = self.view.itemAt(self.start_view)
        if exact is not None and 'resize' in str(exact.data(0) or '') and not self.force_marquee:
            self.mode = 'native'
            return False
        hits = self._hits(self.start_view)
        hit = hits[0] if hits else None
        if hit is None and exact is not None and not self.force_marquee:
            # Preserve specialized, movable-but-not-selectable image groups (WB).
            native = exact
            while native is not None:
                if str(getattr(getattr(native,'layout_object',None),'object_type','')).endswith('_layout_root'):
                    break
                if native.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
                    self.mode = 'native'
                    return False
                native = native.parentItem()
        if event.modifiers() & ALT and hits:
            index = next((n for n, item in enumerate(hits) if item.isSelected()), -1)
            hit = hits[(index+1) % len(hits)]
        additive = bool(event.modifiers() & (SHIFT | CONTROL))
        group_hit = len(self.selection_before) > 1 and self.bounds(self.selection_before).contains(self.start_scene)
        if not self.force_marquee and (hit is not None or group_hit and not additive):
            if hit is not None:
                if isinstance(hit, SharedTextItem):
                    hit.retained_text_selection = None
                members = self.editing_tools.group_members([hit]) if self.editing_tools and not event.modifiers() & ALT else [hit]
                if not members:
                    self.gesture_finished.emit(False)
                    return True
                if additive:
                    if all(item.isSelected() for item in members):
                        self.toggle_off = members
                    else:
                        for item in members:item.setSelected(True)
                elif not all(item.isSelected() for item in members) or event.modifiers() & ALT:
                    self.view.scene().clearSelection()
                    for item in members:item.setSelected(True)
            selected = self.selected()
            self.starts = {}
            for item in selected:
                if not any(parent in selected for parent in self._parents(item)) and not locked(item) and item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
                    self.starts[item] = QPointF(item.pos())
            self.start_bounds = self.bounds(self.starts)
            self.mode = 'move'
            self.view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.mode = 'marquee'
            self.base_selection = set(self.selection_before) if additive else set()
            if not additive:
                self.view.scene().clearSelection()
            self.marquee = QRectF(self.start_scene, self.start_scene)
        self.timer.start()
        return True

    def _move(self, modifiers):
        if self.mode not in {'move', 'marquee'}:
            return
        if not self.dragged and (self.last_view-self.start_view).manhattanLength() < 4:
            return
        self.dragged = True
        point = self.view.mapToScene(self.last_view)
        if self.mode == 'marquee':
            self.marquee = QRectF(self.start_scene, point).normalized()
            chosen = []
            for item in self.items():
                bounds = item.sceneBoundingRect()
                intersects = self.marquee.contains(bounds) if self.contains_only else self.marquee.intersects(bounds)
                if item in self.base_selection or intersects and not locked(item):chosen.append(item)
            if self.editing_tools:chosen=self.editing_tools.group_members(chosen)
            for item in self.items():item.setSelected(item in chosen)
        else:
            delta = point-self.start_scene
            axis = None
            if modifiers & SHIFT:
                axis = 'x' if abs(delta.x()) >= abs(delta.y()) else 'y'
                delta = QPointF(delta.x(), 0) if axis == 'x' else QPointF(0, delta.y())
            self.guides = []
            if self.snap_enabled and not modifiers & ALT:
                delta = self._snap(delta, axis)
            self._position(delta)
        self.overlay.update()

    def _position(self, delta):
        for item, start in list(self.starts.items()):
            if not alive(item):
                continue
            parent = item.parentItem()
            translated = delta if parent is None else parent.mapFromScene(delta)-parent.mapFromScene(QPointF())
            item.setPos(start+translated)

    def _snap(self, delta, axis):
        moving = self.start_bounds.translated(delta)
        targets = [i.sceneBoundingRect() for i in self.items() if i not in self.starts and not i.isSelected()]
        if not targets:
            return delta
        tolerance = 5/max(.01, self.view.transform().m11())
        for dimension in ('x', 'y'):
            if axis and axis != dimension:
                continue
            coords = lambda r: (r.left(), r.center().x(), r.right()) if dimension == 'x' else (r.top(), r.center().y(), r.bottom())
            offset, guide = min(((b-a, b) for r in targets for b in coords(r) for a in coords(moving)), key=lambda pair: abs(pair[0]))
            if abs(offset) <= tolerance:
                delta += QPointF(offset, 0) if dimension == 'x' else QPointF(0, offset)
                self.guides.append((dimension, guide))
        return delta

    def _finish(self, cancelled=False):
        if self.mode is None:
            return
        native = self.mode == 'native'
        if cancelled:
            self._position(QPointF())
            for item in self.items():
                item.setSelected(item in self.selection_before)
        elif self.mode == 'move' and not self.dragged:
            for item in self.toggle_off:
                if alive(item):item.setSelected(False)
        self.mode = None
        self.starts = {}
        self.selection_before = []
        self.marquee = None
        self.guides = []
        self.timer.stop()
        self.view.viewport().unsetCursor()
        self.overlay.update()
        if native and not cancelled:
            self.finish_timer.start(0)
        else:
            self.gesture_finished.emit(cancelled)

    def cancel(self):
        self._finish(True)

    def _edge_pan(self):
        if not self.enabled() or not self.mode:
            self.cancel()
            return
        if not self.dragged or self.mode == 'native':
            return
        rect = self.view.viewport().rect()
        pos = self.last_view
        dx = min(16, max(0, 28-pos.x()))-min(16, max(0, pos.x()-(rect.width()-28)))
        dy = min(16, max(0, 28-pos.y()))-min(16, max(0, pos.y()-(rect.height()-28)))
        if dx or dy:
            from ui.widgets.canvas_graphics_adapter import view_controller_of
            controller = view_controller_of(self.view)
            if controller is not None:
                controller.begin_pan()
                controller.pan_by(dx, dy)
                self._move(self.modifiers)

    @staticmethod
    def key_command(event):
        if event.modifiers() & CONTROL:
            if event.key() == Qt.Key.Key_G:
                return 'ungroup' if event.modifiers() & SHIFT else 'group'
            if event.modifiers() & ALT and event.key() in {Qt.Key.Key_C, Qt.Key.Key_V}:
                return 'copy_style' if event.key() == Qt.Key.Key_C else 'paste_style'
        for name, standard in [('copy', 'Copy'), ('cut', 'Cut'), ('paste', 'Paste'),
                               ('undo', 'Undo'), ('redo', 'Redo'), ('all', 'SelectAll'), ('delete', 'Delete')]:
            if event.matches(getattr(QKeySequence.StandardKey, standard)):
                return name
        if event.key() == Qt.Key.Key_Z and event.modifiers() & CONTROL and event.modifiers() & SHIFT:
            return 'redo'
        return None

    def _key(self, event):
        editing = self.editing()
        if editing:
            if event.key() == Qt.Key.Key_Escape:
                editing.finish_edit()
                return True
            return False
        if event.key() == Qt.Key.Key_Space and self.mode is not None:
            self.cancel()
            return False
        if event.key() == Qt.Key.Key_Escape:
            if self.mode:
                self.cancel()
            else:
                self.view.scene().clearSelection()
            return True
        command = self.key_command(event)
        if self.editing_tools and command in {'group','ungroup','copy_style','paste_style'}:
            self.cancel()
            callback={'group':'group_selection','ungroup':'ungroup_selection'}.get(command,command)
            getattr(self.editing_tools,callback)()
            return True
        if command == 'all':
            for item in self.items():
                item.setSelected(not locked(item))
            return True
        if command and self.command_handler is not None:
            self.cancel()
            return bool(self.command_handler(command))
        deltas = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if event.key() in deltas:
            selected = self.selected()
            self.starts = {i: QPointF(i.pos()) for i in selected if not locked(i) and i.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                           and not any(p in selected for p in self._parents(i))}
            if self.starts:
                self.gesture_started.emit()
                x, y = deltas[event.key()]
                step = 10 if event.modifiers() & SHIFT else 1
                self._position(QPointF(x*step, y*step))
                self.starts = {}
                self.gesture_finished.emit(False)
            return True
        if event.key() == Qt.Key.Key_F2:
            selected = self.selected()
            if len(selected) == 1 and isinstance(selected[0], SharedTextItem):
                selected[0].begin_edit()
                return True
        return False

    @staticmethod
    def _parents(item):
        parent = item.parentItem() or getattr(item,'image_attachment_parent',None)
        while parent is not None:
            yield parent
            parent = parent.parentItem() or getattr(parent,'image_attachment_parent',None)

    def eventFilter(self, watched, event):
        if not alive(self.view):
            return False
        kind = event.type()
        if kind == QEvent.Type.Resize and watched is self.view.viewport():
            self.overlay.setGeometry(self.view.viewport().rect())
        if kind in {QEvent.Type.Hide, QEvent.Type.FocusOut} and watched is self.view:
            self.cancel()
        if not self.enabled():
            return False
        if kind == QEvent.Type.ShortcutOverride:
            command = self.key_command(event)
            if command and (self.editing() or command == 'all' or self.command_handler is not None):
                event.accept()
                return True
        if kind == QEvent.Type.KeyPress:
            return self._key(event)
        if watched is not self.view.viewport() or getattr(self.view, '_space_pressed', False):
            return False
        if kind == QEvent.Type.ContextMenu and self.editing_tools and not self.editing():
            hits = self._hits(event.pos())
            if hits and not hits[0].isSelected():
                self.view.scene().clearSelection()
                for item in self.editing_tools.group_members([hits[0]]):item.setSelected(True)
            menu = self.editing_tools.context_menu()
            action = menu.exec(event.globalPos())
            if action is not None and action.data() == 'module_advanced':
                self.view.contextMenuEvent(event)
            menu.deleteLater()
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == LEFT:
            editing = self.editing()
            if editing:
                point = self.view.mapToScene(event.position().toPoint())
                if editing.contains(editing.mapFromScene(point)) and not self.force_marquee:
                    return False
                editing.finish_edit()
            return self._start(event)
        if kind == QEvent.Type.MouseMove and self.mode:
            self.last_view = event.position().toPoint()
            self.modifiers = event.modifiers()
            self._move(event.modifiers())
            return self.mode != 'native'
        if kind == QEvent.Type.MouseButtonRelease and event.button() == LEFT and self.mode:
            native = self.mode == 'native'
            self._finish()
            return not native
        return False
