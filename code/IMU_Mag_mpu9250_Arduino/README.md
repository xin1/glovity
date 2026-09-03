# 三轴磁力计校准与九轴姿态显示

本目录提供 MPU6500/MPU9250 + AK8963 的磁力计数据采集、稳健完整 `3×3` 椭球校准、结果检查，以及实时九轴姿态显示。

以下命令均假定当前终端位于仓库根目录：

```text
Three-axis_Mag_Calibration/
```

## 目录和文件说明

```text
mag_calibration/
├─ calibrate_magnetometer.py              正式校准程序
├─ simple_calibrate_magnetometer.py       早期简单对角校准，仅供算法对照
├─ interactive_calibration_visualization.py
│                                          交互式显示原始/校准后点云
├─ paths.py                                正式校准程序的统一路径配置
├─ requirements.txt                       Python 依赖列表
├─ README.md                              本说明
├─ tools/
│  ├─ capture_magnetometer.py             从 Arduino 串口采集三轴磁力计数据
│  └─ realtime_imu_3d.py                  Mahony 九轴融合和实时 3D 姿态显示
└─ data/
   ├─ magnetometer_data.csv               椭球校准输入数据
   ├─ calibration_matrix.json             正式校准参数和质量指标
   ├─ calibration_quality_report.md       人类可读质量报告
   ├─ mag_calibration.h                   生成的 Arduino 校准头文件
   ├─ magnetometer_data_calibrated.csv    校准后数据及内点标记
   └─ calibration_interactive.html        交互式校准效果页面
```

Arduino 目录中有两个配套草图：

```text
arduino/
├─ mpu9250_ak8963_raw/
│  ├─ mpu9250_ak8963_raw.ino              采集磁力计数据
│  └─ mag_calibration.h                   自动同步的校准头文件
└─ mpu6500_ak8963_imu_stream/
   ├─ mpu6500_ak8963_imu_stream.ino       正常使用的九轴数据流
   └─ mag_calibration.h                   自动同步的校准头文件
```

### 正式程序与简单程序的区别

日常只使用 `calibrate_magnetometer.py`。它拟合正定完整 `3×3` 椭球矩阵，包含轴间耦合修正、稳健异常点处理、质量报告和 Arduino 头文件同步。

`simple_calibrate_magnetometer.py` 只计算三个轴的独立缩放，属于早期对照程序。它也会写入 `data/calibration_matrix.json`，因此不要对正式数据运行它，否则会覆盖完整 `3×3` JSON，并造成 JSON 与 Arduino 头文件不一致。

## Python 环境

先直接运行需要的程序。只有出现 `ModuleNotFoundError`，确认当前 Python 缺少依赖后，再执行：

```powershell
python -m pip install -r .\mag_calibration\requirements.txt
```

主要依赖为 NumPy、SciPy、Matplotlib、Plotly 和 pyserial。

## 流程一：直接使用当前校准结果

仓库中已经包含校准参数。如果没有更换传感器、PCB 或安装位置，可以直接使用。

### 1. 烧录九轴草图

用 Arduino IDE 打开并烧录：

```text
arduino/mpu6500_ak8963_imu_stream/mpu6500_ak8963_imu_stream.ino
```

草图会调用同目录 `mag_calibration.h` 中的完整 `3×3` 校准。加速度计量程为 `±4 g`，陀螺仪量程为 `±2000 °/s`（`16.4 LSB/(°/s)`），串口波特率为 `115200`。正常启动会看到：

```text
# MPU6500/MPU9250 + AK8963 nine-axis stream
# MPU WHO_AM_I at 0x68 = 0x70
# AK8963 WIA = 0x48
# columns: us,ax,ay,az,gx,gy,gz,mx,my,mz
# READY
```

### 2. 启动实时姿态显示

关闭 Arduino IDE 串口监视器。COM6 可以直接运行：

```powershell
python .\mag_calibration\tools\realtime_imu_3d.py COM6
```

程序开始后的前三秒保持设备静止并尽量平放，用于估算陀螺仪零偏和初始姿态。随后窗口显示三维板体以及 Roll、Pitch、Yaw。

同时保存融合日志：

```powershell
python .\mag_calibration\tools\realtime_imu_3d.py COM6 `
  --log .\mag_calibration\data\imu_fused_log.csv
```

关闭图窗或按 `Ctrl+C` 结束。

## 流程二：重新采集并校准

更换传感器、PCB、传感器安装方向，或固定磁性环境明显变化后，应重新执行完整流程。

### 1. 让 Arduino 输出未校准磁力计数据

打开：

```text
arduino/mpu9250_ak8963_raw/mpu9250_ak8963_raw.ino
```

把：

```cpp
constexpr bool APPLY_MAG_CALIBRATION = true;
```

临时改为：

```cpp
constexpr bool APPLY_MAG_CALIBRATION = false;
```

然后烧录。此时输出仍包含 AK8963 厂家灵敏度修正，但不包含硬铁/软铁校准，适合作为椭球拟合输入。

### 2. 采集完整球面数据

关闭串口监视器并运行：

```powershell
python .\mag_calibration\tools\capture_magnetometer.py COM6
```

缓慢转动整套设备，尽量覆盖所有空间方向，建议采集数千点。按 `Ctrl+C` 结束。

默认输出：

```text
mag_calibration/data/magnetometer_data.csv
```

注意：再次采集会覆盖该文件。不要在 `APPLY_MAG_CALIBRATION = true` 时采集校准输入，否则会形成二次校准。

### 3. 运行正式完整 3×3 校准

```powershell
python .\mag_calibration\calibrate_magnetometer.py
```

程序采用统一公式：

```text
calibrated = soft_iron_matrix @ (raw - hard_iron_offset)
```

成功后会：

1. 写入 `data/calibration_matrix.json`；
2. 写入 `data/calibration_quality_report.md`；
3. 写入 `data/mag_calibration.h`；
4. 写入 `data/magnetometer_data_calibrated.csv`；
5. 将同一头文件同步到两个 Arduino 草图目录。

### 4. 检查质量报告

打开：

```text
mag_calibration/data/calibration_quality_report.md
```

首先确认“总体结论”为 `PASS`。同时关注内点比例、校准后半径 CV、P95 相对误差、方向覆盖和矩阵条件数。如果结论为 `FAIL`，不要立即烧录参数，应重新检查采集覆盖和环境磁干扰。

### 5. 查看交互式校准效果

```powershell
python .\mag_calibration\interactive_calibration_visualization.py
```

生成：

```text
mag_calibration/data/calibration_interactive.html
```

使用浏览器打开即可旋转、缩放并比较原始椭球和校准后球面。

### 6. 烧录九轴草图并运行

重新烧录：

```text
arduino/mpu6500_ak8963_imu_stream/mpu6500_ak8963_imu_stream.ino
```

然后运行：

```powershell
python .\mag_calibration\tools\realtime_imu_3d.py COM6
```

## 常用参数

查看任一程序的完整参数：

```powershell
python .\mag_calibration\calibrate_magnetometer.py --help
python .\mag_calibration\tools\capture_magnetometer.py --help
python .\mag_calibration\tools\realtime_imu_3d.py --help
```

临时覆盖磁力计默认轴映射：

```powershell
python .\mag_calibration\tools\realtime_imu_3d.py COM6 --mag-map x,y,z
```

映射中的三项分别指定输出轴来自哪个输入轴，每个映射必须且只能使用一次 `x`、`y`、`z`。如果俯仰或横滚方向不正确，调整 `--ag-map`；如果它们正常但 Yaw 不正确，只调整 `--mag-map`。

## 实时融合行为

- MPU6500 硬件低通约 41 Hz；
- Python 继续对加速度、陀螺仪和磁力计做一阶低通；
- 启动静止阶段估算陀螺仪零偏；
- Mahony 互补滤波融合三类传感器；
- 明显线性加速时暂不使用加速度修正；
- 磁场强度相对启动参考值偏差超过 30% 时暂不使用磁力计修正。

## 常见问题

- **串口打不开**：关闭 Arduino IDE 串口监视器，并确认 COM 端口没有被其他程序占用。
- **启动后姿态跳动**：重新启动，前三秒不要移动设备。
- **Yaw 方向不正确**：当前默认是 `--mag-map y,-x,-z`；其他硬件方向可显式覆盖。
- **校准质量失败**：远离磁铁、电机和大电流导线，重新缓慢覆盖完整球面。
- **更换硬件后结果异常**：每颗磁力计和每块 PCB 都应独立重新校准。
