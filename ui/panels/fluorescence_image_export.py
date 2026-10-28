from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)

from core.plotting.fluoro_render import FORCED_RGB_SPLIT_MODE, RGB_COMPOSITE_MODE, render_fluorescence_qimage, rgb_interpretation_mode
from core.utils.i18n import tr
from core.utils.recent_directory import remember_file_directory
from core.utils.rendering import qimage_to_bytes
from ui.export_menu import apply_native_png_suffix, suggested_export_path


SOURCE_ORIGINAL = "original"
SOURCE_OVERLAY = "overlay"
SOURCE_UNADJUSTED = "unadjusted"
COMPOSITION_MERGE = "merge"
COMPOSITION_CHANNELS = "channels"
COMPOSITION_BOTH = "both"


class FluorescenceImageExportDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        has_original: bool = True,
        has_overlay: bool = False,
        default_overlay: bool = False,
        allow_separate_channels: bool = True,
    ) -> None:
        super().__init__(parent)
        self._has_original = bool(has_original)
        self._has_overlay = bool(has_overlay)
        self._default_overlay = bool(default_overlay)
        self._allow_separate_channels = bool(allow_separate_channels)
        self.source_prompt = QLabel()
        self.composition_prompt = QLabel()
        self.display_hint = QLabel()
        self.display_hint.setWordWrap(True)
        self.original_radio = QRadioButton()
        self.unadjusted_radio = QRadioButton()
        self.overlay_radio = QRadioButton()
        self.merge_radio = QRadioButton()
        self.channels_radio = QRadioButton()
        self.both_radio = QRadioButton()
        source_group = QButtonGroup(self)
        source_group.addButton(self.original_radio)
        source_group.addButton(self.unadjusted_radio)
        source_group.addButton(self.overlay_radio)
        composition_group = QButtonGroup(self)
        composition_group.addButton(self.merge_radio)
        composition_group.addButton(self.channels_radio)
        composition_group.addButton(self.both_radio)
        self.original_radio.setEnabled(self._has_original)
        self.unadjusted_radio.setEnabled(self._has_original)
        self.overlay_radio.setEnabled(self._has_overlay)
        self.channels_radio.setEnabled(self._allow_separate_channels)
        self.both_radio.setEnabled(self._allow_separate_channels)
        self.merge_radio.setChecked(True)
        if self._default_overlay and self._has_overlay:
            self.overlay_radio.setChecked(True)
        elif self._has_original:
            self.original_radio.setChecked(True)
        elif self._has_overlay:
            self.overlay_radio.setChecked(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.export_button = self.buttons.addButton("", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.source_prompt)
        layout.addWidget(self.original_radio)
        layout.addWidget(self.unadjusted_radio)
        layout.addWidget(self.overlay_radio)
        layout.addWidget(self.composition_prompt)
        layout.addWidget(self.merge_radio)
        layout.addWidget(self.channels_radio)
        layout.addWidget(self.both_radio)
        layout.addWidget(self.display_hint)
        layout.addWidget(self.buttons)
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("export_image_title"))
        self.source_prompt.setText(tr("export_fluoro_source_prompt"))
        self.composition_prompt.setText(tr("export_image_composition_prompt"))
        self.display_hint.setText(tr("export_image_display_hint"))
        self.original_radio.setText(tr("export_current_display"))
        self.unadjusted_radio.setText(tr("export_unadjusted_display"))
        self.overlay_radio.setText(tr("export_current_overlay"))
        self.merge_radio.setText(tr("export_merged_image"))
        self.channels_radio.setText(tr("export_separate_channel_images"))
        self.both_radio.setText(tr("export_merge_and_channels"))
        self.export_button.setText(tr("export_image"))
        if self.cancel_button is not None:
            self.cancel_button.setText(tr("cancel"))

    def exec_choice(self) -> tuple[str, str] | None:
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        source = SOURCE_OVERLAY if self.overlay_radio.isChecked() else SOURCE_ORIGINAL
        if self.unadjusted_radio.isChecked():
            source = SOURCE_UNADJUSTED
        composition = COMPOSITION_CHANNELS if self.channels_radio.isChecked() else COMPOSITION_MERGE
        if self.both_radio.isChecked():
            composition = COMPOSITION_BOTH
        return source, composition


def fluorescence_has_measurement_overlay(image_record: dict | None) -> bool:
    return any(_overlay_records(image_record))


def choose_fluorescence_image_export(
    parent,
    *,
    has_original: bool,
    has_overlay: bool,
    default_overlay: bool = False,
    allow_separate_channels: bool = True,
) -> tuple[str, str] | None:
    if not has_original and not has_overlay:
        QMessageBox.information(parent, tr("export_image_title"), tr("export_image_source_prompt"))
        return None
    dialog = FluorescenceImageExportDialog(
        parent,
        has_original=has_original,
        has_overlay=has_overlay,
        default_overlay=default_overlay,
        allow_separate_channels=allow_separate_channels,
    )
    dialog.exec()
    return dialog.exec_choice()


def overlay_image_point(point) -> tuple[float, float] | None:
    if isinstance(point, dict):
        return float(point.get("x", 0.0)), float(point.get("y", 0.0))
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        return float(point[0]), float(point[1])
    return None


def _overlay_records(image_record: dict | None, *, channel_index: int | None = None) -> list[dict]:
    if not image_record:
        return []
    records = [
        dict(record)
        for record in list(image_record.get("fluorescence_ai_assisted_measurements", []))
        if isinstance(record, dict)
    ]
    if channel_index is None:
        return records
    wanted = int(channel_index)
    return [record for record in records if int(record.get("channel_index", 0) or 0) == wanted]


def _overlay_objects(record: dict) -> list[dict]:
    return [item for item in list(record.get("object_table", [])) if isinstance(item, dict)]


def paint_fluorescence_ai_overlay(overlay_image: QImage, image_record: dict | list | None, *, channel_index: int | None = None) -> QImage:
    if isinstance(image_record, list):
        records = [dict(record) for record in image_record if isinstance(record, dict)]
        if channel_index is not None:
            wanted = int(channel_index)
            records = [record for record in records if int(record.get("channel_index", 0) or 0) == wanted]
    else:
        records = _overlay_records(image_record, channel_index=channel_index)
    painted = QImage(overlay_image)
    painter = QPainter(painted)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    color_cycle = [
        QColor("#FFD13B"),
        QColor("#22D7E6"),
        QColor("#FF2AD4"),
        QColor("#00D26A"),
        QColor("#FF3333"),
    ]
    for record_index, record in enumerate(records):
        color = color_cycle[record_index % len(color_cycle)]
        painter.setPen(QPen(color, 2))
        for detected_object in _overlay_objects(record):
            path = QPainterPath()
            drew = False
            contours = detected_object.get("review_contours_px")
            if contours:
                for points in contours:
                    contour_points = list(points or [])
                    if len(contour_points) < 3:
                        continue
                    first = overlay_image_point(contour_points[0])
                    if first is None:
                        continue
                    path.moveTo(*first)
                    for point in contour_points[1:]:
                        mapped = overlay_image_point(point)
                        if mapped is None:
                            continue
                        path.lineTo(*mapped)
                    path.closeSubpath()
                    drew = True
            if not drew:
                contour_points = list(detected_object.get("contour_points_px", []))
                if len(contour_points) >= 3:
                    first = overlay_image_point(contour_points[0])
                    if first is not None:
                        path.moveTo(*first)
                        for point in contour_points[1:]:
                            mapped = overlay_image_point(point)
                            if mapped is None:
                                continue
                            path.lineTo(*mapped)
                        path.closeSubpath()
                        drew = True
            if drew:
                painter.drawPath(path)
                continue
            x = int(detected_object.get("bbox_x_min", 0) or 0)
            y = int(detected_object.get("bbox_y_min", 0) or 0)
            width = max(1, int(detected_object.get("bbox_x_max", 0) or 0) - x)
            height = max(1, int(detected_object.get("bbox_y_max", 0) or 0) - y)
            painter.drawRect(x, y, width, height)
    painter.end()
    return painted


def _channel_token(channel: dict) -> str:
    index = int(channel.get("index", 0))
    name = str(channel.get("name") or f"ch{index}")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or f"ch{index}"
    return f"ch{index}_{cleaned}"


def fluorescence_export_path(base: Path, token: str, composition: str) -> Path:
    target = Path(base)
    if target.suffix.lower() != ".png":
        target = target.with_suffix(".png")
    if composition == COMPOSITION_MERGE:
        return target
    return target.with_name(f"{target.stem}_{token}.png")


def fluorescence_export_targets(base: Path, tokens: list[str], composition: str) -> list[Path]:
    return [fluorescence_export_path(base, token, composition) for token in tokens]


def _styled_channels(panel, image_record: dict) -> list[dict]:
    channels = list(image_record.get("channels", []))
    styled = getattr(panel, "_styled_channels_for_render", None)
    if callable(styled):
        return styled(channels, image_record)
    return [dict(channel) for channel in channels]


def _apply_scale(image: QImage, image_record: dict, channel_index: int | None = None) -> QImage:
    from ui.panels.fluorescence_workspace import apply_fluorescence_native_export_scale

    target = f"merge_split:channel:{channel_index}" if channel_index is not None else f"{image_record.get('layout_mode', 'merge_split')}:merge"
    return apply_fluorescence_native_export_scale(image, image_record, target_id=target)


def render_fluorescence_export_payloads(panel, image_record: dict, *, source: str, composition: str) -> list[tuple[str, QImage]]:
    if source not in (SOURCE_ORIGINAL,SOURCE_OVERLAY,SOURCE_UNADJUSTED):
        raise ValueError(f'Unknown fluorescence image source: {source}')
    if composition not in (COMPOSITION_MERGE,COMPOSITION_CHANNELS,COMPOSITION_BOTH):
        raise ValueError(f'Unknown fluorescence image composition: {composition}')
    data = panel.image_data(str(image_record.get("uid", "")))
    if data is None:
        raise ValueError("Import a fluorescence image before exporting.")
    if source == SOURCE_UNADJUSTED:
        original = image_record.get('import_display_channels')
        if rgb_interpretation_mode(image_record) == FORCED_RGB_SPLIT_MODE:
            # The import snapshot describes one RGB composite, not the three
            # newly exposed components. Reuse their native initial display.
            channels = panel._rgb_component_channels(data)
        elif original:
            channels = deepcopy(original)
        else:
            # Existing projects predate display snapshots. Their recorded
            # auto-contrast range is the available initial display reference.
            channels = deepcopy(image_record.get('channels', []))
            for channel in channels:
                channel.pop('display_processing', None)
                channel['display_min'] = channel.get('auto_contrast_min', channel.get('display_min',0))
                channel['display_max'] = channel.get('auto_contrast_max', channel.get('display_max',1))
    else:
        channels = _styled_channels(panel, image_record)
    if source == SOURCE_OVERLAY and not fluorescence_has_measurement_overlay(image_record):
        raise ValueError("Run fluorescence measurement before exporting an overlay.")

    def finish(image: QImage, channel_index: int | None = None) -> QImage:
        if source == SOURCE_OVERLAY:
            image = paint_fluorescence_ai_overlay(image, image_record, channel_index=channel_index)
        return _apply_scale(image, image_record, channel_index)

    if composition in (COMPOSITION_CHANNELS,COMPOSITION_BOTH):
        if rgb_interpretation_mode(image_record) == RGB_COMPOSITE_MODE:
            return [("merge", finish(render_fluorescence_qimage(data, image_record, channels, view_mode="merge")))]
        payloads = ([("merge", finish(render_fluorescence_qimage(data, image_record, channels, view_mode="merge")))]
                    if composition == COMPOSITION_BOTH else [])
        for channel in channels:
            if str(channel.get("name", "")).casefold() == "alpha":
                continue
            payloads.append(
                (
                    _channel_token(channel),
                    finish(
                        render_fluorescence_qimage(data, image_record, channels, view_mode="single", channel=channel),
                        channel_index=int(channel.get("index", 0)),
                    ),
                )
            )
        return payloads
    return [("merge", finish(render_fluorescence_qimage(data, image_record, channels, view_mode="merge")))]


def write_fluorescence_png(image: QImage, path: str | Path) -> Path:
    target = Path(path)
    if target.suffix.lower() != ".png":
        target = target.with_suffix(".png")
    if image.isNull():
        raise ValueError(f"Could not export fluorescence image: {target}")
    target.write_bytes(qimage_to_bytes(image, "png"))
    return target


def confirm_fluorescence_export_overwrite(parent, targets: list[Path]) -> bool:
    existing = [Path(target) for target in targets if Path(target).exists()]
    if not existing:
        return True
    names = "\n".join(str(path) for path in existing)
    answer = QMessageBox.question(
        parent,
        tr("export_image_title"),
        f"{tr('export_replace_existing_files')}\n{names}",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def export_fluorescence_images(
    panel,
    image_record: dict,
    base_path: str | Path,
    *,
    source: str,
    composition: str,
    confirm_overwrite=None,
) -> list[Path]:
    payloads = render_fluorescence_export_payloads(panel, image_record, source=source, composition=composition)
    base = Path(apply_native_png_suffix(str(base_path)))
    planned = [(fluorescence_export_path(base, token, composition), image) for token, image in payloads]
    if confirm_overwrite is not None and not confirm_overwrite([path for path, _image in planned]):
        return []
    written = []
    for path, image in planned:
        written.append(write_fluorescence_png(image, path))
    return written


def _show_export_failure(parent, error: BaseException) -> None:
    title = tr("export_image_title")
    message = str(error).strip() or title
    if isinstance(error, OSError):
        QMessageBox.warning(parent, title, message)
        return
    QMessageBox.information(parent, title, message)


def prompt_and_export_fluorescence_image(
    parent,
    panel,
    image_record: dict | None,
    *,
    default_overlay: bool = False,
    get_save_path=None,
    confirm_overwrite=None,
) -> list[Path] | None:
    choice = choose_fluorescence_image_export(
        parent,
        has_original=panel is not None and image_record is not None,
        has_overlay=fluorescence_has_measurement_overlay(image_record),
        default_overlay=default_overlay,
        allow_separate_channels=rgb_interpretation_mode(image_record) != RGB_COMPOSITE_MODE,
    )
    if choice is None:
        return None
    source, composition = choice
    if get_save_path is None:
        path, _selected = QFileDialog.getSaveFileName(
            parent,
            tr("export_image_title"),
            suggested_export_path("fluorescence.png"),
            "PNG (*.png)",
        )
    else:
        path = get_save_path()
    if not path:
        return None
    path = apply_native_png_suffix(path)
    overwrite = confirm_overwrite
    if overwrite is None:
        overwrite = lambda targets, _parent=parent: confirm_fluorescence_export_overwrite(_parent, targets)
    try:
        written = export_fluorescence_images(
            panel,
            image_record,
            path,
            source=source,
            composition=composition,
            confirm_overwrite=overwrite,
        )
    except (ValueError, OSError) as exc:
        _show_export_failure(parent, exc)
        return None
    if not written:
        return None
    remember_file_directory(written[0])
    status_bar = getattr(parent, "statusBar", None)
    if callable(status_bar):
        from core.utils.i18n import trf

        bar = status_bar()
        if bar is not None:
            bar.showMessage(trf("exported_image_status", name=written[0].name), 4000)
    return written
