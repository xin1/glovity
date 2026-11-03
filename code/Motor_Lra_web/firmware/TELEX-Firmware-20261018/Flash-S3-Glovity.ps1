[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(COM\d+|/dev/.+)$')]
    [string]$Port,

    [ValidateRange(115200, 921600)]
    [int]$Baud = 460800
)

$ErrorActionPreference = 'Stop'
$PackageRoot = $PSScriptRoot
$BuildDir = Join-Path $PackageRoot 'build\esp32s3-glovity'
$Bootloader = Join-Path $BuildDir 'bootloader\bootloader.bin'
$PartitionTable = Join-Path $BuildDir 'partition_table\partition-table.bin'
$Application = Join-Path $BuildDir 'telex_device.bin'

foreach ($File in @($Bootloader, $PartitionTable, $Application)) {
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        throw "Build output is missing: $File`nRun .\Build-S3-Glovity.ps1 first."
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'python was not found. Run this script in an ESP-IDF 6.0 PowerShell terminal.'
}

Write-Host "Flashing ESP32-S3 on $Port at $Baud baud."
Write-Host 'Writing bootloader, partition table and application only. NVS will not be erased.'
& python -m esptool --chip esp32s3 --port $Port --baud $Baud write-flash `
    --flash-mode dio --flash-freq 40m --flash-size 4MB `
    0x0 $Bootloader `
    0x8000 $PartitionTable `
    0x10000 $Application
if ($LASTEXITCODE -ne 0) {
    throw "Flash failed with exit code $LASTEXITCODE."
}

Write-Host 'Flash completed. Press EN/RESET or reconnect USB, then reconnect from the web app.'
