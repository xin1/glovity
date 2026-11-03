/**
 * @file  drv_tca9548a.c
 * @brief 分路器实现。选通状态是**全局共享资源**，只能在持锁时改。
 */

#include "drv_tca9548a.h"

#include "cfg_board.h"
#include "drv_i2c_bus.h"
#include "proto_cmd.h"
#include "util_log.h"

/** 固件记录的"当前选通掩码"，避免重复写。0 = 未选通。 */
static uint8_t s_cur_mask = 0;

esp_err_t tca_select(uint8_t ch)
{
    if (ch >= BOARD_CHANNEL_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t mask = (uint8_t)(1u << ch);
    if (mask == s_cur_mask) {
        return ESP_OK;
    }
    esp_err_t err = bus_tca_write_byte(mask, BOARD_I2C_XFER_TIMEOUT_MS);
    if (err == ESP_OK) {
        s_cur_mask = mask;
    } else {
        s_cur_mask = 0;     /* 状态未知，保守置 0 */
    }
    return err;
}

esp_err_t tca_write_mask(uint8_t mask)
{
    if (!tca_mask_is_valid(mask)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = bus_tca_write_byte(mask, BOARD_I2C_XFER_TIMEOUT_MS);
    s_cur_mask = (err == ESP_OK) ? mask : 0;
    return err;
}

esp_err_t tca_read_ctrl(uint8_t *out)
{
    if (out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    return bus_tca_read_byte(out, BOARD_I2C_XFER_TIMEOUT_MS);
}

uint8_t tca_current_mask(void)
{
    return s_cur_mask;
}

void tca_forget(void)
{
    s_cur_mask = 0;
}
