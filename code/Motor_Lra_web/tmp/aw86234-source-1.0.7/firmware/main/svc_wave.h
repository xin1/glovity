/**
 * @file  svc_wave.h
 * @brief 波形下载会话：WAVE_BEGIN / WAVE_ADD / WAVE_COMMIT / READBACK / GEN。
 *
 * 会话期间该通道对播放类命令返回 ERR_BUSY。
 * COMMIT 的写入顺序**不可变**（协议 §9.8、`04` 文档 §9.3），最后必须回读校验 CRC。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "wave_codec.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t svc_wave_begin(uint8_t ch, uint8_t rate_code, uint8_t wave_count,
                         uint8_t flags, uint8_t *session_id,
                         uint16_t *max_total_samples, uint16_t *sram_free);

esp_err_t svc_wave_add(uint8_t ch, uint8_t wave_id, uint16_t offset,
                       uint16_t total_samples, uint8_t flags,
                       const int8_t *samples, uint8_t len, uint16_t *offset_ack);

esp_err_t svc_wave_commit(uint8_t ch, uint8_t lib_version, uint16_t *total_bytes,
                          uint16_t *lib_crc16, uint16_t *base_addr,
                          wave_lib_t *lib_out);

esp_err_t svc_wave_abort(uint8_t ch);
esp_err_t svc_wave_erase(uint8_t ch);

/** 从芯片 SRAM 回读头块并解析出地址表（比固件缓存可信）。 */
esp_err_t svc_wave_get_library(uint8_t ch, uint8_t *lib_version,
                               uint8_t *wave_count, uint16_t *total_bytes,
                               uint16_t *base_addr, wave_lib_t *lib_out);

/** 回读波形数据（用于"下发是否真的写对"的校验）。 */
esp_err_t svc_wave_readback(uint8_t ch, uint8_t wave_id, uint16_t offset,
                            uint8_t len, int8_t *out);

/** 固件内置波形合成，加入当前会话。 */
esp_err_t svc_wave_gen(uint8_t ch, uint8_t wave_id, uint8_t shape,
                       uint16_t freq_hz, uint8_t amplitude, uint16_t cycles,
                       uint8_t rate_code, uint16_t *generated);

/** 便捷：把 ASCII `wave` 命令用的一次性"生成 + 提交 + RAM 播放"全流程。 */
esp_err_t svc_wave_quick_play(uint8_t ch, uint8_t wave_id, uint8_t shape,
                              uint16_t freq_hz, uint8_t amplitude, uint16_t cycles,
                              uint16_t *generated);

#ifdef __cplusplus
}
#endif
