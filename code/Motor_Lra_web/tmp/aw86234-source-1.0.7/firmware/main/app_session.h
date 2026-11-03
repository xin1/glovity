/**
 * @file  app_session.h
 * @brief 会话状态：安全参数（NVS 持久化）、心跳看门狗、播放安全超时、并发限制。
 *
 * 三道保险（协议 §11、`04` 文档 §8）：
 *   S-1 播放安全超时（默认 2000 ms，可配）
 *   S-2 同时满幅路数限制（默认 2，可配；override 需显式）
 *   S-3 心跳看门狗（默认 5000 ms，可配；断连/主机进程死掉也会触发）
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t play_timeout_ms;
    uint8_t  max_simul_full_amp;
    uint16_t heartbeat_timeout_ms;
    uint8_t  flags;             /* bit0 = 允许 override；bit1 = 总线错误自动锁定 */
} app_safety_cfg_t;

#define SAFETY_FLAG_ALLOW_OVERRIDE      0x01u
#define SAFETY_FLAG_LOCK_ON_BUS_ERROR   0x02u

void    app_session_init(void);
void    app_session_get_safety(app_safety_cfg_t *out);
esp_err_t app_session_set_safety(const app_safety_cfg_t *cfg);

/** 收到**任何合法帧**就刷新心跳时间戳（协议 §11）。 */
void     app_session_note_frame(void);
void     app_session_enable_heartbeat(bool enable);
uint32_t app_session_heartbeat_age_ms(void);

uint8_t  app_session_safety_state(void);
void     app_session_set_safety_state(uint8_t state);

void     app_session_note_error(uint8_t err);
uint8_t  app_session_last_err(void);
void     app_session_clear_errors(void);

uint8_t  app_session_active_mask(void);
void     app_session_set_active_mask(uint8_t mask);
void     app_session_add_active(uint8_t ch);
void     app_session_del_active(uint8_t ch);

uint32_t app_session_uptime_ms(void);

/** 由安全任务每 10 ms 调用一次。 */
void app_session_safety_tick(uint32_t now_ms);

/** 是否处于 ASCII 引导模式。 */
bool     app_session_ascii_mode(void);
void     app_session_set_ascii_mode(bool ascii);

#ifdef __cplusplus
}
#endif
