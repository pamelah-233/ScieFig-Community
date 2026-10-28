"""Deterministic startup work boundaries; timing lives in the fresh-process benchmark."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_fresh(code):
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QPA_FONTDIR='C:/Windows/Fonts'),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize('module', [
    'core.plotting', 'core.plotting.style_presets', 'core.utils.json',
    'core.plotting.statistics_plot', 'core.plotting_densitometry',
    'core.export',
    'ui.widgets.preview_canvas', 'ui.widgets.style_lab_workspace',
])
def test_non_rendering_import_does_not_load_plotting_or_dataframe_backends(module):
    run_fresh(f"import sys, importlib; importlib.import_module({module!r}); "
              "assert 'matplotlib' not in sys.modules; assert 'pandas' not in sys.modules")


def test_complete_initial_window_does_not_load_plotting_backends_before_any_plot():
    run_fresh("""
import sys
from pathlib import Path
import main
app = main.QApplication([])
app.setStyleSheet(main.load_qss(main.resolve_resources_dir()))
from ui.main_window import MainWindow
window = MainWindow(main.resolve_resources_dir())
try:
    window.show()
    app.processEvents()
    assert window.isVisible()
    assert window.active_module == 'qpcr'
    assert [panel.panel_type for panel in window.project.panels] == [
        'qPCR_bar_scatter', 'western_blot', 'fluorescence', 'external_image', 'ihc', 'brightfield']
    for name in ('data_panel', 'wb_canvas', 'fluoro_workspace', 'ihc_workspace',
                 'brightfield_workspace', 'image_browser_workspace', 'composer_canvas', 'stylelab_workspace'):
        assert getattr(window, name) is not None
    assert not window.is_project_dirty()
    assert 'matplotlib' not in sys.modules
    assert 'pandas' not in sys.modules
finally:
    window.close()
    app.processEvents()
""")


def test_plotting_package_preserves_actual_function_identity_and_first_render():
    import pandas as pd
    import core.plotting as api
    from core.plotting.qpcr_plot import build_qpcr_figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    assert api.build_qpcr_figure is build_qpcr_figure
    frame = pd.DataFrame({'target': ['Gene'] * 4, 'group': ['A', 'A', 'B', 'B'],
                          'fold_change': [1., 1.2, 2., 2.2],
                          'mean_fold_change': [1.1, 1.1, 2.1, 2.1], 'error': [.1] * 4})
    original = frame.copy(deep=True)
    figure = api.build_qpcr_figure(frame, {})
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    assert figure.dpi == 150
    assert figure.get_size_inches().tolist() == [3.5, 2.8]
    assert len(figure.axes[0].patches) == 2
    assert frame.equals(original)


def test_json_scalar_fast_path_keeps_missing_values_without_loading_pandas():
    run_fresh("""
import sys
import numpy as np
from core.utils.json import make_json_safe
assert make_json_safe(None) is None
assert make_json_safe(float('nan')) is None
assert make_json_safe(np.float64('nan')) is None
assert make_json_safe(np.datetime64('NaT')) is None
assert make_json_safe(np.int64(7)) == 7
assert type(make_json_safe(np.int64(7))) is int
assert type(make_json_safe(np.float64(1.25))) is float
assert make_json_safe(np.bool_(True)) is True
assert make_json_safe('sample') == 'sample'
assert 'pandas' not in sys.modules
""")


def test_json_dataframe_records_preserve_pandas_missing_value_semantics():
    from collections import UserDict
    from types import MappingProxyType
    import pandas as pd
    from core.utils.json import dataframe_records, make_json_safe
    assert make_json_safe(pd.NA) is None
    assert make_json_safe(pd.NaT) is None
    frame = pd.DataFrame({'value': pd.Series([1, pd.NA], dtype='Int64'), 'label': ['A', None]})
    assert dataframe_records(frame) == [{'value': 1, 'label': 'A'}, {'value': None, 'label': None}]
    for value in ({'sample': 1}, UserDict({'sample': 1}), MappingProxyType({'sample': 1})):
        assert make_json_safe(value) is value


def test_statistics_figure_preserves_public_class_identity_and_editable_svg():
    import pickle
    from io import BytesIO
    import core.plotting.statistics_plot as api
    from matplotlib.figure import Figure
    from matplotlib import rc_context
    cls = api.StatisticsFigure
    assert issubclass(cls, Figure)
    assert api.StatisticsFigure is cls
    assert cls.__module__ == 'core.plotting.statistics_plot'
    assert cls.__qualname__ == 'StatisticsFigure'
    assert pickle.loads(pickle.dumps(cls)) is cls
    figure = api.build_group_statistics_figure({'plot_data': [
        {'group': 'Control', 'value': 1.}, {'group': 'Control', 'value': 1.2},
        {'group': 'Treated', 'value': 2.}, {'group': 'Treated', 'value': 2.3}]})
    assert type(figure) is cls
    assert figure.dpi == 180
    assert figure.get_size_inches().tolist() == [3.4, 2.6]
    stream = BytesIO()
    with rc_context({'svg.fonttype': 'path'}):
        figure.savefig(stream, format='svg')
    assert b'<text' in stream.getvalue()


def test_concurrent_first_statistics_figure_requests_share_one_picklable_class():
    run_fresh("""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pickle
import core.plotting.statistics_plot as api
barrier = Barrier(2)
def first_request():
    barrier.wait(timeout=5)
    return api.StatisticsFigure
with ThreadPoolExecutor(max_workers=2) as pool:
    futures = [pool.submit(first_request) for _ in range(2)]
    classes = [future.result(timeout=10) for future in futures]
assert classes[0] is classes[1] is api.StatisticsFigure
assert type(pickle.loads(pickle.dumps(classes[0]()))) is classes[0]
""")
