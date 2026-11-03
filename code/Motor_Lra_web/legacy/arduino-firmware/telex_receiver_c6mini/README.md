# TELEX RECEIVER Ver.2（ESP32-C6-MINI）

该固件把照片中的接收器作为 USB 串口到 BLE 的自动桥接器。接收器自动扫描服务 UUID，连接名称为 `TELEX-HAPTIC-L` 的左手振动板；无需在 Windows 蓝牙设置中手动配对或输入 PIN。

## 烧录

1. Arduino IDE 打开 `telex_receiver_c6mini.ino`。
2. 开发板选 `ESP32C6 Dev Module`，Flash Size 选 `4MB`，USB CDC On Boot 选 `Enabled`，上传速度选 `921600`。
3. 用接收器板上的 USB-C 连接电脑，选择它的 COM 口并上传。
4. 振动板也必须烧录同目录上一级 `telex_core_left_v2/telex_core_left_v2.ino` 的新版固件。
5. 两块板重新上电。接收器会反复扫描并自动重连；EXE 中应选择接收器的 COM 口，而不是振动板的 COM 口。

成功时串口日志包含：

```text
EVENT RECEIVER TELEX_C6MINI VER_2 LEFT
EVENT BLE SCANNING LEFT
EVENT BLE CONNECTED TELEX-HAPTIC-L
EVENT BOARD AW86234_C6_8CH 8
EVENT READY
```

## 注意

- 当前版本固定连接左手板，板上的 LEFT/RIGHT 硬件选择信号尚未使用，因为照片无法确认这两个按键接到哪个 GPIO。
- BLE 断开时接收器自动重扫；振动板原有 1.5 秒看门狗会停止输出。
- USB 串口直连振动板仍然保留，可以单独排查硬件。
