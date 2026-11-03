# UNO 双 IMU 测试（不使用 SYNC_IN）

本方案要求两路 TX 分开接收。原来的二极管公共 RX 连接必须拆开，否则两只 IMU 同时自动发送时仍会发生电平冲突，地址 01/02 无法避免冲突。

## 接线

| IMU | Arduino UNO R3 |
|---|---|
| 地址 01 TXD1 | D8 |
| 地址 02 TXD1 | D10 |
| 两只 GND | UNO GND，共地 |
| 两只 VIN | 按模块/转接板规格接外部稳压电源 |
| 两只 RXD1 | 测试时不接 |
| 两只 SYNC_IN | 不接 |

D9、D11 是 SoftwareSerial 必须占用的虚拟 TX 引脚，但本程序不会发送数据，保持悬空。IMU 的 3.3V TTL TX 可以直接驱动 UNO 输入。不要给 D8/D10 外接 5V 上拉，程序也会关闭内部 5V 上拉。

## 配置每只 IMU

一次只接一只 USB-TTL。在当前波特率下以 ASCII + CRLF 发送：

```text
LOG HI91 ONTIME 0.5
LOG ENABLE
SERIALCONFIG 9600
```

`SERIALCONFIG` 立即生效。把串口助手切换到 9600 后发送：

```text
SAVECONFIG
REBOOT
```

确认每只 IMU 在 9600 下约每 0.5 秒输出一帧，再接 Arduino。地址 01、02 不需要重新修改。

## 运行

在 Arduino IDE 选择经典 `Arduino Uno`，打开 `imu761_uno_no_sync.ino` 并上传。串口监视器选择 115200。程序监听地址 01 的 D8 约 1.5 秒，再监听地址 02 的 D10 约 1.5 秒，循环切换。

UNO 的 SoftwareSerial 同一时刻只能监听一路，所以切换期间会丢弃另一只 IMU 的帧。本程序适合低速功能测试，不适合严格同步、高速或完整无丢帧采集。正式采集建议使用至少两路硬件 UART 的主控，例如 Arduino Mega 或 STM32。

正常结果中两个 `frames` 都应持续增加，`crc`、`overflow` 和 `wrong_addr` 应保持 0。旋转某一只 IMU时，对应地址的姿态角应发生变化。
