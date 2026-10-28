from __future__ import annotations

from PyQt6.QtCore import QBuffer, QIODevice, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter


MM_PER_INCH = 25.4


def target_pixel_size(target_width_mm: float, target_height_mm: float, dpi: int) -> QSize:
    width = max(1, int(round(float(target_width_mm) / MM_PER_INCH * int(dpi))))
    height = max(1, int(round(float(target_height_mm) / MM_PER_INCH * int(dpi))))
    return QSize(width, height)


def fitted_size(source_width: int, source_height: int, target_size: QSize) -> QSize:
    if source_width <= 0 or source_height <= 0:
        return QSize(max(1, target_size.width()), max(1, target_size.height()))
    scale = min(target_size.width() / source_width, target_size.height() / source_height)
    return QSize(max(1, int(round(source_width * scale))), max(1, int(round(source_height * scale))))


def fit_qimage_to_size(image: QImage, target_size: QSize, background: QColor | Qt.GlobalColor = Qt.GlobalColor.transparent) -> QImage:
    if image.isNull():
        raise ValueError("Cannot fit a null QImage.")
    canvas = QImage(target_size, QImage.Format.Format_ARGB32)
    canvas.fill(background)
    scaled = image.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    x = (target_size.width() - scaled.width()) // 2
    y = (target_size.height() - scaled.height()) // 2
    painter = QPainter(canvas)
    painter.drawImage(QPoint(x, y), scaled)
    painter.end()
    return canvas


def qimage_to_bytes(image: QImage, fmt: str = "png") -> bytes:
    if image.isNull():
        raise ValueError("Cannot encode a null QImage.")
    normalized = fmt.lower().lstrip(".")
    qt_format = {
        "jpg": "JPEG",
        "jpeg": "JPEG",
        "png": "PNG",
        "tif": "TIFF",
        "tiff": "TIFF",
        "bmp": "BMP",
    }.get(normalized)
    if qt_format is None:
        raise ValueError(f"Unsupported QImage export format: {fmt}")

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = image.save(buffer, qt_format)
    if not ok:
        raise ValueError(f"Could not encode QImage as {qt_format}.")
    return bytes(buffer.data())


def draw_centered_image(painter: QPainter, image: QImage, target: QRect) -> QRect:
    draw_size = fitted_size(image.width(), image.height(), target.size())
    x = target.x() + (target.width() - draw_size.width()) // 2
    y = target.y() + (target.height() - draw_size.height()) // 2
    draw_rect = QRect(x, y, draw_size.width(), draw_size.height())
    painter.drawImage(draw_rect, image)
    return draw_rect
