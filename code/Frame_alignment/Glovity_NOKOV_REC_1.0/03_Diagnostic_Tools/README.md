# Glovity 帧对齐工具

这是依据两份 V0.1 接口文档实现的第一版主机端工具。它解决四件事：

1. 从 USB 任意拆包、粘包和噪声中恢复并区分旧168/170字节帧与180字节V2帧；
2. 校验 CRC、字段范围并解码 11 颗 IMU；
3. 用多组同步点拟合 `t_nokov = scale * t_glove + offset`；
4. 将手套数据按 NOKOV 时间重采样，姿态使用四元数 SLERP。

## 本交付包中的用途

本目录主要用于接收器串口诊断、180字节V2帧校验、原始数据保存及离线分析。历史实验组件仍保留在源码中，但不属于本次NOKOV基站联调验收范围，也不能代替基站与手套的时钟映射验证。

### 文件用途

| 文件／目录 | 用途 | 日常联调是否需要 |
| --- | --- | --- |
| `frame_alignment/serial_probe.py` | 枚举串口、抓取原始字节、统计有效帧和序号缺口、查询接收器队列统计 | 需要，主要诊断入口 |
| `frame_alignment/protocol.py` | 识别并解析180字节V2及旧版帧，校验CRC／XOR | 由其他工具调用 |
| `frame_alignment/cli.py` | 提供原始数据解码、时钟拟合和离线对齐命令 | 离线分析时需要 |
| `frame_alignment/clock.py` | 拟合手套本地时间到NOKOV时间的偏移和漂移 | 做时间映射时需要 |
| `frame_alignment/resample.py` | 按NOKOV时间轴插值手套数据，四元数使用SLERP | 做离线对齐时需要 |
| `frame_alignment/__init__.py` | 将目录声明为Python包并导出公共接口 | 必须保留 |
| `tests/` | 自动检查协议解析、时钟拟合、插值和串口诊断逻辑 | 开发验证用，运行时不调用 |
| `requirements.txt` | Python依赖列表；`pyserial`用于串口，`numpy`用于计算，`opencv-python`仅供历史视觉实验使用 | 安装工具时使用 |
| `README.md` | 本工具目录的命令与边界说明 | 建议保留 |

## 运行测试

```powershell
python -m unittest discover -s tests -v
```

## 连接手套检查180字节数据

建议创建项目虚拟环境、安装串口依赖并列出端口：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m frame_alignment.serial_probe --list
```

连接手套并探测15秒（只有一个串口时可以省略 `--port`）：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.serial_probe --port COM5 --duration 15 --show-frames --save glove_raw.bin
```

程序会区分以下情况：有效180字节V2帧、文本摘要、没有 `AA 55` 帧头的其他二进制协议、存在帧头但CRC/版本/字段不匹配，以及完全没有数据。USB CDC通常不真正使用串口波特率；如果设备实际是UART转串口，可以用 `--baud` 指定实际配置。

### 接收器队列统计与持续测试

交付接收器v2.3支持队列统计。连接接收器COM口时可在同一串口会话内清零统计、抓取原始数据，并在结束时查询统计：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.serial_probe --port COM号 --baud 921600 --duration 600 --receiver-stats --save receiver_30hz.bin
```

输出的 `$GLOVITY,RX,STATS` 六个数字依次为：有效普通帧接收数、成功入队数、队列溢出数、当前深度、历史最高深度、最大可用容量。启动时的旧缓存帧可能造成序号跳变；持续区间应另按设备时间戳和序号检查。队列溢出为0仅表示接收器没有在入队后丢弃，不能单独证明无线侧无缺帧。

## 解码USB原始数据

```powershell
python -m frame_alignment.cli decode left_usb.bin left_frames.jsonl --device-id left-glove
```

输出是一行一帧的 JSON，并在结果中区分旧168字节、历史170字节USB变体和新版180字节V2。旧版没有设备时间戳、REC录制位和USB可见的失败掩码，只能用于识别、诊断或按主机接收时间降级处理，不能提供与V2相同的严格对齐能力。

## 拟合设备时钟

准备 `sync_points.csv`：

```csv
device_id,glove_us,nokov_us
left-glove,1000000,2010050
left-glove,2000000,3010070
right-glove,900000,2010020
right-glove,1900000,3010060
```

同步点可以来自REC精确边沿事件、周期SYNC脉冲或双向对时。只有一个点时工具只估计偏移；两个以上不同时间点才能估计漂移。

```powershell
python -m frame_alignment.cli fit-clock sync_points.csv clock_models.json
```

`drift_ppm`、`rms_error_us` 和 `max_error_us` 用于判断同步质量。正式录制建议周期性提供同步点，不要只把两只手的第一帧强行设为同一时刻。

## 对齐到NOKOV时间轴

把左右手解码JSONL合并为一个文件，并准备包含 `nokov_us` 列的目标时间轴：

```powershell
Get-Content left_frames.jsonl,right_frames.jsonl | Set-Content all_frames.jsonl
python -m frame_alignment.cli align all_frames.jsonl clock_models.json nokov_timeline.csv aligned.jsonl
```

每个NOKOV时刻都会输出左右手插值结果。若相邻手套帧间隔超过默认的100ms，结果会标记为 `null`，避免跨越长丢帧硬插值。可用 `--max-gap-us` 调整，但应根据业务验收指标设定。

## 当前边界

- `serial_probe.py` 已提供串口实时探测和原始数据保存，但没有图形化实时采集界面；
- 设备固件位于交付包根目录的 `01_Firmware`，本诊断目录只包含主机端工具；
- `timestamp_us`仍是一轮11颗IMU读取的开始时间，不能消除一帧内部的顺序读取时间差；
- NOKOV实际导出文件的列名和时间单位尚未提供；当前批量入口使用最小格式 `nokov_us`，拿到真实样例后应增加对应适配器。
