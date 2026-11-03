/**
 * @file  drv_tca9548a.h
 * @brief TCA9548A 8 路 I2C 分路器：选通、回读、诊断掩码。
 *
 * ⚠️ **同一时刻只允许选通一个通道**。两颗固定的 0x5A 同时应答会导致总线冲突。
 * 因此对外只暴露单 bit 选通；`TCA_SET_MASK` 诊断命令也只接受 `0x00` 或恰好一个 bit。
 *
 * 本模块的函数**必须在 `svc_channel` 的 I2C 互斥锁内调用**。
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 选通通道 ch（写单 bit `1<<ch`）。内部会跳过重复写。 */
esp_err_t tca_select(uint8_t ch);

/** 直接写控制寄存器（仅限 0x00 或单 bit；用于诊断）。 */
esp_err_t tca_write_mask(uint8_t mask);

/** 回读控制寄存器。 */
esp_err_t tca_read_ctrl(uint8_t *out);

/** 当前固件记录的选通掩码（0 = 未选通）。 */
uint8_t tca_current_mask(void);

/** 硬件复位（GPIO23 脉冲）后清零内部状态。 */
void tca_forget(void);

/** 掩码是否合法（0x00 或恰好一个 bit）。 */
static inline bool tca_mask_is_valid(uint8_t mask)
{
    return mask == 0x00u || (mask != 0x00u && (mask & (uint8_t)(mask - 1u)) == 0u);
}

#ifdef __cplusplus
}
#endif
