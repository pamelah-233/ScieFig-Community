"""Canvas-only navigation controls; never rendered into exported image pixels."""
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractButton


class ImageNavigationButton(QAbstractButton):
    def __init__(self,direction,parent):
        super().__init__(parent)
        self.direction=direction
        self.setFixedSize(40,64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def enterEvent(self,event):
        self.update();super().enterEvent(event)

    def leaveEvent(self,event):
        self.update();super().leaveEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active=self.isEnabled() and (self.underMouse() or self.hasFocus())
        painter.setPen(QPen(QColor(228,216,201,65),1))
        painter.setBrush(QColor(217,119,87,225) if active else QColor(52,45,38,225 if self.isEnabled() else 160))
        painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),18,18)
        painter.setPen(QPen(QColor(255,249,240,240 if self.isEnabled() else 65),2.5,
            Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        x=20;d=1 if self.direction<0 else -1
        path=QPainterPath(QPointF(x+4*d,23));path.lineTo(x-4*d,32);path.lineTo(x+4*d,41)
        painter.drawPath(path)
