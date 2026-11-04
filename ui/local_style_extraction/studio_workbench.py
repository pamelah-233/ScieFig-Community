"""Compact macOS presentation for the existing local style workflow."""
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QEvent
from PyQt6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                            QSizePolicy, QVBoxLayout, QWidget)
from ui.widgets.studio_icons import set_studio_icon
from ui.widgets.current_page_stack import CurrentPageStackedWidget
from ui.widgets.workflow_presentation import WorkflowDisclosure


class StudioStyleWorkbench(QObject):
    GROUPS = (
        ('配色', ('series_colors', 'background_color', 'label_color', 'bar_fill_color', 'bar_edge_color', 'bar_use_series_colors')),
        ('文字', ('font_family', 'font_size_pt', 'axis_label_size_pt', 'tick_label_size_pt', 'legend_size_pt', 'font_weight', 'x_tick_rotation')),
        ('线与点', ('line_width', 'axis_line_width', 'point_size', 'point_shape', 'bar_line_width',
                  'bar_width_fraction', 'point_line_width', 'sem_line_width', 'sem_cap_size', 'sem_cap_thick', 'bracket_line_width')),
        ('边框网格', ('show_top_spine', 'show_right_spine', 'gridline_visible', 'gridline_color',
                    'tick_width', 'tick_length', 'x_tick_length', 'axes_box_aspect')),
    )

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._updating_density = False
        self._actions()
        self._source()
        self._preview()
        self._parameters()
        window.stage_header.hide()
        for page in (window.page_a, window.page_b):
            page.layout().setContentsMargins(0, 0, 0, 0)
            page.layout().setSpacing(0)
        for splitter in (window.split_a, window.split_b):
            splitter.setHandleWidth(3)
        # Existing layout/theme controls stay available in a compact footer.
        footer = window.bottom.layout()
        footer.itemAt(0).widget().hide()
        footer.setContentsMargins(6, 2, 6, 2)
        footer.setSpacing(4)
        footer.insertWidget(0, window.action_msg, 1)
        footer.insertWidget(1, window.mismatch, 1)
        footer.insertWidget(2, window.status_label, 1)
        window.btn_a.setText('三栏')
        window.btn_b.setText('分步')
        window.bottom.setFixedHeight(30)
        window.statusBar().hide()
        for label in (window.action_msg, window.mismatch, window.status_label):
            label.setWordWrap(False)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.refresh()
        for bar in (self.action_row,self.source_header,self.preview_header):
            bar.installEventFilter(self)
        self._update_density()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show,QEvent.Type.FontChange,QEvent.Type.StyleChange):
            self._update_density()
        return super().eventFilter(watched,event)

    def _update_density(self):
        if self._updating_density:return
        self._updating_density=True
        try:
            self._fit_buttons(self.action_row,self.action_buttons,reserved=110)
            self._fit_buttons(self.preview_header,self.preview_buttons,reserved=230)
            # The page number and total remain together when the source pane
            # is narrow; give the actual crop and zoom controls their full size.
            compact=self.source_header.width()<290
            self.page_label.setVisible(not compact)
            self.page_total.setVisible(not compact)
            self.window.source_board.page_spin.setToolTip('来源页码 '+self.page_total.text())
        finally:self._updating_density=False

    @staticmethod
    def _fit_buttons(bar,buttons,*,reserved):
        required=reserved+sum(button.fontMetrics().horizontalAdvance(label)+button.iconSize().width()+24 for button,label in buttons)
        compact=bar.width()<required
        for button,label in buttons:
            if button.property('styleIconOnly')!=compact:
                button.setProperty('styleIconOnly',compact)
                button.style().unpolish(button);button.style().polish(button)
            text='' if compact else label
            if button.text()!=text:button.setText(text)
            width=(40 if button.menu() else 32) if compact else button.sizeHint().width()
            button.setFixedWidth(width)

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.layout():
                StudioStyleWorkbench._clear(item.layout())
            if item.widget():
                item.widget().hide()

    def _actions(self):
        w = self.window
        self._clear(w.top.layout())
        row = QWidget(w.top)
        row.setObjectName('StyleActionRow')
        row.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        w.btn_open.setText('打开来源')
        w.btn_extract.setText('提取区域')
        w.btn_extract.setObjectName('StyleExtractAction')
        w.btn_apply.setObjectName('PrimaryButton')
        w.btn_save.setText('JSON')
        w.btn_save.setToolTip('导出样式 JSON')
        w.btn_save.setAccessibleName('导出样式 JSON')
        w.name_edit.setMaximumWidth(132)
        w.name_edit.setMinimumWidth(90)
        w.name_edit.setAccessibleName('样式名称')
        self.action_buttons=[]
        for button, icon in ((w.btn_open, 'open'), (w.btn_extract, 'crop'),
                             (w.btn_library, 'library'), (w.btn_apply, 'check'),
                             (w.btn_load,'open'),(w.btn_saved,'library'),
                             (w.btn_export,'export'),(w.btn_save,'save'),(w.btn_cancel,'reset')):
            set_studio_icon(button, icon, color='#211b16' if button is w.btn_apply else '#c4bbb0')
            label=button.text()
            button.setToolTip(button.toolTip() or label)
            button.setAccessibleName(button.toolTip())
            self.action_buttons.append((button,label))
        for widget in (w.btn_open, w.btn_extract, w.name_edit, w.btn_load, w.btn_saved, w.btn_export, w.btn_save, w.btn_cancel):
            layout.addWidget(widget)
            widget.show()
        layout.addStretch(1)
        for widget in (w.btn_library, w.btn_apply):
            layout.addWidget(widget)
            widget.show()
        w.top.layout().addWidget(row)
        # Reserve a thin busy indicator so starting work cannot move the canvas.
        progress_host = QWidget(w.top)
        progress_host.setFixedHeight(2)
        busy = QHBoxLayout(progress_host)
        busy.setContentsMargins(0, 0, 0, 0)
        w.progress.setMaximumHeight(2)
        w.progress.setMinimumHeight(2)
        busy.addWidget(w.progress)
        w.top.layout().addWidget(progress_host)
        w.top.layout().setContentsMargins(0, 0, 0, 0)
        w.top.layout().setSpacing(0)
        self.action_row = row

    @staticmethod
    def _header(board):
        header = QWidget(board)
        header.setObjectName('StylePaneTools')
        header.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        header.setFixedHeight(36)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(3)
        board.layout().insertWidget(0, header)
        board.layout().setContentsMargins(0, 0, 0, 0)
        board.layout().setSpacing(0)
        return header, layout

    def _source(self):
        board = self.window.source_board
        self._clear(board.layout())
        self.source_header, row = self._header(board)
        board.setMinimumWidth(250)
        self.page_label=QLabel('页')
        row.addWidget(self.page_label)
        board.page_spin.setFixedWidth(44)
        board.page_spin.setAccessibleName('来源页码')
        row.addWidget(board.page_spin)
        board.page_spin.show()
        self.page_total = QLabel('/ —')
        row.addWidget(self.page_total)
        board.region_combo.setMinimumWidth(60)
        board.region_combo.setMaximumWidth(96)
        board.region_combo.setAccessibleName('建议区域或手动裁剪')
        board.region_combo.currentTextChanged.connect(board.region_combo.setToolTip)
        row.addWidget(board.region_combo)
        board.region_combo.show()
        self._zoom(board.canvas.zoom_controls)
        row.addWidget(board.canvas.zoom_controls, 1)
        board.canvas.setMinimumSize(80, 80)
        board.layout().addWidget(board.canvas, 1)
        board.canvas.show()
        board.layout().addWidget(board.thumbs)
        self.source_name = QLabel('未打开来源')
        self.source_name.setObjectName('StylePaneCaption')
        self.source_name.setMinimumWidth(0)
        self.source_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.source_name.setFixedHeight(24)
        board.layout().addWidget(self.source_name)
        board.crop_label.setMinimumWidth(0)
        board.crop_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        board.crop_label.setFixedHeight(22)
        board.layout().addWidget(board.crop_label)
        board.crop_label.show()

    @staticmethod
    def _zoom(controls):
        controls.layout().setContentsMargins(0, 0, 0, 0)
        controls.layout().setSpacing(1)
        for widget in (controls.fit_button, controls.zoom_out_button, controls.zoom_label, controls.zoom_in_button):
            controls.layout().removeWidget(widget)
        for widget in (controls.zoom_out_button, controls.zoom_label, controls.zoom_in_button, controls.fit_button):
            controls.layout().addWidget(widget)
        for button in (controls.fit_button, controls.zoom_out_button, controls.zoom_in_button):
            button.setFixedSize(21, 24)
            button.setStyleSheet('min-height:0px;min-width:0px;padding:0px;')
        controls.fit_button.setFixedWidth(max(32,controls.fit_button.fontMetrics().horizontalAdvance(controls.fit_button.text())+8))
        controls.zoom_label.setMinimumWidth(34)
        controls.show()

    def _preview(self):
        board = self.window.preview_board
        self._clear(board.layout())
        self.preview_header, row = self._header(board)
        board.setMinimumWidth(310)
        board.chart_combo.setMinimumWidth(60)
        board.chart_combo.setMaximumWidth(88)
        board.chart_combo.setAccessibleName('预览图类型')
        board.chart_combo.currentTextChanged.connect(board.chart_combo.setToolTip)
        board.chart_combo.setCurrentIndex(board.chart_combo.findData('bar'))
        row.addWidget(board.chart_combo)
        row.addWidget(board.view_bar)
        board.view_bar.layout().setSpacing(2)
        self.preview_buttons=[]
        for button,icon in ((board.btn_view_styled,'check'),(board.btn_view_base,'reset'),(board.btn_view_compare,'table')):
            label=button.text()
            set_studio_icon(button,icon)
            button.setToolTip(label);button.setAccessibleName(label)
            self.preview_buttons.append((button,label))
        self._zoom(board.zoom_controls)
        row.addWidget(board.zoom_controls, 1)
        set_studio_icon(board.btn_enlarge, 'expand', icon_only=True)
        board.btn_enlarge.setFixedSize(24, 26)
        board.btn_enlarge.setStyleSheet('min-height:0px;min-width:0px;padding:0px;')
        row.addWidget(board.btn_enlarge)
        for widget in (board.chart_combo, board.view_bar, board.btn_enlarge):
            widget.show()
        board.layout().addWidget(board.view_host, 1)
        board.view_host.show()
        board.meta.setWordWrap(False)
        board.meta.setMinimumWidth(0)
        board.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        board.meta.setFixedHeight(24)
        meta_row = QWidget()
        meta_row.setFixedHeight(24)
        meta_layout = QHBoxLayout(meta_row)
        meta_layout.setContentsMargins(0, 0, 4, 0)
        meta_layout.addWidget(board.meta, 1)
        meta_layout.addWidget(board.details_btn)
        board.layout().addWidget(meta_row)
        board.meta.show()
        note = QLabel('固定示例数据 · 编辑只影响样式预览')
        note.setObjectName('StylePaneCaption')
        note.setFixedHeight(22)
        note.setMinimumWidth(0)
        note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        board.layout().addWidget(note)
        # Existing technical metadata remains available below the preview.
        board.layout().addWidget(board.raw_meta)

    def _parameters(self):
        editor = self.window.token_editor
        editor.setMinimumWidth(240)
        editor.setMaximumWidth(320)
        title = editor.layout().itemAt(0).widget()
        title.hide()
        title.setFixedHeight(0)
        self.tabs_header, layout = self._header(editor)
        self.tabs = QButtonGroup(self)
        self.pages = CurrentPageStackedWidget(compact=True)
        self.pages.setObjectName('StyleParameterPages')
        self._clear(editor.inner_layout)
        for index, (label, keys) in enumerate(self.GROUPS):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName('StyleParameterTab')
            self.tabs.addButton(button, index)
            layout.addWidget(button, 1)
            page = QWidget()
            body = QVBoxLayout(page)
            body.setContentsMargins(6, 0, 6, 0)
            body.setSpacing(2)
            for key in keys:
                token = editor.rows[key]
                token.setProperty('studioCompact', True)
                token.layout().setContentsMargins(4, 6, 4, 6)
                token.layout().setSpacing(3)
                token.reset_btn.setToolTip('还原「' + token.title.text() + '」的提取值')
                set_studio_icon(token.reset_btn, 'reset', icon_only=True)
                token.reset_btn.setFixedSize(22, 22)
                token.layout().removeWidget(token.details_btn)
                token.layout().itemAt(0).layout().addWidget(token.details_btn)
                token.details_btn.setText('依据')
                token.details_btn.hide()
                token.reason.hide()
                token.editor.setMinimumWidth(0)
                body.addWidget(token)
                token.show()
            body.addStretch(1)
            self.pages.addWidget(page)
        editor.inner_layout.addWidget(self.pages)
        evidence = QWidget()
        evidence_layout = QVBoxLayout(evidence)
        evidence_layout.setContentsMargins(6, 3, 6, 6)
        for widget in (editor.summary, editor.coverage, editor.details_btn, editor.raw_box):
            evidence_layout.addWidget(widget)
        self.evidence = WorkflowDisclosure('提取依据与兼容性', evidence)
        editor.inner_layout.addWidget(self.evidence)
        editor.inner_layout.addStretch(1)
        self.tabs.idClicked.connect(self.pages.setCurrentIndex)
        self.tabs.button(0).setChecked(True)
        editor.summary.setContentsMargins(8, 6, 8, 4)
        editor.coverage.setContentsMargins(8, 0, 8, 4)

    def arrange(self, mode):
        w = self.window
        for pane in (w.source_board, w.preview_board, w.token_editor):
            pane.setParent(None)
        if mode == 'A':
            for pane in (w.source_board, w.preview_board, w.token_editor):
                w.split_a.addWidget(pane)
            w.split_a.setSizes([310, 390, 256])
            for index, factor in enumerate((3, 4, 0)):
                w.split_a.setStretchFactor(index, factor)
            w.body_stack.setCurrentWidget(w.page_a)
        else:
            w.stage_source_layout.addWidget(w.source_board)
            w.split_b.addWidget(w.preview_board)
            w.split_b.addWidget(w.token_editor)
            w.split_b.setSizes([600, 256])
            w.split_b.setStretchFactor(0, 1)
            w.split_b.setStretchFactor(1, 0)
            w.body_stack.setCurrentWidget(w.page_b)
        w.preview_board.set_layout_kind('A')
        self.stage_changed()

    def stage_changed(self):
        w = self.window
        w.stage_stack.setCurrentIndex(w.stage_index)
        target = self.source_header if w.stage_index == 0 else self.preview_header
        for index, button in enumerate((w.btn_stage1, w.btn_stage2)):
            button.setText(('来源', '预览')[index])
            target.layout().insertWidget(index, button)
            button.setVisible(w.layout_mode == 'B')
        for pane in (w.source_board, w.preview_board, w.token_editor):
            pane.show()

    def refresh(self):
        w = self.window
        self.source_name.setText(Path(w.source_path).name if w.source_path else '未打开来源 · PDF / 图像')
        self.source_name.setToolTip(w.source_path or '在页面上拖拽框选图区；建议区域不会自动提取。')
        self.page_total.setText('/ ' + (str(w.page_count) if w.source_path else '—'))
        for label in (w.action_msg, w.mismatch, w.status_label, w.source_board.crop_label, w.preview_board.meta):
            label.setToolTip(label.text())
