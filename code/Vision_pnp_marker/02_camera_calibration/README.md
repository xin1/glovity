# 02 Camera Calibration

这个目录放相机标定程序和标定结果。PnP 要算真实 3D 位姿，必须知道摄像头的内参和镜头畸变，所以这里的 `camera_calibration.json` 是实时位姿程序的重要输入。

## 文件

- `calibrate_camera.py`：相机标定脚本，用棋盘格采集样本并计算内参。
- `camera_calibration.json`：当前摄像头的标定结果，供 `aruco_palm_pose.py` 使用。

## 什么时候需要重新标定

- 换了摄像头。
- 换了分辨率，例如从 `1280x720` 改成 `1920x1080`。
- 调整了镜头焦距、变焦或明显改变对焦。
- 位姿距离明显不准。
- 重投影误差长期偏大。

## 标定板

当前使用的棋盘格图片位于：

```text
../../image/calibration_boards/checkerboard_10x7_1920x1080.png
```

棋盘格是 `10x7` 个方格，所以 OpenCV 检测的是内部角点：

```text
columns = 9
rows = 6
```

如果在显示器上投放，实测每个小方格边长是 24 mm，则：

```text
square-size = 0.024
```

单位是米。

## 推荐运行命令

从项目根目录运行：

```powershell
cd D:\Desktop\github\glovity\code
C:\Python313\python.exe .\Vision_pnp_marker\02_camera_calibration\calibrate_camera.py --columns 9 --rows 6 --square-size 0.024 --camera 0 --width 1280 --height 720 --min-samples 20 --output .\Vision_pnp_marker\02_camera_calibration\camera_calibration.json
```

## 采集操作

程序打开摄像头后，把棋盘格放在画面中。检测到角点后可以采集样本。

按键：

- `Space`：采集当前样本。
- `D`：删除上一张样本。
- `C`：样本数量足够后开始计算标定结果。
- `Q` 或 `Esc`：退出。

## 样本采集建议

建议至少采集 20 张以上，并让棋盘格覆盖不同位置和角度：

- 中心位置。
- 左上、右上、左下、右下。
- 近距离和远距离。
- 正对摄像头。
- 左右倾斜。
- 上下倾斜。

采集时尽量避免：

- 棋盘格反光。
- 画面模糊。
- 棋盘格严重弯曲。
- 只在画面中心采样。

## 输出结果

标定完成后会生成或覆盖：

```text
camera_calibration.json
```

它里面主要包含：

- `camera_matrix`：相机内参矩阵。
- `distortion_coefficients`：畸变参数。
- `image_width` / `image_height`：标定时分辨率。
- `rms`：整体标定误差。
- `per_view_errors`：每张样本的误差。

## 使用注意

- 实时识别时的分辨率最好和标定时一致。
- 同一个摄像头在不同分辨率下，内参会变化。
- 显示器投放棋盘格时，要用实测方格边长，而不是图片理论尺寸。
- 如果后续改用打印棋盘格，要重新测量实际方格边长。

