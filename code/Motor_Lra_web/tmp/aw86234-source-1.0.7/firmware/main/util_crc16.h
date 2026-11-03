/**
 * @file  util_crc16.h
 * @brief CRC16-CCITT-FALSE 的独立入口（实现复用 proto_frame.c，避免重复表）。
 *
 * 抽出来是为了让"波形库 CRC""SRAM 回读校验"等业务代码不必依赖帧层头文件。
 */

#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UTIL_CRC16_INIT     0xFFFFu
#define UTIL_CRC16_SELFTEST 0x29B1u   /* "123456789" 的期望值 */

/** 增量式 CRC16-CCITT-FALSE（poly=0x1021, init=0xFFFF, 无反射）。 */
uint16_t util_crc16(const uint8_t *data, size_t len, uint16_t crc);

/** 一次性计算（init = 0xFFFF）。 */
static inline uint16_t util_crc16_block(const uint8_t *data, size_t len)
{
    return util_crc16(data, len, UTIL_CRC16_INIT);
}

#ifdef __cplusplus
}
#endif
