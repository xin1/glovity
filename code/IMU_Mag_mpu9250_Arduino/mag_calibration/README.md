# 三轴磁力计校准与九轴姿态显示

本目录提供 MPU6500/MPU9250 + AK8963 的磁力计数据采集、稳健完整 `3×3` 椭球校准、结果检查，以及实时九轴姿态显示。

以下命令均假定当前终端位于仓库根目录：

```text
Three-axis_Mag_Calibration/
```

## 目录和文件说明

```text
mag_calibration/
├─ calibrate_magnetometer.py              校准核心及离线重算程序
├─ simple_calibrate_magnetometer.py       早期简单对角校准，仅供算法对照
├─ interactive_calibration_visualization.py
│                                          交互式显示原始/校准后点云
├─ paths.py                                正式校准程序的统一路径配置
├─ requirements.txt                       Python 依赖列表
├─ README.md                              本说明
├─ magnetometer_calibration_learning_notes.md
│                                          磁力计校准原理、实验和结果学习记录
├─ capture_magnetometer.py             正式可视化采集与校准入口
├─ realtime_imu_3d.py                  Mahony 九轴融合和实时 3D 姿态显示
└─ data/
   ├─ magnetometer_data.csv               椭球校准输入数据
   ├─ calibration_matrix.json             正式校准参数和质量指标
   ├─ calibration_quality_report.md       人类可读质量报告
   ├─ mag_calibration.h                   生成的 Arduino 校准头文件
   ├─ magnetometer_data_calibrated.csv    校准后数据及内点标记
   ├─ calibration_runs/                   独立保存每次实时校准试验及成功率
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

### 完整校准与简单程序的区别

日常重新校准从 `capture_magnetometer.py` 进入，它使用 `calibrate_magnetometer.py` 中的核心算法，完成实时覆盖显示、正定完整 `3×3` 椭球拟合、稳健异常点处理和质量检查。`calibrate_magnetometer.py` 仍保留为已有 CSV 的离线重算工具。

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
python .\mag_calibration\realtime_imu_3d.py COM6
```

程序开始后的前三秒保持设备静止并尽量平放，用于估算陀螺仪零偏和初始姿态。随后窗口显示三维板体以及 Roll、Pitch、Yaw。

同时保存融合日志：

```powershell
python .\mag_calibration\realtime_imu_3d.py COM6 `
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

确认下列开关为：

```cpp
constexpr bool APPLY_MAG_CALIBRATION = false;
```

然后烧录。此时输出仍包含 AK8963 厂家灵敏度修正，但不包含硬铁/软铁校准，适合作为椭球拟合输入。

### 2. 可视化采集并生成试验校准

关闭串口监视器并运行：

```powershell
python .\mag_calibration\capture_magnetometer.py COM6
```

窗口左侧显示原始三维点云，右侧显示72个方向格的覆盖情况。绿色表示该方向至少已有3个样本，黄色表示只有1～2个样本，红色表示尚未覆盖。

当前的结束规则为：

- 少于100个有效样本时，`Finish` 不会结束采集；
- 达到100个样本后可以手动点击 `Finish`；
- 500个样本和8/8八分体是界面建议及最终质量检查标准；
- 即使72/72个方向格全部覆盖，程序也不会自动结束，仍需点击 `Finish`。

原始 CSV 在采集过程中持续写入。点击 `Finish` 后，程序会停止串口采集，自动执行完整 `3×3` 稳健椭球拟合，并生成 JSON、质量报告、Arduino 头文件、校准后 CSV 和交互式 HTML。

每次采集都创建独立时间戳目录，不覆盖以前的试验数据，例如：

```text
mag_calibration/data/calibration_runs/20260907_153058/
```

不要在 `APPLY_MAG_CALIBRATION = true` 时采集校准输入，否则会形成二次校准。

### 3. 检查试验质量

打开本次试验目录中的：

```text
calibration_quality_report.md
```

首先确认“总体结论”为 `PASS`。同时关注内点比例、校准后半径 CV、P95 相对误差、方向覆盖和矩阵条件数。

如果椭球拟合成功但质量检查为 `FAIL`，报告和可视化仍会保留，但不应将该参数用于 Arduino。如果拟合本身报错，则保留原始 CSV 和错误记录。

### 4. 将 PASS 校准用于九轴草图

当确认某次试验为 `PASS` 后，将该试验的：

```text
mag_calibration/data/calibration_runs/<run_id>/mag_calibration.h
```

复制到：

```text
arduino/mpu6500_ak8963_imu_stream/mag_calibration.h
```

实时采集程序无论 PASS 还是 FAIL，都不会自动覆盖 Arduino 目录中的头文件。

### 5. 烧录九轴草图并运行

重新烧录：

```text
arduino/mpu6500_ak8963_imu_stream/mpu6500_ak8963_imu_stream.ino
```

然后运行：

```powershell
python .\mag_calibration\realtime_imu_3d.py COM6
```

## 常用参数

查看任一程序的完整参数：

```powershell
python .\mag_calibration\calibrate_magnetometer.py --help
python .\mag_calibration\capture_magnetometer.py --help
python .\mag_calibration\realtime_imu_3d.py --help
```

临时覆盖磁力计默认轴映射：

```powershell
python .\mag_calibration\realtime_imu_3d.py COM6 --mag-map x,y,z
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
- **启动后姿态跳动或持续漂移**：通常是启动零偏估计不准。重新启动，并在前三秒将设备平稳放置、完全静止。
- **Yaw 方向或航向不正确**：先确认磁力计已经完成椭球校准，再检查默认轴映射 `--mag-map y,-x,-z` 是否符合当前安装方向。
- **磁力计校准质量失败**：确认采集的是未校准数据，远离磁铁、电机、金属结构和大电流导线，然后缓慢转动设备并覆盖完整球面。
- **快速转动后回正较慢**：快速动作时加速度不再只表示重力，融合器会暂时降低加速度计的可信度；陀螺仪量程不足、积分项累积，以及磁力计低通或纠偏带宽较低也会延长恢复时间。早期 `±500 °/s` 日志中三个陀螺轴均出现削顶，当前已改为 `±2000 °/s`。
- **调整 `Kp/Ki`**：`Kp`太小会使回正较慢，太大容易抖动；`Ki`太小会使长期零偏修正不足，太大可能累积错误并引起振荡或持续漂移。
- **更换硬件后结果异常**：每颗磁力计和每块 PCB 都应独立重新校准。
