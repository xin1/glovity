# 六轴手套离线配对烧录工具

`SixAxisGloveFlasher_Offline.exe` 是完全离线版本，只内置：

- 左手预编译固件；
- 接收器预编译固件；
- Espressif `esptool` 烧录器。

最终用户不需要安装 Arduino IDE、ESP32 开发板包、Python，也不需要联网下载工具链。

## 使用

1. 同时连接左手板和接收器；
2. 运行 EXE，点击“扫描并读取 MAC”；
3. 确认哪个串口是左手、哪个是接收器；
4. 点击“开始离线配对并烧录”；
5. 工具清除旧 NVS 配对、烧录对应镜像，再通过串口把对方 MAC 保存进 NVS；
6. 两块板重新上电，接收器 LED 常亮表示收到左手数据。

配对使用 ESP-NOW 信道 1。烧录会覆盖旧固件和旧配对数据。

## 开发者构建

`offline_payload` 必须已有以下文件：

```text
esptool.exe
GloveLeft.merged.bin
Receiver.merged.bin
```

然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```
