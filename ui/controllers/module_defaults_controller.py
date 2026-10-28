"""Restore constructor defaults, independently of project/session parameters."""
from copy import deepcopy

from PyQt6.QtCore import QObject, QSignalBlocker
from PyQt6.QtWidgets import QAbstractSlider, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QPushButton, QSpinBox

from core.utils.i18n import bind_i18n, tr


class ModuleDefaultsController(QObject):
    PRESET_MODULES = {'fluoro':'fluorescence','ihc':'ihc','brightfield':'brightfield'}

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        # Created before any project/session is restored. Never overwrite these
        # snapshots with a measurement, a user preset, or the current widgets.
        self._analysis_defaults = {module:deepcopy(window.analysis_state.capture_widget_state(preset))
                                   for module,preset in self.PRESET_MODULES.items()}
        self._widgets = {}
        for module in ('qpcr','wb','fluoro','ihc','brightfield'):
            owners = [(window.data_panel, module+'_')]
            if module=='qpcr':owners=[(window.data_panel,'instrument_')]
            if module in self.PRESET_MODULES:
                owners.append((getattr(window,module+'_workspace'),''))
                deck=window.measurement_tasks.get(module)
                if deck:owners.extend((controller.controls,'') for controller in deck.controllers.values())
            entries=[];seen=set()
            for owner,prefix in owners:
                for name,widget in vars(owner).items():
                    if not name.startswith(prefix) or any(word in name for word in ('scale_','calibration','um_per_px','active_image','reference_group','layout_','text_')):
                        continue
                    if module=='wb' and any(word in name for word in ('roi_','crop_','rotation','flip_','line_','marker_','protein_')):
                        continue
                    if id(widget) in seen:continue
                    if isinstance(widget,(QAbstractSlider,QSpinBox,QDoubleSpinBox)):entry=(widget,'value',widget.value())
                    elif isinstance(widget,QCheckBox):entry=(widget,'checked',widget.isChecked())
                    elif isinstance(widget,QComboBox) and widget.count():entry=(widget,'index',widget.currentIndex())
                    else:continue
                    seen.add(id(widget));entries.append(entry)
            self._widgets[module]=entries
        self._browser_style=deepcopy(window.image_browser_workspace._scale_preset_style())
        header=window.v9_shell.right_panel.findChild(QFrame,'V9RightPanelHeader')
        self.button=bind_i18n(QPushButton(), 'module_restore_defaults')
        self.button.setObjectName('ModuleRestoreDefaultsButton')
        bind_i18n(self.button,'module_restore_defaults_hint','toolTip')
        header.layout().insertWidget(1,self.button)
        self.button.clicked.connect(self.reset_current)
        window.stylelab_workspace.defaults_requested.connect(self.reset_current)
        window._analysis_job_service.busy_changed.connect(self._busy_changed)

    def _busy_changed(self,*_args):
        self.button.setEnabled(not self.window._analysis_job_service.has_busy_jobs())

    def reset_current(self):
        w=self.window;module=w.active_module
        if w._analysis_job_service.has_busy_jobs():return
        deck=w.measurement_tasks.get(module)
        task=deck.current_task() if deck else None
        with w.analysis_state.suspend_capture():
            for widget,kind,value in self._widgets.get(module,[]):
                with QSignalBlocker(widget):
                    if kind=='value':widget.setValue(value)
                    elif kind=='checked':widget.setChecked(value)
                    elif value<widget.count():widget.setCurrentIndex(value)
            preset=self.PRESET_MODULES.get(module)
            if preset:
                state=deepcopy(self._analysis_defaults[module])
                if module=='fluoro':
                    state['measure_mode']=task
                    ws=w.fluoro_workspace;image=ws._active_image()
                    ws._display_balance.cancel()
                    if image:
                        image['segmentation_input']='raw'
                        image['view_mode']='merge'
                        for channel in image.get('channels',[]):
                            channel['display_processing']={}
                            if channel.get('color_source')=='browser_preview':
                                from core.image_analysis.io_engine import preview_display_max
                                channel['display_min']=0.
                                channel['display_max']=preview_display_max(ws.panel.image_data(str(image['uid'])))
                            else:
                                for axis in ('min','max'):
                                    channel['display_'+axis]=channel.get('auto_contrast_'+axis,channel['display_'+axis])
                        ws._populate_channel_controls();ws._refresh_canvas()
                else:
                    getattr(w,module+'_workspace').white_balance.reset()
                    if module=='brightfield':state['measure_type']=task
                if deck:deck.select(task)
                w.analysis_state.apply_widget_state(preset,state)
                if module=='fluoro' and image:
                    method=deck.controllers['dapi'].controls.method.currentData()
                    image['nuclei_methods']={str(index):method for index,_ in enumerate(image.get('channels',[]))}
                if module=='brightfield':
                    image=w._active_brightfield_panel().active_image()
                    if image:image['myotube_method']=deck.controllers['myotube'].controls.method.currentData()
                if module=='ihc':
                    w.data_panel.set_ihc_overlay_opacity(w.data_panel.ihc_overlay_opacity())
            elif module=='wb':
                panel=w._active_wb_panel()
                if panel.data.get('image_data'):
                    panel.set_display_adjustments(brightness=0,contrast=0)
                    strips=panel.data.get('band_strips',[])
                    index=int(panel.data.get('selected_strip_index',0))
                    if 0<=index<len(strips):
                        adjustments=strips[index].setdefault('adjustments',{})
                        adjustments.update(brightness=0,contrast=0)
                    w.wb_canvas.render()
            elif module=='viewer':
                w.image_browser_workspace._apply_scale_preset_style(deepcopy(self._browser_style))
            elif module=='composer':
                workspace=w.composer_workspace
                if workspace._focus_mode:workspace.toggle_focus()
                workspace.activate_tool('select');workspace.guides.setChecked(True)
            elif module=='stylelab':
                w.stylelab_workspace.restore_defaults()
            if module!='stylelab':self._reset_view(module)
        if module in self.PRESET_MODULES:
            w.analysis_state.capture_outgoing(self.PRESET_MODULES[module])
        if deck:
            deck.scroll.verticalScrollBar().setValue(0)
        w.workflow_ui.schedule_sync()
        w.statusBar().showMessage(tr('module_defaults_restored'),3000)

    def _reset_view(self,module):
        w=self.window
        canvas=(w.wb_canvas if module=='wb' else w.fluoro_workspace.canvas if module=='fluoro' else
                w.preview_canvas if module=='qpcr' else w.image_browser_workspace.canvas if module=='viewer' else
                w.composer_workspace.canvas if module=='composer' else getattr(w,module+'_workspace').canvas_stack.currentWidget())
        controls=canvas.zoom_controls
        if hasattr(controls,'object_controls'):
            for name in ('contains_button','snap_button','marquee_button'):
                getattr(controls,name).setChecked(False)
        controls.fit_button.click()
