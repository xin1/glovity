# UNO 双 IMU 地址轮询测试（新版查询固件）

用于经典 Arduino UNO R3 / ATmega328P。必须确认两只 IMU 已刷支持 `68 04 地址 03 校验` 的查询固件，地址为01和02，上电后保持静默。程序只查询和读取，不修改地址或固件。一次查询一只，验证 CRC 后再查询另一只。

## 硬件接线

```text
                稳压3.3V
                   |
                  4.7kΩ
                   |
UNO D10 RX --------+---- A--|>|--K ---- IMU01 TXD1
                   |
                   +---- A--|>|--K ---- IMU02 TXD1

UNO D11 TX -- 1kΩ --+---------------- IMU01 RXD1
                    +---------------- IMU02 RXD1
                    |
                   2kΩ
                    |
                   GND

外部电源3.3V -------+---------------- IMU01 VIN
                    +---------------- IMU02 VIN
外部电源GND --------+---------------- IMU01 GND
                    +---------------- IMU02 GND
                    +---------------- UNO GND
```

每个 IMU TX 支路各用一个低压降肖特基二极管，阳极 A 朝 UNO D10，阴极 K 朝 IMU TX。公共 D10 用4.7kΩ上拉到3.3V。UNO D11 的5V输出使用1kΩ/2kΩ分压到约3.3V后再并联到两只 IMU RX。所有 GND 必须共地。裸模块 VIN 按手册供电；图中选择外部稳压3.3V作为统一电源，不要用 UNO 的3.3V引脚同时供两只。若使用带稳压转接板，按板上供电规格接线。

UNO D0、D1留给USB串口，不与IMU连接。拆掉测试阶段任何单机USB-TTL对这组信号线的并接。两只 SYNC_IN不接。

## 上传和观察

保持 `.ino` 和 `hi91.h` 在同一个 `imu761_uno_address_poll` 文件夹。在 Arduino IDE选择 **Arduino Uno**、相应端口并上传。串口监视器设为 **115200，文本显示**。传感器通信也是115200。程序发送的为5个二进制字节，不是ASCII，也不带CRLF。

每轮发送：

```text
地址01：68 04 01 03 08
地址02：68 04 02 03 09
```

正常输出类似：

```text
ID=01 ok=3 timeout=0 wrong_addr=0 crc_bad=0 discarded=0 overflow=0 imu_ms=... roll=... pitch=... yaw=...
ID=02 ok=3 timeout=0 wrong_addr=0 crc_bad=0 discarded=0 overflow=0 imu_ms=... roll=... pitch=... yaw=...
```

`ok` 两边持续增长、旋转某只时对应角度变化，说明两个地址都收到了完整且CRC正确的82字节HI91帧。`timeout`、`wrong_addr`、`crc_bad`、`overflow` 应保持为0。若出现超时、错地址或溢出，程序停止下一次查询；按UNO Reset重新开始。

超时时看 `rx_bytes` 和 `RX first bytes`：`rx_bytes=0` 表示没有读到任何回复，应先检查 D11 分压后的主机TX是否真正到达 IMU RX、D10回路、共地及固件查询模式；有字节但无法成帧，应检查软件串口在115200下的接收稳定性、回帧格式及二极管电平。`crc_bad=0` 单独不能说明线路正常。

## 重要限制

经典 UNO R3 的 SoftwareSerial 在115200下接收82字节连续数据，对中断时序和线路质量敏感。如果单只IMU用USB-TTL/新版地址工具查询正常，但本程序两只或单只经D10始终 `ok=0`，优先怀疑 UNO 软件串口。此时可用 STM32/Arduino Mega 的硬件 UART接同一组 TX/RX 做正式方案；地址轮询协议和二极管接线不变。不要仅凭软件串口失败判断固件查询协议有误。

程序按当前已验证的新Wearable HI91布局解析：总长82字节，整帧偏移10为地址，CRC-16/CCITT初值0，姿态角为小端float。若瑞芬新固件返回不同格式，需要根据实际回复更新解析。
