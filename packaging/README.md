# Community preparation notice

**AGPL publication preparation (2026-10-28).** The maintainer approved publication
of the model weights. The installer release workflow remains disabled pending a
verified build.

Current source is based on **1.2.2** and starts without device activation.
See `docs/GETTING_STARTED.md`, `docs/OPEN_SOURCE_READINESS.md` and the model-status
manifest before building a public installer. The maintainer authorized the local
adapted weights; upstream model and training-data notices still apply. The legacy public-key resource is not a secret and is not a community
activation requirement; private signing keys must never enter a public export.

The instructions below document historical activated builds and their resource
layout. They must not be read as instructions to activate the community source,
or to rename old installers as a new community release. The current Windows
builder includes the root license/notices and dependency-license collection.

---

# ScieFig 1.2.0 Packaging

Version 1.1.1 remains a source-only checkpoint. Version 1.2.0 has separate Windows and macOS builds, each tied to its own source snapshot. GitHub Actions builds the published Windows installer; the local installation is built separately from the same Windows commit.

This packaging flow builds a Windows-distributable ScieFig `1.2.0` bundle with
offline device licensing enabled before the workspace opens.

## Inputs

- PyInstaller must already be installed on the build machine.
- Inno Setup must already be installed if you want an `.exe` installer.
- You must provide a **public key** PEM path to `build_r10.ps1`.
- Never provide a **private key** PEM to the build script. Private keys stay
  outside the repository, outside `dist/`, outside installer output, and
  outside every packaged runtime artifact.

## Build

```powershell
pwsh packaging/build_r10.ps1 -PublicKeyPath resources/license_public_key.pem
```

The script:

1. stages the provided public key as `resources/license_public_key.pem`
2. builds a PyInstaller one-directory bundle named `ScieFig_R10`
3. embeds `resources/icons/sciefig_app_icon.ico` as the executable icon
4. hidden-imports lazy CZI/ND2/Excel readers (`czifile`, `nd2`, `openpyxl`,
   `tifffile`) and collect-all for `nd2` plus `openpyxl`
5. excludes test and review/process material such as `.scratch`, `.git`,
   `tests`, and `docs/superpowers`
6. compiles `ScieFig_R10.iss` through Inno Setup; throws if `ISCC.exe` is not
   found unless `-SkipInstaller` is specified

## Output

- Frozen app folder: `dist/r10/ScieFig_R10/`
- Installer: `dist/r10/ScieFig_1.1.1_Setup.exe` when Inno Setup is available

Copy a completed installer into `<your release directory>/`
using the same classified-release layout as `releases\R096_cf6c1a8\`. Do not
write build output to the desktop or repository root.

The runtime bundle contains the public key only. License issuance remains a
developer-only offline step performed with `scripts/licensing/issue_r095_license.py`.
1.1.1 intentionally keeps accepting the R0.95 license channel for the current
trial series, so existing machine-bound R0.95 licenses continue to activate the
same computer.

## 1.1.1 Install And Activation Workflow

For a trial computer with no Python or project environment:

1. Send only `dist/r10/ScieFig_1.1.1_Setup.exe` to the trial user.
2. The user installs ScieFig normally and starts the app from the Start menu or
   desktop shortcut.
3. On first launch, ScieFig shows the activation dialog instead of opening the
   workspace.
4. The user copies the displayed machine code and sends it back through your
   offline channel.
5. Issue a license JSON with the developer-only private key:

```powershell
python scripts/licensing/issue_r095_license.py `
  --private-key C:\secure\sciefig_r095_private_key.pem `
  --machine-code <MACHINE-CODE-FROM-USER> `
  --output C:\secure\customer\sciefig_r095_license.json `
  --license-id r095-customer-001
```

6. Send only the generated license JSON back to the user.
7. The user clicks `Import License`, selects the JSON file, and ScieFig opens
   after signature and machine-code validation pass.

R0.95-channel licenses are permanent and machine-bound. Reinstalling on the same
computer should continue to work after re-importing the same valid license.
Copying the installed application or license JSON to a different computer should
not activate that computer because the machine code will not match.

The installer also includes a user-facing activation guide at:

`{install directory}\_internal\resources\ScieFig_R10_Activation_Guide.txt`

A Start menu shortcut named `ScieFig R1.0 Activation Guide` opens that file.

## Release Checklist

### Bundled local style extraction (1.0.3)

`build_r10.ps1` collects `ui.local_style_extraction` (including its dynamically
loaded engine source), PyMuPDF and fontTools. It copies the complete local
Tesseract runtime, English traineddata and supplied license documentation from
`build/local_style_runtime/tesseract` into the frozen runtime. For a clean source
snapshot, pass `-StyleRuntimeDir <existing build/local_style_runtime>`.
No development Python cache, paper corpus or user images are packaged.
This remains the existing internal/trial distribution; commercial dependency
license review remains required and is not waived by this packaging update.

### Bundled Cellpose (1.0.3 update)

Run `build_cellpose.ps1 -PythonPath <Cellpose Python> -ModelPath <local cyto3 weights> -NucleiModelPath <local nucleitorch_0 weights>` before `build_r10.ps1`.
The Windows worker uses Cellpose 3.1.1.3, Torch 2.12.0+cu130 and Python 3.12.10; pinned requirements are in `requirements-cellpose-windows.txt`. CPU remains available without NVIDIA hardware. Explicit GPU selection requires compatible NVIDIA hardware/drivers and does not silently fall back.
Do not freeze with Python 3.12.0: its bytecode issue causes SciPy imports to fail in the executable.
The worker is written to `dist/cellpose`; `build_r10.ps1` includes that complete directory and its third-party licenses in the installer.
`-CellposeDir` can point to the already built directory when packaging a clean source snapshot.
User machines need neither Python nor model downloads. Six weights are bundled in `cellpose/_internal/models/`: cyto3, nucleitorch_0, DAPI contour, HE tissue-boundary, Laminin membrane adaptations and TRUEFAD. The worker reports its actual model, computing device and parameters next to each output so exported results cannot silently claim another method.
The existing R0.95 machine-bound license channel is unchanged.

### Standard release checks

- Confirm `resources/license_public_key.pem` matches the release private key.
- Run the packaging script with the release public key.
- Keep the private key outside the repository, build output, installer output,
  and user-facing packages.
- Run this release's local tests (`tests/test_packaging_r095.py`,
  `tests/test_licensing.py`, `tests/test_license_activation_ui.py`,
  `tests/test_measurement_excel_export.py`,
  `tests/test_cell_diameter_workspace.py`,
  `tests/test_bright_round_cells_core.py`) and `git diff --check`.
- Smoke-test the installer on a clean directory with an isolated ProgramData
  location.
