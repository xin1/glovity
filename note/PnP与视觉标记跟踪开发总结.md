# PnP 与视觉标记跟踪开发总结

> 日期：2026-08-19  
>
> 目标：使用单目摄像头和人工视觉标记，实时估计手掌的三维位置与姿态

## 1. 今日完成内容

今天完成了从 PnP 合成测试、相机标定到 ArUco/AprilTag 实时位姿跟踪的基础代码框架，并对当前立体标记无法识别的问题进行了初步定位。

整体流程已经明确为：

```text
相机内参标定
    ↓
检测标记二维角点
    ↓
使用已知标记尺寸建立三维角点
    ↓
PnP 求解 T_camera_marker
    ↓
转换为 T_camera_palm
    ↓
输出手掌位置、姿态和质量指标
```

## 2. PnP 合成数据测试

新增文件：

- `code/pnp_algorithm_test.py`

主要功能：

- 使用确定的真实旋转和平移生成三维点；
- 通过 `cv2.projectPoints()` 将三维点投影到图像；
- 添加可控像素噪声和错误匹配点；
- 使用 `solvePnPRansac()` 排除异常点；
- 使用 LM 非线性优化精修位姿；
- 计算内点率、重投影误差、旋转误差和平移误差；
- 检查所有目标点是否位于相机前方；
- 支持平面和非平面合成测试；
- 支持输出 JSON 测试报告。

今日测试结果：

```text
Quality gate:          PASS
Inliers:               68/80 (85.0%)
Inlier reproj. RMSE:   0.986 px
All-point RMSE:        159.603 px
Rotation error:        0.0736 deg
Translation error:     0.000299 model units
Outliers rejected:     12/12
Minimum depth:         1.2758 model units
Solver time:           9.934 ms
```

结果说明：

- 12 个故意注入的异常点全部被 RANSAC 排除；
- 有效内点重投影误差约为 1 像素；
- 旋转误差约为 `0.074°`；
- 平移误差约为 `0.000299` 个模型单位；
- `All-point RMSE` 很大是因为它包含了故意制造的异常点，不代表有效位姿误差；
- PnP 求解部分耗时约 10 ms，不包含摄像头采集和标记检测时间。

## 3. PnP 输入输出与坐标系

PnP 不是单纯的“三维转二维”。其输入和输出为：

```text
已知目标三维点 + 图像中的对应二维像素点
                  ↓
           目标相对于相机的位姿
```

OpenCV 输出满足：

```text
point_camera = R_camera_object @ point_object + t_camera_object
```

即输出变换为：

```text
T_camera_object
```

OpenCV 相机坐标系约定：

- `+X`：画面向右；
- `+Y`：画面向下；
- `+Z`：相机向前；
- `tvec` 的单位与三维模型的单位相同。

如果标记尺寸以米输入，平移输出就是米。

## 4. 方形标记的已知三维点

对于边长为 `L` 的平面方形标记，以标记中心为原点，可以定义：

```python
object_points = np.array(
    [
        [-L / 2,  L / 2, 0.0],
        [ L / 2,  L / 2, 0.0],
        [ L / 2, -L / 2, 0.0],
        [-L / 2, -L / 2, 0.0],
    ],
    dtype=np.float64,
)
```

该顺序与 OpenCV `SOLVEPNP_IPPE_SQUARE` 要求的角点顺序一致。

当前实物标记黑色外框边长为：

```text
40 mm = 0.04 m
```

因此实时跟踪参数应使用：

```text
--marker-length 0.04
```

## 5. 实时手掌位姿程序

新增文件：

- `code/aruco_palm_pose.py`

主要功能：

- 通过 `cv2.VideoCapture` 实时读取摄像头；
- 支持 ArUco 和 OpenCV 内置的 AprilTag 字典；
- 检测标记四个二维角点；
- 使用 `SOLVEPNP_IPPE_SQUARE` 求解平面方形标记的候选位姿；
- 根据正深度和重投影误差选择更合理的候选解；
- 绘制三维坐标轴；
- 显示 `X/Y/Z`、`Roll/Pitch/Yaw`、重投影误差和 FPS；
- 支持 JSON、NPZ 和 OpenCV YAML/XML 相机标定文件；
- 支持固定的标记到手掌变换 `T_marker_palm`；
- 支持自动扫描 OpenCV 内置标记字典；
- 支持自动选择检测到的第一个标记 ID；
- 增加“未检测到标记”“ID 不匹配”“PnP 求解失败”等诊断提示。

位姿关系：

```text
T_camera_palm = T_camera_marker @ T_marker_palm
```

在尚未提供 `T_marker_palm` 时，程序暂时认为标记坐标系就是手掌坐标系。

普通固定字典运行示例：

```bat
C:\Python313\python.exe aruco_palm_pose.py --calibration camera_calibration.json --marker-length 0.04 --marker-id 0 --dictionary DICT_4X4_50 --camera 0 --width 1280 --height 720
```

自动字典与自动 ID 诊断示例：

```bat
C:\Python313\python.exe aruco_palm_pose.py --calibration camera_calibration.json --marker-length 0.04 --marker-id -1 --dictionary AUTO --camera 0 --width 1280 --height 720
```

运行时：

- 按 `Q` 或 `Esc` 退出；
- 自动扫描锁定错误字典时，按 `R` 重新扫描。

## 6. 相机标定程序

新增文件：

- `code/calibrate_camera.py`

该程序使用普通棋盘格完成单目相机标定，并生成 `aruco_palm_pose.py` 可以直接读取的 `camera_calibration.json`。

棋盘格规格：

- 外部方格数：`10 × 7`；
- OpenCV 内部角点数：`9 × 6`；
- 显示器上实测单个方格边长：`24 mm = 0.024 m`。

运行命令：

```bat
C:\Python313\python.exe calibrate_camera.py --columns 9 --rows 6 --square-size 0.024 --camera 0 --width 1280 --height 720 --min-samples 20 --output camera_calibration.json
```

窗口操作：

- `Space`：采集当前检测到的棋盘格角点；
- `D`：删除上一组采样；
- `C`：完成标定并保存 JSON；
- `Q` 或 `Esc`：退出且不保存。

采集建议：

- 采集 20～30 组不同视角；
- 覆盖画面中心、四角和边缘；
- 包含不同距离和上下左右倾角；
- 每次移动后等待图像清晰再采集；
- 不要连续采集大量几乎相同的画面；
- 标定分辨率必须与实际跟踪分辨率一致；
- 标定后不要改变焦距、对焦状态或镜头安装位置。

标定误差参考：

- `< 0.5 px`：较好；
- `0.5～1.0 px`：通常可用；
- `> 1.5 px`：建议重新采集。

## 7. 生成的棋盘格资源

新增文件：

- `image/checkerboard_10x7_1920x1080.png`
- `image/checkerboard_10x7_1920x1080.svg`

参数：

- 图像分辨率：`1920 × 1080`；
- 棋盘方格数：`10 × 7`；
- 内部角点数：`9 × 6`；
- 每个方格：`120 × 120 px`；
- 黑白均为精确几何绘制，不使用生成式图像，避免边缘变形。

在显示器上使用时，必须测量实际显示出来的物理方格尺寸。本次实测结果为 24 mm，因此标定输入为 `0.024 m`。

## 8. 当前立体标记识别问题

现象：

```text
NO MARKER DETECTED
```

已排查结论：

- 摄像头和标定文件能够进入实时程序；
- 问题主要集中在标记字典、标记边框或标记类型不匹配；
- 当前程序已经支持自动扫描 OpenCV 公开的预定义 ArUco/AprilTag 字典；
- 如果自动扫描仍无法识别，说明该图案可能不是 OpenCV 支持的标准 ArUco/AprilTag。

从照片外观初步推测，当前立体件上的图案可能属于以下情况之一：

1. 自定义 ArUco 字典；
2. TopoTag 或其他拓扑式人工标记；
3. 由专用软件生成的私有标记库；
4. 标记外边框不满足 OpenCV 方形标记检测要求。

仅凭照片无法可靠判断具体字典。需要进一步获得：

- 标记来源或产品名称；
- 标记生成软件；
- 字典名称；
- 三个面的标记 ID；
- 立体支架 STL、STEP、CAD 或精确尺寸。

## 9. 调研的人工视觉标记系统

这类标记统称为：

```text
Fiducial Marker
```

常见系统包括：

| 系统 | 特点 | 当前 OpenCV 程序支持情况 |
| --- | --- | --- |
| ArUco | OpenCV 集成方便，使用 Dictionary 管理图案 | 支持 |
| AprilTag | 机器人视觉中常用，检测稳健 | 支持 OpenCV 内置字典 |
| STag | 强调位姿稳定性 | 需要单独检测库 |
| TopoTag | 使用拓扑结构，可采用不规则内部形状 | 需要专用检测库 |
| ARTag/ARToolKit | 较早的方形人工标记系统 | 需要对应库 |
| DeepTag | 使用神经网络统一检测多种标记家族 | 需要 PyTorch 和模型 |

在 ArUco 中，“标记符号库”称为 Dictionary，例如：

- `DICT_4X4_50`；
- `DICT_5X5_100`；
- `DICT_6X6_250`；
- `DICT_APRILTAG_36h11`；
- `DICT_ARUCO_ORIGINAL`。

标记实物、检测器和 Dictionary 必须完全匹配。

## 10. 当前建议方案

### 方案 A：继续确认现有立体标记

适用于能够获得标记来源、字典文件或产品说明的情况。

需要完成：

1. 确认标记类型和字典；
2. 安装对应检测库；
3. 确认三个面的 ID；
4. 根据 CAD 建立三个面的统一三维角点；
5. 融合多个面的角点进行立体 PnP；
6. 输出统一的 `T_camera_rig`。

### 方案 B：替换为标准 ArUco 标记

这是当前最容易落地的方案。

建议：

- 使用 `DICT_4X4_50`；
- 分别生成 ID `0、1、2`；
- 将三个标记打印为 40 mm；
- 分别粘贴到立体架的三个面；
- 记录三个面相对立体架坐标系的准确变换；
- 先完成单面跟踪，再实现多面融合。

## 11. 下一步工作

1. 确认 `camera_calibration.json` 已生成，并记录 Calibration RMS；
2. 使用自动模式确认现有标记能否匹配 OpenCV 内置字典；
3. 如果无法匹配，确认其是否为 TopoTag 或私有标记；
4. 必要时生成标准 ArUco ID `0、1、2` 替换现有图案；
5. 验证静止标记的 `z` 是否接近实际测量距离；
6. 观察重投影误差是否稳定在约 `1～2 px` 以内；
7. 测量标记坐标系到手掌中心的固定外参 `T_marker_palm`；
8. 增加位姿滤波、丢失检测和异常跳变门限；
9. 将位姿通过 JSON、串口、UDP、ROS 或其他接口发送给遥操作系统；
10. 获得立体架精确几何后，实现三个标记面的统一位姿融合。

## 12. 今日新增文件汇总

```text
code/pnp_algorithm_test.py
code/aruco_palm_pose.py
code/calibrate_camera.py
image/checkerboard_10x7_1920x1080.png
image/checkerboard_10x7_1920x1080.svg
学习记录/2026-08-19_PnP与视觉标记跟踪开发总结.md
```

## 13. 阶段性结论

PnP 数值求解部分已经通过合成数据验证，RANSAC 能够正确排除异常点，并恢复高精度位姿。相机标定、实时摄像头读取、方形标记 PnP 和可视化代码框架已经建立。

当前主要阻塞点不是 PnP 算法，而是现有立体标记的类型和 Dictionary 尚未确认。下一阶段应优先确认标记来源；如果无法获得对应检测库和三维模型，建议更换为标准 ArUco 标记，以便快速完成手掌实时位姿闭环。
