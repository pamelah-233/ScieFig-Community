from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QFrame, QGridLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from core.utils.i18n import bind_i18n, tr


class StyleSidebar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StyleSidebar")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("StyleSidebarScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self.scroll_area, 1)

        self.default_content = self._build_default_content()
        self.stylelab_content = self._build_stylelab_content()
        self.scroll_area.setWidget(self.default_content)
        self._module_id = "qpcr"
        self.retranslate()

    def retranslate(self) -> None:
        self.title.setText(tr("style"))
        self.set_module(self._module_id)

    def _build_default_content(self) -> QWidget:
        content = QWidget()
        content.setObjectName("StyleSidebarDefaultContent")
        content.setMinimumWidth(0)

        self.title = bind_i18n(QLabel(), "style")
        self.title.setObjectName("SectionLabel")
        self.module_label = QLabel()
        self.module_label.setObjectName("MutedLabel")
        self.module_label.setWordWrap(True)
        self.stats_label = self._muted("No qPCR statistics yet.")

        self.preset = QComboBox()
        self.preset.setObjectName("StyleSidebarPresetCombo")
        self.preset.addItems(["Nature", "Cell", "Science"])
        self.palette = QComboBox()
        self.palette.setObjectName("StyleSidebarPaletteCombo")
        self.palette.addItems(["Grayscale", "Classic", "Colorblind", "Vibrant"])

        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(self.module_label)
        layout.addSpacing(8)
        layout.addWidget(self._label("JOURNAL PRESET"))
        layout.addWidget(self.preset)
        layout.addWidget(self._label("TYPOGRAPHY"))
        layout.addWidget(self._muted("Font: Arial | Label: 7 px | Tick: 6 px"))
        layout.addWidget(self._label("COLOR PALETTE"))
        layout.addWidget(self.palette)
        layout.addWidget(self._label("EXPORT"))
        layout.addWidget(self._muted("PDF | 600 DPI | RGB | 89 mm"))
        layout.addWidget(self._label("STATISTICS"))
        layout.addWidget(self.stats_label)
        layout.addStretch(1)
        return content

    def _build_stylelab_content(self) -> QWidget:
        content = QWidget()
        content.setObjectName("StyleLabSidebarContent")
        content.setMinimumWidth(0)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        layout.addWidget(self._stylelab_group(
            "Experimental Feature Reserve",
            [self._muted("Not part of the core module workflow yet. This reserves the V9 UI area for journal presets and future style extraction from images/PDFs.")],
        ))

        preset_combo = QComboBox()
        preset_combo.addItems(["Nature", "Cell", "Science", "Custom"])
        scope_combo = QComboBox()
        scope_combo.addItems(["Composer defaults", "Module layout exports", "Both"])
        label_combo = QComboBox()
        label_combo.addItems(["lowercase", "uppercase"])
        dpi_combo = QComboBox()
        dpi_combo.addItems(["600", "300", "1200"])
        layout.addWidget(self._stylelab_form_group(
            "Journal Presets",
            [
                ("Active preset", preset_combo),
                ("Scope", scope_combo),
                ("Panel labels", label_combo),
                ("Default DPI", dpi_combo),
            ],
            [self._button_row(("Apply to New Figures", "Duplicate Preset"))],
        ))

        reference_combo = QComboBox()
        reference_combo.addItems(["PDF figure", "Image file", "Existing Composer figure"])
        layout.addWidget(self._stylelab_form_group(
            "Extract Style From Reference",
            [
                ("Reference type", reference_combo),
                ("Status", self._readonly_value("Reserved, not wired")),
                ("Detected font", self._readonly_value("pending")),
                ("Detected palette", self._readonly_value("pending")),
            ],
            [
                self._button_row(("Import Reference PDF", "Import Reference Image")),
                self._button_row(("Extract Style", "Save Custom Preset"), disabled=True),
            ],
        ))

        table = QWidget()
        table.setObjectName("StyleLabPresetTable")
        table_layout = QGridLayout(table)
        table_layout.setContentsMargins(8, 8, 8, 8)
        table_layout.setHorizontalSpacing(8)
        table_layout.setVerticalSpacing(6)
        rows = (
            ("Name", "Source", "State", "Use"),
            ("Nature", "Built-in", "Ready", "Default"),
            ("My Lab Figure", "Reserved", "Draft", "Later"),
        )
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                cell = QLabel(value)
                cell.setObjectName("StyleLabTableHeader" if row_index == 0 else "StyleLabTableCell")
                cell.setWordWrap(True)
                cell.setToolTip(value)
                table_layout.addWidget(cell, row_index, column_index)
        layout.addWidget(self._stylelab_group("Saved Custom Presets", [table]))
        layout.addStretch(1)
        return content

    def _stylelab_group(self, title: str, widgets: list[QWidget]) -> QFrame:
        group = QFrame()
        group.setObjectName("StyleLabSidebarGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(9)
        layout.addWidget(self._label(title.upper()))
        for widget in widgets:
            layout.addWidget(widget)
        return group

    def _stylelab_form_group(
        self,
        title: str,
        rows: list[tuple[str, QWidget]],
        actions: list[QWidget],
    ) -> QFrame:
        form = QWidget()
        form_layout = QGridLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setHorizontalSpacing(8)
        form_layout.setVerticalSpacing(8)
        for row_index, (label_text, widget) in enumerate(rows):
            label = self._muted(label_text)
            label.setToolTip(label_text)
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if isinstance(widget, QLabel):
                widget.setWordWrap(True)
            form_layout.addWidget(label, row_index, 0)
            form_layout.addWidget(widget, row_index, 1)
        widgets = [form]
        widgets.extend(actions)
        return self._stylelab_group(title, widgets)

    def _button_row(self, labels: tuple[str, ...], disabled: bool = False) -> QWidget:
        row = QWidget()
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(6)
        for index, label in enumerate(labels):
            button = self._button(label)
            button.setEnabled(not disabled)
            layout.addWidget(button, index // 2, index % 2)
        return row

    def _readonly_value(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("StyleLabReadonlyValue")
        label.setWordWrap(True)
        label.setToolTip(text)
        return label

    def _button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(text)
        button.setMinimumWidth(0)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return button

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionLabel")
        label.setWordWrap(True)
        label.setToolTip(text)
        return label

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("DimLabel")
        label.setWordWrap(True)
        label.setToolTip(text)
        return label

    def set_module(self, module_id: str) -> None:
        self._module_id = module_id
        if module_id == "stylelab":
            if self.scroll_area.widget() is not self.stylelab_content:
                self.scroll_area.takeWidget()
                self.scroll_area.setWidget(self.stylelab_content)
            return
        if self.scroll_area.widget() is not self.default_content:
            self.scroll_area.takeWidget()
            self.scroll_area.setWidget(self.default_content)
        labels = {
            "qpcr": tr("v9_module_qpcr"),
            "wb": tr("v9_module_wb"),
            "fluoro": tr("v9_module_fluoro"),
            "ihc": tr("v9_module_ihc"),
            "brightfield": tr("v9_module_brightfield"),
            "composer": tr("v9_module_composer"),
            "stylelab": tr("v9_module_stylelab"),
        }
        self.module_label.setText(labels.get(module_id, module_id))
        if module_id != "qpcr":
            self.stats_label.setText(tr("available_qpcr_after_import"))

    def set_qpcr_statistics(self, text: str) -> None:
        self.stats_label.setText(text)
