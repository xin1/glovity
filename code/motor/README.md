# Arduino UNO 震动马达测试台

这是一个用于 **V1bration Motor 三针模块**（`IN`、`VCC`、`GND`）的测试项目。电脑端滑块通过 USB 串口设置 Arduino UNO 的 PWM，占空比越高，通常震动越强。

## 接线

开始接线前，先拔掉 Arduino 的 USB 电源。

| 马达模块 | Arduino UNO | 说明 |
|---|---|---|
| `IN` | `D9` | PWM 控制信号 |
| `VCC` | `5V` | 模块电源；不要接到数字 IO |
| `GND` | `GND` | 公共地 |

图片标注的模块参数为工作电压 `3.0–5.3V DC`、额定电流不高于约 `60mA`、启动电流不高于约 `90mA`，且模块自带 MOS 管，因此此测试按模块的 `5V` 规格接线。不要把裸马达直接接到 Arduino IO 引脚。

## 1. 上传 Arduino 程序

1. 用 Arduino IDE 打开 `arduino/motor_controller/motor_controller.ino`。
2. 开发板选择 **Arduino Uno**，选择对应端口。
3. 点击“上传”。
4. 上传完成后关闭 Arduino IDE 的串口监视器，避免占用串口。

固件使用 `D9` 输出 PWM，波特率为 `115200`。连续 3 秒收不到电脑端命令时，固件会自动关闭马达。

## 2. 安装并运行 UI

需要 Python 3.9 或更高版本。在本目录打开 PowerShell：

```powershell
py -m pip install -r requirements.txt
py motor_ui.py
```

安装完成后，也可以双击 `run_ui.bat` 启动。

## 3. 测试步骤

1. 在界面中选择 Arduino 的 `COM` 端口并点击“连接”。
2. 将滑块先放在较小强度（例如 20%–30%）。
3. 点击“开始输出”，再拖动滑块调节强度。
4. 点击“立即停止”关闭输出；断开串口或退出界面也会发送停止命令。

低 PWM 时马达可能无法克服静摩擦而不转，这是直流震动马达的常见现象。可先短暂提高强度让其启动，再降低到需要的水平。

## 故障排查

- **没有 COM 端口**：更换支持数据传输的 USB 线；克隆版 UNO 可能需要安装 CH340 驱动。
- **端口被占用**：关闭 Arduino IDE 串口监视器和其他串口工具。
- **只有满速/停止两档**：确认 `IN` 接的是 `D9`，而不是普通非 PWM 引脚。
- **马达不转**：检查 `VCC/GND` 是否接反、模块是否共地，并逐渐提高滑块强度。
- **Arduino 重启或 USB 不稳定**：停止使用 UNO 的 5V 供电，改用稳定的外部 `5V` 电源给模块供电，并务必把外部电源 `GND` 与 Arduino `GND` 相连。

## 串口协议

每条命令以换行结尾：

- `SET:0` 到 `SET:255`：设置 PWM。
- `STOP`：立即停止。
- `PING`：保持连接。

