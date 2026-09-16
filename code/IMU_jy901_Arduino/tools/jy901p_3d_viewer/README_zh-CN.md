# JY901P 实时显示器

## Matplotlib纯姿态展示画面（推荐）

双击 `run_attitude_viewer.bat` 启动。界面使用Matplotlib绘制，只展示姿态，不显示磁力计点云：

- 左侧Matplotlib三维坐标轴和大型IMU模型。
- 右上人工地平仪。
- 右下Matplotlib极坐标航向罗盘。
- Roll、Pitch、Yaw大号数字。
- 按 `F11` 进入全屏，按 `Esc` 退出全屏。
- “开始磁校准 m”按钮只发送`m`，Arduino调用维特官方`WitStartMagCali()`。
- “结束磁校准 e”按钮只发送`e`，Arduino调用维特官方`WitStopMagCali()`。

默认串口为 `COM6`，程序启动后会自动尝试连接。运行前请关闭Arduino IDE串口监视器。

程序文件为 `jy901p_attitude_viewer.py`。也可以先运行演示模式检查画面：

```powershell
python jy901p_attitude_viewer.py --demo
```

## 姿态与磁力计组合画面

该工具读取 `jy901p_new_firmware_validation.ino` 输出的 `LIVE,...` 数据：

- 左侧实时显示 Roll、Pitch、Yaw 驱动的三维 IMU 姿态。
- 右侧显示磁力计三维点云、三轴跨度、中心和覆盖均衡度。
- 界面可直接发送开始磁校准 `m`、结束磁校准 `e`、重新设置100 Hz `r`。

## 首次安装

打开 PowerShell，执行：

```powershell
cd D:\Desktop\github\glovity\code\IMU_jy901_Arduino\tools\jy901p_3d_viewer
python -m pip install -r requirements.txt
```

## 启动

1. 烧录并运行 `jy901p_new_firmware_validation.ino`。
2. 关闭 Arduino IDE 的串口监视器。同一个 COM 口不能同时被两个程序占用。
3. 双击 `run_viewer.bat`，或者执行：

```powershell
python jy901p_3d_viewer.py
```

4. 在窗口顶部选择 Arduino 的 COM 口，点击“连接”。
5. 转动 IMU，检查左侧模型是否与实物同向、同步运动。

## 磁力计校准可视化

1. 点击“开始磁校准 m”，程序会清空旧点云并向 Arduino 发送 `m`。
2. 缓慢绕三个轴转动 IMU。
3. 右侧点云应逐渐覆盖多个方向，而不是只有一条线或一个平面。
4. `balance` 越接近100%，三轴跨度越均衡。低于约70%通常表示某个轴覆盖不足。
5. 完成后点击“结束磁校准 e”。

点云会按当前三轴最小值和最大值自动居中、等比例缩放。因此它适合观察方向覆盖和形状，但屏幕上的球体大小不代表磁场绝对强度。

## 常见问题

- 连接失败：关闭Arduino串口监视器、串口绘图器以及其他占用COM口的软件。
- 没有姿态显示：确认Arduino程序输出行以`LIVE,ms=`开头，波特率为115200。
- 姿态旋转方向相反：先确认JY901P的XYZ轴定义和安装方向；显示器采用`Rz(Yaw) * Ry(Pitch) * Rx(Roll)`旋转顺序。
- 点云只有一个点：确认勾选“记录磁场轨迹”，并在转动时检查`mag_raw`是否变化。
