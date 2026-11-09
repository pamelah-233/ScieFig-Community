"""Histology edits, illumination correction and explicit analysis input selection."""
from PyQt6 import sip
import numpy as np
from PyQt6.QtCore import QObject,QEvent,QRect,Qt,QSignalBlocker,pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QGroupBox,QVBoxLayout,QLabel,QPushButton,QRubberBand,QWidget,
                            QFormLayout,QSpinBox,QDoubleSpinBox,QCheckBox,QButtonGroup)
from pathlib import Path
from PyQt6.QtWidgets import QFileDialog
from core.utils.recent_directory import recent_directory,remember_file_directory
from core.utils.i18n import bind_i18n,tr
from core.plotting.histology_display import sample_white_balance,qimage_rgb
from core.plotting.histology_processing import (
    ILLUMINATION_METHOD, apply_histology_base, estimate_illumination, illumination_parameters,
    processed_histology_qimage,
)
from core.plotting.fluoro_render import rgb_display_channel
from core.plotting.fluoro_display import sample_display_point


class HistologyWhiteBalanceController(QObject):
    processing_changed = pyqtSignal()

    def __init__(self,workspace):
        super().__init__(workspace)
        self.workspace=workspace;self.pending=None;self.label=None;self.start=None;self.rubber=None
        self._synced_image = None
        self.widget=bind_i18n(QGroupBox(),'histology_white_balance','title')
        layout=QVBoxLayout(self.widget)
        self.hint=bind_i18n(QLabel(),'histology_white_balance_hint');self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.pick_button=bind_i18n(QPushButton(),'histology_white_pick');self.pick_button.setCheckable(True)
        self.auto_button=bind_i18n(QPushButton(),'histology_white_auto')
        self.reset_button=bind_i18n(QPushButton(),'reset')
        self.export_button=bind_i18n(QPushButton(),'histology_white_export')
        self.export_button.setEnabled(False)
        self.edit_spins = {}
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for key, label in (('brightness','brightness'),('contrast','contrast'),
                           ('display_min','min'),('display_max','max')):
            spin = QDoubleSpinBox() if key.startswith('display_') else QSpinBox()
            spin.setRange(0,255) if key.startswith('display_') else spin.setRange(-100,100)
            spin.setKeyboardTracking(False)
            spin.setObjectName('histology_' + key)
            spin.valueChanged.connect(lambda value,k=key:self.update_display(k,value))
            self.edit_spins[key] = spin
            caption = bind_i18n(QLabel(),label)
            caption.setWordWrap(True)
            form.addRow(caption,spin)
        layout.addLayout(form)
        from ui.widgets.fluoro_display_controls import FluoroDisplayControls
        self.processing = FluoroDisplayControls()
        bind_i18n(self.processing.hint,'histology_processing_hint')
        self.processing.settings_changed.connect(self.update_processing)
        self.processing.point_requested.connect(lambda point:self.begin(True,point=point))
        layout.addWidget(self.processing)
        self.illumination = bind_i18n(QCheckBox(),'histology_illumination')
        self.illumination.toggled.connect(self.toggle_illumination)
        self.illumination_strength = QSpinBox()
        self.illumination_strength.setRange(0,100)
        self.illumination_strength.setSuffix(' %')
        self.illumination_strength.setKeyboardTracking(False)
        self.illumination_strength.valueChanged.connect(self.update_illumination_strength)
        layout.addWidget(self.illumination)
        strength_form = QFormLayout()
        caption = bind_i18n(QLabel(),'histology_illumination_strength')
        caption.setWordWrap(True)
        strength_form.addRow(caption,self.illumination_strength)
        layout.addLayout(strength_form)
        self.compare = bind_i18n(QCheckBox(),'histology_compare_original')
        self.compare.toggled.connect(lambda:self.workspace.canvas.show_original_preview())
        layout.addWidget(self.compare)
        for button in (self.pick_button,self.auto_button,self.reset_button,self.export_button):
            button.setMinimumWidth(0)
            layout.addWidget(button)
        self.input_widget = QWidget()
        inputs = QVBoxLayout(self.input_widget)
        inputs.setContentsMargins(0,0,0,0)
        inputs.addWidget(bind_i18n(QLabel(),'histology_analysis_input'))
        self.input_group = QButtonGroup(self.input_widget)
        self.input_buttons = {}
        for mode,key in (('raw','histology_input_raw'),('adjusted','histology_input_adjusted')):
            button = bind_i18n(QPushButton(),key)
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.clicked.connect(lambda checked=False,value=mode:self.set_input(value))
            self.input_group.addButton(button)
            inputs.addWidget(button)
            self.input_buttons[mode] = button
        self.pick_button.clicked.connect(self.begin)
        self.auto_button.clicked.connect(lambda:self.apply_region(None))
        self.reset_button.clicked.connect(self.reset)
        self.export_button.clicked.connect(self.choose_export)
        workspace.canvas.display_transform=self.transform
        workspace.canvas.preview_mode_changed.connect(self._preview_changed)

    def image(self):
        panel=self.workspace.panel
        return panel.active_image() if panel else None

    def identity(self):
        return (id(self.workspace.panel),(self.image() or {}).get('uid'))

    def sync(self):
        identity = self.identity()
        if self.pending and (self.pending != identity or self.label is not self.workspace.canvas.image_label):
            self.cancel()
        if identity != self._synced_image:
            self._synced_image = identity
            with QSignalBlocker(self.compare):
                self.compare.setChecked(False)
        self.widget.setEnabled(self.image() is not None)
        image = self.image() or {}
        self.export_button.setEnabled(bool(image))
        self.input_widget.setEnabled(bool(image))
        self.input_buttons['adjusted' if image.get('segmentation_input') == 'adjusted' else 'raw'].setChecked(True)
        values = rgb_display_channel(np.zeros((3,1,1),np.uint8),image)
        for key,spin in self.edit_spins.items():
            with QSignalBlocker(spin):
                spin.setValue(values[key])
        self.processing.set_settings(values.get('display_processing'))
        self.processing.hint.setText(tr('histology_processing_hint'))
        correction = image.get('illumination_correction')
        parameters = illumination_parameters(correction)
        enabled = bool(parameters and correction.get('enabled'))
        with QSignalBlocker(self.illumination):
            self.illumination.setChecked(enabled)
        with QSignalBlocker(self.illumination_strength):
            self.illumination_strength.setValue(round(parameters[2] * 100) if parameters else 100)
        self.illumination_strength.setEnabled(enabled)

    def transform(self,pixmap):
        if self.compare.isChecked():
            return pixmap
        return QPixmap.fromImage(processed_histology_qimage(pixmap.toImage(),self.image() or {}))

    def set_input(self,mode):
        image = self.image()
        if image is not None:
            image['segmentation_input'] = mode
            self._changed('set_histology_input')

    def update_display(self,key,value):
        image = self.image()
        if image is not None:
            image.setdefault('rgb_display',{})[key] = value
            self._changed('adjust_histology_image')

    def update_processing(self,settings):
        self.update_display('display_processing',settings)

    def toggle_illumination(self,enabled):
        image = self.image()
        if image is None:
            return
        correction = image.get('illumination_correction')
        parameters = illumination_parameters(correction)
        if enabled and (not parameters or correction.get('method') != ILLUMINATION_METHOD):
            try:
                source = self.workspace.panel.source_qimage(image['uid'])
                if source.isNull():
                    raise ValueError(tr('histology_white_no_image'))
                correction = estimate_illumination(qimage_rgb(source))
            except (ValueError,OSError) as error:
                self.sync()
                self.hint.setText(str(error))
                return
            if parameters:
                correction['strength'] = round(parameters[2] * 100)
        elif not parameters:
            image.pop('illumination_correction', None)
            self.sync()
            return
        image['illumination_correction'] = {**correction, 'enabled': enabled}
        if enabled:
            image['segmentation_input'] = 'adjusted'
        self._changed('correct_histology_illumination')
        self.sync()

    def update_illumination_strength(self,value):
        image = self.image()
        correction = (image or {}).get('illumination_correction')
        if illumination_parameters(correction) and correction.get('enabled'):
            correction['strength'] = value
            self._changed('adjust_histology_illumination')

    def _changed(self,action):
        self.workspace.panel.append_audit(action,str(self.image()['uid']))
        self.workspace.canvas.show_original_preview()
        self.processing_changed.emit()

    def cancel(self):
        if self.label is not None and not sip.isdeleted(self.label):
            self.label.removeEventFilter(self);self.label.unsetCursor()
        if self.rubber is not None and not sip.isdeleted(self.rubber):self.rubber.hide();self.rubber.deleteLater()
        self.pending=None;self.start=None;self.label=None;self.rubber=None
        self.pick_button.setChecked(False)
        self.processing.black_point_button.setChecked(False)
        self.processing.white_point_button.setChecked(False)
        self.hint.setText(tr('histology_white_balance_hint'))

    def _preview_changed(self, mode, _has_overlay):
        if self.pending and (mode != 'original' or not self.workspace.canvas.has_original_pixmap()):
            self.cancel()

    def begin(self,checked=True,*,point=None):
        self.cancel()
        if not checked or self.image() is None:return
        self.workspace.show_original_preview()
        if not self.workspace.canvas.has_original_pixmap():return
        self.label=self.workspace.canvas.image_label
        if self.label is None:return
        self.pending=self.identity();self.pick_button.setChecked(True)
        self.pick_mode = point
        if point:
            self.pick_button.setChecked(False)
            getattr(self.processing,point + '_point_button').setChecked(True)
        self.label.installEventFilter(self);self.label.setCursor(Qt.CursorShape.CrossCursor)
        self.label.setFocus();self.rubber=QRubberBand(QRubberBand.Shape.Rectangle,self.label)
        self.hint.setText(tr('fluoro_display_pick_' + point) if point else tr('histology_white_pick_hint'))

    def eventFilter(self,watched,event):
        if watched is not self.label or self.pending is None:return False
        if (event.type()==QEvent.Type.Hide or watched is not self.workspace.canvas.image_label
                or self.pending!=self.identity()):
            self.cancel()
            return False
        if event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape:
            self.cancel();return True
        if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            self.start=event.position().toPoint();return True
        if event.type()==QEvent.Type.MouseMove and self.start is not None:
            self.rubber.setGeometry(QRect(self.start,event.position().toPoint()).normalized());self.rubber.show();return True
        if event.type()==QEvent.Type.MouseButtonRelease and self.start is not None:
            rectangle=watched._map_label_rect_to_source(QRect(self.start,event.position().toPoint()).normalized())
            point = self.pick_mode
            self.cancel()
            geometry = dict(x=rectangle.x(),y=rectangle.y(),w=rectangle.width(),h=rectangle.height())
            if point:
                self.apply_point(point,geometry)
            else:
                self.apply_region(geometry)
            return True
        return False

    def apply_region(self,geometry):
        self.cancel()
        image=self.image()
        if image is None:return
        try:
            rgb=qimage_rgb(self.workspace.panel.source_qimage(image['uid']))
            rgb=apply_histology_base(rgb,{**image,'display_white_balance':None})
            settings=sample_white_balance(rgb,geometry)
        except (ValueError,KeyError,OSError) as error:
            self.hint.setText(str(error));return
        image['display_white_balance']=settings
        self._changed('display_white_balance')
        self.hint.setText(tr('histology_white_balance_hint'))
        self.sync()

    def apply_point(self,point,geometry):
        image = self.image()
        if image is None:
            return
        try:
            source = qimage_rgb(self.workspace.panel.source_qimage(image['uid']))
            rgb = apply_histology_base(source,image)
            channel = rgb_display_channel(np.moveaxis(rgb,-1,0),image)
            luminance = rgb[:,:,0] * .2126 + rgb[:,:,1] * .7152 + rgb[:,:,2] * .0722
            settings = sample_display_point(luminance,geometry,channel,point)
            self.update_processing(settings)
            self.sync()
        except (ValueError,OSError) as error:
            self.hint.setText(str(error))

    def reset(self):
        self.cancel()
        image=self.image()
        if image is None:return
        image.pop('display_white_balance',None)
        image.pop('illumination_correction',None)
        image.pop('rgb_display',None)
        image['segmentation_input'] = 'raw'
        with QSignalBlocker(self.compare):
            self.compare.setChecked(False)
        self._changed('reset_histology_processing')
        self.hint.setText(tr('histology_white_balance_hint'))
        self.sync()

    def choose_export(self):
        name=Path(str((self.image() or {}).get('source_name','image'))).stem+'_adjusted.png'
        filename,_=QFileDialog.getSaveFileName(self.workspace,tr('histology_white_export'),
                                              str(Path(recent_directory())/name),'PNG (*.png)')
        if not filename:return
        try:
            target=Path(filename).with_suffix('.png')
            self.export_to_path(target);remember_file_directory(target)
        except (ValueError,OSError) as error:self.hint.setText(str(error))

    def export_to_path(self,path):
        image=self.image()
        if image is None:raise ValueError(tr('histology_white_no_image'))
        # Explicit display export; the existing Original export still preserves raw colors.
        rendered=self.workspace.panel.display_qimage(image['uid'])
        if not rendered.save(str(path),'PNG'):raise OSError(tr('histology_white_export_failed'))
