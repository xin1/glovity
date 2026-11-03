/**
 * @file  svc_measure.h
 * @brief 测量服务：F0 检测（异步）、DET_READ（ADC/电池电压/LRA 阻抗）、
 *        SNAPSHOT、MON 周期监视。
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t present;
    uint8_t chipid_h;
    uint8_t chipid_l;
    uint8_t mode;
    uint8_t playing;
    uint8_t glb_state;
    uint8_t sysst;
    uint8_t sysst2;
    uint8_t reg24;      /* F_LRA_F0_H */
    uint8_t reg25;      /* F_LRA_F0_L */
    uint8_t reg26;      /* CONT_F0_H  */
    uint8_t reg27;      /* CONT_F0_L  */
    uint8_t reg51;      /* DETRD1 */
    uint8_t reg52;      /* DETRD2 */
    uint8_t reg53;      /* DETRD3 */
} snapshot_t;

/** F0_START：立即返回，结果通过 EVT_MEASURE_RESULT 或 F0_RESULT 轮询获取。 */
esp_err_t svc_measure_f0_start(uint8_t ch, uint16_t timeout_ms, uint8_t mode,
                               uint8_t *started);

/** F0_RESULT：读回缓存结果（含换算与合理性判定；一次性检测基准 192000）。 */
esp_err_t svc_measure_f0_result(uint8_t ch, uint8_t *valid, uint8_t *mode,
                                uint8_t *raw_h, uint8_t *raw_l, uint16_t *hz_x10);

/** DET_READ：同步，约 3~5 ms。 */
esp_err_t svc_measure_det_read(uint8_t ch, uint8_t seq, uint16_t *adc_data,
                               uint16_t *avg_data, uint16_t *value_x100,
                               uint8_t *valid, uint8_t *d2s_gain);

esp_err_t svc_measure_snapshot(uint8_t ch, snapshot_t *out);

/** MON_START / MON_STOP。 */
esp_err_t svc_measure_mon_start(uint8_t chan_mask, uint16_t period_ms, uint16_t fields);
esp_err_t svc_measure_mon_stop(uint8_t chan_mask);
void      svc_measure_mon_stop_all(void);

/** 由监视任务周期性调用。 */
void svc_measure_poll(uint32_t now_ms);

/** 是否有未完成的测量（供 GET_STATUS 的 active_mask）。 */
uint8_t svc_measure_active_mask(void);

#ifdef __cplusplus
}
#endif
