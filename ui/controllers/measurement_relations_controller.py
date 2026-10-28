"""First-level, image-local object classification, count ratio and fusion workflows."""
from copy import deepcopy
import csv, json
from pathlib import Path
from uuid import uuid4
import numpy as np
from PyQt6.QtCore import QObject, QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QPushButton, QComboBox,
    QDoubleSpinBox, QSpinBox, QLabel, QFileDialog, QDialog, QTableWidget, QTableWidgetItem, QHeaderView)
from core.utils.i18n import tr, bind_i18n, retranslate_combo
from core.measurement_relations import (measurement_id, classify_measurement,
    measurement_count_ratio, measurement_fusion_index)

TYPES = dict(phenotype='object_classification', count_ratio='count_ratio', fusion='fusion_index')


class RelationControls(QWidget):
    def __init__(self, kind):
        super().__init__(); self.kind=kind; self.relation_kind=kind
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.entry=bind_i18n(QPushButton(),'task_'+kind); self.entry.setCheckable(True); layout.addWidget(self.entry)
        self.body=QWidget(); body=QVBoxLayout(self.body); body.setContentsMargins(0,4,0,4)
        form=QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows); body.addLayout(form)
        self.first=QComboBox(); self.second=QComboBox()
        first_key={'phenotype':'relation_objects','count_ratio':'relation_numerator','fusion':'relation_fibers'}[kind]
        form.addRow(bind_i18n(QLabel(),first_key),self.first)
        if kind!='phenotype':
            form.addRow(bind_i18n(QLabel(),'relation_denominator' if kind=='count_ratio' else 'relation_nuclei'),self.second)
        self.channel=QComboBox(); self.region=QComboBox()
        self.region.addItem(tr('relation_object_region'),'object'); self.region.addItem(tr('relation_ring'),'perinuclear_ring')
        self.positive=QDoubleSpinBox(); self.strong=QDoubleSpinBox(); self.background=QDoubleSpinBox()
        for spin in (self.positive,self.strong,self.background):spin.setRange(0,1e12);spin.setDecimals(3)
        self.positive.setValue(100); self.strong.setValue(500)
        self.ring=QDoubleSpinBox(); self.ring.setRange(.5,500); self.ring.setValue(5);self.ring.setSuffix(' px')
        if kind=='phenotype':
            for key,widget in [('relation_channel',self.channel),('relation_region',self.region),
                               ('relation_ring_width',self.ring),('relation_background',self.background),
                               ('relation_positive',self.positive),('relation_strong',self.strong)]:
                form.addRow(bind_i18n(QLabel(),key),widget)
            self.region.currentIndexChanged.connect(lambda:self.ring.setEnabled(self.region.currentData()=='perinuclear_ring'))
            self.ring.setEnabled(False)
        self.min_nuclei=QSpinBox(); self.min_nuclei.setRange(2,100); self.min_nuclei.setValue(2)
        self.edge=QComboBox();self.edge.addItem(tr('relation_edge_exclude'),'exclude');self.edge.addItem(tr('relation_edge_visible'),'visible')
        if kind=='fusion':
            form.addRow(bind_i18n(QLabel(),'relation_min_nuclei'),self.min_nuclei)
            form.addRow(bind_i18n(QLabel(),'relation_edge_policy'),self.edge)
        self.hint=bind_i18n(QLabel(),'relation_hint_'+kind);self.hint.setWordWrap(True);body.addWidget(self.hint)
        self.run_button=bind_i18n(QPushButton(),'relation_run');self.run_button.setObjectName('PrimaryButton');body.addWidget(self.run_button)
        self.summary=bind_i18n(QLabel(),'nuclei_no_result');self.summary.setWordWrap(True);body.addWidget(self.summary)
        self.review_button=bind_i18n(QPushButton(),'relation_table');body.addWidget(self.review_button)
        self.csv_button=bind_i18n(QPushButton(),'relation_export');body.addWidget(self.csv_button)
        layout.addWidget(self.body)

    def parameters(self):
        if self.kind=='phenotype':
            return dict(channel_index=self.channel.currentData(), positive_threshold=self.positive.value(),
                        strong_threshold=self.strong.value(),background=self.background.value(),
                        region=self.region.currentData(),ring_width_px=self.ring.value())
        if self.kind=='fusion':return dict(min_nuclei=self.min_nuclei.value(),edge_policy=self.edge.currentData())
        return {}

    def restore_parameters(self, params):
        for key,widget in [('positive_threshold',self.positive),('strong_threshold',self.strong),
            ('background',self.background),('ring_width_px',self.ring),('min_nuclei',self.min_nuclei)]:
            if key in params:widget.setValue(params[key])
        for key,widget in [('channel_index',self.channel),('region',self.region),('edge_policy',self.edge)]:
            if key in params:widget.setCurrentIndex(widget.findData(params[key]))

    def retranslate(self):
        retranslate_combo(self.region,dict(object=tr('relation_object_region'),perinuclear_ring=tr('relation_ring')))
        retranslate_combo(self.edge,dict(exclude=tr('relation_edge_exclude'),visible=tr('relation_edge_visible')))


class MeasurementRelationsController(QObject):
    preview_changed = pyqtSignal()
    def __init__(self, window, kind):
        super().__init__(window);self.window=window;self.kind=kind;self.controls=RelationControls(kind)
        self.job_key='measurement_relation_'+kind;self._identity=None;self._syncing=False;self._drafts={}
        self.controls.installEventFilter(self);self.controls.run_button.clicked.connect(self.run)
        self.controls.entry.toggled.connect(self.sync)
        self.controls.review_button.clicked.connect(self.open_review);self.controls.csv_button.clicked.connect(self.choose_export)
        window.fluoro_workspace.active_image_combo.currentIndexChanged.connect(self.sync)
        window.fluoro_workspace.analysis_context_changed.connect(self.sync)
        window._analysis_job_service.busy_changed.connect(self.busy_changed)

    def panel(self):return self.window._active_fluoro_panel()

    def eventFilter(self, watched, event):
        if watched is self.controls and event.type()==QEvent.Type.Show:self.sync()
        return False

    def records(self):
        panel=self.panel();uid=(panel.active_image() or {}).get('uid');rows=[]
        for key in ('nuclei_measurements','myotube_measurements','fluorescence_ai_assisted_measurements','measurements'):
            for source in panel.analysis.get(key,[]):
                if source.get('image_uid')!=uid or not measurement_id(source) or source.get('task')=='muscle':continue
                if not ('nuclei' in source or isinstance(source.get('results'),list) or
                        isinstance(source.get('object_table'),list) or isinstance(source.get('detected_spots'),list)):continue
                rows.append((key,source))
        return rows

    def current_result(self):
        history=getattr(self.window,'measurement_history',None)
        selected=history.selected('fluoro',self.kind) if history else None
        if selected is not None:return selected
        uid=(self.panel().active_image() or {}).get('uid')
        return next((r for r in reversed(self.panel().analysis.get('object_relation_measurements',[]))
                     if r.get('image_uid')==uid and r.get('type')==TYPES[self.kind]),None)

    def is_fresh(self,result):
        if result is None:return False
        current={measurement_id(r):r for key in ('nuclei_measurements','myotube_measurements',
            'fluorescence_ai_assisted_measurements','measurements') for r in self.panel().analysis.get(key,[])}
        return all(current.get(identifier)==snapshot for identifier,snapshot in result.get('source_snapshots',{}).items())

    def sync(self,*_args):
        if self._syncing:return
        self._syncing=True
        try:
            panel=self.panel();record=panel.active_image() or {};identity=(id(panel),record.get('uid'))
            changed=identity!=self._identity
            if changed and self._identity is not None:self._drafts[self._identity]=self.controls.parameters()
            self._identity=identity;rows=self.records();current=self.current_result()
            previous_sources=(current or {}).get('source_measurements',{}) if changed else {}
            for position,combo in enumerate((self.controls.first,self.controls.second)):
                old=combo.currentData()
                source_key=({'phenotype':['objects','unused'],'count_ratio':['numerator','denominator'],'fusion':['fibers','nuclei']}[self.kind])[position]
                wanted=previous_sources.get(source_key,old)
                combo.blockSignals(True);combo.clear()
                for key,source in reversed(rows):
                    if self.kind=='fusion' and key!=('myotube_measurements' if position==0 else 'nuclei_measurements'):continue
                    if self.kind=='fusion' and position==1 and source.get('object_kind','nuclei')!='nuclei':continue
                    channel=source.get('channel_index');name=''
                    if isinstance(channel,int) and channel<len(record.get('channels',[])):name=record['channels'][channel].get('name','')
                    task=tr('task_myotube' if key=='myotube_measurements' else 'task_dapi' if key=='nuclei_measurements' else 'relation_objects')
                    combo.addItem(f'{task} · {name} · {str(measurement_id(source))[:8]}',measurement_id(source))
                index=combo.findData(wanted);combo.setCurrentIndex(index if index>=0 else (0 if combo.count() else -1));combo.blockSignals(False)
            if changed:
                previous_channel=self.controls.channel.currentText()
                self.controls.channel.blockSignals(True);self.controls.channel.clear()
                for index,channel in enumerate(record.get('channels',[])):self.controls.channel.addItem(channel.get('name',str(index+1)),index)
                params=self._drafts.get(identity,(current or {}).get('requested_parameters',{}))
                if 'channel_index' not in params:
                    index=self.controls.channel.findText(previous_channel)
                    self.controls.channel.setCurrentIndex(index if index>=0 else (0 if self.controls.channel.count()==1 else -1))
                self.controls.restore_parameters(params);self.controls.channel.blockSignals(False)
            ready=self.controls.first.currentData() is not None and (self.kind=='phenotype' or self.controls.second.currentData() is not None)
            self.controls.run_button.setEnabled(bool(ready) and not self.window._analysis_job_service.is_busy(self.job_key))
            for button in (self.controls.review_button,self.controls.csv_button):button.setEnabled(current is not None)
            self.controls.csv_button.setEnabled(current is not None and self.is_fresh(current))
            self.controls.summary.setText(self.summary_text(current))
        finally:self._syncing=False
        self.preview_changed.emit()

    def snapshot(self):
        return deepcopy(dict(identity=self._identity,first=self.controls.first.currentData(),second=self.controls.second.currentData(),
                             parameters=self.controls.parameters()))

    def run(self):
        if self.window._analysis_job_service.is_busy(self.job_key):return
        self.sync();snapshot=self.snapshot();panel=self.panel();record=deepcopy(panel.active_image());project=self.window.project
        if not record:return
        rows={measurement_id(r):r for _,r in self.records()}
        first=deepcopy(rows.get(snapshot['first']));second=deepcopy(rows.get(snapshot['second']))
        if first is None or (self.kind!='phenotype' and second is None):return
        params=deepcopy(snapshot['parameters']);data=panel.image_data(record['uid']);shape=data.shape[1:]
        channel=params.pop('channel_index',None)
        if self.kind=='phenotype' and channel is None:return
        signal=data[channel].copy() if self.kind=='phenotype' else None
        def work():
            if self.kind=='phenotype':result=classify_measurement(first,signal,channel_index=channel,parameters=params)
            elif self.kind=='fusion':result=measurement_fusion_index(first,second,shape,**params)
            else:result=measurement_count_ratio(first,second,shape)
            result.update(measurement_uuid=str(uuid4()),requested_parameters=deepcopy(snapshot['parameters']))
            result['source_snapshots']={measurement_id(r):deepcopy(r) for r in (first,second) if r is not None
                                       and (r is first or self.kind!='phenotype')}
            return result
        def accept(result):
            panel.analysis.setdefault('object_relation_measurements',[]).append(result)
            history=getattr(self.window,'measurement_history',None)
            if history:history.forget('fluoro',self.kind)
            panel.append_audit(TYPES[self.kind],result['measurement_uuid']);self.sync()
        self.controls.summary.setText(tr('nuclei_running'))
        self.window._analysis_job_service.start(key=self.job_key,work=work,on_success=accept,
            on_error=lambda error:self.controls.summary.setText(str(error)),
            is_current=lambda:self.window.project is project and self.panel() is panel and self.snapshot()==snapshot
                and all(dict((measurement_id(r),r) for _,r in self.records()).get(measurement_id(original))==original
                        for original in (first,second) if original is not None and (original is first or self.kind!='phenotype')),
            on_stale=lambda:self.controls.summary.setText(tr('nuclei_stale')))

    def busy_changed(self,key,busy):
        if key!=self.job_key:return
        self.controls.body.setEnabled(not busy)
        if not busy:self.sync()

    def summary_text(self,result):
        if result is None:return tr('nuclei_no_result')
        if not self.is_fresh(result):return tr('relation_stale_source')
        s=result['summary']
        if result['type']=='object_classification':
            value=s['positive_percent'];text=tr('relation_signal_summary').format(n=s['eligible_objects'],negative=s['negative'],weak=s['weak_positive'],strong=s['strong_positive'])
        elif result['type']=='fusion_index':
            value=s['fusion_index_percent'];text=tr('relation_fusion_summary').format(n=s['eligible_nuclei'],fused=s['fused_nuclei'],fibers=s['multinucleated_fibers'])
        else:value=s['percent'];text=f"{s['numerator']} / {s['denominator']}"
        return tr('relation_saved_result')+'\n'+text+'\n'+(f'{value:.2f}%' if value is not None else tr('relation_undefined'))

    def open_review(self):
        result=self.current_result()
        if result is None:return
        dialog=QDialog(self.window);dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(tr('task_'+self.kind));dialog.resize(1000,650);layout=QVBoxLayout(dialog)
        caption=QLabel(self.summary_text(result));caption.setWordWrap(True);layout.addWidget(caption)
        rows=result.get('objects',result.get('fibers',[])) or [result['summary']]
        fields=([k for k in rows[0] if 'contour' not in k] if rows else [])
        table=QTableWidget(len(rows),len(fields));table.setHorizontalHeaderLabels(fields)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column in range(len(fields)):table.setColumnWidth(column,155)
        for i,row in enumerate(rows):
            for j,key in enumerate(fields):table.setItem(i,j,QTableWidgetItem(str(row.get(key,''))))
        layout.addWidget(table);dialog.show();self._dialog=dialog
        return dialog

    def choose_export(self):
        path,_=QFileDialog.getSaveFileName(self.window,tr('relation_export'),self.kind+'.csv','CSV (*.csv)')
        if path:self.export_csv(Path(path).with_suffix('.csv'))

    def export_csv(self,path):
        result=self.current_result()
        if result is None:raise ValueError(tr('nuclei_no_result'))
        if not self.is_fresh(result):raise ValueError(tr('relation_stale_source'))
        path=Path(path)
        def write(target,rows):
            fields=list(dict.fromkeys(key for row in rows for key in row))
            with target.open('w',newline='',encoding='utf-8-sig') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
                for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
        latest={}
        for item in self.panel().analysis.get('object_relation_measurements',[]):
            if item['type']==TYPES[self.kind]:latest[item['image_uid']]=item
        write(path,[dict(image_uid=r['image_uid'],image_name=r.get('image_name',''),measurement_uuid=r['measurement_uuid'],
                         parameters=r.get('requested_parameters',{}),source_measurements=r['source_measurements'],
                         result_status='current' if self.is_fresh(r) else 'stale',
                         **(r['summary'] if self.is_fresh(r) else {})) for r in latest.values()])
        for key in ('objects','fibers','nuclei'):
            if key in result:write(path.with_name(path.stem+'_'+key+'.csv'),result[key])

    def retranslate(self):self.controls.retranslate();self.sync()

