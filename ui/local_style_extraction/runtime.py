from pathlib import Path
from core.style_preset_registry import default_style_preset_registry_path

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "build" / "local_style_runtime"
CACHE_DIR = default_style_preset_registry_path().parent / "extraction_cache"
