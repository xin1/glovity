# 固件构建/烧录助手（Windows）
#
# 为什么需要它：
#   本工程所在路径含有中文（"指尖震动模块_JW-BLE-DL500_V2.0"）。ESP-IDF 的
#   riscv32-esp-elf-objdump 在链接阶段处理**构建目录**里的归档文件时，会因非 ASCII
#   路径而报 "No such file or directory"（实测于 ESP-IDF v6.0 + Windows）。
#   把构建目录放到纯 ASCII 路径下即可绕开（源码路径不影响编译）。
#
# 用法：
#   pwsh firmware/build.ps1                 # 只构建
#   pwsh firmware/build.ps1 -Flash -Port COM7
#   pwsh firmware/build.ps1 -Monitor -Port COM7
#   pwsh firmware/build.ps1 -MergeBin        # 额外产出合并烧录 bin

[CmdletBinding()]
param(
    [string]$BuildDir = (Join-Path $env:TEMP "awtest-idf-build"),
    [switch]$Flash,
    [switch]$Monitor,
    [switch]$MergeBin,
    [string]$Port
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- 1) 确保 ESP-IDF 环境已激活 ----
if (-not $env:IDF_PATH) {
    $candidates = @(
        "C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1",
        "$env:USERPROFILE\esp\Microsoft.v6.0.PowerShell_profile.ps1"
    )
    $profile = $null
    foreach ($c in $candidates) {
        if (Test-Path $c) { $profile = $c; break }
    }
    if (-not $profile) {
        # 退而求其次：扫描 Espressif\tools 下的 profile
        $found = Get-ChildItem "C:\Espressif\tools" -Filter "Microsoft.*.PowerShell_profile.ps1" `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $profile = $found.FullName }
    }
    if (-not $profile) {
        Write-Error "未找到 ESP-IDF 环境。请先手动激活（例如 . C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1）后重试。"
        exit 2
    }
    Write-Host "激活 ESP-IDF 环境：$profile" -ForegroundColor Cyan
    . $profile | Out-Null
}

Write-Host "IDF_PATH = $env:IDF_PATH" -ForegroundColor Cyan
Write-Host "构建目录 = $BuildDir（纯 ASCII 路径，规避中文路径导致的 objdump 失败）" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

$common = @("-B", $BuildDir)
if ($Port) { $common += @("-p", $Port) }

Push-Location $Here
try {
    Write-Host "`n== idf.py build ==" -ForegroundColor Green
    idf.py @common build
    if ($LASTEXITCODE -ne 0) { throw "构建失败" }

    if ($MergeBin) {
        Write-Host "`n== idf.py merge-bin ==" -ForegroundColor Green
        idf.py @common merge-bin
    }
    if ($Flash) {
        Write-Host "`n== idf.py flash ==" -ForegroundColor Green
        idf.py @common flash
        if ($LASTEXITCODE -ne 0) { throw "烧录失败" }
    }
    if ($Monitor) {
        Write-Host "`n== idf.py monitor（Ctrl+] 退出）==" -ForegroundColor Green
        idf.py @common monitor
    }
    if (-not $Monitor) {
        Write-Host "`n完成。产物：$BuildDir\aw86234_testbench.bin" -ForegroundColor Green
    }
} finally {
    Pop-Location
}
