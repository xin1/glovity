#pragma once
#include "driver/gpio.h"
#define BOARD_CHANNEL_COUNT 5
#define BOARD_I2C_XFER_TIMEOUT_MS 5
#define BOARD_REG_MAX_LEN 64
#define BOARD_SRAM_TOTAL_BYTES 3072
#define BOARD_AW_CHIPID_EXPECTED 0x2340
#define BOARD_TRIG_GPIO_SHARED GPIO_NUM_12
#define BOARD_TRIG_SHARED_MASK 0x1f
// TELEX pins only; never probe alternate pins on the role straps.
#define BOARD_MOTOR_SDA GPIO_NUM_6
#define BOARD_MOTOR_SCL GPIO_NUM_7
#define BOARD_TCA_RST_GPIO GPIO_NUM_21
#define BOARD_MOTOR_POWER GPIO_NUM_4

#define BOARD_AW_SOFTRST_VAL 0xAA
