# Glovity_NOKOV_测试说明

## 1. 文件说明

1. `Glovity_NOKOV接口与数据输出说明_V0.1.md`，确认四针接口、电平和控制时序。

2.  `Glovity_NOKOV基站固件与帧对接说明_V0.2.md`，确认帧格式、解析、时间戳和验收边界。

3.  `01_Firmware` 左右手和接收器固件

4.  `02_TeleX_SDK` 对应本版本固件的SDK文件

    `02_TeleX_SDK/examples/monitor_recording.py` 检查REC状态、帧序号、时间戳和IMU失败掩码。

​		SDK相关：在本交付包根目录打开PowerShell：		

```python
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\02_TeleX_SDK"
.\.venv\Scripts\python.exe ".\02_TeleX_SDK\examples\monitor_recording.py" --port COM号 --baud 921600
```

​		将 `COM号` 替换为Windows设备管理器中配套接收器的实际端口。COM号可能随电脑和USB口变化，不参与		ESP-NOW无线配对。

5. 出现无数据或坏帧时，使用 `03_Diagnostic_Tools/frame_alignment/serial_probe.py` 进行诊断；接收器队列统计可用 `--receiver-stats` 保存。

## 2. 测试说明

两只手套各以默认 30 Hz 采集，分别通过 ESP-NOW 向配套接收器发送 180 字节 V2 普通帧，接收器经 USB 虚拟串口交付采集主机。每帧只有一只手的数据；`hand_id=0` 为左手，`hand_id=1` 为右手。四针接口只用于控制，不传输 IMU 串行数据。

### 2.1 准备

1. 核对左右手套及接收器的实际 STA MAC。将右手真实 MAC 写入接收器固件 `RIGHT_TX_ADDR`，将接收器真实 MAC 写入右手固件 `RECEIVER_ADDR`；左手也应核对其配对地址。（该手套已烧录配对，无需重复烧录）
2. 分别烧录本包 `01_Firmware` 下的左右手固件及接收器固件；三者使用相同 ESP-NOW 信道 1。右手使用占位 MAC 时，不能视作已完成无线配对。（该手套已烧录配对，无需重复烧录）
3. 按双方最终图纸核对两只手套各自的四针线束，不按线色或插头外观猜测 Pin 1。两只手的 `3V3_REF` 不得并接，也不得向该脚反向供电。

### 2.2 NOKOV 控制接口

| 焊盘脚号 | 信号        | NOKOV 基站操作                          | 手套行为                                 |
| -------: | ----------- | --------------------------------------- | ---------------------------------------- |
|        1 | `GND`       | 与手套共地                              | 控制电平参考                             |
|        2 | `3V3_REF`   | 仅高阻参考／预留                        | 可能随原 IR 电源控制关闭；不作为基站供电 |
|        3 | `OUTPUT_EN` | 释放为高：允许输出；拉低：禁止普通数据  | 低电平时继续采集和计时，但不提交新普通帧 |
|        4 | `REC`       | 主动输出约 3.3 V 为录制；低电平为非录制 | 后续新采集帧的 `recording` 分别为 1／0   |

![接口说明](.\03_Diagnostic_Tools\img\接口说明_1.png)

![接口说明](.\03_Diagnostic_Tools\img\接口说明_2.png)

左右手的 `REC`、`OUTPUT_EN` 均须逐路核对。`REC` 是持续电平，不是脉冲命令；`OUTPUT_EN` 恢复后不会补发停流期间的历史普通帧，帧序号和时间戳可以跳跃。所有控制信号按3.3 V逻辑设计，禁止接入5 V／12 V／24 V。完整电气边界见 `Glovity_NOKOV接口与数据输出说明_V0.1.md`。

### 2.3 数据接收

在交付包根目录打开 PowerShell，安装 SDK 后将 `COM号` 换为接收器实际端口：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\02_TeleX_SDK"
.\.venv\Scripts\python.exe ".\02_TeleX_SDK\examples\monitor_recording.py" --port COM号 --baud 921600
```

接收器同一 USB 串口交错输出两只手的普通帧。上位机应按帧头和 CRC 分帧，再按 `hand_id` 分流；分别保存每只手的 `frame_seq`、`timestamp_us`、`recording`、`imu_fail_mask`、主机单调到达时间和原始 180 字节。接收器的按需统计响应是独立文本，可能出现在普通帧之间，不能把所有串口字节直接按 180 字节切块。

`timestamp_us` 是各手套自己的本地计时，不是 NOKOV 基站时间。左右手时间戳不能直接相减来声明同步精度。严格帧对齐还需双方确认公共事件、时钟映射与验收误差。

### 2.4 左右手全流程验收

1. **配对与识别**：接收器输出 `hand=left` 与 `hand=right` 两路 V2 帧；两路 `hand_id` 与实物一致，各自 `frame_seq` 和 `timestamp_us` 正常推进。若仅有一路，先核对该手 MAC、信道、供电和固件。
2. **非录制与录制**：两只手的 `REC` 均置低，确认两路 `recording=False`；同时置高，确认两路后续新帧变为 `True`；再次置低，确认两路回到 `False`。保留 NOKOV 控制事件时间。
3. **输出门控**：分别及同时拉低两路 `OUTPUT_EN`，确认对应普通帧停止；释放后确认重新输出新帧、不补发停流历史。允许已进入无线／USB 缓冲的尾帧到达。
4. **数据完整性**：分别检查两路 CRC、失败掩码、帧序号缺口、时间戳间隔及接收器队列溢出。停流、重启和启动旧缓存造成的序号跳变须与正常连续运行分开统计。
5. **NOKOV 联调**：共同保存 NOKOV 帧号／时间、控制事件、两路手套原始帧和主机到达时间；以双方确认的时钟映射规则进行匹配，不把 USB 到达顺序当作硬件同步。

需要保存原始接收器字节流并查询队列统计时，在同一个串口会话中运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r ".\03_Diagnostic_Tools\requirements.txt"
$env:PYTHONPATH = ".\03_Diagnostic_Tools"
.\.venv\Scripts\python.exe -m frame_alignment.serial_probe --port COM号 --baud 921600 --duration 600 --receiver-stats --save receiver_dual_30hz.bin
```

长时间抓取由现场人员执行。`$GLOVITY,RX,STATS` 中的溢出数为 0 只表示接收器入队后没有因队列满而丢弃，不能单独证明无线侧无缺帧。详细帧布局、CRC、测试向量和待验收项见 `Glovity_NOKOV基站固件与帧对接说明_V0.2.md`。

### 3.5 测试结果摘要

| 测试项目           | 观察结果                                                     | 结论                                        |
| ------------------ | ------------------------------------------------------------ | ------------------------------------------- |
| 左右手USB直连      | SDK持续解析`v2-180`普通帧                                    | 通过                                        |
| 默认帧率           | 相邻`timestamp_us`约33,333微秒                               | 通过，约30 Hz                               |
| REC高低            | STM32 PA0每5秒切换0 V和约3.3 V，`recording`在False和True间切换 | 通过                                        |
| REC连续性          | 切换期间`frame_seq`和`timestamp_us`持续递增，不清零          | 通过                                        |
| IMU有效性          | 稳定运行阶段`imu_fail_mask=0`                                | 通过                                        |
| 双手无线           | 双手经ESP-NOW到接收器，再由接收器USB进入SDK                  | 通过                                        |
| 无线观察区间       | `frames` 217至666、`seq` 1869至2318，均增加449               | 该观察区间未见丢帧                          |
| 双手持续运行       | 双串口抓取约10分钟；手套USB与接收器正式区间各18001帧、序号连续；重叠17990帧逐字节一致 | 通过；接收器统计收到18001、入队18001、溢出0 |
| OUTPUT_EN门控      | 拉低后停止普通输出，恢复后输出新帧                           | 通过                                        |
| 停发期间内部连续性 | `seq` 333至615，`timestamp_us` 12,351,192至21,751,086        | 内部继续采集和计时，未补发历史              |
| 停发期间跟踪REC    | 停发前`seq=2672, recording=True`；恢复时`seq=2798, recording=False` | 通过                                        |

左手测试结果（SDK显示）

![测试_SDK显示](.\03_Diagnostic_Tools\img\测试_SDK显示.png)

双手测试结果（SDK显示）

![测试_SDK显示](.\03_Diagnostic_Tools\img\测试_双手_SDK显示.png)

**SDK 输出字段说明：**

hand=left/right：左手或右手
protocol=v2-180：当前使用 180 字节 V2 协议
seq：手套本地采集帧序号
timestamp_us：手套本地微秒时间戳
recording=False：REC 为低，非录制状态
recording=True：REC 为高，录制状态
battery：手套上报的电量百分比
fail_mask=0：本帧未报告 IMU 失败

**通过判据：**

1. 左右手均能解析为 protocol=v2-180。
2. 左右手 recording 能够随 REC 电平在 False 和 True 之间切换。
3. OUTPUT_EN 拉低期间，SDK 暂停收到对应手套的普通数据帧。
4. OUTPUT_EN 释放后，SDK 恢复收到新数据。
5. 恢复输出后 seq 和 timestamp_us 跳过停流区间，不补发历史帧。
6. 正常数据帧的 fail_mask 为 0。

**本次实测结果**

左手 recording 已观察到 False -> True -> False -> True 的循环变化。
右手 recording 已观察到 False -> True -> False -> True 的循环变化。

OUTPUT_EN 拉低期间数据停止输出；释放后数据恢复。恢复后的 seq 出现符合预期的
跳号，说明手套在停发期间仍继续采集和计时，但没有向接收器提交普通数据帧。

左右手协议均为 v2-180，实测正常帧 fail_mask=0。

结论：左右手 REC 控制、OUTPUT_EN 门控、独立电气通道、接收器传输及 TeleX SDK
解析均通过本次自动循环联调测试。

**诊断工具安装与运行：**

```powershell
.\.venv\Scripts\python.exe -m pip install -r ".\03_Diagnostic_Tools\requirements.txt"
$env:PYTHONPATH = ".\03_Diagnostic_Tools"
.\.venv\Scripts\python.exe -m frame_alignment.serial_probe --list
.\.venv\Scripts\python.exe -m frame_alignment.serial_probe --port COM号 --duration 15 --show-frames
```

接收器持续测试时，在同一次串口连接中清零并查询队列统计：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.serial_probe --port COM号 --baud 921600 --duration 600 --receiver-stats --save receiver_30hz.bin
```

诊断工具会在结束时打印 `$GLOVITY,RX,STATS,收到,入队,溢出,当前深度,最高深度,容量`。启动和重新连接时可能先读到旧缓存帧；判断丢帧时应以稳定区间的序号与时间戳为准。
