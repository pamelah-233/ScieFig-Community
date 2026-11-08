"""Check real modern and legacy ND2 pixels using the frozen Windows application."""
import shutil
import subprocess
import sys
from pathlib import Path

import nd2
import numpy as np

from update_frozen_application import update_application

exe = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()
work.mkdir(parents=True, exist_ok=True)
samples = {'dims_c2y32x32.nd2': 331776, 'aryeh_b16_14_12.nd2': 24671709}
for name, size in samples.items():
    src = work/name
    subprocess.run(['curl.exe','--fail','--location','--silent','--show-error',
        '--retry','2','--max-time','120',
        f'https://github.com/tlambert03/nd2/releases/download/test-data/{name}',
        '--output',str(src)],check=True)
    assert src.stat().st_size == size
    with nd2.ND2File(src) as handle:
        np.save(src.with_suffix('.npy'), handle.read_frame(0).copy())

(work/'main.py').write_text('''
import faulthandler
import sys
from pathlib import Path

import imagecodecs
import numpy as np
from core.image_analysis.io_engine import read_image

work = Path(sys.argv[1])
with (work/'nd2-smoke.log').open('w') as log:
    faulthandler.enable(file=log)
    assert imagecodecs.JPEG2K.available
    for path in sorted(work.glob('*.nd2')):
        expected = np.load(path.with_suffix('.npy'))
        actual = read_image(path).analysis_data
        assert actual.dtype == expected.dtype
        np.testing.assert_array_equal(actual, expected)
        print('Pixels match:', path.name, actual.shape, actual.dtype, file=log, flush=True)
    (work/'nd2-smoke-ok.txt').write_text('verified', encoding='utf-8')
''', encoding='utf-8')

probe = exe.with_name('ScieFig-ND2-Probe.exe')
assert not probe.exists()
shutil.copy2(exe, probe)
try:
    update_application(probe, work, [])
    subprocess.run([str(probe),str(work)],check=True,timeout=90)
    assert (work/'nd2-smoke-ok.txt').read_text() == 'verified'
finally:
    probe.unlink()
    log = work/'nd2-smoke.log'
    if log.exists():
        print(log.read_text())
