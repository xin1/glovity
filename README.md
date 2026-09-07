# Glovity

Glovity 是一个面向手部姿态与触觉交互实验的代码仓库。目前包含视觉标记位姿估计、相机标定、PnP 算法测试、多种 IMU 的数据采集与姿态显示，以及震动马达控制工具。

## 项目结构

```text
glovity/
├─ code/       可运行程序、嵌入式示例和上位机工具
├─ image/      标定板等项目图片资源
└─ 学习记录/   开发总结、算法原理和实验笔记
```

## `code` 目录总览

```text
code/
├─ 01_realtime_pose/               摄像头标记检测与实时 3D 位姿估计
├─ 02_camera_calibration/          相机内参与畸变标定
├─ 03_pnp_algorithm_test/          PnP + RANSAC 合成数据测试
├─ IMU_jy901_Arduino/              JY901/WitMotion Arduino SDK 与双 IMU 显示
├─ IMU_Mag_mpu9250_Arduino/        MPU9250 磁力计校准与 Arduino 九轴姿态融合
├─ IMU_icm20948_stm32f103c8t6/    STM32 + ICM-20948 串口实时 3D 显示
├─ IMU_mpu9250_stm32f103c8t6/     STM32 + MPU9250 九轴姿态仪表盘
└─ motor/                          Arduino UNO 震动马达控制与电脑端 UI
```

### 模块索引

| 目录 | 平台/硬件 | 主要内容 | 详细说明 |
|---|---|---|---|
| `01_realtime_pose/` | Python、OpenCV、摄像头 | 检测 Find-GCP/ArUco/AprilTag 类标记，通过 PnP 输出标记相对摄像头的位置、姿态和重投影误差 | [查看说明](code/01_realtime_pose/README.md) |
| `02_camera_calibration/` | Python、OpenCV、摄像头 | 使用棋盘格计算相机内参和畸变参数，生成实时位姿程序所需的 `camera_calibration.json` | [查看说明](code/02_camera_calibration/README.md) |
| `03_pnp_algorithm_test/` | Python、OpenCV | 使用带噪声和离群点的合成数据验证 PnP、RANSAC、LM 优化及质量门限 | [查看说明](code/03_pnp_algorithm_test/README.md) |
| `IMU_jy901_Arduino/` | Arduino、JY901/WitMotion | 保存官方 Arduino SDK 和 IIC、Modbus、串口示例，并提供双 IMU 串口可视化工具 | [查看说明](code/IMU_jy901_Arduino/README.md) |
| `IMU_Mag_mpu9250_Arduino/` | Arduino、MPU6500/MPU9250、AK8963 | 采集磁力计数据，执行完整 `3×3` 椭球校准，生成 Arduino 校准头文件并进行 Mahony 九轴姿态显示 | [查看说明](code/IMU_Mag_mpu9250_Arduino/README.md) |
| `IMU_icm20948_stm32f103c8t6/` | STM32F103C8T6、ICM-20948 | 解析 STM32 串口的六轴/九轴数据，完成自检、监视和实时三维姿态显示 | [查看说明](code/IMU_icm20948_stm32f103c8t6/README.md) |
| `IMU_mpu9250_stm32f103c8t6/` | STM32F103、MPU6500/MPU9250、AK8963 | 读取定点串口数据，在电脑端完成磁力计校准和 Mahony 九轴融合，并显示实时仪表盘 | [查看说明](code/IMU_mpu9250_stm32f103c8t6/README.md) |
| `motor/` | Arduino UNO、三针震动马达模块 | Arduino PWM 固件、串口协议和 Python 控制界面，用于调节震动强度并安全停止输出 | [查看说明](code/motor/README.md) |

前三个数字编号目录构成视觉位姿流程：先用 `02_camera_calibration` 标定相机，再运行 `01_realtime_pose` 获取真实标记位姿；修改 PnP 求解逻辑后，可使用 `03_pnp_algorithm_test` 做无硬件回归测试。其余目录是相互独立的硬件实验，请进入对应目录并按照子 README 操作。

## 运行实时位姿识别

推荐先进入项目根目录：

```powershell
cd D:\Desktop\github\glovity
```

如果使用当前已经识别成功的 Find-GCP 标记 ID 19，运行：

```powershell
C:\Python313\python.exe .\code\01_realtime_pose\aruco_palm_pose.py --calibration .\code\02_camera_calibration\camera_calibration.json --marker-length 0.04 --marker-id 19 --dictionary 99 --polarity both --camera 0 --width 1280 --height 720
```

如果想让程序锁定启动后检测到的第一个标记，把 `--marker-id 19` 改成 `--marker-id -1`（也可以直接省略 `--marker-id`）。锁定后不会跳到其他标记；按 `R` 可释放并重新选择：

```powershell
C:\Python313\python.exe .\code\01_realtime_pose\aruco_palm_pose.py --calibration .\code\02_camera_calibration\camera_calibration.json --marker-length 0.04 --marker-id -1 --dictionary 99 --polarity both --camera 0 --width 1280 --height 720
```

## 实时程序参数说明

- `--calibration`：相机标定文件路径，当前使用 `camera_calibration.json`。
- `--marker-length`：真实标记边长，单位是米。40 mm 就写 `0.04`。
- `--marker-id`：指定唯一追踪的标记 ID。写 `19` 表示只处理 ID 19；默认值 `-1` 表示锁定首次检测到的标记，按 `R` 可重新选择。
- `--dictionary`：标记字典。当前 Find-GCP 自定义 3x3 字典使用 `99`。
- `--polarity`：标记颜色极性。`normal` 只识别常规颜色，`inverted` 只识别黑白反相标记，`both` 会先试常规图，再试反相图。
- `--camera`：摄像头编号。一般内置或第一个 USB 摄像头是 `0`。
- `--width`：摄像头画面宽度，例如 `1280`。
- `--height`：摄像头画面高度，例如 `720`。
