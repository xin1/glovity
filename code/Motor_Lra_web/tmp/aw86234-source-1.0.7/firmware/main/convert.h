/**
 * @file  convert.h
 * @brief 参数换算（纯逻辑，无 ESP 依赖，可在 PC 上单测）。
 *
 * 公式全部取自 `02_AW8623X寄存器与模式参考.md` §6 与 `03` 文档 §9.9，
 * 与上位机 `awtest/waveform/convert.py` 必须一致。
 */

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** F0 = 24000 / F_PRE  (Hz)。F_PRE = 0 视为无效，返回 0。 */
float conv_f_pre_to_f0(uint8_t f_pre);

/** F_PRE = round(24000 / F0)，裁剪到 1..255。 */
uint8_t conv_f0_to_f_pre(float f0_hz);

/** F0 换算（Hz）。mode: 0 = F_LRA_F0（0x24/0x25），1 = CONT_F0（0x26/0x27）。
 *  raw = 0 返回 0（除零，必为无效）。
 *  ⚠️ 实测修正：一次性检测基准是 192000（文档写 384000，差 2 倍）；
 *     跟踪结果仍为 384000。详见 convert.c 的实现注释。 */
float conv_f0_raw_to_hz(uint8_t mode, uint8_t h, uint8_t l);

/** 空载输出电压 = VDD × DRV_LVL / 128（软件调节模式）。 */
float conv_drv_lvl_to_volts(uint8_t drv_lvl, float vdd_v);

/** 时间 = code / 48000 秒 -> µs。 */
uint32_t conv_code_to_us(uint8_t code);

/** 由 µs 反算 code（四舍五入，裁剪 0..255）。 */
uint8_t conv_us_to_code(uint32_t us);

/** 增益倍率 = code / 128。 */
float conv_gain_x(uint8_t code);

/** VDD = 6.1 × AVG_DATA / 1023  (V)。 */
float conv_avg_to_vbat(uint16_t avg_data);

/** RL = 610 × AVG_DATA / (1023 × D2S_GAIN)  (Ω)。D2S_GAIN 见 0x50 bit2:0。 */
float conv_avg_to_rl(uint16_t avg_data, uint8_t d2s_gain);

/** D2S_GAIN 码值 -> 倍率（b000=1 b001=2 b010=4 b011=5 b100=8 b101=10 b110=20 b111=40）。 */
uint8_t conv_d2s_gain(uint8_t code);

/** VBAT_REF 码值 -> 电压（V）。 */
float conv_vbat_ref_volts(uint8_t code);

/** 采样率编码 -> Hz。 */
uint32_t conv_rate_hz(uint8_t rate_code);

/** 把 ADC/AVG 的 H/L 位域拼成完整值。 */
uint16_t conv_avg_data(uint8_t reg51, uint8_t reg52);
uint16_t conv_adc_data(uint8_t reg51, uint8_t reg53);

#ifdef __cplusplus
}
#endif
