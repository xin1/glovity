### `firmware/`

固件、交付包和硬件资料。

当前正式 ESP-IDF 固件：

```text
firmware/TELEX-Firmware-20261018/
```

其中：

- `source/firmware`：ESP-IDF源码；
- `binaries`：预编译 BIN；
- `build-config`：S3/C6构建配置；
- `Build-S3-Glovity.ps1`：编译左手；
- `Flash-S3-Glovity.ps1`：烧录左手；
- `GLOVITY_MODIFICATIONS.md`：修改记录。

其他目录是左右手/接收器 PCB硬件工程资料。Bench和 AW86234独立工具已移动到根目录 `vendor-tools/`。

| 目录                               | 内容                      | 是否当前使用 |
| ---------------------------------- | ------------------------- | ------------ |
| `TELEX_BLE_RECEIVE_TYPEC_V1.0`     | C6接收器 PCB工程          | 硬件参考     |
| `TELEX_CORE_LEFT_V2.0`             | 左手 PCB工程              | 硬件参考     |
| `TELEX_CORE_LEFT_V2.0 (2)`         | 左手 PCB工程副本/生产输出 | 硬件参考     |
| `TELEX-Firmware-20261018`          | S3/C6正式 ESP-IDF固件     | 当前使用     |

# TELEX 左手与 C6：配对、烧录、验证

适用环境：Windows、ESP-IDF 6.0.x。

> 每换一套手套和接收器，都必须重新查看 COM 和 MAC。文中的 COM65、COM54 仅为本次示例。

## 1. 查看 COM 和 MAC

一次只插一块板，查看新增串口：

```powershell
python -m serial.tools.list_ports
```

读取左手 MAC：

```powershell
python -m esptool --chip esp32s3 --port GLOVE_COM read-mac
```

读取接收器 MAC：

```powershell
python -m esptool --chip esp32c6 --port RECEIVER_COM read-mac
```

C6 必须记录 6 字节 `BASE MAC`，不要使用带 `FF:FE` 的 8 字节地址。

本次示例：

```text
左手：COM65，MAC 90:70:69:28:EA:84
C6： COM54，BASE MAC 10:BD:A3:93:BE:24
```

## 2. 配对

同时连接两块设备，关闭网页串口、串口助手和 `idf.py monitor`：

```powershell
cd "D:\Desktop\github\glovity\code\Motor_Lra_web"

python .\tools\pair_telex_espnow.py `
  --glove-port GLOVE_COM `
  --receiver-port RECEIVER_COM `
  --suite GLOVITY_L_XX
```

本次示例：

```powershell
python .\tools\pair_telex_espnow.py `
  --glove-port COM65 `
  --receiver-port COM54 `
  --suite GLOVITY_L_02
```

核对脚本打印的 UID 是否与第 1 步读取的 MAC 一致。

成功标志：

```text
Receiver configuration written and verified.
Left-glove configuration written and verified.
Pairing complete.
```

如果是空白板，配对脚本无法识别设备：先执行第 3、4 节烧录，然后回来重新配对。

## 3. 烧录左手 S3

```powershell
cd "D:\Desktop\github\glovity\code\Motor_Lra_web\firmware\TELEX-Firmware-20261018\TELEX-Firmware-20261018"

.\Build-S3-Glovity.ps1
.\Flash-S3-Glovity.ps1 -Port GLOVE_COM
```

本次示例：

```powershell
.\Flash-S3-Glovity.ps1 -Port COM65
```

烧录不稳定时：

```powershell
.\Flash-S3-Glovity.ps1 -Port GLOVE_COM -Baud 115200
```

## 4. 烧录 C6 接收器

```powershell
$pkg = "D:\Desktop\github\glovity\code\Motor_Lra_web\firmware\TELEX-Firmware-20261018\TELEX-Firmware-20261018"

idf.py `
  -C "$pkg\source\firmware" `
  -B "$pkg\build\esp32c6" `
  -D "SDKCONFIG=$pkg\build-config\esp32c6\sdkconfig" `
  -D IDF_TARGET=esp32c6 `
  build

idf.py `
  -C "$pkg\source\firmware" `
  -B "$pkg\build\esp32c6" `
  -p RECEIVER_COM `
  flash
```

本次示例将 `RECEIVER_COM` 替换为 `COM54`。

## 5. 重启并验证

先拔掉两块设备，然后先插 C6，再启动左手，等待约 3 秒。

```powershell
cd "D:\Desktop\github\glovity\code\Motor_Lra_web"

python .\tools\pair_telex_espnow.py `
  --receiver-port RECEIVER_COM `
  --verify-only
```

成功输出：

```text
PASS: C6 received ... left-glove pose packets over ESP-NOW.
```

## 6. 禁止擦除 NVS

普通更新不要执行：

```powershell
idf.py erase-flash
```

整片擦除会删除配对信息。普通 `flash` 不会主动覆盖 NVS。

## 操作顺序

已有 TELEX 固件：

```text
查看 COM/MAC → 配对 → 烧录左手 → 烧录 C6 → 重启 → 验证
```

空白板：

```text
查看 COM/MAC → 烧录左手和 C6 → 配对 → 重启 → 验证
```

