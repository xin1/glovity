# TeleX 手套 SDK — C++ 实现

Windows/Linux C++手套链路：180B V2及168/170B legacy协议、串口后台读取、
11路IMU → 21关节FK、三步校准。V2快照保留设备时间、REC、失败掩码、有效性及原始帧。
零外部依赖，对外暴露纯C ABI。

面向集成方的说明见 [`docs/集成指南.md`](docs/集成指南.md)。本文档面向本仓库开发者。

## 目录

```
cpp/
├── include/          交付给集成方的头文件（telex_glove.h / .hpp）
├── src/              实现，不随产物分发
├── tests/            单元测试 + golden/ 金标准（由 Python 侧生成）
├── examples/         C99 与 C++ 示例
├── tools/            金标准生成器、校准文件转换脚本
├── packaging/        Docker 构建环境、加固配置、发布与自检脚本
└── docs/             集成文档
```

## 开发构建

```bash
# 金标准由仓库现有 Python 实现生成，是 C++ 侧的唯一真值来源
PYTHONPATH=../src python3 tools/gen_golden.py

cmake -S . -B build -DTELEX_BUILD_TESTS=ON
cmake --build build -j
cd build && ctest --output-on-failure
```

## 发布构建

### Linux

```bash
bash packaging/build_release.sh        # 需要 Docker
# 产出 telex-glove-<版本>-linux-x86_64.tar.gz + .sha256
```

该脚本会在 Ubuntu 20.04 容器内构建（保证 glibc 向上兼容）、跑全部测试、
执行 `packaging/verify_artifact.sh` 的全部门禁，任一项不过即中止。

### Windows

需要 Visual Studio 2019/2022 并勾选"使用 C++ 的桌面开发"。在 `cpp\` 目录、
普通 PowerShell 中运行（无需开发者命令行，脚本会用 vswhere 自己找工具链）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_release_windows.ps1
# 产出 dist\telex-glove-<版本>-windows-x64.zip + .sha256
```

流程：MSVC x64 加固构建 → 全部测试 → `verify_artifact_windows.ps1` 门禁
（与 Linux 版逐条对应：仅导出 `telex_glove_*`、CRT 已静态链接、无 pdb、
中文字符串白名单、无源码路径、ASLR/DEP/CFG）→ 以集成方视角 `find_package`
构建 C 与 C++ 消费者并回放出数 → 打包。

从零配置 Windows 环境、日常开发、真机测试与常见问题见 [`BUILD_WINDOWS.md`](BUILD_WINDOWS.md)。

平台相关代码只有串口（`src/serial_port.cpp` / `src/serial_port_win.cpp`）与
文件访问（`src/file_util.hpp`，Windows 上走宽字符 API 以支持中文路径）。

## 与 Python 实现的关系

C++ 侧**逐位复现** Python 的数值行为，由 `tests/golden/` 的金标准强制约束：

| 路径 | 容差 |
|------|------|
| 旋转数学（对拍 scipy） | 1e-14 |
| 协议解析 / 帧构造 | 逐字节相同 |
| FK 未校准 | 1e-12 |
| FK 三步校准 | 1e-9 |
| 校准文件读取 | 逐位相同 |

修改任何算法前先跑 `ctest`；金标准变更必须同步说明原因。

对同一份真实录制数据逐帧比对两侧输出（补充金标准覆盖不到的真实姿势分布）：

```bash
./build/dump_capture capture.bin [左.npy] [右.npy] > joints_cpp.csv
PYTHONPATH=../src python3 tools/compare_capture.py capture.bin joints_cpp.csv \
    [--calib-left 左.npy] [--calib-right 右.npy]
```

## 有意的行为差异

| Python | C++ | 理由 |
|--------|-----|------|
| 每帧解析 3 次 | 解析 1 次 | 性能，无语义差异 |
| 只能轮询 | 增加 `wait_next` | 避免集成方忙等 |
| 无排序键（frame_id 会回绕） | 增加单调 `seq` | uint16 在30 Hz下约36.4分钟回绕 |
| 中文日志打到 stdout | 默认静默，诊断为中性英文 | 加固；库不应污染宿主输出 |
| 无离线数据源 | `file://<路径>` 回放 | 无硬件集成 + 一致性验证 |

## 加固要点

`-DTELEX_HARDENED=ON` 时：隐藏全部符号（version script 只放行 `telex_glove_*`）、
strip、静态链 libstdc++、去源码路径、RELRO/BIND_NOW/NX。

> version script 不只是防逆向。静态链入的 libstdc++ 符号若泄漏到 `.dynsym`，
> 会与宿主程序的 libstdc++ 发生符号插入，导致跨堆 new/delete 配对而**随机崩溃**。
> `verify_artifact.sh` 把"导出符号全部为 `telex_glove_*`"作为硬门禁。

诚实评估：以上措施把逆向成本从"`nm` + `strings` 直接看懂"提高到"需要认真做静态
分析与浮点常量比对"。骨长表等 IEEE754 常量在 `.rodata` 里仍是可识别的，有经验的
逆向者能在数日内恢复大部分几何参数。若需更强保护，下一步性价比最高的是把 FK 常量
表做成运行时解密的 blob。
