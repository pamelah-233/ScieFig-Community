"""Expose the existing calibration editor at intake, not during analysis."""
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QScrollArea


class ImportScaleController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        from ui.controllers.scale_position_history import ScalePositionHistory
        self.positions = ScalePositionHistory(window)
        self.editors = {}
        self.intake_groups = {}
        from ui.widgets.workflow_forms import readable_forms
        for module in ('fluoro', 'ihc', 'brightfield'):
            workspace = getattr(window, module + '_workspace')
            editor = (window.data_panel.brightfield_calibration_controls if module == 'brightfield'
                      else workspace.calibration_controls)
            editor.parentWidget().layout().removeWidget(editor)
            content = workspace.workflow_panel.findChild(QScrollArea).widget().layout()
            section = workspace.scale_display_section
            section.layout().setContentsMargins(14, 0, 14, 0)
            index = content.indexOf(section)
            group = QWidget()
            group.setProperty('workflowPlain', True)
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(14, 0, 14, 0)
            group_layout.addWidget(editor)
            editor.layout().setContentsMargins(0, 8, 0, 8)
            content.insertWidget(index, group)
            readable_forms(editor)
            readable_forms(workspace.scale_appearance_group)
            self.editors[module] = editor
            self.intake_groups[module] = group
            editor.scale_style_provider = (workspace._layout_scale_bar_template_from_controls if module == 'fluoro'
                                           else workspace._scale_bar_template_from_controls)
            editor.scale_style_applied.connect(lambda template, name=module: self.apply_preset_style(name, template))
            if module != 'fluoro':
                from ui.widgets.microscopy_scale_preview import paint_scale_preview
                workspace.canvas.scale_overlay_painter = lambda display, source, ws=workspace, key=module+'_layout': paint_scale_preview(ws, key, display, source)

    def apply_preset_style(self, module, template):
        from ui.widgets.calibration_controls import sync_scale_appearance_controls
        workspace = getattr(self.window, module + '_workspace')
        sync_scale_appearance_controls(workspace, template)
        if module == 'fluoro':
            workspace._apply_layout_scale_bar(current_only=True)
        else:
            workspace._apply_scale_bar(current_only=True)

    def sync(self, module, step):
        for name, editor in self.editors.items():
            workspace = getattr(self.window, name + '_workspace')
            intake = name == module and step == 0
            layout = name == module and step == (3 if name == 'ihc' else 4)
            if intake:
                content = workspace.workflow_panel.findChild(QScrollArea).widget().layout()
                image_index = content.indexOf(workspace.active_image_group)
                if image_index >= 0 and content.indexOf(self.intake_groups[name]) != image_index + 1:
                    for widget in (self.intake_groups[name], workspace.scale_display_section):
                        content.removeWidget(widget)
                    image_index = content.indexOf(workspace.active_image_group)
                    for offset, widget in enumerate((self.intake_groups[name], workspace.scale_display_section), 1):
                        content.insertWidget(image_index + offset, widget)
            editor.setVisible(intake)
            self.intake_groups[name].setVisible(intake)
            has_image = workspace.panel is not None and workspace.panel.active_image() is not None
            editor.native_status.setVisible(has_image)
            editor.status.hide()
            workspace.scale_display_section.setVisible(intake or layout)
            workspace.scale_display_section.setExpanded(True)
            workspace.scale_display_section.toggle.hide()
            form = workspace.scale_appearance_group.layout()
            form.setRowVisible(workspace.scale_bar_manual_um_per_px_spin, layout)
            if name == 'fluoro':
                workspace.canvas.view.scale_editing_enabled = intake or layout
                if not (intake or layout) and workspace.canvas.view.roi_mode == 'scale_line':
                    workspace.canvas.set_roi_mode('')
            else:
                canvas = workspace.canvas
                changed = getattr(canvas, 'scale_editing_enabled', True) != intake
                canvas.scale_editing_enabled = intake
                if not intake:
                    canvas.set_line_selection_enabled(False)
                if changed:
                    canvas._refresh_image_display()
