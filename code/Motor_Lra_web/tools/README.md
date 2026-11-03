### `tools/`

辅助工具：

- `telex_device_tool.py`：统一入口，提供串口识别、S3/C6构建烧录、配对和验证；
- `pair_telex_espnow.py`：左手和 C6 配对、验证；
- `pixi.exe`：环境管理程序。

常用命令：

```powershell
python .\tools\telex_device_tool.py ports
python .\tools\telex_device_tool.py inspect --chip esp32s3 --port COM65
python .\tools\telex_device_tool.py build-glove
python .\tools\telex_device_tool.py flash-glove --port COM65
python .\tools\telex_device_tool.py build-receiver
python .\tools\telex_device_tool.py flash-receiver --port COM54
python .\tools\telex_device_tool.py pair --glove-port COM65 --receiver-port COM54 --suite GLOVITY_L_02
python .\tools\telex_device_tool.py verify --receiver-port COM54
```

### 查看串口
python .\tools\telex_device_tool.py ports

### 查看 S3 MAC
python .\tools\telex_device_tool.py inspect --chip esp32s3 --port COM65

### 查看 C6 BASE MAC
python .\tools\telex_device_tool.py inspect --chip esp32c6 --port COM54

### 编译、烧录左手
python .\tools\telex_device_tool.py build-glove
python .\tools\telex_device_tool.py flash-glove --port COM65

### 编译、烧录接收器
python .\tools\telex_device_tool.py build-receiver
python .\tools\telex_device_tool.py flash-receiver --port COM54

### 配对
python .\tools\telex_device_tool.py pair `
  --glove-port COM65 `
  --receiver-port COM54 `
  --suite GLOVITY_L_02

### 验证无线链路
python .\tools\telex_device_tool.py verify --receiver-port COM54
