"""Independent image/channel-local muscle-tube jobs; shared review and exports."""
from copy import deepcopy
import csv,json
from pathlib import Path
from uuid import uuid4
import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import QObject,QEvent,pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QDialog,QFileDialog
from core.image_browser import rgb_to_qimage
from core.utils.i18n import tr
from ui.widgets.myotube_controls import MyotubeControls


class MyotubeController(QObject):
    preview_changed = pyqtSignal()
    def __init__(self,window,module):
        super().__init__(window)
        self.window=window;self.module=module;self.job_key='myotube_'+module
        self.controls=MyotubeControls(module);self.review_dialog=None
        self._context=None;self._image_context=None;self._drafts={};self._syncing=False
        if module=='fluoro':
            host=window.fluoro_workspace.context_stack.widget(2)
            window.fluoro_workspace.active_image_combo.currentIndexChanged.connect(self.sync)
            window.fluoro_workspace.analysis_context_changed.connect(self.sync)
        else:
            host=window.data_panel.brightfield_context_stack.widget(2)
            window.data_panel.brightfield_active_image_combo.currentIndexChanged.connect(self.sync)
        host.layout().insertWidget(0,self.controls);self.controls.installEventFilter(self)
        self.controls.entry.toggled.connect(self._entry)
        self.controls.channel.currentIndexChanged.connect(self.sync)
        if module=='brightfield':self.controls.method.currentIndexChanged.connect(self.sync)
        self.controls.run_button.clicked.connect(self.run)
        self.controls.review_button.clicked.connect(self.open_review)
        self.controls.csv_button.clicked.connect(lambda:self.choose_export('csv'))
        self.controls.excel_button.clicked.connect(lambda:self.choose_export('xlsx'))
        self.controls.overlay_button.clicked.connect(lambda:self.choose_export('png'))
        window.nuclei_controllers[module].controls.entry.toggled.connect(
            lambda checked:self.controls.entry.setChecked(False) if checked else None)
        window._analysis_job_service.busy_changed.connect(self._busy)

    def panel(self):return getattr(self.window,'_active_'+self.module+'_panel')()

    def current_result(self):
        history=getattr(self.window,'measurement_history',None)
        selected=history.selected(self.module,'myotube') if history else None
        if selected is not None:return selected
        panel=self.panel();uid=(panel.active_image() or {}).get('uid')
        channel=self.controls.channel.currentData() if self.module=='fluoro' else None
        return next((r for r in reversed(panel.analysis.get('myotube_measurements',[]))
            if r.get('image_uid')==uid and r.get('channel_index')==channel
            and (self.module!='brightfield' or r.get('params',r.get('settings',{})).get('method','cyto3')==self.controls.method.currentData())),None)

    def _entry(self,checked):
        if checked:self.window.nuclei_controllers[self.module].controls.entry.setChecked(False)
        else:
            self._clear_overlay()
            if self.module=='brightfield':self.window._refresh_brightfield_preview(self.panel())
        self.sync()

    def eventFilter(self,watched,event):
        if watched is self.controls and event.type()==QEvent.Type.Show:self.sync()
        return False

    def sync(self,*_args):
        if self._syncing:return
        self._syncing=True
        try:
            panel=self.panel();record=panel.active_image() or {};uid=record.get('uid')
            image_context=(id(panel),uid)
            image_changed=image_context!=self._image_context
            if image_changed and self.module=='brightfield':
                latest=next((r for r in reversed(panel.analysis.get('myotube_measurements',[])) if r.get('image_uid')==uid),{})
                method=record.get('myotube_method',latest.get('params',{}).get('method','cyto3'))
                combo=self.controls.method;combo.blockSignals(True)
                combo.setCurrentIndex(max(0,combo.findData(method)));combo.blockSignals(False)
            if image_context!=self._image_context and self.module=='fluoro':
                combo=self.controls.channel;combo.blockSignals(True);combo.clear()
                for index,channel in enumerate(record.get('channels',[])):
                    name=channel.get('name') or f'Channel {index+1}'
                    if not any(token in name.lower() for token in ('dapi','hoechst')):combo.addItem(name,index)
                latest=next((r for r in reversed(panel.analysis.get('myotube_measurements',[])) if r['image_uid']==uid),{})
                selected=combo.findData(record.get('myotube_channel_index',latest.get('channel_index')))
                combo.setCurrentIndex(selected if selected>=0 else (0 if combo.count() else -1));combo.blockSignals(False)
            self._image_context=image_context
            channel=self.controls.channel.currentData() if self.module=='fluoro' else None
            if self.module=='fluoro' and uid and channel is not None:record['myotube_channel_index']=channel
            method=self.controls.method.currentData() if self.module=='brightfield' else 'cyto3'
            if self.module=='brightfield' and uid:record['myotube_method']=method
            context=(image_context,channel,method)
            result=self.current_result()
            if context!=self._context:
                if self._context is not None:self._drafts[self._context]=(self.controls.parameters(),self.controls.roi.currentData())
                if self.review_dialog is not None and not sip.isdeleted(self.review_dialog):self.review_dialog.close()
                params,scope=self._drafts.get(context,((result or {}).get('params',{}),'roi' if (result or {}).get('roi') else 'full'))
                if not image_changed and self.module=='brightfield':
                    params,scope=self.controls.parameters(),self.controls.roi.currentData()
                elif self.module=='brightfield':params={**params,'method':method}
                self.controls.set_parameters(params);self.controls.roi.setCurrentIndex(self.controls.roi.findData(scope))
                self._context=context
            self.controls.show_result(result)
            busy=any(self.window._analysis_job_service.is_busy(k) for k in (self.job_key,'import:'+self.module))
            self.controls.run_button.setEnabled(bool(uid) and not busy and (self.module!='fluoro' or channel is not None))
            self.preview_changed.emit()
        finally:self._syncing=False

    def input_snapshot(self):
        panel=self.panel();record=panel.active_image() or {};uid=record.get('uid')
        return deepcopy(dict(panel=id(panel),uid=uid,parameters=self.controls.parameters(),
            segmentation_input=panel.segmentation_input_context(uid) if self.module=='fluoro' and uid else None,
            channel=self.controls.channel.currentData() if self.module=='fluoro' else None,
            scope=self.controls.roi.currentData(),roi=record.get('rois' if self.module=='fluoro' else 'roi'),
            calibration=panel.measurement_scale_bar(uid) if uid else {},interpretation=record.get('channel_interpretation')))

    def _source(self):
        panel=self.panel();record=panel.active_image();roi=None;mask=None
        if record is None:raise ValueError(tr('bright_round_cell_no_image'))
        if self.module=='fluoro':
            from core.plotting.fluoro_render import RGB_COMPOSITE_MODE,rgb_interpretation_mode
            channel=self.controls.channel.currentData()
            if channel is None or rgb_interpretation_mode(record)==RGB_COMPOSITE_MODE:raise ValueError(tr('nuclei_choose_channel'))
            source=panel.segmentation_input_data(record['uid'])[channel].copy()
            if self.controls.roi.currentData()=='roi':
                choices=[r for r in record.get('rois',[]) if r.get('channel_index',channel)==channel]
                if not choices:raise ValueError(tr('nuclei_choose_roi'))
                original=choices[-1];mask=panel._roi_mask(source.shape,original['type'],original['geometry'])
                geometry=deepcopy(original['geometry']);kind=original['type']
                if kind in ('circle','ellipse'):
                    geometry=dict(x=geometry['cx']-geometry['rx'],y=geometry['cy']-geometry['ry'],w=2*geometry['rx'],h=2*geometry['ry'])
                roi=dict(type={'square':'rectangle','freehand':'polygon'}.get(kind,kind),coordinates_px=geometry)
        else:
            _,source=panel._load_image_rgb(record['uid']);source=source.copy()
            if self.controls.roi.currentData()=='roi':
                roi=panel._resolve_roi_snapshot(record);mask=panel._build_roi_mask(source.shape[:2],roi)
                if mask is None:raise ValueError(tr('nuclei_choose_roi'))
        return source,roi,mask

    def run(self):
        from core.image_analysis.myotube_cells import analyze_myotubes
        if any(self.window._analysis_job_service.is_busy(k) for k in (self.job_key,'import:'+self.module)):return
        panel=self.panel();record=deepcopy(panel.active_image());project=self.window.project
        try:source,roi,mask=self._source();inputs=self.input_snapshot()
        except Exception as error:self.controls.summary.setText(str(error));return
        raw_measurement=panel.image_data(record['uid'])[inputs['channel']].copy() if self.module=='fluoro' else None
        def work():
            result=analyze_myotubes(source,modality=self.module,settings=inputs['parameters'],calibration=inputs['calibration'],roi_mask=mask,
                measurement_source=raw_measurement)
            result.update(measurement_uuid=str(uuid4()),image_uid=record['uid'],image_name=record.get('source_name',''),
                channel_index=inputs['channel'],roi=roi,params=deepcopy(result['settings']))
            if self.module=='fluoro':result['segmentation_input']=deepcopy(inputs['segmentation_input'])
            return result
        def accept(result):
            panel.analysis.setdefault('myotube_measurements',[]).append(result)
            history=getattr(self.window,'measurement_history',None)
            if history:history.forget(self.module,'myotube')
            panel.append_audit('measure_'+self.module+'_myotubes',result['measurement_uuid'])
            self.sync()
        self.controls.summary.setText(tr('nuclei_running'))
        self.window._analysis_job_service.start(key=self.job_key,work=work,on_success=accept,
            on_error=lambda error:self.controls.summary.setText(str(error)),
            is_current=lambda:self.window.project is project and self.panel() is panel and self.input_snapshot()==inputs,
            on_stale=lambda:self.controls.summary.setText(tr('nuclei_stale')))

    def _busy(self,key,busy):
        if key not in (self.job_key,'import:'+self.module):return
        self.controls.body.setEnabled(not busy)
        if busy:self.controls.run_button.setEnabled(False)
        else:self.sync()

    def source_rgb(self):
        result=self.current_result()
        if result is None:raise ValueError(tr('myotube_no_result'))
        panel=self.panel();uid=result['image_uid']
        if self.module=='brightfield':return panel._load_image_rgb(uid)[1].copy()
        from core.plotting.fluoro_render import render_single_qimage
        from core.plotting.histology_display import qimage_rgb
        channel=result['channel_index'];record=panel.active_image()
        return qimage_rgb(render_single_qimage(panel.image_data(uid),record['channels'][channel]))

    def overlay_rgb(self):
        from core.image_analysis.bright_round_cells import render_bright_round_cell_overlay
        return render_bright_round_cell_overlay(self.source_rgb(),self.current_result())

    def _clear_overlay(self):
        if self.module=='fluoro':
            canvas=self.window.fluoro_workspace.canvas
            if str(canvas.active_measurement_overlay_uuid or '').startswith('myotube:'):canvas.clear_measurement_overlay()

    def show_overlay(self):
        self.preview_changed.emit()

    def open_review(self):
        from ui.dialogs.object_review_dialog import ObjectReviewDialog
        from core.measurement_review_adapter import apply_review_to_panel
        result=self.current_result()
        if result is None:return
        original=deepcopy(result);panel=self.panel();context=self.input_snapshot()
        dialog=ObjectReviewDialog.from_measurement(panel,result,self.window);self.review_dialog=dialog
        def finish(code):
            if code!=QDialog.DialogCode.Accepted or dialog.applied_record is None:return
            try:
                if self.panel() is not panel or self.input_snapshot()!=context:raise ValueError(tr('nuclei_geometry_stale'))
                apply_review_to_panel(panel,original,dialog.applied_record);self.sync()
            except Exception as error:self.controls.summary.setText(str(error))
        dialog.finished.connect(finish);dialog.show();return dialog

    def export_csv(self,path):
        result=self.current_result()
        if result is None:raise ValueError(tr('myotube_no_result'))
        fields=['id','max_width_px','max_width_um','area_px','area_um2','centerline_length_px','centerline_length_um',
                'width_method','length_method','geometry_review_required','geometry_review_reason',
                'border_truncated','include_in_statistics','image_uid','image_name','modality','channel_index','measurement_uuid','parameters']
        with Path(path).open('w',newline='',encoding='utf-8-sig') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
            for row in result['results']:writer.writerow({**row,**{k:result.get(k) for k in ('image_uid','image_name','modality','channel_index','measurement_uuid')},'parameters':json.dumps(result['params'])})

    def export_excel(self,path):
        from ui.widgets.measurement_excel_export import export_tables_xlsx
        result=self.current_result()
        if result is None:raise ValueError(tr('myotube_no_result'))
        return export_tables_xlsx([('Myotubes',self.controls.table,(0,1,2,3,4,5,6))],path,
            provenance=[{k:result.get(k) for k in ('measurement_uuid','image_uid','image_name','modality','channel_index','params','measurement_scale_bar','provenance')}])

    def export_overlay(self,path):
        if not rgb_to_qimage(self.overlay_rgb()).save(str(path),'PNG'):raise OSError('Could not save myotube overlay.')

    def choose_export(self,kind):
        path,_=QFileDialog.getSaveFileName(self.window,tr('myotube_'+self.module),'myotubes.'+kind,kind.upper()+f' (*.{kind})')
        if not path:return
        try:{'csv':self.export_csv,'xlsx':self.export_excel,'png':self.export_overlay}[kind](Path(path).with_suffix('.'+kind))
        except Exception as error:self.controls.summary.setText(str(error))

    def retranslate(self):
        self.controls.retranslate();self.sync()
        if self.review_dialog is not None and not sip.isdeleted(self.review_dialog):self.review_dialog.retranslate()

