from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_importing_main_window_alone_does_not_import_pandas_or_matplotlib() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import ui.main_window; "
            "print('pandas' in sys.modules, 'matplotlib' in sys.modules)",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False False"
