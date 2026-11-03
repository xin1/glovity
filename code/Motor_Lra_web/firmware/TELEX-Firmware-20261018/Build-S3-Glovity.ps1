[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PackageRoot = $PSScriptRoot
$SourceDir = Join-Path $PackageRoot 'source\firmware'
$BuildDir = Join-Path $PackageRoot 'build\esp32s3-glovity'
$SdkConfig = Join-Path $PackageRoot 'build-config\esp32s3\sdkconfig'

if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    throw 'idf.py was not found. Open an ESP-IDF 6.0 PowerShell terminal, then run this script again.'
}

Write-Host 'Building ESP32-S3 Glovity firmware (original binaries will not be overwritten)...'
& idf.py -C $SourceDir -B $BuildDir -D "SDKCONFIG=$SdkConfig" -D IDF_TARGET=esp32s3 build
if ($LASTEXITCODE -ne 0) {
    throw "ESP-IDF build failed with exit code $LASTEXITCODE."
}

Write-Host ''
Write-Host 'Build completed:'
Write-Host "  $BuildDir\bootloader\bootloader.bin"
Write-Host "  $BuildDir\partition_table\partition-table.bin"
Write-Host "  $BuildDir\telex_device.bin"
Write-Host 'Next command example: .\Flash-S3-Glovity.ps1 -Port COM5'
