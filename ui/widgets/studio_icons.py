"""Cached, high-DPI outline icons for the macOS studio chrome."""
from functools import lru_cache

from PyQt6.QtCore import QByteArray, Qt, QSize, QRectF
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


_PATHS = {
    'new': '<path d="M14 3H5v18h14V8z M14 3v5h5 M8 14h8 M12 10v8"/>',
    'open': '<path d="M3 8V5h6l3 3h9l-3 12H3z M3 11h16"/>',
    'save': '<path d="M4 3h14l3 3v15H3V3z M7 3v6h10V3 M7 21v-8h10v8"/>',
    'save_as': '<path d="M7 3h11l3 3v12 M4 6h11l3 3v12H3V6z M7 6v6h7V6 M7 21v-5h7v5"/>',
    'export': '<path d="M12 3v12 M7 10l5 5 5-5 M4 15v6h16v-6"/>',
    'minus': '<path d="M5 12h14"/>',
    'plus': '<path d="M5 12h14 M12 5v14"/>',
    'fit': '<path d="M8 3H3v5 M16 3h5v5 M3 16v5h5 M16 21h5v-5"/>',
    'left': '<path d="m14 6-6 6 6 6"/>',
    'right': '<path d="m10 6 6 6-6 6"/>',
    'panel_left': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16 m7-12-4 4 4 4"/>',
    'panel_left_open': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16 m4-12 4 4-4 4"/>',
    'panel_right': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16 m-7-12 4 4-4 4"/>',
    'panel_right_open': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16 m-3-12-4 4 4 4"/>',
    'table': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18 M9 4v16"/>',
    'play': '<path d="m7 4 13 8-13 8z"/>',
    'crop': '<path d="M8 3H3v5 M16 3h5v5 M3 16v5h5 M16 21h5v-5 M7 7h10v10H7z"/>',
    'library': '<path d="M4 4v16 M9 4v16 M14 5l5-1 3 15-5 1z"/>',
    'check': '<path d="m4 12 5 5L20 6"/>',
    'reset': '<path d="M4 10a8 8 0 1 1 1 8 M4 4v6h6"/>',
    'expand': '<path d="M14 3h7v7 M21 3l-7 7 M10 21H3v-7 M3 21l7-7"/>',
}


@lru_cache(maxsize=40)
def studio_icon(name, color='#c4bbb0'):
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, '#766f68')):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
               f'fill="none" stroke="{ink}" stroke-width="1.65" '
               'stroke-linecap="round" stroke-linejoin="round">' + _PATHS[name] + '</svg>')
        pixmap = QPixmap(48, 48)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, 24, 24))
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def set_studio_icon(button, name, *, icon_only=False, color='#c4bbb0'):
    button.setIcon(studio_icon(name, color))
    button.setIconSize(QSize(16, 16))
    if icon_only:
        if not button.toolTip():
            button.setToolTip(button.text())
        button.setAccessibleName(button.toolTip())
        button.setText('')
