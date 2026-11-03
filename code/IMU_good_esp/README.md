# Glovity 11-IMU 左右手固件与配对烧录器

校准模式读取每只手 11 个 IMU 的九轴原始寄存器值，字段顺序为
`ax, ay, az, gx, gy, gz, hx, hy, hz`。普通模式保留原姿态数据格式，并且不读取磁力计。

## 模式控制

- 有线：`$GLOVITY,CAL,START` 开始，`$GLOVITY,CAL,STOP` 保存并退出。
- 接收器：先发 `$GLOVITY,RX,MAINT,ENTER`，再发
  `$GLOVITY,RX,CAL,START,left,<session>,<sequence>`。
- 校准数据前缀：`$GLOVITY,CAL,RAW9`（手套直连）或
  `$GLOVITY,RX,CAL,RAW9`（经接收器）。
- 停止、放弃或 60 秒超时均会恢复 `CALSW=0`；启动校准写入 `CALSW=7`。

## 配对烧录器

`pair_flasher` 会读取接收器、左手和右手 ESP32-S3 的 Wi-Fi MAC（ESP-NOW 使用该
MAC），替换三个固件中的固定占位符，然后从地址 `0x0` 烧录 merged binary。

固件构建后请放置为：

- `pair_flasher/firmware/receiver.merged.bin`
- `pair_flasher/firmware/left.merged.bin`
- `pair_flasher/firmware/right.merged.bin`

运行 `pair_flasher/build.ps1` 可生成 `pair_flasher/dist/Glovity_Pair_Flasher.exe`。
