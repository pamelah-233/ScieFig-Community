param(
    [Parameter(Mandatory = $true)]
    [string]$PublicKeyPath,

    [string]$DistRoot = '',

    [string]$CellposeDir = '',

    [string]$StyleRuntimeDir = '',

    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSEdition -ne 'Core' -or $PSVersionTable.PSVersion -lt [version]'7.6.4') {
    throw "This script requires PowerShell Core 7.6.4 or later. Current: $($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$mainScript = Join-Path $repoRoot 'main.py'
$resourcesDir = Join-Path $repoRoot 'resources'
$configDir = Join-Path $repoRoot 'config'
$iconPath = Join-Path $resourcesDir 'icons\sciefig_app_icon.ico'
$resolvedPublicKeyPath = (Resolve-Path $PublicKeyPath).Path
$distBase = if ($DistRoot) { $DistRoot } else { Join-Path $repoRoot 'dist\r10' }
$buildRoot = Join-Path $repoRoot 'build\r10'
$distDir = Join-Path $distBase 'ScieFig_R10'
$stagingResources = Join-Path $buildRoot 'staging_resources'
$bundledPublicKeyPath = Join-Path $stagingResources 'license_public_key.pem'
$activationGuidePath = Join-Path $resourcesDir 'ScieFig_R10_Activation_Guide.txt'
$pyinstallerWorkPath = Join-Path $buildRoot 'pyinstaller_work'
$specPath = Join-Path $buildRoot 'spec'
$versionFilePath = Join-Path $buildRoot 'version_r10.txt'
$issPath = Join-Path $PSScriptRoot 'ScieFig_R10.iss'
$excludedMarkers = @('.scratch', 'tests', '.git', 'docs\superpowers')
if (-not $CellposeDir) { $CellposeDir = Join-Path $repoRoot 'dist\cellpose' }
if (-not (Test-Path (Join-Path $cellposeDir 'ScieFig_Cellpose.exe'))) {
    throw 'Build the bundled Cellpose runtime with packaging/build_cellpose.ps1 first.'
}
if (-not $StyleRuntimeDir) { $StyleRuntimeDir = Join-Path $repoRoot 'build\local_style_runtime' }
$tesseractDir = Join-Path $StyleRuntimeDir 'tesseract'
if (-not (Test-Path (Join-Path $tesseractDir 'tesseract.exe')) -or -not (Test-Path (Join-Path $tesseractDir 'tessdata\eng.traineddata'))) {
    throw 'The local style OCR runtime (Tesseract and English traineddata) is required.'
}

if ((Get-Content -Raw $resolvedPublicKeyPath) -match 'PRIVATE KEY') {
    throw "Refusing to package a private key. Provide the public key PEM only."
}

if (-not (Test-Path $activationGuidePath)) {
    throw "Activation guide was not found: $activationGuidePath"
}

if (-not (Test-Path $iconPath)) {
    throw "Application icon was not found: $iconPath"
}

New-Item -ItemType Directory -Force -Path $distBase, $buildRoot, $stagingResources, $pyinstallerWorkPath, $specPath | Out-Null
Copy-Item $resolvedPublicKeyPath $bundledPublicKeyPath -Force

@'
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 2, 2, 0),
    prodvers=(1, 2, 2, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'ScieFig'),
            StringStruct('FileDescription', 'ScieFig 1.2.2'),
            StringStruct('FileVersion', '1.2.2'),
            StringStruct('InternalName', 'ScieFig_R10'),
            StringStruct('OriginalFilename', 'ScieFig_R10.exe'),
            StringStruct('ProductName', 'ScieFig'),
            StringStruct('ProductVersion', '1.2.2')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
'@ | Set-Content -Encoding UTF8 $versionFilePath

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw 'python is not available on this build machine.'
}

$pyinstallerArgs = @(
    '-m'
    'PyInstaller'
    '--noconfirm'
    '--clean'
    '--windowed'
    '--onedir'
    '--name'
    'ScieFig_R10'
    '--icon'
    $iconPath
    '--distpath'
    $distBase
    '--workpath'
    $pyinstallerWorkPath
    '--specpath'
    $specPath
    '--version-file'
    $versionFilePath
    '--exclude-module'
    'pytest'
    '--exclude-module'
    'tests'
    '--hidden-import'
    'czifile'
    '--hidden-import'
    'nd2'
    '--hidden-import'
    'openpyxl'
    '--hidden-import'
    'tifffile'
    '--hidden-import'
    'matplotlib.backends.backend_svg'
    '--collect-all'
    'nd2'
    '--collect-all'
    'openpyxl'
    '--collect-all'
    'ui.local_style_extraction'
    '--collect-submodules'
    'core.image_analysis'
    '--collect-all'
    'pymupdf'
    '--collect-all'
    'fontTools'
    '--copy-metadata'
    'pymupdf'
    '--copy-metadata'
    'fonttools'
    '--add-data'
    "${resourcesDir};resources"
    '--add-data'
    "${configDir};config"
    '--add-data'
    "${bundledPublicKeyPath};resources"
    $mainScript
)

& $pythonCommand.Source @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path (Join-Path $distDir '_internal\resources\license_public_key.pem'))) {
    throw 'Bundled public key was not copied into the frozen resources directory.'
}
if (-not (Test-Path (Join-Path $distDir '_internal\resources\ScieFig_R10_Activation_Guide.txt'))) {
    throw 'Bundled activation guide was not copied into the frozen resources directory.'
}
if (-not (Test-Path (Join-Path $distDir '_internal\resources\icons\sciefig_app_icon.ico'))) {
    throw 'Bundled application icon was not copied into the frozen resources directory.'
}
if (-not (Test-Path (Join-Path $distDir '_internal\config\journal_presets.json'))) {
    throw 'Bundled journal preset config was not copied into the frozen config directory.'
}

foreach ($marker in $excludedMarkers) {
    if (Test-Path (Join-Path $distDir $marker)) {
        throw "Unexpected packaged content marker found: $marker"
    }
}

# PyInstaller can mis-bundle a version-suffixed ICU DLL; frozen ScieFig should use the ICU interface provided by Windows.
$bundledIcuuc = Join-Path $distDir '_internal\icuuc.dll'
if (Test-Path -LiteralPath $bundledIcuuc) {
    Remove-Item -LiteralPath $bundledIcuuc -Force
}

$bundledCellpose = Join-Path $distDir 'cellpose'
New-Item -ItemType Directory -Force -Path $bundledCellpose | Out-Null
Copy-Item -Path (Join-Path $cellposeDir '*') -Destination $bundledCellpose -Recurse -Force

$bundledStyleRuntime = Join-Path $distDir '_internal\build\local_style_runtime'
New-Item -ItemType Directory -Force -Path $bundledStyleRuntime | Out-Null
Copy-Item -LiteralPath $tesseractDir -Destination $bundledStyleRuntime -Recurse -Force

foreach ($noticeName in @('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md')) {
    $noticeSource = Join-Path $repoRoot $noticeName
    if (-not (Test-Path -LiteralPath $noticeSource)) {
        throw "Missing community distribution notice: $noticeName"
    }
    Copy-Item -LiteralPath $noticeSource -Destination (Join-Path $distDir $noticeName) -Force
}
& $pythonCommand.Source (Join-Path $PSScriptRoot 'collect_cellpose_licenses.py') (Join-Path $distDir 'THIRD_PARTY_LICENSES')
if ($LASTEXITCODE -ne 0) { throw 'Application dependency license collection failed' }
Copy-Item -LiteralPath (Join-Path $repoRoot 'LICENSES') -Destination (Join-Path $distDir 'THIRD_PARTY_LICENSES/ScieFig-Models') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs/MODEL_LICENSE_REVIEW.md') -Destination (Join-Path $distDir 'THIRD_PARTY_LICENSES/ScieFig-Models') -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs/DEPENDENCY_SOURCES.json') -Destination (Join-Path $distDir 'DEPENDENCY_SOURCES.json') -Force

if (-not $SkipInstaller) {
    $filesList = Join-Path $buildRoot 'installer_files.iss'
    & $pythonCommand.Source (Join-Path $PSScriptRoot 'installer_files.py') $distDir $filesList
    if ($LASTEXITCODE -ne 0) { throw 'Installer file listing failed' }
    $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $userScopeIscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    $isccPath = if ($isccCommand) { $isccCommand.Source } elseif (Test-Path $userScopeIscc) { $userScopeIscc } else { $null }
    if (-not $isccPath) {
        throw 'Inno Setup compiler was not found. ISCC.exe is required unless -SkipInstaller is specified.'
    }
    & $isccPath "/DDistDir=$distDir" "/DOutputDir=$distBase" "/DIconFile=$iconPath" "/DFilesList=$filesList" $issPath
    if ($LASTEXITCODE -ne 0) {
        throw "ISCC failed with exit code $LASTEXITCODE"
    }
}

Write-Output $distDir
