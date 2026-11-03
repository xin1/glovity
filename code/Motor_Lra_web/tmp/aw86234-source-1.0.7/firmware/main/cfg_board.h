/**
 * @file  cfg_board.h
 * @brief 全部引脚 / 地址 / 板级常量集中于此（唯一事实来源）。
 *
 * 目标板卡：Vibration_Test_Board_A_V1.0
 *           ESP32-C6-MINI-1 + TCA9548A(0x70) + 8 × AW86234FCR(0x5A)
 *
 * 依据：`01_硬件事实与接线表.md` §2/§3/§5/§6/§10。
 * ⚠️ 本文件里的引脚是**实测网表**结论，改动前请先核对原理图审查报告。
 */

#pragma once

#include "driver/gpio.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ==========================================================================
 * I2C 引脚（两套候选，运行时自动探测选择）
 * J1 跳线决定 TCA 的上游接到 LP(IO6/IO7) 还是 HP(IO3/IO2)。
 * ========================================================================== */
#define BOARD_I2C_LP_SDA            GPIO_NUM_6
#define BOARD_I2C_LP_SCL            GPIO_NUM_7
#define BOARD_I2C_HP_SDA            GPIO_NUM_3
#define BOARD_I2C_HP_SCL            GPIO_NUM_2

#define BOARD_I2C_SPEED_FAST        400000  /* 上限：TCA9548A 与 C6 都只到 Fast mode */
#define BOARD_I2C_SPEED_SAFE        100000  /* 上拉不足时的降级档 */
#define BOARD_I2C_PROBE_SPEED       100000  /* 探测期用低速更稳 */
#define BOARD_I2C_XFER_TIMEOUT_MS   50

/* ==========================================================================
 * 分路器 TCA9548A
 * ========================================================================== */
#define BOARD_TCA9548A_ADDR         0x70
#define BOARD_TCA_RST_GPIO          GPIO_NUM_23
#define BOARD_TCA_RST_HOLD_US       10      /* 手册 tW(L) 最小 6 ns，10 µs 有极大余量 */
#define BOARD_CHANNEL_COUNT         8

/* ==========================================================================
 * 驱动芯片 AW86234FCR
 * ⚠️ 0x5A 既是 I2C 从机地址，又是内部寄存器 TRIMCFG2 的地址，代码里用两个常量名。
 * ========================================================================== */
#define BOARD_AW8623X_ADDR          0x5A
#define BOARD_AW_SOFTRST_REG        0x00
#define BOARD_AW_SOFTRST_VAL        0xAA
#define BOARD_AW_CHIPID_H_REG       0x57    /* 期望 0x23 */
#define BOARD_AW_CHIPID_L_REG       0x69    /* 期望 0x40 */
#define BOARD_AW_CHIPID_EXPECTED    0x2340

/* ==========================================================================
 * TRIG（A1 脚）
 * ★ 2026-09 硬件更新：**每个电机一条独立 TRIG**，CH5/6/7 不再共用一根。
 *   依据改动后的原理图（TRIG0~TRIG7 八条独立网络 + R33~R40 八颗 4.7K 下拉）：
 *     CH0=TRIG0=IO18   CH1=TRIG1=IO19   CH2=TRIG2=IO20   CH3=TRIG3=IO21
 *     CH4=TRIG4=IO22   CH5=TRIG5=IO14   CH6=TRIG6=IO0    CH7=TRIG7=IO1
 *   同图其它变化：IO4/IO5 = LED1/LED2（P0-2 已修）、IO9 = BOOT 按键、IO23 = TCA_RESET。
 * ========================================================================== */
#define BOARD_TRIG_GPIO_0           GPIO_NUM_18
#define BOARD_TRIG_GPIO_1           GPIO_NUM_19
#define BOARD_TRIG_GPIO_2           GPIO_NUM_20
#define BOARD_TRIG_GPIO_3           GPIO_NUM_21
#define BOARD_TRIG_GPIO_4           GPIO_NUM_22
#define BOARD_TRIG_GPIO_5           GPIO_NUM_14
#define BOARD_TRIG_GPIO_6           GPIO_NUM_0
#define BOARD_TRIG_GPIO_7           GPIO_NUM_1
#define BOARD_TRIG_GPIO_COUNT       8

/* 旧名兼容（= CH5 的 TRIG5）；新硬件下不存在共用线 */
#define BOARD_TRIG_GPIO_SHARED      BOARD_TRIG_GPIO_5

#define BOARD_TRIG_PULSE_MIN_US     1
#define BOARD_TRIG_PULSE_US         20      /* 手册要求 ≥1 µs，实际给 10~50 µs 更稳 */
#define BOARD_TRIG_LEVEL_MIN_MS     1       /* 手册要求 ≥0.8 ms，取 1 ms 余量 */

/* 联动组掩码：新硬件为 0（八条独立线，硬触发下各通道完全独立） */
#define BOARD_TRIG_SHARED_MASK      0x00u

/* ==========================================================================
 * BOOT 按键（SW2 / GPIO9）与现场演示（svc_bootdemo.c）
 * ⚠️ GPIO9 是 strapping 脚：**复位瞬间按住会进下载模式**（芯片固有行为，无法规避）；
 *    运行期把它当普通输入读是安全的。
 *    BOARD_BOOTDEMO_ENABLE=1 时：上电自动给在线通道装波形并切 TRIG 模式，
 *    之后每按一次 BOOT 键 → 全部通道硬触发一次（所有马达同时振一下）。
 * ========================================================================== */
#define BOARD_BOOT_BTN_GPIO         GPIO_NUM_9
#define BOARD_BOOTDEMO_ENABLE       1

/* ==========================================================================
 * 安全默认值（可被 NVS 覆盖，运行时经 SET_SAFETY 可配）
 * ========================================================================== */
#define BOARD_SAFETY_TIMEOUT_MS_DEFAULT     2000
#define BOARD_MAX_SIMUL_FULL_AMP_DEFAULT    2
#define BOARD_HEARTBEAT_TIMEOUT_MS_DEFAULT  5000

/* 满幅判定阈值见 cfg_defaults.h（BOARD_FULL_AMP_*，纯逻辑层也要用） */

/* ==========================================================================
 * 串口链路
 * USB-Serial-JTAG 无波特率概念；备用 UART0 本板未引出，若补焊可切成 1。
 * ========================================================================== */
#define BOARD_PROTO_USE_USB_JTAG    1
#define BOARD_PROTO_UART_NUM        0
#define BOARD_PROTO_UART_TX         GPIO_NUM_16
#define BOARD_PROTO_UART_RX         GPIO_NUM_17
#define BOARD_PROTO_UART_BAUD       921600
#define BOARD_PROTO_RX_BUF          1024
#define BOARD_PROTO_TX_BUF          1024

/* ==========================================================================
 * 芯片内部常量（写固件时的边界）
 * ========================================================================== */
#define BOARD_SRAM_TOTAL_BYTES      3072    /* 3 kB，波形库 + RTP FIFO 共用 */
#define BOARD_WAVE_MAX_WAVES        127
#define BOARD_WAVE_MAX_CHUNK        184     /* WAVE_ADD 单片样本上限 */
#define BOARD_REG_MAX_LEN           64

/* VREG 正常范围（判活标志，TP10/TP11） */
#define BOARD_VREG_MIN_MV           1350
#define BOARD_VREG_MAX_MV           1650

/* ==========================================================================
 * 禁止使用 / 不要使用（写代码时不要碰）
 *   GPIO10/11/24~30 : 模组内部 flash
 *   GPIO15          : strapping 脚，本板悬空
 *   GPIO12/13       : USB PHY（USB-Serial-JTAG）
 *   GPIO9           : BOOT strapping
 * ========================================================================== */

/** 通道 → TRIG GPIO 映射（CH5/6/7 共用 IO14）。 */
gpio_num_t board_trig_gpio(uint8_t ch);

/** 通道 → 是否属于 CH5/6/7 共用组。 */
bool board_trig_is_shared(uint8_t ch);

/** 把所有 TRIG 输出置低（上电、超时、心跳丢失、STOP_ALL 时必须调用）。 */
void board_trig_park_all(void);

/** TRIG 输出低 + 使能内部下拉。**app_main 的第一件事**，不得挪后。 */
void board_trig_safe_init(void);

#ifdef __cplusplus
}
#endif
