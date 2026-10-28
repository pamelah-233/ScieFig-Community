param(
    [Parameter(Mandatory = $true)]
    [string]$PublicKeyPath,

    [string]$DistRoot = '',

    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$mainScript = Join-Path $repoRoot 'main.py'
$resourcesDir = Join-Path $repoRoot 'resources'
$configDir = Join-Path $repoRoot 'config'
$resolvedPublicKeyPath = (Resolve-Path $PublicKeyPath).Path
$distBase = if ($DistRoot) { $DistRoot } else { Join-Path $repoRoot 'dist\r096' }
$buildRoot = Join-Path $repoRoot 'build\r096'
$distDir = Join-Path $distBase 'ScieFig_R096'
$stagingResources = Join-Path $buildRoot 'staging_resources'
$bundledPublicKeyPath = Join-Path $stagingResources 'license_public_key.pem'
$activationGuidePath = Join-Path $resourcesDir 'ScieFig_R096_Activation_Guide.txt'
$pyinstallerWorkPath = Join-Path $buildRoot 'pyinstaller_work'
$specPath = Join-Path $buildRoot 'spec'
$versionFilePath = Join-Path $buildRoot 'version_r096.txt'
$issPath = Join-Path $PSScriptRoot 'ScieFig_R096.iss'
$excludedMarkers = @('.scratch', 'tests', '.git', 'docs\superpowers')

if ((Get-Content -Raw $resolvedPublicKeyPath) -match 'PRIVATE KEY') {
    throw "Refusing to package a private key. Provide the public key PEM only."
}

if (-not (Test-Path $activationGuidePath)) {
    throw "Activation guide was not found: $activationGuidePath"
}

New-Item -ItemType Directory -Force -Path $distBase, $buildRoot, $stagingResources, $pyinstallerWorkPath, $specPath | Out-Null
Copy-Item $resolvedPublicKeyPath $bundledPublicKeyPath -Force

@'
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(0, 96, 0, 0),
    prodvers=(0, 96, 0, 0),
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
            StringStruct('FileDescription', 'ScieFig R0.96'),
            StringStruct('FileVersion', 'R0.96'),
            StringStruct('InternalName', 'ScieFig_R096'),
            StringStruct('OriginalFilename', 'ScieFig_R096.exe'),
            StringStruct('ProductName', 'ScieFig'),
            StringStruct('ProductVersion', 'R0.96')
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
    'ScieFig_R096'
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
    '--add-data'
    "${resourcesDir};resources"
    '--add-data'
    "${configDir};config"
    '--add-data'
    "${bundledPublicKeyPath};resources"
    $mainScript
)

& $pythonCommand.Source @pyinstallerArgs

if (-not (Test-Path (Join-Path $distDir '_internal\resources\license_public_key.pem'))) {
    throw 'Bundled public key was not copied into the frozen resources directory.'
}
if (-not (Test-Path (Join-Path $distDir '_internal\resources\ScieFig_R096_Activation_Guide.txt'))) {
    throw 'Bundled activation guide was not copied into the frozen resources directory.'
}
if (-not (Test-Path (Join-Path $distDir '_internal\config\journal_presets.json'))) {
    throw 'Bundled journal preset config was not copied into the frozen config directory.'
}

foreach ($marker in $excludedMarkers) {
    if (Test-Path (Join-Path $distDir $marker)) {
        throw "Unexpected packaged content marker found: $marker"
    }
}

if (-not $SkipInstaller) {
    $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $userScopeIscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    $isccPath = if ($isccCommand) { $isccCommand.Source } elseif (Test-Path $userScopeIscc) { $userScopeIscc } else { $null }
    if ($isccPath) {
        & $isccPath "/DDistDir=$distDir" "/DOutputDir=$distBase" $issPath
    } else {
        Write-Warning 'Inno Setup compiler was not found. PyInstaller output was built, installer was skipped.'
    }
}

Write-Output $distDir
