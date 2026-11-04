from __future__ import annotations

from pathlib import Path
import re


def test_build_script_requires_public_key_and_uses_pyinstaller_onedir() -> None:
    script_path = Path(__file__).resolve().parents[1] / "packaging" / "build_r10.ps1"

    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    assert "param(" in script
    assert "PublicKeyPath" in script
    assert "pyinstaller" in script.lower()
    assert "--onedir" in script
    assert "main.py" in script
    assert "license_public_key.pem" in script
    assert "config" in script
    assert "journal_presets.json" in script
    assert "ScieFig_R10_Activation_Guide.txt" in script
    assert "1.2.2" in script
    assert "filevers=(1, 2, 2, 0)" in script
    assert "prodvers=(1, 2, 2, 0)" in script
    assert ".scratch" in script
    assert "--icon" in script
    assert "sciefig_app_icon.ico" in script
    assert "--hidden-import" in script
    assert "czifile" in script
    assert "nd2" in script
    assert "openpyxl" in script
    assert "tifffile" in script
    assert "--collect-all" in script
    assert "ui.local_style_extraction" in script
    assert "pymupdf" in script
    assert "StyleRuntimeDir" in script
    assert "local_style_runtime" in script
    assert "tesseract.exe" in script
    assert "matplotlib.backends.backend_svg" in script


def test_inno_setup_script_consumes_dist_only_and_registers_shortcuts() -> None:
    script_path = Path(__file__).resolve().parents[1] / "packaging" / "ScieFig_R10.iss"

    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    assert "AppId={{0C73A76E-0E1D-49D2-A2D1-9809DE8926BE}" in script
    assert "AppName=ScieFig" in script
    assert "AppVersion=1.2.2" in script
    assert "OutputBaseFilename=ScieFig_1.2.2_Setup" in script
    assert "DefaultDirName={autopf}\\ScieFig" in script
    assert "#include FilesList" in script
    listing = (script_path.parent / 'installer_files.py').read_text(encoding='utf-8')
    assert 'DistDir' in listing and '{app}' in listing and 'ignoreversion' in listing
    assert "Name: \"{autoprograms}\\ScieFig\"" in script
    assert "Name: \"{autoprograms}\\ScieFig Getting Started\"" in script
    assert "LicenseFile=..\\LICENSE" in script
    assert "_internal\\resources\\ScieFig_R10_Activation_Guide.txt" in script
    assert "Name: \"{autodesktop}\\ScieFig\"" in script
    assert "license_public_key.pem" in script
    assert "SetupIconFile={#IconFile}" in script
    assert "sciefig_app_icon.ico" in script


def test_build_script_finds_user_scope_inno_setup_when_iscc_is_not_on_path() -> None:
    script_path = Path(__file__).resolve().parents[1] / "packaging" / "build_r10.ps1"

    script = script_path.read_text(encoding="utf-8")

    assert "$env:LOCALAPPDATA" in script
    assert "Programs\\Inno Setup 6\\ISCC.exe" in script


def test_build_script_excludes_frozen_internal_icuuc_before_iscc() -> None:
    script_path = Path(__file__).resolve().parents[1] / "packaging" / "build_r10.ps1"
    script = script_path.read_text(encoding="utf-8")

    pyinstaller_done = script.find("PyInstaller failed")
    icuuc = script.find("_internal\\icuuc.dll")
    iscc_block = script.find("if (-not $SkipInstaller)")
    assert pyinstaller_done != -1
    assert icuuc != -1
    assert iscc_block != -1
    assert pyinstaller_done < icuuc < iscc_block
    cleanup = re.search(
        r"(?ms)^\$bundledIcuuc\s*=\s*[^\n]+\n\s*if\s*\(Test-Path\s+-LiteralPath\s+\$bundledIcuuc\)\s*\{(?P<body>.*?)^\}",
        script,
    )
    assert cleanup is not None
    assert cleanup.end() < iscc_block
    snippet = cleanup.group(0)
    assert "Remove-Item -LiteralPath $bundledIcuuc -Force" in snippet
    assert "Copy-Item" not in snippet
    assert "System32" not in snippet
    icuuc_references = [line for line in script.splitlines() if "$bundledIcuuc" in line or "icuuc.dll" in line.lower()]
    assert not any("Copy-Item" in line for line in icuuc_references)
    assert "icuuc.dll" not in script[iscc_block:]


def test_packaging_readme_documents_public_key_only_boundary() -> None:
    readme_path = Path(__file__).resolve().parents[1] / "packaging" / "README.md"

    assert readme_path.exists()
    readme = readme_path.read_text(encoding="utf-8")

    assert "public key" in readme.casefold()
    assert "private key" in readme.casefold()
    assert "PyInstaller" in readme
    assert "Inno Setup" in readme
    assert "1.2.0" in readme
    assert "R0.95 license channel" in readme
    assert "sciefig_app_icon.ico" in readme
    assert "czifile" in readme
    assert "openpyxl" in readme


def test_user_activation_guide_is_packaged_with_runtime_resources() -> None:
    guide_path = Path(__file__).resolve().parents[1] / "resources" / "ScieFig_R10_Activation_Guide.txt"

    assert guide_path.exists()
    guide = guide_path.read_text(encoding="utf-8")

    assert "Community source does not require machine-code activation" in guide
    assert "ScieFig 1.1.0 Activation Guide" in guide
    assert "R0.95 licenses remain accepted" in guide
    assert "machine code" in guide.casefold()
    assert "license JSON" in guide
    assert "permanent" in guide.casefold()


def test_r10_keeps_r095_license_channel_and_machine_code_formula() -> None:
    root = Path(__file__).resolve().parents[1]
    licensing = (root / "core" / "licensing.py").read_text(encoding="utf-8")
    constants = (root / "core" / "constants.py").read_text(encoding="utf-8")

    assert 'LICENSE_CHANNEL: Final[str] = "R0.95"' in licensing
    assert 'f"{PRODUCT_NAME}|{LICENSE_CHANNEL}|{normalized}"' in licensing
    assert "SOFTWARE\\Microsoft\\Cryptography" in licensing
    assert 'SOFTWARE_VERSION = "1.2.2"' in constants
    assert "skip_signature" in licensing
    assert "install_license_file" in licensing
    assert "self.validate_license_file(source_path)" in licensing
