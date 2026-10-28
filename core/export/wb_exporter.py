from __future__ import annotations

import json
from pathlib import Path
from shutil import copy2
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
from PyQt6.QtCore import QMarginsF, QRectF, QSize, QSizeF
from PyQt6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtSvg import QSvgGenerator
from PyQt6.QtWidgets import QGraphicsScene

from core.models import WesternBlotPanel


def export_wb_source_data(panel: WesternBlotPanel, path: str | Path) -> Path:
    target = Path(path)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    image_data = panel.data.get("image_data", {})
    raw_source_path = getattr(panel, "raw_source_path", None)
    if raw_source_path is None or not Path(raw_source_path).exists():
        raise FileNotFoundError("Western Blot source image is not available on disk.")

    target.parent.mkdir(parents=True, exist_ok=True)
    panel.append_audit("export_wb_source_data", target.name)
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(raw_source_path, f"{panel.panel_id}_wb_fullblot{Path(raw_source_path).suffix.lower()}")
        archive.writestr(
            f"{panel.panel_id}_wb_crops.json",
            json.dumps(panel.source_data_manifest(), ensure_ascii=False, indent=2),
        )
        audit_lines = [json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) for entry in panel.audit_log]
        archive.writestr(f"{panel.panel_id}_audit.log", "\n".join(audit_lines) + "\n")
        archive.writestr(
            f"{panel.panel_id}_source_manifest.json",
            json.dumps(
                {
                    "panel_uuid": panel.panel_uuid,
                    "panel_id": panel.panel_id,
                    "original_source": image_data.get("original_source"),
                    "original_hash": image_data.get("original_hash"),
                    "non_destructive": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return target


def export_wb_source_folder(panel: WesternBlotPanel, figure_path: str | Path) -> Path:
    target = Path(figure_path)
    source_dir = target.with_name(f"{target.stem}_source")
    source_dir.mkdir(parents=True, exist_ok=True)
    raw_source_path = getattr(panel, "raw_source_path", None)
    if raw_source_path is None or not Path(raw_source_path).exists():
        raise FileNotFoundError("Western Blot source image is not available on disk.")

    copy2(raw_source_path, source_dir / f"{panel.panel_id}_fullblot{Path(raw_source_path).suffix.lower()}")
    (source_dir / f"{panel.panel_id}_crops.json").write_text(
        json.dumps(panel.source_data_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    audit_lines = [json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) for entry in panel.audit_log]
    (source_dir / f"{panel.panel_id}_audit.log").write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
    panel.append_audit("export_wb_source_folder", source_dir.name)
    return source_dir


def export_wb_figure_scene(scene: QGraphicsScene, path: str | Path, dpi: int = 1200) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix == ".pdf":
        _export_pdf(scene, target, dpi=dpi)
    elif suffix == ".svg":
        _export_svg(scene, target)
    elif suffix in {".tif", ".tiff"}:
        _export_raster(scene, target, dpi=dpi, fmt="TIFF")
    elif suffix == ".png":
        _export_raster(scene, target, dpi=dpi, fmt="PNG")
    else:
        raise ValueError(f"Unsupported WB figure export format: {suffix}")
    return target


def _export_rect(scene: QGraphicsScene) -> QRectF:
    rect = scene.itemsBoundingRect().adjusted(-4, -4, 4, 4)
    if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
        rect = scene.sceneRect()
    return rect


def _export_pdf(scene: QGraphicsScene, filepath: Path, dpi: int = 1200) -> None:
    scene_rect = _export_rect(scene)
    scale = max(float(dpi), 72.0) / 72.0
    target_rect = QRectF(0, 0, scene_rect.width() * scale, scene_rect.height() * scale)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(filepath))
    printer.setResolution(int(dpi))
    printer.setPageSize(QPageSize(QSizeF(scene_rect.width(), scene_rect.height()), QPageSize.Unit.Point))
    printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    painter = QPainter(printer)
    painter.fillRect(target_rect, QColor(255, 255, 255))
    scene.render(painter, target_rect, scene_rect)
    painter.end()


def _export_svg(scene: QGraphicsScene, filepath: Path) -> None:
    scene_rect = _export_rect(scene)
    generator = QSvgGenerator()
    generator.setFileName(str(filepath))
    generator.setSize(QSize(max(1, int(scene_rect.width())), max(1, int(scene_rect.height()))))
    generator.setViewBox(QRectF(0, 0, scene_rect.width(), scene_rect.height()))
    painter = QPainter(generator)
    painter.fillRect(QRectF(0, 0, scene_rect.width(), scene_rect.height()), QColor(255, 255, 255))
    scene.render(painter, QRectF(0, 0, scene_rect.width(), scene_rect.height()), scene_rect)
    painter.end()


def _export_raster(scene: QGraphicsScene, filepath: Path, dpi: int = 300, fmt: str = "PNG") -> None:
    scene_rect = _export_rect(scene)
    scale = dpi / 72.0
    width = max(1, int(scene_rect.width() * scale))
    height = max(1, int(scene_rect.height() * scale))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    dots_per_meter = int(dpi / 0.0254)
    image.setDotsPerMeterX(dots_per_meter)
    image.setDotsPerMeterY(dots_per_meter)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scene.render(painter, QRectF(0, 0, width, height), scene_rect)
    painter.end()
    if fmt == "TIFF":
        buffer = image.bits().asstring(image.sizeInBytes())
        pil_image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
        pil_image.save(filepath, "TIFF", dpi=(dpi, dpi), compression="none")
    else:
        image.save(str(filepath), "PNG")
