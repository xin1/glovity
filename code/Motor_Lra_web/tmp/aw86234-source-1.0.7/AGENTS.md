# AGENTS.md — `aw86234-testbench` 代码树

> **本项目是「指尖震动模块」的代码树，也是该项目的唯一有效代码树。**
> **动手前请先读资料树的交接总文档：**
> `D:\Users\Bingxu_Ma\Documents\Glovity\指尖震动模块_JW-BLE-DL500_V2.0\AGENTS.md`
> 那里有完整的项目状态、待办清单、环境命令与三个必读陷阱。
> 最后更新：2026-09-29

---

## 这是什么

**AW86234 八路振动测试板 —— ESP32-C6 固件 + PC 串口调试上位机**

* 目标板卡：`Vibration_Test_Board_A_V1.0`（ESP32-C6-MINI-1 + TCA9548A + 8 × AW86234FCR，CH4~CH7 已焊接）
* 规格来源：`D:\Users\Bingxu_Ma\Documents\Glovity\指尖震动模块_JW-BLE-DL500_V2.0\07_串口调试工具开发包\`
* 完整说明见本目录 **`README.md`**（环境、构建、烧录、M0→M7 首次联调、安全须知、交付物对照）

---

## 只在本目录改代码

| 位置 | 状态 |
|---|---|
| **`D:\aw86234-testbench\`（本目录）** | ✅ **唯一有效代码树**，所有修改/构建/烧录都在这里 |
| `…\指尖震动模块_JW-BLE-DL500_V2.0\aw86234-testbench\` | ❌ **已停止维护的废弃副本**，缺 8 项修复，**改它等于白干** |
| `E:\Glovity\aw86234-testbench\` | 📦 2026-09-29 归档快照（FAT32 U 盘，**只读备份，不要在这里开发**） |

> 废弃副本的原因见其自带 `README_代码已迁移.md`：**中文路径会让 ESP-IDF 的 ldgen 调不动
> `riscv32-esp-elf-objdump.exe`，固件根本构建不出来。**

---

## 三条硬性约束

1. **构建目录必须是纯 ASCII 路径。** 本目录是 ASCII 路径，直接 `idf.py build` 即可；
   若改用资料树内的路径构建，必须走 `firmware/build.ps1`（自动把构建目录放到 `%TEMP%\awtest-idf-build`）。
2. **串口是 `COM32`**（USB-Serial-JTAG）。`idf.py monitor` 与上位机**抢同一个串口**，用上位机前先退出 monitor。
3. **安全**：8 路满幅电流 3~8 A，远超 USB 口 500 mA；多路并发前必须限流。
   播放有 2 s 安全超时、同时满幅路数上限 2、5 s 心跳看门狗——**都是设计，不是 bug**。

---

## 常用命令

```powershell
# 固件
. C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1
cd D:\aw86234-testbench\firmware
idf.py set-target esp32c6
idf.py build
idf.py -p COM32 flash monitor        # 退出 monitor：Ctrl+]

# 固件纯逻辑单测（无需 ESP32 硬件，419 项）
pwsh D:\aw86234-testbench\firmware\main\test\run_host_tests.ps1

# 上位机
cd D:\aw86234-testbench\host
python -m pytest -q                  # 测试
python -m awtest                     # 启动 GUI
python -m awtest --selfcheck         # 无硬件自检
python -m awtest --cli --port COM32  # 纯文本联调
```

---

## 当前状态与待办（摘要）

**已完成**：测试板 CH4~CH7 硬件验证全部通过（掩码 `0xF0`，四个马达实测振动）；
固件 F-1/F-2/F-2b/F-3 修复完成；TRIG 改 8 路独立；BOOT 键演示交付；
持续振动（`CONT` + `WAIT_NUM=0`）已实现；F0 采纳 168 Hz；RL 7.87~8.08 Ω；VBAT 5.07~5.10 V。

**待办（详见 `docs/已知问题与待确认清单.md` 与资料树 AGENTS.md）**：
* 三条交付级实测结论未回填：CONT 中改幅度即时生效性、RTP 12K/24K 经 TCA 可行性、`max_simul_full_amp` 取值
* ⚠️ **GUI 的 CONT 参数页仍显示手册默认值**（会重现"一段一段"断续振动）→ 待加"持续振动"预设或 `WAIT_NUM>0` 提示
* 工作重心已转向 `TELEX_CORE_LEFT_V2.0` 核心板的 P0 整改与 ESP32-S3 换料

**注意**：`docs/已知问题与待确认清单.md` 的 **§1 硬件闸门表仍标 `【待填】`，但 §0「实测回填」已给出全部结论——以 §0 为准**。
