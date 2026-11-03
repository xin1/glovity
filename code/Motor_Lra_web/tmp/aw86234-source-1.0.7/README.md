# aw86234-testbench

## 2026-09-30：触觉到振动反馈已接入

新增「触觉反馈」页面与 `python -m awtest.tactile`：本机 PX-6AX COM7/地址3 → AW86234 COM32/CH4，固件1.0.7。含可标定力度映射、四种触感、锁定鼠标交互的双轴合并曲线、CSV导出、零电平停机与重新起振、逐通道输出和断流停机。使用方法、算法、引用与实测边界见 [触觉反馈实现与验收](docs/触觉反馈实现与验收.md)。原有实时调试功能保留。


**AW86234 八路振动测试板 —— ESP32-C6 固件 + PC 串口调试上位机**

> 目标板卡：`Vibration_Test_Board_A_V1.0`（ESP32-C6-MINI-1 + TCA9548A + 8 × AW86234FCR，已焊接）
> 规格来源：`../07_串口调试工具开发包/`（任务书 `00`、协议 `03`、固件规格 `04`、上位机规格 `05`、验收清单 `06`）
> 本目录是**实现交付物**。

---

## 快速开始

双击根目录 `launch.cmd` 启动上位机。默认打开简化后的「快捷控制」：连接串口 → 选通道 → 选模式 → 下发并启动。CONT 连续振动无需上传波形；完整寄存器及原有页面可从「高级调试」展开。

详见 [快捷控制使用说明](docs/快捷控制使用说明.md)。

## 1. 它是什么

一套串口调试工具，让调试者能在 PC 上：

* 切换 **RAM / CONT / TRIG / RTP-12K** 四种播放模式（RTP-48K 在本板物理上不可用，界面会禁用并说明原因）
* 逐项编辑并下发**全部**参数，且**下发 → 回读 → 逐字段比对**（不一致会高亮）
* 编辑/导入/下发**自定义波形**（`WAVE_BEGIN→WAVE_ADD→WAVE_COMMIT`，含 SRAM 回读校验）
* 回读 **F0 / LRA 阻抗 / 电池电压 / 寄存器快照**，F0 失败时给出明确诊断而不是假数据
* 跑**批量测试脚本**（与 A/B/C 测试矩阵编号对齐），导出 CSV + Markdown 报告
* 任意地址**寄存器读写**，含地址簿、位域解释、整片 dump、未定义地址警告

固件侧实现协议 v1.0 的**全部命令与事件**，含三道安全保险（播放超时 / 同时满幅路数限制 / 心跳看门狗）。

---

## 2. 目录结构

```
aw86234-testbench/
├── README.md                     本文件
├── docs/
│   ├── 协议一致性说明.md          ★ 实现与协议 v1.0 的差异、三处文档不一致的处理、向量证据
│   ├── 测试报告模板.md            可直接填写的测试报告（与 06 文档记录表对齐）
│   ├── 已知问题与待确认清单.md    ★ 硬件闸门、待实测项、已知风险
│   └── 上位机模块接口约定.md      内部模块契约
├── reference/                    随包参考资料（协议参考实现 + 测试向量，两侧共用）
│   ├── protocol_reference.py / .c
│   ├── protocol_test_vectors.json     ★ 上下位机共用的 11 条向量
│   └── check_c_vectors.py
├── firmware/                     ★ ESP-IDF 工程（esp32c6）
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults
│   └── main/
│       ├── app_main.c            启动顺序（TRIG 置低必须第一件事）
│       ├── app_protocol.c/.h     帧分派、应答、事件队列
│       ├── app_ascii.c           ASCII 引导模式（含 wave 命令）
│       ├── app_session.c/.h      安全参数（NVS）、心跳、播放超时
│       ├── svc_*.c/.h            通道/播放/波形/测量/扫描/自检
│       ├── drv_*.c/.h            I2C 总线 / TCA9548A / AW86234
│       ├── proto_frame.c/.h      帧层（纯逻辑）
│       ├── wave_codec.c/.h       波形库打包与合成（纯逻辑）
│       ├── aw_profile.c/.h       64 字节 Profile（纯逻辑）
│       ├── convert.c/.h          参数换算（纯逻辑）
│       └── test/                 ★ 主机可跑的单元测试（不需要 ESP32）
└── host/                         ★ PySide6 上位机
    ├── pyproject.toml
    ├── requirements.txt
    ├── awtest/
    │   ├── __main__.py           入口：python -m awtest
    │   ├── protocol/             帧编解码 + 串口传输（无 GUI 依赖）
    │   ├── device.py             高层设备 API
    │   ├── profiles.py           参数 Profile + 预设
    │   ├── waveform/             波形生成/编辑/CSV（无 GUI 依赖）
    │   ├── script/               测试脚本引擎与报告导出（无 GUI 依赖）
    │   ├── ui/                   六个功能页 + 常驻通道矩阵 + 日志页
    │   └── resources/            寄存器地址簿、预设 Profile、预设测试脚本
    └── tests/                    pytest（协议向量、换算、脚本 schema、UI 冒烟）
```

---

## 3. 环境准备

| 组件 | 版本 | 说明 |
|---|---|---|
| ESP-IDF | **v5.3 或更新**（本工程在 **v6.0** 上实测构建） | 新版 `i2c_master` 驱动；组件名 `esp_driver_i2c` |
| Python（上位机） | 3.11+（实测 3.13） | `pyserial` / `PySide6` / `pyqtgraph` / `numpy` |
| 主机 C 编译器（跑固件单测） | 任意 gcc/clang，或 `pip install ziglang` | 见 §5.2；没有 ESP32 硬件也能跑 |

```powershell
# 上位机依赖
cd host
python -m pip install -r requirements.txt

# 固件单测用的主机编译器（若系统里没有 gcc）
python -m pip install ziglang
```

---

## 4. 固件：构建、烧录、监视

```powershell
# 1) 激活 ESP-IDF 环境（示例路径，按本机实际改）
. C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1

# 2) 构建
cd firmware
idf.py set-target esp32c6
idf.py build

# 3) 烧录并看日志（USB-Serial-JTAG，板载 Type-C）
idf.py -p COM7 flash monitor
#    退出 monitor：Ctrl+]
```

> ⚠️ **`idf.py monitor` 与上位机抢同一个串口**。用上位机之前务必先退出 monitor。

**如果 `idf.py build` 在链接阶段报 `riscv32-esp-elf-objdump ... No such file or directory`**：
这是 Windows 上**中文路径**导致的工具链问题（本工程目录名含中文，实测于 ESP-IDF v6.0）。
把构建目录放到纯 ASCII 路径即可绕开，本仓库提供了一个助手脚本：

```powershell
pwsh firmware/build.ps1                                # 构建（构建目录自动放到 %TEMP%\awtest-idf-build）
pwsh firmware/build.ps1 -Flash -Port COM7              # 构建 + 烧录
pwsh firmware/build.ps1 -Monitor -Port COM7            # 构建 + 监视
pwsh firmware/build.ps1 -MergeBin                      # 额外产出合并烧录 bin
```

> 源码放在中文路径下**不影响**编译，只有构建目录需要是 ASCII 路径。
> 实测产物：`aw86234_testbench.bin` ≈ 245 kB（250 880 字节），`ESP-IDF v6.0`，target `esp32c6`，**无编译警告**。

上电后应看到：

```
READY aw86234-testbench fw=1.0.0 board=1
输入 help 查看命令；发送 "MODE BIN" 切换到二进制协议模式
```

---

## 5. 首次联调（按 M0 → M7，**先不要写/用 GUI**）

### 5.1 M0 · 环境自检

烧录后看到上面的问候语即可。**没有任何输出**时先查 `docs/已知问题与待确认清单.md`
的第一条（C27 电容）与 `06_验收测试与调试清单.md` §4.1。

### 5.2 M1 · 总线打通（最关键的一步）

用 ASCII 模式直接验证（此时能看到最原始的信息）：

```
bus auto            → OK sel=LP sda=6 scl=7 speed=400000 tca=1 ctrl=0x00 pullup=0
scan                → 0x70 在线
probe all           → 逐个通道打印 CHIPID=0x2340（插了几块就报几路）
```

### 5.3 M2 · 第一次让它振起来（第一个真正的功能里程碑）

```
probe 4
softrst 4
default 4
cont 4 169 127       # CH4：169 Hz + 满量程，持续振动（默认按会话超时 2 s，可加第 4 个参数）
cont 4 169 110 10000 # 10 秒持续振动
```

期望：CH4 马达明显振动，且**全程连续平稳**。
**不振**时依次查：① SW3 是否在断开位、VDD 是否到位；② 该通道芯片的**输出球 A3(HDP)/C3(HDN)** 是否虚焊；③ 该路座子焊点与马达插头接触。

> ⚠️ **不要再用 `wave <ch> 1 175 90 40` 做首振**：24 kHz 采样率下 175 Hz 每周期约 137 个样本，
> 而波形库只有约 1 kB 预算 → 必然 `ERR_SRAM_OVERFLOW`（40 周期需约 5486 样本）。
> 现在 `wave` 已支持"自动降速 + 周期数收敛"，但**首振请用 `cont`**（不占 SRAM，可长时间振）。

> ★ **持续振动为什么要用 `cont` 而不是 `wave`**（2026-09 实测）：
> `cont` 走芯片**内部方波发生器**，本身没有循环接缝，**连续平稳**；
> 而 `wave` 是从 SRAM 波形库循环播放，每轮波形边界都会断续一下
> （10 周期 ≈ 59 ms ⇒ ~17 Hz 的"一块一块"顿挫感）。
> `cont` 已内置持续振动所需的全部参数（`DRV1_TIME=255`、`DRV2_TIME=0`、`BRK_TIME=0`、
> **`WAIT_NUM=0`**）—— 其中 `WAIT_NUM` 不置 0 就会在每个驱动段之间插停顿，
> 是"一段一段"的真正元凶。详见 `docs/` 与寄存器全表 §8.1。

### 5.4 M3–M7

按 `../07_串口调试工具开发包/06_验收测试与调试清单.md` §1 逐阶段推进；
卡住时查该文档 §4 的故障排查表。

### 5.5 用上位机

```powershell
cd host
python -m awtest                 # 启动 GUI
python -m awtest --selfcheck     # 无硬件自检：依赖/模块/协议向量
python -m awtest --cli --port COM7   # 纯文本联调（不启动 GUI）
```

连接流程（`05` 文档 §3.3）：先试二进制 `PING`，失败再发 `MODE BIN`；
随后 `GET_CAPS → GET_STATUS → BUS_PROBE(auto) → CH_PROBE_ALL`，最后启动心跳。

> **心跳不能关**：上位机停发请求 5 s（默认）后，固件会认为遥控端失联并强制停机——
> 这是安全设计，不是 bug。

---

## 6. 测试与验证

### 6.1 上位机

```powershell
cd host
python -m pytest -q
```

覆盖：协议 11 条向量 + CRC 自检 + 解析器健壮性、参数换算、波形生成/编辑/CSV、
脚本 schema 与执行引擎、UI 冒烟（offscreen）。

### 6.2 固件纯逻辑（**不需要 ESP32 硬件**）

```powershell
pwsh firmware/main/test/run_host_tests.ps1
```

该脚本会：

1. 从**同一份** `reference/protocol_test_vectors.json` 生成 `vectors_generated.h`；
2. 用主机 C 编译器（gcc 或 `python -m ziglang cc`）编译 `main/test/test_pure.c`
   与全部纯逻辑源文件（`-std=c11 -Wall -Wextra -Werror`）；
3. 运行并断言 **419 项**：CRC、11 条向量、解析器健壮性（粘包/逐字节断包/垃圾字节/
   `VER` 错/`LEN` 超限/CRC 损坏恢复/210 字节边界）、波形库打包（含 `N=127`、容量刚好满/超一字节）、
   内置波形合成、Profile 校验边界与默认值陷阱、参数换算。

### 6.3 协议一致性证据

见 `docs/协议一致性说明.md` §1：上下位机**共用同一份**向量文件，各自跑通并有输出记录。

---

## 7. 安全须知（务必读）

| # | 事项 |
|---|---|
| **S-1** | 任何一次"开始播放"都带安全超时（默认 2000 ms，可配）；超时后固件强制 `STOP + 刹车` |
| **S-2** | 同时满幅路数限制（默认 2）。广播/多路触发超限会被**拒绝**并返回 `ERR_SAFETY_LIMIT`，除非显式 `override` |
| **S-3** | 心跳看门狗（默认 5000 ms）。拔 USB / 关掉上位机 / 主机进程死掉 → 固件自动停机并进安全态 |
| — | 上电默认：所有 TRIG 输出低、所有通道未选通、不做任何自动播放 |
| — | **8 路满幅电流 3~8 A，远超 USB 口 500 mA**。多路并发测试前必须限流（本板从 Type-C VBUS 注入，**没有 J10 外部电源口**） |
| — | 本板**没有硬件复位、没有 GPIO 控制的马达电源**：复位只能靠写 `0x00=0xAA` 软复位或人工拨 SW3。界面上"断电重启"是禁用状态并写明原因 |
| — | ★ **2026-09 硬件更新：每个电机一条独立 TRIG**（`TRIG0~7` = `IO18/19/20/21/22/14/IO0/IO1`，各带 4.7K 下拉）→ **硬触发下 8 路完全独立**，不再有 CH5/6/7 联动 |
| — | **BOOT 键（SW2/GPIO9）演示**：上电约 2.5 s 后自动给在线通道装波形并切 TRIG 模式，按一下 BOOT 即对全部通道发一次硬触发脉冲（`svc_bootdemo.c`，可用 `BOARD_BOOTDEMO_ENABLE` 关掉） |

---

## 8. 已知问题与待确认

完整的硬件闸门与待实测清单见 **`docs/已知问题与待确认清单.md`**。开工前必须确认的三条：

1. **C27（SW2 消抖电容）必须 ≤10 nF** —— 否则每次上电进下载模式，固件根本不运行；
2. ~~马达回路的 16 颗串联电容必须为 0 Ω 或未贴~~ **本条已作废**：实板核对表明 `C24/C26` 是 **HDP/HDN 对地的 100pF**（正是数据手册推荐做法），**芯片到座子之间是直连**；原理图审查报告的 P0-1 结论与实物不符；
3. **J1 跳线必须插且只插一组**（推荐 `3-5 + 4-6` = LP）—— 不插无法通信，两组都插可能损坏 IO。

三条**要交付的实测结论**（软件已把测试做成可复现的操作）：

* CONT 播放中改写 `DRV1_LVL`/`DRV2_LVL` **是否即时生效**（决定实时幅度滑条的做法）
* RTP **12 kHz / 24 kHz** 经 TCA9548A 的实际可行性（48 kHz 物理不可用）
* 上游 I2C 上拉是否缺失（决定是否需要补焊 4.7 kΩ 并降速到 100 kHz）

---

## 9. 交付物清单对照（任务书 §3）

| 要求 | 交付位置 |
|---|---|
| ESP-IDF 工程，`idf.py build flash monitor` 一条龙 | `firmware/` |
| 协议全部命令与事件 | `firmware/main/app_protocol.c`（73 条命令 + 11 个事件） |
| 分层（协议 / 服务 / 驱动，驱动不含协议解析） | `app_*` / `svc_*` / `drv_*` |
| 波形编码器 | `firmware/main/wave_codec.c` |
| 上电自检（引脚 → TCA → 逐通道 CHIPID → 汇总） | `firmware/main/svc_selftest.c` |
| 波形编码器 / CRC16 / 帧解析的主机单测 | `firmware/main/test/`（419 项，`run_host_tests.ps1`） |
| Python 包，可 `pip install -e .` 或 `python -m awtest` | `host/pyproject.toml`、`host/awtest/` |
| 独立无 GUI 的协议模块，与固件逐字节一致 | `host/awtest/protocol/` |
| 六个功能页 + 日志控制台 | `host/awtest/ui/` |
| 参数 Profile 存 JSON，可保存/加载/另存 | `host/awtest/profiles.py` + `resources/profiles/` |
| pytest（协议、换算、脚本解析） | `host/tests/` |
| README（环境/构建/烧录/运行/首次联调） | 本文件 |
| 协议一致性说明 | `docs/协议一致性说明.md` |
| 测试报告模板 | `docs/测试报告模板.md` |
| 5 个预设测试脚本 | `host/awtest/resources/scripts/` |
