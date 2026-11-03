/**
 * @file  util_crc16.c
 * @brief 复用 proto_frame.c 里的查表实现，避免两份 CRC 表。
 */

#include "util_crc16.h"

#include "proto_frame.h"

uint16_t util_crc16(const uint8_t *data, size_t len, uint16_t crc)
{
    return proto_crc16_ccitt(data, len, crc);
}
