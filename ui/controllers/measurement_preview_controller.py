"""Present saved measurements on the existing canvases, without re-analysis."""
from PyQt6.QtCore import QObject, QTimer, QEvent
from PyQt6.QtGui import QPixmap

from core.image_browser import rgb_to_qimage
from core.measurement_review_adapter import measurement_identity


def preview_objects(record, *, color=None):
    """Adapt saved native contours to the shared fluorescence overlay painter."""
    from core.measurement_review import object_rows
    rows = record.get('nuclei')
    if rows is None:
        if record.get('type') == 'object_classification':
            rows = record.get('objects', [])
        else:
            _, rows = object_rows(record)
    result = []
    for row in rows:
        points = row.get('contour_points_px', row.get('contour_px', row.get('contour', [])))
        points = [p if isinstance(p, dict) else dict(x=p[0], y=p[1]) for p in points]
        shade = color or ('#28FF50' if 'nuclei' in record else '#FFD13B')
        if not row.get('include_in_statistics', True) or row.get('classification') == 'excluded':
            shade = '#969696'
        elif not record.get('signal_classification', {}).get('stale'):
            shade = {'negative': '#A0A0A0', 'weak_positive': '#FFBE14',
                     'strong_positive': '#28FF50', 'central': '#14F050',
                     'peripheral': '#FFBE14', 'outside': '#46B4FF',
                     'uncertain': '#FF3CC8'}.get(row.get('signal_class', row.get('classification')), shade)
        result.append(dict(row, contour_points_px=points, overlay_color=shade,
                           object_id=row.get('object_id', row.get('id'))))
    return result


class MeasurementPreviewController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._history = {}
        self._visible = {'fluoro': True, 'brightfield': True}
        self._updating = False
        self._suspended = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.sync)
        window.installEventFilter(self)
        for module, deck in window.measurement_tasks.items():
            deck.selector.currentIndexChanged.connect(self.schedule)
            for controller in deck.controllers.values():
                controller.preview_changed.connect(self.schedule)
        f = window.fluoro_workspace
        f.measurement_preview = self
        f.canvas.refreshed.connect(self.schedule)
        f.analysis_context_changed.connect(self.schedule)
        f.context_stack.currentChanged.connect(self.schedule)
        f.measurement_selected.connect(self.select_fluoro_result)
        f.analysis_completed.connect(self.clear_fluoro_history)
        f.ai_original_preview_button.clicked.connect(lambda: self._choose('fluoro', False))
        f.ai_overlay_preview_button.clicked.connect(lambda: self._choose('fluoro', True))
        b = window.brightfield_workspace
        b.original_preview_button.clicked.connect(lambda: self._choose('brightfield', False))
        b.overlay_preview_button.clicked.connect(lambda: self._choose('brightfield', True))
        b.canvas.preview_mode_changed.connect(self._brightfield_refreshed)
        window.canvas_stack.currentChanged.connect(self.schedule)
        window._analysis_job_service.job_finished.connect(self.schedule)
        for signal in (window.data_panel.brightfield_measurement_selected,
                       window.data_panel.brightfield_ai_measurement_selected):
            signal.connect(self.select_brightfield_result)

    def _choose(self, module, visible):
        self._visible[module] = visible

    def restore_preview_mode(self, module, mode):
        if module in self._visible and mode in {'original', 'overlay'}:
            self._visible[module] = mode == 'overlay'
            self.schedule()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            self._suspended = True
            self._timer.stop()
        elif event.type() == QEvent.Type.Show:
            self._suspended = False
            self.schedule()
        return False

    def _brightfield_refreshed(self, mode, has_overlay):
        if not has_overlay:
            self.schedule()

    def schedule(self, *_args):
        if not self._updating and not self._suspended:
            self._timer.start(0)

    def _key(self, task):
        panel = self.window._active_fluoro_panel()
        return id(panel), (panel.active_image() or {}).get('uid'), task

    def clear_fluoro_history(self):
        task = self.window.measurement_tasks['fluoro'].current_task()
        self._history.pop(self._key(task), None)
        self.schedule()

    def select_fluoro_result(self, record):
        task = 'spot_count' if record.get('count_uuid') else 'ai_assisted' if record.get('ai_measurement_uuid') else 'roi_intensity'
        self.window.measurement_tasks['fluoro'].select(task)
        self._history[self._key(task)] = measurement_identity(record)
        self.schedule()

    def select_brightfield_result(self, identifier):
        panel = self.window._active_brightfield_panel()
        record = panel.measurement_by_uuid(identifier)
        if record is None:
            return
        task = 'fiber' if record.get('type') == 'brightfield_ai_object_count' else 'area'
        self.window.measurement_tasks['brightfield'].select(task)

    def sync(self):
        self._timer.stop()
        if self._updating or self._suspended:
            return
        module = self.window.active_module
        if module not in self.window.measurement_tasks:
            return
        deck = self.window.measurement_tasks[module]
        workspace = getattr(self.window, module+'_workspace')
        step = workspace.context_stack.currentIndex() if module == 'fluoro' else workspace.current_step
        if step != 2:
            return
        self._updating = True
        try:
            task = deck.current_task()
            controller = deck.controllers.get(task)
            if module == 'brightfield':
                if task=='cells':
                    history=getattr(self.window,'measurement_history',None)
                    result=history.selected(module,task) if history else None
                    if result:
                        self.window.data_panel.populate_bright_round_cell_results(result)
                        panel=self.window._active_brightfield_panel()
                        workspace.canvas.set_display_state(QPixmap.fromImage(rgb_to_qimage(panel.render_bright_round_cell_overlay(result))),
                            title=deck.selector.currentText(),body=result.get('image_name',''),is_overlay=True)
                        if not self._visible[module]:workspace.canvas.show_original_preview()
                if controller is not None:
                    result = controller.current_result()
                    if result is None:
                        workspace.canvas.clear_overlay_pixmap()
                    else:
                        workspace.canvas.set_display_state(QPixmap.fromImage(rgb_to_qimage(controller.overlay_rgb())),
                            title=deck.selector.currentText(), body=result.get('image_name', ''), is_overlay=True)
                        if not self._visible[module]:
                            workspace.canvas.show_original_preview()
                return
            if controller is not None:
                result = controller.current_result()
                records = self._controller_records(controller, result) if result is not None else []
                if records:
                    workspace.canvas.show_ai_assisted_overlay(records)
                    workspace.canvas.active_measurement_overlay_uuid = task+':'+str(result.get('measurement_uuid', ''))
                else:
                    workspace.canvas.clear_measurement_overlay()
            else:
                self._show_legacy(workspace, task)
            has_overlay = bool(workspace.canvas._measurement_overlay_items)
            workspace._sync_ai_overlay_buttons(has_overlay=has_overlay, show_overlay=self._visible[module])
        finally:
            self._updating = False

    def _show_legacy(self, workspace, task):
        image = workspace._active_image() or {}
        if task in ('line_profile','colocalization'):
            history=getattr(self.window,'measurement_history',None)
            rows=history.rows('fluoro',task) if history else []
            result=(history.selected('fluoro',task) if history else None) or next((r for r in reversed(rows) if r.get('image_uid')==image.get('uid')),None)
            if result is None:
                workspace.canvas.clear_measurement_overlay()
            elif task=='line_profile':workspace.canvas.show_profile_overlay(result)
            else:
                workspace.canvas.show_ai_assisted_overlay(result.get('outlines',[]))
                workspace.canvas.active_measurement_overlay_uuid='colocalization:'+result['measurement_uuid']
            return
        key = {'spot_count': 'object_counts', 'ai_assisted': 'fluorescence_ai_assisted_measurements',
               'roi_intensity': 'measurements'}.get(task)
        if key is None:
            if str(workspace.canvas.active_measurement_overlay_uuid).split(':')[0] in self.window.measurement_tasks['fluoro'].controllers:
                workspace.canvas.clear_measurement_overlay()
            return
        rows = [row for row in image.get(key, []) if task != 'roi_intensity' or row.get('type') == 'roi_intensity']
        identity = self._history.get(self._key(task))
        chosen = next((row for row in rows if measurement_identity(row) == identity), None)
        if identity is not None and chosen is None:
            self._history.pop(self._key(task), None)
        result = chosen or (rows[-1] if rows else None)
        if result is None:
            workspace.canvas.clear_measurement_overlay()
        elif task == 'spot_count':
            workspace.canvas.show_count_overlay(result)
        elif task == 'ai_assisted':
            workspace.canvas.show_ai_assisted_overlay([chosen] if chosen is not None else rows)
        else:
            workspace.canvas.show_measurement_overlay(result)

    @staticmethod
    def _controller_records(controller, result):
        if getattr(controller, 'kind', None) in {'fusion', 'count_ratio'}:
            if not controller.is_fresh(result):
                return []
            return [dict(channel_index=source.get('channel_index', 0), object_table=preview_objects(source, color=color),
                         ai_measurement_uuid=result['measurement_uuid'])
                    for source, color in zip(result.get('source_snapshots', {}).values(), ('#22D7E6', '#FFBE14'))]
        if getattr(controller, 'kind', None) == 'phenotype' and not controller.is_fresh(result):
            return []
        return [dict(channel_index=result.get('channel_index', result.get('signal_channel_index', 0)),
                     object_table=preview_objects(result), ai_measurement_uuid=result.get('measurement_uuid', ''))]
