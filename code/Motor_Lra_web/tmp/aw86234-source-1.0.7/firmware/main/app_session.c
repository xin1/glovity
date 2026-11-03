/**
 * @file  app_session.c
 * @brief 会话与安全实现。
 */

#include "app_session.h"

#include <string.h>

#include "app_protocol.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "drv_aw8623x.h"
#include "esp_err.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "svc_measure.h"
#include "svc_play.h"
#include "util_log.h"

#define NVS_NAMESPACE   "awtest"
#define NVS_KEY_SAFETY  "safety"

static app_safety_cfg_t s_cfg;
static uint32_t s_last_rx_ms = 0;
static bool     s_hb_enabled = false;
static uint8_t  s_safety_state = SAFETY_NORMAL;
static uint8_t  s_last_err = ERR_OK;
static uint8_t  s_active_mask = 0;
static bool     s_ascii = true;

static inline uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void cfg_defaults(void)
{
    s_cfg.play_timeout_ms = BOARD_SAFETY_TIMEOUT_MS_DEFAULT;
    s_cfg.max_simul_full_amp = BOARD_MAX_SIMUL_FULL_AMP_DEFAULT;
    s_cfg.heartbeat_timeout_ms = BOARD_HEARTBEAT_TIMEOUT_MS_DEFAULT;
    s_cfg.flags = SAFETY_FLAG_ALLOW_OVERRIDE;
}

static bool cfg_valid(const app_safety_cfg_t *c)
{
    return c->play_timeout_ms >= 50u && c->play_timeout_ms <= 60000u &&
           c->max_simul_full_amp >= 1u && c->max_simul_full_amp <= 8u &&
           c->heartbeat_timeout_ms >= 200u && c->heartbeat_timeout_ms <= 60000u;
}

static void cfg_load_nvs(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) {
        return;
    }
    app_safety_cfg_t tmp;
    size_t len = sizeof(tmp);
    esp_err_t err = nvs_get_blob(h, NVS_KEY_SAFETY, &tmp, &len);
    nvs_close(h);
    /* NVS 里存的必须是合法值，否则宁可回落到默认值（`04` 文档 §8） */
    if (err == ESP_OK && len == sizeof(tmp) && cfg_valid(&tmp)) {
        s_cfg = tmp;
        LOGI("已从 NVS 载入安全参数：超时 %u ms，满幅上限 %u，心跳 %u ms",
             s_cfg.play_timeout_ms, s_cfg.max_simul_full_amp, s_cfg.heartbeat_timeout_ms);
    } else if (err == ESP_OK) {
        LOGW("NVS 中的安全参数不合法，已回落到默认值");
    }
}

static void cfg_save_nvs(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        return;
    }
    (void)nvs_set_blob(h, NVS_KEY_SAFETY, &s_cfg, sizeof(s_cfg));
    (void)nvs_commit(h);
    nvs_close(h);
}

void app_session_init(void)
{
    cfg_defaults();
    cfg_load_nvs();
    s_last_rx_ms = now_ms();
    s_hb_enabled = false;
    s_safety_state = SAFETY_NORMAL;
    s_last_err = ERR_OK;
    s_active_mask = 0;
    s_ascii = true;
}

void app_session_get_safety(app_safety_cfg_t *out)
{
    if (out) {
        *out = s_cfg;
    }
}

esp_err_t app_session_set_safety(const app_safety_cfg_t *cfg)
{
    if (cfg == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    app_safety_cfg_t tmp = *cfg;
    /* 0 表示"用固件默认"（协议 §9.1） */
    if (tmp.play_timeout_ms == 0) {
        tmp.play_timeout_ms = BOARD_SAFETY_TIMEOUT_MS_DEFAULT;
    }
    if (tmp.heartbeat_timeout_ms == 0) {
        tmp.heartbeat_timeout_ms = BOARD_HEARTBEAT_TIMEOUT_MS_DEFAULT;
    }
    if (!cfg_valid(&tmp)) {
        return ESP_ERR_INVALID_ARG;
    }
    s_cfg = tmp;
    cfg_save_nvs();
    LOGI("安全参数已更新：超时 %u ms，满幅上限 %u，心跳 %u ms，flags 0x%02X",
         s_cfg.play_timeout_ms, s_cfg.max_simul_full_amp,
         s_cfg.heartbeat_timeout_ms, s_cfg.flags);
    return ESP_OK;
}

void app_session_note_frame(void)
{
    s_last_rx_ms = now_ms();
    s_hb_enabled = true;
}

void app_session_enable_heartbeat(bool enable)
{
    s_hb_enabled = enable;
    s_last_rx_ms = now_ms();
}

uint32_t app_session_heartbeat_age_ms(void)
{
    return now_ms() - s_last_rx_ms;
}

uint8_t app_session_safety_state(void)
{
    return s_safety_state;
}

void app_session_set_safety_state(uint8_t state)
{
    s_safety_state = state;
}

void app_session_note_error(uint8_t err)
{
    if (err != ERR_OK) {
        s_last_err = err;
    }
}

uint8_t app_session_last_err(void)
{
    return s_last_err;
}

void app_session_clear_errors(void)
{
    s_last_err = ERR_OK;
    if (s_safety_state != SAFETY_BUS_LOCKED) {
        s_safety_state = SAFETY_NORMAL;
    }
}

uint8_t app_session_active_mask(void)
{
    return s_active_mask;
}

void app_session_set_active_mask(uint8_t mask)
{
    s_active_mask = mask;
}

void app_session_add_active(uint8_t ch)
{
    if (ch < BOARD_CHANNEL_COUNT) {
        s_active_mask |= (uint8_t)(1u << ch);
    }
}

void app_session_del_active(uint8_t ch)
{
    if (ch < BOARD_CHANNEL_COUNT) {
        s_active_mask &= (uint8_t)~(1u << ch);
    }
}

uint32_t app_session_uptime_ms(void)
{
    return now_ms();
}

bool app_session_ascii_mode(void)
{
    return s_ascii;
}

void app_session_set_ascii_mode(bool ascii)
{
    s_ascii = ascii;
}

/* ------------------------------------------------------------------ */
/* 安全任务                                                            */
/* ------------------------------------------------------------------ */

/** S-1：播放安全超时。 */
static void check_play_timeout(uint32_t now)
{
    svc_play_tick(now);
}

/** S-3：心跳看门狗。 */
static void check_heartbeat(void)
{
    if (!s_hb_enabled) {
        return;
    }
    if (app_session_heartbeat_age_ms() < s_cfg.heartbeat_timeout_ms) {
        return;
    }
    if (s_safety_state == SAFETY_HEARTBEAT_LOST) {
        return;     /* 已在安全态，不重复上报 */
    }

    LOGW("心跳丢失（%u ms 未收到任何合法帧）：停止全部通道并进入安全态",
         s_cfg.heartbeat_timeout_ms);
    uint8_t stopped = 0;
    svc_play_stop_all(STOP_MODE_IMMEDIATE, &stopped);
    svc_play_trig_park();
    svc_measure_mon_stop_all();
    s_safety_state = SAFETY_HEARTBEAT_LOST;
    app_session_note_error(ERR_HEARTBEAT_LOST);
    evt_safety_trip(TRIP_HEARTBEAT_LOST, stopped, 0);
    evt_error(ERR_HEARTBEAT_LOST, 0, "心跳丢失：固件已停止全部通道并进入安全态");
    /* 重新计时，避免每 10 ms 重复触发 */
    s_last_rx_ms = now_ms();
    s_hb_enabled = false;
}

void app_session_safety_tick(uint32_t now)
{
    /* 1) 播放安全超时 */
    check_play_timeout(now);
    /* 2) 播放结束轮询（产生 EVT_PLAY_DONE） */
    svc_play_poll(now);
    /* 3) 心跳看门狗放在最后：它会停机，前面的轮询结果更有价值 */
    check_heartbeat();
}
