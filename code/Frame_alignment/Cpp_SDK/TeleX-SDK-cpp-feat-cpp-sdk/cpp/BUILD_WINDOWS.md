# Windows 构建与测试手册（内部）

面向本仓库开发者：在一台全新的 Windows 机器上，从零开始编译、测试、打包 C++ 手套 SDK，
并在真机上验收。面向集成方的说明见 [`docs/集成指南.md`](docs/集成指南.md)
（本文件不随产物分发）。

---

## 0. 当前状态（2026-10-29）

Windows 支持写在 `feat/cpp-sdk` 分支上，在 macOS 上做过以下验证：

| 项目 | 结果 |
|------|------|
| MinGW 交叉编译 + Wine 运行全部 6 个测试 | 通过 |
| `dump_capture` 未校准输出与 macOS 逐字节相同 | 通过 |
| 不存在的串口报 `no such port` | 通过 |
| UTF-8 中文路径（含反向验证：撤掉修复后测试会失败） | 通过 |
| 真 Windows + MSVC 编译、6 个测试（ctest） | 通过 |

**以下几项还没在真 Windows 上做过，是换机后首先要确认的：**

1. ~~用 **MSVC** 编译~~ 已通过
2. `build_release_windows.ps1` / `verify_artifact_windows.ps1` 完整跑通。首次实跑时自检有两条误报，已修正：中文标签被 `-` 切断后与白名单对不上；新版 dumpbin 把 CFG 写成 "Control Flow Guard" 而非 "Guard"
3. **真机串口**：`hw_selftest.exe COM3` 通过（Wine 模拟不了 USB 串口驱动）
4. 中文 Windows 上用 GBK 编码的中文路径传校准文件（ANSI 代码页兜底）

---

## 1. 准备环境

### 1.1 必装

| 软件 | 说明 |
|------|------|
| **Visual Studio 2022**（或 2019） | Community 版即可。安装时勾选工作负载 **"使用 C++ 的桌面开发"**，右侧确认已勾选 **MSVC x64/x86 生成工具**、**Windows 11 SDK**（或 10 SDK）、**适用于 Windows 的 C++ CMake 工具** |
| **Git for Windows** | 默认选项即可 |

CMake 不必单独安装，打包脚本会用 VS 自带的那份。若想在普通终端里直接敲 `cmake`，
可另装 CMake ≥ 3.20 并加入 PATH，脚本会优先用 PATH 里的。

### 1.2 选装（做 Python ↔ C++ 一致性比对时需要）

- **Python 3.10+**（安装时勾选 "Add python.exe to PATH"）

### 1.3 拉代码

代码仓库走 SSH，新电脑需要先把 SSH 公钥加到 Codeup：

```powershell
ssh-keygen -t ed25519 -C "你的邮箱"          # 一路回车
Get-Content $HOME\.ssh\id_ed25519.pub        # 复制输出，粘到 Codeup → 个人设置 → SSH 公钥
```

然后：

```powershell
git clone git@codeup.aliyun.com:6a55d418a6fcee143fa293e7/TeleX-SDK.git
cd TeleX-SDK
git switch feat/cpp-sdk
```

> `cpp/.gitattributes` 保证了金标准二进制文件与 `.sh` 脚本在 Windows 上原样检出，
> 不受 `core.autocrlf` 影响，无需改 git 配置。

**路径建议：** 仓库放在纯英文路径下（如 `D:\work\TeleX-SDK`）最省事。
放中文路径下理论上也能工作（SDK 与测试都按 UTF-8 处理路径），但这条路径上没做过真机验证。

---

## 2. 一键打包（含全部测试）

在 `cpp\` 目录，**普通 PowerShell** 即可（不需要"开发者命令行"）：

```powershell
cd cpp
powershell -ExecutionPolicy Bypass -File packaging\build_release_windows.ps1
```

脚本依次执行，任一步失败即中止：

1. 用 vswhere 定位 VS、cmake、ctest、dumpbin
2. 配置并编译（x64、Release、`TELEX_HARDENED=ON`）→ 输出在 `build-win\`
3. `ctest` 跑全部 6 个测试
4. `verify_artifact_windows.ps1` 发布门禁（见 §5）
5. 安装到 `dist\telex-glove-<版本>-windows-x64\`，检查没有混入实现源码
6. **消费方验收**：以集成方视角新建一个工程，`find_package` 链接安装好的包，
   编译 C++ 与纯 C 示例并用随包录制流回放出数
7. 打包为 `dist\telex-glove-<版本>-windows-x64.zip` + `.sha256`

成功时最后一行是：

```
== 完成: cpp\dist\telex-glove-1.2.0-windows-x64.zip ==
```

---

## 3. 日常开发（不打包）

改代码、调试时不必每次跑完整打包，直接用 CMake：

```powershell
cd cpp
cmake -S . -B build -A x64                    # 只需执行一次
cmake --build build --config Release
cd build; ctest -C Release --output-on-failure; cd ..
```

产物都在 `build\bin\Release\`（DLL 与 exe 在同一目录，直接双击/运行即可）：

| 文件 | 用途 |
|------|------|
| `telex_glove.dll` | SDK 本体（导入库在 `build\lib\Release\telex_glove.lib`） |
| `test_*.exe` | 单元测试（`ctest` 会全部跑） |
| `monitor_hands.exe` | 持续打印双手关节 |
| `hw_selftest.exe` | 真机验收自检 |
| `dump_capture.exe` | 录制流 → CSV，用于一致性比对 |
| `minimal_poll_c.exe` | 纯 C 示例 |

也可以用 VS 直接打开 `cpp` 文件夹（"文件 → 打开 → 文件夹"），VS 会识别 CMakeLists.txt，
可以断点调试。注意把配置切到 **x64-Release** 或 **x64-Debug**。

单独跑某个测试并看详细输出：

```powershell
.\build\bin\Release\test_public_api.exe
```

---

## 4. 真机测试

### 4.1 找串口号

插上 Receiver_RX → "设备管理器" → "端口 (COM 和 LPT)"，看到类似 `USB 串行设备 (COM3)`。
也可以在 PowerShell 里：

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
```

**确认没有其它程序占用该串口**（串口助手、Python 版上位机等）。Windows 上串口是独占的，
被占用时会报 `port busy or access denied`。

### 4.2 自检

```powershell
cd cpp\build\bin\Release
.\hw_selftest.exe COM3
.\hw_selftest.exe COM3 D:\calib\imu_calibration_left.npy D:\calib\imu_calibration_right.npy
echo $LASTEXITCODE          # 0 = 全部通过
```

检查项：端口打开 → 连续 5 秒出数（无长空档）→ 帧率与丢帧率 → 电量 → 关节有限且腕部为原点 →
骨长守恒 → 平展姿势共面性（会提示摆姿势，未校准时跳过）。

**第 2 项是重点。** 若板卡被反复复位（表现为每隔一会儿就掉线、`max_gap` 很大），
说明 DTR/RTS 没有被正确拉低。相关代码在 `src/serial_port_win.cpp` 的 `open()` 中
（`DTR_CONTROL_DISABLE` + `EscapeCommFunction(CLRDTR)`）。

### 4.3 实时观察

```powershell
.\monitor_hands.exe COM3
.\monitor_hands.exe COM3 左.npy 右.npy
```

### 4.4 拔线测试

运行 `monitor_hands.exe COM3` 期间拔掉 USB：应在 1 秒内看到"离线"，程序不应卡死或崩溃。
（SDK 不自动重连，这是已知行为。）

### 4.5 无硬件时

所有程序都可用 `file://` 回放代替串口：

```powershell
.\monitor_hands.exe file://..\..\..\tests\golden\sample_capture.bin
```

---

## 5. 发布门禁说明

`verify_artifact_windows.ps1` 与 Linux 的 `verify_artifact.sh` 逐条对应：

| 检查 | 不通过时多半是 |
|------|----------------|
| 目标架构 x64 | 用了 `-A Win32` |
| 导出符号全部为 `telex_glove_*` | 新增了带 `TELEX_GLOVE_API` 的非公开函数 |
| C/C++ 运行时已静态链接 | `CMAKE_MSVC_RUNTIME_LIBRARY` 没生效（CMake < 3.15 或策略被改） |
| 动态依赖仅系统 DLL | 引入了新的系统库依赖（若确属必要，加到脚本中的 `$allowed` 列表） |
| 无调试信息 / .pdb 路径 | 链接时带了 `/DEBUG` |
| 中文字符串仅限标签白名单 | 新增了中文错误信息；诊断信息必须用英文 |
| 无源码路径泄露 | 用了 `__FILE__` / `assert`，或 MSVC 运行库自带的路径字符串 |
| 无算法线索字符串 | 字符串里出现了 skeleton、rotvec 等术语 |
| ASLR / 高熵 ASLR / DEP / CFG | `TELEX_HARDENED` 没开，或链接选项被覆盖 |
| 体积 < 3 MB | 误链了大库或带了调试信息 |

**首次在真 Windows 上运行时**，"动态依赖仅系统 DLL"与"无源码路径泄露"这两条的匹配规则
是按 `dumpbin` 文档写的，未经实机验证。若出现**误报**（报出来的东西其实无害），
把完整输出记下来再调整规则；**不要直接删掉检查项**。

---

## 6. Python ↔ C++ 一致性比对

除 `ctest` 里的金标准测试外，还可以对同一份录制数据逐帧比对两侧输出：

```powershell
# 在仓库根目录，首次需要安装 Python 依赖
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# 录一段真实数据（用串口助手以"原始二进制"保存为 capture.bin），然后：
.\cpp\build\bin\Release\dump_capture.exe capture.bin 左.npy 右.npy > joints_cpp.csv
python cpp\tools\compare_capture.py capture.bin joints_cpp.csv --calib-left 左.npy --calib-right 右.npy
```

> ⚠️ PowerShell 5.1 的 `>` 重定向会把输出转成 UTF-16，CSV 会被破坏。请改用：
>
> ```powershell
> cmd /c ".\cpp\build\bin\Release\dump_capture.exe capture.bin 左.npy 右.npy > joints_cpp.csv"
> ```
>
> 或使用 PowerShell 7（`pwsh`），其默认输出为 UTF-8。

输出 `== 一致 ==` 且退出码为 0 即通过。默认容差 1e-9 米，未校准时可用 `--tol 1e-12` 收紧。

**用真机录制数据 + 真实校准文件跑这一项，是目前还没完成的关键验证。**

---

## 7. 常见问题

| 现象 | 原因与处理 |
|------|-----------|
| `未找到 Visual Studio（缺少 vswhere.exe）` | Visual Studio 没装（只装 VS Code 不算） |
| `缺少构建工具: ...cmake.exe` | VS Installer 中补勾"适用于 Windows 的 C++ CMake 工具" |
| 脚本报"无法加载，因为在此系统上禁止运行脚本" | 命令里必须带 `-ExecutionPolicy Bypass`，或先执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| 脚本中文全是乱码、报语法错误 | `.ps1` 的 UTF-8 BOM 被编辑器去掉了。用 VS Code 右下角"通过编码保存 → UTF-8 with BOM" |
| 编译报 `C4819`（该文件包含不能在当前代码页中表示的字符） | 某个源文件缺少 `/utf-8` 编译选项。SDK 自身已全局加上；你自己新建的工程需要 `add_compile_options(/utf-8)` |
| 控制台输出中文乱码 | 示例已调用 `SetConsoleOutputCP(CP_UTF8)`；若仍乱码，把终端字体换成支持中文的（如"新宋体"）或使用 Windows Terminal |
| `open failed: no such port (win32 error 2)` | COM 号不对，或设备没插好 |
| `open failed: port busy or access denied (win32 error 5)` | 串口被其它程序占用 |
| `unsupported baud rate` | 驱动不支持 2000000 波特率。检查是否装了正确的 USB 串口驱动 |
| 测试 `npy_loader` / `public_api` 找不到金标准文件 | 构建目录被拷到了别处。金标准路径编译时写死为源码目录，可设环境变量 `TELEX_GOLDEN_DIR` 指向 `cpp\tests\golden` |

---

## 8. 相关文件速查

| 文件 | 作用 |
|------|------|
| `src/serial_port_win.cpp` | Windows 串口实现（POSIX 版为 `serial_port.cpp`） |
| `src/file_util.hpp` | UTF-8 路径 → 宽字符文件 API，GBK 兜底 |
| `CMakeLists.txt` 中 `if(MSVC)` / `TELEX_HARDENED AND MSVC` 段 | 编译选项与加固 |
| `packaging/build_release_windows.ps1` | 一键打包 |
| `packaging/verify_artifact_windows.ps1` | 发布门禁 |
| `packaging/telex_glove.rc.in` | DLL 版本信息资源 |
| `tools/compare_capture.py` | Python ↔ C++ 逐帧比对 |
