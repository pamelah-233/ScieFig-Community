"""Drag-to-create annotation gestures, isolated from measurement ROI modes."""
import math
from PyQt6.QtCore import QObject,QEvent,Qt,QPointF,QRectF,pyqtSignal
from PyQt6.QtGui import QPainterPath,QPen,QColor
from PyQt6.QtWidgets import QGraphicsPathItem,QGraphicsView


class LayoutDrawTool(QObject):
    created=pyqtSignal(str,QRectF,dict)
    mode_changed=pyqtSignal(object)

    def __init__(self,view):
        super().__init__(view)
        self.view=view
        self.kind=None
        self.start=None
        self.preview=None
        self.previous_drag_mode=None
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)

    def arm(self,kind):
        self.cancel()
        self.kind=kind
        self.view.setProperty('layoutDrawingActive',True)
        self.previous_drag_mode=self.view.dragMode()
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.mode_changed.emit(kind)

    def cancel(self):
        was_active=self.kind is not None
        if self.preview is not None:
            if self.preview.scene() is not None:self.preview.scene().removeItem(self.preview)
            self.preview=None
        self.kind=None;self.start=None
        self.view.setProperty('layoutDrawingActive',False)
        if self.previous_drag_mode is not None:
            self.view.setDragMode(self.previous_drag_mode)
            self.previous_drag_mode=None
        self.view.viewport().unsetCursor()
        if was_active:self.mode_changed.emit(None)

    def _point(self,event):
        point=self.view.mapToScene(event.position().toPoint())
        if self.start is not None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            delta=point-self.start
            if self.kind in {'line','arrow'}:
                angle=round(math.atan2(delta.y(),delta.x())/(math.pi/4))*math.pi/4
                length=math.hypot(delta.x(),delta.y())
                point=self.start+QPointF(math.cos(angle)*length,math.sin(angle)*length)
            elif self.kind in {'rectangle','ellipse'}:
                size=max(abs(delta.x()),abs(delta.y()))
                point=self.start+QPointF(math.copysign(size,delta.x()),math.copysign(size,delta.y()))
        return point

    def eventFilter(self,watched,event):
        if self.kind is None:return False
        if event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape:
            self.cancel();return True
        if watched is not self.view.viewport():return False
        if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            self.start=self._point(event)
            self.preview=QGraphicsPathItem()
            pen=QPen(QColor('#4F9FDF'),1,Qt.PenStyle.DashLine);pen.setCosmetic(True)
            self.preview.setPen(pen);self.preview.setZValue(100000)
            self.preview.setData(0,'layout_draw_preview')
            self.view.scene().addItem(self.preview)
            return True
        if event.type()==QEvent.Type.MouseMove and self.start is not None:
            end=self._point(event);path=QPainterPath()
            if self.kind in {'line','arrow'}:path.moveTo(self.start);path.lineTo(end)
            elif self.kind=='ellipse':path.addEllipse(QRectF(self.start,end).normalized())
            else:path.addRect(QRectF(self.start,end).normalized())
            self.preview.setPath(path)
            return True
        if event.type()==QEvent.Type.MouseButtonRelease and self.start is not None:
            kind=self.kind;start=QPointF(self.start);end=self._point(event)
            rect=QRectF(start,end).normalized()
            if rect.width()<2 and rect.height()<2:
                rect=QRectF(start.x(),start.y(),140 if kind=='text' else 120,40 if kind=='text' else 70)
                end=rect.bottomRight()
            rect.setWidth(max(1,rect.width()));rect.setHeight(max(1,rect.height()))
            endpoints={'x1':start.x()-rect.x(),'y1':start.y()-rect.y(),'x2':end.x()-rect.x(),'y2':end.y()-rect.y()} if kind in {'line','arrow'} else {}
            self.cancel()
            self.created.emit(kind,rect,endpoints)
            return True
        return False
