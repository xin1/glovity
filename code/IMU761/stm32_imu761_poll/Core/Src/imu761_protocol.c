#include "imu761_protocol.h"
#include <string.h>

static uint16_t crc_update(uint16_t crc, const uint8_t *src, uint16_t len) {
    while (len--) {
        crc ^= (uint16_t)(*src++) << 8;
        for (uint8_t bit = 0; bit < 8; ++bit)
            crc = (uint16_t)((crc << 1) ^ ((crc & 0x8000u) ? 0x1021u : 0u));
    }
    return crc;
}

static uint32_t read_u32_le(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static float read_float_le(const uint8_t *p) {
    uint32_t bits = read_u32_le(p);
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static void drop_one(imu761_parser_t *parser) {
    --parser->used;
    memmove(parser->bytes, parser->bytes + 1, parser->used);
    ++parser->discarded;
}

void imu761_build_query(uint8_t address, uint8_t command[5]) {
    command[0] = 0x68;
    command[1] = 0x04;
    command[2] = address;
    command[3] = 0x03;
    command[4] = (uint8_t)(0x04u + address + 0x03u);
}

void imu761_parser_reset(imu761_parser_t *parser) {
    parser->used = 0;
}

int imu761_parser_feed(imu761_parser_t *parser, uint8_t byte, imu761_data_t *out) {
    uint8_t *b = parser->bytes;
    b[parser->used++] = byte;
    while (parser->used) {
        if (b[0] != 0x5A || (parser->used >= 2 && b[1] != 0xA5) ||
            (parser->used >= 3 && b[2] != 0x4C) ||
            (parser->used >= 4 && b[3] != 0x00) ||
            (parser->used >= 7 && b[6] != 0x91)) {
            drop_one(parser);
            continue;
        }
        if (parser->used < IMU761_FRAME_LEN) return 0;
        uint16_t actual = crc_update(0, b, 4);
        actual = crc_update(actual, b + 6, 76);
        uint16_t expected = (uint16_t)b[4] | ((uint16_t)b[5] << 8);
        if (actual != expected) {
            ++parser->crc_errors;
            drop_one(parser);
            continue;
        }
        out->address = b[10];
        out->timestamp_ms = read_u32_le(b + 14);
        out->roll_deg = read_float_le(b + 54);
        out->pitch_deg = read_float_le(b + 58);
        out->yaw_deg = read_float_le(b + 62);
        parser->used = 0;
        return 1;
    }
    return 0;
}
