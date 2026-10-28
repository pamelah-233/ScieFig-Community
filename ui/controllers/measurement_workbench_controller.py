"""Approved A workbench: persistent task selection, one real form at a time."""
import sys
from PyQt6.QtCore import QObject,Qt,QTimer
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QFormLayout,QScrollArea,QPushButton,QButtonGroup,QGroupBox
from core.utils.i18n import bind_i18n,tr
from ui.widgets.current_page_stack import CurrentPageStackedWidget
from ui.widgets.workflow_presentation import WorkflowDisclosure


from ui.widgets.workflow_forms import readable_forms


class MeasurementWorkbenchController(QObject):
    def __init__(self,window,module):
        super().__init__(window)
        self.window=window;self.module=module;self._selecting=False
        d=window.data_panel;f=window.fluoro_workspace
        self.panel=f.workflow_panel if module=='fluoro' else window.brightfield_workspace.workflow_panel
        host=f.context_stack.widget(2) if module=='fluoro' else d.brightfield_context_stack.widget(2)
        self.selector=f.measure_task_combo if module=='fluoro' else d.brightfield_measure_type_combo
        self.controllers={'myotube':window.myotube_controllers[module],
            'dapi' if module=='fluoro' else 'he':window.nuclei_controllers[module]}
        if module=='fluoro':
            from ui.controllers.measurement_relations_controller import MeasurementRelationsController
            self.controllers.update({task:MeasurementRelationsController(window,task) for task in ('phenotype','count_ratio','fusion')})
        if module=='brightfield':self.controllers['central']=window.central_nuclei_controller
        # Reuse entry buttons as the actual task buttons. No duplicate hidden
        # selectors, checkable sections or alternate UI are kept alive.
        for task,controller in self.controllers.items():
            self.selector.addItem(tr('task_'+task),task,controller.controls.entry)
        if module=='fluoro':
            self.selector.reorder(['roi_intensity','spot_count','dapi','myotube','ai_assisted','phenotype','count_ratio','fusion','line_profile','colocalization'])
            native=QWidget();native_layout=QVBoxLayout(native);native_layout.setContentsMargins(0,0,0,0);native_layout.setSpacing(8)
            layout=host.layout();dedicated=[c.controls for c in self.controllers.values()]
            while layout.count():
                item=layout.takeAt(0);widget=item.widget()
                if widget is self.selector or widget in dedicated:continue
                if widget:native_layout.addWidget(widget)
                elif item.layout():native_layout.addLayout(item.layout())
            self.stack=CurrentPageStackedWidget(compact=True);self.stack.addWidget(native)
            self.pages={key:native for key in f.measure_mode_buttons}
            layout.addWidget(self.stack)
            self.shared=None
        else:
            self.selector.reorder(['cells','area','fiber','he','central','myotube'])
            self.stack=d.brightfield_measure_mode_stack
            self.pages={key:self.stack.widget(index) for index,key in enumerate(('cells','area','fiber'))}
            d.brightfield_measure_group.layout().removeWidget(self.stack)
            host.layout().addWidget(self.stack)
            self.shared=d.brightfield_measure_group
            host.layout().removeWidget(self.shared)
            host.layout().addWidget(self.shared)
            self.shared.setTitle('');self.shared.setContentsMargins(0,0,0,0)
            self.shared.layout().setContentsMargins(0,0,0,0)
            d.brightfield_bright_round_cell_group.layout().setContentsMargins(0,0,0,0)
            # Calibration/display are accessible without occupying the top of
            # every measurement. Scientific values and commands are unchanged.
            for index,key in enumerate(('cells','area','fiber')):self.selector.setItemText(index,tr('task_'+key))
        for task,controller in self.controllers.items():
            self.stack.addWidget(controller.controls);self.pages[task]=controller.controls
            controller.controls.entry.toggled.connect(lambda checked,k=task:self.select(k) if checked else None)
            self._disclose_parameters(controller.controls,task)
        for page in set(self.pages.values()):
            page.setProperty('measurementPage',True);page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
            page.style().unpolish(page);page.style().polish(page)
        if module=='fluoro':
            self._advanced_rows(f.ai_form,[f.ai_min_area_spin,f.ai_max_area_spin,f.ai_sensitivity_spin,
                f.ai_allow_restricted_checkbox],f.ai_assisted_group.layout())
        self.selector.parentWidget().layout().removeWidget(self.selector)
        self.selector_wrap=QWidget();pick=QVBoxLayout(self.selector_wrap);pick.setContentsMargins(10,8,10,12);pick.setSpacing(7)
        self.selector_wrap.setProperty('workflowPlain',True)
        pick.addWidget(bind_i18n(QLabel(),'task_select'));pick.addWidget(self.selector)
        if module=='fluoro':
            self.preview_row=QWidget();row=QHBoxLayout(self.preview_row);row.setContentsMargins(0,0,0,0)
            self.preview_row.setProperty('workflowPlain',True)
            for button in (f.ai_original_preview_button,f.ai_overlay_preview_button,f.export_image_button):row.addWidget(button)
            pick.addWidget(self.preview_row)
            self.input_row=QWidget();input_layout=QVBoxLayout(self.input_row);input_layout.setContentsMargins(0,0,0,0);input_layout.setSpacing(4)
            self.input_row.setProperty('workflowPlain',True)
            input_layout.addWidget(bind_i18n(QLabel(),'segmentation_input'))
            row=QHBoxLayout();self.input_buttons={};self.input_group=QButtonGroup(self.input_row)
            for mode,key in (('raw','segmentation_input_raw'),('adjusted','segmentation_input_adjusted')):
                button=bind_i18n(QPushButton(),key);button.setCheckable(True);button.setMinimumWidth(0)
                bind_i18n(button,'segmentation_input_hint','toolTip')
                self.input_group.addButton(button);row.addWidget(button);self.input_buttons[mode]=button
                button.clicked.connect(lambda checked=False,value=mode:self.set_input_selection(value))
            input_layout.addLayout(row);pick.addWidget(self.input_row)
            f.analysis_context_changed.connect(self.sync_input_selection)
            self.sync_input_selection()
        else:
            self.preview_row=window.brightfield_workspace.preview_toggle_panel
            old=self.preview_row.parentWidget().layout();self._preview_home=(old,old.indexOf(self.preview_row))
            old.removeWidget(self.preview_row);pick.addWidget(self.preview_row)
        self.panel.layout().insertWidget(1,self.selector_wrap)
        # Pin only image selection and task buttons. Display tools go below the
        # active form; neither white balance nor an idle preview row pushes the
        # primary parameters out of view.
        image_group=f.active_image_group if module=='fluoro' else window.brightfield_workspace.active_image_group
        old=image_group.parentWidget().layout();self._image_home=(old,old.indexOf(image_group))
        old.removeWidget(image_group);self.panel.layout().insertWidget(1,image_group)
        self.image_group=image_group
        self.selector.currentIndexChanged.connect(lambda *_args:self.select(self.selector.currentData()))
        for task,button in self.selector.buttons.items():
            button.clicked.connect(lambda checked=False,key=task:self.select(key))
        scroll=self.panel.findChild(QScrollArea)
        self.scroll=scroll
        # White balance belonged above every BF operation; move it to a
        # disclosure rather than allowing it to obscure the selected task.
        if module=='brightfield':
            workspace=window.brightfield_workspace
            display=QWidget();display_layout=QVBoxLayout(display);display_layout.setContentsMargins(0,0,0,0)
            for widget in (workspace.white_balance.widget,):
                widget.parentWidget().layout().removeWidget(widget);display_layout.addWidget(widget)
            section=WorkflowDisclosure('task_display_calibration',display)
            host.layout().addWidget(section)
            self.display_section=section
        self.task_title=QLabel();self.task_title.setObjectName('MeasurementTaskHeading');self.task_title.setWordWrap(True)
        host.layout().insertWidget(0,self.task_title)
        if sys.platform in {'darwin', 'win32'}:
            # The context page originally ended in a stretch. New task pages
            # were appended after it, leaving an expanding gap inside the form.
            for index in range(host.layout().count()-1,-1,-1):
                if host.layout().itemAt(index).spacerItem():host.layout().takeAt(index)
            host.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
            if module=='brightfield':
                workspace=window.brightfield_workspace
                content=workspace.workflow_group.parentWidget().layout()
                content.setStretchFactor(workspace.workflow_group,0)
                workspace.workflow_group.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
                d.brightfield_context_stack.parentWidget().layout().setAlignment(Qt.AlignmentFlag.AlignTop)
            groups=[]
            if module=='fluoro':
                for index in range(f.measure_params_stack.count()):
                    groups.extend(f.measure_params_stack.widget(index).findChildren(QGroupBox,options=Qt.FindChildOption.FindDirectChildrenOnly))
            else:
                groups=[self.shared,d.brightfield_bright_round_cell_group]
            for group in groups:
                # The persistent task heading already identifies these forms.
                # Remove its old binding too, so a language change cannot
                # restore a second title and its empty title-band spacing.
                group.setProperty('i18nTitleKey',None)
                group.setProperty('i18nKey',None)
                group.setProperty('measurementTaskBody',True)
                group.setTitle('')
                group.style().unpolish(group);group.style().polish(group)
        readable_forms(host)
        self.select(self.selector.currentData())
        self.retranslate()

    def _disclose(self,widget,title):
        parent=widget.parentWidget();layout=parent.layout();index=layout.indexOf(widget)
        if index<0:return
        layout.removeWidget(widget);section=WorkflowDisclosure(title,widget)
        layout.insertWidget(index,section)
        self.window.workflow_ui.disclosures.append(section) if hasattr(self.window,'workflow_ui') else None
        return section

    def _disclose_parameters(self,controls,task):
        if getattr(controls,'relation_kind',None):
            readable_forms(controls);return
        # Necessary controls stay in view, advanced thresholds remain one click
        # away. Moving rows preserves each control's owner, value and signals.
        advanced=QWidget();advanced.setProperty('workflowPlain',True)
        form=QFormLayout(advanced);form.setContentsMargins(0,0,0,0)
        widgets=[controls.minimum,controls.aspect] if task=='myotube' else ([] if task=='central' else [controls.minimum,controls.maximum,controls.cellpose_input])
        for widget in widgets:
            parent=widget.parentWidget()
            if parent is None:continue
            old=parent.layout()
            if not isinstance(old,QFormLayout):continue
            row,role=old.getWidgetPosition(widget)
            if row<0:continue
            taken=old.takeRow(row)
            if taken.labelItem:form.addRow(taken.labelItem.widget(),widget)
            else:form.addRow(widget)
        if widgets:
            section=WorkflowDisclosure('task_parameters',advanced)
            controls.body.layout().insertWidget(controls.body.layout().indexOf(controls.hint),section)
        if task=='central':
            controls.go_fibers=bind_i18n(QPushButton(),'task_open_fibers')
            controls.go_nuclei=bind_i18n(QPushButton(),'task_open_nuclei')
            index=controls.body.layout().indexOf(controls.muscle_options)+1
            controls.body.layout().insertWidget(index,controls.go_fibers);controls.body.layout().insertWidget(index+1,controls.go_nuclei)
            controls.go_fibers.clicked.connect(lambda:self.select('fiber'))
            controls.go_nuclei.clicked.connect(lambda:self.select('he'))
        controls.hint.setToolTip(controls.hint.text())
        bind_i18n(controls.hint,'task_hint_'+task)
        readable_forms(controls)

    def _advanced_rows(self,source,widgets,target):
        advanced=QWidget();advanced.setProperty('workflowPlain',True)
        form=QFormLayout(advanced);form.setContentsMargins(0,0,0,0)
        for widget in widgets:
            row,role=source.getWidgetPosition(widget)
            if row<0:continue
            taken=source.takeRow(row)
            if taken.labelItem:form.addRow(taken.labelItem.widget(),widget)
            else:form.addRow(widget)
        section=WorkflowDisclosure('task_parameters',advanced);target.addWidget(section)
        readable_forms(advanced)

    def current_task(self):return self.selector.currentData()

    def select(self,task):
        if task not in self.pages:return
        if self._selecting:
            self._pending_task=task
            return
        self._pending_task=None
        task_changed=task!=getattr(self,'_selected_task',None)
        self._selecting=True
        try:
            self.selector.setCurrentIndex(self.selector.findData(task))
            if self.module=='fluoro':self.preview_row.setVisible(True)
            if self.module=='fluoro':self.input_row.setVisible(task in ('spot_count','dapi','myotube','ai_assisted'))
            for key,controller in self.controllers.items():
                controller.controls.entry.setChecked(key==task)
            if self.module=='fluoro':
                self.window.fluoro_workspace._set_measure_mode(task)
            self.stack.setCurrentWidget(self.pages[task])
            if self.shared is not None:self.shared.setVisible(task in ('cells','area','fiber'))
            if hasattr(self,'task_title'):self.task_title.setText(self.selector.currentText())
            if task in self.controllers:self.controllers[task].sync()
            self.stack._sync_current_page_geometry()
            self.window.workflow_ui.schedule_sync() if hasattr(self.window,'workflow_ui') else None
            if task_changed:self.scroll.verticalScrollBar().setValue(0)
            self._selected_task=task
        finally:self._selecting=False
        pending=self._pending_task
        if pending is not None and pending!=task:
            self.select(pending)

    def primary_action(self):
        task=self.current_task()
        if task in self.controllers:return self.controllers[task].controls.run_button
        d=self.window.data_panel;f=self.window.fluoro_workspace
        if self.module=='brightfield':
            return {'cells':d.brightfield_bright_round_cell_measure_button,'area':d.brightfield_run_analysis_button,'fiber':d.brightfield_ai_object_count_button}[task]
        return {'roi_intensity':self.window.workflow_ui._roi_source,'spot_count':f.count_button,'ai_assisted':f.ai_run_button,
                'line_profile':f.profile_line_button,'colocalization':f.coloc_draw_roi_button}[task]

    def sync_input_selection(self,*_args):
        if self.module!='fluoro':return
        record=self.window.fluoro_workspace._active_image() or {}
        mode=record.get('segmentation_input','raw')
        self.input_buttons['adjusted' if mode=='adjusted' else 'raw'].setChecked(True)
        for button in self.input_buttons.values():button.setEnabled(bool(record))

    def set_input_selection(self,mode):
        workspace=self.window.fluoro_workspace;record=workspace._active_image()
        if record is None:return
        if record.get('segmentation_input','raw')!=mode:
            record['segmentation_input']=mode
            workspace.panel.append_audit('set_segmentation_input',f"{record['uid']}: {mode}")
        self.sync_input_selection()

    def sync_visibility(self,step):
        self.selector_wrap.setVisible(step==2)
        if step!=2 and self.image_group.parentWidget() is self.panel:
            self.panel.layout().removeWidget(self.image_group)
            layout,index=self._image_home;layout.insertWidget(max(0,index),self.image_group)
        elif step==2 and self.image_group.parentWidget() is not self.panel:
            self.image_group.parentWidget().layout().removeWidget(self.image_group)
            self.panel.layout().insertWidget(1,self.image_group)
        if self.module=='brightfield':
            if step!=2 and self.preview_row.parentWidget() is self.selector_wrap:
                self.selector_wrap.layout().removeWidget(self.preview_row)
                layout,index=self._preview_home;layout.insertWidget(max(0,index),self.preview_row)
            elif step==2 and self.preview_row.parentWidget() is not self.selector_wrap:
                self.preview_row.parentWidget().layout().removeWidget(self.preview_row)
                self.selector_wrap.layout().addWidget(self.preview_row)
        if step==2:
            if self.shared is not None:self.shared.setVisible(self.current_task() in ('cells','area','fiber'))
            current=self.current_task()
            if self.stack.currentWidget() is not self.pages[current]:self.select(current)
            self.task_title.setText(self.selector.currentText())

    def retranslate(self):
        for key in self.selector.buttons:
            label='task_'+key if key in ('cells','area','fiber','he','central','dapi','myotube','phenotype','count_ratio','fusion') else 'workflow_task_'+key
            self.selector.setItemText(self.selector.findData(key),tr(label))
        for section in self.panel.findChildren(WorkflowDisclosure):section.retranslate()
        for controller in self.controllers.values():
            if getattr(controller.controls,'relation_kind',None):controller.retranslate()
        self.task_title.setText(self.selector.currentText())
        readable_forms(self.stack)
        from ui.widgets.model_selection_hint import ModelSelectionHint
        for hint in self.panel.findChildren(ModelSelectionHint):hint.refresh()


