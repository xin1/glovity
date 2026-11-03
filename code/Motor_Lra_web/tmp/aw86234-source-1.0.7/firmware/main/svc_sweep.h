/**
 * @file  svc_sweep.h
 * @brief 幅度/频率扫描与内置测试项（异步执行，进度经事件上报）。
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** SWEEP_AMP：mode 0 = CONT（改 DRV1/2_LVL），1 = RAM（改 PLAYCFG2 GAIN）。 */
esp_err_t svc_sweep_amp(uint8_t ch, uint8_t mode, uint8_t from_lvl, uint8_t to_lvl,
                        uint8_t step, uint16_t dwell_ms, uint16_t settle_ms,
                        uint8_t *accepted, uint8_t *total_steps);

/** SWEEP_FREQ：CONT 模式下扫描 F_PRE。 */
esp_err_t svc_sweep_freq(uint8_t ch, uint16_t from_hz, uint16_t to_hz,
                         uint16_t step_hz, uint16_t dwell_ms,
                         uint8_t *accepted, uint8_t *total_steps);

esp_err_t svc_sweep_abort(void);

/** 内置测试项（编号与 `reference/06_固件配置与测试计划.md` 的 A/B/C 矩阵对齐）。 */
esp_err_t svc_test_run(uint16_t test_id, const uint8_t *params, uint8_t params_len,
                       uint8_t *accepted);
esp_err_t svc_test_abort(void);

/** 由监视任务周期性调用。 */
void svc_sweep_poll(uint32_t now_ms);

bool svc_sweep_active(void);
bool svc_test_active(void);

#ifdef __cplusplus
}
#endif
