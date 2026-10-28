import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]


def run_fresh(code):
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,
        env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


def test_reading_guided_ui_defaults_does_not_import_image_analysis_engines():
    run_fresh("import sys; from core.image_analysis.brightfield_guided import BRIGHTFIELD_PREVIEW_DEBOUNCE_MS; assert BRIGHTFIELD_PREVIEW_DEBOUNCE_MS==300; assert 'scipy.ndimage' not in sys.modules; assert 'skimage' not in sys.modules")


def test_opening_empty_style_workspace_does_not_import_plotting():
    run_fresh("import sys; from PyQt6.QtWidgets import QApplication; app=QApplication([]); from ui.local_style_extraction.app import LocalStyleExtractionWindow; window=LocalStyleExtractionWindow(); assert window.api.can_extract and window.api.can_preview; assert 'matplotlib.pyplot' not in sys.modules; window.worker.shutdown(); assert window.worker.wait(5000); window.close()")


def test_main_window_import_does_not_load_unused_segmentation_dependencies():
    run_fresh("import sys; import ui.main_window; assert 'scipy.ndimage' not in sys.modules")


def test_empty_window_first_paint_does_not_load_segmentation_or_plotting():
    run_fresh('''
import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
app=QApplication([])
from ui.main_window import MainWindow
window=MainWindow(Path('resources'))
window._confirm_destructive_action=lambda *args:True
try:
    window.show();app.processEvents()
    assert 'scipy.ndimage' not in sys.modules
    assert 'matplotlib.pyplot' not in sys.modules
finally:
    window.close();app.processEvents()
''')


def test_count_table_metadata_does_not_load_counting_engines():
    run_fresh("import sys; from core.image_analysis.fluorescence_counting import count_provenance; assert count_provenance({'algorithm':'log'})[0]=='LoG'; assert 'scipy.ndimage' not in sys.modules")


def test_lazy_analysis_exports_remain_original_functions_and_masks_unchanged():
    import numpy as np
    from core.image_analysis import clean_mask, analyze_brightfield_image
    from core.image_analysis.brightfield import analyze_brightfield_image as actual
    assert analyze_brightfield_image is actual
    mask=np.zeros((7,7),dtype=bool);mask[1:6,1:6]=True;mask[3,3]=False
    cleaned=clean_mask(mask,fill_holes=True)
    assert cleaned.dtype==bool and cleaned.sum()==25
    assert not mask[3,3]


@pytest.mark.parametrize('chart',['line','multipanel','box_dot','violin_dot'])
def test_deferred_plotting_still_renders_full_preview_and_svg(chart,tmp_path):
    from PIL import Image
    from ui.local_style_extraction.demo_common import SCHEMA
    from ui.local_style_extraction.preview import render_comparison
    from ui.local_style_extraction.chart_types import render_raw_comparison
    render=render_raw_comparison if chart.endswith('_dot') else render_comparison
    result=render({'schema':SCHEMA,'resolved_style':{}},str(tmp_path),chart=chart)
    with Image.open(result['styled_png']) as image:
        assert image.width>=800 and image.height>=500
    assert Path(result['styled_svg']).stat().st_size>1000
