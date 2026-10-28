from pathlib import Path
import importlib.util

import pytest

_spec = importlib.util.spec_from_file_location(
    "sciefig_public_export", Path(__file__).resolve().parents[1] / "packaging/export_public_source.py"
)
_export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_export)
check_text, export_source, include_path = _export.check_text, _export.export_source, _export.include_path


@pytest.mark.parametrize("name", [
    ".git/config", "AGENTS.md", "docs/superpowers/plans/internal.md",
    "examples/research.png", "packaging/models/cyto3_dapi_reviewed_v1",
    "packaging/models/truefad_myotube.pt", "resources/customer/private_key.pem",
    "core/.env", "sample_data/private.czi", "../outside.py",
])
def test_public_export_rejects_private_history_and_unapproved_artifacts(name):
    assert not include_path(name)


def _minimal_source(root: Path):
    root.mkdir()
    names = ["LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "main.py", "README.md"]
    for name in names:
        (root / name).write_text("reviewed source", encoding="utf-8")
    return names


def test_public_export_refuses_sensitive_content_before_copying(tmp_path):
    root, output = tmp_path / "source", tmp_path / "public"
    names = _minimal_source(root)
    (root / "main.py").write_text("-----BEGIN " + "PRIVATE KEY-----", encoding="utf-8")
    with pytest.raises(ValueError, match="credential"):
        export_source(root, output, names)
    assert not output.exists()


def test_public_export_preserves_existing_destination(tmp_path):
    root, output = tmp_path / "source", tmp_path / "public"
    names = _minimal_source(root)
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(FileExistsError):
        export_source(root, output, names)
    assert marker.read_text(encoding="utf-8") == "do not overwrite"


def test_public_export_copies_licensed_code_without_weights_or_history(tmp_path):
    root, output = tmp_path / "source", tmp_path / "public"
    names = _minimal_source(root)
    names += [".git/config", "packaging/models/truefad_myotube.pt"]
    report = export_source(root, output, names)
    assert (output / "main.py").is_file()
    assert (output / "PUBLIC_SOURCE_MANIFEST.json").is_file()
    assert not (output / ".git").exists()
    assert not (output / "packaging/models/truefad_myotube.pt").exists()
    assert report["history_included"] is False


def test_public_text_allows_public_key_and_portable_paths():
    check_text("resources/license_public_key.pem", b"-----BEGIN PUBLIC KEY-----\nexample")
    check_text("main.py", b"image = 'sample_data/example.png'")


def test_public_scanner_can_export_its_own_rules():
    source = Path(__file__).resolve().parents[1] / "packaging/export_public_source.py"
    check_text("packaging/export_public_source.py", source.read_bytes())


@pytest.mark.parametrize('approval,owner,expected', [(True, True, True), (True, False, False), (False, True, False)])
def test_reviewed_weight_export_requires_both_licensing_and_owner_confirmation(tmp_path, approval, owner, expected):
    import json
    root, output = tmp_path / "source", tmp_path / "public"
    names = _minimal_source(root)
    model = root / "packaging/models/cyto3_dapi_reviewed_v1"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"synthetic-test-weights")
    status = model.parent / "PUBLIC_MODEL_STATUS.json"
    status.write_text(json.dumps({"public_weights_approved": approval, "maintainer_authorized_local_adaptations": owner}), encoding="utf-8")
    names += [model.relative_to(root).as_posix(), status.relative_to(root).as_posix()]
    report = export_source(root, output, names)
    assert report["model_weights_included"] is expected
    assert (output / "packaging/models/cyto3_dapi_reviewed_v1").exists() is expected
    assert not include_path("packaging/models/unreviewed.pt", approved_weights=True)
