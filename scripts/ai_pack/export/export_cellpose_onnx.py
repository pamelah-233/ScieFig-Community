from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ai_pack" / "release"
DEFAULT_OPSET = 17
MODEL_TO_OUTPUT_NAME = {
    "cyto3": "cellpose_cyto3_v0.1.0.onnx",
    "nuclei": "cellpose_nuclei_v0.1.0.onnx",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Cellpose CPnet weights to ScieFig-compatible ONNX.")
    parser.add_argument("--model", default="cyto3", choices=sorted(MODEL_TO_OUTPUT_NAME))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_sidecar_documents(output_dir: Path, *, model: str, source_model_path: Path, source_sha256: str, onnx_path: Path) -> None:
    license_text = (
        "Cellpose-derived research artifact\n"
        "\n"
        "This export is intended for personal/research use only pending commercial audit.\n"
        "ScieFig records this artifact as commercial_use_status = restricted.\n"
    )
    provenance_text = "\n".join(
        [
            "# ScieFig AI Pack Provenance",
            "",
            f"Created: {datetime.now(UTC).strftime('%Y-%m-%d')}",
            f"Source package: cellpose",
            f"source model name: {model}",
            f"source local filename: {source_model_path.name}",
            f"source SHA256: {source_sha256}",
            f"export command: python scripts/ai_pack/export/export_cellpose_onnx.py --model {model} --out {output_dir}",
            f"output ONNX filename: {onnx_path.name}",
            f"output ONNX SHA256: sha256:{sha256_file(onnx_path)}",
            f"output ONNX file size: {onnx_path.stat().st_size}",
            "commercial_use_status: restricted",
        ]
    )
    (output_dir / "LICENSE.txt").write_text(license_text, encoding="utf-8")
    (output_dir / "PROVENANCE.md").write_text(provenance_text, encoding="utf-8")


def export_model(*, model_name: str, output_dir: Path) -> dict[str, Any]:
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from cellpose import models as cellpose_models

    rng = np.random.default_rng(0)
    torch.manual_seed(0)
    output_dir.mkdir(parents=True, exist_ok=True)

    example_np = rng.uniform(0.0, 1.0, size=(1, 2, 256, 256)).astype("float32")
    example_tensor = torch.from_numpy(example_np)

    model = cellpose_models.CellposeModel(gpu=False, model_type=model_name, mkldnn=False)
    net = model.net.eval()
    source_model_path = Path(str(getattr(model, "pretrained_model", ""))).expanduser()

    class PrimaryTensorWrapper(torch.nn.Module):
        def __init__(self, wrapped_net) -> None:
            super().__init__()
            self.net = wrapped_net

        def forward(self, x):
            outputs = self.net(x)
            if isinstance(outputs, (list, tuple)) and outputs:
                return outputs[0]
            return outputs

    wrapper = PrimaryTensorWrapper(net).eval()
    onnx_path = output_dir / MODEL_TO_OUTPUT_NAME[model_name]
    torch.onnx.export(
        wrapper,
        example_tensor,
        str(onnx_path),
        input_names=["input"],
        output_names=["output0"],
        opset_version=DEFAULT_OPSET,
        do_constant_folding=False,
        dynamo=False,
    )

    with torch.no_grad():
        torch_output = wrapper(example_tensor).detach().cpu().numpy()

    onnx_model = onnx.load(str(onnx_path))
    onnx.checker.check_model(onnx_model)
    ort_session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    ort_output = ort_session.run(None, {"input": example_np})[0]
    if not np.allclose(torch_output, ort_output, rtol=1e-3, atol=1e-4):
        raise RuntimeError("Torch vs ONNX outputs diverged beyond rtol=1e-3 atol=1e-4")

    source_sha256 = sha256_file(source_model_path) if source_model_path.is_file() else ""
    _write_sidecar_documents(
        output_dir,
        model=model_name,
        source_model_path=source_model_path,
        source_sha256=source_sha256,
        onnx_path=onnx_path,
    )

    return {
        "model": model_name,
        "onnx_path": str(onnx_path),
        "sha256": f"sha256:{sha256_file(onnx_path)}",
        "file_size_bytes": onnx_path.stat().st_size,
        "opset": DEFAULT_OPSET,
        "mkldnn": False,
        "platform": platform.platform(),
        "source_model_path": str(source_model_path),
        "source_model_sha256": source_sha256,
    }


def main() -> int:
    args = parse_args()
    result = export_model(model_name=args.model, output_dir=Path(args.out).expanduser())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
