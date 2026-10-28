import subprocess
import sys
from pathlib import Path


def test_importing_image_canvases_does_not_load_plotting_engines():
    result=subprocess.run([sys.executable,'-c',
        "import sys; from ui.widgets import fluoro_canvas, wb_canvas; print('matplotlib' in sys.modules, 'pandas' in sys.modules)"],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,check=True)
    assert result.stdout.strip() == 'False False'
