"""Shared screen-only canvas framing; exported document geometry stays separate."""
import sys
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QPainter, QPen

OUTER_MARGIN = 8
CONTROL_SPACING = 6
FIT_VIEWPORT_MARGIN_PX = 16
PAPER_INSET = 28
REFERENCE_PAPER_SIZE = (620, 420)
SURROUND_COLOR = "#171717" if sys.platform in {'darwin', 'win32'} else "#211c17"
PAPER_COLOR = "#ffffff"
PAPER_BORDER_COLOR = "#e2e4e8"
VIEWPORT_BORDER_COLOR = "#3a3734" if sys.platform in {'darwin', 'win32'} else "#3a342d"
CORNER_RADIUS = 6


def configure_canvas_layout(layout):
    layout.setContentsMargins(OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN, OUTER_MARGIN)
    layout.setSpacing(CONTROL_SPACING)


def configure_canvas_viewport(widget):
    widget.setStyleSheet(f"""
        QGraphicsView, QScrollArea#CanvasScrollArea {{
            background: {SURROUND_COLOR};
            border: 1px solid {VIEWPORT_BORDER_COLOR};
            border-radius: {CORNER_RADIUS}px;
        }}
        QWidget#CanvasScrollContent {{ background: {SURROUND_COLOR}; }}
    """)


def configure_canvas_paper(widget):
    widget.setStyleSheet(f"""
        QFrame#PaperCanvas {{
            background: {PAPER_COLOR};
            border: 1px solid {PAPER_BORDER_COLOR};
            border-radius: {CORNER_RADIUS}px;
        }}
    """)


def content_scale(width, height):
    return min((REFERENCE_PAPER_SIZE[0]-2*PAPER_INSET)/max(1,width),
               (REFERENCE_PAPER_SIZE[1]-2*PAPER_INSET)/max(1,height))


def content_frame_rect(bounds, *, margin=None):
    rect = QRectF(bounds)
    if rect.isEmpty():
        return QRectF(0,0,*REFERENCE_PAPER_SIZE)
    if margin is not None:
        return rect.adjusted(-margin, -margin, margin, margin)
    scale = content_scale(rect.width(),rect.height())
    paper = QRectF(0,0,REFERENCE_PAPER_SIZE[0]/scale,REFERENCE_PAPER_SIZE[1]/scale)
    paper.moveCenter(rect.center())
    return paper


def paint_canvas_surround(painter, rect):
    painter.fillRect(rect, QColor(SURROUND_COLOR))


def paint_canvas_paper(painter, rect, zoom=1.0, color=PAPER_COLOR):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(PAPER_BORDER_COLOR), 1)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(QColor(color))
    radius = CORNER_RADIUS/max(zoom,1e-6)
    painter.drawRoundedRect(rect,radius,radius)
    painter.restore()

