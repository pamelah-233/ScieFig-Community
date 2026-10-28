"""Shared scale-line constraint, validation and preview for all image canvases.

Coordinates are supplied by each canvas adapter; calibration stays in
core.measurement_scale and always receives original-image coordinates.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import QGraphicsLineItem


def constrained_scale_line_end(start_x, start_y, end_x, end_y, *, free_angle):
    return float(end_x), float(end_y if free_angle else start_y)


def scale_line_is_valid(line, *, min_manhattan=1.0):
    if not line or not {'x1','y1','x2','y2'}.issubset(line):return False
    return abs(float(line['x2'])-float(line['x1']))+abs(float(line['y2'])-float(line['y1']))>min_manhattan


def scale_line_preview_pen(*, outline=False):
    pen=QPen(QColor('#000000' if outline else '#FFFFFF'),5 if outline else 3)
    pen.setCosmetic(True);pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def paint_scale_line_preview(painter,start,end):
    painter.save()
    for outline in (True,False):
        painter.setPen(scale_line_preview_pen(outline=outline))
        painter.drawLine(start,end)
    painter.restore()


class ScaleLinePreviewItem(QGraphicsLineItem):
    def paint(self,painter,option,widget=None):
        paint_scale_line_preview(painter,self.line().p1(),self.line().p2())

    def boundingRect(self):
        return super().boundingRect().adjusted(-2,-2,2,2)
