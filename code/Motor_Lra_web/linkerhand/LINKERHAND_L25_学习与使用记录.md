# LinkerHand L25 学习与使用记录

> 适用设备：LinkerHand L25 左手、蓝色 PCAN-USB、Windows。  
> 项目目录：`D:\Desktop\github\glovity\code\Motor_Lra_web`  
> 当前功能：ROS 2 驱动、五指触觉显示、12 × 6 热力图、25 关节控制、姿态预设、软件基准标定、触觉到 LRA 振动联动。

## 1. 项目目标

本项目把 LinkerHand L25 的运动和触觉数据接入现有 Web 平台：

1. 通过 PCAN-USB 在 Windows 上连接机械手。
2. 使用 ROS 2 驱动读取关节、力和指尖压力矩阵。
3. 使用 Python 桥接程序把 ROS 2 数据转换成浏览器可用的 HTTP/SSE 数据。
4. 在网页中显示五指触觉、12 × 6 热力图和压力变化曲线。
5. 把触觉峰值换算为实验压力，并映射为 LRA 振动强度。
6. 在网页中调节 L25 的 25 个关节，保存预设姿态和软件标定基准。

数据链路如下：

```text
LinkerHand L25
    │ CAN
PCAN-USB
    │ python-can / PCAN 驱动
ROS 2 LinkerHand 驱动
    │ ROS 2 topics
linkerhand_bridge.py
    │ HTTP + Server-Sent Events
浏览器 Web 控制台
    │ USB 串口
Arduino → JW-BLE-DL500 → LRA 振动马达
```

## 2. 学习信息

### 2.1 ROS 2 中的节点和话题

本项目主要有两个 ROS 2 节点：

| 节点 | 用途 |
|---|---|
| `/linker_hand_sdk` | 与 PCAN 和机械手通信，发布状态与触觉数据，接收关节控制命令 |
| `/linkerhand_web_bridge` | 订阅机械手数据，将数据提供给网页，并转发网页关节命令 |

关键话题：

| 话题 | 消息类型 | 说明 |
|---|---|---|
| `/cb_left_hand_state` | `sensor_msgs/msg/JointState` | L25 的 25 个关节当前位置 |
| `/cb_left_hand_control_cmd` | `sensor_msgs/msg/JointState` | 发送 25 个关节目标值，范围 0–255 |
| `/cb_left_hand_force` | `std_msgs/msg/Float32MultiArray` | 法向力、切向力、切向方向和接近量，每类 5 个值 |
| `/cb_left_hand_matrix_touch` | `std_msgs/msg/String` | 五指压力矩阵，内容为 JSON |
| `/cb_left_hand_matrix_touch_mass` | `std_msgs/msg/String` | 每根手指矩阵数据的合值 |
| `/cb_left_hand_info` | `std_msgs/msg/String` | 型号、速度、电流、故障、温度等信息 |

`/cb_left_hand_force` 的 20 个元素依次为：

```text
0–4    五指法向力
5–9    五指切向力
10–14  五指切向力方向；无方向时通常为 255
15–19  五指接近量
```

### 2.2 触觉矩阵与热力图

网页按每根手指的 `12 × 6` 矩阵显示触觉分布，共 72 个采样单元。颜色规则为：

- 蓝色：低压力。
- 紫色/红色：中等压力。
- 黄色：最高压力。

网页从矩阵中计算峰值、合值、接触单元数量和压力中心。当前实验换算关系为：

```text
压力（N）≈ min(15, 原始点数 / 20 + 8 × 原始点数 × (原始点数 - 10) / 18200)
```

因此峰值 10 点约为 0.5 N，140 点约为 15 N，超过 15 N 时显示封顶。这是根据当前设备高端约 140 点的观察暂定的换算曲线，不是厂商的正式标定；取得标准砝码测量结果后应替换为实测拟合公式。

### 2.3 触觉到振动的映射

网页可以把实机触觉映射到 LRA 振动：

1. 矩阵峰值换算为 N。
2. 低于 `0.4 N` 时进入死区，避免零点噪声引起持续振动。
3. 死区以上使用感知曲线计算振幅。
4. 最终振幅受网页“最大 AMP”限制。
5. 压力矩阵的中心位置用于判断接触方向，配合不同振动模式表达触感。

### 2.4 L25 关节控制

L25 使用 25 个 `0–255` 位置值。网页滑杆改变的是目标值，只有点击“发送姿态”才会发布，避免拖动滑杆时机械手连续突跳。

使用时应遵守以下原则：

- 第一次调试从当前姿态附近开始，每次只改变较小数值。
- 先单独测试一个关节，确认正负方向和安全范围。
- 手指接触刚性物体后不要继续大幅收紧。
- 使用预设前先检查全部 25 个目标值。
- 网页标定属于软件基准，不会擦写机械手内部零位。

## 3. 首次环境配置

当前项目已经配置完成，通常无需重复安装。关键配置文件为：

```text
linkerhand/ros2/setup-ros.bat
linkerhand/ros2/linkerhand-ros2-sdk/linker_hand_ros2_sdk/launch/linker_hand.launch.py
```

当前机械手参数：

```python
hand_type = "left"
hand_joint = "L25"
is_touch = True
can = "PCAN_USBBUS1"
modbus = "None"
```

Windows 上使用 Fast DDS。项目统一设置 `rmw_fastrtps_cpp`，用于避开代理网卡、VMware 虚拟网卡对 Cyclone DDS 本机发现造成的影响。

如源码有修改，在 `linkerhand/ros2` 目录重新构建：

```bat
call setup-ros.bat
.pixi\envs\default\Scripts\colcon.exe build --merge-install --packages-select linker_hand_ros2_sdk
```

## 4. 每次使用步骤

### 4.1 接线和上电

1. 给机械手接独立电源。
2. 连接机械手 CAN 线和蓝色 PCAN-USB。
3. 将 PCAN-USB 插入电脑。
4. 确认 PCAN 指示灯正常闪烁。
5. 不要同时打开两个 LinkerHand 驱动；一个 PCAN 通道同一时间只应由一个驱动占用。

### 4.2 启动驱动

双击：

```text
linkerhand\ros2\start-driver.bat
```

正常日志应包含：

```text
成功连接: interface='pcan', channel='PCAN_USBBUS1'
left L25 Equipped with matrix pressure sensing
SDK V1
```

保持这个窗口打开。

### 4.3 启动 Web 桥

驱动连接成功后，双击项目根目录：

```text
web\start-linkerhand-web.bat
```

浏览器地址：

```text
http://127.0.0.1:8765/
```

页面显示绿色“ROS2 数据流正常”后，可以按压五个指尖观察热力图和力数据。

### 4.4 使用关节控制

1. 在页面底部找到“L25 运动控制”。
2. 点击“读取当前位置”，确认已经收到 25 个实际位置值。
3. 小幅调整目标关节滑杆。
4. 点击“发送姿态”。
5. 确认运动方向后再继续调节。
6. 可载入“张开、握拳、OK”等内置预设。
7. 输入名称可把当前目标保存为本机浏览器预设。

### 4.5 软件基准标定

1. 让机械手处于希望作为初始状态的自然姿态。
2. 点击“读取当前位置”。
3. 确认页面已有有效的 25 个关节值。
4. 点击“用当前位置标定”。
5. 以后点击“载入标定基准”，再点击“发送姿态”即可恢复该目标。

该标定保存在浏览器本地，仅用于网页控制，不是硬件零位标定。

### 4.6 正确关闭

先关闭 Web 窗口和驱动窗口，或双击：

```text
linkerhand\ros2\stop-linkerhand.bat
```

确认驱动停止后再拔出 PCAN-USB 或关闭机械手电源。

## 5. 检查与诊断命令

先打开：

```text
linkerhand\ros2\open-ros-shell.bat
```

然后执行以下命令。

查看 CAN 适配器：

```bat
check-can.bat
```

查看节点：

```bat
ros2 node list
```

正常应看到：

```text
/linker_hand_sdk
/linkerhand_web_bridge
```

查看全部话题：

```bat
ros2 topic list -t
```

查看关节状态：

```bat
ros2 topic echo /cb_left_hand_state
```

查看指尖矩阵：

```bat
ros2 topic echo /cb_left_hand_matrix_touch
```

查看发布者和订阅者：

```bat
ros2 topic info /cb_left_hand_matrix_touch -v
```

查看发布频率：

```bat
ros2 topic hz /cb_left_hand_matrix_touch
```

检查 Web 桥状态：

```text
http://127.0.0.1:8765/api/linkerhand/state
```

其中 `connected` 应为 `true`，`sequence` 应持续增长。

## 6. 常见问题

### 6.1 `PCAN Channel has not been initialized`

原因通常是旧驱动仍占用 PCAN 通道。

处理步骤：

1. 双击 `linkerhand\ros2\stop-linkerhand.bat`。
2. 关闭遗留的驱动、GUI 和 ROS 终端。
3. 拔出 PCAN-USB，等待 5 秒后重新插入。
4. 重新启动驱动。

不要同时启动官方 GUI 驱动和 Web 项目的驱动。

### 6.2 页面显示“桥接已启动 · 等待 ROS2”

这表示网页服务器已经运行，但没有持续收到 ROS 2 消息。依次检查：

1. 驱动窗口是否仍在运行。
2. `ros2 node list` 是否同时显示两个节点。
3. `ros2 topic info ... -v` 是否同时存在 publisher 和 subscription。
4. Web 状态中的 `sequence` 是否增长。

本项目曾出现 SDK 的 `get_state()` 暂时返回 `None`，发布线程报错：

```text
TypeError: 'NoneType' object is not iterable
```

当前代码已经加入空值保护，并为 L25 建立正确的 25 关节占位数组，避免发布线程因此永久退出。

### 6.3 节点存在，但所有值都是 `-1`

这表示 ROS 2 连接正常，但机械手没有返回有效 CAN 数据。处理方法：

1. 停止全部 LinkerHand 进程。
2. 断开机械手电源和 PCAN-USB。
3. 等待 5 秒。
4. 先恢复机械手电源，再连接 PCAN-USB。
5. 重新启动驱动。

如果出现 `Hardware version number not recognized`，也应执行上述断电和拔插流程。

### 6.4 ROS 2 节点彼此看不见

这台 Windows 电脑存在代理和 VMware 虚拟网卡，Cyclone DDS 曾选择错误接口。项目已经改用 Fast DDS。所有终端和批处理必须通过同一个 `setup-ros.bat` 初始化，不能在一个进程使用 Cyclone DDS、另一个进程使用 Fast DDS。

### 6.5 GUI 启动时报 `No module named 'gui_control.config'`

这是安装目录未包含 GUI 配置包导致的构建/打包问题，与 PCAN 硬件连接无关。当前主要使用 Web 界面；如需官方 GUI，应重新构建 GUI 包并确认 `gui_control/config` 被安装。

### 6.6 热力图在线但按压没有变化

检查顺序：

1. `/cb_left_hand_matrix_touch` 是否持续发布。
2. 矩阵是否全为 `-1`；全为 `-1` 表示无有效硬件数据。
3. 矩阵是否为零但按压不变；检查机械手触觉版本和固件协议。
4. 页面是否选择了“实机数据”。
5. 按 `Ctrl+F5` 清除浏览器旧资源缓存。

## 7. 本次学习经历

### 阶段一：识别硬件和通信接口

开始时把 USB-CAN 当作 Candle 设备检测，程序提示 `No Candle adapter detected`。继续检查后发现 Windows 已把设备识别为 `PCAN-USB`，通道为 `PCAN_USBBUS1`。因此将驱动配置从 Candle 通道改成 PCAN 接口。

这一阶段得到的经验是：USB-CAN 外壳颜色只能作为线索，最终要以系统驱动和 `python-can` 枚举结果为准。

### 阶段二：确认真实机械手型号

最初按 L20 配置，但过去使用该机械手时一直选择 L25。切换到 L25 后，驱动能够正确使用 25 关节协议和矩阵触觉路径。

这一阶段说明：外形相似不代表通信协议相同。型号会影响关节数量、CAN 数据格式、触觉协议和控制命令长度。

### 阶段三：修复 GUI 与 ROS 2 环境

官方 GUI 曾因缺少 `gui_control.config` 退出。随后改为使用 Web 界面，并在项目内建立隔离的 ROS 2 Jazzy/Pixi 环境。Windows 的符号链接警告只影响日志目录的 `latest` 链接，不影响驱动本身。

### 阶段四：处理 ROS 2 发现问题

驱动和桥接进程都启动后，曾出现节点互相不可见。排查发现电脑同时存在代理、VMware 和 WLAN 网络接口，Cyclone DDS 在本机 UDP 发现时失败。统一切换到 Fast DDS 后，能够同时发现 `/linker_hand_sdk` 和 `/linkerhand_web_bridge`。

这一阶段得到的经验是：进程存在不等于 ROS 2 已经连通；必须同时检查节点、话题端点和消息是否持续更新。

### 阶段五：定位“节点存在但页面等待”的故障

页面曾显示“桥接已启动 · 等待 ROS2”。节点和话题都存在，但 Web 状态的 `sequence` 停止增长。捕获控制台后发现，机械手状态偶尔返回 `None`，SDK 的后台发布线程因此崩溃，而主节点仍留在 ROS 图中。

修复内容包括：

- 仅在机械手返回有效数组时更新关节缓存。
- L25 使用 25 个元素的初始状态和速度数组。
- 发布时对空状态再次进行保护。
- 重新构建 ROS 2 包并验证 `sequence` 持续增长。

这一阶段最重要的经验是：判断系统健康不能只看 `ros2 node list`，还要监测消息频率、时间戳或递增序号。

### 阶段六：触觉数据接入 Web 和振动反馈

在 Web 桥中订阅法向力、切向力、方向、接近量和五指矩阵；网页加入 12 × 6 热力图，设置蓝色最低、黄色最高的视觉映射，并把矩阵峰值按 `100 → 10 N` 的实验关系换算。随后加入压力死区和感知映射，将压力转换为合适的 LRA 振幅。

### 阶段七：加入关节控制、预设与标定

最后把 25 个关节控制集成到网页。为了降低误动作风险，滑杆只编辑目标，点击后才发送；预设保存在浏览器本地。由于当前 SDK 没有 L25 硬件零位标定接口，采用读取实际位置并保存的软件基准方式。

## 8. 后续学习方向

1. 使用标准砝码逐点采样，得到原始峰值到 N 的正式标定曲线。
2. 分别标定五根手指，比较传感器灵敏度差异。
3. 记录加载与卸载曲线，评估迟滞、漂移和重复性。
4. 研究法向力、切向力和矩阵压力中心之间的关系。
5. 为 25 个关节建立机械角度与 `0–255` 数值的对应表。
6. 为关节控制增加限位、最大步长和紧急停止机制。
7. 记录 ROS 2 数据到 rosbag，用于离线分析和算法训练。
8. 根据不同材质和接触事件设计更准确的触觉到振动编码策略。

## 9. 相关文件

| 文件 | 作用 |
|---|---|
| `README.md` | 项目总体说明和 LRA 使用方法 |
| `linkerhand/ros2/setup-ros.bat` | Windows ROS 2 环境和 DDS 设置 |
| `linkerhand/ros2/start-driver.bat` | 启动 LinkerHand 驱动 |
| `web/start-linkerhand-web.bat` | 启动 ROS 2-Web 桥和网页 |
| `linkerhand/ros2/stop-linkerhand.bat` | 关闭本项目的 ROS 2/PCAN 相关进程 |
| `web/linkerhand_bridge.py` | ROS 2 与 Web 之间的数据桥 |
| `web/index.html` | Web 页面结构 |
| `web/app.js` | 触觉显示、压力计算、振动联动和关节控制逻辑 |
| `web/style.css` | 页面和热力图样式 |
| `linkerhand/ros2/linkerhand-ros2-sdk/doc/Topic-Reference.md` | 厂商 ROS 2 话题参考 |

---

最后更新：2026-09-24
