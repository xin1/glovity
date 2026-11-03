/**
 * @file  convert.c
 * @brief 参数换算实现。纯逻辑，无 ESP 依赖。
 */

#include "convert.h"

#include "proto_cmd.h"

float conv_f_pre_to_f0(uint8_t f_pre)
{
    if (f_pre == 0) {
        return 0.0f;
    }
    return 24000.0f / (float)f_pre;
}

uint8_t conv_f0_to_f_pre(float f0_hz)
{
    if (f0_hz <= 0.0f) {
        return 255;
    }
    int code = (int)((24000.0f / f0_hz) + 0.5f);
    if (code < 1) {
        code = 1;
    }
    if (code > 255) {
        code = 255;
    }
    return (uint8_t)code;
}

float conv_f0_raw_to_hz(uint8_t mode, uint8_t h, uint8_t l)
{
    uint32_t raw = ((uint32_t)h << 8) | (uint32_t)l;
    if (raw == 0) {
        return 0.0f;
    }
    /* ⚠️ 实测修正（2026-09，AW86234FCR ×4，马达 1040）：
     *   一次性检测寄存器 0x24/0x25 的原始值是"文档公式"的 2 倍，
     *   即该寄存器实际基准是 192000（=24000×8），而不是文档写的 384000（=24000×16）。
     *   证据：
     *     ① 修好 GO 后四路 raw 稳定在 1096~1184；
     *     ② 按 192000 换算 = 162~175 Hz，与 1040 标称 F0=170.5 Hz 吻合；
     *     ③ 170 Hz 与 340 Hz 的 A/B 主观对比中 170 Hz 明显更强 ⇒ 真实 F0 在 170 附近；
     *     ④ 跟踪寄存器 0x26/0x27（另一套机制）按 384000 换算得 152~160 Hz，同量级。
     *   跟踪结果（mode=1）仍按手册的 384000。 */
    const float base = (mode == 1u) ? 384000.0f : 192000.0f;
    return base / (float)raw;
}

float conv_drv_lvl_to_volts(uint8_t drv_lvl, float vdd_v)
{
    return vdd_v * (float)drv_lvl / 128.0f;
}

uint32_t conv_code_to_us(uint8_t code)
{
    /* code / 48000 秒 -> µs，用定点避免浮点： code * 1000000 / 48000 */
    return ((uint32_t)code * 1000000u + 24000u) / 48000u;
}

uint8_t conv_us_to_code(uint32_t us)
{
    uint32_t code = (us * 48000u + 500000u) / 1000000u;
    if (code > 255u) {
        code = 255u;
    }
    return (uint8_t)code;
}

float conv_gain_x(uint8_t code)
{
    return (float)code / 128.0f;
}

float conv_avg_to_vbat(uint16_t avg_data)
{
    return 6.1f * (float)avg_data / 1023.0f;
}

uint8_t conv_d2s_gain(uint8_t code)
{
    static const uint8_t tbl[8] = { 1u, 2u, 4u, 5u, 8u, 10u, 20u, 40u };
    return tbl[code & 0x07u];
}

float conv_avg_to_rl(uint16_t avg_data, uint8_t d2s_gain)
{
    if (d2s_gain == 0u) {
        return 0.0f;
    }
    return 610.0f * (float)avg_data / (1023.0f * (float)d2s_gain);
}

float conv_vbat_ref_volts(uint8_t code)
{
    static const float tbl[8] = { 3.3f, 3.6f, 4.0f, 4.2f, 4.5f, 4.8f, 5.0f, 5.5f };
    return tbl[code & 0x07u];
}

uint32_t conv_rate_hz(uint8_t rate_code)
{
    switch (rate_code) {
    case RATE_24K:     return 24000u;
    case RATE_48K:     return 48000u;
    case RATE_12K:     return 12000u;
    case RATE_12K_ALT: return 12000u;
    default:           return 24000u;
    }
}

uint16_t conv_avg_data(uint8_t reg51, uint8_t reg52)
{
    return (uint16_t)(((uint16_t)(reg51 & 0x03u) << 8) | (uint16_t)reg52);
}

uint16_t conv_adc_data(uint8_t reg51, uint8_t reg53)
{
    return (uint16_t)(((uint16_t)((reg51 >> 2) & 0x03u) << 8) | (uint16_t)reg53);
}
