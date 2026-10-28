"""Compact independent muscle-tube entry, shared by two microscopy modules."""
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QPushButton,QComboBox,
    QDoubleSpinBox,QSpinBox,QLabel,QTableWidget,QTableWidgetItem,QHeaderView)
from core.utils.i18n import bind_i18n,tr,retranslate_combo
from core.image_analysis.myotube_cells import DEFAULTS
from ui.widgets.calibration_controls import action_grid
from ui.widgets.measurement_excel_export import EXCEL_VALUE_ROLE
from ui.widgets.model_selection_hint import ModelSelectionHint


class MyotubeControls(QWidget):
    def __init__(self,module,parent=None):
        super().__init__(parent)
        self.module=module
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.entry=bind_i18n(QPushButton(),'myotube_'+module);self.entry.setCheckable(True);layout.addWidget(self.entry)
        self.body=QWidget();body=QVBoxLayout(self.body);body.setContentsMargins(0,4,0,4)
        form=QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.channel=QComboBox();self.channel.setMinimumWidth(0)
        if module=='fluoro':form.addRow(bind_i18n(QLabel(),'myotube_channel'),self.channel)
        self.method=QComboBox();self.method.setMinimumWidth(0)
        self.method.addItem(tr('myotube_method_general'),'cyto3')
        if module=='brightfield':
            self.method.addItem(tr('myotube_method_boundary'),'truefad_rim_path')
            form.addRow(bind_i18n(QLabel(),'myotube_method'),self.method)
        self.model_hint=ModelSelectionHint(self.method,dict(cyto3='cyto3',truefad_rim_path='truefad_myotube'))
        form.addRow(self.model_hint)
        self.diameter=QDoubleSpinBox();self.diameter.setRange(2,2000);self.diameter.setSuffix(' px')
        self.minimum=QSpinBox();self.minimum.setRange(1,1000000)
        self.aspect=QDoubleSpinBox();self.aspect.setRange(1.5,30);self.aspect.setSingleStep(.1)
        self.roi=QComboBox();self.roi.addItem(tr('nuclei_full_image'),'full');self.roi.addItem(tr('nuclei_current_roi'),'roi')
        form.addRow(bind_i18n(QLabel(),'myotube_model_size'),self.diameter)
        form.addRow(bind_i18n(QLabel(),'myotube_min_area'),self.minimum)
        form.addRow(bind_i18n(QLabel(),'myotube_min_aspect'),self.aspect)
        form.addRow(bind_i18n(QLabel(),'myotube_scope'),self.roi)
        body.addLayout(form)
        self.hint=bind_i18n(QLabel(),'myotube_hint');self.hint.setWordWrap(True);body.addWidget(self.hint)
        self.run_button=bind_i18n(QPushButton(),'myotube_run');self.run_button.setObjectName('PrimaryButton');body.addWidget(self.run_button)
        self.summary=bind_i18n(QLabel(),'myotube_no_result');self.summary.setWordWrap(True);body.addWidget(self.summary)
        self.table=QTableWidget(0,8);self.table.setMinimumWidth(0)
        self.table.setMinimumHeight(130);self.table.setMaximumHeight(260)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        body.addWidget(self.table)
        self.review_button=bind_i18n(QPushButton(),'myotube_review')
        self.csv_button=bind_i18n(QPushButton(),'nuclei_export_csv')
        self.excel_button=bind_i18n(QPushButton(),'export_excel')
        self.overlay_button=bind_i18n(QPushButton(),'nuclei_export_overlay')
        body.addWidget(self.review_button);body.addWidget(action_grid((self.csv_button,self.excel_button,self.overlay_button)))
        layout.addWidget(self.body);self.body.hide();self.entry.toggled.connect(self.body.setVisible)
        self.set_parameters({});self.retranslate()
        self.method.currentIndexChanged.connect(self._method_hint)
        self._method_hint()

    def parameters(self):
        params=dict(diameter_px=self.diameter.value(),min_area_px=self.minimum.value(),
                    min_aspect_ratio=self.aspect.value(),exclude_scale_bar=True)
        if self.module=='brightfield':params['method']=self.method.currentData()
        return params

    def set_parameters(self,params):
        values={**DEFAULTS,**params}
        selected=self.method.findData(values.get('method','cyto3'))
        self.method.blockSignals(True);self.method.setCurrentIndex(max(0,selected));self.method.blockSignals(False)
        for widget,key in ((self.diameter,'diameter_px'),(self.minimum,'min_area_px'),(self.aspect,'min_aspect_ratio')):
            widget.blockSignals(True);widget.setValue(values[key]);widget.blockSignals(False)
        self._method_hint()

    def _method_hint(self,*_args):
        adapted=self.method.currentData()=='truefad_rim_path'
        self.diameter.setEnabled(not adapted)
        self.hint.setText(tr('myotube_boundary_hint' if adapted else 'myotube_hint'))

    def show_result(self,result):
        rows=(result or {}).get('results',[]);self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            values=[row['id'],row.get('max_width_px'),row.get('max_width_um'),row.get('area_px'),row.get('area_um2'),
                    row.get('centerline_length_px'),row.get('centerline_length_um'),
                    tr('myotube_geometry_review' if row.get('geometry_review_required') else
                       ('cell_border_truncated' if row.get('border_truncated') else
                        ('myotube_width_review' if row.get('max_width_px') is None else 'cell_complete')))]
            for col,value in enumerate(values):
                text='—' if value is None else (f'{value:.2f}' if isinstance(value,float) else str(value))
                item=QTableWidgetItem(text)
                if col<7:item.setData(EXCEL_VALUE_ROLE,value)
                self.table.setItem(i,col,item)
        for button in (self.review_button,self.csv_button,self.excel_button,self.overlay_button):button.setEnabled(result is not None)
        if result is None:self.summary.setText(tr('myotube_no_result'));return
        s=result['summary'];parts=[tr('myotube_summary').format(n=s['count'],complete=s['complete_count'],edge=s.get('border_truncated_count',0))]
        if s.get('mean_max_width_um') is not None:
            parts.append(tr('myotube_physical_summary').format(width=s['mean_max_width_um'],area=s['mean_area_um2']))
        elif result.get('calibrated'):
            if s.get('mean_area_um2') is not None:parts.append(tr('myotube_area_summary').format(area=s['mean_area_um2']))
        else:parts.append(tr('myotube_pixel_hint'))
        if s.get('mean_centerline_length_um') is not None:
            parts.append(tr('myotube_length_summary').format(length=s['mean_centerline_length_um']))
        if s.get('geometry_review_count'):
            parts.append(tr('myotube_review_summary').format(n=s['geometry_review_count']))
        self.summary.setText('\n'.join(parts))

    def retranslate(self):
        retranslate_combo(self.method,{'cyto3':tr('myotube_method_general'),'truefad_rim_path':tr('myotube_method_boundary')})
        self.table.setHorizontalHeaderLabels(tr('myotube_headers').split('|'))
        retranslate_combo(self.roi,{'full':tr('nuclei_full_image'),'roi':tr('nuclei_current_roi')})
        self._method_hint()

