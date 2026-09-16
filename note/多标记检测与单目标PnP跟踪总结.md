# 多标记检测与单目标 PnP 跟踪总结

> 日期：2026-08-20  
> 项目：Glovity 手部视觉位姿跟踪  
> 核心目标：检测并框出画面中的全部标记，但只对一个目标标记进行 PnP 位姿解算

## 1. 今日需求调整

今天对实时标记识别程序的目标选择逻辑进行了重新梳理。

最终需求不是融合三个标记，也不是计算三个标记中心、坐标轴延长线交点或平均姿态，而是：

1. 检测画面中所有符合字典的标记；
2. 给所有检测成功的标记绘制绿色外框和 ID；
3. 只选择一个标记作为手掌位姿目标；
4. 只对目标标记执行 PnP；
5. 只在目标标记上绘制三维坐标轴并显示位置、姿态和重投影误差；
6. 不计算其他标记的三维位姿，也不做多标记融合。

最终处理流程为：

```text
摄像头图像
    ↓
检测所有标记的二维角点和 ID
    ↓
给所有标记绘制外框与 ID
    ↓
根据 --marker-id 选择唯一目标
    ↓
只取目标标记的四个二维角点
    ↓
PnP 解算目标标记位姿
    ↓
绘制目标坐标轴并显示位姿数据
```

## 2. 两种目标选择方式

### 2.1 指定目标 ID

运行时传入非负 ID，例如：

```text
--marker-id 27
```

程序会继续检测并框出其他标记，但只有 ID 27 会进入 PnP 解算。

如果当前画面没有 ID 27，程序会显示已经检测到的 ID，并继续等待 ID 27 出现，不会自动切换到其他标记。

### 2.2 自动锁定第一个标记

使用：

```text
--marker-id -1
```

或者完全省略 `--marker-id`，因为当前默认值就是 `-1`。

程序会把首次检测结果中的第一个 ID 锁定为目标。锁定以后，即使画面中出现其他标记，也不会自动切换目标，从而避免立体标记不同面之间发生坐标系跳变。

按键说明：

- `R`：释放自动目标，并重新锁定下一个首次检测到的标记；
- `Q` 或 `Esc`：退出实时程序。

## 3. 最终运行命令

### 3.1 只计算 ID 27 的位姿

在项目根目录运行：

```powershell
C:\Python313\python.exe .\code\01_realtime_pose\aruco_palm_pose.py --calibration .\code\02_camera_calibration\camera_calibration.json --marker-length 0.04 --marker-id 27 --dictionary 99 --polarity both --camera 0 --width 1280 --height 720
```

### 3.2 自动锁定首次检测到的标记

```powershell
C:\Python313\python.exe .\code\01_realtime_pose\aruco_palm_pose.py --calibration .\code\02_camera_calibration\camera_calibration.json --marker-length 0.04 --marker-id -1 --dictionary 99 --polarity both --camera 0 --width 1280 --height 720
```

## 4. 代码中的关键逻辑

实时程序位于：

```text
code/01_realtime_pose/aruco_palm_pose.py
```

检测封装仍然会返回全部标记：

```python
corners, ids, rejected, detected_polarity = detect_markers(
    frame,
    detector_bundle,
    args.polarity,
)
```

全部检测结果统一绘制：

```python
cv2.aruco.drawDetectedMarkers(frame, corners, ids)
```

随后根据目标 ID 找到唯一的角点索引：

```python
target_indices = [
    index
    for index, detected_id in enumerate(detected_ids)
    if detected_id == active_target_id
]
```

只有目标标记的角点会传入 PnP：

```python
target_corners = corners[target_index]
marker_pose = solve_square_marker_pose(
    target_corners,
    object_points,
    calibration,
)
```

因此需要区分两个概念：

- **检测**：检测器会检测全部标记，用于显示外框和 ID；
- **位姿解算**：只对目标标记执行，其他标记不会参与 PnP。

## 5. PnP 使用的数据

目标标记的四个已知三维角点由真实边长生成。当前标记边长为 40 mm，因此运行参数使用：

```text
--marker-length 0.04
```

标记四个三维角点与图像中检测到的四个二维角点建立对应关系，再结合相机标定数据：

```text
camera_calibration.json
```

即可求得：

```text
T_camera_marker
```

也就是目标标记坐标系相对于相机坐标系的位置和姿态。

## 6. 画面显示含义

所有检测成功的标记都会显示：

- 绿色四边形：检测到的标记边界；
- ID 文本：字典解码得到的标记编号。

只有目标标记额外显示：

- 红色轴：X 轴；
- 绿色轴：Y 轴；
- 蓝色轴：Z 轴；
- `t [m]`：目标相对于相机的位置，单位为米；
- `RPY [deg]`：滚转角、俯仰角和偏航角，单位为度；
- `reprojection`：重投影均方根误差，单位为像素；
- `FPS`：实时处理帧率。

## 7. 已移除的多标记逻辑

本次修改已经移除下列位姿计算逻辑：

- 三个标记中心点求平均；
- 多个旋转矩阵求平均；
- 多个 Y 轴延长线求交点；
- 使用 27 号标记方向构造新的中心坐标系；
- 等待三个指定 ID 同时出现；
- 多标记中心位姿显示。

旧命令中的以下参数不再使用：

```text
--center-marker-ids
--center-orientation-marker-id
--min-center-markers
```

## 8. 当前方案的优势与限制

优势：

- 逻辑简单，目标位姿含义明确；
- 计算量较小，只执行一次 PnP；
- 固定 ID 后不会在立体标记的不同面之间跳变；
- 仍可通过全部外框观察其他标记是否检测成功。

限制：

- 当目标面被遮挡时，即使其他面可见，也不会使用其他面继续输出位姿；
- 不同标记面之间没有建立统一的刚体坐标转换；
- 当前得到的是目标标记位姿。如果标记中心与真实手掌中心不重合，还需要通过 `--palm-offset` 配置固定变换。

## 9. 今日验证结果

完成修改后进行了静态检查：

```text
Syntax check: PASS
Legacy multi-marker pose code: NONE
```

最终状态可以概括为：

```text
全部检测、全部框选、单个目标、单次 PnP、单个位姿输出
```
