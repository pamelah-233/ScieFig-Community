"""Keep an editable scale line and its text attached to their image geometry."""
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsLineItem


class AttachedScaleBarLine(QGraphicsLineItem):
    def __init__(self, *args):
        super().__init__(*args)
        self.position_changed = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.position_changed:
            self.position_changed()
        return result


class ScaleBarAttachment:
    def __init__(self, line, text, *, text_position, gap, changed):
        self.line, self.text = line, text
        self.text_position, self.gap = text_position, float(gap)
        self.changed = changed
        self._busy = False
        self._remember_positions()
        line.position_changed = lambda: self._moved(line)
        if text is not None:
            text._geometry_changed_callback = lambda *_: self._moved(text)
            text._style_changed_callback = self._text_style_changed

    def _remember_positions(self):
        self._line_pos = QPointF(self.line.pos())
        self._text_pos = QPointF(self.text.pos()) if self.text is not None else QPointF()

    def _moved(self, item):
        if self._busy:
            return
        self._busy = True
        try:
            if item is self.line:
                delta = self.line.pos()-self._line_pos
                if self.text is not None:self.text.setPos(self.text.pos()+delta)
            else:
                self.line.setPos(self.line.pos()+self.text.pos()-self._text_pos)
            self._remember_positions()
        finally:
            self._busy = False
        self.changed(self)

    def _place_text(self):
        if self.text is None:
            return
        line = self.line.line()
        origin = self.line.pos()+line.p1()
        self.text._fit_text_width()
        rect = self.text.boundingRect()
        y = origin.y()+self.gap if self.text_position == 'below' else origin.y()-rect.height()-self.gap
        self.text.setPos(origin.x()+(line.length()-rect.width())/2, y)

    def _text_style_changed(self, *_):
        if self._busy:
            return
        self._busy = True
        try:
            self._place_text()
            self._remember_positions()
        finally:
            self._busy = False
        self.changed(self)

    def resize_with_image(self, sx, sy):
        self._busy = True
        try:
            p = self.line.pos()
            line = self.line.line()
            self.line.setPos(p.x()*sx,p.y()*sy)
            self.line.setLine(line.x1()*sx,line.y1()*sy,line.x2()*sx,line.y2()*sy)
            pen = self.line.pen();pen.setWidthF(pen.widthF()*sy);self.line.setPen(pen)
            self.gap *= sy
            if self.text is not None:
                font = self.text.font();font.setPointSizeF(font.pointSizeF()*sy);self.text.setFont(font)
                self._place_text()
            self._remember_positions()
        finally:
            self._busy = False
        self.changed(self)
