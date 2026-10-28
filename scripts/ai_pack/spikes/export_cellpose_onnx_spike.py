from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import inspect
import io
import json
import platform
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SPIKE_OUTPUT_DIR = REPO_ROOT / "reference_docs" / "spike_outputs"
DEFAULT_ARTIFACT_DIR = Path(tempfile.gettempdir()) / "sciefig_spikes" / "ai_pack"
DEFAULT_OPSET = 17


class PrimaryTensorWrapper:  # defined lazily against torch.nn.Module in build_wrapper
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dev-only Cellpose ONNX export spike. Writes JSON evidence under reference_docs/spike_outputs/."
    )
    parser.add_argument("--model", default="cyto3", help="Cellpose model type to probe. Default: cyto3")
    parser.add_argument(
        "--artifacts-dir",
        default=str(DEFAULT_ARTIFACT_DIR),
        help="Directory for temporary ONNX artifacts. Defaults outside the repo.",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional explicit JSON output path. Defaults to reference_docs/spike_outputs/cellpose_<model>_onnx_schema.json",
    )
    return parser.parse_args()


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize_value(value: Any) -> Any:
    if hasattr(value, "shape"):
        return {
            "kind": type(value).__name__,
            "shape": [int(part) for part in value.shape],
            "dtype": str(getattr(value, "dtype", "")),
        }
    if isinstance(value, (list, tuple)):
        return {
            "kind": type(value).__name__,
            "items": [summarize_value(item) for item in value],
        }
    if isinstance(value, dict):
        return {
            "kind": "dict",
            "items": {str(key): summarize_value(item) for key, item in value.items()},
        }
    return {"kind": type(value).__name__, "repr": repr(value)[:200]}


def discover_input_channels(net: Any) -> int | None:
    for module in getattr(net, "modules", lambda: [])():
        weight = getattr(module, "weight", None)
        if weight is not None and getattr(weight, "ndim", 0) == 4 and getattr(weight, "shape", (0, 0))[1] > 0:
            return int(weight.shape[1])
    return None


def sanitize_error(exc: BaseException) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(limit=12),
    }


def build_wrapper(torch_module: Any, net: Any) -> Any:
    class _PrimaryTensorWrapper(torch_module.nn.Module):
        def __init__(self, wrapped_net: Any) -> None:
            super().__init__()
            self.net = wrapped_net

        def forward(self, x: Any) -> Any:
            outputs = self.net(x)
            if isinstance(outputs, (list, tuple)) and outputs:
                primary = outputs[0]
            else:
                primary = outputs
            return primary

    return _PrimaryTensorWrapper(net)


def export_once(
    *,
    wrapper: Any,
    torch_module: Any,
    example_input: Any,
    onnx_path: Path,
    dynamic: bool,
    opset_version: int,
) -> dict[str, Any]:
    export_kwargs: dict[str, Any] = {
        "f": str(onnx_path),
        "input_names": ["input"],
        "output_names": ["output0"],
        "opset_version": opset_version,
        "do_constant_folding": False,
        "dynamo": False,
    }
    if dynamic:
        export_kwargs["dynamic_axes"] = {
            "input": {2: "H", 3: "W"},
            "output0": {2: "H", 3: "W"},
        }
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        torch_module.onnx.export(wrapper, example_input, **export_kwargs)
    return {
        "path": str(onnx_path),
        "dynamic_axes": export_kwargs.get("dynamic_axes", {}),
        "opset_version": opset_version,
        "exporter": "torch.onnx.export(dynamo=False)",
        "stdout_excerpt": stdout_buffer.getvalue()[:1000],
        "stderr_excerpt": stderr_buffer.getvalue()[:2000],
    }


def onnx_io_schema(session: Any) -> dict[str, Any]:
    return {
        "inputs": [
            {
                "name": item.name,
                "dtype": str(item.type),
                "shape": list(item.shape),
            }
            for item in session.get_inputs()
        ],
        "outputs": [
            {
                "name": item.name,
                "dtype": str(item.type),
                "shape": list(item.shape),
            }
            for item in session.get_outputs()
        ],
    }


def run_spike(model_name: str, artifacts_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model_type": model_name,
        "status": "error",
        "environment": {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "cellpose": module_version("cellpose"),
            "torch": module_version("torch"),
            "onnx": module_version("onnx"),
            "onnxruntime": module_version("onnxruntime"),
            "cpu_only": True,
        },
        "notes": [
            "This spike disables Cellpose mkldnn during export because mkldnn inserts unsupported ONNX operators on this host."
        ],
    }

    try:
        numpy = importlib.import_module("numpy")
        torch = importlib.import_module("torch")
        onnx = importlib.import_module("onnx")
        ort = importlib.import_module("onnxruntime")
        cellpose_models = importlib.import_module("cellpose.models")
    except Exception as exc:
        result["error"] = sanitize_error(exc)
        result["notes"].append(
            "Optional spike dependencies are unavailable in the current interpreter. Use an isolated Cellpose dev env."
        )
        return result

    rng = numpy.random.default_rng(0)
    torch.manual_seed(0)
    example_np = rng.uniform(0.0, 1.0, size=(1, 2, 256, 256)).astype("float32")
    example_tensor = torch.from_numpy(example_np)
    result["dummy_input"] = {
        "dtype": "float32",
        "shape": [1, 2, 256, 256],
        "range": [0.0, 1.0],
        "seed": 0,
    }

    model = cellpose_models.CellposeModel(gpu=False, model_type=model_name, mkldnn=False)
    net = model.net.eval()
    raw_outputs = None
    with torch.no_grad():
        raw_outputs = net(example_tensor)
    raw_primary = raw_outputs[0] if isinstance(raw_outputs, (list, tuple)) else raw_outputs

    pretrained_model_path = Path(str(getattr(model, "pretrained_model", ""))).expanduser()
    result["source_model"] = {
        "path": str(pretrained_model_path),
        "exists": pretrained_model_path.is_file(),
        "sha256": sha256_file(pretrained_model_path) if pretrained_model_path.is_file() else None,
        "file_size_bytes": pretrained_model_path.stat().st_size if pretrained_model_path.is_file() else None,
    }
    result["network"] = {
        "object_type": f"{type(net).__module__}.{type(net).__name__}",
        "source_file": inspect.getsourcefile(type(net)),
        "expected_input_channels": int(getattr(model, "nchan", 0)) or discover_input_channels(net),
        "raw_forward_signature": str(inspect.signature(net.forward)),
        "raw_outputs": summarize_value(raw_outputs),
        "exported_output_note": (
            "The ONNX spike exports only the primary tensor output. "
            "Cellpose 3.1.1.3 also emits a style tensor and an auxiliary non-tensor output."
        ),
    }

    wrapper = build_wrapper(torch, net).eval()
    with torch.no_grad():
        wrapped_primary = wrapper(example_tensor).detach().cpu().numpy()

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    selected_export: dict[str, Any] | None = None
    selected_onnx_path: Path | None = None

    for mode in ("dynamic_hw", "static_256"):
        onnx_path = artifacts_dir / f"cellpose_{model_name}_{mode}.onnx"
        try:
            export_details = export_once(
                wrapper=wrapper,
                torch_module=torch,
                example_input=example_tensor,
                onnx_path=onnx_path,
                dynamic=mode == "dynamic_hw",
                opset_version=DEFAULT_OPSET,
            )
            selected_export = {"mode": mode, **export_details}
            selected_onnx_path = onnx_path
            attempts.append({"mode": mode, "status": "success", **export_details})
            break
        except Exception as exc:
            attempts.append({"mode": mode, "status": "error", "error": sanitize_error(exc)})

    result["export_attempts"] = attempts
    if selected_onnx_path is None or selected_export is None:
        result["error"] = {
            "type": "ExportFailed",
            "message": "All ONNX export attempts failed.",
            "traceback": "",
        }
        return result

    result["selected_export"] = selected_export
    result["selected_export"]["sha256"] = sha256_file(selected_onnx_path)
    result["selected_export"]["file_size_bytes"] = selected_onnx_path.stat().st_size

    onnx_model = onnx.load(str(selected_onnx_path))
    onnx.checker.check_model(onnx_model)
    ort_session = ort.InferenceSession(str(selected_onnx_path), providers=["CPUExecutionProvider"])
    ort_outputs = ort_session.run(None, {"input": example_np})
    ort_primary = ort_outputs[0]
    max_abs_diff = float(numpy.max(numpy.abs(wrapped_primary - ort_primary)))
    mean_abs_diff = float(numpy.mean(numpy.abs(wrapped_primary - ort_primary)))

    result["input_schema"] = {
        "input": {
            "dtype": "float32",
            "shape": [1, 2, 256, 256],
            "layout": "NCHW",
        }
    }
    result["output_schema"] = {
        "output0": {
            "dtype": str(ort_primary.dtype),
            "shape": [int(part) for part in ort_primary.shape],
            "interpretation": "candidate flow_y / flow_x / cellprob tensor from CPnet primary output",
        }
    }
    result["onnx_runtime"] = {
        "providers": ort_session.get_providers(),
        "io_schema": onnx_io_schema(ort_session),
        "onnx_checker": "pass",
    }
    result["comparison"] = {
        "rtol": 1e-3,
        "atol": 1e-4,
        "allclose_primary": bool(numpy.allclose(wrapped_primary, ort_primary, rtol=1e-3, atol=1e-4)),
        "max_abs_diff": max_abs_diff,
        "mean_abs_diff": mean_abs_diff,
        "torch_primary_shape": [int(part) for part in wrapped_primary.shape],
        "ort_primary_shape": [int(part) for part in ort_primary.shape],
    }
    result["status"] = "success"
    if selected_export["mode"] != "dynamic_hw":
        result["status"] = "success_with_limitations"
        result["notes"].append("Dynamic H/W export failed; static 256x256 export succeeded.")
    return result


def main() -> int:
    args = parse_args()
    safe_model_name = args.model.replace("/", "_").replace("\\", "_")
    json_out = Path(args.json_out) if args.json_out else SPIKE_OUTPUT_DIR / f"cellpose_{safe_model_name}_onnx_schema.json"
    artifacts_dir = Path(args.artifacts_dir).expanduser()

    try:
        payload = run_spike(args.model, artifacts_dir)
    except Exception as exc:
        payload = {
            "model_type": args.model,
            "status": "error",
            "environment": {
                "os": platform.platform(),
                "python": sys.version.split()[0],
                "python_executable": sys.executable,
            },
            "error": sanitize_error(exc),
        }

    print(f"[cellpose-onnx-spike] model_type={payload.get('model_type')}")
    env = payload.get("environment", {})
    print(
        "[cellpose-onnx-spike] environment "
        f"python={env.get('python')} "
        f"cellpose={env.get('cellpose')} "
        f"torch={env.get('torch')} "
        f"onnx={env.get('onnx')} "
        f"onnxruntime={env.get('onnxruntime')} "
        f"os={env.get('os')}"
    )
    network = payload.get("network", {})
    if network:
        print(
            "[cellpose-onnx-spike] network "
            f"type={network.get('object_type')} "
            f"source_file={network.get('source_file')} "
            f"channels={network.get('expected_input_channels')}"
        )
    source_model = payload.get("source_model", {})
    if source_model:
        print(
            "[cellpose-onnx-spike] source_model "
            f"path={source_model.get('path')} "
            f"sha256={source_model.get('sha256')} "
            f"exists={source_model.get('exists')}"
        )
    input_schema = payload.get("input_schema", {})
    if input_schema:
        print(f"[cellpose-onnx-spike] input_schema={json.dumps(input_schema, sort_keys=True)}")
    output_schema = payload.get("output_schema", {})
    if output_schema:
        print(f"[cellpose-onnx-spike] output_schema={json.dumps(output_schema, sort_keys=True)}")
    selected_export = payload.get("selected_export", {})
    if selected_export:
        print(
            "[cellpose-onnx-spike] selected_export "
            f"mode={selected_export.get('mode')} "
            f"path={selected_export.get('path')} "
            f"opset={selected_export.get('opset_version')} "
            f"sha256={selected_export.get('sha256')}"
        )
    onnx_runtime = payload.get("onnx_runtime", {})
    comparison = payload.get("comparison", {})
    if onnx_runtime or comparison:
        print(
            "[cellpose-onnx-spike] validation "
            f"onnx_checker={onnx_runtime.get('onnx_checker')} "
            f"ort_providers={onnx_runtime.get('providers')} "
            f"allclose_primary={comparison.get('allclose_primary')} "
            f"max_abs_diff={comparison.get('max_abs_diff')} "
            f"mean_abs_diff={comparison.get('mean_abs_diff')}"
        )
    for attempt in payload.get("export_attempts", []):
        if attempt.get("status") == "error":
            error = attempt.get("error", {})
            print(
                "[cellpose-onnx-spike] export_attempt "
                f"mode={attempt.get('mode')} "
                f"status=error "
                f"error_type={error.get('type')} "
                f"error_message={error.get('message')}"
            )

    write_json(json_out, payload)
    summary = {
        "status": payload.get("status"),
        "model_type": payload.get("model_type"),
        "json_out": str(json_out),
        "selected_export": payload.get("selected_export", {}).get("path"),
        "allclose_primary": payload.get("comparison", {}).get("allclose_primary"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if str(payload.get("status", "")).startswith("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
