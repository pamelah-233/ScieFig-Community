"""Per-channel display controls, separate from measurement parameters."""
from pathlib import Path
from PyQt6.QtCore import QDir,QSignalBlocker,pyqtSignal
from PyQt6.QtWidgets import QBoxLayout,QCheckBox,QFormLayout,QGroupBox,QHBoxLayout,QLabel,QPushButton,QSizePolicy,QSpinBox
from core.plotting.fluoro_display import display_settings
from core.utils.i18n import bind_i18n, tr


class FluoroDisplayControls(QGroupBox):
    settings_changed=pyqtSignal(dict)
    point_requested=pyqtSignal(str)

    def __init__(self,settings=None,parent=None):
        super().__init__(parent)
        self.setObjectName('FluoroDisplayControls')
        QDir.addSearchPath('sciefigSpin', str(Path(__file__).resolve().parents[2] / 'resources' / 'icons'))
        bind_i18n(self,'fluoro_display_optimization','title')
        form=QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.strength=QSpinBox();self.strength.setRange(-100,100);self.strength.setSingleStep(5)
        self.denoise=QSpinBox();self.denoise.setRange(0,100);self.denoise.setSuffix(' %');self.denoise.setSingleStep(10)
        self.radius=QSpinBox();self.radius.setRange(8,500);self.radius.setValue(50);self.radius.setSuffix(' px')
        for widget in (self.strength,self.denoise,self.radius):
            widget.setKeyboardTracking(False)
            widget.setFixedHeight(36)
        form.addRow(bind_i18n(QLabel(),'fluoro_display_strength'),self.strength)
        form.addRow(bind_i18n(QLabel(),'fluoro_display_denoise'),self.denoise)
        self.background=bind_i18n(QCheckBox(),'fluoro_display_background')
        bind_i18n(self.background,'workflow_background_method','toolTip')
        self.background.setObjectName('FluoroDisplayBackground')
        form.addRow(self.background,self.radius)
        for field in (self.strength,self.denoise,self.radius):
            form.labelForField(field).setMinimumHeight(field.height())
            label=form.labelForField(field)
            if isinstance(label,QLabel):
                label.setWordWrap(True)
                label.setMinimumWidth(0)
        bind_i18n(self.radius,'fluoro_display_radius_hint','toolTip')
        self.black_point_button=bind_i18n(QPushButton(),'fluoro_display_black_point')
        self.white_point_button=bind_i18n(QPushButton(),'fluoro_display_white_point')
        points=QHBoxLayout()
        for point,button in (('black',self.black_point_button),('white',self.white_point_button)):
            button.setCheckable(True)
            button.clicked.connect(lambda _checked,p=point:self.point_requested.emit(p))
            points.addWidget(button)
        form.addRow(points)
        self.compare=bind_i18n(QCheckBox(),'fluoro_display_compare')
        form.addRow(self.compare)
        self.denoise_button=bind_i18n(QPushButton(),'fluoro_display_one_click')
        self.reset_button=bind_i18n(QPushButton(),'fluoro_display_reset')
        actions=QHBoxLayout();actions.addWidget(self.denoise_button);actions.addWidget(self.reset_button)
        form.addRow(actions)
        self._button_rows=(points,actions)
        for button in (self.black_point_button,self.white_point_button,self.denoise_button,self.reset_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        hint=bind_i18n(QLabel(),'fluoro_display_only_hint');hint.setWordWrap(True);hint.setObjectName('DimLabel')
        self.hint=hint
        form.addRow(hint)
        self.set_settings(settings)
        for widget in (self.strength,self.denoise,self.radius):widget.valueChanged.connect(self._emit)
        self.background.toggled.connect(self._emit);self.compare.toggled.connect(self._emit)
        self.denoise_button.clicked.connect(self._one_click)
        self.reset_button.clicked.connect(self._reset)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        for row in self._button_rows:
            needed=sum(row.itemAt(i).widget().sizeHint().width() for i in range(row.count()))+row.spacing()
            direction=QBoxLayout.Direction.TopToBottom if needed>self.layout().contentsRect().width() else QBoxLayout.Direction.LeftToRight
            if row.direction()!=direction:
                row.setDirection(direction)
        # The surrounding form can retain its wide-sidebar height after a
        # hidden page is narrowed. Wrapped rows still need their actual height.
        height=self.heightForWidth(self.width())
        if height>0 and self.minimumHeight()!=height:
            self.setMinimumHeight(height)
            self.layout().invalidate()
            self.layout().activate()

    def set_settings(self,payload):
        s=display_settings(payload)
        self._points={key:s[key] for key in ('black_point','white_point')}
        blockers=[QSignalBlocker(w) for w in (self.strength,self.denoise,self.radius,self.background,self.compare)]
        self.strength.setValue(round(s['strength']));self.denoise.setValue(round(s['denoise']))
        self.background.setChecked(s['background_radius']>0)
        self.radius.setValue(round(s['background_radius']) or 50)
        self.radius.setEnabled(self.background.isChecked())
        self.compare.setChecked(not s['enabled'])
        self.reset_hint()

    def reset_hint(self):
        active=self._points['black_point'] != 0 or self._points['white_point'] != 1
        self.hint.setText(tr('fluoro_display_point_applied' if active else 'fluoro_display_only_hint'))

    def settings(self):
        return {**self._points,'enabled':not self.compare.isChecked(),'strength':self.strength.value(),
                'denoise':self.denoise.value(),'background_radius':self.radius.value() if self.background.isChecked() else 0}

    def _emit(self,*_):
        self.radius.setEnabled(self.background.isChecked())
        self.settings_changed.emit(self.settings())

    def _one_click(self):
        s=self.settings();s.update(enabled=True,denoise=40)
        self.set_settings(s);self._emit()

    def _reset(self):
        self.set_settings({});self._emit()

