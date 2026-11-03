$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Entry = Join-Path $ProjectDir 'six_axis_flasher.py'
$OfflineDir = Join-Path $ProjectDir 'offline_payload'
$DistDir = Join-Path $ProjectDir 'dist_offline'
$BuildDir = Join-Path $ProjectDir 'build_offline'

$Required = @('esptool.exe', 'GloveLeft.merged.bin', 'Receiver.merged.bin')
foreach ($Name in $Required) {
    $Path = Join-Path $OfflineDir $Name
    if (-not (Test-Path -LiteralPath $Path)) { throw "Offline payload missing: $Path" }
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name 'SixAxisGloveFlasher_Offline' `
    --add-data "${OfflineDir};offline" `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $ProjectDir `
    $Entry

Write-Host "Built: $(Join-Path $DistDir 'SixAxisGloveFlasher_Offline.exe')"
