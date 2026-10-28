from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from .runtime import RUNTIME_DIR
DEP_PYTHON = RUNTIME_DIR / "python"
TESS_DIR = RUNTIME_DIR / "tesseract"
TESS_EXE = TESS_DIR / "tesseract.exe"
TESSDATA = TESS_DIR / "tessdata"
MAX_SIDE = 1600
OCR_TIMEOUT_SEC = 20
MAX_WORDS = 12
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def tesseract_run_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return kwargs


def tesseract_status() -> dict[str, Any]:
    if sys.platform == "darwin":
        import importlib.util
        available = importlib.util.find_spec("Vision") is not None
        return {"ok": available, "exe": "macOS Vision", "tessdata": "", "eng": "",
                "exe_exists": available, "eng_exists": available, "method": "macos_vision"}
    exe = TESS_EXE
    tessdata = TESSDATA
    eng = tessdata / "eng.traineddata"
    ok = exe.is_file() and eng.is_file()
    return {
        "ok": ok,
        "exe": str(exe),
        "tessdata": str(tessdata),
        "eng": str(eng),
        "exe_exists": exe.is_file(),
        "eng_exists": eng.is_file(),
    }


def _ensure_image(path: str | Path, rgb=None) -> Path:
    if rgb is not None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgb, mode="RGB").save(dest, format="PNG")
        return dest
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"OCR image not found: {src}")
    with Image.open(src) as image:
        im = image.convert("RGB")
        w, h = im.size
        longest = max(w, h, 1)
        if longest > MAX_SIDE:
            scale = MAX_SIDE / float(longest)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            dest = src.with_name(src.stem + "_ocrbound.png")
            im.save(dest, format="PNG")
            return dest
    return src


def ocr_words(path: str | Path, rgb=None) -> dict[str, Any]:
    if sys.platform == "darwin":
        from .macos_ocr import ocr_words as recognize
        return recognize(_ensure_image(path, rgb=rgb), max_words=MAX_WORDS)
    st = tesseract_status()
    if not st["ok"]:
        return {
            "ok": False,
            "reason": f"local OCR unavailable: tesseract={st['exe_exists']} eng.traineddata={st['eng_exists']} exe={st['exe']}",
            "words": [],
            "status": st,
        }
    image_path = _ensure_image(path, rgb=rgb)
    cmd = [
        st["exe"],
        str(image_path),
        "stdout",
        "tsv",
        "-l",
        "eng",
        "--psm",
        "6",
        "--tessdata-dir",
        st["tessdata"],
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=OCR_TIMEOUT_SEC,
            cwd=str(TESS_DIR),
            env={**os.environ, "TESSDATA_PREFIX": st["tessdata"]},
            check=False,
            **tesseract_run_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"tesseract timed out after {OCR_TIMEOUT_SEC}s", "words": [], "status": st}
    except OSError as exc:
        return {"ok": False, "reason": f"tesseract exec failed: {exc}", "words": [], "status": st}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = err[-1] if err else f"exit {proc.returncode}"
        return {"ok": False, "reason": f"tesseract failed: {detail}", "words": [], "status": st, "stderr": (proc.stderr or "")[:500]}
    words: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(proc.stdout or ""), delimiter="\t")
    for row in reader:
        if str(row.get("level") or "") != "5":
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        letters = sum(1 for ch in text if ch.isalnum())
        if letters < 2:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        try:
            left = int(float(row.get("left") or 0))
            top = int(float(row.get("top") or 0))
            width = int(float(row.get("width") or 0))
            height = int(float(row.get("height") or 0))
        except (TypeError, ValueError):
            continue
        if width < 4 or height < 6:
            continue
        words.append(
            {
                "text": text,
                "bbox": [left, top, width, height],
                "conf": conf,
                "letters": letters,
            }
        )
        if len(words) >= MAX_WORDS:
            break
    return {
        "ok": True,
        "reason": "",
        "words": words,
        "status": st,
        "n_words": len(words),
        "letter_count": sum(int(w["letters"]) for w in words),
        "image": str(image_path),
        "method": "tesseract_tsv",
    }

