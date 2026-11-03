# Release Note — HY IMU Full SDK（kebo / 科博智能）


| 项       | 内容                                                                                |
| ------- | --------------------------------------------------------------------------------- |
| SDK 包版本 | 0.6.2                                                                             |
| 日期      | 2026-11-02                                                                        |
| 库文件     | `lib/libhy_aru8113_v0.6.2.a`（156860 bytes）                                        |
| 料号      | ICM-45686                                                                         |
| 实例数     | 11（`HY_IMU_MAX_INSTANCES=11`）                                                     |
| 采集库版本   | 0.6.1（`HY_IMU_VERSION_STRING`，与 `MAJOR/MINOR/PATCH` 一致）                           |
| 算法库版本   | 0.6.2（`HY_IMU_PROC_VERSION_STRING`，与 `MAJOR/MINOR/PATCH` 一致）                      |
| 适用 MCU  | STM32H743VIT6（Cortex-M7，小端）                                                       |
| 工具链     | `arm-none-eabi-gcc`（Arm GNU Toolchain 13.3.Rel1 (Build arm-13.24)）13.3.1 20240614 |


链接名是 `libhy_aru8113_v0.6.2.a`。Include Path 指到本包 `include/`。

本编译器与 STM32CubeIDE 1.19.0 所带 GNU Tools for STM32 13.3.rel1 为同一 gcc 13.3.1 基线。

## 本版说明

SDK 包版本 0.6.2。实例数 11

## 编译选项（本库实际使用）

```text
-mcpu=cortex-m7 -mthumb
-mfpu=fpv5-d16 -mfloat-abi=hard
-std=gnu11 -O1
-ffunction-sections -fdata-sections
-Wall -Wextra
-DHY_IMU_DEVICE=45686 -DHY_IMU_MAX_INSTANCES=11
```

`-print-multi-directory`：`thumb/v7e-m+dp/hard`。

抽查对象：`Tag_CPU_arch: v7E-M`，`Tag_THUMB_ISA_use: Thumb-2`，`Tag_FP_arch: FPv5/FP-D16 for ARMv8`，`Tag_ABI_VFP_args: VFP registers`（hard float）。`FPv5/FP-D16 for ARMv8` 是 gcc 13.3 对 `-mfpu=fpv5-d16` 的属性名，CPU 架构仍是 v7E-M。

未传入、但和 ABI 有关的项：

- 字节序：小端。未传 `-mbig-endian`。
- 枚举：未传 `-fshort-enums`，也未传 `-fno-short-enums`。这套 gcc 默认枚举为 short，对象标记 `Tag_ABI_enum_size: small`。不要再打开或关闭 short enum。
- `char`：未传 `-funsigned-char`，也未传 `-fsigned-char`。这套 gcc 默认 `char` 为无符号。
- 未传 `-fpack-struct`、`-flto`、`-g`。
- 优化为 `-O1`。未传 `-O0`、`-O2`、`-O3`、`-Os`、`-Og`。

`-DSTM32H743xx`、`-DUSE_HAL_DRIVER` 和 `-IInc`、`-IApp/Inc` 用于编译贵司自己的 `aru8113_port.c`。本库不包含 STM32 HAL，未传入。贵司工程链接本库时继续使用这些宏和头文件路径即可。

## 使用说明

1. Include Path 指向本包 `include/`
2. 链接 `lib/libhy_aru8113_v0.6.2.a`
3. 实现 `cust_imu_*`：参考 `port/cust_imu_platform_template.c`
4. 示例见 `docs/`
5. 公开头与库须使用同一 `HY_IMU_MAX_INSTANCES`（本包为 11）

