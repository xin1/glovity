# Windows 交付前自检。每一条都是硬门禁，任何一条不过就不许发布。
# 与 verify_artifact.sh（Linux）逐条对应，工具换成 dumpbin。
#
# 用法: powershell -ExecutionPolicy Bypass -File verify_artifact_windows.ps1 <telex_glove.dll> <dumpbin.exe>
param(
    [Parameter(Mandatory = $true)][string]$Dll,
    [Parameter(Mandatory = $true)][string]$Dumpbin
)
$ErrorActionPreference = 'Stop'
$script:Failed = $false

function Pass($msg) { Write-Host "  [OK]   $msg" }
function Fail($msg) { Write-Host "  [FAIL] $msg"; $script:Failed = $true }

Write-Host "== 交付物自检: $Dll =="

$headers = (& $Dumpbin /nologo /headers $Dll) -join "`n"
$exports = & $Dumpbin /nologo /exports $Dll
$deps    = & $Dumpbin /nologo /dependents $Dll

# 1) 目标架构必须是 x64
if ($headers -match '8664 machine \(x64\)') { Pass "目标架构 x64" }
else { Fail "目标架构不是 x64" }

# 2) 导出符号必须全部是 telex_glove_*
#    dumpbin /exports 的表格行形如: "    1    0 00001000 telex_glove_hub_create"
$names = @()
foreach ($line in $exports) {
    if ($line -match '^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s+(\S+)') { $names += $Matches[1] }
}
$leak = $names | Where-Object { $_ -notlike 'telex_glove_*' }
if ($names.Count -gt 0 -and -not $leak) { Pass "导出符号 $($names.Count) 个，全部为 telex_glove_*" }
else {
    Fail "存在非 telex_glove_* 导出符号（或未能解析导出表）:"
    $leak | Select-Object -First 20 | ForEach-Object { Write-Host "         $_" }
}

# 3) 动态依赖只允许系统 DLL；C/C++ 运行时必须已静态链入（/MT）
$depNames = @()
$inDeps = $false
foreach ($line in $deps) {
    if ($line -match 'Image has the following dependencies') { $inDeps = $true; continue }
    if ($inDeps -and $line -match '^\s+(\S+\.dll)\s*$') { $depNames += $Matches[1] }
    elseif ($inDeps -and $line -match '^\s*Summary') { break }
}
$allowed = @('kernel32.dll', 'advapi32.dll', 'ntdll.dll')
$crt = $depNames | Where-Object { $_ -match '^(vcruntime|msvcp|ucrtbase|api-ms-win-crt-|concrt|vccorlib)' }
$other = $depNames | Where-Object { $allowed -notcontains $_.ToLower() -and $crt -notcontains $_ }
if ($crt) { Fail "C/C++ 运行时未静态链接（集成方需另装 VC++ 运行库）: $($crt -join ', ')" }
else { Pass "C/C++ 运行时已静态链接" }
if ($other) { Fail "存在意外的动态依赖: $($other -join ', ')" }
else { Pass "动态依赖仅系统 DLL: $($depNames -join ', ')" }

# 4) 不得携带调试信息 / pdb 路径（CodeView 记录以 RSDS 开头）
$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path $Dll))
$latin1 = [System.Text.Encoding]::GetEncoding(28591).GetString($bytes)
if ($latin1.Contains('RSDS') -or $latin1 -match '(?i)\.pdb\b') { Fail "含调试信息或 .pdb 路径" }
else { Pass "无调试信息、无 .pdb 路径" }

# 5) 字符串审计：除白名单标签外不得出现中文；不得泄露源码路径与算法术语
#    按 Latin-1 把字节一一映射成字符后做字节级匹配，与 Linux 版同一套正则。
#    先像 strings -e S 那样切出完整的可打印串，再挑含中文的；不能只抽汉字片段，
#    否则 "拇指-近" 会被 '-' 切成 "拇指"，与白名单整条标签对不上而误报。
#    UTF-8 汉字固定为 E4-E9 开头 + 两个续字节，且要求连续 2 个以上汉字以避免机器码误报。
$wl = Get-Content -Encoding UTF8 (Join-Path $PSScriptRoot 'allowed_cjk.txt') | Where-Object { $_ -ne '' }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$cjkRe = '([\xe4-\xe9][\x80-\xbf]{2}){2,}'
$cjk = [regex]::Matches($latin1, '[\x20-\x7e\x80-\xff]{2,}') |
       Where-Object { $_.Value -cmatch $cjkRe } | ForEach-Object {
    $utf8.GetString([System.Text.Encoding]::GetEncoding(28591).GetBytes($_.Value))
}
if (-not ($cjk | Where-Object { $_.Contains($wl[0]) })) {
    # 白名单里的标签必须真能被提取到，否则"没发现问题"是绿灯谎报
    Fail "中文字符串检查失效（未能提取到已知标签），无法判定，按不通过处理"
} else {
    $unexpected = @()
    foreach ($s in $cjk) {
        $rest = $s
        foreach ($label in $wl) { $rest = $rest.Replace($label, '') }
        # U+4000-U+9FFF 即 UTF-8 的 E4-E9 首字节区间，与上面的字节正则一致
        if ($rest -cmatch '[\u4000-\u9fff]{2,}') { $unexpected += $s }
    }
    if ($unexpected.Count -eq 0) { Pass "中文字符串仅限标签白名单（提取到 $(@($cjk).Count) 条，自检命中）" }
    else {
        Fail "出现非白名单中文字符串（可能泄露算法语义）:"
        $unexpected | Select-Object -First 10 | ForEach-Object { Write-Host "         $_" }
    }
}

$pathLeak = [regex]::Matches($latin1, '(?i)[A-Z]:\\[\x20-\x7e]{3,}|\\Users\\|[\w/\\-]+\.(cpp|hpp)\b') |
            ForEach-Object { $_.Value } | Select-Object -Unique
if (-not $pathLeak) { Pass "无源码路径泄露" }
else { Fail "泄露源码路径:"; $pathLeak | Select-Object -First 5 | ForEach-Object { Write-Host "         $_" } }

# numpy / NUMPY 是 .npy 解析的必需品，不在禁列（理由同 Linux 版）
$hint = [regex]::Matches($latin1, '(?i)scipy|eigen|installation_error|skeleton|rotvec|bone_length|dip_coupling') |
        ForEach-Object { $_.Value } | Select-Object -Unique
if (-not $hint) { Pass "无算法线索字符串" }
else { Fail "泄露算法线索: $($hint -join ', ')" }

# 6) 安全加固位（dumpbin /headers 的 DLL characteristics 段）
if ($headers -match 'Dynamic base')                       { Pass "启用 ASLR (DYNAMICBASE)" }  else { Fail "缺少 DYNAMICBASE" }
if ($headers -match 'High Entropy Virtual Addresses')     { Pass "启用高熵 ASLR" }           else { Fail "缺少 HIGHENTROPYVA" }
if ($headers -match 'NX compatible')                      { Pass "启用 DEP (NXCOMPAT)" }      else { Fail "缺少 NXCOMPAT" }
# CFG 按位判断（IMAGE_DLLCHARACTERISTICS_GUARD_CF = 0x4000）：不同版本 dumpbin
# 对这一位的文字描述不同（旧版 "Guard"，新版 "Control Flow Guard"），数值才可靠
$dllChar = [regex]::Match($headers, '([0-9A-Fa-f]+) DLL characteristics')
if ($dllChar.Success -and ([Convert]::ToInt32($dllChar.Groups[1].Value, 16) -band 0x4000)) {
    Pass "启用 Control Flow Guard"
} else {
    Fail "缺少 /guard:cf（DLL characteristics = $($dllChar.Groups[1].Value)）"
}

# 7) 体积合理性（静态链 CRT 后通常数百 KB；异常偏大通常意味着误链或带了调试信息）
$size = (Get-Item $Dll).Length
if ($size -lt 3000000) { Pass "体积 $size 字节" } else { Fail "体积 $size 字节，异常偏大" }

Write-Host ""
if (-not $script:Failed) { Write-Host "== 全部门禁通过，可以发布 =="; exit 0 }
Write-Host "== 存在未通过项，禁止发布 =="
exit 1
