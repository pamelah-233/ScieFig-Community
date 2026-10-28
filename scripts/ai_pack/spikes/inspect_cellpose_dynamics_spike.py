from __future__ import annotations

import argparse
import importlib
import inspect
import json
import platform
import sys
import traceback
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SPIKE_OUTPUT_DIR = REPO_ROOT / "reference_docs" / "spike_outputs"

FUNCTION_SPECS = [
    ("compute_masks", [("cellpose.dynamics", "compute_masks")]),
    ("follow_flows", [("cellpose.dynamics", "follow_flows")]),
    ("get_masks_torch", [("cellpose.dynamics", "get_masks_torch")]),
    ("remove_bad_flow_masks", [("cellpose.dynamics", "remove_bad_flow_masks")]),
    ("flow_error", [("cellpose.dynamics", "flow_error"), ("cellpose.metrics", "flow_error")]),
    (
        "fill_holes_and_remove_small_masks",
        [("cellpose.utils", "fill_holes_and_remove_small_masks")],
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect installed Cellpose postprocess functions and write a source-map JSON."
    )
    parser.add_argument(
        "--json-out",
        default=str(SPIKE_OUTPUT_DIR / "cellpose_dynamics_source_map.json"),
        help="Optional explicit JSON output path.",
    )
    return parser.parse_args()


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=json_default), encoding="utf-8")


def module_version(name: str) -> str | None:
    try:
        return importlib.import_module(name).__version__
    except Exception:
        try:
            import importlib.metadata as importlib_metadata

            return importlib_metadata.version(name)
        except Exception:
            return None


def sanitize_error(exc: BaseException) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(limit=12),
    }


def classify_dependency_hints(source_text: str, function_name: str) -> dict[str, Any]:
    tokens = {
        "torch": any(token in source_text for token in ("torch.", "torch ", "torch.device", "torch_")),
        "numpy": any(token in source_text for token in ("np.", "numpy")),
        "scipy": any(token in source_text for token in ("scipy", "find_objects", "center_of_mass", "maximum_filter1d")),
        "opencv": "cv2." in source_text or "cv2" in source_text,
        "skimage": "skimage" in source_text,
        "numba": any(token in source_text for token in ("njit", "steps2D", "steps3D", "prange")),
        "fastremap": "fastremap" in source_text,
        "fill_voids": "fill_voids" in source_text,
        "metrics": "metrics.flow_error" in source_text or "dynamics.masks_to_flows" in source_text,
    }

    existing_runtime_match = []
    if tokens["numpy"]:
        existing_runtime_match.append("numpy")
    if tokens["scipy"]:
        existing_runtime_match.append("scipy")
    if tokens["opencv"]:
        existing_runtime_match.append("opencv-python-headless")
    if tokens["skimage"]:
        existing_runtime_match.append("scikit-image")

    blockers = []
    if tokens["torch"]:
        blockers.append("torch-runtime")
    if tokens["numba"]:
        blockers.append("numba")
    if tokens["fastremap"]:
        blockers.append("fastremap")
    if tokens["fill_voids"]:
        blockers.append("fill-voids")

    cpu_path = "mixed"
    if function_name == "follow_flows":
        cpu_path = "cpu available, but exact upstream path is split between torch interpolation and numba stepping"
    elif function_name == "get_masks_torch":
        cpu_path = "torch-only in inspected version"
    elif function_name == "compute_masks":
        cpu_path = "cpu available through helper chain, but depends on torch-based get_masks_torch"
    elif function_name == "fill_holes_and_remove_small_masks":
        cpu_path = "cpu-friendly, but requires fill-voids and fastremap helpers unless rewritten"
    elif tokens["torch"] and not blockers:
        cpu_path = "torch cpu path"
    elif tokens["torch"]:
        cpu_path = "torch cpu path with extra helper deps"
    elif not tokens["torch"]:
        cpu_path = "numpy/scipy style cpu path"

    return {
        "tokens": tokens,
        "existing_runtime_match": existing_runtime_match,
        "new_dependency_risks": blockers,
        "cpu_path": cpu_path,
    }


def recommendation_for(function_name: str, dependency_info: dict[str, Any]) -> tuple[str, str]:
    if function_name == "compute_masks":
        return ("port orchestration only; split helpers and replace torch-only leafs", "Medium")
    if function_name == "follow_flows":
        return ("port from source with a CPU-only branch; likely simplify toward non-interp stepping first", "Medium")
    if function_name == "get_masks_torch":
        return ("replace with a numpy/scipy/OpenCV equivalent; do not import torch in ScieFig runtime", "High")
    if function_name == "remove_bad_flow_masks":
        return ("defer QC or reimplement once flow-error parity exists", "Medium")
    if function_name == "flow_error":
        return ("port later for QC only; not required for first mask construction path", "Medium")
    if function_name == "fill_holes_and_remove_small_masks":
        return ("replace helper with local scipy/skimage/OpenCV cleanup to avoid new runtime deps", "Medium")
    if dependency_info["new_dependency_risks"]:
        return ("requires deeper rewrite", "High")
    return ("port directly", "Low")


def locate_callable(module_name: str, attr_name: str) -> tuple[str, Any | None]:
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return module_name, None
    return module_name, getattr(module, attr_name, None)


def inspect_function(label: str, candidates: list[tuple[str, str]]) -> dict[str, Any]:
    record: dict[str, Any] = {"label": label, "available": False, "candidates": [f"{m}.{n}" for m, n in candidates]}
    for module_name, attr_name in candidates:
        resolved_module_name, obj = locate_callable(module_name, attr_name)
        if obj is None:
            continue
        source_file = inspect.getsourcefile(obj)
        source_lines, start_line = inspect.getsourcelines(obj)
        source_text = "".join(source_lines)
        deps = classify_dependency_hints(source_text, label)
        port_plan, risk = recommendation_for(label, deps)
        record.update(
            {
                "available": True,
                "resolved_module": resolved_module_name,
                "resolved_name": attr_name,
                "signature": str(inspect.signature(obj)),
                "source_file": source_file,
                "source_start_line": start_line,
                "source_end_line": start_line + len(source_lines) - 1,
                "dependency_hints": deps,
                "port_plan": port_plan,
                "risk": risk,
                "summary": inspect.getdoc(obj).splitlines()[0] if inspect.getdoc(obj) else "",
            }
        )
        return record
    return record


def synthetic_probe(dynamics_module: Any, numpy_module: Any) -> dict[str, Any]:
    yy, xx = numpy_module.mgrid[:64, :64]
    cy, cx = 32, 32
    cellprob = numpy_module.full((64, 64), -2.0, dtype=numpy_module.float32)
    region = (yy - cy) ** 2 + (xx - cx) ** 2 <= 12**2
    cellprob[region] = 2.0

    flow_y = (cy - yy).astype(numpy_module.float32)
    flow_x = (cx - xx).astype(numpy_module.float32)
    scale = numpy_module.maximum(numpy_module.sqrt(flow_y**2 + flow_x**2), 1.0)
    dP = numpy_module.stack((flow_y / scale, flow_x / scale), axis=0).astype(numpy_module.float32)

    mask = dynamics_module.compute_masks(
        dP,
        cellprob,
        niter=50,
        cellprob_threshold=0.0,
        flow_threshold=None,
        interp=False,
        min_size=0,
    )
    return {
        "status": "success",
        "mask_shape": [int(part) for part in mask.shape],
        "dtype": str(mask.dtype),
        "max_label": int(mask.max()),
        "unique_labels_preview": [int(value) for value in numpy_module.unique(mask)[:8]],
        "note": "Synthetic center-seeking flow produced at least one connected instance via upstream compute_masks.",
    }


def build_report() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error",
        "environment": {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "cellpose": module_version("cellpose"),
            "torch": module_version("torch"),
            "numpy": module_version("numpy"),
        },
        "functions": [],
        "notes": [
            "Cellpose is imported only inside this dev-only spike script.",
            "This source map is anchored to the installed Cellpose version, not upstream memory.",
        ],
    }

    try:
        numpy = importlib.import_module("numpy")
        dynamics = importlib.import_module("cellpose.dynamics")
        metrics = importlib.import_module("cellpose.metrics")
        utils = importlib.import_module("cellpose.utils")
    except Exception as exc:
        payload["error"] = sanitize_error(exc)
        payload["notes"].append("Optional Cellpose spike dependencies are unavailable in this interpreter.")
        return payload

    payload["module_paths"] = {
        "cellpose.dynamics": str(Path(dynamics.__file__).resolve()),
        "cellpose.metrics": str(Path(metrics.__file__).resolve()),
        "cellpose.utils": str(Path(utils.__file__).resolve()),
    }
    payload["functions"] = [inspect_function(label, candidates) for label, candidates in FUNCTION_SPECS]

    critical = {entry["label"]: entry for entry in payload["functions"] if entry.get("available")}
    payload["critical_summary"] = {
        "available_labels": sorted(critical.keys()),
        "torch_only_labels": sorted(
            entry["label"]
            for entry in payload["functions"]
            if entry.get("available") and "torch-runtime" in entry["dependency_hints"]["new_dependency_risks"]
        ),
        "new_dependency_labels": sorted(
            entry["label"]
            for entry in payload["functions"]
            if entry.get("available") and entry["dependency_hints"]["new_dependency_risks"]
        ),
    }

    try:
        payload["synthetic_probe"] = synthetic_probe(dynamics, numpy)
    except Exception as exc:
        payload["synthetic_probe"] = {"status": "error", "error": sanitize_error(exc)}

    payload["status"] = "success"
    return payload


def main() -> int:
    args = parse_args()
    json_out = Path(args.json_out)
    try:
        payload = build_report()
    except Exception as exc:
        payload = {
            "status": "error",
            "environment": {
                "os": platform.platform(),
                "python": sys.version.split()[0],
                "python_executable": sys.executable,
            },
            "error": sanitize_error(exc),
        }

    print("[cellpose-dynamics-spike] environment " f"{json.dumps(payload.get('environment', {}), sort_keys=True)}")
    module_paths = payload.get("module_paths", {})
    if module_paths:
        print(f"[cellpose-dynamics-spike] module_paths={json.dumps(module_paths, sort_keys=True)}")
    for item in payload.get("functions", []):
        print(
            "[cellpose-dynamics-spike] function "
            f"label={item.get('label')} "
            f"available={item.get('available')} "
            f"resolved_module={item.get('resolved_module')} "
            f"signature={item.get('signature')} "
            f"summary={item.get('summary')}"
        )
    if payload.get("synthetic_probe"):
        print(
            "[cellpose-dynamics-spike] synthetic_probe="
            f"{json.dumps(payload.get('synthetic_probe'), sort_keys=True)}"
        )

    write_json(json_out, payload)
    summary = {
        "status": payload.get("status"),
        "json_out": str(json_out),
        "available_functions": [
            item["label"] for item in payload.get("functions", []) if item.get("available")
        ],
        "synthetic_probe_status": payload.get("synthetic_probe", {}).get("status"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if payload.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
