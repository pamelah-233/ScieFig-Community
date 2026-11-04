"""Compact, shared nuclear counting entry in the existing Measure pages."""
from copy import deepcopy
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QPushButton,QComboBox,QDoubleSpinBox,QSpinBox,QLabel,QHBoxLayout,QCheckBox
from core.utils.i18n import bind_i18n,tr,retranslate_combo
from ui.widgets.model_selection_hint import ModelSelectionHint


class NucleiCountControls(QWidget):
    def __init__(self,module,parent=None,*,task=None):
        super().__init__(parent)
        self.module=module
        self.fixed_task=task or ('dapi' if module=='fluoro' else 'he')
        self._he_settings=None;self._local_he_settings=None;self._he_settings_method=None;self._sensitivity=1.
        self._he_pale_settings=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.entry=bind_i18n(QPushButton(),{'dapi':'task_dapi','he':'task_he','muscle':'task_central'}[self.fixed_task])
        self.entry.setCheckable(True);layout.addWidget(self.entry)
        self.body=QWidget();body=QVBoxLayout(self.body);body.setContentsMargins(0,4,0,4)
        form=QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        body.addLayout(form)
        self.nuclear_options=QWidget();form=QFormLayout(self.nuclear_options);form.setContentsMargins(0,0,0,0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.channel=QComboBox();self.channel.setMinimumWidth(0)
        self.object_kind=QComboBox()
        for kind in ('nuclei','cells','spots'):self.object_kind.addItem(tr('relation_kind_'+kind),kind)
        if module!='fluoro':self.object_kind.hide()
        self.method=QComboBox();self.method.addItem(tr('nuclei_cellpose'),'cellpose_nuclei');self.method.addItem(tr('nuclei_watershed'),'watershed')
        if module=='fluoro':
            self.method.insertItem(0,tr('nuclei_cyto3'),'cellpose_cyto3')
            self.method.insertItem(1,tr('nuclei_cyto3_reviewed'),'cellpose_cyto3_dapi_reviewed_v1')
        self.method.setMinimumWidth(0)
        self.method.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.method.setMinimumContentsLength(12)
        if module=='brightfield':
            self.method.addItem(tr('nuclei_he_round'),'he_round')
            self.method.addItem(tr('nuclei_he_refined'),'he_refined')
            self.method.addItem(tr('nuclei_cyto3'),'cellpose_cyto3')
            self.method.addItem(tr('nuclei_he_pale'),'cellpose_cyto3_he_pale_v1')
        form.addRow(bind_i18n(QLabel(),'nuclei_method'),self.method)
        self.model_hint=ModelSelectionHint(self.method,dict(cellpose_nuclei='nuclei',cellpose_cyto3='cyto3',
            cellpose_cyto3_dapi_reviewed_v1='cyto3_dapi_reviewed_v1',cellpose_cyto3_he_pale_v1='cyto3'))
        form.addRow(self.model_hint)
        if module=='fluoro':
            form.addRow(bind_i18n(QLabel(),'nuclei_native_channel'),self.channel)
            form.addRow(bind_i18n(QLabel(),'relation_object_kind'),self.object_kind)
        self.cellpose_input=bind_i18n(QCheckBox(),'nuclei_local_contrast')
        bind_i18n(self.cellpose_input,'nuclei_local_contrast_hint','toolTip')
        if module=='fluoro':form.addRow(self.cellpose_input)
        else:self.cellpose_input.hide()
        self.method.currentIndexChanged.connect(self._sync_method)
        self.diameter=QDoubleSpinBox();self.diameter.setRange(2,500);self.diameter.setValue(17);self.diameter.setSuffix(' px')
        form.addRow(bind_i18n(QLabel(),'nuclei_diameter'),self.diameter)
        self.minimum=QSpinBox();self.minimum.setRange(1,100000);self.minimum.setValue(8)
        self.maximum=QSpinBox();self.maximum.setRange(1,1000000);self.maximum.setValue(2000)
        form.addRow(bind_i18n(QLabel(),'nuclei_min_area'),self.minimum)
        form.addRow(bind_i18n(QLabel(),'nuclei_max_area'),self.maximum)
        self.roi=QComboBox();self.roi.addItem(tr('nuclei_full_image'),'full');self.roi.addItem(tr('nuclei_current_roi'),'roi')
        form.addRow(bind_i18n(QLabel(),'nuclei_scope'),self.roi)
        body.addWidget(self.nuclear_options)
        self.classify_signal=bind_i18n(QCheckBox(),'relation_classify_with_count')
        self.signal_controls=QWidget();signal_form=QFormLayout(self.signal_controls)
        signal_form.setContentsMargins(0,0,0,0);signal_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.signal_channel=QComboBox();self.signal_positive=QDoubleSpinBox();self.signal_strong=QDoubleSpinBox();self.signal_background=QDoubleSpinBox()
        for widget in (self.signal_positive,self.signal_strong,self.signal_background):widget.setRange(0,1e12);widget.setDecimals(3)
        self.signal_positive.setValue(100);self.signal_strong.setValue(500)
        for key,widget in [('relation_channel',self.signal_channel),('relation_background',self.signal_background),
                           ('relation_positive',self.signal_positive),('relation_strong',self.signal_strong)]:
            signal_form.addRow(bind_i18n(QLabel(),key),widget)
        if module=='fluoro':
            body.addWidget(self.classify_signal);body.addWidget(self.signal_controls)
            self.classify_signal.toggled.connect(self.signal_controls.setVisible)
        else:self.classify_signal.hide()
        self.signal_controls.hide()
        self.muscle_options=QWidget();muscle=QFormLayout(self.muscle_options);muscle.setContentsMargins(0,0,0,0)
        muscle.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.band=QSpinBox();self.band.setRange(5,60);self.band.setValue(20);self.band.setSuffix(' %')
        self.fiber_source=QComboBox();self.fiber_source.setMinimumWidth(0)
        self.nuclear_source=QComboBox();self.nuclear_source.setMinimumWidth(0)
        muscle.addRow(bind_i18n(QLabel(),'nuclei_fiber_source'),self.fiber_source)
        muscle.addRow(bind_i18n(QLabel(),'nuclei_nuclear_source'),self.nuclear_source)
        muscle.addRow(bind_i18n(QLabel(),'nuclei_peripheral_band'),self.band)
        body.addWidget(self.muscle_options)
        self.hint=bind_i18n(QLabel(),'nuclei_raw_hint');self.hint.setWordWrap(True);body.addWidget(self.hint)
        self.run_button=bind_i18n(QPushButton(),'nuclei_run');self.run_button.setObjectName('PrimaryButton');body.addWidget(self.run_button)
        self.summary=bind_i18n(QLabel(),'nuclei_no_result');self.summary.setWordWrap(True);body.addWidget(self.summary)
        self.review_button=bind_i18n(QPushButton(),'nuclei_review');body.addWidget(self.review_button)
        self.csv_button=bind_i18n(QPushButton(),'nuclei_export_csv');self.overlay_button=bind_i18n(QPushButton(),'nuclei_export_overlay')
        from ui.widgets.calibration_controls import action_grid
        body.addWidget(action_grid((self.csv_button,self.overlay_button)))
        self.legend=bind_i18n(QLabel(),'nuclei_legend');self.legend.setWordWrap(True);body.addWidget(self.legend)
        layout.addWidget(self.body);self.body.hide()
        self.entry.toggled.connect(self.body.setVisible)
        self.sync_task()
        if module=='fluoro':self.set_parameters({})
        self._sync_method()

    def _sync_method(self):
        method=self.method.currentData()
        self.model_hint.refresh()
        self.cellpose_input.setEnabled(method in ('cellpose_nuclei','cellpose_cyto3','cellpose_cyto3_dapi_reviewed_v1'))
        hint={'cellpose_cyto3_dapi_reviewed_v1':'nuclei_cyto3_reviewed_hint','cellpose_cyto3_he_pale_v1':'nuclei_he_pale_hint',
              'cellpose_cyto3':'nuclei_cyto3_hint','cellpose_nuclei':'nuclei_cellpose_hint','watershed':'nuclei_watershed_hint',
              'he_round':'nuclei_he_round_hint','he_refined':'nuclei_he_refined_hint'}.get(method)
        self.method.setToolTip(tr(hint) if hint else '')

    def sync_task(self):
        muscle=self.fixed_task=='muscle'
        self.muscle_options.setVisible(muscle)
        self.nuclear_options.setVisible(not muscle)
        bind_i18n(self.run_button,'nuclei_joint_run' if muscle else 'nuclei_run')
        bind_i18n(self.hint,'nuclei_joint_hint' if muscle else 'nuclei_raw_hint')
        bind_i18n(self.legend,'nuclei_legend' if muscle else 'nuclei_count_legend')

    def parameters(self):
        result=dict(method=self.method.currentData(),diameter_px=self.diameter.value(),
                    min_area_px=self.minimum.value(),max_area_px=self.maximum.value(),sensitivity=self._sensitivity)
        if self.module=='fluoro':result['object_kind']=self.object_kind.currentData()
        if self.module=='fluoro' and self.method.currentData() in ('cellpose_nuclei','cellpose_cyto3','cellpose_cyto3_dapi_reviewed_v1'):
            result['cellpose_input']=('tile_normalization' if self.method.currentData().startswith('cellpose_cyto3') else 'local_contrast') if self.cellpose_input.isChecked() else 'raw'
        if self.module=='brightfield' and self.method.currentData()=='cellpose_cyto3':
            result['cellpose_input']='tile_normalization'
        if self._he_pale_settings is not None and self.method.currentData()=='cellpose_cyto3_he_pale_v1':
            result['he_pale_settings']=deepcopy(self._he_pale_settings)
        if self._he_settings is not None and self.method.currentData()==self._he_settings_method:
            result['he_settings']=deepcopy(self._he_settings)
        if self._local_he_settings is not None and self.method.currentData()=='he_refined':
            result['local_he_settings']=deepcopy(self._local_he_settings)
        if self.module=='fluoro' and self.classify_signal.isChecked():
            result['signal_classification']=dict(channel_index=self.signal_channel.currentData(),
                positive_threshold=self.signal_positive.value(),strong_threshold=self.signal_strong.value(),
                background=self.signal_background.value())
        return result

    def set_parameters(self,params):
        if self.module=='brightfield' and not params:
            params=dict(method='cellpose_nuclei',diameter_px=17,min_area_px=8,max_area_px=2000)
        if self.module=='fluoro':
            method=params.get('method','cellpose_cyto3')
            cyto=method in ('cellpose_cyto3','cellpose_cyto3_dapi_reviewed_v1')
            params={**dict(method=method,diameter_px=25 if cyto else 17,min_area_px=50 if cyto else 8,
                           max_area_px=2000,cellpose_input='tile_normalization' if cyto else 'raw'),**params}
        self.object_kind.setCurrentIndex(self.object_kind.findData(params.get('object_kind','nuclei')))
        for name,key in [('method','method'),('diameter','diameter_px'),('minimum','min_area_px'),('maximum','max_area_px')]:
            if key not in params:continue
            widget=getattr(self,name);widget.blockSignals(True)
            if name=='method':widget.setCurrentIndex(widget.findData(params[key]))
            else:widget.setValue(params[key])
            widget.blockSignals(False)
        self._he_settings=deepcopy(params.get('he_settings'))
        self._local_he_settings=deepcopy(params.get('local_he_settings'))
        self._he_pale_settings=deepcopy(params.get('he_pale_settings'))
        self._he_settings_method=params.get('method')
        self._sensitivity=params.get('sensitivity',1.)
        self.cellpose_input.setChecked(params.get('cellpose_input','raw') in ('local_contrast','tile_normalization'))
        signal=params.get('signal_classification')
        self.classify_signal.setChecked(bool(signal))
        if signal:
            self.signal_channel.setCurrentIndex(self.signal_channel.findData(signal.get('channel_index')))
            self.signal_positive.setValue(signal['positive_threshold']);self.signal_strong.setValue(signal['strong_threshold'])
            self.signal_background.setValue(signal.get('background',0))
        self._sync_method()

    def retranslate(self):
        retranslate_combo(self.object_kind,{kind:tr('relation_kind_'+kind) for kind in ('nuclei','cells','spots')})
        retranslate_combo(self.method,{'cellpose_nuclei':tr('nuclei_cellpose'),'cellpose_cyto3':tr('nuclei_cyto3'),'cellpose_cyto3_dapi_reviewed_v1':tr('nuclei_cyto3_reviewed'),'cellpose_cyto3_he_pale_v1':tr('nuclei_he_pale'),'watershed':tr('nuclei_watershed'),'he_round':tr('nuclei_he_round'),'he_refined':tr('nuclei_he_refined')})
        retranslate_combo(self.roi,{'full':tr('nuclei_full_image'),'roi':tr('nuclei_current_roi')})
        self.sync_task()
        self._sync_method()

