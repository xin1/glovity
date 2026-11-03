param([string]$Python = 'python', [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$hostRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $hostRoot 'dist' }
Push-Location $hostRoot
$originalPath = $env:PATH
try {
    # Do not collect unrelated DLLs from tools injected into the caller's PATH.
    $pythonPath = (Get-Command $Python -ErrorAction Stop).Source
    $pythonRoot = Split-Path -Parent $pythonPath
    $env:PATH = "$pythonRoot;$pythonRoot/DLLs;$env:SystemRoot/System32;$env:SystemRoot"
    & $Python -m PyInstaller --noconfirm --clean --distpath $OutputDirectory --workpath (Join-Path $hostRoot 'build/pyinstaller') (Join-Path $PSScriptRoot 'AW86234.spec')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
} finally { $env:PATH = $originalPath; Pop-Location }
