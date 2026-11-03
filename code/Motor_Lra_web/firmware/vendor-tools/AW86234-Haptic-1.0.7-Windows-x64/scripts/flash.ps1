param([ValidateSet('Update','NewBoard')][string]$Mode='Update', [string]$Port='', [switch]$CheckOnly)
$ErrorActionPreference='Stop'
$packageRoot=Split-Path -Parent $PSScriptRoot
Write-Host 'AW86234 ESP32-C6 firmware 1.0.7'
Write-Host 'Close the control app and serial monitors before flashing.'
if (-not $Port) { $Port=Read-Host 'Motor board port [COM32]' }
if (-not $Port) { $Port='COM32' }
if ($Port -notmatch '^COM[0-9]+$') { throw 'Invalid COM port' }
$manifest=Get-Content -LiteralPath (Join-Path $packageRoot 'SHA256SUMS.json') -Raw | ConvertFrom-Json
$names=@('firmware/app-1.0.7.bin','tools/AW86234-Flasher.exe')
if ($Mode -eq 'NewBoard') { $names+=@('firmware/bootloader.bin','firmware/partition-table.bin') }
foreach($name in $names){
    $record=@($manifest.files | Where-Object { $_.path -eq $name })
    if($record.Count -ne 1){throw "Missing manifest entry: $name"}
    $actual=(Get-FileHash -LiteralPath (Join-Path $packageRoot $name) -Algorithm SHA256).Hash
    if($actual -ne $record[0].sha256){throw "Checksum mismatch: $name"}
}
$flashArgs=@('--chip','esp32c6','--port',$Port,'write-flash','--flash-mode','dio','--flash-freq','80m','--flash-size','4MB')
if($Mode -eq 'NewBoard'){
    $flashArgs+=@('0x0',(Join-Path $packageRoot 'firmware/bootloader.bin'),'0x8000',(Join-Path $packageRoot 'firmware/partition-table.bin'))
}
$flashArgs+=@('0x10000',(Join-Path $packageRoot 'firmware/app-1.0.7.bin'))
if($CheckOnly){Write-Host ('Verified command: '+($flashArgs -join ' ')); exit 0}
& (Join-Path $packageRoot 'tools/AW86234-Flasher.exe') @flashArgs
if($LASTEXITCODE -ne 0){throw "Flashing failed: $LASTEXITCODE"}
Write-Host 'Firmware written and verified. You can now open Start.cmd.'
