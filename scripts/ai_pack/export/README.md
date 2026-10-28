# AI Pack Export

These scripts are developer-side export helpers for ScieFig AI Pack release artifacts. They are not imported by runtime code.

## Recommended isolated environment

On Windows:

```powershell
py -3.12 -m venv D:\ScieFig_spike_envs\cellpose_c3
D:\ScieFig_spike_envs\cellpose_c3\Scripts\python.exe -m pip install --upgrade pip
D:\ScieFig_spike_envs\cellpose_c3\Scripts\python.exe -m pip install "cellpose>=3,<4" torch onnx onnxruntime
```

## Export command

```powershell
D:\ScieFig_spike_envs\cellpose_c3\Scripts\python.exe scripts\ai_pack\export\export_cellpose_onnx.py --model cyto3 --out artifacts\ai_pack\release
```

The script:

- exports a static 256x256 ONNX artifact
- validates Torch vs ONNX output parity with `rtol=1e-3` and `atol=1e-4`
- writes adjacent `LICENSE.txt` and `PROVENANCE.md`
- prints `sha256` and `file_size_bytes` for `config/ai_pack/manifest.json`

## WARNING: Manifest synchronization (critical)

`config/ai_pack/manifest.json` ships with the SHA256 and file size values
recorded by the C-2.1 spike export. **Do not** mix a freshly rerun export with
the shipped manifest values.

If you rerun the export script and the printed SHA256 or file size differs
from what is currently in `config/ai_pack/manifest.json`:

1. Update both fields in `config/ai_pack/manifest.json` to match the new
   export.
2. Do not silently keep the old manifest values; AI inference will fail at
   model-load time with a hash-mismatch error when ScieFig's runtime verifies
   the cached file.
3. Note the export rerun in your local audit trail (LICENSE.txt /
   PROVENANCE.md are produced alongside the .onnx for this purpose).

Rationale: SHA256 byte parity across PyTorch / ONNX / Cellpose minor releases
is fragile, so the export script does not enforce a hard expected-SHA assertion.
But the manifest IS the trust anchor for runtime cache verification - keeping
them in sync is the user's responsibility.

Do not commit generated `.onnx`, `.pth`, `.pt`, `.ckpt`, `.bin`, or downloaded Cellpose weights.
