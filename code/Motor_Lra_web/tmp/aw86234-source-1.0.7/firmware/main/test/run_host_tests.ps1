# 固件纯逻辑主机单测：生成向量 + 用 zig cc 编译并运行（不需要 ESP32 硬件）
#
# 用法（在仓库根 aw86234-testbench/ 下）：
#     pwsh firmware/main/test/run_host_tests.ps1
#
# 依赖：Python 3.11+ 且已 `pip install ziglang`（提供主机 C 编译器），
#       或系统 PATH 里已有 gcc。脚本会自行选择可用的编译器。

[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$FwMain = (Resolve-Path (Join-Path $Here "..")).Path
$Root = (Resolve-Path (Join-Path $FwMain "..\..")).Path
$BuildDir = Join-Path $Root "build\host_tests"

if ($Clean -and (Test-Path $BuildDir)) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Host "== 1/3 生成协议测试向量头文件 ==" -ForegroundColor Cyan
python (Join-Path $Here "gen_vectors.py")
if ($LASTEXITCODE -ne 0) { throw "gen_vectors.py 失败" }

Write-Host "`n== 2/3 编译 ==" -ForegroundColor Cyan
$Sources = @(
    (Join-Path $Here "test_pure.c"),
    (Join-Path $FwMain "proto_frame.c"),
    (Join-Path $FwMain "util_crc16.c"),
    (Join-Path $FwMain "wave_codec.c"),
    (Join-Path $FwMain "aw_profile.c"),
    (Join-Path $FwMain "convert.c")
)
$OutExe = Join-Path $BuildDir "test_pure.exe"

$CommonFlags = @("-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-I", $FwMain)

$Compiler = $null
if (Get-Command gcc -ErrorAction SilentlyContinue) {
    $Compiler = @("gcc")
} else {
    # pip 安装的 zig 提供主机 C 编译器
    $zigOk = $false
    try {
        python -c "import ziglang" 2>$null
        $zigOk = ($LASTEXITCODE -eq 0)
    } catch { $zigOk = $false }
    if ($zigOk) { $Compiler = @("python", "-m", "ziglang", "cc") }
}

if (-not $Compiler) {
    Write-Error "未找到主机 C 编译器。请 `pip install ziglang` 或安装 gcc/MinGW。"
    exit 2
}

$Cmd = @()
$Cmd += $Compiler
$Cmd += $CommonFlags
$Cmd += $Sources
$Cmd += @("-o", $OutExe, "-lm")

Write-Host ("  " + ($Cmd -join " "))
& $Cmd[0] $Cmd[1..($Cmd.Count - 1)]
if ($LASTEXITCODE -ne 0) { throw "编译失败" }

Write-Host "`n== 3/3 运行 ==" -ForegroundColor Cyan
& $OutExe
$rc = $LASTEXITCODE
if ($rc -ne 0) { throw "主机单测失败（退出码 $rc）" }
Write-Host "`n主机单测全部通过。" -ForegroundColor Green
exit 0
