"""Small vector-drawn tools; no font-glyph or external asset dependency."""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap


def composer_tool_icon(kind):
    pixmap=QPixmap(48,48);pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#e4d8c9'),1.6,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind=='select':
        path=QPainterPath(QPointF(5,3))
        for x,y in [(5,19),(9,15),(12,21),(15,19),(12,13),(19,13),(5,3)]:path.lineTo(x,y)
        painter.drawPath(path)
    elif kind=='pan':
        path=QPainterPath(QPointF(7,12))
        path.lineTo(7,5);path.quadTo(9,2,11,5);path.lineTo(11,10)
        path.lineTo(11,4);path.quadTo(13,1,15,4);path.lineTo(15,11)
        path.quadTo(18,7,19,11);path.lineTo(20,15);path.quadTo(20,21,14,21)
        path.lineTo(11,21);path.quadTo(8,20,5,15);path.quadTo(2,11,5,11);path.lineTo(7,13)
        painter.drawPath(path)
    elif kind=='text':
        font=QFont('Arial');font.setPixelSize(21);font.setBold(True);painter.setFont(font)
        painter.drawText(QRectF(0,0,24,24),Qt.AlignmentFlag.AlignCenter,'T')
    elif kind=='rectangle':painter.drawRect(QRectF(4,5,16,14))
    elif kind=='ellipse':painter.drawEllipse(QRectF(3,5,18,14))
    elif kind in {'line','arrow'}:
        painter.drawLine(QPointF(4,19),QPointF(20,5))
        if kind=='arrow':
            painter.drawLine(QPointF(12,5),QPointF(20,5));painter.drawLine(QPointF(20,5),QPointF(20,13))
    elif kind=='delete':
        painter.drawLine(4,6,20,6);painter.drawLine(9,3,15,3)
        painter.drawRect(QRectF(6,7,12,14));painter.drawLine(10,10,10,17);painter.drawLine(14,10,14,17)
    painter.end()
    return QIcon(pixmap)
