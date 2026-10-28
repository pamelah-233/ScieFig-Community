# AI Pack Spike Scripts

These scripts are dev-only feasibility probes for Phase C-2.1. They are not ScieFig runtime code and they must be run from an isolated environment outside the repo.

## Recommended isolated env

On Windows:

```powershell
py -3.12 -m venv D:\ScieFig_spike_envs\cellpose_c2_1
D:\ScieFig_spike_envs\cellpose_c2_1\Scripts\python.exe -m pip install --upgrade pip
D:\ScieFig_spike_envs\cellpose_c2_1\Scripts\python.exe -m pip install "cellpose>=3,<4" onnx onnxruntime
```

Example runs:

```powershell
D:\ScieFig_spike_envs\cellpose_c2_1\Scripts\python.exe scripts\ai_pack\spikes\export_cellpose_onnx_spike.py --model cyto3
D:\ScieFig_spike_envs\cellpose_c2_1\Scripts\python.exe scripts\ai_pack\spikes\inspect_cellpose_dynamics_spike.py
```

If `cyto3` succeeds:

```powershell
D:\ScieFig_spike_envs\cellpose_c2_1\Scripts\python.exe scripts\ai_pack\spikes\export_cellpose_onnx_spike.py --model nuclei
```

## Outputs

- JSON evidence is written to `reference_docs/spike_outputs/`.
- Generated ONNX files default to a temp directory outside the repo.
- Do not commit `.onnx`, `.pth`, downloaded Cellpose weights, or other generated binary artifacts.

## Notes

- The scripts catch optional dependency failures and write reportable JSON errors instead of mutating ScieFig runtime code.
- The export spike disables Cellpose mkldnn during export on this host because the mkldnn path emits unsupported ONNX operators.
