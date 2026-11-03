# Glovity 固件修改记录

修改日期：2026-10-19  
目标：ESP32-S3 Mini 手套固件

## 本次修改

- `source/firmware/components/telex_core/haptics.c`
  - 以 `GLOVITY MOD 2026-10-19` 注释明确标记。
  - 将触觉会话开始时原有的两次提示振动改为静默开始。
  - 网页下发的目标手指和强度现在是会话开始后的第一份物理振动输出。
  - 正常停止时原有的三次结束提示、紧急停止、安全租约和强度预算保持不变。

## 明确未修改

- IMU 采集、滤波、姿态处理、封包、校准及频率逻辑均未修改。
- `imu.c`、`imu_service.c`、`imu_jy901p.c` 均保持原交付内容。
- `binaries/esp32s3` 中的原始预编译镜像没有覆盖，因此仍带有原来的两次启动提示。

## 构建产物

在 ESP-IDF 6.0 环境运行 `Build-S3-Glovity.ps1`。修改后的镜像生成在
`build/esp32s3-glovity`，与原始镜像分开保存。随后运行
`Flash-S3-Glovity.ps1 -Port COM5`（按实际串口修改）即可烧录。
