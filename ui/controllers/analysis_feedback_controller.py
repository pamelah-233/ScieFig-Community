"""Show real job state beside the current task, scoped to its image and channel."""
from time import monotonic
from PyQt6.QtCore import QObject, QTimer


class AnalysisFeedbackController(QObject):
    MODULES = {
        'nuclei_fluoro': 'fluoro', 'nuclei_brightfield': 'brightfield',
        'central_nuclei': 'brightfield', 'myotube_fluoro': 'fluoro',
        'myotube_brightfield': 'brightfield', 'brightfield-cellpose': 'brightfield',
        'brightfield-fiber': 'brightfield', 'brightfield': 'brightfield', 'ihc': 'ihc',
    }

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.jobs = {}
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.sync)
        service = window._analysis_job_service
        service.busy_changed.connect(self._busy)
        service.job_finished.connect(self._finished)
        for module, combo in (('fluoro', window.fluoro_workspace.active_image_combo),
                              ('brightfield', window.data_panel.brightfield_active_image_combo),
                              ('ihc', window.data_panel.ihc_active_image_combo)):
            combo.currentIndexChanged.connect(self.sync)
        for controller in (window.nuclei_controllers['fluoro'], window.myotube_controllers['fluoro']):
            controller.controls.channel.currentIndexChanged.connect(self.sync)

    def _context(self, module):
        w = self.window
        panel = getattr(w, '_active_' + module + '_panel')()
        task = w.measurement_tasks[module].current_task() if module in w.measurement_tasks else None
        channel = None
        if module == 'fluoro':
            controller = (w.nuclei_controllers[module] if task == 'dapi'
                          else w.myotube_controllers[module] if task == 'myotube' else None)
            if controller is not None:
                channel = controller.controls.channel.currentData()
        return (id(w.project), id(panel), (panel.active_image() or {}).get('uid'),
                w.workflow_ui._step(module), task, channel)

    def _busy(self, key, busy):
        module = 'fluoro' if key.startswith('fluoro-analysis:') else self.MODULES.get(key)
        if module is None or not busy:
            return
        self.jobs[key] = dict(module=module, context=self._context(module),
                              state='running', started=monotonic(), elapsed=0.0, error=None)
        self.timer.start()
        self.sync()

    def _finished(self, key, state, error):
        job = self.jobs.get(key)
        if job is None:
            return
        job.update(state=state, elapsed=monotonic()-job['started'], error=error)
        if not any(item['state'] == 'running' for item in self.jobs.values()):
            self.timer.stop()
        self.sync()

    def sync(self, *_args):
        module = self.window.active_module
        footer = self.window.workflow_ui.footers.get(module)
        if footer is None:
            return
        context = self._context(module) if module in {'fluoro', 'brightfield', 'ihc'} else None
        matching = [job for job in self.jobs.values() if job['module'] == module and job['context'] == context]
        if not matching:
            footer.set_job_feedback()
            return
        job = max(matching, key=lambda item: item['started'])
        elapsed = monotonic()-job['started'] if job['state'] == 'running' else job['elapsed']
        footer.set_job_feedback(job['state'], elapsed=elapsed, error=job['error'])
