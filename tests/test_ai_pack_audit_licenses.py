from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ai_pack" / "audit_licenses.py"
MANIFEST_PATH = REPO_ROOT / "config" / "ai_pack" / "manifest.json"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_audit_reports_restricted_entry_in_default_manifest() -> None:
    completed = _run("--manifest", str(MANIFEST_PATH))

    assert completed.returncode == 0
    assert "cellpose_cyto3" in completed.stdout
    assert "restricted" in completed.stdout


def test_audit_strict_mode_fails_for_restricted_entry() -> None:
    completed = _run("--manifest", str(MANIFEST_PATH), "--strict")

    assert completed.returncode == 1


def test_audit_strict_mode_passes_for_synthetic_allowed_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "allowed_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "1.0",
                "entries": [
                    {
                        "model_id": "allowed_model",
                        "display_name": "Allowed Model",
                        "version": "0.0.1",
                        "source_url": "https://example.com/allowed.onnx",
                        "sha256": "sha256:" + ("a" * 64),
                        "file_size_bytes": 123,
                        "license": "MIT",
                        "commercial_use_status": "allowed",
                        "input_schema": {"input": {"dtype": "float32", "shape": [1, 1]}},
                        "output_schema": {"output": {"dtype": "float32", "shape": [1, 1]}},
                        "postprocess": "noop",
                        "cache_subdir": "allowed_model/0.0.1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = _run("--manifest", str(manifest_path), "--strict")

    assert completed.returncode == 0
