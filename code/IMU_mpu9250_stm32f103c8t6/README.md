# STM32F103 + MPU6500/MPU9250 实时显示

本工具读取 STM32 USART1 输出，使用电脑端 Mahony 九轴融合计算姿态，并实时显示：

- 随传感器旋转的三维板体；
- Roll、Pitch、Yaw 姿态角；
- 三轴加速度，单位 `g`；
- 三轴角速度，单位 `deg/s`；
- 三轴磁场，单位 `uT`；
- 芯片温度，单位摄氏度。

兼容当前固件格式：

```text
A_mg=-2,-58,-928 G_mdps=855,1550,0 T_cC=2538 M_uT_x100=4087,9816,7002
```

也兼容磁力计暂无新数据时的 `M=NA`。

## 接线与串口

- STM32 `PA9/USART1_TX` 接 USB-TTL 的 `RX`；
- STM32 与 USB-TTL 必须共地；
- 串口参数为 `115200 8N1`；
- 运行前关闭串口助手、CubeIDE 串口终端等占用 COM 口的程序。

## 安装依赖

在本目录打开 PowerShell：

```powershell
python -m pip install -r .\requirements.txt
```

## 使用

列出串口：

```powershell
python .\imu_realtime_viewer.py --list-ports
```

运行解析自检：

```powershell
python .\imu_realtime_viewer.py --self-test
```

自动选择 CH340、CP210x 或 FTDI 串口并打开仪表盘：

```powershell
python .\imu_realtime_viewer.py
```

明确指定串口：

```powershell
python .\imu_realtime_viewer.py --port COM27 --baud 115200
```

只监视五秒串口并检查解析，不打开窗口：

```powershell
python .\imu_realtime_viewer.py --port COM27 --monitor 5
```

没有连接硬件时预览界面：

```powershell
python .\imu_realtime_viewer.py --demo
```

## 快捷键

| 按键 | 功能 |
|---|---|
| `M` | 开始采集磁场校准数据；旋转完成后再按一次，拟合并保存校准参数 |
| `R` | 将当前姿态设为显示零位 |
| `C` | 清空姿态并重新进行陀螺仪校准 |
| `Space` | 暂停或继续画面刷新 |
| `Q` / `Esc` | 关闭窗口 |

## 磁力计校准

建议在传感器装进最终设备、线缆和电池均处于正常位置后校准，并远离桌腿、扬声器、磁铁、大电流导线等磁性干扰源。

1. 打开实时窗口，按一次 `M` 开始采集；
2. 缓慢连续旋转整个设备，让六个面、各条边和各个斜向姿态都朝向上方；
3. 至少采集 200 个点，推荐旋转 30～60 秒。不要只在桌面上水平画“8”字，否则三维覆盖不足；
4. 再按一次 `M`。拟合成功后状态区会显示 `MAG CAL: READY`；
5. 参数保存在同目录的 `mag_calibration.json`，以后启动时自动加载并在九轴融合前应用。

如果提示 `samples are nearly planar` 或某一轴覆盖不足，请重新按 `M`，增加倒置、侧立和斜向姿态。若提示残差过大，请换到磁场更干净的位置重做。可用 `--mag-calibration 路径.json` 指定另一份校准文件。

## 数据说明

STM32 使用定点整数传输，Python 自动完成换算：

- `A_mg / 1000` 得到 `g`；
- `G_mdps / 1000` 得到 `deg/s`；
- `T_cC / 100` 得到摄氏度；
- `M_uT_x100 / 100` 得到 `uT`。

打开程序后先将模块静置约 2.5 秒，等待界面显示陀螺仪校准为 `READY`，再移动模块。

程序会自动估算陀螺仪静止零偏。磁力计校准使用三维椭球拟合，同时估算硬铁偏置和完整的 `3×3` 软铁校正矩阵。校准只能补偿随设备一起转动的固定磁性畸变，不能消除外界变化磁场；因此即使校准成功，也应远离磁铁、大块钢铁和大电流导线。

AK8963 原始磁场先在其自身坐标系中完成校准，再按 MPU9250 典型封装关系转换为 `[My, Mx, -Mz]` 参与融合。若使用外置磁力计且安装方向不同，需要修改程序顶部的 `MAG_TO_IMU_FRAME`。
