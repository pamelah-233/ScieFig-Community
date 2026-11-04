"""Arrange existing workbenches as compact A-layout without changing analysis."""
import sys
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,QFormLayout

from core.utils.i18n import bind_i18n, get_language, tr
from ui.widgets.workflow_presentation import WorkflowActionFooter, WorkflowDisclosure, WorkflowTaskHeading


class WorkflowPresentationController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.bars, self.footers, self.disclosures, self.headings = {}, {}, [], {}
        self._footer_sources = set()
        self._last_steps = {}
        self._layout_sections = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.sync)
        data, fluoro = window.data_panel, window.fluoro_workspace
        self.buttons = {name: getattr(data, f'{name}_step_buttons') for name in ('qpcr', 'wb', 'ihc', 'brightfield')}
        self.buttons['fluoro'] = fluoro.step_buttons
        self.panels = dict(qpcr=window.qpcr_workflow_panel, wb=window.wb_workflow_panel,
                           fluoro=fluoro.workflow_panel, ihc=window.ihc_workspace.workflow_panel,
                           brightfield=window.brightfield_workspace.workflow_panel)
        self.navigation = QStackedWidget()
        self.navigation.setObjectName('WorkflowNavigationStack')
        self.navigation.setFixedHeight(32)
        self.navigation.setMinimumWidth(0)
        self.navigation.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        window.v9_shell.set_workflow_navigation(self.navigation)
        for module, buttons in self.buttons.items():
            self._install_navigation(module, buttons)
            self.panels[module].setProperty('workflowWorkbench', True)
            heading = WorkflowTaskHeading()
            self.panels[module].layout().insertWidget(0, heading)
            self.headings[module] = heading
            footer = WorkflowActionFooter(
                lambda name=module: self._navigate(name, -1),
                lambda name=module: self._navigate(name, 1),
            )
            self.panels[module].layout().addWidget(footer)
            self.footers[module] = footer
            stack = fluoro.context_stack if module == 'fluoro' else getattr(data, f'{module}_context_stack')
            stack.currentChanged.connect(self.schedule_sync)
            for button in buttons:
                button.clicked.connect(self.schedule_sync)
        self._organize_secondary_controls()
        menu = self.headings['fluoro'].settings_button.menu()
        self._settings_actions = []
        for kind, key in (('parameters', 'workflow_advanced'), ('mode', 'workflow_presets_batch')):
            action = menu.addAction(tr(key))
            action.triggered.connect(lambda checked=False, target=kind: self._show_fluoro_settings(target))
            self._settings_actions.append((action, key))
        self._flatten_workbenches()
        if sys.platform in {'darwin', 'win32'}:
            self._configure_workflow_tables()
        fluoro.measure_params_stack.currentChanged.connect(self.schedule_sync)
        for button in fluoro.measure_mode_buttons.values():
            button.clicked.connect(self.schedule_sync)
        for button in (fluoro.draw_square_button, fluoro.draw_circle_button, fluoro.draw_freehand_button):
            button.clicked.connect(lambda checked=False, source=button: self._select_roi_source(source))
        self._roi_source = fluoro.draw_square_button
        for combo in (data.brightfield_measure_type_combo, data.ihc_mode_combo, data.brightfield_mode_combo):
            combo.currentIndexChanged.connect(self.schedule_sync)
        for controller in window.nuclei_controllers.values():
            controller.controls.entry.toggled.connect(self.schedule_sync)
        for controller in window.myotube_controllers.values():
            controller.controls.entry.toggled.connect(self.schedule_sync)
        window.v9_shell.stage_title_changed.connect(self.schedule_sync)
        # Move chrome before the incoming canvas can paint at its old, shorter size.
        window.canvas_stack.currentChanged.connect(lambda *_args:self.sync())
        for workspace in (window.ihc_workspace,window.brightfield_workspace):
            workspace.canvas_stack.currentChanged.connect(lambda *_args:self.sync())
        for button in (window.language_zh_button, window.language_en_button):
            button.clicked.connect(self.schedule_sync)
        self._watched_actions = set()
        from ui.controllers.measurement_workbench_controller import MeasurementWorkbenchController
        window.measurement_tasks = {name:MeasurementWorkbenchController(window,name) for name in ('brightfield','fluoro')}
        from ui.controllers.import_scale_controller import ImportScaleController
        self.scales = ImportScaleController(window)
        self.preview_groups = {}
        if sys.platform in {'darwin', 'win32'}:
            self._install_studio_preview_controls()
        from ui.controllers.measurement_preview_controller import MeasurementPreviewController
        window.measurement_preview = MeasurementPreviewController(window)
        from ui.controllers.module_defaults_controller import ModuleDefaultsController
        window.module_defaults = ModuleDefaultsController(window)
        from ui.controllers.measurement_history_controller import MeasurementHistoryController
        window.measurement_history = MeasurementHistoryController(window,self)
        from ui.widgets.workflow_forms import readable_forms
        from ui.widgets.responsive_action_grid import adapt_action_rows
        for panel in self.panels.values():
            readable_forms(panel);adapt_action_rows(panel)
        for panel in (window.image_browser_workspace.side_panel,window.composer_workspace.inspector_dialog):
            readable_forms(panel);adapt_action_rows(panel)
        self.sync()

    def _install_studio_preview_controls(self):
        """Move the existing view buttons; keep their state, commands and gate."""
        w = self.window
        entries = (
            ('fluoro', w.measurement_tasks['fluoro'].preview_row,
             w.fluoro_workspace.canvas.zoom_controls,
             (w.fluoro_workspace.ai_original_preview_button, w.fluoro_workspace.ai_overlay_preview_button,
              w.fluoro_workspace.export_image_button)),
            ('brightfield', w.measurement_tasks['brightfield'].preview_row,
             w.brightfield_workspace.canvas.zoom_controls,
             (w.brightfield_workspace.original_preview_button, w.brightfield_workspace.overlay_preview_button)),
            ('ihc', w.ihc_workspace.preview_toggle_panel,
             w.ihc_workspace.canvas.zoom_controls,
             (w.ihc_workspace.original_preview_button, w.ihc_workspace.overlay_preview_button)),
        )
        for module, source_row, controls, buttons in entries:
            group = QWidget()
            group.setObjectName('MeasurementPreviewGroup')
            group.setFixedHeight(32)
            group.setProperty('workflowPlain', True)
            row = QHBoxLayout(group)
            row.setContentsMargins(4, 2, 4, 2)
            row.setSpacing(2)
            caption = bind_i18n(QLabel(), 'preview_display_group')
            caption.setObjectName('PreviewGroupCaption')
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setFixedHeight(26)
            row.addWidget(caption)
            for button in buttons:
                button.parentWidget().layout().removeWidget(button)
                button.setProperty('studioPreview', True)
                button.setFixedHeight(26)
                row.addWidget(button)
            controls.layout().insertWidget(0, group)
            self.preview_groups[module] = (source_row, group)

    def _install_navigation(self, module, buttons):
        original = buttons[0].parentWidget()
        old_group = original.parentWidget()
        bar = QWidget()
        bar.setObjectName('WorkflowInlineNavigation')
        bar.setFixedHeight(32)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0 if sys.platform in {'darwin', 'win32'} else 3)
        for button in buttons:
            original.layout().removeWidget(button)
            button.setObjectName('WorkflowInlineStep')
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            button.setFixedHeight(26)
            layout.addWidget(button)
            if button is not buttons[-1]:
                arrow = QLabel('›')
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setFixedHeight(26)
                arrow.setObjectName('WorkflowStepArrow' if sys.platform in {'darwin', 'win32'} else 'WorkflowStepMeta')
                layout.addWidget(arrow)
        layout.addStretch(1)
        original.hide()
        old_group.hide()
        self.navigation.addWidget(bar)
        self.bars[module] = bar

    def _wrap(self, widget, key, *, expanded=False):
        parent_layout = widget.parentWidget().layout()
        index = parent_layout.indexOf(widget)
        parent_layout.removeWidget(widget)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(widget)
        section = WorkflowDisclosure(key, body, expanded=expanded)
        parent_layout.insertWidget(index, section)
        self.disclosures.append(section)
        return section

    def _organize_secondary_controls(self):
        window, data, ws = self.window, self.window.data_panel, self.window.fluoro_workspace
        for workspace in (ws, window.ihc_workspace, window.brightfield_workspace):
            group = workspace.scale_appearance_group if workspace is ws else workspace.scale_bar_controls_panel
            section = self._wrap(group, 'workflow_display_settings')
            workspace.scale_display_section = section
            scroll = workspace.workflow_panel.findChild(QScrollArea)
            content = scroll.widget().layout()
            content.removeWidget(section)
            content.insertWidget(max(0, content.count() - 1), section)
            section.setExpanded(True)
            section.toggle.hide()
        page_layout = ws.context_stack.widget(2).layout()
        # Keep the original preset/mode/batch controls, but behind the current task.
        tools = QWidget()
        tools_layout = QVBoxLayout(tools)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        for widget in (ws.mode_label, ws.mode_combo, window.analysis_state.bars['fluorescence'],
                       window.microscopy_workflow.batch_buttons['fluorescence']):
            widget.parentWidget().layout().removeWidget(widget)
            tools_layout.addWidget(widget)
        review = window.microscopy_workflow.review_buttons['fluorescence']
        old_row = review.parentWidget()
        old_row.layout().removeWidget(review)
        page_layout.removeWidget(old_row)
        old_row.hide()
        page_layout.insertWidget(page_layout.indexOf(ws.measure_summary_stack) + 1, review)
        ws.secondary_tools_section = WorkflowDisclosure('workflow_presets_batch', tools)
        page_layout.insertWidget(page_layout.count() - 1, ws.secondary_tools_section)
        self.disclosures.append(ws.secondary_tools_section)
        ws.results_drawer.setCheckable(False)
        ws.full_results_section = self._wrap(ws.results_drawer, 'full_results')
        for section in (ws.roi_advanced_section, ws.count_threshold_section):
            section.parentWidget().layout().removeWidget(section)
            page_layout.insertWidget(page_layout.indexOf(ws.secondary_tools_section), section)
        from ui.widgets.calibration_controls import action_grid
        roi_layout = ws.roi_intensity_summary_panel.layout()
        actions = (ws.open_roi_results_button, ws.clear_roi_button, ws.delete_selected_measurement_button)
        for button in actions:
            roi_layout.removeWidget(button)
        roi_layout.addWidget(action_grid(actions))
        page_layout.insertWidget(page_layout.indexOf(ws.measure_summary_stack),
                                 bind_i18n(QLabel(), 'workflow_current_results'))
        ws.active_image_panel.layout().setContentsMargins(0, 0, 0, 0)
        ws.active_image_panel.layout().setSpacing(6)
        ws.active_image_group.setMaximumHeight(90)
        for name in ('ihc', 'brightfield'):
            self._wrap(window.analysis_state.bars[name], 'workflow_presets_batch')
        # The current task precedes staging; the original full list remains one click away.
        self.staging_section = self._wrap(window.wb_staging_group, 'strip_staging')
        self.canvas_sections = dict(
            wb=self._wrap(window.wb_canvas_tools_group, 'canvas_tools', expanded=True),
            qpcr=self._wrap(window.qpcr_canvas_tools_group, 'canvas_tools', expanded=True),
        )

    def _flatten_workbenches(self):
        from ui.widgets.workflow_forms import readable_forms
        data=self.window.data_panel
        # All modules use the same outer workbench gutter as fluorescence.
        # Legacy context panels added another 12 px on both sides.
        for module in ('qpcr','wb','ihc','brightfield'):
            getattr(data,f'{module}_context_panel').layout().setContentsMargins(0,0,0,0)
        # White balance is outside the nested workflow group at import/measure.
        # Give it the same gutter rather than drawing its title on the edge.
        for module in ('ihc',):
            widget=getattr(self.window,module+'_workspace').white_balance.widget
            layout=widget.parentWidget().layout();index=layout.indexOf(widget)
            if index>=0:
                layout.removeWidget(widget)
                wrapper=QWidget();wrapper.setProperty('workflowPlain',True)
                inner=QVBoxLayout(wrapper);inner.setContentsMargins(14,0,14,0);inner.addWidget(widget)
                layout.insertWidget(index,wrapper)
        for panel in self.panels.values():
            panel.layout().setContentsMargins(0, 0, 0, 0)
            for widget in panel.findChildren(QWidget):
                if (type(widget) is QWidget or isinstance(widget, (QStackedWidget, QScrollArea))
                        or (type(widget) is QFrame and widget.objectName() != 'WorkflowActionFooter')):
                    widget.setProperty('workflowPlain', True)
                if isinstance(widget, QGroupBox):
                    widget.setProperty('workflowGroup', True)
                if widget.objectName() == 'V9RightWorkbenchGroup':
                    widget.layout().setContentsMargins(14, 0, 14, 0)
                    widget.layout().setSpacing(6)
                    widget.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
                    parent_layout=widget.parentWidget().layout()
                    if isinstance(parent_layout,QVBoxLayout):
                        parent_layout.setStretchFactor(widget,0)
                    for label in widget.findChildren(QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly):
                        if label.objectName() == 'SectionLabel':
                            label.hide()
            scroll = panel.findChild(QScrollArea)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.widget().setProperty('workflowPlain', True)
            readable_forms(panel)
        ws = self.window.fluoro_workspace
        ws.context_panel.layout().setContentsMargins(0, 0, 0, 0)
        ws.context_panel.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
        ws.context_stack.widget(2).layout().setContentsMargins(0, 0, 0, 0)
        for stack in (ws.measure_params_stack, ws.measure_summary_stack):
            for index in range(stack.count()):
                stack.widget(index).layout().setContentsMargins(0, 0, 0, 0)

    def _configure_workflow_tables(self):
        data = self.window.data_panel
        tables = [data.qpcr_group_table, data.wb_stats_group_table,
                  data.ihc_stats_group_table, data.brightfield_stats_group_table,
                  self.window.fluoro_workspace.fluoro_stats_group_table]
        for table in tables:
            table.setProperty('workflowGroupTable', True)
            table.verticalHeader().hide()
            table.horizontalHeader().setMinimumSectionSize(32)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(2, 64)
            table.verticalHeader().setDefaultSectionSize(30)
        table = data.wb_export_item_table
        table.setObjectName('WorkflowExportTable')
        table.verticalHeader().hide()
        table.setWordWrap(True)
        table.horizontalHeader().setMinimumSectionSize(32)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 54)
        table.setColumnWidth(2, 82)
        table.verticalHeader().setDefaultSectionSize(48)
        def fit_export_height(*_args):
            table.setFixedHeight(min(184, max(96, 34 + 48 * table.rowCount())))
        table.model().rowsInserted.connect(fit_export_height)
        table.model().rowsRemoved.connect(fit_export_height)
        fit_export_height()

    def _step(self, module):
        return next((index for index, button in enumerate(self.buttons[module]) if button.isChecked()), 0)

    def _show_fluoro_settings(self, kind):
        ws = self.window.fluoro_workspace
        if kind == 'mode':
            target = ws.secondary_tools_section
        elif kind == 'appearance':
            target = ws.scale_display_section
        else:
            target = {'roi_intensity': ws.roi_advanced_section,
                      'spot_count': ws.count_threshold_section}.get(ws.measure_mode, ws.measure_params_stack)
        if isinstance(target, WorkflowDisclosure):
            target.setExpanded(True)
            anchor = target.toggle
        else:
            anchor = target
        scroll = ws.workflow_panel.findChild(QScrollArea)
        def position_settings():
            scroll.widget().layout().activate()
            scroll.verticalScrollBar().setValue(anchor.mapTo(scroll.widget(), QPoint()).y() - 12)
        QTimer.singleShot(0, position_settings)

    def _navigate(self, module, delta):
        index = self._step(module) + delta
        if 0 <= index < len(self.buttons[module]):
            self.buttons[module][index].click()

    def _select_roi_source(self, source):
        self._roi_source = source
        self.schedule_sync()

    def schedule_sync(self, *_args):
        self._timer.start(0)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.Show):
            self.schedule_sync()
        return super().eventFilter(watched, event)

    def _action(self, module, step):
        w, d, f = self.window, self.window.data_panel, self.window.fluoro_workspace
        if step==2 and module in w.measurement_tasks:return w.measurement_tasks[module].primary_action(),None
        if module == 'fluoro':
            return {0: f.import_button, 1: self.buttons[module][2], 3: f.run_fluoro_stats_button,
                    4: f.send_layout_to_composer_button, 5: f.export_button}.get(step), None
        if module == 'qpcr':
            return {0: d.import_button, 1: d.run_analysis_button, 2: d.qpcr_to_composer_button,
                    3: self.window.v9_shell._action_buttons['export']}.get(step), None
        if module == 'wb':
            return {0: d.wb_import_strip_button, 1: d.wb_apply_crop_button, 2: self.buttons[module][3],
                    3: d.wb_density_measure_button, 4: d.wb_stats_run_button,
                    5: d.wb_apply_layout_button, 6: d.wb_export_button}.get(step), None
        if module == 'ihc':
            return {0: w.ihc_workspace.import_button, 1: d.ihc_run_analysis_button,
                    2: d.ihc_stats_run_button, 3: w.ihc_workspace.send_layout_to_composer_button}.get(step), None
        return {0: w.brightfield_workspace.import_button, 1: d.brightfield_preview_mask_button,
                3: d.brightfield_stats_run_button, 4: w.brightfield_workspace.send_layout_to_composer_button}.get(step), None

    def sync(self):
        module = self.window.active_module
        if getattr(self,'_form_language',None)!=get_language():
            self._form_language=get_language()
            for panel in self.panels.values():
                for form in panel.findChildren(QFormLayout):
                    for row in range(form.rowCount()):
                        item=form.itemAt(row,QFormLayout.ItemRole.LabelRole)
                        label=item.widget() if item else None
                        if isinstance(label,QLabel):
                            label.setMinimumWidth(min(120,label.fontMetrics().horizontalAdvance(label.text())+6))
                            label.setMinimumHeight(label.fontMetrics().height()+3)
        self.window.v9_shell.set_stage_header_visible(module not in {'viewer', 'composer', 'stylelab'})
        self.navigation.setVisible(module in self.bars)
        if module not in self.bars:
            if hasattr(self, 'scales'):
                self.scales.sync(module, -1)
            controls=self.window.image_browser_workspace.canvas.zoom_controls if module=='viewer' else None
            self.window.v9_shell.set_workflow_controls(controls,in_global_toolbar=module=='viewer')
            return
        self.navigation.setCurrentWidget(self.bars[module])
        if sys.platform in {'darwin', 'win32'}:
            for button in self.buttons[module]:
                label = button.text().lstrip('✓•○● ')
                if button.text() != label:
                    button.setText(label)
        w=self.window
        canvas=(w.qpcr_layout_canvas if w.canvas_stack.currentWidget() is w.qpcr_layout_workspace else w.preview_canvas) if module=='qpcr' else (
            w.wb_canvas if module=='wb' else w.fluoro_workspace.canvas if module=='fluoro' else
            getattr(w,f'{module}_workspace').canvas_stack.currentWidget())
        if module=='wb':
            canvas.zoom_controls.object_controls.setVisible(w._source_layout_context(module) is not None)
        self.window.v9_shell.set_workflow_controls(canvas.zoom_controls)
        self.window.v9_shell.stage_title_label.setText(tr(f'v9_module_{module}'))
        footer = self.footers[module]
        step = self._step(module)
        history=getattr(w,'measurement_history',None)
        if history:history.sync_visibility(module,step)
        for name,deck in self.window.measurement_tasks.items():
            deck.sync_visibility(step if name==module else -1)
        for name, (source_row, group) in self.preview_groups.items():
            group.setVisible(name == module and source_row.isVisibleTo(self.panels[name]))
        self.scales.sync(module, step)
        self.window._sync_shared_layout_editor()
        for name, section in self.canvas_sections.items():
            section.setVisible(module == name and step == (5 if name == 'wb' else 2))
        source, label = self._action(module, step)
        if source is not None and source not in self._watched_actions:
            source.installEventFilter(self)
            self._watched_actions.add(source)
        # Proxy the existing command once in the fixed footer; do not show two CTAs.
        if (source is not None and self.panels[module].isAncestorOf(source)
                and source not in (self.window.fluoro_workspace.draw_square_button,
                                   self.window.fluoro_workspace.draw_circle_button,
                                   self.window.fluoro_workspace.draw_freehand_button)):
            self._footer_sources.add(source)
        for button in self._footer_sources:
            if not button.isHidden():
                button.hide()
        if module == 'fluoro':
            ws = self.window.fluoro_workspace
            ws.roi_advanced_section.setVisible(ws.measure_mode == 'roi_intensity')
            ws.count_threshold_section.setVisible(ws.measure_mode == 'spot_count')
        if module in ('ihc', 'brightfield'):
            workspace = getattr(self.window, f'{module}_workspace')
            workspace.title_label.hide()
            workspace.subtitle_label.hide()
            workspace.import_button.setVisible(False)
            workspace.remove_button.setVisible(step == 0)
        if module == 'wb':
            if self._last_steps.get(module) != step:
                self.staging_section.setExpanded(step == 0)
        self._last_steps[module] = step
        tasks = dict(qpcr=('import', 'measure', 'layout', 'export'),
                     wb=('import', 'crop', 'labels', 'measure', 'stats', 'layout', 'export'),
                     fluoro=('import', 'channels', 'measure', 'stats', 'layout', 'export'),
                     ihc=('import', 'measure', 'stats', 'layout'),
                     brightfield=('import', 'segment', 'measure', 'stats', 'layout'))
        self.headings[module].set_step(step, len(self.buttons[module]),
                                      self.buttons[module][step].text().lstrip('✓•○● '), tasks[module][step])
        self.headings[module].setVisible(not (module in self.window.measurement_tasks and step==2))
        self.headings[module].settings_button.setVisible(module == 'fluoro' and step == 2)
        for action, key in self._settings_actions:
            action.setText(tr(key))
        footer.set_action(source, label=label)
        layout_step={'qpcr':2,'wb':5,'fluoro':4,'ihc':3,'brightfield':4}[module]
        states=self._layout_sections.setdefault(module,{})
        if step==layout_step:
            for section in self.panels[module].findChildren(WorkflowDisclosure):
                if section.isVisibleTo(self.panels[module]):
                    states.setdefault(section,(section.expanded,section.toggle.isHidden()))
                    section.setExpanded(True);section.toggle.hide()
        elif states:
            for section,(expanded,hidden) in states.items():
                section.setExpanded(expanded);section.toggle.setVisible(not hidden)
            states.clear()
        footer.previous.setEnabled(step > 0)
        footer.following.setEnabled(step < len(self.buttons[module]) - 1)
        footer.retranslate()
        if hasattr(self.window, 'analysis_feedback'):
            self.window.analysis_feedback.sync()
        for section in self.disclosures:
            section.retranslate()

