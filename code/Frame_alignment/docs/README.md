# Glovity 帧对齐工具

这是依据两份 V0.1 接口文档实现的第一版主机端工具。它解决四件事：

1. 从 USB 任意拆包、粘包和噪声中恢复并区分旧168/170字节帧与180字节V2帧；
2. 校验 CRC、字段范围并解码 11 颗 IMU；
3. 用多组同步点拟合 `t_nokov = scale * t_glove + offset`；
4. 将手套数据按 NOKOV 时间重采样，姿态使用四元数 SLERP。

## OV9782 + ESP32-S3/11 IMU 有线实验

先运行无硬件演示，确认可视化界面正常：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.experiment --demo
```

扫描相机索引：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.experiment --scan-cameras
```

当前实验确认只使用有画面的右相机 `camera 1`，这也是程序默认配置：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.experiment --layout mono --camera 1 --serial auto --width 640 --height 480 --fps 30
```

汇博视捷模块若把左右图像拼在一个UVC画面中：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.experiment --layout sbs --camera 0 --serial COM49 --width 2560 --height 800 --fps 30
```

若Windows枚举成两个独立摄像头：

```powershell
.\.venv\Scripts\python.exe -m frame_alignment.experiment --layout dual --left-camera 0 --right-camera 1 --serial COM49 --width 1280 --height 800 --fps 30
```

窗口显示CAMERA、IMU、ALIGN和RESULT状态。IMU协议会自动识别当前实测的170字节旧帧（帧尾XOR）和180字节V2帧（CRC16）。三项同时正常时，RESULT变绿并显示 `PASS: WIRED STREAMS + ALIGNMENT`。按Q或ESC结束。每次实验会在 `experiment_output/session_日期_时间` 下保存原始IMU、解码帧和相机/IMU配对日志。

170字节旧帧没有设备时间戳和帧序号，因此只能按USB到达时间做初步软件对齐；180字节V2帧会自动切换为ESP32设备时间映射。

注意：第一版使用USB到达时间进行软件对齐，适合验证有线数据链路和算法流程，不等同于相机曝光级硬件同步。后续若相机控制板引出FSIN/STROBE，可继续加入硬件触发事件。

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

- 当前实现是主机端核心库，还没有接串口实时采集界面；
- 当前目录没有文档中所述ESP32-S3 good固件，因此没有直接修改设备固件；
- `timestamp_us`仍是一轮11颗IMU读取的开始时间，不能消除一帧内部的顺序读取时间差；
- NOKOV实际导出文件的列名和时间单位尚未提供；当前批量入口使用最小格式 `nokov_us`，拿到真实样例后应增加对应适配器。
