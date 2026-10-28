from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from core.constants import AI_PACK_MODELS_SUBDIR, AI_PACK_SUBDIR, APP_NAME
from core.ai_pack.manifest import validate_cache_subdir
from core.utils.hashing import verify_hash


def ai_pack_cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / APP_NAME / AI_PACK_SUBDIR / AI_PACK_MODELS_SUBDIR


def _safe_filename_from_source_url(source_url: str, fallback: str) -> str:
    parsed = urlparse(str(source_url))
    path_segments = [segment for segment in parsed.path.replace("\\", "/").split("/") if segment]
    if any(segment in {".", ".."} for segment in path_segments):
        candidate = Path(parsed.path).name or str(fallback or "")
        raise ValueError(f"Unsafe model cache filename: {candidate}")

    name = Path(parsed.path).name or str(fallback or "")
    if not name or name in {".", ".."}:
        raise ValueError(f"Unsafe model cache filename: {name}")
    if Path(name).name != name:
        raise ValueError(f"Unsafe model cache filename: {name}")
    if "/" in name or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe model cache filename: {name}")
    return name


def model_cache_path(entry, cache_root: str | Path | None = None) -> Path:
    root = Path(cache_root) if cache_root is not None else ai_pack_cache_root()
    try:
        cache_subdir = validate_cache_subdir(entry.cache_subdir)
    except ValueError as exc:
        raise ValueError(f"Unsafe model cache subdir: {entry.cache_subdir}") from exc

    filename = _safe_filename_from_source_url(entry.source_url, f"{entry.model_id}.onnx")
    return root / cache_subdir / filename


def ensure_cache_dir(entry, cache_root: str | Path | None = None) -> Path:
    target = model_cache_path(entry, cache_root=cache_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def is_model_cached(entry, cache_root: str | Path | None = None) -> bool:
    target = model_cache_path(entry, cache_root=cache_root)
    return target.is_file() and verify_hash(target, entry.sha256)
