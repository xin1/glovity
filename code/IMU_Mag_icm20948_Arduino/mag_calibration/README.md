# 三轴磁力计校准与九轴姿态显示

本目录支持 MPU6500/MPU9250 + AK8963 和 ICM-20948 + AK09916，提供磁力计数据采集、稳健完整 `3×3` 椭球校准、结果检查，以及实时九轴姿态显示。

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

Arduino 目录中保留两套硬件各自的原始采集和九轴数据流草图：

```text
arduino/
├─ mpu9250_ak8963_raw/
│  ├─ mpu9250_ak8963_raw.ino              采集磁力计数据
│  └─ mag_calibration.h                   自动同步的校准头文件
├─ mpu6500_ak8963_imu_stream/
│  ├─ mpu6500_ak8963_imu_stream.ino       正常使用的九轴数据流
│  └─ mag_calibration.h                   自动同步的校准头文件
├─ icm20948_ak09916_raw/
│  └─ icm20948_ak09916_raw.ino            AK09916 原始磁场采集
└─ icm20948_ak09916_imu_stream/
   ├─ icm20948_ak09916_imu_stream.ino     ICM-20948 九轴数据流
   └─ mag_calibration.h                   待替换的单位矩阵占位头文件
```

## ICM-20948 硬件连接

新草图采用 I²C 和 Arduino 默认的 `Wire` 总线，不需要连接 ICM-20948 的 INT、FSYNC，也不需要单独连接内部 AK09916。

| ICM-20948 模块引脚 | 连接 | 说明 |
| --- | --- | --- |
| `VIN` / `VCC` | 模块说明指定的电源 | 不确定时不要接 5 V，详见下方供电说明 |
| `GND` | Arduino `GND` | 必须共地 |
| `SDA` | Arduino `SDA` | I²C 数据线 |
| `SCL` | Arduino `SCL` | I²C 时钟线 |
| `AD0` / `SDO` | `GND`，推荐 | 地址为 `0x68`；接模块逻辑电压时地址为 `0x69` |
| `nCS` / `CS` | 模块逻辑电压 | 选择 I²C；若模块已有上拉可不额外连接，不能接超过模块允许值的电压 |
| `INT` | 不接 | 当前代码使用轮询，不需要中断 |
| `FSYNC` | 不接 | 当前代码不使用外部同步 |

常见 Arduino 默认 I²C 引脚：

| 主控板 | SDA | SCL | 注意事项 |
| --- | --- | --- | --- |
| Uno / Nano | `A4` 或标注的 `SDA` | `A5` 或标注的 `SCL` | 主控为 5 V 逻辑，模块必须自带电平转换，否则需要双向 I²C 电平转换器 |
| Mega 2560 | `20` | `21` | 同样是 5 V 逻辑 |
| Due | `20` / `SDA` | `21` / `SCL` | 3.3 V 主控 |
| ESP32 | 默认 `GPIO21` | 默认 `GPIO22` | 不同开发板可重新指定 |
| STM32F103（常见 Arduino Core 默认） | `PB7` | `PB6` | 以所选 Core 和板卡定义为准 |

供电不能只凭模块名称判断：

- ICM-20948 裸芯的接口电压不是 5 V，严禁把裸芯或没有电平转换的简单模块直接连接 Uno/Nano 的 5 V I²C；
- 模块标有 `VIN`、`3-5V`，并明确带稳压和双向电平转换时，按模块说明给 `VIN` 供电；
- 只有 `VCC` 的廉价模块通常应使用其标称的 1.8 V 或 3.3 V，先核对原理图或商品说明；
- 不确定模块是否带电平转换时，先使用 3.3 V/低压主控或增加双向 I²C 电平转换器，不要尝试 5 V。

代码会依次尝试 `0x68` 和 `0x69`，正常启动应检测到 ICM-20948 的 `WHO_AM_I = 0xEA`，随后通过旁路模式检测内部 AK09916 的 `WIA2 = 0x09`。

## ICM-20948 + AK09916 使用流程

### 1. 验证接线并采集原始磁场

用 Arduino IDE 打开并烧录：

```text
arduino/icm20948_ak09916_raw/icm20948_ak09916_raw.ino
```

串口监视器设为 `115200`。正常输出开头为：

```text
# ICM-20948 WHO_AM_I at 0x68 = 0xEA
# AK09916 WIA2 = 0x09
# columns: mx,my,mz
# READY
```

关闭串口监视器，再进行可视化采集：

```powershell
python .\mag_calibration\capture_magnetometer.py COM6
```

AK09916 草图输出未经旧校准处理的原始计数，不能拿 AK8963 的旧数据或旧矩阵替代。校准时应使用装配完成后的实际设备，并保持 LRA 马达关闭。

### 2. 手动选用 PASS 头文件

检查本次时间戳目录中的 `calibration_quality_report.md`。只有总体结论为 `PASS` 时，才将：

```text
mag_calibration/data/calibration_runs/<run_id>/mag_calibration.h
```

复制并覆盖：

```text
arduino/icm20948_ak09916_imu_stream/mag_calibration.h
```

目录中预置的是单位矩阵占位文件，仅用于先检查通信。未替换时，九轴草图会输出 `WARNING: identity mag calibration`，此时不要评价 Yaw 精度。

### 3. 烧录九轴数据流并显示姿态

烧录：

```text
arduino/icm20948_ak09916_imu_stream/icm20948_ak09916_imu_stream.ino
```

新草图与旧草图保持相同的十列协议、量程和换算：加速度计 `±4 g`（`8192 LSB/g`）、陀螺仪 `±2000 °/s`（`16.4 LSB/(°/s)`）、磁力计 `0.15 µT/LSB`。因此仍可运行：

```powershell
python .\mag_calibration\realtime_imu_3d.py COM6
```

当前 `realtime_imu_3d.py` 已接入这颗 ICM-20948 的六面加速度校准结果：

```text
原始加速度零偏：1203.228571, -11194.440476, -53.033333 LSB
每轴1g灵敏度：  8068.628571, 8088.726190, 8215.633333 LSB/g
加速度/陀螺映射：x,y,z
磁力计映射：      x,-y,-z
```

因此这块硬件直接运行即可，不再需要额外输入轴映射：

```powershell
python .\mag_calibration\realtime_imu_3d.py COM6
```

旧 MPU6500 使用的统一 `8192 LSB/g`、`--ag-map x,y,z` 和 `--mag-map y,-x,-z`，以及此前试用过的 ICM-20948 映射，均已在代码中保留为注释，当前不生效。六面校准参数只属于这颗 ICM-20948；更换 IMU 后必须重新测量，不能复制使用。

融合内部保持右手坐标系；显示和输出日志会统一修正 Roll/Pitch 的方向，使设备向左/右及前/后倾斜时，三维模型与实际动作同向。该显示变换不改变 Yaw，也不参与 Mahony 融合反馈。

如果 Roll/Pitch 正常而 Yaw 方向或轴不正确，只调整 `--mag-map`；不要为了修正坐标方向重新做椭球拟合。校准矩阵仍应先在 Arduino 的原始 AK09916 坐标系内应用，再执行 Python 坐标映射。

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

右侧方向覆盖默认使用当前数据各轴 `2%～98%` 分位范围的中点，不会自动读取正式或旧的 `calibration_matrix.json`。这样更换磁力计后不会因为旧硬铁中心而错误显示整片未覆盖区域。

只有确认是同一颗传感器、同一安装状态，并且 Arduino 输出仍处于相同原始尺度时，才可以显式使用已有 JSON 的中心辅助预览：

```powershell
python .\mag_calibration\capture_magnetometer.py COM6 `
  --preview-center-json .\mag_calibration\data\calibration_matrix.json
```

该参数只影响实时覆盖预览，不会把旧矩阵应用到采集数据，也不会改变完成后的完整椭球拟合。

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

临时覆盖当前磁力计默认轴映射：

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
- **Yaw 方向或航向不正确**：先确认磁力计已经完成椭球校准，再检查当前 ICM-20948 默认轴映射 `--mag-map x,-y,-z` 是否符合安装方向。
- **磁力计校准质量失败**：确认采集的是未校准数据，远离磁铁、电机、金属结构和大电流导线，然后缓慢转动设备并覆盖完整球面。
- **快速转动后回正较慢**：快速动作时加速度不再只表示重力，融合器会暂时降低加速度计的可信度；陀螺仪量程不足、积分项累积，以及磁力计低通或纠偏带宽较低也会延长恢复时间。早期 `±500 °/s` 日志中三个陀螺轴均出现削顶，当前已改为 `±2000 °/s`。
- **调整 `Kp/Ki`**：`Kp`太小会使回正较慢，太大容易抖动；`Ki`太小会使长期零偏修正不足，太大可能累积错误并引起振荡或持续漂移。
- **更换硬件后结果异常**：每颗磁力计和每块 PCB 都应独立重新校准。
