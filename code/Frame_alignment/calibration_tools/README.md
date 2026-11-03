# Glovity / JY901P 校准工具

## 1. 磁力计校准

现有 `good_esp32/RemoteCal_*` 固件已实现维特 JY901P 的 I²C 校准写入：解锁
`KEY=0xB588`，进入球面拟合 `CALSW=0x0007`，结束后退出并 `SAVE=0`。
`Glovity_NOKOV_REC_1.0/01_Firmware` 中交付的三个固件也包含相同命令。

直连手套：

```powershell
.\.venv\Scripts\python.exe -m calibration_tools.mag_calibrate --port COM5
```

通过接收器无线校准左手或右手：

```powershell
.\.venv\Scripts\python.exe -m calibration_tools.mag_calibrate --port COM5 --side left
.\.venv\Scripts\python.exe -m calibration_tools.mag_calibrate --port COM5 --side right
```

校准时远离磁铁、电机、扬声器和大块钢铁，缓慢转动整只手套，使三个轴分别覆盖完整一圈。
默认采集 45 秒，随后自动退出校准并保存到 11 颗 IMU。

## 2. 三步姿态校准

该工具读取项目现有 180 字节 V2 数据帧，不会写 JY901P 寄存器；它生成 C++/Python SDK
可直接加载的 `imu_calibration_left.npy` 或 `imu_calibration_right.npy`。

```powershell
.\.venv\Scripts\python.exe -m calibration_tools.three_step_calibrate `
  --port COM5 --side left --output imu_calibration_left.npy
```

按提示完成三个姿态。三个步骤中手指都必须完全平直，只转动整只手：基准平放、+90°、-90°。
每只实体手套分别执行一次。生成后用 `hub.set_calibration(...)` 加载，并检查
`snapshot.calibrated`。

## 依赖

需要 `pyserial`、`numpy`、`scipy`；项目 Python SDK 环境已经包含这些依赖。

## 协议核对

- JY901P 在本项目中走 ESP32 I²C 主机，不是 PC 直接访问 11 个串口设备。
- I²C 地址为 `0x50..0x59, 0x60`，每个寄存器值为小端 16 位。
- 正常输出由 ESP32 封装成 Glovity V2：`AA 55 02` 开头、180 字节、CRC-16/CCITT-FALSE。
- 磁力计校准控制走 ASCII 管理命令；直连手套使用 `$GLOVITY,CAL,*`，接收器使用
  `$GLOVITY,RX,MAINT,*` 与 `$GLOVITY,RX,CAL,*`。
- 三步姿态校准只读取正常 V2 四元数，不修改 JY901P；磁力计校准会修改并保存每颗
  JY901P 的内部磁场参数。
