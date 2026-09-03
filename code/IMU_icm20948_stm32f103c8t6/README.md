# STM32F103C8T6 + ICM-20948 实时 3D 显示

这个目录中的 Python 工具读取 STM32 串口数据，用六轴 Mahony 姿态融合实时显示 ICM-20948 模块的三维姿态。

## 当前数据格式

程序同时兼容两种 STM32 输出。旧版六轴格式：

```text
A:-0.127,-1.301,-1.002 g  G:-5.527,-11.634,-4.702 dps  T:27.93 C
```

字段含义：

- `A`：X/Y/Z 加速度，单位 g；
- `G`：X/Y/Z 角速度，单位 deg/s；
- `T`：ICM-20948 内部温度，单位摄氏度。

初始化提示等其他串口行会自动忽略。

加入磁力计和固件端 Mahony 融合后的完整九轴格式：

```text
A:-0.127,-1.301,-1.002 g  G:-5.527,-11.634,-4.702 dps  T:27.93 C  M:25.10,-3.20,41.80 uT  R:1.20,-2.30,87.40  USE:1,1
```

旧格式由电脑端六轴 Mahony 算法计算姿态；完整格式直接使用 STM32 返回的姿态结果。

## 1. 关闭其他串口软件

一个 COM 口同一时间只能由一个程序打开。运行 3D 工具前必须关闭：

- PowerShell 中的串口读取循环；
- 串口调试助手；
- STM32CubeIDE 串口终端；
- 其他占用 COM27 的程序。

如果 PowerShell 仍在运行，按 `Ctrl+C`，或者直接关闭 PowerShell 窗口。

## 2. 安装依赖

在本目录打开 PowerShell，执行：

```powershell
python -m pip install -r .\requirements.txt
```

如果 `python` 命令不可用，而电脑安装的是仓库之前使用的 Python 3.13：

```powershell
C:\Python313\python.exe -m pip install -r .\requirements.txt
```

## 3. 查看串口

```powershell
python .\imu_3d_visualizer.py --list-ports
```

应该能看到类似：

```text
COM27    USB-SERIAL CH340
```

## 4. 先运行自检

```powershell
python .\imu_3d_visualizer.py --self-test
```

正常结果：

```text
Self-test passed: parser and orientation filter are working.
```

然后可以用 5 秒串口监视模式确认实时数据能被解析：

```powershell
python .\imu_3d_visualizer.py --monitor 5
```

每一条有效数据前会显示 `[OK]`，最后显示解析数量。这个模式不打开 3D 窗口，适合区分“串口问题”和“绘图问题”。

## 5. 运行实时 3D

程序默认使用 115200 波特率，未传入 `--port` 时先检测 `COM28`；如果没有 `COM28`，再自动查找设备描述中的 `CH340` 或 `USB-SERIAL`。因此可以直接运行：

```powershell
python .\imu_3d_visualizer.py
```

也可以直接双击本目录中的：

```text
start_3d_viewer.bat
```

有多个 CH340 或自动识别失败时，可以明确指定：

```powershell
python .\imu_3d_visualizer.py --port COM28 --baud 115200
```

启动后：

1. 保持 ICM-20948 完全静止约 3 秒；
2. 右侧出现 `Gyro calibration: READY` 后再移动模块；
3. 蓝色长方体会随模块转动；
4. 红、绿、蓝箭头分别表示模块 X、Y、Z 轴；
5. 紫色 `Accel` 箭头显示当前加速度方向；
6. 右上角显示最近 20 秒 roll、pitch、yaw 曲线。

界面右下角的 `Parsed samples` 应持续增加，`Last packet age` 应保持在约 `0.02～0.2 s`。串口断开或被占用时，程序会每秒自动重试；释放串口后无需重启 3D 工具。

## 提高实时刷新率

如果 STM32 的 `while (1)` 末尾仍是：

```c
HAL_Delay(100);
```

那么 STM32 每秒只发送约 10 帧，3D 画面会明显跳动。建议改成：

```c
HAL_Delay(20);
```

这会把串口数据提高到约 50 Hz，115200 波特率仍能承载当前一行 ASCII 数据。修改后重新编译并下载 STM32 程序。

## 快捷键

| 按键 | 功能 |
|---|---|
| `R` | 将当前姿态设为显示零位 |
| `C` | 清空姿态并重新进行陀螺仪静止校准 |
| `Space` | 暂停/继续画面刷新 |
| `Q` 或 `Esc` | 关闭程序 |

## 不连接硬件时预览

```powershell
python .\imu_3d_visualizer.py --demo
```

演示模式会生成模拟姿态数据，用于确认 Python、Matplotlib 和 3D 窗口是否正常。

## 当前算法边界

当前 STM32 串口只输出加速度计和陀螺仪，因此工具使用六轴 Mahony 融合：

- roll 和 pitch 会被重力方向长期约束；
- yaw 只能由陀螺仪积分得到，是相对角度；
- yaw 会因为陀螺仪残余零偏逐渐漂移；
- 快速平移、碰撞或振动时，加速度计不再只包含重力，程序会在加速度模长超出 `0.75～1.25 g` 时暂时停止重力纠偏。

配置 AK09916 并完成磁力计标定后，可以把算法升级为九轴融合，用磁北方向约束 yaw。

## 常见问题

### COM 口被占用

```text
Serial error: could not open port 'COM28'
```

关闭串口助手和 PowerShell 串口循环，然后重新运行。

### 一直停在校准状态

校准要求连续静止约 2.5 秒，并满足：

```text
|加速度模长 - 1g| <= 0.25g
陀螺仪模长 <= 8 deg/s
```

将模块放稳，不要手持。如果静止数据仍明显超出这些范围，应先检查量程换算、机械振动和传感器接线。

### 有窗口但没有数据

确认：

- STM32 已经下载当前输出程序；
- 串口波特率为 115200；
- `--list-ports` 显示的 CH340 串口号正确，或让程序自动识别；
- STM32 TX `PA9` 接 USB-TTL RX；
- STM32 与 USB-TTL 共地；
- 串口输出仍是本文开头的 ASCII 格式。

### 3D 方向与模块方向不一致

首先分别绕物理 X/Y/Z 轴正向转动，记录 `gx/gy/gz` 的响应。模块丝印坐标与期望坐标不一致时，应在解析后统一做轴交换和符号修正，而不是只在绘图层随意改方向。
