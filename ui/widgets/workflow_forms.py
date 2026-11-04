"""Shared readable forms for workflow, browser and Composer sidebars."""
import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFormLayout, QLabel, QComboBox, QAbstractSpinBox, QSizePolicy, QTableWidget, QHeaderView, QGroupBox


def readable_forms(root):
    """Labels above fields: neither translated text nor controls steal columns."""
    groups=root.findChildren(QGroupBox)+([root] if isinstance(root,QGroupBox) else [])
    for group in groups:
        layout=group.layout()
        if layout is not None:
            margins=layout.contentsMargins()
            layout.setContentsMargins(0,margins.top(),0,margins.bottom())
    forms=[item for item in root.findChildren(QFormLayout)]
    if isinstance(root.layout(),QFormLayout):forms.append(root.layout())
    for form in forms:
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(7);form.setHorizontalSpacing(0)
        for index in range(form.rowCount()):
            item=form.itemAt(index,QFormLayout.ItemRole.LabelRole)
            if item and isinstance(item.widget(),QLabel):
                label=item.widget();label.setWordWrap(True)
                label.setMinimumWidth(min(120,label.fontMetrics().horizontalAdvance(label.text())+6))
                label.setMaximumWidth(16777215)
                policy=QSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
                policy.setHeightForWidth(True);label.setSizePolicy(policy)
                label.setMinimumHeight(label.fontMetrics().height()+3)
    for widget in root.findChildren(QComboBox)+root.findChildren(QAbstractSpinBox):
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        widget.setMinimumHeight(max(30,widget.minimumSizeHint().height()))
    for combo in root.findChildren(QComboBox):
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
    for label in root.findChildren(QLabel):
        if label.wordWrap():
            policy=QSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
            policy.setHeightForWidth(True);label.setSizePolicy(policy)
            label.setMinimumHeight(max(label.minimumHeight(),label.fontMetrics().height()+3))
    for table in root.findChildren(QTableWidget):
        if sys.platform in {'darwin', 'win32'}:
            from ui.widgets.table_presentation import readable_table
            readable_table(table)
            continue
        if table.property('measurementReadable'):continue
        table.setProperty('measurementReadable',True)
        header=table.horizontalHeader();header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(80)
        for index in range(table.columnCount()):
            title=table.horizontalHeaderItem(index)
            table.setColumnWidth(index,max(96,table.fontMetrics().horizontalAdvance(title.text() if title else '')+24))
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
