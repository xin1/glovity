# 振动板固件版本

### `legacy/arduino-firmware/`

旧版 Arduino 和 Arduino-ESP32 固件：

- `lra_visual_control`：早期 Arduino + JW-BLE-DL500 单路振动方案。
- `telex_core_left_v2`：旧版左手固件。
- `telex_core_left_v2_ble`：旧版 BLE 左手固件。
- `telex_core_left_v2_usb`：旧版 USB 左手固件。
- `telex_receiver_c6mini`：旧版 C6 接收器。

当前正式版本不建议从这里烧录。

## 纯 USB 版（推荐当前使用）

打开 `telex_core_left_v2_usb/telex_core_left_v2_usb.ino`。该版本不初始化 BLE，适合振动板通过 Type-C 直接连接电脑，并通过项目的 `web/start-linkerhand-web.bat` 启动网页；四指到 CH4–CH7 的独立映射与安全看门狗全部保留。

当前网页的整体频率与整体振幅通过 `PARAM 频率 AMP` 写入，并在触觉联动中作为中等压力的基准值；压力变化会分别调制实际频率与振幅。USB 固件在持续输出时会立即应用新参数，并保留四路中原本关闭的通道；锯齿模式按触发时的频率播放两轮五级渐强。

USB 固件支持 `DETECT_F0 通道` 检测单个指尖马达的谐振频率。ESP32-C6 测试板上食指、中指、无名指、小指依次对应 CH4、CH5、CH6、CH7；检测时固件会先停止所有振动，以安全电平执行约 800 ms 的 AW86234 F0 检测，再返回 `EVENT F0 通道 频率×10`。当前 C6 板没有拇指振动通道，网页选择拇指时不会发起检测。

界面可设 0–400 Hz、AMP 0–100，0 表示关闭。浅色标记的建议区间为频率 50–300 Hz、AMP 25–47。AW86234 的 `F_PRE` 为 8 位，当前驱动方式下非零频率低于约 94 Hz 会生效为约 94 Hz；AMP 25–47 保留已验证的 AW 电平 18–40 映射，超过 47 的范围逐步映射到最高电平 77，尚需按实际马达额定值与温升标定。

## USB + BLE 版（备用）

打开 `telex_core_left_v2_ble/telex_core_left_v2_ble.ino`。该版本同时保留 USB 串口，并广播 `TELEX-HAPTIC-L`，可与 `telex_receiver_c6mini` 接收器固件配合。

## Arduino 选项

- Board：`ESP32C6 Dev Module`
- USB CDC On Boot：`Enabled`
- Flash Mode：`QIO`
- Flash Size：`4MB`
- CPU Frequency：`160MHz`
- Upload Speed：`921600`（不稳定时降为 `460800`）

两个工程不能同时放在一个 Arduino 草图目录中编译；请分别打开各自与文件同名的目录。原始 `telex_core_left_v2` 工程继续保留。
