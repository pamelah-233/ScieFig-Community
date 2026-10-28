from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QImage, QImageReader, QPainter
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtSvg import QSvgRenderer

from core.models.panel import BasePanel, register_panel_class
from core.image_analysis.io_engine import resolve_channel_display_colors
from core.utils.hashing import sha256_file
from core.plotting.fluoro_render import render_merge_qimage
from core.utils.fluoro_image_io import import_fluorescence_image
from core.utils.image_formats import COMPOSER_IMAGE_SUFFIXES, FLUORESCENCE_MICROSCOPY_SUFFIXES, compound_suffix
from core.utils.rendering import fitted_size, fit_qimage_to_size, qimage_to_bytes, target_pixel_size


NORMALIZED_EXPORT_METADATA_KEYS: tuple[str, ...] = (
    "source_module",
    "source_panel_uuid",
    "export_item_key",
    "export_item_type",
    "source_record_id",
    "source_record_namespace",
    "source_context_hash",
    "created_from_layout_object_id",
    "source_dpi",
    "preferred_width_mm",
    "preferred_height_mm",
    "source_format",
    "source_width_px",
    "source_height_px",
    "raw_source",
    "image_path",
)


@register_panel_class
@dataclass
class ExternalImagePanel(BasePanel):
    PANEL_TYPE: ClassVar[str] = "external_image"
    SOURCE_MODULE: ClassVar[str] = "external"
    panel_type: str = field(default=PANEL_TYPE)

    @classmethod
    def archive_copy_path_for(cls, panel_uuid: str, data: dict[str, Any]) -> str:
        source_name = str(data.get("raw_source") or "").strip()
        if not source_name:
            for key in ("image_path", "embedded_copy_path", "raw_copy_path"):
                candidate = Path(str(data.get(key, "")).strip()).name
                if candidate:
                    source_name = candidate
                    break
        if not source_name:
            source_name = "external_asset"
        return f"data/external/{panel_uuid}/{source_name}"

    def archive_copy_path(self) -> str:
        return self.archive_copy_path_for(self.panel_uuid, self.data)

    def normalized_source_metadata(self) -> dict[str, Any]:
        return {key: self.data[key] for key in NORMALIZED_EXPORT_METADATA_KEYS if key in self.data}

    def normalized_source_module(self) -> str:
        return str(self.data.get("source_module") or self.source_module or self.panel_type)

    def import_image(self, path: str | Path) -> None:
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        suffix = compound_suffix(source)
        if suffix not in COMPOSER_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported external image format: {suffix}")
        self.data.update(
            {
                "raw_source": source.name,
                "raw_hash": sha256_file(source),
                "raw_copy_path": f"data/external/{source.name}",
                "embedded_copy_path": f"data/external/{source.name}",
                "image_path": str(source),
                "media_type": suffix.lstrip("."),
                "source_format": suffix.lstrip(".").upper(),
                "is_vector": suffix in {".svg", ".pdf"},
                "linked_or_embedded": "embedded",
            }
        )
        self.raw_source_path = source
        self.append_audit("import_external_image", source.name)

    def render_preview(self, target_width_mm: float, target_height_mm: float, dpi: int = 150) -> QImage:
        source = self._source_path()
        if source is None or not source.exists():
            raise FileNotFoundError("External image source is not available on disk.")
        target_size = target_pixel_size(target_width_mm, target_height_mm, dpi)
        return self._render_source_to_qimage(source, target_size)

    def render_native_microscopy(self) -> QImage:
        source = self._source_path()
        if source is None or not source.exists():
            raise FileNotFoundError("External microscopy source is not available on disk.")
        if compound_suffix(source) not in FLUORESCENCE_MICROSCOPY_SUFFIXES:
            raise ValueError(f"External image is not a microscopy source: {source}")
        return self._render_native_microscopy(source)

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> Path | bytes:
        source = self._source_path()
        if source is None or not source.exists():
            raise FileNotFoundError("External image source is not available on disk.")
        export_format = format.lower().lstrip(".")
        source_suffix = source.suffix.lower().lstrip(".")
        if source_suffix == export_format and export_format in {"svg", "pdf"}:
            return source
        if source_suffix in {"svg", "pdf"} and export_format in {"svg", "pdf"}:
            return source
        raster_format = export_format if export_format in {"png", "tif", "tiff", "jpg", "jpeg", "bmp"} else "png"
        image = self.render_preview(target_width_mm, target_height_mm, dpi=dpi)
        return qimage_to_bytes(image, raster_format)

    def _source_path(self) -> Path | None:
        image_path = self.data.get("image_path") or getattr(self, "raw_source_path", None)
        return Path(str(image_path)) if image_path else None

    def _render_source_to_qimage(self, source: Path, target_size: QSize) -> QImage:
        suffix = compound_suffix(source)
        if suffix == ".svg":
            return self._render_svg(source, target_size)
        if suffix == ".pdf":
            return self._render_pdf(source, target_size)
        if suffix in FLUORESCENCE_MICROSCOPY_SUFFIXES:
            return self._render_microscopy(source, target_size)
        return self._render_raster(source, target_size)

    def _render_svg(self, source: Path, target_size: QSize) -> QImage:
        renderer = QSvgRenderer(str(source))
        if not renderer.isValid():
            raise ValueError(f"Could not render SVG external panel: {source}")
        canvas = QImage(target_size, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        default_size = renderer.defaultSize()
        draw_size = fitted_size(default_size.width(), default_size.height(), target_size)
        rect = QRectF(
            (target_size.width() - draw_size.width()) / 2,
            (target_size.height() - draw_size.height()) / 2,
            draw_size.width(),
            draw_size.height(),
        )
        painter = QPainter(canvas)
        renderer.render(painter, rect)
        painter.end()
        return canvas

    def _render_pdf(self, source: Path, target_size: QSize) -> QImage:
        document = QPdfDocument(None)
        if document.load(str(source)) != QPdfDocument.Error.None_:
            raise ValueError(f"Could not render PDF external panel: {source}")
        page_size = document.pagePointSize(0)
        render_size = fitted_size(int(page_size.width()), int(page_size.height()), target_size)
        image = document.render(0, render_size)
        if image.isNull():
            raise ValueError(f"Could not render PDF page for external panel: {source}")
        return fit_qimage_to_size(image, target_size)

    def _render_raster(self, source: Path, target_size: QSize) -> QImage:
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        natural_size = reader.size()
        if natural_size.isValid():
            reader.setScaledSize(fitted_size(natural_size.width(), natural_size.height(), target_size))
        image = reader.read()
        if image.isNull():
            raise ValueError(f"Could not render raster external panel: {source}")
        return fit_qimage_to_size(image, target_size)

    def _render_microscopy(self, source: Path, target_size: QSize) -> QImage:
        image = self._render_native_microscopy(source)
        return fit_qimage_to_size(image, target_size, Qt.GlobalColor.black)

    def _render_native_microscopy(self, source: Path) -> QImage:
        imported = import_fluorescence_image(source)
        channels = []
        pseudocolors = resolve_channel_display_colors(
            imported.channel_names,
            imported.metadata.get("channel_colors"),
            channel_count=int(imported.analysis_data.shape[0]),
        )
        max_value = float(imported.analysis_data.max()) if imported.analysis_data.size else 1.0
        for index in range(imported.analysis_data.shape[0]):
            channels.append(
                {
                    "index": index,
                    "name": imported.channel_names[index] if index < len(imported.channel_names) else f"Ch{index + 1}",
                    "pseudocolor": pseudocolors[index],
                    "display_min": 0.0,
                    "display_max": max(max_value, 1.0),
                    "visible": True,
                }
            )
        return render_merge_qimage(imported.analysis_data, channels)
