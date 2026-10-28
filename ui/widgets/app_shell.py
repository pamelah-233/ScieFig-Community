from __future__ import annotations

from collections.abc import Callable
import sys

from PyQt6.QtCore import Qt, QSize, QTimer, QEvent, pyqtSignal, QVariantAnimation, QEasingCurve
from PyQt6 import sip
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from core.utils.i18n import tr


DEFAULT_RAIL_WIDTH = 68 if sys.platform in {'darwin', 'win32'} else 76
MIN_RAIL_WIDTH = DEFAULT_RAIL_WIDTH
MAX_RAIL_WIDTH = 220
MIN_RIGHT_WIDTH = 240
DEFAULT_RIGHT_WIDTH = 300 if sys.platform in {'darwin', 'win32'} else 360
MAX_RIGHT_WIDTH = 640


class V9AppShell(QWidget):
    """Application frame for the V9 preview shell.

    This class owns only shell chrome and splitter state. Module widgets remain
    owned by MainWindow so existing scientific and composer behavior stays put.
    """

    stage_title_changed = pyqtSignal()

    def __init__(
        self,
        module_selector: QWidget,
        stage_widget: QWidget,
        right_widget: QWidget,
        actions: dict[str, Callable[[], None]],
        preset_names: list[str] | tuple[str, ...],
        on_preset_changed: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("V9AppShell")
        self._module_selector = module_selector
        self._rail_collapsed = False
        self._right_collapsed = False
        self._right_panel_available = True
        self._stage_header_visible = True
        self._saved_rail_width = DEFAULT_RAIL_WIDTH
        self._saved_right_width = DEFAULT_RIGHT_WIDTH
        self._clamping_splitter = False
        self._splitter_animation = None
        self._action_buttons: dict[str, QPushButton] = {}
        self._stage_buttons: list[QPushButton] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar(actions, preset_names, on_preset_changed))
        layout.addWidget(self._build_body(module_selector, stage_widget, right_widget), 1)
        self._sync_control_visibility()
        if sys.platform in {'darwin', 'win32'}:
            from ui.widgets.studio_icons import set_studio_icon
            self.rail_panel.layout().removeWidget(self.rail_hide_button)
            self.global_toolbar.layout().insertWidget(0, self.rail_hide_button)
            self.right_panel.findChild(QFrame, 'V9RightPanelHeader').layout().removeWidget(self.right_hide_button)
            self.global_toolbar.layout().insertWidget(self.global_toolbar.layout().count()-1, self.right_hide_button)
            self.rail_hide_button.setFixedSize(28, 28)
            self.right_hide_button.setFixedSize(28, 28)
            for button, icon in ((self.rail_hide_button, 'panel_left'),
                                 (self.rail_reopen_button, 'panel_left_open'),
                                 (self.right_show_button, 'panel_right_open'),
                                 (self.right_hide_button, 'panel_right')):
                set_studio_icon(button, icon, icon_only=True)
            self._toggle_slots = []
            for hide, show in ((self.rail_hide_button, self.rail_reopen_button),
                               (self.right_hide_button, self.right_show_button)):
                toolbar_layout = self.global_toolbar.layout()
                index = min(toolbar_layout.indexOf(hide), toolbar_layout.indexOf(show))
                toolbar_layout.removeWidget(hide)
                toolbar_layout.removeWidget(show)
                slot = QWidget()
                slot.setFixedSize(28, 28)
                stack = QStackedLayout(slot)
                stack.setContentsMargins(0, 0, 0, 0)
                stack.addWidget(hide)
                stack.addWidget(show)
                toolbar_layout.insertWidget(index, slot)
                self._toggle_slots.append((slot, stack, hide, show))
            self._sync_control_visibility()
            self._toolbar_compact = False
            self.global_toolbar.installEventFilter(self)
            QTimer.singleShot(0, self._update_toolbar_density)
        QTimer.singleShot(0, self._apply_splitter_sizes)

    def _build_toolbar(
        self,
        actions: dict[str, Callable[[], None]],
        preset_names: list[str] | tuple[str, ...],
        on_preset_changed: Callable[[str], None] | None,
    ) -> QWidget:
        toolbar = QFrame()
        self.global_toolbar = toolbar
        toolbar.setObjectName("V9GlobalToolbar")
        toolbar.setFixedHeight(42 if sys.platform in {'darwin', 'win32'} else 38)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        for action_name, label_key, object_name in (
            ("new", "v9_new", "V9NewButton"),
            ("open", "v9_open", "V9OpenButton"),
            ("save", "v9_save", "V9SaveButton"),
            ("save_as", "v9_save_as", "V9SaveAsButton"),
        ):
            button = self._action_button(tr(label_key), object_name, actions.get(action_name))
            button.setProperty("i18nKey", label_key)
            self._action_buttons[action_name] = button
            layout.addWidget(button)

        layout.addStretch(0 if sys.platform in {'darwin', 'win32'} else 1)

        self.toolbar_hint = QLabel(tr("v9_toolbar_hint"))
        self.toolbar_hint.setObjectName("V9ToolbarHint")
        self.toolbar_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toolbar_hint.setSizePolicy(QSizePolicy.Policy.Ignored if sys.platform in {'darwin', 'win32'} else QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Preferred)
        layout.addWidget(self.toolbar_hint, 2)

        layout.addStretch(0 if sys.platform in {'darwin', 'win32'} else 1)

        if sys.platform in {'darwin', 'win32'}:
            from core.image_analysis.cellpose_backend import cellpose_device_preference
            from ui.controllers.model_device_controller import ModelDeviceController
            self.cellpose_device_combo = QComboBox()
            self.cellpose_device_combo.setObjectName('V9CellposeDeviceCombo')
            self.cellpose_device_combo.addItem(tr('cellpose_device_cpu'), 'cpu')
            self.cellpose_device_combo.addItem(tr('cellpose_device_gpu'), 'mps' if sys.platform == 'darwin' else 'cuda')
            self.cellpose_device_combo.setCurrentIndex(self.cellpose_device_combo.findData(cellpose_device_preference()))
            self.cellpose_device_combo.setFixedWidth(206)
            self.cellpose_device_combo.setToolTip(tr('cellpose_device_hint'))
            self.cellpose_device_combo.setAccessibleName(tr('cellpose_device_label'))
            self.model_device_controller = ModelDeviceController(self.cellpose_device_combo, self)
            layout.addWidget(self.cellpose_device_combo)

        self.preset_label = QLabel(tr("v9_style"))
        self.preset_label.setObjectName("V9ToolbarLabel")
        layout.addWidget(self.preset_label)

        self.style_preset_combo = QComboBox()
        self.style_preset_combo.setObjectName("V9StylePresetCombo")
        self.style_preset_combo.addItems(list(preset_names))
        if self.style_preset_combo.findText("Nature") >= 0:
            self.style_preset_combo.setCurrentText("Nature")
        if on_preset_changed is not None:
            self.style_preset_combo.currentTextChanged.connect(on_preset_changed)
        layout.addWidget(self.style_preset_combo)

        export_button = self._action_button(tr("export"), "V9ExportButton", actions.get("export"))
        export_button.setObjectName("V9ExportButton")
        export_button.setProperty("i18nKey", "export")
        export_button.setProperty("primary", True)
        self._action_buttons["export"] = export_button
        layout.addWidget(export_button)
        return toolbar

    def _action_button(self, label: str, object_name: str, action: Callable[[], None] | None) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName(object_name)
        if sys.platform in {'darwin', 'win32'}:
            from ui.widgets.studio_icons import set_studio_icon
            icons = {'V9NewButton': 'new', 'V9OpenButton': 'open', 'V9SaveButton': 'save',
                     'V9SaveAsButton': 'save_as', 'V9ExportButton': 'export'}
            if object_name in icons:
                set_studio_icon(button, icons[object_name], color='#201a16' if object_name == 'V9ExportButton' else '#c4bbb0')
        if action is not None:
            button.clicked.connect(lambda _checked=False, callback=action: callback())
        return button

    def _update_toolbar_density(self) -> None:
        if sys.platform not in {'darwin', 'win32'} or not hasattr(self, '_toolbar_compact'):
            return
        layout = self.global_toolbar.layout()
        margins = layout.contentsMargins()
        required = margins.left() + margins.right()
        widgets = [layout.itemAt(i).widget() for i in range(layout.count())
                   if layout.itemAt(i).widget() is not None]
        for widget in widgets:
            if widget in self._action_buttons.values():
                label = tr(widget.property('i18nKey'))
                required += widget.fontMetrics().horizontalAdvance(label) + widget.iconSize().width() + 30
            else:
                required += widget.sizeHint().width()
        required += layout.spacing() * (layout.count() - 1)
        compact = self.global_toolbar.width() < required + (16 if self._toolbar_compact else 0)
        self._toolbar_compact = compact
        self.toolbar_hint.setVisible(not compact)
        for button in self._action_buttons.values():
            label = tr(button.property('i18nKey'))
            button.setProperty('sciefigAutoButtonTooltip', False)
            if button.isEnabled() or not button.toolTip():
                button.setToolTip(label)
            button.setAccessibleName(label)
            if button.property('toolbarIconOnly') != compact:
                button.setProperty('toolbarIconOnly', compact)
                button.style().unpolish(button)
                button.style().polish(button)
            text = '' if compact else label
            if button.text() != text:
                button.setText(text)
            width = 34 if compact else button.sizeHint().width()
            button.setMaximumWidth(width)
            button.setMinimumWidth(width)

    def _build_body(self, module_selector: QWidget, stage_widget: QWidget, right_widget: QWidget) -> QWidget:
        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.setObjectName("V9BodySplitter")
        self.body_splitter.setHandleWidth(5)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.splitterMoved.connect(self._on_splitter_moved)

        self.rail_panel = QFrame()
        self.rail_panel.setObjectName("V9Rail")
        self.rail_panel.setMinimumWidth(MIN_RAIL_WIDTH)
        self.rail_panel.setMaximumWidth(MAX_RAIL_WIDTH)
        rail_layout = QVBoxLayout(self.rail_panel)
        rail_layout.setContentsMargins(1, 6, 1, 6)
        rail_layout.setSpacing(6)
        self.rail_hide_button = QPushButton("<")
        self.rail_hide_button.setObjectName("V9RailHideButton")
        self.rail_hide_button.setToolTip(tr("v9_hide_navigation"))
        self.rail_hide_button.setFixedSize(QSize(30, 28))
        self.rail_hide_button.clicked.connect(self.collapse_rail)
        rail_layout.addWidget(self.rail_hide_button)
        rail_layout.addWidget(module_selector, 1)
        rail_layout.addStretch(1)

        self.stage_area = QFrame()
        self.stage_area.setObjectName("V9StageArea")
        self.stage_area.setMinimumWidth(0)
        self.stage_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        stage_layout = QVBoxLayout(self.stage_area)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)
        self.stagebar = QFrame()
        self.stagebar.setObjectName("V9Stagebar")
        self.stagebar.setMinimumWidth(0)
        self.stagebar.setFixedHeight(46)
        stagebar_layout = QHBoxLayout(self.stagebar)
        stagebar_layout.setContentsMargins(10, 7, 10, 7)
        stagebar_layout.setSpacing(8)
        self.rail_reopen_button = QPushButton(">")
        self.rail_reopen_button.setObjectName("V9RailReopenButton")
        self.rail_reopen_button.setToolTip(tr("v9_show_navigation"))
        self.rail_reopen_button.setFixedSize(QSize(32, 32))
        self.rail_reopen_button.clicked.connect(self.restore_rail)
        stagebar_layout.addWidget(self.rail_reopen_button)
        self.stage_title_label = QLabel(tr("v9_stage_qpcr"))
        self.stage_title_label.setObjectName("V9StageTitle")
        self.stage_title_label.setMinimumWidth(0)
        self.stage_title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        stagebar_layout.addWidget(self.stage_title_label, 1)
        self.stage_tools_container = QWidget()
        self.stage_tools_container.setObjectName("V9StageTools")
        self.stage_tools_container.setMinimumWidth(0)
        self.stage_tools_container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.stage_tools_layout = QHBoxLayout(self.stage_tools_container)
        self.stage_tools_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_tools_layout.setSpacing(6)
        stagebar_layout.addWidget(self.stage_tools_container)
        self.right_show_button = QPushButton("<")
        self.right_show_button.setObjectName("V9RightPanelShowButton")
        self.right_show_button.setToolTip(tr("v9_show_workflow"))
        self.right_show_button.setFixedSize(QSize(32, 32))
        self.right_show_button.clicked.connect(self.restore_right_panel)
        stagebar_layout.addWidget(self.right_show_button)
        # Panel restore buttons must not require a title-only canvas row.
        stagebar_layout.removeWidget(self.rail_reopen_button)
        stagebar_layout.removeWidget(self.right_show_button)
        self.global_toolbar.layout().insertWidget(0,self.rail_reopen_button)
        self.global_toolbar.layout().insertWidget(self.global_toolbar.layout().count()-1,self.right_show_button)
        self.rail_reopen_button.setFixedSize(28,28)
        self.right_show_button.setFixedSize(28,28)
        stage_layout.addWidget(self.stagebar)
        stage_layout.addWidget(stage_widget, 1)

        self.right_panel = QFrame()
        self.right_panel.setObjectName("V9RightPanel")
        self.right_panel.setMinimumWidth(MIN_RIGHT_WIDTH)
        self.right_panel.setMaximumWidth(MAX_RIGHT_WIDTH)
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("V9RightPanelHeader")
        header.setFixedHeight(34 if sys.platform in {'darwin', 'win32'} else 44)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4 if sys.platform in {'darwin', 'win32'} else 8, 10, 4 if sys.platform in {'darwin', 'win32'} else 8)
        header_layout.setSpacing(8)
        self.right_panel_title = QLabel(tr("style_inspector"))
        self.right_panel_title.setObjectName("V9RightPanelTitle")
        header_layout.addWidget(self.right_panel_title, 1)
        hide_button = QPushButton(">")
        self.right_hide_button = hide_button
        hide_button.setObjectName("V9RightPanelHideButton")
        hide_button.setToolTip(tr("v9_hide_workflow"))
        hide_button.setFixedSize(QSize(26, 26) if sys.platform in {'darwin', 'win32'} else QSize(32, 32))
        hide_button.clicked.connect(self.collapse_right_panel)
        header_layout.addWidget(hide_button)
        self.right_layout.addWidget(header)
        self._right_widget: QWidget | None = None
        self.set_right_widget(right_widget, tr("style_inspector"))

        self.body_splitter.addWidget(self.rail_panel)
        self.body_splitter.addWidget(self.stage_area)
        self.body_splitter.addWidget(self.right_panel)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setStretchFactor(2, 0)
        self.body_splitter.setSizes([DEFAULT_RAIL_WIDTH, 860, self._saved_right_width])
        self._sync_control_visibility()
        return self.body_splitter

    def set_stage_title(self, title: str) -> None:
        self.stage_title_label.setText(title)
        self.stage_title_changed.emit()

    def set_stage_header_visible(self, visible: bool) -> None:
        self._stage_header_visible = bool(visible)
        self._sync_control_visibility()

    def set_workflow_navigation(self, widget: QWidget) -> None:
        self.stagebar.setFixedHeight(38)
        self.stagebar.layout().setContentsMargins(10, 3, 10, 3)
        self.stage_title_label.setMaximumWidth(160)
        self.stage_title_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.stagebar.layout().setStretch(1, 0)
        self.workflow_row=QWidget()
        self.workflow_row.setProperty('workflowPlain',True)
        self.workflow_row_layout=QBoxLayout(QBoxLayout.Direction.LeftToRight,self.workflow_row)
        self.workflow_row_layout.setContentsMargins(0,0,0,0)
        self.workflow_row_layout.setSpacing(6)
        self.workflow_row_layout.addWidget(widget)
        if sys.platform in {'darwin', 'win32'}:
            divider = QFrame()
            divider.setObjectName('WorkflowToolbarDivider')
            divider.setFixedSize(1, 18)
            self.workflow_divider = divider
            self.workflow_row_layout.addWidget(divider)
        self.stagebar.layout().addWidget(self.workflow_row,1)
        self._workflow_navigation=widget
        self._workflow_control_origin=None
        self._workflow_controls_in_toolbar=False
        self.workflow_row.installEventFilter(self)

    def set_workflow_controls(self,controls, *, in_global_toolbar=False):
        origin=self._workflow_control_origin
        if origin is not None and controls is not origin[0]:
            old,parent,layout,index,alignment,margins,policy,width_limits=origin
            if not sip.isdeleted(old) and not sip.isdeleted(parent):
                old.parentWidget().layout().removeWidget(old)
                old.setParent(parent)
                old.layout().setContentsMargins(margins)
                old.setSizePolicy(policy)
                old.setMinimumWidth(width_limits[0]);old.setMaximumWidth(width_limits[1])
                layout.insertWidget(index,old,0,alignment)
                old.show()
            self._workflow_control_origin=None
        if controls is not None and self._workflow_control_origin is None:
            parent=controls.parentWidget();layout=parent.layout();index=layout.indexOf(controls)
            alignment=layout.itemAt(index).alignment()
            self._workflow_control_origin=(controls,parent,layout,index,alignment,
                                           controls.layout().contentsMargins(),controls.sizePolicy(),
                                           (controls.minimumWidth(),controls.maximumWidth()))
            layout.removeWidget(controls)
            controls.layout().setContentsMargins(0,0,0,0)
            controls.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed)
            if in_global_toolbar:
                controls.retranslate()
                target=self.global_toolbar.layout()
                target.insertWidget(target.indexOf(self.preset_label),controls)
                controls.setFixedWidth(controls.sizeHint().width())
                controls.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
            else:
                self.workflow_row_layout.addWidget(controls,1)
            controls.show()
            # Nested canvas stacks can paint before their queued LayoutRequest.
            # Reclaim the removed row immediately, not in the following frame.
            layout.activate()
        self._workflow_controls_in_toolbar=in_global_toolbar
        if controls is not None and in_global_toolbar:
            controls.retranslate()
            controls.setFixedWidth(controls.sizeHint().width())
        active=controls is not None and not in_global_toolbar
        self.workflow_row.setVisible(active)
        self.stage_title_label.setVisible(not active)
        self.stage_tools_container.setVisible(not active)
        self._layout_workflow_row()
        if sys.platform in {'darwin', 'win32'}:self._update_toolbar_density()

    def _layout_workflow_row(self):
        if not getattr(self,'_workflow_control_origin',None) or self._workflow_controls_in_toolbar:return
        controls=self._workflow_control_origin[0]
        page=self._workflow_navigation.currentWidget()
        nav_width=page.minimumSizeHint().width() if page is not None else 0
        self._workflow_navigation.setMaximumWidth(max(1,nav_width))
        available=max(1,self.stage_area.width()-20)
        required=nav_width+controls.minimumSizeHint().width()+6
        wrapped=required>available
        if hasattr(self, 'workflow_divider'):
            self.workflow_divider.setVisible(not wrapped)
        direction=QBoxLayout.Direction.TopToBottom if wrapped else QBoxLayout.Direction.LeftToRight
        if self.workflow_row_layout.direction()!=direction:self.workflow_row_layout.setDirection(direction)
        base_height = 42 if sys.platform in {'darwin', 'win32'} else 38
        height=max(base_height,self._workflow_navigation.height()+max(controls.sizeHint().height(), controls.minimumSizeHint().height())+self.workflow_row_layout.spacing()+6) if wrapped else base_height
        if self.stagebar.height()!=height:self.stagebar.setFixedHeight(height)

    def eventFilter(self,watched,event):
        if watched is getattr(self, 'global_toolbar', None) and event.type() == QEvent.Type.Resize:
            self._update_toolbar_density()
        if watched is getattr(self,'workflow_row',None) and event.type() in {QEvent.Type.Resize,QEvent.Type.LayoutRequest}:
            self._layout_workflow_row()
        return super().eventFilter(watched,event)

    def set_style_preset(self, preset_name: str) -> None:
        if not preset_name:
            return
        self.style_preset_combo.blockSignals(True)
        if self.style_preset_combo.findText(preset_name) >= 0:
            self.style_preset_combo.setCurrentText(preset_name)
        self.style_preset_combo.blockSignals(False)

    def set_stage_tools(self, tools: list[tuple[str, Callable[[], None] | None]]) -> None:
        while self.stage_tools_layout.count():
            item = self.stage_tools_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._stage_buttons = []
        for label, callback in tools:
            label_text = tr(label)
            button = QPushButton(label_text)
            button.setObjectName("V9StageToolButton")
            button.setProperty("stageToolLabel", label_text)
            button.setProperty("i18nKey", label)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            if callback is not None:
                button.clicked.connect(lambda _checked=False, action=callback: action())
            self.stage_tools_layout.addWidget(button)
            self._stage_buttons.append(button)

    def set_right_title(self, title: str) -> None:
        self.right_panel_title.setText(title)

    def set_right_widget(self, widget: QWidget, title: str | None = None) -> None:
        title = title or tr("style_inspector")
        if self._right_widget is widget:
            self.set_right_title(title)
            widget.setVisible(True)
            return
        if self._right_widget is not None:
            self.right_layout.removeWidget(self._right_widget)
            self._right_widget.setVisible(False)
        self._right_widget = widget
        self.set_right_title(title)
        self.right_layout.addWidget(widget, 1)
        widget.setVisible(True)

    def retranslate(self) -> None:
        for button in self._action_buttons.values():
            key = button.property("i18nKey")
            if isinstance(key, str):
                button.setText(tr(key))
        self.toolbar_hint.setText(tr("v9_toolbar_hint"))
        self.preset_label.setText(tr("v9_style"))
        if hasattr(self, 'cellpose_device_combo'):
            self.model_device_controller.retranslate()
        self.rail_hide_button.setToolTip(tr("v9_hide_navigation"))
        self.rail_reopen_button.setToolTip(tr("v9_show_navigation"))
        self.right_show_button.setToolTip(tr("v9_show_workflow"))
        if sys.platform in {'darwin', 'win32'}:
            self.right_hide_button.setToolTip(tr('v9_hide_workflow'))
            for button in (self.rail_hide_button, self.rail_reopen_button, self.right_hide_button, self.right_show_button):
                button.setAccessibleName(button.toolTip())
        for button in self._stage_buttons:
            key = button.property("i18nKey")
            if isinstance(key, str):
                label = tr(key)
                button.setText(label)
                button.setProperty("stageToolLabel", label)
        self._update_toolbar_density()

    def collapse_rail(self) -> None:
        if self._rail_collapsed:
            return
        start = self.body_splitter.sizes()
        if not self._clamping_splitter:
            self._saved_rail_width = self._clamp_rail_width(self.rail_width() or DEFAULT_RAIL_WIDTH)
        self._rail_collapsed = True
        self.rail_hide_button.setVisible(False)
        self._apply_splitter_sizes(animate=True, start_sizes=start)
        self._sync_control_visibility()

    def restore_rail(self) -> None:
        if not self._rail_collapsed:
            return
        start = self.body_splitter.sizes()
        self._rail_collapsed = False
        self.rail_hide_button.setVisible(True)
        self._module_selector.setVisible(True)
        self._apply_splitter_sizes(animate=True, start_sizes=start)
        self._sync_control_visibility()

    def is_rail_collapsed(self) -> bool:
        return self._rail_collapsed

    def set_rail_width(self, width: int) -> None:
        self._saved_rail_width = self._clamp_rail_width(width)
        if self._rail_collapsed:
            return
        self._apply_splitter_sizes()

    def rail_width(self) -> int:
        if self._rail_collapsed:
            return 0
        sizes = self.body_splitter.sizes()
        if sizes and sizes[0] > 0:
            return sizes[0]
        return self.rail_panel.width()

    def collapse_right_panel(self) -> None:
        if self._right_collapsed:
            return
        start = self.body_splitter.sizes()
        if not self._clamping_splitter:
            self._saved_right_width = self._clamp_right_width(self.right_panel_width() or DEFAULT_RIGHT_WIDTH)
        self._right_collapsed = True
        self._apply_splitter_sizes(animate=True, start_sizes=start)
        self._sync_control_visibility()

    def set_right_panel_available(self, available: bool) -> None:
        self._right_panel_available = bool(available)
        self.right_panel.setVisible(self._right_panel_available)
        self._apply_splitter_sizes()
        self._sync_control_visibility()

    def restore_right_panel(self) -> None:
        if not self._right_panel_available:
            return
        if not self._right_collapsed:
            return
        start = self.body_splitter.sizes()
        self._right_collapsed = False
        self._apply_splitter_sizes(animate=True, start_sizes=start)
        self._sync_control_visibility()

    def is_right_panel_collapsed(self) -> bool:
        return self._right_collapsed or not self._right_panel_available

    def set_right_panel_width(self, width: int) -> None:
        self._saved_right_width = self._clamp_right_width(width)
        if self._right_collapsed:
            return
        self._apply_splitter_sizes()

    def right_panel_width(self) -> int:
        if self._right_collapsed or not self._right_panel_available:
            return 0
        sizes = self.body_splitter.sizes()
        if len(sizes) >= 3 and sizes[2] > 0:
            return sizes[2]
        return self.right_panel.width()

    def _apply_splitter_sizes(self, *, animate=False, start_sizes=None) -> None:
        from ui.widgets.studio_motion import motion_enabled
        if self._splitter_animation is not None:
            self._splitter_animation.stop()
            self._splitter_animation.deleteLater()
            self._splitter_animation = None
        self._clamping_splitter = True
        total = sum(self.body_splitter.sizes()) or self.body_splitter.width() or 1250
        rail_width = 0 if self._rail_collapsed else self._saved_rail_width
        right_width = 0 if self.is_right_panel_collapsed() else self._saved_right_width
        minimum_stage_width = max(420, self.stage_area.minimumSizeHint().width())
        if not self._rail_collapsed and not self.is_right_panel_collapsed():
            side_budget = max(0, total - minimum_stage_width)
            if rail_width + right_width > side_budget:
                right_width = max(MIN_RIGHT_WIDTH, side_budget - rail_width)
            if rail_width + right_width > side_budget:
                rail_width = max(MIN_RAIL_WIDTH, side_budget - right_width)
        stage_width = max(minimum_stage_width, total - rail_width - right_width)
        target = [rail_width, stage_width, right_width]
        if animate and motion_enabled() and self.isVisible() and start_sizes:
            self._module_selector.show()
            for panel in (self.rail_panel, self.right_panel):
                panel.setMinimumWidth(0)
                panel.setMaximumWidth(16777215)
                panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            animation = QVariantAnimation(self)
            self._splitter_animation = animation
            animation.setDuration(160)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.valueChanged.connect(lambda value: self.body_splitter.setSizes([
                round(start + (end-start)*value) for start, end in zip(start_sizes, target)]))
            animation.finished.connect(lambda: self._finish_splitter_transition(target))
            animation.start()
        else:
            self._finish_splitter_transition(target)

    def _finish_splitter_transition(self, target):
        for panel, closed, minimum, maximum in (
            (self.rail_panel, self._rail_collapsed, MIN_RAIL_WIDTH, MAX_RAIL_WIDTH),
            (self.right_panel, self.is_right_panel_collapsed(), MIN_RIGHT_WIDTH, MAX_RIGHT_WIDTH),
        ):
            panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            panel.setMinimumWidth(0 if closed else minimum)
            panel.setMaximumWidth(0 if closed else maximum)
        self._module_selector.setVisible(not self._rail_collapsed)
        self.body_splitter.setSizes(target)
        self._clamping_splitter = False

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        if self._clamping_splitter:
            return
        sizes = self.body_splitter.sizes()
        if len(sizes) < 3:
            return
        next_rail_width = 0 if self._rail_collapsed else self._clamp_rail_width(sizes[0])
        next_right_width = 0 if self._right_collapsed else self._clamp_right_width(sizes[2])
        changed = False
        if not self._rail_collapsed:
            self._saved_rail_width = next_rail_width
            changed = changed or sizes[0] != next_rail_width
        if not self._right_collapsed and self._right_panel_available:
            self._saved_right_width = next_right_width
            changed = changed or sizes[2] != next_right_width
        if changed:
            self._apply_splitter_sizes()

    def _sync_control_visibility(self) -> None:
        if sys.platform in {'darwin', 'win32'}:
            self.rail_hide_button.setVisible(not self._rail_collapsed)
            self.right_hide_button.setVisible(not self._right_collapsed and self._right_panel_available)
        self.rail_reopen_button.setVisible(self._rail_collapsed)
        self.right_show_button.setVisible(self._right_collapsed and self._right_panel_available)
        if hasattr(self, '_toggle_slots'):
            for slot, stack, hide, show in self._toggle_slots:
                stack.setCurrentWidget(show if (self._rail_collapsed if hide is self.rail_hide_button else self._right_collapsed) else hide)
                slot.setVisible(hide is self.rail_hide_button or self._right_panel_available)
        self.stagebar.setVisible(self._stage_header_visible)

    def _clamp_right_width(self, width: int) -> int:
        return max(MIN_RIGHT_WIDTH, min(int(width), MAX_RIGHT_WIDTH))

    def _clamp_rail_width(self, width: int) -> int:
        return max(MIN_RAIL_WIDTH, min(int(width), MAX_RAIL_WIDTH))

