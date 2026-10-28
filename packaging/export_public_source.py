"""Export a reviewed source snapshot without private history, data or weights."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


ROOT_FILES = {
    "main.py", "README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md",
    "TRADEMARKS.md", "CONTRIBUTING.md", "SECURITY.md", ".gitignore",
    ".gitattributes", "requirements.txt", "requirements-ai.txt", "requirements-ai-dev.txt",
}
NEW_FILES = ROOT_FILES | {
    "packaging/export_public_source.py", "packaging/models/PUBLIC_MODEL_STATUS.json",
    "tests/test_public_source.py", "docs/GETTING_STARTED.md",
    "docs/OPEN_SOURCE_READINESS.md", "docs/MODEL_LICENSE_REVIEW.md",
    "docs/DEPENDENCY_SOURCES.json", "docs/COMMUNITY_RELEASE_NOTES.md",
    ".github/workflows/community-checks.yml", ".github/workflows/community-release-windows.yml",
}
MODEL_FILES = {
    "packaging/models/cyto3_dapi_reviewed_v1", "packaging/models/cyto3_he_fiber_boundary_v1",
    "packaging/models/cyto3_laminin_final_v1", "packaging/models/truefad_myotube.pt",
}
PREFIXES = ("core/", "ui/", "config/", "resources/", "packaging/", "scripts/", "tests/", "sample_data/", "LICENSES/")
PRIVATE_PATH = re.compile(r"[A-Z]:[\\/](?:DATA[\\/]|Users[\\/](?!Public[\\/])[^\\/]+[\\/])|\u5341\u9662|\u751f\u5316\u6240", re.I)
SECRET = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".ps1", ".iss", ".spec", ".csv", ".toml"}


def include_path(name: str, *, approved_weights: bool = False) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if any(p in {".git", "__pycache__", "build", "dist", "validation_reports", "artifacts"} for p in path.parts):
        return False
    if name.startswith("packaging/models/"):
        return name.endswith((".json", ".yaml")) or (approved_weights and name in MODEL_FILES)
    if name.startswith("scripts/analysis_experiments/"):
        return False
    if name in {".github/workflows/community-checks.yml", ".github/workflows/community-release-windows.yml"}:
        return True
    if name.startswith("packaging/release_notes"):
        return False
    if path.suffix.lower() in {".zip", ".exe", ".dmg", ".sfig", ".czi", ".nd2", ".pt", ".onnx"}:
        return False
    if any(part.lower() in {".env", "credentials.json", "license.json"} or "private_key" in part.lower() for part in path.parts):
        return False
    return name in ROOT_FILES or name in NEW_FILES or name.startswith(PREFIXES)


def check_text(name: str, content: bytes) -> None:
    path = PurePosixPath(name)
    if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix:
        return
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError:
        return
    if PRIVATE_PATH.search(text) or SECRET.search(text):
        raise ValueError(f"Private path or credential candidate in {name}; export stopped")


def export_source(root: Path, destination: Path, names: list[str] | None = None) -> dict:
    root, destination = root.resolve(), destination.absolute()
    if destination.exists():
        raise FileExistsError("Public export destination already exists; no files were overwritten")
    if names is None:
        tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode("utf-8").split("\0")
        names = sorted(set(tracked) | NEW_FILES | {p.relative_to(root).as_posix() for p in (root / "LICENSES").glob("*.txt")})
    status_file = root / "packaging/models/PUBLIC_MODEL_STATUS.json"
    status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else {}
    approved = status.get("public_weights_approved") is True and status.get("maintainer_authorized_local_adaptations") is True
    selected = [name for name in sorted(set(names)) if include_path(name, approved_weights=approved)]
    required = {"LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "main.py", "README.md"}
    if not required.issubset(selected) or any(not (root / n).is_file() for n in required):
        raise ValueError("Public source is missing required licensing or application files")
    # Check everything before creating any part of the export.
    for name in selected:
        source = root / name
        if not source.exists():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError(f"Source link leaves the reviewed tree: {name}")
        check_text(name, source.read_bytes())
    destination.mkdir(parents=True)
    copied = []
    for name in selected:
        source = root / name
        if not source.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(name)
    report = {
        "application_baseline": "1.2.2", "copyright_holder": "pamelah-233",
        "code_license": "AGPL-3.0-only", "history_included": False,
        "model_weights_included": bool(MODEL_FILES.intersection(copied)),
        "model_weight_files": sorted(MODEL_FILES.intersection(copied)),
        "private_research_materials_included": False,
        "files": copied,
    }
    (destination / "PUBLIC_SOURCE_MANIFEST.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = export_source(Path(__file__).resolve().parents[1], arguments.output)
    print(f"Exported {len(report['files'])} reviewed files; private history/data excluded; model weight files: {len(report['model_weight_files'])}")
