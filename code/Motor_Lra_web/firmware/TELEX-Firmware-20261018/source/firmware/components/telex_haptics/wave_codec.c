/**
 * @file  wave_codec.c
 * @brief 波形库规划/打包/合成实现。纯逻辑，无 ESP 依赖。
 */

#include "wave_codec.h"

#include <math.h>
#include <string.h>

#include "convert.h"
#include "proto_cmd.h"
#include "util_crc16.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

uint16_t wave_lib_avail_bytes(uint16_t base_addr)
{
    if ((uint32_t)base_addr >= WAVE_LIB_MAX_BYTES) {
        return 0;
    }
    return (uint16_t)(WAVE_LIB_MAX_BYTES - (uint32_t)base_addr);
}

uint16_t wave_lib_max_samples(uint16_t base_addr, uint8_t wave_count)
{
    uint16_t avail = wave_lib_avail_bytes(base_addr);
    uint32_t overhead = 1u + 4u * (uint32_t)wave_count;   /* 版本字节 + 头块 */
    if (avail <= overhead) {
        return 0;
    }
    return (uint16_t)(avail - overhead);
}

int wave_lib_plan(uint16_t base_addr, uint8_t lib_version,
                  const wave_input_t *waves, uint8_t n, wave_lib_t *out)
{
    if (out == NULL || waves == NULL || n == 0u) {
        return ERR_BAD_PARAM;
    }
    if (n > WAVE_LIB_MAX_WAVES) {
        return ERR_TOO_MANY_CHUNKS;
    }

    uint16_t avail = wave_lib_avail_bytes(base_addr);
    uint32_t total = 1u + 4u * (uint32_t)n;
    for (uint8_t k = 0; k < n; k++) {
        if (waves[k].samples == NULL && waves[k].len > 0u) {
            return ERR_BAD_PARAM;
        }
        total += waves[k].len;
    }
    if (total > avail) {
        return ERR_SRAM_OVERFLOW;
    }

    memset(out, 0, sizeof(*out));
    out->lib_version = lib_version;
    out->wave_count  = n;
    out->base_addr   = base_addr;
    out->total_bytes = (uint16_t)total;

    /* 组库算法（02 文档 §5）：
     *   offset = 1 + 4N
     *   for k: start = BASE + offset; offset += len(k); end = BASE + offset - 1
     * 端点语义：end = start + len - 1（含端点）。*/
    uint32_t offset = 1u + 4u * (uint32_t)n;
    for (uint8_t k = 0; k < n; k++) {
        uint16_t start = (uint16_t)(base_addr + offset);
        offset += waves[k].len;
        uint16_t end = (uint16_t)(base_addr + offset - 1u);
        out->start[k] = start;
        out->end[k]   = end;
    }
    return 0;
}

size_t wave_lib_pack(const wave_lib_t *lib, const wave_input_t *waves, uint8_t n,
                     uint8_t *out, size_t out_cap)
{
    if (lib == NULL || waves == NULL || out == NULL) {
        return 0;
    }
    if (lib->total_bytes > out_cap) {
        return 0;
    }

    out[0] = lib->lib_version;
    size_t pos = 1;
    for (uint8_t k = 0; k < n; k++) {
        /* 每头 4 字节：起始地址高/低，结束地址高/低（高字节在前） */
        out[pos++] = (uint8_t)(lib->start[k] >> 8);
        out[pos++] = (uint8_t)(lib->start[k] & 0xFFu);
        out[pos++] = (uint8_t)(lib->end[k] >> 8);
        out[pos++] = (uint8_t)(lib->end[k] & 0xFFu);
    }
    for (uint8_t k = 0; k < n; k++) {
        if (waves[k].len > 0u) {
            memcpy(&out[pos], waves[k].samples, waves[k].len);
            pos += waves[k].len;
        }
    }
    return pos;
}

int wave_lib_parse_header(uint16_t base, const uint8_t *head, size_t len, wave_lib_t *out)
{
    if (!head || !out || base >= WAVE_LIB_MAX_BYTES || len < 5u) {
        return ERR_BAD_PARAM;
    }
    wave_lib_t lib = {0};
    lib.base_addr = base;
    lib.lib_version = head[0];
    uint16_t first = (uint16_t)((head[1] << 8) | head[2]);
    uint16_t end = (uint16_t)((head[3] << 8) | head[4]);
    if (!first && !end) {
        lib.total_bytes = 1;
        *out = lib;
        return 0;
    }
    if (first <= base + 1u || first >= WAVE_LIB_MAX_BYTES) { return ERR_BAD_PARAM; }
    uint16_t table_bytes = (uint16_t)(first - base - 1u);
    if (table_bytes % 4u || table_bytes / 4u > WAVE_LIB_MAX_WAVES ||
        1u + table_bytes > len) { return ERR_BAD_PARAM; }
    lib.wave_count = (uint8_t)(table_bytes / 4u);
    uint16_t cursor = first;
    for (uint8_t i = 0; i < lib.wave_count; i++) {
        const uint8_t *h = head + 1u + 4u * i;
        uint16_t start = (uint16_t)((h[0] << 8) | h[1]);
        end = (uint16_t)((h[2] << 8) | h[3]);
        if (start != cursor || end < start || end >= WAVE_LIB_MAX_BYTES) {
            return ERR_BAD_PARAM;
        }
        lib.start[i] = start;
        lib.end[i] = end;
        cursor = (uint16_t)(end + 1u);
    }
    lib.total_bytes = (uint16_t)(cursor - base);
    *out = lib;
    return 0;
}

uint16_t wave_lib_crc(const uint8_t *data, size_t len)
{
    return util_crc16_block(data, len);
}

/* ==========================================================================
 * 内置波形合成
 * ========================================================================== */

static int8_t clamp_s8(int v)
{
    if (v > 127) {
        return 127;
    }
    if (v < -128) {
        return -128;
    }
    return (int8_t)v;
}

int wave_gen_samples(uint8_t shape, uint16_t freq_hz, uint8_t amplitude,
                     uint16_t cycles, uint8_t rate_code,
                     int8_t *out, size_t out_cap, uint16_t *out_len,
                     uint16_t *actual_hz)
{
    if (out == NULL || out_len == NULL || freq_hz == 0u || cycles == 0u) {
        return ERR_BAD_PARAM;
    }
    if (amplitude > 127u) {
        amplitude = 127u;
    }

    uint32_t rate = conv_rate_hz(rate_code);
    /* 每周期样本数必须是整数，除不尽时向上取整（协议 §9.8） */
    uint32_t spc = (rate + freq_hz - 1u) / freq_hz;
    if (spc < 2u) {
        spc = 2u;
    }
    uint32_t total = spc * (uint32_t)cycles;
    if (total > out_cap) {
        return ERR_SRAM_OVERFLOW;
    }

    for (uint16_t c = 0; c < cycles; c++) {
        for (uint32_t i = 0; i < spc; i++) {
            double phase = (double)i / (double)spc;      /* 0..1 */
            double v;
            switch (shape) {
            case SHAPE_SQUARE:
                v = (phase < 0.5) ? 1.0 : -1.0;
                break;
            case SHAPE_TRIANGLE:
                v = (phase < 0.5) ? (4.0 * phase - 1.0) : (3.0 - 4.0 * phase);
                break;
            case SHAPE_SAWTOOTH:
                v = 2.0 * phase - 1.0;
                break;
            case SHAPE_SINE_RISE: {
                double env = 0.2 + 0.8 * ((double)c / (double)cycles);
                v = sin(2.0 * M_PI * phase) * env;
                break;
            }
            case SHAPE_SINE_FALL: {
                double env = 1.0 - 0.8 * ((double)c / (double)cycles);
                v = sin(2.0 * M_PI * phase) * env;
                break;
            }
            case SHAPE_CLICK: {
                /* 单周期指数衰减：整段就是一次点击 */
                double t = (double)(c * spc + i) / (double)total;
                v = sin(2.0 * M_PI * t * (double)cycles) * exp(-8.0 * t);
                break;
            }
            case SHAPE_SINE:
            default:
                v = sin(2.0 * M_PI * phase);
                break;
            }
            out[(size_t)c * spc + i] = clamp_s8((int)(v * (double)amplitude));
        }
    }

    *out_len = (uint16_t)total;
    if (actual_hz != NULL) {
        *actual_hz = (uint16_t)(rate / spc);
    }
    return 0;
}
