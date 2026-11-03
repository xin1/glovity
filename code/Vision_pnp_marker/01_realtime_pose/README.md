# 01 Realtime Pose

这个目录放实时视觉位姿程序。它是当前项目里真正连接摄像头、识别标记、输出 3D 位姿的核心部分。

## 文件

- `aruco_palm_pose.py`：实时检测 Find-GCP / ArUco / AprilTag 类标记，并使用 PnP 计算标记相对摄像头的位姿。

## 它做了什么

程序运行后会执行下面这条流程：

1. 打开摄像头。
2. 读取 `camera_calibration.json` 中的相机内参和畸变参数。
3. 从视频画面中检测标记。
4. 获取标记四个角点的 2D 像素坐标。
5. 根据 `--marker-length` 生成标记四个角点的真实 3D 坐标。
6. 使用 OpenCV PnP 求解 `T_camera_marker`。
7. 在画面中绘制标记边框、坐标轴、平移量、欧拉角、重投影误差和 FPS。

## 推荐运行命令

从项目根目录运行：

```powershell
cd D:\Desktop\github\glovity\code
C:\Python313\python.exe .\Vision_pnp_marker\01_realtime_pose\aruco_palm_pose.py --calibration .\Vision_pnp_marker\02_camera_calibration\camera_calibration.json --marker-length 0.04 --marker-id 19 --dictionary 99 --polarity both --camera 0 --width 1280 --height 720
```

如果不确定当前标记 ID，可以使用自动锁定模式。程序只处理首次检测到的标记，之后不会跳到其他 ID；按 `R` 可重新选择：

```powershell
cd D:\Desktop\github\glovity\code
C:\Python313\python.exe .\Vision_pnp_marker\01_realtime_pose\aruco_palm_pose.py --calibration .\Vision_pnp_marker\02_camera_calibration\camera_calibration.json --marker-length 0.04 --marker-id -1 --dictionary 99 --polarity both --camera 0 --width 1280 --height 720
```

检测成功后，画面上会显示类似：

```text
Palm marker ID: 19
t [m] x:+0.003 y:+0.016 z:+0.254
RPY [deg] -179.8 +33.8 +5.2
reprojection: 0.03px FPS: 30.0
```

## 参数说明

- `--calibration`：相机标定文件路径。实时位姿必须使用。
- `--marker-length`：标记真实边长，单位是米。40 mm 写 `0.04`。
- `--marker-id`：唯一要追踪的标记 ID。写 `19` 表示只处理 ID 19；默认值 `-1` 表示锁定首次检测到的标记，按 `R` 可重新选择。
- `--dictionary`：标记字典。当前 Find-GCP 自定义 3x3 字典使用 `99`。
- `--polarity`：标记颜色极性。`normal` 只识别常规颜色，`inverted` 只识别黑白反相标记，`both` 会先试常规图，再试反相图。
- `--camera`：摄像头编号。常见默认摄像头为 `0`。
- `--width`：摄像头宽度。
- `--height`：摄像头高度。
- `--palm-offset`：可选。传入 marker 到 palm 的固定变换 JSON，用于把标记坐标系转换为真实手掌坐标系。

## 输出含义

- `t [m]`：标记相对摄像头的位置，单位是米。
- `RPY [deg]`：姿态角，单位是度。
- `reprojection`：重投影误差，越小说明当前角点和求解位姿越匹配。
- `FPS`：实时处理帧率。

坐标轴颜色：

- 红色：X 轴。
- 绿色：Y 轴。
- 蓝色：Z 轴。

相机坐标系一般可以理解为：

- X 向右。
- Y 向下。
- Z 向前，也就是从摄像头指向标记。

## 注意事项

- `camera_calibration.json` 应该来自同一个摄像头、同一个分辨率和相近的对焦状态。
- `--marker-length` 必须填真实边长，不是图片文件里的像素尺寸。
- 如果使用三面立体标记，建议固定 `--marker-id`。自动模式会锁定第一次检测到的那一面，按 `R` 才会重新选择。
- 如果你的标记是最外围黑色、里面一圈白色、内部编码区为黑色，属于反相标记，使用 `--polarity both` 或 `--polarity inverted`。
- 如果要稳定融合三面标记，需要额外建立每个标记面到同一个手掌坐标系的固定 3D 变换。

## 常见问题

### NO MARKER DETECTED

优先检查：

- 是否使用 `--dictionary 99`。
- 反相标记是否使用了 `--polarity both` 或 `--polarity inverted`。
- 标记黑边是否完整。
- 标记是否太小、太远、反光或模糊。
- 摄像头是否对焦。

### 姿态乱跳

优先检查：

- 是否固定了正确的 `--marker-id`。
- `--marker-length` 是否写成 `0.04`。
- 相机标定文件是否来自当前摄像头和当前分辨率。

### drawFrameAxes warning

如果出现：

```text
cv::drawFrameAxes Some of projected axes endpoints are out of frame.
```

通常只是坐标轴端点画到画面外，不一定是 PnP 失败。可以把标记移到画面中心，或者后续调小坐标轴长度。
