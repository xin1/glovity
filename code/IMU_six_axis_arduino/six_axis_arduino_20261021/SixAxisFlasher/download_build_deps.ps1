$ErrorActionPreference = 'Stop'
$Root = Join-Path $PSScriptRoot 'build_toolchain\manual'
New-Item -ItemType Directory -Force -Path $Root | Out-Null

$Files = @(
    @('https://github.com/espressif/esp32-arduino-lib-builder/releases/download/idf-release_v5.1/esp32-arduino-libs-idf-release_v5.1-632e0c2a.zip', 'esp32-arduino-libs-idf-release_v5.1-632e0c2a.zip'),
    @('https://github.com/espressif/crosstool-NG/releases/download/esp-12.2.0_20230208/xtensa-esp32s3-elf-12.2.0_20230208-x86_64-w64-mingw32.zip', 'xtensa-esp32s3-elf-12.2.0_20230208-x86_64-w64-mingw32.zip'),
    @('https://github.com/espressif/arduino-esp32/releases/download/3.0.7/esp32-3.0.7.zip', 'esp32-3.0.7.zip'),
    @('https://github.com/espressif/arduino-esp32/releases/download/2.0.9/esptool-v4.6-win64.zip', 'esptool-v4.6-win64.zip')
)

foreach ($Item in $Files) {
    $Output = Join-Path $Root $Item[1]
    & curl.exe --location --fail --retry 10 --retry-delay 3 --continue-at - --output $Output $Item[0]
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($Item[1])" }
}

Set-Content -LiteralPath (Join-Path $Root 'DOWNLOAD_COMPLETE') -Value 'ok' -Encoding ascii
