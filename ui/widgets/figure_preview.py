"""View-only, vector Matplotlib preview. Navigation never resizes the Figure."""
from io import BytesIO

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget


class FigurePreview(QWidget):
    def __init__(self, figure, parent=None):
        super().__init__(parent)
        self.figure = figure
        self.renderer = QSvgRenderer(self)
        self.setMinimumSize(1, 1)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def draw(self):
        from matplotlib import rc_context
        data = BytesIO()
        with rc_context({"svg.fonttype": "path"}):
            self.figure.savefig(data, format="svg", bbox_inches=None, pad_inches=0)
        self.renderer.load(QByteArray(data.getvalue()))
        self.update()

    def draw_idle(self):
        self.draw()

    def content_rect(self):
        size = self.renderer.viewBoxF().size()
        if size.isEmpty():
            return QRectF(self.rect())
        factor = min(self.width() / size.width(), self.height() / size.height())
        w, h = size.width() * factor, size.height() * factor
        return QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.renderer.render(painter, self.content_rect())
