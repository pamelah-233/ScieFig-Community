"""One visible run history per measurement task, reusing saved results."""
from PyQt6.QtCore import QObject, QSignalBlocker, QTimer, Qt
from PyQt6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from core.measurement_review_adapter import measurement_identity
from core.utils.i18n import bind_i18n, tr


class MeasurementHistoryView(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName('MeasurementResultsSection')
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,5,10,5);layout.setSpacing(5)
        self.heading=bind_i18n(QLabel(),'measurement_results_title')
        bind_i18n(self.heading,'measurement_run_history','toolTip')
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.table=QTableWidget(0,3)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        from ui.widgets.table_presentation import readable_table
        readable_table(self.table)
        self.table.setMinimumWidth(0)
        layout.addWidget(self.table)
        self.empty=bind_i18n(QLabel(),'measurement_history_empty');self.empty.setWordWrap(True)
        layout.addWidget(self.empty)


class MeasurementHistoryController(QObject):
    def __init__(self,window,presentation):
        super().__init__(window)
        self.presentation=presentation
        self.window=window;self.views={};self._pins={};self._updating=False;self._keys={}
        self._timer=QTimer(self);self._timer.setSingleShot(True);self._timer.timeout.connect(self.sync)
        for module,deck in window.measurement_tasks.items():
            view=MeasurementHistoryView();self.views[module]=view
            self._place_in_workflow(module,view)
            view.table.currentCellChanged.connect(lambda *_,m=module:self.select_row(m))
            view.table.cellClicked.connect(lambda *_,m=module:self.select_row(m))
            deck.selector.currentIndexChanged.connect(self.schedule)
            for controller in deck.controllers.values():controller.preview_changed.connect(self.schedule)
        window._analysis_job_service.job_finished.connect(self.schedule)
        window.fluoro_workspace.analysis_context_changed.connect(self.schedule)
        window.fluoro_workspace.analysis_completed.connect(self.schedule)
        window.canvas_stack.currentChanged.connect(self.schedule)
        self.fixed_views={}
        d=window.data_panel
        # Object details and QC stay with their existing controls; the main
        # result list always follows the parameters in the workflow scroll area.
        bind_i18n(d.ihc_results_group,'measurement_details','title')
        for label in d.wb_context_stack.widget(3).findChildren(QLabel):
            if label.property('i18nTextKey')=='results':bind_i18n(label,'measurement_details')
        for module,widgets in (
            ('ihc',(d.ihc_results_table,)),
            ('wb',(d.wb_density_result_table,d.wb_density_copy_button)),
            ('qpcr',(d.summary,d.copy_summary_button)),
        ):
            view=QWidget();view.setObjectName('MeasurementResultsSection')
            view.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
            layout=QVBoxLayout(view);layout.setContentsMargins(10,5,10,5);layout.setSpacing(5)
            layout.addWidget(bind_i18n(QLabel(),'measurement_results_title'))
            for widget in widgets:
                parent=widget.parentWidget()
                if parent is not None and parent.layout() is not None:parent.layout().removeWidget(widget)
                if isinstance(widget,QTableWidget):
                    widget.setMinimumHeight(70);widget.setMaximumHeight(140)
                    layout.addWidget(widget)
                elif isinstance(widget,QLabel):
                    scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                    scroll.setMinimumHeight(45);scroll.setMaximumHeight(130);scroll.setWidget(widget)
                    layout.addWidget(scroll)
                else:layout.addWidget(widget)
            self.fixed_views[module]=view;self._place_in_workflow(module,view)
        self.sync()

    def _place_in_workflow(self,module,view):
        panel=self.presentation.panels[module]
        scroll=panel.findChild(QScrollArea)
        layout=scroll.widget().layout()
        index=layout.count()
        while index and layout.itemAt(index-1).spacerItem() is not None:
            index-=1
        layout.insertWidget(index,view)

    def sync_visibility(self,module,step):
        stages={'qpcr':1,'wb':3,'ihc':1,'fluoro':2,'brightfield':2}
        for name,view in {**self.views,**self.fixed_views}.items():
            view.setVisible(name==module and step==stages[name])

    def schedule(self,*_args):
        if not self._updating:self._timer.start(0)

    def _panel(self,module):return getattr(self.window,'_active_'+module+'_panel')()

    def _key(self,module,task):
        panel=self._panel(module)
        return id(panel),(panel.active_image() or {}).get('uid'),task

    def rows(self,module,task):
        panel=self._panel(module)
        if task in ('dapi','he','central'):
            kind='muscle' if task=='central' else task
            return [r for r in panel.analysis.get('nuclei_measurements',[]) if r.get('task')==kind]
        if task=='myotube':return panel.analysis.get('myotube_measurements',[])
        if task in ('phenotype','fusion','count_ratio'):
            from ui.controllers.measurement_relations_controller import TYPES
            return [r for r in panel.analysis.get('object_relation_measurements',[]) if r.get('type')==TYPES[task]]
        if task=='cells':return panel.analysis.get('bright_round_cell_diameter_measurements',[])
        if module=='brightfield':
            key='brightfield_ai_object_count_measurements' if task=='fiber' else 'measurements'
            return panel.analysis.get(key,[])
        key={'spot_count':'object_counts','ai_assisted':'fluorescence_ai_assisted_measurements',
             'roi_intensity':'measurements','line_profile':'line_profile_runs','colocalization':'channel_overlap_runs'}[task]
        rows=[];seen=set()
        for source in [panel.analysis,*panel.images()]:
            for row in source.get(key,[]):
                if task=='roi_intensity' and row.get('type')!='roi_intensity':continue
                identity=measurement_identity(row)
                if not identity[1] or identity in seen:continue
                seen.add(identity);rows.append(row)
        return rows

    def selected(self,module,task):
        identity=self._pins.get(self._key(module,task))
        record=next((r for r in self.rows(module,task) if measurement_identity(r)==identity),None) if identity else None
        if record is not None and task in ('dapi','he','myotube'):
            controls=self.window.measurement_tasks[module].controllers[task].controls
            if module=='fluoro' and record.get('channel_index')!=controls.channel.currentData():return None
            method=record.get('params',record.get('parameters',{})).get('method')
            if method and controls.method.currentData()!=method:return None
        return record

    def forget(self,module,task):
        self._pins.pop(self._key(module,task),None);self.schedule()

    def sync(self):
        self._timer.stop()
        if self._updating:return
        w=self.window;module=w.active_module
        if module not in self.views:return
        self._updating=True
        try:
            deck=w.measurement_tasks[module];task=deck.current_task();rows=list(reversed(self.rows(module,task)))
            view=self.views[module];table=view.table
            table.setHorizontalHeaderLabels([tr('image'),tr('measurement_channel'),tr('measurement_history_run')])
            view.table.setVisible(bool(rows));view.empty.setVisible(not rows)
            panel=self._panel(module);images={r.get('uid'):r for r in panel.images()}
            signature=(id(panel),task,(panel.active_image() or {}).get('uid'),
                       tuple((measurement_identity(r),r.get('image_uid')) for r in rows),tr('image'))
            if self._keys.get(module)==signature:return
            self._keys[module]=signature
            chosen=measurement_identity(self.selected(module,task) or {})
            with QSignalBlocker(table):
                table.clearSelection();table.setCurrentCell(-1,-1)
                table.setRowCount(len(rows))
                for index,row in enumerate(rows):
                    image=images.get(row.get('image_uid'),{})
                    channel=row.get('channel_index',row.get('signal_channel_index'))
                    channels=image.get('channels',[])
                    label=channels[channel].get('name',str(channel+1)) if isinstance(channel,int) and 0<=channel<len(channels) else '—'
                    for column,text in enumerate((row.get('image_name') or image.get('source_name','—'),label,'#'+str(len(rows)-index))):
                        item=QTableWidgetItem(str(text));item.setToolTip(str(text))
                        item.setData(Qt.ItemDataRole.UserRole,measurement_identity(row));table.setItem(index,column,item)
                    if measurement_identity(row)==chosen:table.setCurrentCell(index,0)
                header=max(table.horizontalHeader().height(),table.horizontalHeader().sizeHint().height())
                row_height=sum(table.rowHeight(index) for index in range(min(4,len(rows))))
                table.setFixedHeight(min(200,header+row_height+table.horizontalScrollBar().sizeHint().height()+4))
        finally:self._updating=False

    def select_row(self,module):
        if self._updating or self.window.active_module!=module:return
        item=self.views[module].table.currentItem()
        if item is None:return
        w=self.window;deck=w.measurement_tasks[module];task=deck.current_task()
        identity=tuple(item.data(Qt.ItemDataRole.UserRole) or ())
        record=next((r for r in self.rows(module,task) if measurement_identity(r)==identity),None)
        if record is None:return
        self._updating=True
        try:
            panel=self._panel(module);uid=record.get('image_uid')
            if not any(image.get('uid')==uid for image in panel.images()):return
            if module=='fluoro':
                ws=w.fluoro_workspace
                ws._activate_record_image(record,identity[1]);ws.set_step(2)
            else:
                w.select_brightfield_image(uid);w.brightfield_workspace.set_step(2)
            deck.select(task)
            controller=deck.controllers.get(task)
            if controller is not None:
                controls=controller.controls
                if hasattr(controls,'channel') and record.get('channel_index') is not None:
                    with QSignalBlocker(controls.channel):controls.channel.setCurrentIndex(controls.channel.findData(record['channel_index']))
                params=record.get('params',record.get('parameters',record.get('requested_parameters',{})))
                setter=getattr(controls,'set_parameters',None) or controls.restore_parameters
                setter(params)
                if task=='central':controller.sync_sources(record)
                if task in ('phenotype','fusion','count_ratio'):
                    keys={'phenotype':('objects',),'fusion':('fibers','nuclei'),'count_ratio':('numerator','denominator')}[task]
                    for key,combo in zip(keys,(controls.first,controls.second)):
                        combo.setCurrentIndex(combo.findData(record.get('source_measurements',{}).get(key)))
                self._pins[self._key(module,task)]=identity
                controller.sync()
            elif module=='fluoro' and task in ('roi_intensity','spot_count','ai_assisted'):
                ws.select_result_identity(*identity)
            elif module=='brightfield' and task in ('area','fiber'):
                if task=='area':w.data_panel.select_brightfield_measurement(identity[1])
                else:w.data_panel.select_brightfield_ai_measurement(identity[1])
            else:
                self._pins[self._key(module,task)]=identity
                if module=='fluoro':ws._refresh_canvas()
            preview=w.measurement_preview
            preview.restore_preview_mode(module,'overlay')
            if module=='fluoro':ws._preferred_measurement_overlay=True
            preview.sync()
        finally:self._updating=False
        self.schedule()
