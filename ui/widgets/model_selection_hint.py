"""Quiet, live identification of the method used by the next analysis."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSizePolicy
from core.utils.i18n import tr


class ModelSelectionHint(QLabel):
    def __init__(self, combo, models=None, *, toggle=None):
        super().__init__()
        self.combo=combo;self.models=models or {};self.toggle=toggle
        self.setObjectName('DimLabel')
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True);self.setMinimumWidth(0)
        policy=QSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True);self.setSizePolicy(policy)
        combo.currentTextChanged.connect(self.refresh)
        if toggle is not None:toggle.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self,*_args):
        name=self.combo.currentText()
        model=self.models.get(self.combo.currentData())
        if self.toggle is not None and not self.toggle.isChecked():
            name=tr('model_traditional');model=None
        if model:name+=' · '+model
        self.setText(tr('model_selection_hint').format(model=name))
        self.setToolTip(tr('model_selection_hint_tooltip'))
