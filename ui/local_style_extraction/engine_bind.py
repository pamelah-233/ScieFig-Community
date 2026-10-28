from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
_BOUND: EngineAPI | None = None

ENGINE_FUNCS = (
    "inspect_source",
    "render_page",
    "suggest_regions",
    "extract_style",
    "edit_token",
    "save_style",
    "load_style",
)
PREVIEW_FUNCS = ("render_comparison",)


class EngineAPI:
    def __init__(self) -> None:
        self.inspect_source = None
        self.render_page = None
        self.suggest_regions = None
        self.extract_style = None
        self.edit_token = None
        self.save_style = None
        self.load_style = None
        self.render_comparison = None
        self.import_error = ""
        self.missing: list[str] = []
        self.found_modules: list[str] = []

    @property
    def can_open(self) -> bool:
        return callable(self.inspect_source) and callable(self.render_page)

    @property
    def can_suggest(self) -> bool:
        return callable(self.suggest_regions)

    @property
    def can_extract(self) -> bool:
        return callable(self.extract_style)

    @property
    def can_edit(self) -> bool:
        return callable(self.edit_token)

    @property
    def can_save(self) -> bool:
        return callable(self.save_style)

    @property
    def can_load(self) -> bool:
        return callable(self.load_style)

    @property
    def can_preview(self) -> bool:
        return callable(self.render_comparison)

    def explain(self) -> str:
        if self.import_error:
            return self.import_error
        if self.missing:
            return "引擎未就绪：缺少 " + "、".join(self.missing)
        return "引擎已绑定"

    def status_line(self) -> str:
        if self.can_extract and self.can_preview and self.can_open:
            return "引擎已绑定（真实提取，无替身）"
        if self.can_open and not self.can_extract:
            return "引擎部分绑定：可打开来源，提取函数不可用"
        if self.missing or self.import_error:
            return "引擎未就绪，提取已禁用"
        return "引擎未绑定"


def _load_module(name: str):
    return importlib.import_module(f".{name}", package=__package__)


def bind_engine() -> EngineAPI:
    global _BOUND
    if _BOUND is not None and _BOUND.can_open and _BOUND.can_extract and _BOUND.can_preview:
        return _BOUND
    api = EngineAPI()
    demo = str(DEMO_DIR)

    engine_path = DEMO_DIR / "engine.py"
    preview_path = DEMO_DIR / "preview.py"
    if not engine_path.is_file():
        api.missing.append("engine.py")
    if not preview_path.is_file():
        api.missing.append("preview.py")

    if engine_path.is_file():
        try:
            engine_mod = _load_module("engine")
            api.found_modules.append("engine")
            for name in ENGINE_FUNCS:
                fn = getattr(engine_mod, name, None)
                if callable(fn):
                    setattr(api, name, fn)
                else:
                    api.missing.append(f"engine.{name}")
        except Exception as exc:
            api.import_error = f"导入 engine.py 失败：{type(exc).__name__}: {exc}"
            api.missing.append("engine.py(import)")

    if preview_path.is_file():
        try:
            preview_mod = _load_module("preview")
            api.found_modules.append("preview")
            for name in PREVIEW_FUNCS:
                fn = getattr(preview_mod, name, None)
                if callable(fn):
                    setattr(api, name, fn)
                else:
                    api.missing.append(f"preview.{name}")
        except Exception as exc:
            extra = f"导入 preview.py 失败：{type(exc).__name__}: {exc}"
            api.import_error = (api.import_error + "；" + extra).strip("；") if api.import_error else extra
            api.missing.append("preview.py(import)")

    if api.import_error:
        tb = traceback.format_exc(limit=3).strip()
        if tb and tb not in api.import_error:
            api.import_error = api.import_error + " | " + tb.splitlines()[-1]
    _BOUND = api
    return api
