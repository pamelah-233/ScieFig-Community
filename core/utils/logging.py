from __future__ import annotations

import traceback
import sys
from pathlib import Path

from core.utils.time import utc_now_iso


LOG_DIR = (Path.home() / "Library" / "Logs" / "ScieFig" if sys.platform == "darwin"
           else Path(__file__).resolve().parents[2] / "logs")
ERROR_LOG = LOG_DIR / "sciefig_error.log"


def log_exception(context: str, exc: BaseException) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with ERROR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now_iso()}] {context}\n")
        handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        handle.write("\n")
    return ERROR_LOG

