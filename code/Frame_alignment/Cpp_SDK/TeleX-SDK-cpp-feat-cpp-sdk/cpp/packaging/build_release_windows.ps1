# 在 Windows 上构建发布产物：全部测试 + 全部自检门禁 + 消费方验收 + 打包。
# 任一环节失败即中止，不会产出半成品。与 build_release.sh（Linux）流程对应。
#
# 前置: Visual Studio 2019/2022，勾选"使用 C++ 的桌面开发"（含 MSVC、Windows SDK、CMake）。
# 用法（在 cpp\ 目录，普通 PowerShell 即可，无需开发者命令行）:
#   powershell -ExecutionPolicy Bypass -File packaging\build_release_windows.ps1
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Ver  = ([regex]::Match((Get-Content -Raw (Join-Path $Root 'CMakeLists.txt')),
         'project\(TelexGlove VERSION ([0-9.]+)')).Groups[1].Value
$Pkg  = "telex-glove-$Ver-windows-x64"
$Build = Join-Path $Root 'build-win'
$Dist  = Join-Path $Root 'dist'
$Stage = Join-Path $Dist $Pkg

function Step($msg) { Write-Host ""; Write-Host "== $msg ==" }
function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "失败 (exit $LASTEXITCODE): $exe $($argv -join ' ')" }
}

# ---------------------------------------------------------------- 定位工具链
# 不要求在"开发者命令行"里运行：CMake 的 Visual Studio 生成器会自己找编译器，
# 这里只需借 vswhere 找到 VS 自带的 cmake / ctest / dumpbin。
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) { throw "未找到 Visual Studio（缺少 vswhere.exe）" }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "未找到带 MSVC x64 工具集的 Visual Studio" }

$vsCmake = Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
# 必须优先使用VS自带CMake。PATH中可能存在MinGW CMake，其默认Ninja生成器
# 与下面的-A x64不兼容，也可能错误选择MinGW编译器。
$cmake = if (Test-Path $vsCmake) { $vsCmake } else { (Get-Command cmake -ErrorAction SilentlyContinue).Source }
$ctest = Join-Path (Split-Path $cmake) 'ctest.exe'
$dumpbin = Get-ChildItem (Join-Path $vs 'VC\Tools\MSVC') -Recurse -Filter dumpbin.exe |
           Where-Object { $_.FullName -match '\\Hostx64\\x64\\' } | Select-Object -First 1 -ExpandProperty FullName
foreach ($t in @($cmake, $ctest, $dumpbin)) {
    if (-not $t -or -not (Test-Path $t)) { throw "缺少构建工具: $t（请在 VS Installer 中勾选 C++ CMake 工具）" }
}

Step "构建 $Pkg"
Write-Host "Visual Studio: $vs"
Write-Host "CMake:         $cmake"

if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Dist "$Pkg.zip"), (Join-Path $Dist "$Pkg.zip.sha256")

Run $cmake @('-S', $Root, '-B', $Build, '-G', 'Visual Studio 17 2022', '-A', 'x64',
             '-DTELEX_HARDENED=ON', '-DTELEX_BUILD_TESTS=ON', '-DTELEX_BUILD_EXAMPLES=ON')
Run $cmake @('--build', $Build, '--config', 'Release', '--parallel', '1')

Step "单元测试"
Push-Location $Build
try { Run $ctest @('-C', 'Release', '--output-on-failure') } finally { Pop-Location }

Step "交付物自检"
$dll = Join-Path $Build 'bin\Release\telex_glove.dll'
if (-not (Test-Path $dll)) { $dll = Join-Path $Build 'bin\telex_glove.dll' }
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'verify_artifact_windows.ps1') $dll $dumpbin
if ($LASTEXITCODE -ne 0) { throw "交付物自检未通过" }

Step "安装到暂存目录"
Run $cmake @('--install', $Build, '--config', 'Release', '--prefix', $Stage)

# 交付物中绝不能混入实现源码。有意交付的只有 include\telex_glove.hpp
#（header-only 糖层，不含算法）与 examples\ 下的示例。
$leak = Get-ChildItem $Stage -Recurse -Include *.cpp, *.hpp, *.pdb |
        Where-Object { $_.FullName -notmatch '\\examples\\' -and $_.Name -ne 'telex_glove.hpp' }
if ($leak) { $leak.FullName | Write-Host; throw "产物中混入了实现源码或 pdb" }

# ---------------------------------------------------------------- 消费方验收
# 以集成方的视角，从安装好的包 find_package 构建 C++ 与纯 C 两个程序，
# 并用随包录制流跑通，证明产物开箱可用（不依赖本仓库任何源文件）。
Step "消费方验收（find_package + 回放）"
$consumer = Join-Path $Build 'consumer'
New-Item -ItemType Directory -Force $consumer | Out-Null
Copy-Item (Join-Path $Stage 'share\telex-glove\examples\monitor_hands.cpp') $consumer
Copy-Item (Join-Path $Stage 'share\telex-glove\examples\minimal_poll.c') $consumer
@'
cmake_minimum_required(VERSION 3.16)
project(consumer LANGUAGES C CXX)
find_package(TelexGlove 1.0 REQUIRED)
# 示例里的中文字符串需按 UTF-8 编进程序（配合示例中的 SetConsoleOutputCP）
if(MSVC)
  add_compile_options(/utf-8)
endif()
add_executable(monitor_hands monitor_hands.cpp)
target_link_libraries(monitor_hands PRIVATE TelexGlove::telex_glove)
add_executable(minimal_poll_c minimal_poll.c)
target_link_libraries(minimal_poll_c PRIVATE TelexGlove::telex_glove)
'@ | Set-Content -Encoding ASCII (Join-Path $consumer 'CMakeLists.txt')

$cbuild = Join-Path $consumer 'build'
Run $cmake @('-S', $consumer, '-B', $cbuild, '-G', 'Visual Studio 17 2022', '-A', 'x64', "-DCMAKE_PREFIX_PATH=$Stage")
Run $cmake @('--build', $cbuild, '--config', 'Release', '--parallel', '1')
$cbin = Join-Path $cbuild 'Release'
Copy-Item (Join-Path $Stage 'bin\telex_glove.dll') $cbin
$sample = 'file://' + (Join-Path $Stage 'share\telex-glove\sample_capture.bin')
foreach ($exe in @('monitor_hands.exe', 'minimal_poll_c.exe')) {
    # PowerShell 5.1 在 Stop 模式下会把被重定向的 stderr 当成异常，这里临时放宽
    $ErrorActionPreference = 'Continue'
    $out = & (Join-Path $cbin $exe) $sample 2>&1
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0 -or -not ($out -match 'frame_seq=')) {
        $out | Select-Object -First 20 | Write-Host
        throw "消费方验收失败: $exe"
    }
    Write-Host "  [OK]   $exe 回放出数"
}

# ---------------------------------------------------------------- 打包
Step "打包"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = Join-Path $Dist "$Pkg.zip"
[System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $zip,
    [System.IO.Compression.CompressionLevel]::Optimal, $true)
$hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
"$hash  $Pkg.zip" | Set-Content -Encoding ASCII "$zip.sha256"

Step "产物内容"
Get-ChildItem $Stage -Recurse -File | ForEach-Object { $_.FullName.Substring($Dist.Length + 1) }
Step "校验和"
Get-Content "$zip.sha256"

Write-Host ""
Write-Host "== 完成: cpp\dist\$Pkg.zip =="
