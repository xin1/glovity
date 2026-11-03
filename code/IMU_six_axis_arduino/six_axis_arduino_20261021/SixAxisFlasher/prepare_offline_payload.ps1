$ErrorActionPreference = 'Stop'
$Project = $PSScriptRoot
$Manual = Join-Path $Project 'build_toolchain\manual'
$Data = Join-Path $Project 'build_toolchain\minimal_data'
$Stage = Join-Path $Project 'build_toolchain\extract_stage'
$Output = Join-Path $Project 'build_toolchain\compiled'
$Payload = Join-Path $Project 'offline_payload'
$Cli = Join-Path $Project 'vendor_toolchain\arduino-cli.exe'

function Expand-Package([string]$Archive, [string]$Destination, [string]$StageName) {
    $Unpack = Join-Path $Stage $StageName
    New-Item -ItemType Directory -Force -Path $Unpack, $Destination | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Unpack -Force
    $Children = @(Get-ChildItem -LiteralPath $Unpack -Force)
    $Source = $Unpack
    if ($Children.Count -eq 1 -and $Children[0].PSIsContainer) { $Source = $Children[0].FullName }
    Copy-Item -Path (Join-Path $Source '*') -Destination $Destination -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $Data, $Stage, $Output, $Payload | Out-Null

Expand-Package `
    (Join-Path $Manual 'esp32-3.0.7.zip') `
    (Join-Path $Data 'packages\esp32\hardware\esp32\3.0.7') 'core'
Expand-Package `
    (Join-Path $Manual 'esp32-arduino-libs-idf-release_v5.1-632e0c2a.zip') `
    (Join-Path $Data 'packages\esp32\tools\esp32-arduino-libs\idf-release_v5.1-632e0c2a') 'libs'
Expand-Package `
    (Join-Path $Manual 'xtensa-esp32s3-elf-12.2.0_20230208-x86_64-w64-mingw32.zip') `
    (Join-Path $Data 'packages\esp32\tools\esp-xs3\2302') 'compiler'
Expand-Package `
    (Join-Path $Manual 'esptool-v4.6-win64.zip') `
    (Join-Path $Data 'packages\esp32\tools\esptool_py\4.6') 'esptool'

$Config = Join-Path $Project 'build_toolchain\minimal-cli.yaml'
$Yaml = @(
    'directories:',
    "  data: $($Data.Replace('\','/'))",
    "  downloads: $((Join-Path $Project 'build_toolchain\minimal_downloads').Replace('\','/'))",
    "  user: $((Join-Path $Project 'build_toolchain\minimal_user').Replace('\','/'))"
) -join "`n"
Set-Content -LiteralPath $Config -Value $Yaml -Encoding utf8NoBOM

$GloveOut = Join-Path $Output 'GloveLeft'
$ReceiverOut = Join-Path $Output 'Receiver'
New-Item -ItemType Directory -Force -Path $GloveOut, $ReceiverOut | Out-Null

& $Cli --config-file $Config compile --fqbn 'esp32:esp32:esp32s3' --output-dir $GloveOut (Join-Path (Split-Path $Project -Parent) 'six_axis_arduino_2files_20261021\GloveLeft')
if ($LASTEXITCODE -ne 0) { throw 'GloveLeft compile failed' }
& $Cli --config-file $Config compile --fqbn 'esp32:esp32:esp32s3' --output-dir $ReceiverOut (Join-Path (Split-Path $Project -Parent) 'six_axis_arduino_2files_20261021\Receiver')
if ($LASTEXITCODE -ne 0) { throw 'Receiver compile failed' }

$GloveMerged = Get-ChildItem -LiteralPath $GloveOut -Filter '*.merged.bin' | Select-Object -First 1
$ReceiverMerged = Get-ChildItem -LiteralPath $ReceiverOut -Filter '*.merged.bin' | Select-Object -First 1
if (-not $GloveMerged -or -not $ReceiverMerged) { throw 'Merged firmware image was not generated' }
Copy-Item -LiteralPath $GloveMerged.FullName -Destination (Join-Path $Payload 'GloveLeft.merged.bin') -Force
Copy-Item -LiteralPath $ReceiverMerged.FullName -Destination (Join-Path $Payload 'Receiver.merged.bin') -Force
$EspTool = Get-ChildItem -LiteralPath (Join-Path $Data 'packages\esp32\tools\esptool_py\4.6') -Filter 'esptool.exe' -Recurse | Select-Object -First 1
if (-not $EspTool) { throw 'esptool.exe was not found after extraction' }
Copy-Item -LiteralPath $EspTool.FullName -Destination (Join-Path $Payload 'esptool.exe') -Force

Get-ChildItem -LiteralPath $Payload | Select-Object Name, Length
