from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.image_browser import ScaleBarState
from core.utils.i18n import bind_i18n, retranslate_bound_widgets, tr
from core.utils.recent_directory import recent_directory, remember_directory
from ui.export_menu import apply_native_png_suffix
from ui.widgets.image_browser_canvas import ImageBrowserCanvas


def _core_discover(folder: str | Path, *, recursive=False):
    from core.image_browser import discover_image_files

    return discover_image_files(folder, recursive=recursive)


def _core_load(path: str | Path):
    from core.image_browser import load_browser_image

    return load_browser_image(path)


def _core_load_thumbnail(path: str | Path, max_size: QSize = QSize(176, 132)):
    from core.image_browser import load_browser_thumbnail

    return load_browser_thumbnail(path, max_size)


def _core_thumbnail(image, max_size: QSize = QSize(176, 132)):
    from core.image_browser import thumbnail_qimage

    return thumbnail_qimage(image, max_size)


def _core_to_qimage(rgb):
    from core.image_browser import rgb_to_qimage

    return rgb_to_qimage(rgb)


def _core_snapshot(
    image,
    show_scale_bar: bool,
    length_um: float,
    manual_um_per_px: float | None = None,
    *,
    scale_bar_state: ScaleBarState | None = None,
):
    from core.image_browser import render_snapshot

    return render_snapshot(
        image,
        show_scale_bar=show_scale_bar,
        length_um=length_um,
        manual_um_per_px=manual_um_per_px,
        scale_bar_state=scale_bar_state,
    )


class _BrowserSignals(QObject):
    succeeded = pyqtSignal(int, str, str, object)
    failed = pyqtSignal(int, str, str, str)


class _BrowserWorker(QRunnable):
    def __init__(self, signals: _BrowserSignals, generation: int, kind: str, path: str, work: Callable[[], Any]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._signals = signals
        self._generation = generation
        self._kind = kind
        self._path = path
        self._work = work

    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:
            self._signals.failed.emit(self._generation, self._kind, self._path, str(exc) or exc.__class__.__name__)
            return
        self._signals.succeeded.emit(self._generation, self._kind, self._path, result)


class ImageBrowserWorkspace(QWidget):
    status_changed = pyqtSignal(str)
    export_failed = pyqtSignal(str)
    send_to_fluorescence_requested = pyqtSignal(list)
    send_to_fluorescence_transfer_requested = pyqtSignal(list)
    send_to_histology_transfer_requested = pyqtSignal(str, list)
    browser_state_changed = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        loader=None,
        discover=None,
        thumbnail_loader=None,
        thumbnail=None,
        snapshot=None,
        to_qimage=None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._load = loader or _core_load
        self._discover = discover or _core_discover
        self._thumbnail_loader = thumbnail_loader
        self._thumbnail = thumbnail
        self._snapshot = snapshot or _core_snapshot
        self._to_qimage = to_qimage or _core_to_qimage
        if thread_pool is None:
            self.thread_pool = QThreadPool(self)
            self.thread_pool.setMaxThreadCount(2)
        else:
            self.thread_pool = thread_pool
        self._generation = 0
        self._folder: Path | None = None
        self._cached_path: Path | None = None
        self._cached_image = None
        self._requested_path: Path | None = None
        self._scale_bar_states: dict[str, ScaleBarState] = {}
        self._manual_calibrations: dict[str, dict | float | None] = {}
        self._resolved_calibrations: dict[str, dict[str, Any]] = {}
        self._scale_bar_controls_blocked = False
        self._restoring = False
        self._candidate_transfer_busy = False
        self._candidate_results: list[str | None] = []
        self._candidate_pending = 0
        self._candidate_errors: list[str] = []
        self._candidate_index_by_path: dict[str, int] = {}
        self._transfer_tempdir = TemporaryDirectory(prefix="sciefig-image-browser-")
        self.transfer_dir = Path(self._transfer_tempdir.name)
        self._placeholder_icon = self._make_placeholder_icon()
        self._signals = _BrowserSignals(self)
        self._signals.succeeded.connect(self._on_worker_succeeded)
        self._signals.failed.connect(self._on_worker_failed)

        self.canvas = ImageBrowserCanvas(self)
        self.canvas.scale_bar_moved.connect(self._on_scale_bar_moved)
        self.canvas.line_selected.connect(self._on_calibration_line)
        self.canvas.line_selection_cancelled.connect(lambda:self._set_draw_scale_line(False))
        self.canvas.previous_requested.connect(lambda:self._navigate_image(-1))
        self.canvas.next_requested.connect(lambda:self._navigate_image(1))
        self.canvas.set_module('viewer', tr('module_viewer_canvas'))
        self.side_panel = self._build_side_panel()
        self.retranslate()

    def _build_side_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setObjectName("ImageBrowserSidePanel")
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(6)
        self.folder_path_label = QLabel()
        self.folder_path_label.setObjectName("ImageBrowserFolderPath")
        self.folder_path_label.setWordWrap(True)
        self.choose_folder_button = bind_i18n(QPushButton(), "viewer_choose_folder")
        self.choose_folder_button.setObjectName("ImageBrowserChooseFolderButton")
        self.choose_folder_button.clicked.connect(self.choose_folder)
        folder_layout.addWidget(self.folder_path_label, 1)
        folder_layout.addWidget(self.choose_folder_button, 0)
        layout.addWidget(folder_row)
        self.include_subfolders_checkbox = bind_i18n(QCheckBox(), 'viewer_include_subfolders')
        self.include_subfolders_checkbox.toggled.connect(self._reload_folder_scope)
        layout.addWidget(self.include_subfolders_checkbox)

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setObjectName("ImageBrowserThumbnailList")
        self.thumbnail_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbnail_list.setIconSize(QSize(176, 132))
        self.thumbnail_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumbnail_list.setMovement(QListWidget.Movement.Static)
        self.thumbnail_list.setWordWrap(True)
        self.thumbnail_list.setSpacing(8)
        self.thumbnail_list.setUniformItemSizes(True)
        self.thumbnail_list.setMinimumHeight(196)
        self.thumbnail_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.thumbnail_list.currentItemChanged.connect(self._on_current_item_changed)
        self.thumbnail_list.itemSelectionChanged.connect(self._update_candidate_button)
        self.preview_splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_splitter.setObjectName("ImageBrowserPreviewSplitter")
        self.preview_splitter.setChildrenCollapsible(False)
        self.preview_splitter.setHandleWidth(8)
        self.preview_splitter.addWidget(self.thumbnail_list)
        layout.addWidget(self.preview_splitter, 1)

        # Calibration/appearance settings scroll independently instead of consuming
        # the thumbnail viewport when the sidebar is short or display scaling is high.
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(8)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName('ImageBrowserSettingsScroll')
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setMinimumHeight(160)
        self.settings_scroll.setWidget(settings)
        self.preview_splitter.addWidget(self.settings_scroll)
        self.preview_splitter.setSizes([360, 420])
        self.preview_splitter.setStretchFactor(0, 1)
        self.preview_splitter.setStretchFactor(1, 1)
        layout = settings_layout

        self.metadata_label = QLabel()
        self.metadata_label.setObjectName("ImageBrowserMetadata")
        self.metadata_label.setWordWrap(True)
        self.calibration_label = QLabel()
        self.calibration_label.setObjectName("ImageBrowserCalibration")
        self.calibration_label.setWordWrap(True)
        layout.addWidget(self.metadata_label)
        layout.addWidget(self.calibration_label)

        self.scale_bar_checkbox = bind_i18n(QCheckBox(), "viewer_add_scale_bar")
        self.scale_bar_checkbox.setObjectName("ImageBrowserScaleBar")
        self.scale_bar_checkbox.toggled.connect(self._refresh_scale_bar_preview)
        layout.addWidget(self.scale_bar_checkbox)

        self.scale_bar_drag_hint = bind_i18n(QLabel(), "viewer_scale_bar_drag_hint")
        self.scale_bar_drag_hint.setObjectName("ImageBrowserScaleBarDragHint")
        self.scale_bar_drag_hint.setWordWrap(True)
        layout.addWidget(self.scale_bar_drag_hint)

        self.length_label = bind_i18n(QLabel(), "viewer_length_um")
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setObjectName("ImageBrowserLengthUm")
        self.length_spin.setDecimals(1)
        self.length_spin.setRange(0.1, 10000.0)
        self.length_spin.setValue(50.0)
        self.length_spin.valueChanged.connect(self._refresh_scale_bar_preview)
        self.um_per_px_label = bind_i18n(QLabel(), "viewer_um_per_px")
        self.um_per_px_spin = QDoubleSpinBox()
        self.um_per_px_spin.setObjectName("ImageBrowserUmPerPx")
        self.um_per_px_spin.setDecimals(4)
        self.um_per_px_spin.setRange(0.0, 1000.0)
        self.um_per_px_spin.setSingleStep(0.01)
        self.um_per_px_spin.setValue(0.0)
        self.um_per_px_spin.valueChanged.connect(self._refresh_scale_bar_preview)
        from ui.widgets.calibration_controls import CalibrationControls
        self.draw_scale_line_button=bind_i18n(QPushButton(),'viewer_draw_scale_line')
        self.draw_scale_line_button.setCheckable(True)
        self.draw_scale_line_button.setEnabled(False)
        bind_i18n(self.draw_scale_line_button,'viewer_draw_scale_line_hint','toolTip')
        self.draw_scale_line_button.toggled.connect(self._set_draw_scale_line)
        self.calibration_controls = CalibrationControls(length_spin=self.length_spin, x_spin=self.um_per_px_spin,
            actions=(self.draw_scale_line_button,))
        self.calibration_controls.calibration_applied.connect(self._apply_calibration_preset)
        self.calibration_controls.y_spin.valueChanged.connect(self._refresh_scale_bar_preview)
        layout.addWidget(self.calibration_controls)

        from ui.widgets.calibration_controls import create_scale_appearance_group, action_grid
        self.scale_appearance_group, style_form = create_scale_appearance_group()
        style_form.setContentsMargins(0, 0, 0, 0)
        style_form.setSpacing(6)
        self.line_width_label = bind_i18n(QLabel(), "viewer_line_width")
        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setObjectName("ImageBrowserLineWidth")
        self.line_width_spin.setDecimals(1)
        self.line_width_spin.setRange(0.1, 100.0)
        self.line_width_spin.setSingleStep(0.5)
        self.line_width_spin.setValue(4.0)
        self.line_width_spin.valueChanged.connect(self._refresh_scale_bar_preview)
        style_form.addRow(self.line_width_label, self.line_width_spin)

        self.line_color_label = bind_i18n(QLabel(), "viewer_line_color")
        self.line_color_button = QPushButton()
        self.line_color_button.setObjectName("ImageBrowserLineColor")
        self.line_color_button.clicked.connect(self.choose_line_color)
        style_form.addRow(self.line_color_label, self.line_color_button)

        self.text_color_label = bind_i18n(QLabel(), "viewer_text_color")
        self.text_color_button = QPushButton()
        self.text_color_button.setObjectName("ImageBrowserTextColor")
        self.text_color_button.clicked.connect(self.choose_text_color)
        style_form.addRow(self.text_color_label, self.text_color_button)

        self.font_label = bind_i18n(QLabel(), "viewer_font")
        self.font_combo = QFontComboBox()
        self.font_combo.setObjectName("ImageBrowserFont")
        self.font_combo.currentFontChanged.connect(self._refresh_scale_bar_preview)
        style_form.addRow(self.font_label, self.font_combo)

        self.font_size_label = bind_i18n(QLabel(), "viewer_font_size_px")
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setObjectName("ImageBrowserFontSizePx")
        self.font_size_spin.setRange(1, 1000)
        self.font_size_spin.setValue(18)
        self.font_size_spin.valueChanged.connect(self._refresh_scale_bar_preview)
        style_form.addRow(self.font_size_label, self.font_size_spin)
        self.text_position_combo = QComboBox()
        self.text_position_combo.addItem(tr("above"), "above")
        self.text_position_combo.addItem(tr("below"), "below")
        self.text_position_combo.currentIndexChanged.connect(self._refresh_scale_bar_preview)
        style_form.addRow(bind_i18n(QLabel(), "scale_bar_text_position"), self.text_position_combo)
        layout.addWidget(self.scale_appearance_group)

        style_toggle_row = QHBoxLayout()
        self.bold_checkbox = bind_i18n(QCheckBox(), "viewer_bold")
        self.bold_checkbox.setObjectName("ImageBrowserScaleBarBold")
        self.bold_checkbox.toggled.connect(self._refresh_scale_bar_preview)
        self.italic_checkbox = bind_i18n(QCheckBox(), "viewer_italic")
        self.italic_checkbox.setObjectName("ImageBrowserScaleBarItalic")
        self.italic_checkbox.toggled.connect(self._refresh_scale_bar_preview)
        style_toggle_row.addWidget(self.bold_checkbox)
        style_toggle_row.addWidget(self.italic_checkbox)
        style_toggle_row.addStretch(1)
        style_form.addRow(style_toggle_row)
        actions = []
        for key, callback in (
            ("apply_scale", lambda: self.scale_bar_checkbox.setChecked(True)),
            ("scale_bar_apply_all_images", lambda: self._apply_browser_scale_to_all(True)),
            ("scale_bar_remove_current", lambda: self.scale_bar_checkbox.setChecked(False)),
            ("scale_bar_remove_all_images", lambda: self._apply_browser_scale_to_all(False)),
        ):
            button = bind_i18n(QPushButton(), key)
            button.clicked.connect(callback)
            actions.append(button)
        style_form.addRow(action_grid(actions))

        self.send_to_fluorescence_button = bind_i18n(QPushButton(), "viewer_send_to_fluorescence")
        self.send_to_fluorescence_button.setObjectName("ImageBrowserSendToFluorescenceButton")
        self.send_to_fluorescence_button.setEnabled(False)
        self.send_to_fluorescence_button.clicked.connect(self.send_selected_to_fluorescence)
        layout.addWidget(self.send_to_fluorescence_button)
        for destination in ('ihc', 'brightfield'):
            button = bind_i18n(QPushButton(), 'viewer_send_to_'+destination)
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, name=destination: self.send_selected_to_histology(name))
            setattr(self, 'send_to_'+destination+'_button', button)
            layout.addWidget(button)

        self.export_button = bind_i18n(QPushButton(), "viewer_export_png")
        self.export_button.setObjectName("ImageBrowserExportButton")
        self.export_button.clicked.connect(self.choose_export_path)
        layout.addWidget(self.export_button)

        self.error_label = QLabel()
        self.error_label.setObjectName("ImageBrowserError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self._set_color_button(self.line_color_button, "#FFFFFF")
        self._set_color_button(self.text_color_button, "#FFFFFF")
        bind_i18n(self.calibration_controls.length_label, 'viewer_length_um')
        self.calibration_controls.scale_style_provider = self._scale_preset_style
        self.calibration_controls.scale_style_applied.connect(self._apply_scale_preset_style)
        return panel

    def _scale_preset_style(self):
        return dict(label_length_um=self.length_spin.value(),
                    line_thickness=self.line_width_spin.value(),
                    line_color=str(self.line_color_button.property('colorValue')),
                    text_color=str(self.text_color_button.property('colorValue')),
                    font_family=self.font_combo.currentFont().family(), font_size=self.font_size_spin.value(),
                    bold=self.bold_checkbox.isChecked(), italic=self.italic_checkbox.isChecked(),
                    text_position=self.text_position_combo.currentData())

    def _apply_scale_preset_style(self, template):
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import QSignalBlocker
        widgets=(self.length_spin,self.line_width_spin,self.font_combo,self.font_size_spin,
                 self.bold_checkbox,self.italic_checkbox,self.text_position_combo)
        blockers=[QSignalBlocker(widget) for widget in widgets]
        self.length_spin.setValue(template['label_length_um'])
        self.line_width_spin.setValue(template['line_thickness'])
        self.font_combo.setCurrentFont(QFont(template['font_family']))
        self.font_size_spin.setValue(round(template['font_size']))
        self.bold_checkbox.setChecked(template['bold'])
        self.italic_checkbox.setChecked(template['italic'])
        self.text_position_combo.setCurrentIndex(self.text_position_combo.findData(template['text_position']))
        self.set_line_color(template['line_color'])
        self.set_text_color(template['text_color'])
        del blockers
        self._refresh_scale_bar_preview()

    def retranslate(self) -> None:
        self.canvas.retranslate()
        retranslate_bound_widgets(self)
        retranslate_bound_widgets(self.canvas)
        retranslate_bound_widgets(self.side_panel)
        self.calibration_controls.retranslate()
        from core.utils.i18n import retranslate_combo
        retranslate_combo(self.text_position_combo, {"above": tr("above"), "below": tr("below")})
        if self._cached_image is not None:
            self.canvas.title.setText(Path(self._cached_image.path).name)
            self._update_metadata_labels(self._cached_image)
        else:
            self.canvas._bind_empty_copy()
            if self.thumbnail_list.count() == 0 and self._folder is not None:
                self.error_label.setText(tr("viewer_no_images"))
        self._update_candidate_button()

    def fit_to_canvas(self) -> None:
        self.canvas.fit_to_canvas()

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.side_panel, tr("viewer_open_folder"), recent_directory())
        if folder:
            remember_directory(folder)
            self.open_folder(folder)

    def _reload_folder_scope(self, _checked):
        if self._folder is not None and not self._restoring:
            self.open_folder(self._folder, retain_states=True)

    def choose_export_path(self) -> None:
        host = self.window()
        exporter = getattr(host, "export_viewer_native_image", None)
        if callable(exporter) and host is not self:
            exporter()
            return
        path, _selected = QFileDialog.getSaveFileName(
            self.side_panel,
            tr("viewer_native_png"),
            "",
            "PNG (*.png)",
        )
        if not path:
            return
        path = apply_native_png_suffix(path)
        self.export_snapshot(path)

    def open_folder(self, folder: str | Path, *, retain_states: bool | None = None) -> None:
        if self._candidate_transfer_busy:
            self.status_changed.emit(tr("viewer_transfer_preparing"))
            return
        self._set_draw_scale_line(False)
        target = Path(folder)
        keep = bool(retain_states)
        if retain_states is None and self._folder is not None and target == self._folder:
            keep = True
        retained_states = dict(self._scale_bar_states) if keep else {}
        retained_manual = dict(self._manual_calibrations) if keep else {}
        retained_cals = dict(self._resolved_calibrations) if keep else {}
        self._generation += 1
        generation = self._generation
        self._folder = target
        self._cached_path = None
        self._cached_image = None
        self._requested_path = None
        self._scale_bar_states.clear()
        self._manual_calibrations.clear()
        self._resolved_calibrations.clear()
        self._scale_bar_states.update(retained_states)
        self._manual_calibrations.update(retained_manual)
        self._resolved_calibrations.update(retained_cals)
        self.canvas.clear_figure()
        self.canvas._bind_empty_copy()
        self.folder_path_label.setText(str(self._folder))
        self.thumbnail_list.clear()
        self.metadata_label.clear()
        self.calibration_label.clear()
        self._set_error("")
        self._update_candidate_button()
        self._sync_navigation()
        try:
            paths = list(self._discover(self._folder, recursive=True) if self.include_subfolders_checkbox.isChecked()
                         else self._discover(self._folder))
        except Exception as exc:
            self._set_error(str(exc) or tr("viewer_load_failed"))
            self.status_changed.emit(self.error_label.text())
            return
        if not paths:
            self.canvas.clear_figure()
            self.canvas._bind_empty_copy()
            self._set_error(tr("viewer_no_images"))
            self.status_changed.emit(tr("viewer_no_images"))
            self._notify_browser_state_changed()
            return
        for path in paths:
            key = str(path)
            if key in retained_states:
                self._scale_bar_states[key] = retained_states[key]
            if key in retained_manual:
                self._manual_calibrations[key] = retained_manual[key]
            if key in retained_cals:
                self._resolved_calibrations[key] = retained_cals[key]
            label = str(Path(path).relative_to(self._folder)) if self.include_subfolders_checkbox.isChecked() else Path(path).name
            item = QListWidgetItem(self._placeholder_icon, label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(str(path))
            self.thumbnail_list.addItem(item)
            self._start_job(generation, "thumbnail", Path(path))
        self.status_changed.emit(tr("viewer_status_ready"))
        self._notify_browser_state_changed()

    def select_image(self, path: str | Path) -> None:
        self._set_draw_scale_line(False)
        resolved = Path(path)
        self._requested_path = resolved
        self._sync_navigation()
        if self._cached_path == resolved and self._cached_image is not None:
            self._apply_image(self._cached_image, reset_view=False)
            return
        self._start_job(self._generation, "image", resolved)

    def _navigate_image(self,step):
        path=self._requested_path or self._cached_path
        item=self._item_for_path(Path(path)) if path is not None else None
        row=self.thumbnail_list.row(item) if item is not None else -1
        target=row+step
        if row>=0 and 0<=target<self.thumbnail_list.count():
            if target==self.thumbnail_list.currentRow():
                self.select_image(self.thumbnail_list.item(target).data(Qt.ItemDataRole.UserRole))
            else:
                self.thumbnail_list.setCurrentRow(target)

    def _sync_navigation(self):
        path=self._requested_path or self._cached_path
        item=self._item_for_path(Path(path)) if path is not None else None
        row=self.thumbnail_list.row(item) if item is not None else -1
        self.canvas.set_navigation_state(row>0,0<=row<self.thumbnail_list.count()-1,visible=row>=0)
        ready=self._cached_image is not None and (self._requested_path is None or self._requested_path==self._cached_path)
        self.draw_scale_line_button.setEnabled(ready)

    def _set_draw_scale_line(self,enabled):
        enabled=bool(enabled and self._cached_image is not None)
        previous=self.draw_scale_line_button.blockSignals(True)
        self.draw_scale_line_button.setChecked(enabled)
        self.draw_scale_line_button.blockSignals(previous)
        self.canvas.set_line_selection_enabled(enabled)
        if enabled:
            self.canvas.setFocus()
            self.status_changed.emit(tr('viewer_draw_scale_line_hint'))

    def _on_calibration_line(self,line):
        if self._cached_image is None or not self.draw_scale_line_button.isChecked():return
        from core.measurement_scale import line_length_px
        if line_length_px(line)<=0:return
        key=str(Path(self._cached_image.path))
        self._manual_calibrations[key]={
            'calibration_source':'manual_line','line_px':dict(line),'length_um':self.length_spin.value()}
        state=self._active_scale_bar_state()
        state.length_um=self.length_spin.value()
        state.um_per_px=self._metadata_um_per_px(self._cached_image)
        state.center_x_px=(line['x1']+line['x2'])/2
        state.line_y_px=(line['y1']+line['y2'])/2
        state.visible=True
        self._set_draw_scale_line(False)
        self.canvas.image_label.set_source_line({})
        self._sync_scale_bar_controls(state,self._cached_image)
        self.canvas.set_scale_bar_state(state)
        self._set_error('')
        self._notify_browser_state_changed()

    def scale_bar_state_for(self, path: str | Path) -> ScaleBarState:
        """Return the mutable session state associated with one source path."""
        key = str(Path(path))
        state = self._scale_bar_states.get(key)
        if state is None:
            state = ScaleBarState()
            self._scale_bar_states[key] = state
        return state

    def export_snapshot(self, path: str | Path) -> bool:
        if self._cached_image is None:
            message = tr("viewer_export_failed")
            self._set_error(message)
            self.export_failed.emit(message)
            return False
        state = self.scale_bar_state_for(self._cached_path or self._cached_image.path)
        try:
            snapshot = self._snapshot(
                self._cached_image,
                show_scale_bar=bool(state.visible),
                length_um=float(state.length_um),
                manual_um_per_px=None,
                scale_bar_state=state,
            )
        except Exception as exc:
            message = str(exc) or tr("viewer_scale_bar_uncalibrated")
            self._set_error(message)
            self.export_failed.emit(message)
            return False
        target = str(path)
        if not snapshot.save(target, "PNG"):
            message = tr("viewer_export_failed")
            self._set_error(message)
            self.export_failed.emit(message)
            return False
        self._set_error("")
        self.status_changed.emit(Path(target).name)
        return True

    def _start_job(
        self,
        generation: int,
        kind: str,
        path: Path,
        work_override: Callable[[], Any] | None = None,
    ) -> None:
        path_text = str(path)

        def work():
            if work_override is not None:
                return work_override()
            if kind == "thumbnail":
                if self._thumbnail_loader is not None:
                    return self._thumbnail_loader(path)
                if self._thumbnail is not None:
                    return self._thumbnail(self._load(path))
                return _core_load_thumbnail(path)
            return self._load(path)

        worker = _BrowserWorker(self._signals, generation, kind, path_text, work)
        self.thread_pool.start(worker, 1 if kind == "image" else 0)

    def _on_current_item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if current is None:
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        if path:
            self.select_image(path)

    def _apply_browser_scale_to_all(self, visible):
        current = self._active_scale_bar_state()
        if current is None:
            return
        fields = ("length_um", "line_color", "text_color", "line_thickness_px",
                  "font_family", "font_size_px", "bold", "italic", "text_position")
        for index in range(self.thumbnail_list.count()):
            state = self.scale_bar_state_for(self.thumbnail_list.item(index).data(Qt.ItemDataRole.UserRole))
            if visible:
                for field in fields:
                    setattr(state, field, getattr(current, field))
            state.visible = bool(visible)
        self.scale_bar_checkbox.setChecked(bool(visible))
        self._refresh_scale_bar_preview()
        self._notify_browser_state_changed()

    def _active_scale_bar_state(self) -> ScaleBarState | None:
        if self._cached_image is None:
            return None
        return self.scale_bar_state_for(self._cached_path or self._cached_image.path)

    def _metadata_um_per_px(self, image) -> float | None:
        resolved = self._cache_resolved_calibration(image)
        if not resolved.get("calibrated"):
            return None
        value = float(resolved.get("um_per_px") or 0.0)
        return value if value > 0 else None

    def _saved_manual_scale(self, path_key: str) -> dict[str, Any] | None:
        manual = self._manual_calibrations.get(path_key)
        if isinstance(manual, dict):
            return deepcopy(manual)
        try:
            manual_value = float(manual) if manual not in (None, "") else 0.0
        except (TypeError, ValueError):
            manual_value = 0.0
        if manual_value > 0:
            return {"um_per_px": manual_value, "calibration_source": "manual"}
        return None

    def _cache_resolved_calibration(self, image) -> dict[str, Any]:
        from core.measurement_scale import resolve_calibration

        path_key = str(Path(image.path))
        metadata = image.metadata if isinstance(getattr(image, "metadata", None), dict) else {}
        manual = self._saved_manual_scale(path_key) or {}
        resolved = dict(deepcopy(manual),**resolve_calibration(dict(metadata),manual))
        for name in ('preset_id', 'preset_name'):
            if name in manual:
                resolved[name] = manual[name]
        self._resolved_calibrations[path_key] = resolved
        return resolved

    def _notify_browser_state_changed(self) -> None:
        if self._restoring or self._scale_bar_controls_blocked:
            return
        self.browser_state_changed.emit()

    def _sync_scale_bar_controls(self, state: ScaleBarState, image) -> None:
        self._scale_bar_controls_blocked = True
        try:
            self.length_spin.setValue(float(state.length_um))
            self.calibration_controls.set_scale(dict(self._cache_resolved_calibration(image), length_um=state.length_um), metadata=image.metadata)
            self.line_width_spin.setValue(float(state.line_thickness_px))
            self.font_size_spin.setValue(int(round(float(state.font_size_px))))
            self.bold_checkbox.setChecked(bool(state.bold))
            self.italic_checkbox.setChecked(bool(state.italic))
            self.text_position_combo.setCurrentIndex(1 if state.text_position == "below" else 0)
            self.font_combo.setCurrentFont(QFont(str(state.font_family or "Arial")))
            self._set_color_button(self.line_color_button, state.line_color)
            self._set_color_button(self.text_color_button, state.text_color)
            self.scale_bar_checkbox.setChecked(bool(state.visible))
        finally:
            self._scale_bar_controls_blocked = False

    def _refresh_scale_bar_preview(self, *_args) -> None:
        if self._scale_bar_controls_blocked:
            return
        state = self._active_scale_bar_state()
        if state is None or self._cached_image is None:
            return

        path_key = str(Path(self._cached_image.path))
        current = self._cache_resolved_calibration(self._cached_image)
        from core.measurement_scale import merge_measurement_scale_updates
        merged = merge_measurement_scale_updates(current, self.calibration_controls.updates_for(current))
        if merged.get('calibration_source') in {'manual','manual_line'} and float(merged.get('um_per_px', 0)) > 0:
            self._manual_calibrations[path_key] = (float(merged['um_per_px']) if merged.get('calibration_source')=='manual' and not merged.get('preset_id') and
                merged.get('um_per_px_x') == merged.get('um_per_px_y') else merged)
        else:
            self._manual_calibrations[path_key] = None
        metadata_value = self._metadata_um_per_px(self._cached_image)
        state.length_um = float(self.length_spin.value())
        state.um_per_px = metadata_value
        state.line_thickness_px = float(self.line_width_spin.value())
        state.font_family = self.font_combo.currentFont().family()
        state.font_size_px = float(self.font_size_spin.value())
        state.bold = bool(self.bold_checkbox.isChecked())
        state.italic = bool(self.italic_checkbox.isChecked())
        state.text_position = str(self.text_position_combo.currentData())
        state.visible = bool(self.scale_bar_checkbox.isChecked() and state.um_per_px is not None)

        if self.scale_bar_checkbox.isChecked() and state.um_per_px is None:
            self._set_error(tr("viewer_scale_bar_uncalibrated"))
        else:
            self._set_error("")
        self._cache_resolved_calibration(self._cached_image)
        if merged.get('calibration_source')=='manual_line':
            self._sync_scale_bar_controls(state,self._cached_image)
        self.canvas.set_scale_bar_state(state)
        self._notify_browser_state_changed()

    def _apply_calibration_preset(self, updates) -> None:
        if self._cached_image is None:
            return
        path_key = str(Path(self._cached_image.path))
        self._manual_calibrations[path_key] = deepcopy(updates)
        self._cache_resolved_calibration(self._cached_image)
        self._refresh_scale_bar_preview()

    def _on_scale_bar_moved(self, moved_state: ScaleBarState) -> None:
        state = self._active_scale_bar_state()
        if state is None:
            return
        state.center_x_px = float(moved_state.center_x_px)
        state.line_y_px = float(moved_state.line_y_px)
        self.canvas.set_scale_bar_state(state)
        self._notify_browser_state_changed()

    def set_line_color(self, value: str | QColor) -> None:
        state = self._active_scale_bar_state()
        if state is None:
            return
        color = QColor(value)
        if not color.isValid():
            return
        state.line_color = color.name().upper()
        self._set_color_button(self.line_color_button, state.line_color)
        self.canvas.set_scale_bar_state(state)
        self._notify_browser_state_changed()

    def set_text_color(self, value: str | QColor) -> None:
        state = self._active_scale_bar_state()
        if state is None:
            return
        color = QColor(value)
        if not color.isValid():
            return
        state.text_color = color.name().upper()
        self._set_color_button(self.text_color_button, state.text_color)
        self.canvas.set_scale_bar_state(state)
        self._notify_browser_state_changed()

    # Explicit aliases keep the color seam discoverable to callers that use
    # the longer scale-bar terminology.
    set_scale_bar_line_color = set_line_color
    set_scale_bar_text_color = set_text_color

    def choose_line_color(self) -> None:
        state = self._active_scale_bar_state()
        initial = QColor(state.line_color if state is not None else "#FFFFFF")
        color = QColorDialog.getColor(initial, self.side_panel, tr("viewer_line_color"))
        if color.isValid():
            self.set_line_color(color)

    def choose_text_color(self) -> None:
        state = self._active_scale_bar_state()
        initial = QColor(state.text_color if state is not None else "#FFFFFF")
        color = QColorDialog.getColor(initial, self.side_panel, tr("viewer_text_color"))
        if color.isValid():
            self.set_text_color(color)

    @staticmethod
    def _set_color_button(button: QPushButton, value: str) -> None:
        button.setProperty("colorValue", str(value))
        button.setStyleSheet(f"background-color: {value};")

    def _update_candidate_button(self) -> None:
        if not hasattr(self, "send_to_fluorescence_button"):
            return
        enabled = bool(self.thumbnail_list.selectedItems()) and not self._candidate_transfer_busy
        self.send_to_fluorescence_button.setEnabled(enabled)
        for name in ('ihc', 'brightfield'):
            getattr(self, 'send_to_'+name+'_button').setEnabled(enabled)

    def serialize_browser_scale_states(self) -> dict[str, Any]:
        images: dict[str, Any] = {}
        for key, state in self._scale_bar_states.items():
            images[str(key)] = {
                "display": {
                    "visible": bool(state.visible),
                    "length_um": float(state.length_um),
                    "um_per_px": float(state.um_per_px) if state.um_per_px not in (None, "") else 0.0,
                    "center_x_px": float(state.center_x_px),
                    "line_y_px": float(state.line_y_px),
                    "line_color": str(state.line_color),
                    "text_color": str(state.text_color),
                    "line_thickness_px": float(state.line_thickness_px),
                    "font_family": str(state.font_family),
                    "font_size_px": float(state.font_size_px),
                    "bold": bool(state.bold),
                    "italic": bool(state.italic),
                    "text_position": state.text_position,
                },
                "manual_um_per_px": self._manual_calibrations.get(str(key)),
                "calibration": deepcopy(self._resolved_calibrations[str(key)])
                if str(key) in self._resolved_calibrations
                else None,
            }
        return deepcopy({"folder": str(self._folder) if self._folder is not None else "", "images": images,
                         "include_subfolders": self.include_subfolders_checkbox.isChecked()})

    def restore_browser_scale_states(self, payload: dict[str, Any] | None) -> None:
        data = deepcopy(payload) if isinstance(payload, dict) else {}
        images = data.get("images") if isinstance(data.get("images"), dict) else {}
        self._restoring = True
        try:
            self.include_subfolders_checkbox.setChecked(bool(data.get('include_subfolders', False)))
            self._restore_browser_scale_maps(data, images)
        finally:
            self._restoring = False

    def _restore_browser_scale_maps(self, data: dict[str, Any], images: dict[str, Any]) -> None:
        self._scale_bar_states.clear()
        self._manual_calibrations.clear()
        self._resolved_calibrations.clear()
        for key, item in images.items():
            if not isinstance(item, dict):
                continue
            display = item.get("display") if isinstance(item.get("display"), dict) else {}
            try:
                um_value = float(display.get("um_per_px")) if display.get("um_per_px") not in (None, "") else None
            except (TypeError, ValueError):
                um_value = None
            state = ScaleBarState(
                visible=bool(display.get("visible", False)),
                length_um=float(display.get("length_um", 50.0) or 50.0),
                um_per_px=um_value if um_value is not None and um_value > 0 else None,
                center_x_px=float(display.get("center_x_px", 0.0) or 0.0),
                line_y_px=float(display.get("line_y_px", 0.0) or 0.0),
                line_color=str(display.get("line_color", "#FFFFFF")),
                text_color=str(display.get("text_color", "#FFFFFF")),
                line_thickness_px=float(display.get("line_thickness_px", 4.0) or 4.0),
                font_family=str(display.get("font_family", "Arial") or "Arial"),
                font_size_px=float(display.get("font_size_px", 18.0) or 18.0),
                bold=bool(display.get("bold", False)),
                italic=bool(display.get("italic", False)),
                text_position=str(display.get("text_position", "above")),
            )
            self._scale_bar_states[str(key)] = state
            manual = item.get("manual_um_per_px")
            try:
                manual_value = float(manual) if manual not in (None, "") else None
            except (TypeError, ValueError):
                manual_value = None
            self._manual_calibrations[str(key)] = manual_value if manual_value is not None and manual_value > 0 else None
            if isinstance(manual, dict):
                self._manual_calibrations[str(key)] = deepcopy(manual)
            calibration = item.get("calibration")
            if isinstance(calibration, dict):
                self._resolved_calibrations[str(key)] = deepcopy(calibration)
        folder_text = str(data.get("folder") or "").strip()
        if folder_text:
            folder_path = Path(folder_text)
            if folder_path.is_dir():
                self.open_folder(folder_path, retain_states=True)
            else:
                self._folder = folder_path
                self.folder_path_label.setText(folder_text)

    def clear_browser_workspace_state(self) -> None:
        self._set_draw_scale_line(False)
        self._restoring = True
        try:
            self._generation += 1
            self._folder = None
            self._cached_path = None
            self._cached_image = None
            self._requested_path = None
            self._scale_bar_states.clear()
            self._manual_calibrations.clear()
            self._resolved_calibrations.clear()
            self.canvas.clear_figure()
            self.canvas._bind_empty_copy()
            self.folder_path_label.clear()
            self.thumbnail_list.clear()
            self.metadata_label.clear()
            self.calibration_label.clear()
            self._set_error("")
            self._update_candidate_button()
            self._sync_navigation()
        finally:
            self._restoring = False

    def _browser_transfer_payload(self, path: Path) -> dict[str, Any]:
        from core.measurement_scale import resolve_calibration

        path_key = str(path)
        state = self._scale_bar_states.get(path_key) or ScaleBarState()
        saved = self._saved_manual_scale(path_key)
        cached = self._resolved_calibrations.get(path_key)
        calibration = None
        if saved is not None:
            calibration = deepcopy(cached) if isinstance(cached, dict) and cached.get("calibrated") else deepcopy(resolve_calibration({}, saved))
        elif isinstance(cached, dict) and cached.get("calibrated"):
            calibration = deepcopy(cached)
        display = {
            "visible": bool(state.visible),
            "length_um": float(state.length_um),
            "um_per_px": float((calibration or {}).get("um_per_px") or 0.0),
            "center_x_px": float(state.center_x_px),
            "line_y_px": float(state.line_y_px),
            "line_color": str(state.line_color),
            "text_color": str(state.text_color),
            "line_thickness_px": float(state.line_thickness_px),
            "font_family": str(state.font_family),
            "font_size_px": float(state.font_size_px),
            "bold": bool(state.bold),
            "italic": bool(state.italic),
        }
        payload: dict[str, Any] = {"path": path_key, "display": display}
        if calibration is not None:
            payload["calibration"] = calibration
        return payload

    def send_selected_to_histology(self, destination):
        if self._candidate_transfer_busy or destination not in ('ihc', 'brightfield'):
            return
        paths = [Path(item.data(Qt.ItemDataRole.UserRole)) for item in self.thumbnail_list.selectedItems()
                 if item.data(Qt.ItemDataRole.UserRole)]
        if paths:
            self.send_to_histology_transfer_requested.emit(destination, [self._browser_transfer_payload(p) for p in paths])

    def send_selected_to_fluorescence(self) -> None:
        if self._candidate_transfer_busy:
            return
        selected = self.thumbnail_list.selectedItems()
        if not selected:
            self._update_candidate_button()
            return
        paths = [Path(item.data(Qt.ItemDataRole.UserRole)) for item in selected if item.data(Qt.ItemDataRole.UserRole)]
        if not paths:
            self._update_candidate_button()
            return

        self._candidate_transfer_busy = True
        self._candidate_results = [str(path) for path in paths]
        self._candidate_pending = 0
        self._candidate_errors = []
        self._candidate_index_by_path = {str(path): index for index, path in enumerate(paths)}
        self.send_to_fluorescence_button.setEnabled(False)
        self.choose_folder_button.setEnabled(False)
        self.status_changed.emit(tr("viewer_transfer_preparing"))
        try:
            payloads = [self._browser_transfer_payload(path) for path in paths]
            ordered = [str(path) for path in paths]
            self.send_to_fluorescence_requested.emit(ordered)
            self.send_to_fluorescence_transfer_requested.emit(payloads)
            self._set_error("")
            self.status_changed.emit(tr("viewer_transfer_ready"))
        except Exception as exc:
            message = tr("viewer_transfer_failed").format(error=str(exc) or exc.__class__.__name__)
            self._set_error(message)
            self.status_changed.emit(message)
        finally:
            self._candidate_transfer_busy = False
            self.choose_folder_button.setEnabled(True)
            self._update_candidate_button()

    def _finish_candidate_transfer(self) -> None:
        if self._candidate_pending:
            return
        self._candidate_transfer_busy = False
        self.choose_folder_button.setEnabled(True)
        self._update_candidate_button()
        if self._candidate_errors:
            message = tr("viewer_transfer_failed").format(error=self._candidate_errors[0])
            self._set_error(message)
            self.status_changed.emit(message)
            return
        ordered = [path for path in self._candidate_results if path is not None]
        self.send_to_fluorescence_requested.emit(ordered)
        self.status_changed.emit(tr("viewer_transfer_ready"))

    def _on_worker_succeeded(self, generation: int, kind: str, path: str, result: object) -> None:
        if generation != self._generation:
            return
        resolved = Path(path)
        if kind == "candidate":
            index = self._candidate_index_by_path.get(str(resolved))
            if index is None:
                return
            self._candidate_results[index] = str(result)
            self._candidate_pending = max(0, self._candidate_pending - 1)
            self._finish_candidate_transfer()
            return
        if kind == "thumbnail":
            item = self._item_for_path(resolved)
            if item is None:
                return
            pixmap = QPixmap.fromImage(result)
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap))
            return
        if self._requested_path is not None and resolved != self._requested_path:
            return
        self._cached_path = resolved
        self._cached_image = result
        self._apply_image(result)

    def _on_worker_failed(self, generation: int, kind: str, path: str, message: str) -> None:
        if generation != self._generation:
            return
        if kind == "candidate":
            self._candidate_errors.append(message or tr("viewer_transfer_failed"))
            self._candidate_pending = max(0, self._candidate_pending - 1)
            self._finish_candidate_transfer()
            return
        if kind == "thumbnail":
            item = self._item_for_path(Path(path))
            if item is not None:
                item.setIcon(self._placeholder_icon)
            return
        if self._requested_path is not None and Path(path) != self._requested_path:
            return
        self._set_error(message or tr("viewer_load_failed"))
        self.status_changed.emit(self.error_label.text())

    def _apply_image(self, image, *, reset_view=True) -> None:
        qimage = self._to_qimage(image.rgb)
        pixmap = QPixmap.fromImage(qimage)
        state = self.scale_bar_state_for(image.path)
        if self._manual_calibrations.get(str(Path(image.path))) in (None,):
            state.um_per_px = self._metadata_um_per_px(image)
        else:
            self._cache_resolved_calibration(image)
        self.canvas.set_pixmap(pixmap, title=Path(image.path).name)
        self._sync_scale_bar_controls(state, image)
        self.canvas.set_scale_bar_state(state)
        if reset_view:
            self.canvas.fit_to_canvas()
        self.canvas.set_scale_bar_state(state)
        self._update_metadata_labels(image)
        self._sync_navigation()
        self._set_error("")
        width, height = image.size
        self.status_changed.emit(
            tr("viewer_status_loaded").format(name=Path(image.path).name, width=width, height=height)
        )

    def _update_metadata_labels(self, image) -> None:
        width, height = image.size
        self.metadata_label.setText(f"{Path(image.path).name}  {width} × {height}")
        pixel_size = image.metadata.get("pixel_size_um") if isinstance(image.metadata, dict) else None
        try:
            value = float(pixel_size)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            self.calibration_label.setText(f"{value:g} µm/px")
        else:
            self.calibration_label.setText(tr("viewer_use_file_metadata"))

    def _item_for_path(self, path: Path) -> QListWidgetItem | None:
        target = str(path)
        for index in range(self.thumbnail_list.count()):
            item = self.thumbnail_list.item(index)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == target:
                return item
        return None

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def closeEvent(self, event) -> None:
        try:
            self._transfer_tempdir.cleanup()
        finally:
            super().closeEvent(event)

    @staticmethod
    def _make_placeholder_icon() -> QIcon:
        pixmap = QPixmap(176, 132)
        pixmap.fill(QColor("#3a342f"))
        return QIcon(pixmap)
