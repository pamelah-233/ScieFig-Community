"""Exercise real microscopy jobs from the Windows development executable's modules."""

import argparse
from copy import deepcopy
import ctypes
from ctypes import wintypes
import faulthandler
import json
import os
from pathlib import Path
import sys
import time
import traceback


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def memory_sampler():
    class ProcessMemory(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                'peak_working_set', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged',
                'nonpaged', 'commit', 'peak_commit', 'private_bytes',
            )
        ]

    query = ctypes.WinDLL('psapi').GetProcessMemoryInfo
    query.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD)
    handle = wintypes.HANDLE(-1)

    def sample():
        counters = ProcessMemory()
        counters.size = ctypes.sizeof(counters)
        if not query(handle, ctypes.byref(counters), counters.size):
            raise ctypes.WinError()
        return round(counters.private_bytes / 2**20, 1)

    return sample


def isolate_qt_settings(folder):
    from PyQt6 import QtCore

    class IsolatedSettings(QtCore.QSettings):
        def __init__(self, organization, application):
            super().__init__(str(folder / f'{organization}-{application}.ini'), self.Format.IniFormat)

    QtCore.QSettings = IsolatedSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brightfield', nargs=2, type=Path, required=True)
    parser.add_argument('--ihc', nargs=2, type=Path, required=True)
    parser.add_argument('--fluoro', nargs='+', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='A new directory for profiles and results')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--duration-minutes', type=float, default=0, help='Repeat for at least this long, retaining first and latest exports')
    parser.add_argument('--idle-sample-every', type=int, default=5, help='Sample settled memory every N duration-mode rounds')
    parser.add_argument('--serial', action='store_true', help='Run the two measurements and the import separately for a baseline')
    parser.add_argument('--whole-image', action='store_true', help='Use the default whole-image scope')
    parser.add_argument('--timeout', type=float, default=180, help='Maximum seconds to wait for each job group')
    args = parser.parse_args()
    require(args.rounds > 0 and args.timeout > 0 and args.duration_minutes >= 0 and args.idle_sample_every > 0,
            'Invalid rounds, timeout, duration or sampling interval')
    if args.duration_minutes:
        set_awake = ctypes.WinDLL('kernel32').SetThreadExecutionState
        set_awake.argtypes = (wintypes.DWORD,)
        set_awake.restype = wintypes.DWORD
        require(set_awake(0x80000001), 'Could not keep the system awake for the duration test')
    sources = {key: [path.resolve(strict=True) for path in getattr(args, key)]
               for key in ('brightfield', 'ihc', 'fluoro')}
    for paths in sources.values():
        require(len(set(paths)) == len(paths) and all(path.is_file() for path in paths), 'Use distinct image files')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fault_log = (out / 'faults.log').open('w', encoding='utf-8')
    faulthandler.enable(fault_log)

    def arm_watchdog():
        faulthandler.dump_traceback_later(args.timeout + 5, file=fault_log, exit=True)

    arm_watchdog()
    root = Path(__file__).resolve().parents[1]
    internal = root / '.local-runtime/ScieFig/_internal'
    os.chdir(root)
    executable = root / '.local-runtime/ScieFig/ScieFig_R10.exe'
    os.environ.update(QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORM_PLUGIN_PATH=str(internal / 'PyQt6/Qt6/plugins'),
                      APPDATA=str(out / 'APPDATA'), LOCALAPPDATA=str(out / 'LOCALAPPDATA'),
                      TEMP=str(out / 'temp'), TMP=str(out / 'temp'),
                      REVIEW_EXECUTABLE=str(executable))
    for key in ('APPDATA', 'LOCALAPPDATA', 'TEMP'):
        Path(os.environ[key]).mkdir()
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ.setdefault(key, '2')

    loader = root / '.local-runtime/bundled-dev-imports.py'
    exec(compile(loader.read_text(encoding='utf-8'), str(loader), 'exec'), {'__name__': 'stress_imports'})
    isolate_qt_settings(out / 'settings')
    import numpy as np
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from core.plotting.histology_display import qimage_rgb
    from main import load_qss
    from ui.controllers.analysis_job_service import _AnalysisWorker
    from ui.controllers.model_device_controller import ModelDeviceController
    from ui.controllers.project_open_controller import open_project_path
    from ui.main_window import MainWindow
    from ui.platform_support import configure_application

    report = out / 'events.jsonl'

    def log(event, **data):
        payload = json.dumps({'event': event, **data}, ensure_ascii=True)
        with report.open('a', encoding='utf-8') as stream:
            stream.write(payload + '\n')
        print(payload, flush=True)

    errors = []

    def capture_exception(kind, value, tb):
        errors.append(''.join(traceback.format_exception(kind, value, tb)))

    def unexpected_dialog(_parent, title, body, *_args, **_kwargs):
        errors.append(f'{title}: {body}')
        return QMessageBox.StandardButton.Ok

    def unexpected_question(*values, **kwargs):
        unexpected_dialog(*values, **kwargs)
        return QMessageBox.StandardButton.Cancel

    sys.excepthook = capture_exception
    QMessageBox.warning = QMessageBox.critical = QMessageBox.information = unexpected_dialog
    QMessageBox.question = unexpected_question
    # The pilot uses H&E and DAB CPU measurements. GPU contention is a separate scenario.
    ModelDeviceController.check_current_device = lambda self: None
    app = QApplication([])
    configure_application(app)
    app.setStyleSheet(load_qss(internal / 'resources'))
    window = MainWindow(internal / 'resources')
    window.resize(1500, 960)
    window.show()

    spans = []
    worker_run = _AnalysisWorker.run

    def timed_run(worker):
        start = time.perf_counter()
        try:
            worker_run(worker)
        finally:
            spans.append((worker._key, start, time.perf_counter()))

    _AnalysisWorker.run = timed_run
    service = window._analysis_job_service
    outcomes = []
    service.job_finished.connect(lambda key, status, error: outcomes.append((key, status, str(error) if error else None)))
    memory = memory_sampler()
    samples = []
    last = time.perf_counter()
    interval = .05

    def tick():
        nonlocal last
        now = time.perf_counter()
        samples.append((max(0, now - last - interval), memory()))
        last = now

    heartbeat = QTimer()
    heartbeat.setInterval(round(interval * 1000))
    heartbeat.timeout.connect(tick)
    heartbeat.start()

    def wait_for(predicate, label, interact=None):
        deadline = time.monotonic() + args.timeout
        while not predicate():
            app.processEvents()
            require(not errors, '\n'.join(errors))
            require(time.monotonic() < deadline, f'Timed out: {label}')
            if interact:
                interact()
            time.sleep(.01)
        app.processEvents()
        require(not errors, '\n'.join(errors))

    def submit_import(module):
        window.set_module(module)
        entry = 'import_fluorescence_images' if module == 'fluoro' else f'import_{module}_images'
        getattr(window, entry)([str(path) for path in sources[module]])
        return getattr(window, f'_active_{module}_panel')()

    def prepare(module, measure_second):
        arm_watchdog()
        panel = submit_import(module)
        wait_for(lambda: not service.is_busy(f'import:{module}'), f'{module} import')
        require(len(panel.images()) == 2, f'{module}: expected two imported images')
        first, second = panel.images()
        select = getattr(window, f'select_{module}_image')
        select(first['uid'])
        workspace = getattr(window, f'{module}_workspace')
        controls = workspace.white_balance
        controls.illumination.click()
        controls.illumination_strength.setValue(53)
        controls.auto_button.click()
        correction = deepcopy(first.get('illumination_correction') or {})
        wb = deepcopy(first.get('display_white_balance'))
        require(correction.get('gains') and wb, f'{module}: illumination or white balance was not applied')
        select(second['uid'])
        require(not controls.illumination.isChecked(), f'{module}: inherited the previous image checkbox')
        controls.illumination.click()
        require((second.get('illumination_correction') or {}).get('gains'), f'{module}: missing gains on image 2')
        controls.illumination.click()
        controls.illumination.click()
        controls.illumination_strength.setValue(71)
        controls.auto_button.click()
        select(first['uid'])
        require(controls.illumination_strength.value() == 53 and first['illumination_correction'] == correction
                and first['display_white_balance'] == wb, f'{module}: switching images changed the first image settings')
        target = second if measure_second else first
        select(target['uid'])
        require(controls.illumination_strength.value() == (71 if measure_second else 53),
                f'{module}: the selected image has the wrong correction strength')
        canvas = workspace.canvas
        area = canvas.scroll_area
        for _ in range(5):
            canvas._view_controller.zoom_in()
        app.processEvents()
        require(area.horizontalScrollBar().maximum() or area.verticalScrollBar().maximum(), f'{module}: zoom did not overflow')
        for _ in range(5):
            canvas._view_controller.zoom_out()
        canvas._view_controller.fit()
        app.processEvents()
        require(not (area.horizontalScrollBar().maximum() or area.verticalScrollBar().maximum()), f'{module}: Fit still scrolls')
        raw = panel.source_qimage(target['uid'])
        if not args.whole_image:
            panel.set_image_roi(target['uid'], 'rectangle', {'x': raw.width() // 4, 'y': raw.height() // 4,
                                'w': raw.width() // 3, 'h': raw.height() // 3})
        return panel, target['uid'], deepcopy(target['illumination_correction'])

    started = time.monotonic()
    deadline = started + args.duration_minutes * 60
    index = 0
    log('start', exe=str(executable), exe_mtime=executable.stat().st_mtime, rounds=args.rounds,
        duration_minutes=args.duration_minutes, serial=args.serial, whole_image=args.whole_image, pid=os.getpid())
    while True:
        index += 1
        directory = out / ('latest' if args.duration_minutes and index > 1 else f'round-{index:03}')
        directory.mkdir(exist_ok=True)
        start_memory = memory()
        ihc = prepare('ihc', measure_second=index % 2 == 0)
        brightfield = prepare('brightfield', measure_second=index % 2 == 0)
        window.set_brightfield_workflow_step(2)
        window.measurement_tasks['brightfield'].select('area')
        window.set_module('ihc')
        window.set_ihc_workflow_step(1)
        app.processEvents()
        outcomes.clear()
        spans.clear()
        samples.clear()
        last = time.perf_counter()
        arm_watchdog()
        window.run_ihc_analysis()
        if args.serial:
            wait_for(lambda: not service.has_busy_jobs(), 'solo IHC measurement')
            arm_watchdog()
        window.set_module('brightfield')
        window.run_brightfield_analysis()
        if args.serial:
            wait_for(lambda: not service.has_busy_jobs(), 'solo Brightfield measurement')
            arm_watchdog()
        fluoro_panel = submit_import('fluoro')
        visits = 0
        next_switch = time.monotonic() + .15
        selected_module = 'fluoro'
        navigation = []
        edited_fluoro_uid = None

        def navigate():
            nonlocal visits, next_switch, selected_module, edited_fluoro_uid
            require(window.active_module == selected_module, 'A background result changed the active module')
            if selected_module == 'fluoro' and not edited_fluoro_uid and not service.is_busy('import:fluoro'):
                record = fluoro_panel.active_image()
                if record and (service.is_busy('ihc') or service.is_busy('brightfield')):
                    window._set_fluoro_workflow_step(1)
                    window.fluoro_workspace._display_processing_controls[0].strength.setValue(5)
                    edited_fluoro_uid = record['uid']
            if time.monotonic() >= next_switch:
                selected_module = ('ihc', 'brightfield', 'fluoro', 'viewer')[visits % 4]
                start = time.perf_counter()
                window.set_module(selected_module)
                navigation.append((selected_module, round((time.perf_counter() - start) * 1000, 1)))
                visits += 1
                next_switch = time.monotonic() + .2

        wait_for(lambda: not service.has_busy_jobs(), 'IHC, Brightfield and Fluoro jobs', None if args.serial else navigate)
        compute_samples = samples.copy()
        require(window.active_module == selected_module, 'A background result changed the active module')
        keys = ('ihc', 'brightfield', 'import:fluoro')
        completed = {key: (status, error) for key, status, error in outcomes}
        if all(completed.get(key) == ('completed', None) for key in keys):
            wait_for(lambda: all(any(span[0] == key for span in spans) for key in keys), 'worker timing')
        intervals = {key: (start, end) for key, start, end in spans if key in keys}
        first_start = min((start for start, _ in intervals.values()), default=0)
        log('jobs', number=index, outcomes=outcomes,
            spans_s={key: [round(start - first_start, 3), round(end - first_start, 3)]
                     for key, (start, end) in intervals.items()})
        require(all(completed.get(key) == ('completed', None) for key in keys), f'Job outcomes: {outcomes}')
        overlap = min(intervals[key][1] for key in keys[:2]) - max(intervals[key][0] for key in keys[:2])
        three_way = min(end for start, end in intervals.values()) - max(start for start, end in intervals.values())
        require(args.serial or (overlap > 0 and visits > 0), 'Run did not exercise overlapping computations and navigation')
        require(len(fluoro_panel.images()) == len(sources['fluoro']), 'Fluoro import lost images during overlap')
        require(args.serial or edited_fluoro_uid, 'No Fluoro edit ran while a histology measurement was in progress')
        arm_watchdog()
        expected = {}
        for module, (panel, uid, correction) in (('ihc', ihc), ('brightfield', brightfield)):
            results = panel.analysis[f'{module}_measurements']
            require(len(results) == 1 and results[0]['image_uid'] == uid, f'{module}: wrong result count or image')
            result = results[0]
            used = result['analysis_inputs']['settings']['input_processing']
            expected_scope = 'whole_image' if args.whole_image else 'selected_roi'
            require(used['illumination'] == correction and result['roi']['mode'] == expected_scope,
                    f'{module}: calculation used the wrong illumination or ROI')
            window.set_module(module)
            getattr(window, f'select_{module}_image')(uid)
            target = directory / f'{module}.png'
            getattr(window, f'{module}_workspace').white_balance.export_to_path(target)
            np.testing.assert_array_equal(qimage_rgb(QImage(str(target))), panel._load_image_rgb(uid)[1])
            expected[module] = deepcopy(result)

        project = directory / 'stress.sfig'
        window.project.save_sfig(project)
        require(open_project_path(window, project, confirm_unsaved=False), 'Project did not reopen')
        for module, result in expected.items():
            reopened = getattr(window, f'_active_{module}_panel')().analysis[f'{module}_measurements']
            require(reopened == [result], f'{module}: reopened measurement differs from saved result')
        if edited_fluoro_uid:
            fluoro = getattr(window, '_active_fluoro_panel')()
            record = next(image for image in fluoro.images() if image['uid'] == edited_fluoro_uid)
            processing = (record.get('rgb_display') or {}).get('display_processing') or record['channels'][0].get('display_processing')
            require((processing or {}).get('strength') == 5, 'Fluoro edit did not persist')
        require(not errors, '\n'.join(errors))
        log('round', number=index, elapsed_s=round(time.monotonic() - started, 1), two_compute_overlap_s=round(max(0, overlap), 3),
            three_job_overlap_s=round(max(0, three_way), 3), navigation_count=visits,
            slowest_navigation_ms=sorted(navigation, key=lambda entry: entry[1], reverse=True)[:3],
            task_s={key: round(end - start, 3) for key, (start, end) in intervals.items()},
            qt_timer_p95_late_ms=round(float(np.percentile([delay for delay, _ in compute_samples], 95)) * 1000, 1),
            qt_timer_max_late_ms=round(max(delay for delay, _ in compute_samples) * 1000, 1),
            private_mib_before=start_memory, private_mib_peak=max(value for _, value in compute_samples),
            private_mib_after_reopen=memory())
        finished = time.monotonic() >= deadline if args.duration_minutes else index >= args.rounds
        if finished:
            break
        window._capture_clean_project_baseline()
        window.new_project()
        require(not errors and all(not getattr(window, f'_active_{m}_panel')().images() for m in sources),
                'Could not create a clean project for the next round')
        if args.duration_minutes and (index == 3 or index % args.idle_sample_every == 0):
            arm_watchdog()
            settle_until = time.monotonic() + 30
            wait_for(lambda: time.monotonic() >= settle_until, 'closed-project idle sample')
            handles = wintypes.DWORD()
            kernel = ctypes.WinDLL('kernel32')
            kernel.GetProcessHandleCount.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            require(kernel.GetProcessHandleCount(wintypes.HANDLE(-1), ctypes.byref(handles)), 'Could not read handle count')
            temporary_bytes = sum(path.stat().st_size for path in (out / 'temp').rglob('*') if path.is_file())
            log('idle', after_round=index, private_mib=memory(), handles=handles.value, temporary_bytes=temporary_bytes)
    window._capture_clean_project_baseline()
    require(window.close(), 'Window did not accept close')
    app.processEvents()
    require(not errors, '\n'.join(errors))
    faulthandler.cancel_dump_traceback_later()
    log('pass', rounds=index, elapsed_s=round(time.monotonic() - started, 1))
    if args.duration_minutes:
        set_awake(0x80000000)
    faulthandler.disable()
    fault_log.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    os._exit(0)
