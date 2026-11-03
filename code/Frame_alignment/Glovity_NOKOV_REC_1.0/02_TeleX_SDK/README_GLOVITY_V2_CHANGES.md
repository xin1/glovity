# TeleX SDK — Glovity 180 字节 V2 协议适配说明

## 1. 本次修改的目的

原 TeleX SDK 读取的是旧版 168 字节手套数据帧。本分支将手套输入升级为 Glovity 180 字节 V2 帧，使上位机能够取得固件新增的采集起始时间、录制状态、帧序号、失败掩码和 CRC 校验结果。

SDK 包版本由 `0.1.0` 更新为 `0.3.0`：`0.2.0` 完成V2适配，`0.3.0` 增加旧168/170字节协议自动识别。视觉定位、双目相机和手指正向运动学代码未改协议含义。

## 2. 支持的数据帧

SDK 优先接收以下180字节小端V2帧，同时兼容旧接收器输出的168字节帧和已实测的170字节旧USB变体：

| 偏移 | 长度 | 字段 | 说明 |
|---:|---:|---|---|
| 0 | 2 | `magic` | 固定 `AA 55` |
| 2 | 1 | `version` | 固定 `2` |
| 3 | 1 | `hand_id` | `0` 左手，`1` 右手 |
| 4 | 1 | `flags` | bit0 录制中；bit1 腕部角速度有效 |
| 5 | 1 | `battery_percent` | `0..100` |
| 6 | 2 | `frame_seq` | `uint16`，自然回绕 |
| 8 | 8 | `timestamp_us` | 手套本轮采集开始时刻，微秒 |
| 16 | 2 | `imu_fail_mask` | bit0..10 对应 11 路 IMU；1 表示本帧该路失败 |
| 18 | 154 | `imu[11]` | 每路 `Euler int16[3] + quaternion int16[4]` |
| 172 | 6 | `wrist_gyro` | `int16[3]` |
| 178 | 2 | `crc16` | CRC-16/CCITT-FALSE，覆盖字节 `[0,178)` |

字段定义以项目中的《Glovity 核心板触发与数据输出完整技术说明》和《Glovity NOKOV 接口与数据输出说明》为准。

## 3. Python API 新增字段

`GloveFrame` 新增：

- `frame_seq`：`frame_id` 的 V2 别名，便于旧代码继续工作；
- `timestamp_us`：固件设备时间戳；
- `host_receive_s`：PC 收到完整串口帧时的单调时钟；
- `recording`：固件是否处于录制状态；
- `battery_percent`；
- `imu_fail_mask`；
- `wrist_gyro_valid` 和 `wrist_gyro_dps`。

每个 `ImuData` 新增 `valid`。失败掩码对应位为 1 时，该路仍保留在 11 路数组的固定位置，但 `valid=False`，不能当作有效姿态使用。

所有帧都提供 `protocol` 与 `protocol_version`。可能值为 `v2-180`、`legacy-168`、`legacy-170`。旧版没有录制位、设备时间戳和可从USB帧取得的失败掩码，因此还会明确返回 `has_device_timestamp=False`、`has_recording_flag=False`、`has_fail_mask=False`；这些缺失信息不得伪造成可靠的零值参与严格帧对齐。

`TeleXSession.read()` 返回的 `HandState` 同步暴露：

```python
state.frame_seq
state.glove_timestamp_us
state.glove_host_receive_s
state.recording
state.imu_fail_mask
state.wrist_gyro_valid
state.wrist_gyro_dps
state.protocol
state.protocol_version
state.has_device_timestamp
state.has_recording_flag
state.has_fail_mask
```

只要 11 路中有一路失败，SDK 仍返回原始 `imus`，但本帧 `joints=None`，避免用无效四元数计算整手骨架。腕部 IMU 或腕部角速度无效时，该帧不会送入视觉/IMU 融合。

## 4. 修改的源码

- `src/telex/tracker/imu/receiver.py`：180 字节流同步、字段解析及 CRC16 校验；
- `src/telex/hub/parse.py`：完整手套帧解析和 V2 测试帧生成；
- `src/telex/glove/types.py`：增加有效位和帧元数据；
- `src/telex/hub/runtime.py`：缓存和复制完整 V2 元数据；
- `src/telex/tracker/imu/wireless.py`：阻止无效腕部数据进入融合；
- `src/telex/types.py`、`src/telex/session.py`：向上位机暴露 V2 字段并处理失败 IMU；
- `tests/test_hub_parse.py`：增加 CRC、时间戳、录制位、失败掩码和串流重同步测试。

## 5. 安装与验证

在本 README 所在目录执行：

```powershell
python -m pip install -e ".[test]"
python -m pytest
```

连接接收器后可运行串口监视程序：

```powershell
python -m telex.tracker.imu.receiver --port COM号 --baud 2000000 --hand all
```

## 6. 与帧对齐的关系和当前边界

V2把帧对齐所需的原始依据可靠地送到上位机：硬件触发后的 `recording`、手套 `timestamp_us`、连续 `frame_seq`、数据质量标志和主机接收时刻。旧版虽能被识别和读取，但没有足够字段实现同等级别的帧对齐，只能使用主机接收时间做降级处理。

它没有替上位机实现“按 NOKOV 帧号/时间戳查找最近手套帧”的缓存匹配算法。当前 `TeleXSession.read()` 仍读取相机结果和手套的最新一帧，因此不能把两者称为严格帧对齐。建议上位机以共同触发沿作为录制起点，分别保存相机与手套数据，再依据双方确认的公共时基或映射规则进行匹配。若相机时间戳与手套 `timestamp_us` 并非同一时钟域，还必须先做时钟映射，不能直接相减。

## 7. 联调前仍需确认

1. 接收器 USB 串口完整转发 180 字节普通帧；仅在上位机主动发送统计命令时，可能插入 `$GLOVITY,RX,STATS` 文本响应，解析器应按帧头与 CRC 重新同步；
2. 固件与 SDK 对 CRC 参数、字节序、IMU 顺序和量程定义完全一致；
3. `timestamp_us` 的起点、回绕周期和重启行为；
4. NOKOV 输出的帧号/时间戳定义，以及它与触发沿的关系；
5. 左右手是否共用时钟；若不共用，上位机需分别维护映射；
6. 实机丢包、CRC 错误、序号回绕和触发反复切换测试。
