"""Include installed dependency copyright/license materials with the worker."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys

target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    shutil.copyfile(python_license, target / 'Python-LICENSE.txt')
for dist in distributions():
    for entry in dist.files or []:
        if any(part.lower().startswith(('license', 'copying', 'notice', 'copyright')) for part in entry.parts):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                dest = target / dist.metadata['Name'] / Path(*entry.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
