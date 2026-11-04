"""Nuclear analysis orchestration; existing panels retain their measurements unchanged."""
from copy import deepcopy
import csv
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import QObject,QEvent,pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFileDialog
from core.image_browser import rgb_to_qimage
from core.plotting.histology_display import qimage_rgb
from core.utils.i18n import tr
from core.utils.recent_directory import recent_directory,remember_file_directory
from ui.widgets.nuclei_count_controls import NucleiCountControls


class NucleiCountController(QObject):
    preview_changed = pyqtSignal()
    def __init__(self,window,module,*,task=None):
        super().__init__(window)
        self.task=task or ('dapi' if module=='fluoro' else 'he')
        self.window=window;self.module=module;self.job_key='central_nuclei' if self.task=='muscle' else 'nuclei_'+module;self.review_dialog=None
        self.controls=NucleiCountControls(module,task=self.task)
        if module=='fluoro':
            host=window.fluoro_workspace.context_stack.widget(2)
            window.fluoro_workspace.active_image_combo.currentIndexChanged.connect(self.sync)
        else:
            host=window.data_panel.brightfield_context_stack.widget(2)
            window.data_panel.brightfield_active_image_combo.currentIndexChanged.connect(self.sync)
        host.layout().insertWidget(0,self.controls)
        self.controls.installEventFilter(self)
        self.controls.run_button.clicked.connect(self.run)
        self.controls.entry.toggled.connect(self.sync)
        self.controls.channel.currentIndexChanged.connect(self.sync)
        if self.task!='muscle':self.controls.method.currentIndexChanged.connect(self._method_changed)
        self.controls.fiber_source.currentIndexChanged.connect(self.sync)
        self.controls.nuclear_source.currentIndexChanged.connect(self.sync)
        self.controls.review_button.clicked.connect(self.open_review)
        self.controls.csv_button.clicked.connect(lambda:self.choose_export('csv'))
        self.controls.overlay_button.clicked.connect(lambda:self.choose_export('png'))
        window._analysis_job_service.busy_changed.connect(self._busy)
        self.last_identity=None

    def panel(self):
        return getattr(self.window,'_active_'+self.module+'_panel')()

    def identity(self):
        panel=self.panel()
        return (id(panel),(panel.active_image() or {}).get('uid'))

    def current_task(self):
        return self.task

    def _method_changed(self):
        record=self.panel().active_image();channel=self.controls.channel.currentData() if self.module=='fluoro' else 'he'
        if record is None or channel is None:return
        methods=record.setdefault('nuclei_methods',{})
        method=self.controls.method.currentData()
        if methods.get(str(channel))!=method:
            methods[str(channel)]=method
            self.panel().append_audit('select_nuclear_method',f"{record['uid']} channel={channel} method={method}")
        self.sync()

    def load_recipe(self):
        names={'watershed':'he_local_contrast_v1_20261021.json','he_round':'he_round_nuclei_v1_20261021.json',
               'he_refined':'he_refined_nuclei_v1_20261022.json','cellpose_cyto3':'he_cyto3_original_v1_20261023.json',
               'cellpose_cyto3_he_pale_v1':'he_cyto3_pale_v1_20261023.json'}
        name=names.get(self.controls.method.currentData())
        if self.module=='fluoro':
            name={'cellpose_nuclei':'dapi_cellpose_nuclei_v1_20261022.json',
                  'cellpose_cyto3':'fluoro_cyto3_weak_nuclei_v1_20261023.json',
                  'cellpose_cyto3_dapi_reviewed_v1':'dapi_cyto3_reviewed_v1_20261023.json'}.get(self.controls.method.currentData())
        if name is None:return
        try:
            recipe=json.loads((self.window.resources_dir/'analysis_presets'/name).read_text(encoding='utf-8'))
            self.controls.set_parameters(recipe['parameters'])
            self.sync()
        except (OSError,ValueError,KeyError) as error:
            self.controls.summary.setText(tr('nuclei_recipe_error').format(error=error))

    def current_result(self):
        history=getattr(self.window,'measurement_history',None)
        selected=history.selected(self.module,'central' if self.task=='muscle' else self.task) if history else None
        if selected is not None:return selected
        uid=(self.panel().active_image() or {}).get('uid')
        return next((r for r in reversed(self.panel().analysis.get('nuclei_measurements',[]))
                     if r.get('image_uid')==uid and r.get('task')==self.current_task()
                     and (r.get('task')!='muscle' or not r.get('source_measurements') or r['source_measurements']==dict(
                         fibers=self.controls.fiber_source.currentData(),nuclei=self.controls.nuclear_source.currentData()))
                     and (self.module!='fluoro' or r.get('channel_index')==self.controls.channel.currentData())
                     and (self.current_task()=='muscle' or r.get('parameters',{}).get('method')==self.controls.method.currentData())),None)

    def input_snapshot(self):
        record=self.panel().active_image() or {}
        if self.current_task()=='muscle':
            fiber,nuclear=self.joint_sources()
            return deepcopy(dict(identity=self.identity(),task='muscle',fiber=fiber,nuclear=nuclear,
                                 peripheral_band=self.controls.band.value()))
        scope=self.controls.roi.currentData()
        return deepcopy(dict(identity=self.identity(),task=self.current_task(),parameters=self.controls.parameters(),
            segmentation_input=self.panel().segmentation_input_context(record['uid']) if self.module=='fluoro' and record.get('uid') else None,
            channel=self.controls.channel.currentData() if self.module=='fluoro' else None,scope=scope,
            roi=record.get('rois' if self.module=='fluoro' else 'roi') if scope=='roi' else None,
            channel_interpretation=record.get('channel_interpretation')))

    def joint_sources(self):
        uid=(self.panel().active_image() or {}).get('uid')
        fiber=next((r for r in self.panel().analysis.get('brightfield_ai_object_count_measurements',[])
                    if r.get('image_uid')==uid and r.get('measurement_uuid')==self.controls.fiber_source.currentData()
                    and r.get('analysis_metadata',{}).get('analysis_kind')=='he_fiber_area'),None)
        nuclear=next((r for r in self.panel().analysis.get('nuclei_measurements',[])
                      if r.get('image_uid')==uid and r.get('task')=='he'
                      and r.get('measurement_uuid')==self.controls.nuclear_source.currentData()),None)
        return fiber,nuclear

    def sync_sources(self,restore_result=None):
        uid=(self.panel().active_image() or {}).get('uid')
        selected=(restore_result or {}).get('source_measurements',{})
        for kind,combo,rows in (
            ('fibers',self.controls.fiber_source,[r for r in self.panel().analysis.get('brightfield_ai_object_count_measurements',[])
                if r.get('image_uid')==uid and r.get('analysis_metadata',{}).get('analysis_kind')=='he_fiber_area']),
            ('nuclei',self.controls.nuclear_source,[r for r in self.panel().analysis.get('nuclei_measurements',[])
                if r.get('image_uid')==uid and r.get('task')=='he'])):
            old=selected.get(kind,combo.currentData())
            combo.blockSignals(True);combo.clear()
            for row in reversed(rows):
                if kind=='fibers':
                    n=row['results']['he_fiber_count']
                    method=tr('he_fiber_method_'+row.get('params',{}).get('he_fiber_segmentation_method','original'))
                else:
                    n=sum(r.get('include_in_statistics',True) for r in row['nuclei'])
                    method=self.controls.method.itemText(self.controls.method.findData(row.get('parameters',{}).get('method')))
                combo.addItem(tr('nuclei_source_item').format(n=n,method=method,id=row['measurement_uuid'][:8]),row['measurement_uuid'])
            index=combo.findData(old);combo.setCurrentIndex(index if index>=0 else (0 if rows else -1))
            combo.blockSignals(False)

    def eventFilter(self,watched,event):
        if watched is self.controls and event.type()==QEvent.Type.Show:self.sync()
        return False

    def sync(self,*_args):
        record=self.panel().active_image()
        identity=self.identity()
        controls_context=(identity,self.current_task())
        restore=controls_context!=getattr(self,'_controls_context',None)
        if identity!=self.last_identity:
            if self.review_dialog is not None and not sip.isdeleted(self.review_dialog):self.review_dialog.close()
            self.last_identity=identity
        if self.module=='fluoro':
            combo=self.controls.channel;old=combo.currentData()
            combo.blockSignals(True);combo.clear()
            for i,channel in enumerate((record or {}).get('channels',[])):
                combo.addItem(str(channel.get('name') or f'Channel {i+1}'),i)
            selected=combo.findData(old) if old is not None and identity==getattr(self,'_channel_identity',None) else -1
            if selected<0:selected=next((i for i in range(combo.count()) if 'dapi' in combo.itemText(i).lower() or 'hoechst' in combo.itemText(i).lower()),-1)
            combo.setCurrentIndex(selected);combo.blockSignals(False);self._channel_identity=identity
            signal_combo=self.controls.signal_channel;old_signal=signal_combo.currentData()
            signal_combo.blockSignals(True);signal_combo.clear()
            for i,channel in enumerate((record or {}).get('channels',[])):signal_combo.addItem(str(channel.get('name') or f'Channel {i+1}'),i)
            signal_index=signal_combo.findData(old_signal)
            signal_combo.setCurrentIndex(signal_index if signal_index>=0 else selected);signal_combo.blockSignals(False)
        latest=None
        if restore:
            latest=next((r for r in reversed(self.panel().analysis.get('nuclei_measurements',[]))
                         if r.get('image_uid')==identity[1] and r.get('task')==self.current_task()),None)
            self._controls_context=controls_context
            if latest is None and record is not None:self.controls.set_parameters({})
            if latest is not None:
                params=dict(latest.get('parameters',{}))
                if self.module=='fluoro':params['object_kind']=latest.get('object_kind','nuclei')
                if latest.get('task')=='muscle':
                    self.controls.band.setValue(round(params.get('peripheral_fraction',.2)*100))
                    params=params.get('nuclear_segmentation',{})
                self.controls.set_parameters(params)
                self.controls.roi.setCurrentIndex(self.controls.roi.findData('roi' if latest.get('roi') else 'full'))
                if self.module=='fluoro':
                    self.controls.channel.blockSignals(True)
                    self.controls.channel.setCurrentIndex(self.controls.channel.findData(latest.get('channel_index')))
                    self.controls.channel.blockSignals(False)
        if self.module=='fluoro' and record is not None:
            channel=self.controls.channel.currentData()
            context=(identity,channel)
            if context!=getattr(self,'_method_context',None):
                if not restore:
                    channel_result=next((r for r in reversed(self.panel().analysis.get('nuclei_measurements',[]))
                        if r.get('image_uid')==identity[1] and r.get('task')==self.current_task() and r.get('channel_index')==channel),None)
                    self.controls.set_parameters((channel_result or {}).get('parameters',{}))
                method=record.get('nuclei_methods',{}).get(str(channel))
                if method is not None:
                    combo=self.controls.method;index=combo.findData(method)
                    if index>=0:
                        combo.blockSignals(True);combo.setCurrentIndex(index);combo.blockSignals(False)
                        self.controls._sync_method()
                self._method_context=context
        if self.module=='brightfield' and self.current_task()=='he' and record is not None and restore:
            method=record.get('nuclei_methods',{}).get('he')
            index=self.controls.method.findData(method)
            if index>=0:
                self.controls.method.blockSignals(True);self.controls.method.setCurrentIndex(index);self.controls.method.blockSignals(False)
                self.controls._sync_method()
        if self.current_task()=='muscle':self.sync_sources(latest)
        review_context=(controls_context,self.controls.channel.currentData() if self.module=='fluoro' else None,
                        (self.controls.fiber_source.currentData(),self.controls.nuclear_source.currentData())
                        if self.current_task()=='muscle' else None,self.controls.method.currentData())
        if review_context!=getattr(self,'_review_context',None):
            if self.review_dialog is not None and not sip.isdeleted(self.review_dialog):self.review_dialog.close()
            self._review_context=review_context
        has_sources=self.current_task()!='muscle' or all(r is not None for r in self.joint_sources())
        self.controls.run_button.setEnabled(record is not None and has_sources and not any(self.window._analysis_job_service.is_busy(key)
            for key in (self.job_key,'import:'+self.module)))
        result=self.current_result()
        for button in (self.controls.review_button,self.controls.csv_button,self.controls.overlay_button):button.setEnabled(result is not None)
        self.controls.summary.setText(tr('nuclei_joint_missing') if self.current_task()=='muscle' and not has_sources and result is None else self.summary_text(result))
        self.preview_changed.emit()

    def _busy(self,key,busy):
        if key=='import:'+self.module:
            if busy:self.controls.run_button.setEnabled(False)
            else:self.sync()
            return
        if key==self.job_key:
            self.controls.body.setEnabled(not busy)
            has_sources=self.current_task()!='muscle' or all(r is not None for r in self.joint_sources())
            self.controls.run_button.setEnabled(not busy and has_sources and self.panel().active_image() is not None)

    @staticmethod
    def summary_text(result):
        if not result:return tr('nuclei_no_result')
        if result['task']=='muscle':
            s=result['summary']
            bins='\n'.join(tr('nuclei_bin').format(k=b['central_nuclei'],n=b['fiber_count'],p=b['percentage']) for b in s['distribution'])
            return tr('nuclei_muscle_summary').format(n=s['included_fibers'],r=s['review_fibers'],e=s['excluded_edge_fibers'],bins=bins,o=s['outside_nuclei'],u=s['uncertain_nuclei'],
                p=s.get('peripheral_review_fibers',0),m=s.get('excluded_manual_fibers',0))
        included=sum(r.get('include_in_statistics',True) for r in result['nuclei'])
        text=tr('nuclei_summary').format(n=included,e=len(result['nuclei'])-included)
        if result.get('signal_classification'):
            if result['signal_classification'].get('stale'):return text+'\n'+tr('relation_stale')
            from collections import Counter
            counts=Counter(r.get('signal_class') for r in result['nuclei'] if r.get('include_in_statistics',True))
            text+='\n'+tr('relation_signal_summary').format(n=included,negative=counts['negative'],weak=counts['weak_positive'],strong=counts['strong_positive'])
        return text

    def run(self):
        from core.image_analysis.nuclei_counting import count_nuclei
        from core.image_analysis.muscle_nuclei import classify_fiber_nuclei,summarize_fiber_nuclei
        panel=self.panel();record=panel.active_image()
        if record is None or any(self.window._analysis_job_service.is_busy(key) for key in (self.job_key,'import:'+self.module)):return
        identity=self.identity();task=self.current_task();params=self.controls.parameters()
        object_kind=params.pop('object_kind','nuclei')
        classification_params=params.pop('signal_classification',None)
        project=self.window.project;inputs=self.input_snapshot()
        roi_snapshot=None;roi_mask=None;channel_index=None
        band=self.controls.band.value()/100
        fiber,nuclear=deepcopy(self.joint_sources()) if task=='muscle' else (None,None)
        try:
            if task=='muscle':
                if fiber is None or nuclear is None:raise ValueError(tr('nuclei_joint_missing'))
                fiber_roi=fiber.get('roi') or None
                if fiber_roi and fiber_roi.get('mode')=='whole_image':fiber_roi=None
                roi_snapshot=nuclear.get('roi') or None
                if roi_snapshot and roi_snapshot.get('mode')=='whole_image':roi_snapshot=None
                if fiber_roi!=roi_snapshot:raise ValueError(tr('nuclei_joint_scope_mismatch'))
            elif self.module=='fluoro':
                from core.plotting.fluoro_render import RGB_COMPOSITE_MODE
                channel_index=self.controls.channel.currentData()
                if channel_index is None or record.get('channel_interpretation',{}).get('mode')==RGB_COMPOSITE_MODE:
                    raise ValueError(tr('nuclei_choose_channel'))
                source=panel.segmentation_input_data(record['uid'])[channel_index].copy()
                measurement_source=panel.image_data(record['uid'])[channel_index].copy()
                if classification_params:
                    signal_channel=classification_params.get('channel_index')
                    if signal_channel is None:raise ValueError(tr('nuclei_choose_channel'))
                    classification_signal=panel.image_data(record['uid'])[signal_channel].copy()
                if self.controls.roi.currentData()=='roi':
                    rois=[r for r in record.get('rois',[]) if r.get('channel_index',channel_index)==channel_index]
                    if not rois:raise ValueError(tr('nuclei_choose_roi'))
                    roi_snapshot=deepcopy(rois[-1]);roi_mask=panel._roi_mask(source.shape,roi_snapshot['type'],roi_snapshot['geometry'])
            else:
                _,source=panel._load_image_rgb(record['uid']);source=source.copy()
                if self.controls.roi.currentData()=='roi':
                    roi_snapshot=panel._resolve_roi_snapshot(record)
                    roi_mask=panel._build_roi_mask(source.shape[:2],roi_snapshot)
                    if roi_mask is None:raise ValueError(tr('nuclei_choose_roi'))
        except Exception as error:
            self.controls.summary.setText(str(error));return
        def work():
            if task=='muscle':
                fiber_labels=panel._he_fiber_area_labels_for_measurement(fiber)
                result=classify_fiber_nuclei(fiber_labels,nuclei=nuclear['nuclei'],peripheral_fraction=band)
                joint_roi=panel._build_roi_mask(fiber_labels.shape,roi_snapshot) if roi_snapshot else None
                if joint_roi is not None:
                    import cv2
                    for row in result['fibers']:
                        mask=np.zeros(joint_roi.shape,np.uint8)
                        cv2.drawContours(mask,[np.asarray(row['contour'],np.int32)],-1,1,-1)
                        from scipy.ndimage import binary_erosion
                        if np.any(mask.astype(bool) & (joint_roi & ~binary_erosion(joint_roi))):
                            row.update(border_truncated=True,include_in_statistics=False)
                result['summary']=summarize_fiber_nuclei(result['fibers'],result['nuclei'])
                result['parameters'].update(fiber_segmentation=fiber['params'],nuclear_segmentation=nuclear['parameters'])
                result['fiber_provenance']=fiber['analysis_metadata']
                result['source_measurements']=dict(fibers=fiber['measurement_uuid'],nuclei=nuclear['measurement_uuid'])
                provenance=nuclear['provenance']
            else:
                extra=dict(measurement_source=measurement_source) if self.module=='fluoro' else {}
                counted=count_nuclei(source,stain='dapi' if task=='dapi' else 'he',roi_mask=roi_mask,**params,**extra)
                result=dict(nuclei=counted['nuclei'],parameters=counted['parameters'])
                from core.image_analysis.cellpose_backend import pack_instance_labels
                result['instance_labels']=pack_instance_labels(counted['labels'])
                if classification_params:
                    from core.image_analysis.object_phenotyping import classify_object_signal
                    options={k:v for k,v in classification_params.items() if k!='channel_index'}
                    classified=classify_object_signal(counted['labels'],classification_signal,roi_mask=roi_mask,**options)
                    lookup={r['object_id']:r for r in classified['objects']}
                    for row in result['nuclei']:
                        for key in ('signal_class','background_corrected_mean','mean_signal'):row[key]=lookup[row['object_id']][key]
                    result['signal_classification']=dict(parameters=deepcopy(classification_params),summary=classified['summary'],signal_source='raw')
                    result['parameters']['signal_classification']=deepcopy(classification_params)
                provenance=counted['provenance']
                for row in result['nuclei']:
                    row['classification']='included' if row['include_in_statistics'] else 'excluded'
                    row['automatic_classification']=row['classification']
            result.update(task=task,image_uid=record['uid'],image_name=record.get('source_name',''),
                          source_hash=record.get('source_hash'),channel_index=channel_index,
                          roi=roi_snapshot,provenance=provenance,object_kind=object_kind)
            if self.module=='fluoro':result['segmentation_input']=deepcopy(inputs['segmentation_input'])
            return result
        self.controls.summary.setText(tr('nuclei_running'))
        self.window._analysis_job_service.start(key=self.job_key,work=work,on_success=self.accept_result,
            on_error=lambda error:self.controls.summary.setText(str(error)),
            is_current=lambda:self.window.project is project and self.input_snapshot()==inputs,
            on_stale=lambda:self.controls.summary.setText(tr('nuclei_stale')))

    def accept_result(self,result):
        if result['image_uid']!=(self.panel().active_image() or {}).get('uid'):return
        result=deepcopy(result);result['measurement_uuid']=str(uuid4())
        self.panel().analysis.setdefault('nuclei_measurements',[]).append(result)
        history=getattr(self.window,'measurement_history',None)
        if history:history.forget(self.module,'central' if self.task=='muscle' else self.task)
        self.panel().append_audit('count_'+result['task']+'_nuclei',result['measurement_uuid'])
        self.sync()

    def reclassify(self,object_id,category,fiber_id=None):
        result=self.current_result()
        if result is None:return
        allowed={'central','peripheral','outside','uncertain','excluded'} if result['task']=='muscle' else {'included','excluded'}
        if category not in allowed:raise ValueError('Invalid nuclear classification.')
        if category in ('central','peripheral') and fiber_id not in {r['fiber_id'] for r in result['fibers']}:
            raise ValueError(tr('nuclei_invalid_assignment'))
        row=next(r for r in result['nuclei'] if r['object_id']==object_id)
        row.update(classification=category,include_in_statistics=category!='excluded',reviewed=True)
        if result['task']=='muscle':
            from core.image_analysis.muscle_nuclei import summarize_fiber_nuclei
            row['assignment_reason']='manual_review'
            row['fiber_id']=fiber_id if category in ('central','peripheral') else None
            if category in ('central','peripheral'):row['candidate_fiber_ids']=[fiber_id]
            if category=='uncertain':row['central_candidate_fiber_ids']=list(row.get('candidate_fiber_ids',[]))
            result['summary']=summarize_fiber_nuclei(result['fibers'],result['nuclei'])
        self.panel().append_audit('review_nucleus',f"{result['measurement_uuid']} {object_id} {category} fiber={fiber_id}")
        self.sync();self._refresh_review()

    def set_fiber_included(self,fiber_id,included):
        from core.image_analysis.muscle_nuclei import summarize_fiber_nuclei
        result=self.current_result()
        row=next(r for r in result['fibers'] if r['fiber_id']==fiber_id)
        row.update(include_in_statistics=bool(included),reviewed=True)
        result['summary']=summarize_fiber_nuclei(result['fibers'],result['nuclei'])
        self.panel().append_audit('review_fiber',f"{result['measurement_uuid']} {fiber_id} included={included}")
        self.sync();self._refresh_review()

    def source_rgb(self):
        result=self.current_result()
        if result is None:raise ValueError(tr('nuclei_no_result'))
        if self.module=='brightfield':rgb=qimage_rgb(self.panel().display_qimage(result['image_uid']))
        else:
            plane=self.panel().image_data(result['image_uid'])[result['channel_index']].astype(float)
            low,high=np.percentile(plane,[1,99.8]);display=np.uint8(np.clip((plane-low)/max(high-low,1),0,1)*255)
            rgb=np.stack([np.zeros_like(display),np.zeros_like(display),display],axis=-1)
        return rgb

    def overlay_rgb(self):
        from core.image_analysis.muscle_nuclei import nuclear_overlay
        return nuclear_overlay(self.source_rgb(),self.current_result())

    def open_review(self):
        if self.current_result() is None:return
        from ui.widgets.nuclei_review_dialog import NucleiReviewDialog
        if self.review_dialog is None or sip.isdeleted(self.review_dialog):self.review_dialog=NucleiReviewDialog(self)
        self.review_dialog.refresh();self.review_dialog.show();self.review_dialog.raise_()
        return self.review_dialog

    def open_geometry_review(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QDialog
        from core.image_analysis.nuclei_counting import nuclear_signal
        from core.nuclear_geometry_review import nuclear_review_record,apply_nuclear_geometry,nuclear_source_from_joint
        from ui.dialogs.object_review_dialog import ObjectReviewDialog
        current=self.current_result()
        if current is None:return
        original=deepcopy(current);panel=self.panel();uid=original['image_uid']
        if self.module=='brightfield':signal=nuclear_signal(panel._load_image_rgb(uid)[1],'he')
        else:signal=panel.image_data(uid)[original['channel_index']].copy()
        record=nuclear_review_record(original)
        record['measurement_scale_bar']=panel.measurement_scale_bar(uid)
        dialog=ObjectReviewDialog(record,rgb_to_qimage(self.source_rgb()),self.review_dialog or self.window,intensity_image=signal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if self.review_dialog is not None:self.review_dialog.finished.connect(dialog.reject)
        def finish(code):
            if code!=QDialog.DialogCode.Accepted or dialog.applied_record is None:return
            try:
                if self.panel() is not panel or self.current_result()!=original:
                    raise ValueError(tr('nuclei_geometry_stale'))
                labels=None;source=None
                if original['task']=='muscle':
                    fiber,source=self.joint_sources()
                    if fiber is None:raise ValueError(tr('nuclei_joint_missing'))
                    labels=panel._he_fiber_area_labels_for_measurement(fiber)
                corrected=apply_nuclear_geometry(original,dialog.applied_record,fiber_labels=labels)
                if source is not None:
                    nuclear=nuclear_source_from_joint(source,corrected)
                    panel.analysis['nuclei_measurements'].append(nuclear)
                    corrected['source_measurements']['nuclei']=nuclear['measurement_uuid']
                    self.sync_sources(corrected)
                self.accept_result(corrected);self._refresh_review()
                if source is not None:self.open_review()
            except Exception as error:
                self.controls.summary.setText(str(error))
                if self.review_dialog is not None:self.review_dialog.status.setText(str(error))
        dialog.finished.connect(finish);dialog.show();dialog.raise_()
        return dialog

    def _refresh_review(self):
        if self.review_dialog is not None and not sip.isdeleted(self.review_dialog) and self.review_dialog.isVisible():self.review_dialog.refresh()

    def choose_export(self,kind):
        filename,_=QFileDialog.getSaveFileName(self.window,tr('nuclei_export_csv' if kind=='csv' else 'nuclei_export_overlay'),
                                              str(Path(recent_directory())/('nuclei.'+kind)),f'{kind.upper()} (*.{kind})')
        if not filename:return
        path=Path(filename).with_suffix('.'+kind)
        try:
            self.export_csv(path) if kind=='csv' else self.export_overlay(path)
            remember_file_directory(path)
        except Exception as error:self.controls.summary.setText(str(error))

    @staticmethod
    def _csv(path,rows,fields):
        with Path(path).open('w',newline='',encoding='utf-8-sig') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)

    def export_csv(self,path):
        result=self.current_result()
        if result is None:raise ValueError(tr('nuclei_no_result'))
        rows=[{**r,'measurement_uuid':result['measurement_uuid'],'image_name':result.get('image_name',''),
               'task':result['task'],'channel_index':result.get('channel_index'),'parameters':json.dumps(result['parameters']),
               'candidate_fiber_ids':json.dumps(r.get('candidate_fiber_ids',[])),
               'provenance':json.dumps(result['provenance'])} for r in result['nuclei']]
        self._csv(path,rows,['object_id','classification','automatic_classification','fiber_id','center_x_px','center_y_px',
                             'area_px','mean_raw_signal','signal_class','mean_signal','background_corrected_mean','border_truncated','include_in_statistics','reviewed',
                             'measurement_uuid','image_name','task','channel_index','parameters','provenance',
                             'overlap_fraction','nuclear_edge_distance_px','normalized_nuclear_edge_distance',
                             'candidate_fiber_ids','assignment_reason','automatic_assignment_reason'])
        if result['task']=='muscle':
            path=Path(path)
            distribution=[{**r,'included_fibers':result['summary']['included_fibers']} for r in result['summary']['distribution']]
            self._csv(path.with_name(path.stem+'_distribution.csv'),distribution,['central_nuclei','fiber_count','percentage','included_fibers'])
            fibers=[{**r,'included_in_central_distribution':bool(r.get('include_in_statistics',True) and not r.get('uncertain_central_nuclei',r.get('uncertain_nuclei',0)))} for r in result['fibers']]
            self._csv(path.with_name(path.stem+'_fibers.csv'),fibers,['fiber_id','central_nuclei','peripheral_nuclei','uncertain_nuclei','uncertain_central_nuclei','area_px','border_truncated','include_in_statistics','included_in_central_distribution'])

    def export_overlay(self,path):
        if not rgb_to_qimage(self.overlay_rgb()).save(str(path),'PNG'):raise OSError('Could not save nuclear overlay.')

    def retranslate(self):
        self.controls.retranslate();self.sync()
        from core.utils.i18n import bind_i18n
        bind_i18n(self.controls.hint,'task_hint_central' if self.task=='muscle' else 'task_hint_he' if self.task=='he' else 'task_hint_dapi')
        if self.review_dialog is not None and not sip.isdeleted(self.review_dialog):self.review_dialog.retranslate()

