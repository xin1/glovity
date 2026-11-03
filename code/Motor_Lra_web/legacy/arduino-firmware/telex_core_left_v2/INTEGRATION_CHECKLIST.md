# TELEX CORE LEFT V2.0 联调检查清单

## 从 AW86234-Haptic 1.0.7 移植的关键项

- AW86234 地址 `0x5A`，TCA9548A 地址 `0x70`。
- 持续反馈使用 CONT 模式；`TRACK_EN=0`、`DRV_WIDTH=80`、`WAIT_NUM=0`、`DRV1_TIME=255`、`DRV2_TIME=0`。
- 网页 AMP 25–47 映射为 AW 驱动电平 18–40，不沿用未经验证的高电平映射。
- 持续非零期间只读改写 `0x1C` 的低 7 位，保留同寄存器其他位，不重复 STOP/GO。
- 短击在 TELEX 板上使用 18 ms；旧 LRA 驱动仍保持 100 ms。
- 停止时设置 `BRK_TIME=4`，开启制动并 STOP；16 ms 后必须关闭 `BRK_EN`、清零 `BRK_TIME` 并再次 STOP，避免残余微振。
- 主机停止、串口失联和通道切换都会停止输出；固件还有 1.5 秒无命令看门狗。

## 与旧交付包不同的硬件

旧包是 ESP32-C6、8 通道测试板。TELEX 核心板是 ESP32-S3、5 通道，原理图对应：

- I²C SDA/SCL：GPIO6/GPIO7
- TCA_RESET：GPIO21
- 振动电源使能：GPIO4，低电平有效
- AW 通道：TCA 0–4
- USB：ESP32-S3 原生 USB CDC

因此不要把旧包的 `bootloader.bin`、`partition-table.bin` 或 `app-1.0.7.bin` 烧录到 TELEX 板。

## 首次上电检查

1. 先不安装马达或将 AMP 保持 25，烧录 S3 固件并重新插拔 USB。
2. EXE 连接串口后确认显示 `TELEX`，日志中五路均为 `OK 2340`。
3. 依次选择五个手指，确认收到 `EVENT CHANNEL 0` 到 `EVENT CHANNEL 4`。
4. 单路安装马达，从 AMP 25 开始短击；确认停止后没有持续微振。
5. 再测试持续按压，日志第一帧为 `EVENT START`，后续压力变化应为 `EVENT OUTPUT`，不应反复 START。
6. 松开后应出现 `EVENT STOP`；拔掉 USB 或停止主机命令后，最迟由看门狗关闭输出。
7. 单路确认后再接五路，并检查最大强度下电源电流、驱动芯片和马达温升。

## 当前验证边界

网页自动测试覆盖五指通道路由、18 ms 短击选择、强度 18–40 映射、热更新和制动清理的代码存在性。当前开发环境没有 Arduino CLI/PlatformIO，也没有连接 TELEX 真板，因此 ESP32-S3 编译、GPIO4 实际使能极性、五路 CHIPID 和机械手感仍必须按上面的步骤做真板确认。
