from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import ClassVar

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QTransform

from core.models.panel import BasePanel, register_panel_class
from core.utils.hashing import sha256_file
from core.utils.image_formats import WB_IMAGE_SUFFIXES
from core.utils.rendering import fit_qimage_to_size, qimage_to_bytes, target_pixel_size


@register_panel_class
@dataclass
class WesternBlotPanel(BasePanel):
    PANEL_TYPE: ClassVar[str] = "western_blot"
    SOURCE_MODULE: ClassVar[str] = "wb"
    panel_type: str = field(default=PANEL_TYPE)

    def import_full_blot(self, path: str | Path) -> dict:
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        suffix = source.suffix.lower()
        if suffix not in WB_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported Western Blot image format: {suffix}")

        reader = QImageReader(str(source))
        image = self._read_source_qimage(source)
        if image.isNull():
            raise ValueError(f"Could not read Western Blot image: {source}")

        image_format = bytes(reader.format()).decode("ascii", errors="ignore").upper() or suffix.lstrip(".").upper()
        image_data = {
            "original_source": source.name,
            "original_path": f"data/wb/{source.name}",
            "original_hash": sha256_file(source),
            "original_format": image_format,
            "bit_depth": image.depth(),
            "width_px": image.width(),
            "height_px": image.height(),
            "compression": "unknown",
            "source_pixel_unit": "px",
        }
        band_strips = list(self.data.get("band_strips", []))
        next_strip_number = 1
        for existing in band_strips:
            digits = "".join(ch for ch in str(existing.get("strip_id", "")).split("_")[-1] if ch.isdigit())
            if digits:
                next_strip_number = max(next_strip_number, int(digits) + 1)
        strip = {
            "strip_id": f"strip_{next_strip_number}",
            "image_path": str(source),
            "image_hash": image_data["original_hash"],
            "original_source": source.name,
            "protein_name": source.stem,
            "marker_values": [],
            "crop_roi": {"x": 0, "y": 0, "w": image.width(), "h": image.height()},
            "adjustments": {
                "brightness": 0,
                "contrast": 0,
                "rotation": 0.0,
                "flip_horizontal": False,
                "flip_vertical": False,
            },
        }
        band_strips.append(strip)
        self.data.update(
            {
                "raw_source": source.name,
                "raw_hash": image_data["original_hash"],
                "raw_copy_path": image_data["original_path"],
                "image_path": str(source),
                "image_data": image_data,
                "band_strips": band_strips,
                "crops": self.data.get("crops", []),
                "adjustments": self.data.get(
                    "adjustments",
                    {
                        "brightness": 0,
                        "contrast": 0,
                        "rotation": 0.0,
                        "flip_horizontal": False,
                        "flip_vertical": False,
                        "scope": "global",
                        "local_edits": False,
                        "display_only": True,
                    },
                ),
                "annotations": self.data.get(
                    "annotations",
                    {
                        "lane_labels": [],
                        "protein_labels": [],
                        "markers": [],
                        "lane_anchors": [],
                        "marker_anchors": [],
                    },
                ),
                "layout": self.data.get("layout", {"image_x": 0.0, "image_y": 0.0}),
                "wb_layout": self.data.get(
                    "wb_layout",
                    {
                        "enabled": False,
                        "bands": [],
                        "lane_label_position": "top",
                        "protein_label_side": "right",
                        "marker_label_side": "left",
                        "loading_control_index": None,
                        "lane_hierarchy": {},
                        "group_gaps": [],
                    },
                ),
            }
        )
        self.raw_source_path = source
        self.analysis["integrity_badge"] = self._build_integrity_badge()
        self.append_audit("import_wb_full_blot", source.name)
        return image_data

    def set_crop_roi(self, x: int, y: int, width: int, height: int, protein: str = "") -> dict:
        image_data = self.data.get("image_data", {})
        image_width = int(image_data.get("width_px", 0))
        image_height = int(image_data.get("height_px", 0))
        if image_width <= 0 or image_height <= 0:
            raise ValueError("Import a Western Blot image before setting a crop ROI.")
        if width <= 0 or height <= 0:
            raise ValueError("Crop ROI width and height must be positive.")
        if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
            raise ValueError("Crop ROI must stay within the source image bounds.")

        crops = list(self.data.get("crops", []))
        crop = {
            "crop_id": f"crop_{len(crops) + 1}",
            "protein": protein,
            "roi": {"x": int(x), "y": int(y), "w": int(width), "h": int(height)},
            "source_image": image_data.get("original_source", ""),
            "coordinate_space": "source_pixel",
            "non_destructive": True,
        }
        crops.append(crop)
        self.data["crops"] = crops
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.analysis["integrity_badge"] = self._build_integrity_badge()
        self.append_audit("set_wb_crop_roi", f"{crop['crop_id']}: {crop['roi']}")
        return crop

    def set_display_adjustments(
        self,
        brightness: int,
        contrast: int,
        rotation: float | None = None,
        flip_horizontal: bool | None = None,
        flip_vertical: bool | None = None,
    ) -> dict:
        current = dict(self.data.get("adjustments", {}))
        adjustments = {
            "brightness": int(brightness),
            "contrast": int(contrast),
            "rotation": float(current.get("rotation", 0.0) if rotation is None else rotation),
            "flip_horizontal": bool(current.get("flip_horizontal", False) if flip_horizontal is None else flip_horizontal),
            "flip_vertical": bool(current.get("flip_vertical", False) if flip_vertical is None else flip_vertical),
            "scope": "global",
            "local_edits": False,
            "display_only": True,
        }
        self.data["adjustments"] = adjustments
        self.append_audit("set_wb_display_adjustments", str(adjustments))
        return adjustments

    def update_band_strip_crop(self, index: int, x: int, y: int, width: int, height: int, protein: str | None = None) -> dict:
        strips = list(self.data.get("band_strips", []))
        if index < 0 or index >= len(strips):
            raise IndexError("Band strip index out of range.")
        strip = dict(strips[index])
        image = QImage(str(strip.get("image_path", "")))
        image_width = image.width() if not image.isNull() else int(strip.get("crop_roi", {}).get("w", 1))
        image_height = image.height() if not image.isNull() else int(strip.get("crop_roi", {}).get("h", 1))
        if width <= 0 or height <= 0:
            raise ValueError("Strip crop ROI width and height must be positive.")
        x = max(0, min(int(x), max(image_width - 1, 0)))
        y = max(0, min(int(y), max(image_height - 1, 0)))
        width = max(1, min(int(width), max(image_width - x, 1)))
        height = max(1, min(int(height), max(image_height - y, 1)))
        strip["crop_roi"] = {"x": x, "y": y, "w": width, "h": height}
        if protein is not None:
            strip["protein_name"] = protein
        strips[index] = strip
        self.data["band_strips"] = self._renumber_band_strips(strips)
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("update_wb_band_strip_crop", f"{strip.get('strip_id', index)}: {strip['crop_roi']}")
        return strip

    def update_band_strip_adjustments(
        self,
        index: int,
        brightness: int,
        contrast: int,
        rotation: float,
        flip_horizontal: bool | None = None,
        flip_vertical: bool | None = None,
    ) -> dict:
        strips = list(self.data.get("band_strips", []))
        if index < 0 or index >= len(strips):
            raise IndexError("Band strip index out of range.")
        strip = dict(strips[index])
        current = dict(strip.get("adjustments", {}))
        adjustments = {
            "brightness": int(brightness),
            "contrast": int(contrast),
            "rotation": float(rotation),
            "flip_horizontal": bool(current.get("flip_horizontal", False) if flip_horizontal is None else flip_horizontal),
            "flip_vertical": bool(current.get("flip_vertical", False) if flip_vertical is None else flip_vertical),
        }
        strip["adjustments"] = adjustments
        strips[index] = strip
        self.data["band_strips"] = self._renumber_band_strips(strips)
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("update_wb_band_strip_adjustments", f"{strip.get('strip_id', index)}: {adjustments}")
        return adjustments

    def update_band_strip_protein(self, index: int, protein: str) -> dict:
        strips = list(self.data.get("band_strips", []))
        if index < 0 or index >= len(strips):
            raise IndexError("Band strip index out of range.")
        strips[index]["protein_name"] = protein
        self.data["band_strips"] = self._renumber_band_strips(strips)
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("update_wb_band_strip_protein", f"{strips[index].get('strip_id', index)}: {protein}")
        return strips[index]

    def delete_band_strip(self, index: int) -> None:
        strips = list(self.data.get("band_strips", []))
        if index < 0 or index >= len(strips):
            raise IndexError("Band strip index out of range.")
        removed = strips.pop(index)
        self.data["band_strips"] = self._renumber_band_strips(strips)
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("delete_wb_band_strip", str(removed.get("strip_id", index)))

    def duplicate_band_strip(self, index: int) -> dict:
        strips = list(self.data.get("band_strips", []))
        if index < 0 or index >= len(strips):
            raise IndexError("Band strip index out of range.")
        duplicate = deepcopy(strips[index])
        next_strip_number = 1
        for existing in strips:
            digits = "".join(ch for ch in str(existing.get("strip_id", "")).split("_")[-1] if ch.isdigit())
            if digits:
                next_strip_number = max(next_strip_number, int(digits) + 1)
        duplicate["strip_id"] = f"strip_{next_strip_number}"
        duplicate["protein_name"] = f"{duplicate.get('protein_name', 'strip')} copy"
        strips.insert(index + 1, duplicate)
        self.data["band_strips"] = strips
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("duplicate_wb_band_strip", str(duplicate["strip_id"]))
        return duplicate

    def move_band_strip(self, index: int, direction: int) -> None:
        strips = list(self.data.get("band_strips", []))
        target = index + direction
        if index < 0 or index >= len(strips) or target < 0 or target >= len(strips):
            return
        strips[index], strips[target] = strips[target], strips[index]
        self.data["band_strips"] = self._renumber_band_strips(strips)
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("reorder_wb_band_strip", f"{index} -> {target}")

    def _renumber_band_strips(self, strips: list[dict]) -> list[dict]:
        renumbered = []
        for index, strip in enumerate(strips, start=1):
            item = dict(strip)
            item.setdefault("strip_id", f"strip_{index}")
            renumbered.append(item)
        return renumbered

    def set_annotations(
        self,
        lane_labels: list[str],
        protein_labels: list[str],
        markers: list[str],
        lane_anchors: list[float] | None = None,
        marker_anchors: list[float] | None = None,
    ) -> dict:
        current = dict(self.data.get("annotations", {}))
        annotations = {
            "lane_labels": [label for label in lane_labels if label],
            "protein_labels": [label for label in protein_labels if label],
            "markers": [marker for marker in markers if marker],
            "lane_anchors": list(current.get("lane_anchors", []) if lane_anchors is None else lane_anchors),
            "marker_anchors": list(current.get("marker_anchors", []) if marker_anchors is None else marker_anchors),
        }
        self.data["annotations"] = annotations
        self.append_audit("set_wb_annotations", str(annotations))
        return annotations

    def update_crop_roi(self, index: int, x: int, y: int, width: int, height: int) -> dict:
        crops = list(self.data.get("crops", []))
        if index < 0 or index >= len(crops):
            raise IndexError("Crop index out of range.")
        image_data = self.data.get("image_data", {})
        image_width = int(image_data.get("width_px", 0))
        image_height = int(image_data.get("height_px", 0))
        if width <= 0 or height <= 0:
            raise ValueError("Crop ROI width and height must be positive.")
        x = max(0, min(int(x), max(image_width - 1, 0)))
        y = max(0, min(int(y), max(image_height - 1, 0)))
        width = max(1, min(int(width), max(image_width - x, 1)))
        height = max(1, min(int(height), max(image_height - y, 1)))
        crops[index]["roi"] = {"x": x, "y": y, "w": width, "h": height}
        self.data["crops"] = crops
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("update_wb_crop_roi", f"{crops[index].get('crop_id', index)}: {crops[index]['roi']}")
        return crops[index]

    def delete_crop_roi(self, index: int) -> None:
        crops = list(self.data.get("crops", []))
        if index < 0 or index >= len(crops):
            raise IndexError("Crop index out of range.")
        removed = crops.pop(index)
        for crop_index, crop in enumerate(crops, start=1):
            crop["crop_id"] = f"crop_{crop_index}"
        self.data["crops"] = crops
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("delete_wb_crop_roi", str(removed.get("crop_id", index)))

    def move_crop_roi(self, index: int, direction: int) -> None:
        crops = list(self.data.get("crops", []))
        target = index + direction
        if index < 0 or index >= len(crops) or target < 0 or target >= len(crops):
            return
        crops[index], crops[target] = crops[target], crops[index]
        for crop_index, crop in enumerate(crops, start=1):
            crop["crop_id"] = f"crop_{crop_index}"
        self.data["crops"] = crops
        self.data.setdefault("wb_layout", {})["enabled"] = False
        self.append_audit("reorder_wb_crop_roi", f"{index} -> {target}")

    def set_band_configuration(
        self,
        bands: list[dict],
        lane_labels: list[str],
        loading_control_index: int | None = None,
        lane_label_position: str = "top",
        protein_label_side: str = "right",
        marker_label_side: str = "left",
        lane_hierarchy: dict | None = None,
        group_gaps: list[dict] | None = None,
        strip_gap_px: int | None = None,
        label_offset_px: int | None = None,
        enabled: bool = True,
    ) -> dict:
        crops = list(self.data.get("crops", []))
        band_strips = list(self.data.get("band_strips", []))
        placed_ids = list(self.data.get("placed_strip_ids", []))
        ordered_strips = band_strips
        if placed_ids:
            by_id = {str(strip.get("strip_id")): strip for strip in band_strips}
            ordered_strips = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
        configured_bands = []
        band_sources = crops if crops else [
            {
                "crop_id": strip.get("strip_id", f"strip_{index + 1}"),
                "protein": strip.get("protein_name", ""),
                "roi": strip.get("crop_roi", {}),
                "strip_id": strip.get("strip_id", f"strip_{index + 1}"),
                "image_path": strip.get("image_path", ""),
            }
            for index, strip in enumerate(ordered_strips)
        ]
        for index, crop in enumerate(band_sources):
            source = bands[index] if index < len(bands) else {}
            protein = str(source.get("protein", "")).strip()
            mw = str(source.get("mw_kda", "") or source.get("mw", "")).strip()
            is_loading_ctrl = bool(source.get("is_loading_ctrl", False))
            crop["protein"] = protein
            if index < len(crops):
                crops[index]["protein"] = protein
            strip_id = crop.get("strip_id")
            for strip in band_strips:
                if strip.get("strip_id") == strip_id:
                    strip["protein_name"] = protein
                    strip["marker_values"] = [{"kda": mw, "y_position": 0.5}] if mw else []
                    break
            configured_bands.append(
                {
                    "band_index": index,
                    "crop_id": crop.get("crop_id", f"crop_{index + 1}"),
                    "strip_id": crop.get("strip_id"),
                    "image_path": crop.get("image_path"),
                    "protein": protein,
                    "mw_kda": mw,
                    "is_loading_ctrl": is_loading_ctrl,
                    "roi": deepcopy(crop.get("roi", {})),
                }
            )
        self.data["crops"] = crops
        self.data["band_strips"] = band_strips
        layout = {
            "enabled": bool(enabled),
            "bands": configured_bands,
            "lane_labels": [label for label in lane_labels if label],
            "lane_label_position": lane_label_position,
            "protein_label_side": protein_label_side,
            "marker_label_side": marker_label_side,
            "loading_control_index": loading_control_index,
            "lane_hierarchy": lane_hierarchy or {},
            "group_gaps": group_gaps or [],
            "style": {
                "font_family": "Arial",
                "tick_font_pt": 6,
                "protein_italic": True,
                "marker_line_pt": 0.5,
                "band_gap_pt": 8,
                "strip_gap_px": 25 if strip_gap_px is None else int(strip_gap_px),
                "label_offset_px": 15 if label_offset_px is None else int(label_offset_px),
                "padding_pt": 10,
            },
        }
        self.data["wb_layout"] = layout
        annotations = dict(self.data.get("annotations", {}))
        annotations["lane_labels"] = layout["lane_labels"]
        annotations["protein_labels"] = [band["protein"] for band in configured_bands if band["protein"]]
        annotations["markers"] = [band["mw_kda"] for band in configured_bands if band["mw_kda"]]
        self.data["annotations"] = annotations
        self.append_audit("set_wb_band_configuration", str(layout))
        return layout

    def reset_to_original(self) -> None:
        adjustments = dict(self.data.get("adjustments", {}))
        adjustments.update(
            {
                "brightness": 0,
                "contrast": 0,
                "rotation": 0.0,
                "flip_horizontal": False,
                "flip_vertical": False,
                "scope": "global",
                "local_edits": False,
                "display_only": True,
            }
        )
        self.data["adjustments"] = adjustments
        self.data["crops"] = []
        self.data["annotations"] = {
            "lane_labels": [],
            "protein_labels": [],
            "markers": [],
            "lane_anchors": [],
            "marker_anchors": [],
        }
        self.data["layout"] = {"image_x": 0.0, "image_y": 0.0}
        self.data["wb_layout"] = {
            "enabled": False,
            "bands": [],
            "lane_label_position": "top",
            "protein_label_side": "right",
            "marker_label_side": "left",
            "loading_control_index": None,
            "lane_hierarchy": {},
            "group_gaps": [],
        }
        self.data["band_strips"] = []
        self.analysis["integrity_badge"] = self._build_integrity_badge()
        self.append_audit("reset_wb_to_original", "crops, annotations, layout and display adjustments cleared")

    def set_lane_anchor(self, index: int, x_px: float) -> list[float]:
        annotations = dict(self.data.get("annotations", {}))
        anchors = list(annotations.get("lane_anchors", []))
        while len(anchors) <= index:
            anchors.append(0.0)
        anchors[index] = float(x_px)
        annotations["lane_anchors"] = anchors
        self.data["annotations"] = annotations
        self.append_audit("set_wb_lane_anchor", f"{index}: {x_px}")
        return anchors

    def set_lane_calibration(self, first_x_px: float, last_x_px: float, lane_count: int) -> list[float]:
        if lane_count <= 0:
            raise ValueError("Lane count must be positive.")
        if lane_count == 1:
            anchors = [float(first_x_px)]
        else:
            step = (float(last_x_px) - float(first_x_px)) / (lane_count - 1)
            anchors = [float(first_x_px) + step * index for index in range(lane_count)]
        annotations = dict(self.data.get("annotations", {}))
        annotations["lane_anchors"] = anchors
        annotations["lane_calibration"] = {
            "mode": "first_last_interpolation",
            "first_x_px": float(first_x_px),
            "last_x_px": float(last_x_px),
            "lane_count": int(lane_count),
        }
        self.data["annotations"] = annotations
        self.append_audit("set_wb_lane_calibration", str(annotations["lane_calibration"]))
        return anchors

    def set_detected_lane_anchors(self, anchors: list[float]) -> list[float]:
        annotations = dict(self.data.get("annotations", {}))
        annotations["lane_anchors"] = [float(anchor) for anchor in anchors]
        annotations["lane_calibration"] = {
            "mode": "auto_projection",
            "lane_count": len(anchors),
        }
        self.data["annotations"] = annotations
        self.append_audit("set_wb_detected_lane_anchors", str(annotations["lane_anchors"]))
        return annotations["lane_anchors"]

    def set_marker_anchor(self, index: int, y_px: float) -> list[float]:
        annotations = dict(self.data.get("annotations", {}))
        anchors = list(annotations.get("marker_anchors", []))
        while len(anchors) <= index:
            anchors.append(0.0)
        anchors[index] = float(y_px)
        annotations["marker_anchors"] = anchors
        self.data["annotations"] = annotations
        self.append_audit("set_wb_marker_anchor", f"{index}: {y_px}")
        return anchors

    def clear_annotation_anchors(self) -> None:
        annotations = dict(self.data.get("annotations", {}))
        annotations["lane_anchors"] = []
        annotations["marker_anchors"] = []
        self.data["annotations"] = annotations
        self.append_audit("clear_wb_annotation_anchors", "lane_anchors and marker_anchors cleared")

    def set_layout_position(self, image_x: float, image_y: float) -> dict:
        layout = dict(self.data.get("layout", {}))
        layout["image_x"] = float(image_x)
        layout["image_y"] = float(image_y)
        self.data["layout"] = layout
        self.touch()
        return layout

    def render_preview(self, target_width_mm: float, target_height_mm: float, dpi: int = 150) -> QImage:
        panel_image = self._render_wb_panel_image()
        return fit_qimage_to_size(panel_image, target_pixel_size(target_width_mm, target_height_mm, dpi), QColor("white"))

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> bytes:
        raster_format = format.lower().lstrip(".")
        if raster_format in {"pdf", "svg"}:
            raster_format = "png"
        image = self.render_preview(target_width_mm, target_height_mm, dpi=dpi)
        return qimage_to_bytes(image, raster_format)

    def _render_wb_panel_image(self) -> QImage:
        layout = dict(self.data.get("wb_layout", {}))
        band_sources = self._render_band_sources()
        if layout.get("enabled") and band_sources:
            return self._render_wb_layout_image(band_sources, layout)
        if band_sources:
            return self._render_wb_stack_image(band_sources)
        source = self._source_display_image_for_model(self._default_source_path(), self.data.get("adjustments", {}))
        return self._transform_image(source, self.data.get("adjustments", {}))

    def _render_band_sources(self) -> list[dict]:
        crops = list(self.data.get("crops", []))
        if crops:
            return [
                {
                    "roi": dict(crop.get("roi", {})),
                    "protein": crop.get("protein", ""),
                    "mw_kda": crop.get("mw_kda", ""),
                    "image_path": crop.get("image_path") or self._default_source_path(),
                    "adjustments": crop.get("adjustments", self.data.get("adjustments", {})),
                }
                for crop in crops
            ]
        band_strips = list(self.data.get("band_strips", []))
        placed_ids = list(self.data.get("placed_strip_ids", []))
        if placed_ids:
            by_id = {str(strip.get("strip_id")): strip for strip in band_strips}
            band_strips = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
        return [
            {
                "roi": dict(strip.get("crop_roi", {})),
                "protein": strip.get("protein_name", ""),
                "mw_kda": (strip.get("marker_values") or [{}])[0].get("kda", "") if strip.get("marker_values") else "",
                "image_path": strip.get("image_path") or self._default_source_path(),
                "adjustments": strip.get("adjustments", {}),
            }
            for strip in band_strips
        ]

    def _render_wb_stack_image(self, band_sources: list[dict]) -> QImage:
        bands = [(self._band_image(source), source) for source in band_sources]
        gap = 12
        label_width = 120 if any(str(source.get("protein", "")).strip() for _image, source in bands) else 0
        width = max(image.width() for image, _source in bands) + label_width
        height = sum(image.height() for image, _source in bands) + gap * max(0, len(bands) - 1)
        canvas = QImage(max(1, width), max(1, height), QImage.Format.Format_ARGB32)
        canvas.fill(QColor("white"))
        painter = QPainter(canvas)
        painter.setPen(QColor("#111111"))
        painter.setFont(QFont("Arial", 9))
        y = 0
        for image, source in bands:
            painter.drawImage(0, y, image)
            protein = str(source.get("protein", "")).strip()
            if protein:
                painter.drawText(image.width() + 12, y + image.height() // 2 + 4, protein)
            y += image.height() + gap
        painter.end()
        return canvas

    def _render_wb_layout_image(self, band_sources: list[dict], layout: dict) -> QImage:
        bands = layout.get("bands", [])
        band_images = [self._band_image(source) for source in band_sources]
        gap = int(layout.get("style", {}).get("strip_gap_px", 25))
        padding_left = 72
        padding_right = 126
        hierarchy = layout.get("lane_hierarchy", {})
        hierarchy_levels = list(hierarchy.get("levels", [])) if isinstance(hierarchy, dict) else []
        group_levels = [level for level in hierarchy_levels if level.get("type") != "condition_row"]
        condition_rows = [level for level in hierarchy_levels if level.get("type") == "condition_row"]
        hierarchy_height = len(group_levels) * 22 + len(condition_rows) * 15 + (8 if hierarchy_levels else 0)
        padding_top = 36 + hierarchy_height
        padding_bottom = 32
        band_width = max(image.width() for image in band_images)
        content_width = band_width
        width = padding_left + content_width + padding_right
        height = padding_top + padding_bottom + sum(image.height() for image in band_images) + gap * max(0, len(band_images) - 1)
        canvas = QImage(max(1, width), max(1, height), QImage.Format.Format_ARGB32)
        canvas.fill(QColor("white"))
        painter = QPainter(canvas)
        painter.setPen(QColor("#111111"))
        font = QFont(str(layout.get("style", {}).get("font_family", "Arial")), int(layout.get("style", {}).get("tick_font_pt", 7)))
        painter.setFont(font)
        lane_labels = layout.get("lane_labels") or self.data.get("annotations", {}).get("lane_labels", [])
        lane_count = len(lane_labels)
        lane_positions = [padding_left + int((index + 0.5) * content_width / max(lane_count, 1)) for index in range(lane_count)]
        if hierarchy_levels and lane_count:
            current_y = padding_top - 12
            for row in reversed(condition_rows):
                current_y -= 15
                row_label = str(row.get("row_label", ""))
                painter.drawText(4, current_y + 10, row_label)
                for index, value in enumerate(row.get("values", [])[:lane_count]):
                    painter.drawText(lane_positions[index] - 12, current_y + 10, 24, 12, int(Qt.AlignmentFlag.AlignCenter), str(value))
            for level in reversed(group_levels):
                current_y -= 22
                for group_def in level.get("groups", []):
                    start = max(1, int(group_def.get("lane_start", 1)))
                    end = min(lane_count, int(group_def.get("lane_end", start)))
                    if start > end:
                        continue
                    x_start = lane_positions[start - 1]
                    x_end = lane_positions[end - 1]
                    x_center = (x_start + x_end) // 2
                    label = str(group_def.get("label", ""))
                    painter.drawText(x_center - 48, current_y, 96, 14, int(Qt.AlignmentFlag.AlignCenter), label)
                    if bool(group_def.get("show_bracket", True)):
                        y_line = current_y + 18
                        painter.drawLine(x_start, y_line, x_end, y_line)
                        painter.drawLine(x_start, y_line, x_start, y_line + 4)
                        painter.drawLine(x_end, y_line, x_end, y_line + 4)
        if lane_count:
            for index, label in enumerate(lane_labels):
                x = lane_positions[index]
                painter.drawText(x - 24, 18, 48, 16, int(Qt.AlignmentFlag.AlignCenter), str(label))
        y = padding_top
        protein_font = QFont(font)
        protein_font.setItalic(True)
        for index, image in enumerate(band_images):
            if image.width() != band_width:
                image = image.scaledToWidth(band_width, Qt.TransformationMode.SmoothTransformation)
            painter.drawImage(padding_left, y, image)
            band = bands[index] if index < len(bands) else {}
            source = band_sources[index]
            center_y = y + image.height() // 2 + 4
            mw = str(band.get("mw_kda", source.get("mw_kda", ""))).strip()
            if mw:
                painter.setFont(font)
                painter.drawText(6, center_y, mw)
                painter.drawLine(padding_left - 16, center_y - 4, padding_left - 4, center_y - 4)
            protein = str(band.get("protein", source.get("protein", ""))).strip()
            if protein:
                painter.setFont(protein_font)
                painter.drawText(padding_left + content_width + 18, center_y, protein)
            y += image.height() + gap
        painter.end()
        return canvas

    def _band_image(self, source: dict) -> QImage:
        image = self._source_display_image_for_model(source.get("image_path"), source.get("adjustments", {}))
        roi = dict(source.get("roi", {}))
        x = max(0, int(roi.get("x", 0)))
        y = max(0, int(roi.get("y", 0)))
        width = max(1, min(int(roi.get("w", image.width())), image.width() - x))
        height = max(1, min(int(roi.get("h", image.height())), image.height() - y))
        return self._transform_image(image.copy(x, y, width, height), source.get("adjustments", {}))

    def _default_source_path(self):
        return self.data.get("image_path") or getattr(self, "raw_source_path", None)

    def _source_display_image_for_model(self, source_path, adjustments: dict | None = None) -> QImage:
        if not source_path:
            raise FileNotFoundError("Western Blot source image is not available on disk.")
        image = self._read_source_qimage(Path(str(source_path)))
        if image.isNull():
            raise ValueError(f"Could not read Western Blot image: {source_path}")
        adjustments = adjustments or {}
        brightness = int(adjustments.get("brightness", 0))
        contrast = int(adjustments.get("contrast", 0))
        if brightness or contrast:
            image = self._adjust_qimage_for_display(image, brightness, contrast)
        return image

    def _read_source_qimage(self, source_path: Path) -> QImage:
        if source_path.suffix.lower() == ".svg":
            from PyQt6.QtCore import QSize
            from PyQt6.QtSvg import QSvgRenderer

            renderer = QSvgRenderer(str(source_path))
            if not renderer.isValid():
                return QImage()
            default_size = renderer.defaultSize()
            size = default_size if default_size.isValid() else QSize(1024, 768)
            image = QImage(size, QImage.Format.Format_ARGB32)
            image.fill(QColor("white"))
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            return image
        return QImage(str(source_path))

    def _transform_image(self, image: QImage, adjustments: dict) -> QImage:
        transform = QTransform()
        if adjustments.get("flip_horizontal"):
            transform.scale(-1, 1)
        if adjustments.get("flip_vertical"):
            transform.scale(1, -1)
        rotation = float(adjustments.get("rotation", 0.0))
        if rotation:
            transform.rotate(rotation)
        if transform.isIdentity():
            return image
        return image.transformed(transform, Qt.TransformationMode.SmoothTransformation)

    def _adjust_qimage_for_display(self, image: QImage, brightness: int, contrast: int) -> QImage:
        adjusted = image.convertToFormat(QImage.Format.Format_ARGB32)
        factor = (259 * (contrast + 255)) / (255 * (259 - contrast)) if contrast != 0 else 1.0
        for y in range(adjusted.height()):
            for x in range(adjusted.width()):
                color = adjusted.pixelColor(x, y)
                red = max(0, min(255, int(factor * (color.red() - 128) + 128 + brightness)))
                green = max(0, min(255, int(factor * (color.green() - 128) + 128 + brightness)))
                blue = max(0, min(255, int(factor * (color.blue() - 128) + 128 + brightness)))
                adjusted.setPixelColor(x, y, QColor(red, green, blue, color.alpha()))
        return adjusted

    def source_data_manifest(self) -> dict:
        return {
            "panel_uuid": self.panel_uuid,
            "panel_id": self.panel_id,
            "panel_type": self.panel_type,
            "image_data": self.data.get("image_data", {}),
            "crops": self.data.get("crops", []),
            "adjustments": self.data.get("adjustments", {}),
            "band_strips": self.data.get("band_strips", []),
            "annotations": self.data.get("annotations", {}),
            "integrity_badge": self.analysis.get("integrity_badge", {}),
            "audit_log": [entry.to_dict() for entry in self.audit_log],
        }

    def _build_integrity_badge(self) -> dict:
        image_data = self.data.get("image_data", {})
        warnings = []
        if str(image_data.get("original_format", "")).upper() in {"JPG", "JPEG"}:
            warnings.append("JPEG is lossy; use original TIFF/PNG when available.")
        if int(image_data.get("bit_depth", 0) or 0) <= 8:
            warnings.append("8-bit source recorded; keep full-length blot for traceability.")
        return {
            "status": "warning" if warnings else "pass",
            "label": "Integrity: source retained, non-destructive crop",
            "warnings": warnings,
        }
