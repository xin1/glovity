param([switch]$SkipInstall)
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    python -m venv $VenvDir
}

if (-not $SkipInstall) {
    & $PythonExe -m pip install -r (Join-Path $ProjectDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed with exit code $LASTEXITCODE" }
}
& $PythonExe -m unittest discover -s $ProjectDir -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed with exit code $LASTEXITCODE" }
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "IMU761_Address_Tool" `
    --distpath (Join-Path $ProjectDir "dist") `
    --workpath (Join-Path $ProjectDir "build") `
    --specpath $ProjectDir `
    (Join-Path $ProjectDir "imu761_address_tool.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Write-Host "Build complete: $(Join-Path $ProjectDir 'dist\IMU761_Address_Tool.exe')"
