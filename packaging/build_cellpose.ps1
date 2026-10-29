param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$ModelPath,
    [Parameter(Mandatory=$true)][string]$NucleiModelPath,
    [ValidateSet('full','cpu')][string]$RuntimeFlavor = 'full',
    [string]$OutputDir = ''
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Core' -or $PSVersionTable.PSVersion -lt [version]'7.6.4') { throw 'PowerShell Core 7.6.4+ required' }
& $PythonPath -c "import sys; assert sys.version_info[:3] != (3,12,0), 'Python 3.12.0 has a frozen-bytecode bug; use a newer patch release.'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Cellpose build interpreter' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$build = Join-Path $repo 'build\r10\cellpose'
$dist = Join-Path $repo 'dist'
$target = if ($OutputDir) { [IO.Path]::GetFullPath($OutputDir) } else { Join-Path $dist 'cellpose' }
if ((Test-Path -LiteralPath $target) -and (Get-Item -LiteralPath $target).LinkType) { throw 'Refusing to overwrite a linked runtime directory. Use a separate OutputDir.' }
if ((Test-Path -LiteralPath $target) -and (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1)) { throw 'Use an empty OutputDir to avoid mixing CPU and GPU runtime files.' }
& $PythonPath -c "import torch; assert ('$RuntimeFlavor' != 'cpu' or torch.version.cuda is None), 'CPU builds require CPU-only PyTorch'"
if ($LASTEXITCODE -ne 0) { throw 'Runtime flavor does not match the build environment' }
$model = (Resolve-Path -LiteralPath $ModelPath).Path
$nucleiModel = (Resolve-Path -LiteralPath $NucleiModelPath).Path
$reviewedDapiModel = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'models\cyto3_dapi_reviewed_v1')).Path
$heFiberModel = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'models\cyto3_he_fiber_boundary_v1')).Path
$lamininModel = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'models\cyto3_laminin_final_v1')).Path
$truefadModel = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'models\truefad_myotube.pt')).Path
New-Item -ItemType Directory -Force -Path $build,$dist | Out-Null
& $PythonPath -m PyInstaller --noconfirm --clean --onedir --console --name ScieFig_Cellpose --distpath $build --workpath (Join-Path $build 'work') --specpath $build --collect-all cellpose --copy-metadata cellpose --copy-metadata torch --exclude-module pytest --exclude-module matplotlib --exclude-module pandas --exclude-module IPython --exclude-module torchvision --exclude-module torchaudio --exclude-module onnx --exclude-module onnxruntime --add-data "${model};models" --add-data "${nucleiModel};models" --add-data "${reviewedDapiModel};models" --add-data "${heFiberModel};models" --add-data "${lamininModel};models" --add-data "${truefadModel};models" (Join-Path $PSScriptRoot 'cellpose_worker.py')
if ($LASTEXITCODE -ne 0) { throw 'Cellpose freeze failed' }
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Path (Join-Path $build 'ScieFig_Cellpose\*') -Destination $target -Recurse -Force
if ($RuntimeFlavor -eq 'cpu') {
    '{"id":"cellpose3-torch212-win64-v1","device":"cpu"}' | Set-Content -LiteralPath (Join-Path $target 'runtime.json') -Encoding utf8NoBOM
}
& $PythonPath (Join-Path $PSScriptRoot 'collect_cellpose_licenses.py') (Join-Path $target 'THIRD_PARTY_LICENSES')
if ($LASTEXITCODE -ne 0) { throw 'Cellpose license collection failed' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'models\truefad_source_rdf.yaml'),(Join-Path $PSScriptRoot 'models\truefad_myotube.json') -Destination (Join-Path $target 'THIRD_PARTY_LICENSES')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../LICENSES') -Destination (Join-Path $target 'THIRD_PARTY_LICENSES/ScieFig-Models') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../docs/MODEL_LICENSE_REVIEW.md'),(Join-Path $PSScriptRoot 'models/PUBLIC_MODEL_STATUS.json') -Destination (Join-Path $target 'THIRD_PARTY_LICENSES/ScieFig-Models') -Force
Write-Output $target
