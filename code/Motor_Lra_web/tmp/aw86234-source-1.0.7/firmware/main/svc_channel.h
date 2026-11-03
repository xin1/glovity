/**
 * @file  svc_channel.h
 * @brief 8 通道抽象：在线表、每通道状态机与 Profile 缓存，
 *        以及**唯一**的原子访问口径 `ch_xfer()`。
 *
 * ⚠️ 设计纪律（`04` 文档 §5.4）：`ch_xfer()` 内部完成
 *   取锁 → 写 TCA `1<<ch` → 目标传输 → 放锁，
 * **不向任何调用者暴露裸的选通接口**。绕过它的通路都是 bug。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "aw_profile.h"
#include "cfg_defaults.h"
#include "esp_err.h"
#include "wave_codec.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CH_ABSENT = 0,
    CH_IDLE,
    CH_ARMED,
    CH_PLAYING,
    CH_BUSY,
    CH_ERROR,
} ch_state_t;

typedef struct {
    /* ---- 基本状态 ---- */
    ch_state_t state;
    uint8_t    present;
    uint16_t   chipid;
    uint8_t    vreg_ok;             /* 1 = LDO_OK；0xFF = 读不到 */
    uint8_t    mode;                /* 0=RAM 1=RTP 2=CONT 3=TRIG */
    bool       playing;
    uint8_t    glb_state;
    uint8_t    last_err;
    uint32_t   play_started_ms;
    uint32_t   play_elapsed_ms;

    /* ---- 播放安全超时 ---- */
    bool       safety_armed;
    uint32_t   safety_deadline_ms;
    uint16_t   safety_timeout_ms;

    /* ---- 当前生效的 Profile（软复位后必须重放） ---- */
    aw_profile_t profile;

    /* ---- 波形下载会话 ---- */
    bool       wave_session;
    uint8_t    wave_session_id;
    uint8_t    wave_rate_code;
    uint8_t    wave_count;
    uint16_t   wave_len[WAVE_LIB_MAX_WAVES];
    uint16_t   wave_total_samples;
    int8_t     wave_buf[WAVE_BUF_BYTES];

    /* ---- 异步测量 ---- */
    bool       f0_pending;
    uint8_t    f0_mode;
    uint32_t   f0_deadline_ms;
    uint8_t    f0_valid;
    uint8_t    f0_raw_h;
    uint8_t    f0_raw_l;
    uint16_t   f0_hz_x10;

    /* ---- 长操作占用标记（扫描 / 自检 / 测试项） ---- */
    bool       long_op;
} ch_ctx_t;

void        svc_channel_init(void);
ch_ctx_t   *ch_ctx(uint8_t ch);
bool        ch_valid(uint8_t ch);

/**
 * **唯一的原子访问口径**：选通 ch + 传输 + （可选）读回，全程持同一把锁。
 *
 * @param wr       要写的字节（至少 1 字节，通常是寄存器地址）
 * @param rd       读缓冲，可空
 * @return ESP_OK / ESP_ERR_TIMEOUT / ESP_ERR_INVALID_STATE / 驱动错误
 */
esp_err_t ch_xfer(uint8_t ch, const uint8_t *wr, size_t wr_len,
                  uint8_t *rd, size_t rd_len, uint32_t timeout_ms);

/** 选通并执行一段自定义操作（仍是同一把锁内）。用于连续写等多事务序列。 */
esp_err_t ch_lock(void);
void      ch_unlock(void);

/** 在线位图。 */
uint8_t ch_present_mask(void);

/** 记录通道错误码（同时更新 last_err 与通道状态）。 */
void ch_note_error(uint8_t ch, uint8_t err);

/** 标记通道为"长操作占用"，用于 ERR_BUSY 判定。 */
bool ch_is_busy(uint8_t ch);
void ch_set_long_op(uint8_t ch, bool busy);

/* ---- 播放安全超时 ---- */
void     ch_arm_safety(uint8_t ch, uint16_t timeout_ms);
void     ch_disarm_safety(uint8_t ch);
int32_t  ch_safety_remaining_ms(uint8_t ch);   /* <0 表示已超时 */
bool     ch_any_safety_armed(void);
void     ch_mark_play_started(uint8_t ch);
void     ch_mark_play_stopped(uint8_t ch);

/** 用当前 Profile 重新配置某通道（软复位后重放）。 */
esp_err_t ch_reapply_profile(uint8_t ch);

#ifdef __cplusplus
}
#endif
