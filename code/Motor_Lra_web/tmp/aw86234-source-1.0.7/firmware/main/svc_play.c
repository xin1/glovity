/**
 * @file  svc_play.c
 * @brief 播放服务实现。
 */

#include "svc_play.h"
#include "live_codec.h"

#include <string.h>

#include "app_protocol.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "drv_aw8623x.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "util_log.h"

static uint32_t s_last_poll_ms = 0;

/* Playback transactions use a separate lock, always before the I2C lock.
 * A pending DONE survives a failed STOP (SYSINT itself is read-to-clear).
 * Pulse waits release this lock so watchdogs can stop/cancel a sequence. */
static SemaphoreHandle_t s_play_mutex;
static uint32_t s_generation[BOARD_CHANNEL_COUNT];
static uint32_t s_deadline[BOARD_CHANNEL_COUNT];
static bool s_timed[BOARD_CHANNEL_COUNT], s_pending[BOARD_CHANNEL_COUNT];
static bool s_burst[BOARD_CHANNEL_COUNT], s_trip_reported[BOARD_CHANNEL_COUNT];
static uint8_t s_finish_reason[BOARD_CHANNEL_COUNT];
static uint8_t s_boot_mask;
/* Independent live lease. Ordinary PLAY keeps its original timeout semantics. */
static live_config_t s_live;
static bool s_tactile;
static bool s_tactile_starting;
static bool s_tactile_braking;
static uint32_t s_tactile_brake_until;
static uint16_t s_tactile_seq;
static uint8_t s_tactile_levels[8];
static uint8_t s_live_mask;
static bool s_live_transition;
static uint32_t s_live_deadline, s_live_started;
static uint64_t s_live_phase_us[BOARD_CHANNEL_COUNT];
static uint8_t s_live_last_amp[BOARD_CHANNEL_COUNT];
static bool live_cancel(uint8_t reason);
static void live_tick(uint32_t now);



void svc_play_init(void)
{
    s_play_mutex = xSemaphoreCreateRecursiveMutex();
    configASSERT(s_play_mutex != NULL);
}
void svc_play_lock(void) { xSemaphoreTakeRecursive(s_play_mutex, portMAX_DELAY); }
void svc_play_unlock(void) { xSemaphoreGiveRecursive(s_play_mutex); }
static void unlock_guard(int *unused) { (void)unused; svc_play_unlock(); }
#define PLAY_GUARD int guard __attribute__((cleanup(unlock_guard), unused)) = (svc_play_lock(), 0)

static void reset_run(uint8_t ch)
{
    ++s_generation[ch];
    s_timed[ch] = s_pending[ch] = s_burst[ch] = s_trip_reported[ch] = false;
}

static bool finish_run(uint8_t ch, uint8_t reason)
{
    if (s_live_mask & (1u << ch)) { return live_cancel(reason); }
    ch_ctx_t *c = ch_ctx(ch);
    if (s_burst[ch]) { ++s_generation[ch]; s_burst[ch] = false; }
    gpio_set_level(board_trig_gpio(ch), 0);
    if (aw_stop(ch, STOP_MODE_IMMEDIATE) != ESP_OK) {
        s_pending[ch] = true;
        s_finish_reason[ch] = reason;
        return false; /* Keep playing/watchdog armed; retry next tick. */
    }
    c->play_elapsed_ms = (uint32_t)(esp_timer_get_time() / 1000) - c->play_started_ms;
    ch_mark_play_stopped(ch);
    ch_disarm_safety(ch);
    reset_run(ch);
    if (!s_live_transition) { evt_play_done(ch, reason, (uint16_t)c->play_elapsed_ms); }
    return true;
}

static void poll_channel(uint8_t ch)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (s_live_mask & (1u << ch)) { return; }
    if (c == NULL || !c->present || s_burst[ch]) { return; }
    if (s_pending[ch]) { (void)finish_run(ch, s_finish_reason[ch]); return; }
    if (!c->playing) { return; }
    /* CONT and infinite RAM queues do not have a final DONEI. */
    if (c->mode == AW_MODE_CONT ||
        (c->mode == AW_MODE_RAM && (c->profile.mainloop & 15u) == 15u)) { return; }
    uint8_t irq = 0;
    if (aw_get_sysint(ch, &irq) == ESP_OK && (irq & BIT_DONEI)) {
        (void)finish_run(ch, PLAY_DONE_NATURAL);
    }
}

void svc_play_tick(uint32_t now)
{
    PLAY_GUARD;
    live_tick(now);
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        ch_ctx_t *c = ch_ctx(ch);
        if (s_pending[ch]) { (void)finish_run(ch, s_finish_reason[ch]); continue; }
        if (!c || !c->safety_armed) { continue; }
        /* A missed normal deadline must not conceal a real watchdog overrun. */
        if (ch_safety_remaining_ms(ch) <= 0) {
            if (!s_trip_reported[ch]) {
                evt_safety_trip(TRIP_PLAY_TIMEOUT, (uint8_t)(1u << ch), 0);
                app_session_note_error(ERR_SAFETY_TIMEOUT);
                s_trip_reported[ch] = true;
            }
            (void)finish_run(ch, PLAY_DONE_SAFETY_TIMEOUT);
        } else if (s_timed[ch] && (int32_t)(now - s_deadline[ch]) >= 0) {
            (void)finish_run(ch, PLAY_DONE_DURATION);
        }
    }
}


/* ------------------------------------------------------------------ */
/* TRIG GPIO                                                           */
/* ------------------------------------------------------------------ */

/* 通道 → TRIG GPIO 表（原理图 TRIG0~TRIG7，每路一条独立线） */
static const gpio_num_t s_trig_gpio[BOARD_TRIG_GPIO_COUNT] = {
    BOARD_TRIG_GPIO_0, BOARD_TRIG_GPIO_1, BOARD_TRIG_GPIO_2, BOARD_TRIG_GPIO_3,
    BOARD_TRIG_GPIO_4, BOARD_TRIG_GPIO_5, BOARD_TRIG_GPIO_6, BOARD_TRIG_GPIO_7,
};

static const uint64_t s_trig_pin_mask =
    (1ULL << BOARD_TRIG_GPIO_0) | (1ULL << BOARD_TRIG_GPIO_1) |
    (1ULL << BOARD_TRIG_GPIO_2) | (1ULL << BOARD_TRIG_GPIO_3) |
    (1ULL << BOARD_TRIG_GPIO_4) | (1ULL << BOARD_TRIG_GPIO_5) |
    (1ULL << BOARD_TRIG_GPIO_6) | (1ULL << BOARD_TRIG_GPIO_7);

gpio_num_t board_trig_gpio(uint8_t ch)
{
    if (ch >= BOARD_TRIG_GPIO_COUNT) {
        return BOARD_TRIG_GPIO_0;
    }
    return s_trig_gpio[ch];
}

bool board_trig_is_shared(uint8_t ch)
{
    (void)ch;
    return false;       /* 2026-09 起每路一条独立 TRIG，不再有共用线 */
}

static void trig_gpio_init_all(void)
{
    gpio_config_t cfg = {
        .pin_bit_mask = s_trig_pin_mask,
        .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
}

void board_trig_park_all(void)
{
    trig_gpio_init_all();
    for (uint8_t ch = 0; ch < BOARD_TRIG_GPIO_COUNT; ch++) {
        gpio_set_level(s_trig_gpio[ch], 0);
    }
}

void board_trig_safe_init(void)
{
    /* ★ app_main 的第一件事。
     * IO0/IO1/IO14/IO18~IO22 复位默认是"输入 + 内部弱上拉（约 45 kΩ）"，
     * 若不及时拉低，A1 会被抬到约 1 V，高于 AW8623X 的 VIH(min)=0.8 V → 可能被误触发。
     * 先把输出拉到低电平，再使能内部下拉覆盖复位期的弱上拉，尽量缩短这个窗口。
     * 注：新硬件已给 8 条 TRIG 各加了 4.7K 外部下拉（R33~R40），该窗口风险已大幅降低。 */
    board_trig_park_all();

    gpio_config_t pd = {
        .pin_bit_mask = s_trig_pin_mask,
        .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&pd);
    board_trig_park_all();
}

uint8_t svc_play_expand_shared(uint8_t mask)
{
    if (mask & BOARD_TRIG_SHARED_MASK) {
        return (uint8_t)(mask | BOARD_TRIG_SHARED_MASK);
    }
    return mask;
}

/* ------------------------------------------------------------------ */
/* 安全预检                                                            */
/* ------------------------------------------------------------------ */

esp_err_t svc_play_check_safety(uint8_t mask, bool override, uint8_t *violating_mask)
{
    app_safety_cfg_t cfg;
    app_session_get_safety(&cfg);

    uint8_t bad = 0;
    int full = 0;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (!(mask & (uint8_t)(1u << ch))) {
            continue;
        }
        ch_ctx_t *c = ch_ctx(ch);
        if (c == NULL) {
            continue;
        }
        if (aw_profile_is_full_amplitude(&c->profile, c->mode)) {
            full++;
            bad |= (uint8_t)(1u << ch);
        }
    }
    if (violating_mask) {
        *violating_mask = bad;
    }
    if (full > (int)cfg.max_simul_full_amp && !override) {
        return ESP_ERR_INVALID_STATE;   /* 调用方转 ERR_SAFETY_LIMIT */
    }
    return ESP_OK;
}

/* ------------------------------------------------------------------ */
/* 单通道播放                                                          */
/* ------------------------------------------------------------------ */

esp_err_t svc_play_set_mode(uint8_t ch, uint8_t mode)
{
    PLAY_GUARD;
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || mode > AW_MODE_TRIG) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!c->present) {
        return ESP_ERR_NOT_FOUND;
    }
    if (ch_is_busy(ch)) {
        return ESP_ERR_INVALID_STATE;   /* -> ERR_BUSY */
    }

    if (svc_play_prepare_config((uint8_t)(1u << ch)) != ESP_OK) { return ESP_FAIL; }
    /* 进入 TRIG 前强制写并校验安全位（协议 §11.1） */
    if (mode == AW_MODE_TRIG) {
        esp_err_t e = aw_ensure_trig_safety(ch);
        if (e != ESP_OK) {
            return ESP_ERR_INVALID_STATE;   /* -> ERR_VERIFY_FAILED */
        }
    }
    esp_err_t err = aw_apply_mode(ch, mode, &c->profile);
    if (err != ESP_OK) {
        return err;
    }
    c->profile.mode = mode;
    c->mode = mode;
    if (mode == AW_MODE_TRIG) {
        err = aw_verify_trap_bits(ch, mode);
        if (err != ESP_OK) {
            return ESP_ERR_INVALID_STATE;
        }
    }
    LOGI("CH%u 播放模式切换为 %u", ch, mode);
    return ESP_OK;
}

esp_err_t svc_play_start(uint8_t ch, uint16_t timeout_ms, bool override,
                         uint16_t *actual_timeout_ms)
{
    PLAY_GUARD;
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!c->present) {
        return ESP_ERR_NOT_FOUND;
    }
    if (ch_is_busy(ch)) {
        return ESP_ERR_INVALID_STATE;
    }

    app_safety_cfg_t cfg;
    app_session_get_safety(&cfg);
    uint16_t to = (timeout_ms == 0) ? cfg.play_timeout_ms : timeout_ms;

    esp_err_t err = svc_play_check_safety((uint8_t)(1u << ch), override, NULL);
    if (err != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }

    if (s_pending[ch]) { return ESP_ERR_INVALID_STATE; }
    if (c->playing && !finish_run(ch, PLAY_DONE_STOPPED)) { return ESP_FAIL; }
    err = aw_clear_sysint(ch);
    if (err != ESP_OK) { return err; }
    reset_run(ch);
    err = aw_play_go(ch);
    if (err != ESP_OK) {
        return err;
    }
    ch_arm_safety(ch, to);
    ch_mark_play_started(ch);
    evt_play_started(ch, c->mode, to);
    if (actual_timeout_ms) {
        *actual_timeout_ms = to;
    }
    return ESP_OK;
}

esp_err_t svc_play_stop(uint8_t ch, uint8_t immediate)
{
    PLAY_GUARD;
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL) { return ESP_ERR_INVALID_ARG; }
    (void)immediate; /* Explicit stop is immediate, as the previous driver was. */
    ++s_generation[ch]; /* Cancel a pulse train even if I2C STOP fails. */
    s_burst[ch] = false;
    if (!c->present) { return ESP_ERR_NOT_FOUND; }
    return finish_run(ch, PLAY_DONE_STOPPED) ? ESP_OK : ESP_FAIL;
}

esp_err_t svc_play_play_multi(uint8_t chan_mask, uint16_t timeout_ms,
                              uint16_t skew_us, bool override,
                              uint8_t *started_mask, uint16_t *actual_skew_us)
{
    PLAY_GUARD;
    app_safety_cfg_t cfg;
    app_session_get_safety(&cfg);
    uint16_t to = (timeout_ms == 0) ? cfg.play_timeout_ms : timeout_ms;

    esp_err_t err = svc_play_check_safety(chan_mask, override, NULL);
    if (err != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t started = 0;
    int64_t t_prev = 0;
    uint16_t max_skew = 0;

    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (!(chan_mask & (uint8_t)(1u << ch))) {
            continue;
        }
        ch_ctx_t *c = ch_ctx(ch);
        if (c == NULL || !c->present || ch_is_busy(ch)) {
            continue;
        }
        if (skew_us > 0 && t_prev != 0) {
            int64_t target = t_prev + (int64_t)skew_us;
            while (esp_timer_get_time() < target) {
                /* 忙等保证 µs 级间隔 */
            }
        }
        if (s_pending[ch] || (c->playing && !finish_run(ch, PLAY_DONE_STOPPED))) { continue; }
        if (aw_clear_sysint(ch) != ESP_OK) { continue; }
        reset_run(ch);
        int64_t t0 = esp_timer_get_time();
        if (aw_play_go(ch) != ESP_OK) {
            continue;
        }
        ch_arm_safety(ch, to);
        ch_mark_play_started(ch);
        evt_play_started(ch, c->mode, to);
        started |= (uint8_t)(1u << ch);
        if (t_prev != 0) {
            uint16_t d = (uint16_t)(t0 - t_prev);
            if (d > max_skew) {
                max_skew = d;
            }
        }
        t_prev = t0;
    }

    if (started_mask) {
        *started_mask = started;
    }
    if (actual_skew_us) {
        *actual_skew_us = (skew_us > 0) ? max_skew : 0;
    }
    return (started != 0) ? ESP_OK : ESP_ERR_NOT_FOUND;
}

esp_err_t svc_play_stop_all(uint8_t immediate, uint8_t *stopped_mask)
{
    PLAY_GUARD;
    uint8_t stopped = 0;
    esp_err_t result = ESP_OK;
    board_trig_park_all();
    s_boot_mask = 0; /* Emergency stop also disarms the physical button. */
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        ch_ctx_t *c = ch_ctx(ch);
        if (c && c->present) {
            if (svc_play_stop(ch, immediate) == ESP_OK) { stopped |= (uint8_t)(1u << ch); }
            else { result = ESP_FAIL; }
        }
    }
    if (stopped_mask) { *stopped_mask = stopped; }
    return result;
}

void svc_play_trig_park_channels(uint8_t chan_mask)
{
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (!(chan_mask & (uint8_t)(1u << ch))) {
            continue;
        }
        ch_ctx_t *c = ch_ctx(ch);
        if (c != NULL && c->present) {
            /* 第二道手段：同时写 0x08 的 STOP 位 */
            (void)aw_update(ch, REG_PLAYCFG3, BIT_STOP_MODE, BIT_STOP_MODE, NULL, NULL);
        }
    }
}

void svc_play_trig_park(void)
{
    PLAY_GUARD;
    (void)live_cancel(PLAY_DONE_STOPPED);
    board_trig_park_all();
    svc_play_trig_park_channels(0xFFu);
}

/* ------------------------------------------------------------------ */
/* TRIG 脉冲生成（忙等，µs 级）                                        */
/* ------------------------------------------------------------------ */

static void trig_set_mask(uint8_t mask, int level)
{
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (mask & (uint8_t)(1u << ch)) {
            gpio_set_level(board_trig_gpio(ch), level);
        }
    }
}

esp_err_t svc_play_trig_pulse(uint8_t chan_mask, uint16_t width_us, uint8_t count,
                              uint16_t period_ms, uint16_t initial_delay_us,
                              uint8_t *started_mask)
{
    PLAY_GUARD;
    if (started_mask) { *started_mask = 0; }
    if (!chan_mask || width_us < BOARD_TRIG_PULSE_MIN_US || width_us > 1000 || !count || count > 32) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t mask = svc_play_expand_shared(chan_mask);
    app_safety_cfg_t cfg;
    app_session_get_safety(&cfg);
    if (svc_play_check_safety(mask, false, NULL) != ESP_OK) { return ESP_ERR_INVALID_STATE; }
    /* This operation is a positive-edge pulse, not an arbitrary dual-edge waveform.
       Reject unsupported polarity before any output; use level/pattern tools for those. */
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        ch_ctx_t *c = ch_ctx(ch);
        if (!c || !c->present || ch_is_busy(ch) || s_pending[ch] || c->mode != AW_MODE_TRIG ||
            c->profile.trig1_lev || c->profile.trig1_polar ||
            !c->profile.trig1_pos_en || c->profile.trig1_neg_en) { return ESP_ERR_INVALID_ARG; }
    }
    uint32_t generation[BOARD_CHANNEL_COUNT] = {0};
    trig_set_mask(mask, 0);
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        if (ch_ctx(ch)->playing && !finish_run(ch, PLAY_DONE_STOPPED)) { return ESP_FAIL; }
    }
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        reset_run(ch);
        generation[ch] = s_generation[ch];
        s_burst[ch] = true;
    }
    uint32_t wait_us = initial_delay_us;
    for (uint8_t i = 0; i < count; ++i) {
        /* Release between bounded waits: STOP/watchdog can cancel the remaining edges. */
        while (wait_us >= 1000) {
            uint32_t ms = wait_us / 1000 > 10 ? 10 : wait_us / 1000;
            svc_play_unlock();
            vTaskDelay(pdMS_TO_TICKS(ms));
            svc_play_lock();
            wait_us -= ms * 1000;
        }
        if (wait_us) { esp_rom_delay_us(wait_us); }
        for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
            if (!(mask & (1u << ch))) { continue; }
            if (generation[ch] != s_generation[ch]) { goto cancelled; }
            /* Stop a prior overlapping pulse before clearing its DONE, so the
               next waveform owns all subsequent completion events. */
            if (aw_stop(ch, STOP_MODE_IMMEDIATE) != ESP_OK || aw_clear_sysint(ch) != ESP_OK) { goto failed; }
            ch_arm_safety(ch, cfg.play_timeout_ms);
            ch_mark_play_started(ch);
        }
        trig_set_mask(mask, 1);
        esp_rom_delay_us(width_us);
        trig_set_mask(mask, 0);
        wait_us = (uint32_t)period_ms * 1000;
    }
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (mask & (1u << ch)) {
            s_burst[ch] = false;
            evt_play_started(ch, AW_MODE_TRIG, cfg.play_timeout_ms);
        }
    }
    if (started_mask) { *started_mask = mask; }
    return ESP_OK;
failed:
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if ((mask & (1u << ch)) && generation[ch] == s_generation[ch]) { (void)svc_play_stop(ch, 1); }
    }
    return ESP_FAIL;
cancelled:
    /* Never stop a newer run that replaced the cancelled sequence. */
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if ((mask & (1u << ch)) && generation[ch] == s_generation[ch]) { (void)svc_play_stop(ch, 1); }
    }
    return ESP_ERR_INVALID_STATE;
}


static bool wait_trigger(uint32_t us, uint8_t mask, const uint32_t *generation)
{
    while (us >= 1000) {
        uint32_t ms = us / 1000 > 10 ? 10 : us / 1000;
        svc_play_unlock();
        vTaskDelay(pdMS_TO_TICKS(ms));
        svc_play_lock();
        us -= ms * 1000;
        for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
            if ((mask & (1u << ch)) && generation[ch] != s_generation[ch]) { return false; }
        }
    }
    if (us) { esp_rom_delay_us(us); }
    return true;
}

static esp_err_t advanced_trigger(uint8_t mask, uint32_t on_us, uint32_t off_us,
                                  uint16_t count, uint16_t period_ms, bool level_mode)
{
    PLAY_GUARD;
    if (!mask || !on_us || !count || count > 256) { return ESP_ERR_INVALID_ARG; }
    if (svc_play_check_safety(mask, false, NULL) != ESP_OK) { return ESP_ERR_INVALID_STATE; }
    uint32_t generation[BOARD_CHANNEL_COUNT] = {0};
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        ch_ctx_t *c = ch_ctx(ch);
        if (!c || !c->present || ch_is_busy(ch) || s_pending[ch] || c->mode != AW_MODE_TRIG ||
            c->profile.trig1_polar || !c->profile.trig1_pos_en || c->profile.trig1_neg_en ||
            (bool)c->profile.trig1_lev != level_mode) { return ESP_ERR_INVALID_ARG; }
    }
    app_safety_cfg_t cfg;
    app_session_get_safety(&cfg);
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        if (ch_ctx(ch)->playing && !finish_run(ch, PLAY_DONE_STOPPED)) { return ESP_FAIL; }
    }
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        reset_run(ch); generation[ch] = s_generation[ch]; s_burst[ch] = true;
    }
    trig_set_mask(mask, 0);
    for (uint16_t i = 0; i < count; ++i) {
        for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
            if (!(mask & (1u << ch))) { continue; }
            if (aw_stop(ch, STOP_MODE_IMMEDIATE) != ESP_OK || aw_clear_sysint(ch) != ESP_OK) { goto failed; }
            ch_arm_safety(ch, cfg.play_timeout_ms); ch_mark_play_started(ch);
        }
        trig_set_mask(mask, 1);
        if (!wait_trigger(on_us, mask, generation)) { goto failed; }
        trig_set_mask(mask, 0);
        if (i + 1u < count && !wait_trigger(off_us + (uint32_t)period_ms * 1000, mask, generation)) { goto failed; }
    }
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (mask & (1u << ch)) {
            s_burst[ch] = false;
            evt_play_started(ch, AW_MODE_TRIG, cfg.play_timeout_ms);
        }
    }
    return ESP_OK;
failed:
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if ((mask & (1u << ch)) && generation[ch] == s_generation[ch]) { (void)svc_play_stop(ch, 1); }
    }
    return ESP_FAIL;
}

esp_err_t svc_play_trig_level(uint8_t chan_mask, uint8_t level, uint16_t hold_ms)
{
    if (level != 1 || hold_ms < BOARD_TRIG_LEVEL_MIN_MS) { return ESP_ERR_INVALID_ARG; }
    return advanced_trigger(chan_mask, (uint32_t)hold_ms * 1000, 0, 1, 0, true);
}

esp_err_t svc_play_trig_pattern(uint8_t chan_mask, uint16_t on_us, uint16_t off_us,
                                uint16_t count, uint16_t period_ms)
{
    return advanced_trigger(chan_mask, on_us, off_us, count, period_ms, false);
}

/* ------------------------------------------------------------------ */
/* 播放结束轮询                                                        */
/* ------------------------------------------------------------------ */

uint32_t svc_play_poll_interval_ms(void)
{
    int n = 0;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        ch_ctx_t *c = ch_ctx(ch);
        if (c != NULL && c->playing) {
            n++;
        }
    }
    return (n >= 4) ? PLAY_POLL_INTERVAL_MS_MANY : PLAY_POLL_INTERVAL_MS_FEW;
}

void svc_play_poll(uint32_t now_ms)
{
    PLAY_GUARD;
    if ((now_ms - s_last_poll_ms) < svc_play_poll_interval_ms()) { return; }
    s_last_poll_ms = now_ms;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) { poll_channel(ch); }
}

esp_err_t svc_play_query_state(uint8_t ch, uint8_t *playing, uint8_t *glb_state,
                               uint16_t *elapsed_ms)
{
    PLAY_GUARD;
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL) { return ESP_ERR_INVALID_ARG; }
    poll_channel(ch);
    if (c->present) { (void)aw_get_glb_state(ch, &c->glb_state); }
    if (playing) { *playing = c->playing; }
    if (glb_state) { *glb_state = c->glb_state; }
    uint32_t elapsed = c->playing ? (uint32_t)(esp_timer_get_time() / 1000) - c->play_started_ms : c->play_elapsed_ms;
    if (elapsed_ms) { *elapsed_ms = (uint16_t)(elapsed > 65535 ? 65535 : elapsed); }
    return ESP_OK;
}


esp_err_t svc_play_timed(uint8_t mask, uint16_t duration_ms, uint8_t *started)
{
    PLAY_GUARD;
    if (!mask || duration_ms < 100 || duration_ms > 60000) { return ESP_ERR_INVALID_ARG; }
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        ch_ctx_t *c = ch_ctx(ch);
        if (!c || !c->present || ch_is_busy(ch) ||
            (c->mode != AW_MODE_RAM && c->mode != AW_MODE_CONT)) { return ESP_ERR_INVALID_ARG; }
    }
    uint8_t result = 0;
    uint16_t skew = 0;
    esp_err_t err = svc_play_play_multi(mask, (uint16_t)((uint32_t)duration_ms + 500u), 0, false, &result, &skew);
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (result & (1u << ch)) {
            s_timed[ch] = true;
            s_deadline[ch] = ch_ctx(ch)->play_started_ms + duration_ms;
        }
    }
    if (started) { *started = result; }
    return err;
}

static esp_err_t check_boot_channels(uint8_t mask)
{
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        ch_ctx_t *c = ch_ctx(ch);
        if (!c || !c->present || ch_is_busy(ch) || c->mode != AW_MODE_TRIG ||
            c->profile.trig1_lev || c->profile.trig1_polar ||
            !c->profile.trig1_pos_en || c->profile.trig1_neg_en || !c->profile.trig1_seq_p) {
            return ESP_ERR_INVALID_STATE;
        }
        uint8_t head[4];
        uint16_t base = c->profile.base_addr;
        uint16_t addr = base + 1u + 4u * (c->profile.trig1_seq_p - 1u);
        if (aw_wave_lib_read(ch, addr, head, sizeof(head)) != ESP_OK) { return ESP_FAIL; }
        uint16_t start = (uint16_t)((head[0] << 8) | head[1]);
        uint16_t end = (uint16_t)((head[2] << 8) | head[3]);
        if (addr + 3u >= WAVE_LIB_MAX_BYTES || start <= addr + 3u || end < start || end >= WAVE_LIB_MAX_BYTES) { return ESP_ERR_INVALID_STATE; }
    }
    return ESP_OK;
}

esp_err_t svc_play_boot_arm(uint8_t mask)
{
    PLAY_GUARD;
    s_boot_mask = 0;
    esp_err_t err = check_boot_channels(mask);
    if (err == ESP_OK) { s_boot_mask = mask; }
    return err;
}

void svc_play_boot_invalidate(uint8_t mask)
{
    PLAY_GUARD;
    s_boot_mask &= (uint8_t)~mask;
}

esp_err_t svc_play_boot_fire(uint8_t *started)
{
    PLAY_GUARD;
    if (started) { *started = 0; }
    if (!s_boot_mask) { return ESP_ERR_INVALID_STATE; }
    esp_err_t err = check_boot_channels(s_boot_mask);
    if (err != ESP_OK) { s_boot_mask = 0; return err; }
    return svc_play_trig_pulse(s_boot_mask, BOARD_TRIG_PULSE_US, 1, 0, 0, started);
}

bool svc_play_active(uint8_t ch)
{
    PLAY_GUARD;
    ch_ctx_t *c = ch_ctx(ch);
    return c && (c->playing || c->safety_armed || s_pending[ch] || s_burst[ch]);
}
esp_err_t svc_play_prepare_config(uint8_t mask)
{
    PLAY_GUARD;
    svc_play_boot_invalidate(mask);
    if ((s_live_mask & mask) && !live_cancel(PLAY_DONE_STOPPED)) { return ESP_FAIL; }
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if ((mask & (1u << ch)) && ch_ctx(ch) && ch_ctx(ch)->f0_pending) { return ESP_ERR_INVALID_STATE; }
        if ((mask & (1u << ch)) && svc_play_active(ch) && svc_play_stop(ch, 1) != ESP_OK) { return ESP_FAIL; }
    }
    return ESP_OK;
}

static bool live_cancel(uint8_t reason)
{
    uint8_t mask = s_live_mask;
    s_live_mask = 0; /* Revoke before any I2C. Failed STOP must never resume feeding. */
    s_tactile = false;
    s_tactile_braking = false;
    bool ok = true;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if ((mask & (1u << ch)) && !finish_run(ch, reason)) { ok = false; }
    }
    return ok;
}

uint8_t svc_play_live_mask(void)
{
    PLAY_GUARD;
    return s_live_mask;
}

static esp_err_t live_feed(uint8_t ch, bool initial)
{
    uint8_t st = 0, irq = 0;
    if (aw_get_sysint(ch, &irq) != ESP_OK || aw_get_sysst(ch, &st) != ESP_OK) { return ESP_FAIL; }
    if ((irq & 0x27u) || (st & 0x26u)) { return ESP_FAIL; }
    if (!initial && !(st & 0x10u)) { return ESP_OK; } /* True FIFO almost-empty. */
    uint16_t ae, af;
    live_fifo_thresholds(s_live.frequency_x10, &ae, &af);
    ch_ctx_t *ctx = ch_ctx(ch);
    /* Defer changed thresholds until old low-water, limiting old+new queue latency. */
    if (ctx->profile.fifo_ae != ae || ctx->profile.fifo_af != af) {
        uint8_t water[3] = {(uint8_t)(((ae >> 8) << 4) | (af >> 8)), (uint8_t)ae, (uint8_t)af};
        if (aw_write(ch, 0x2E, water, 3) != ESP_OK) { return ESP_FAIL; }
        ctx->profile.fifo_ae = ae; ctx->profile.fifo_af = af;
    }
    /* Re-read after a threshold update. The old AE gate alone is insufficient
     * when reducing frequency, because the new byte thresholds are smaller. */
    if (aw_get_sysst(ch, &st) != ESP_OK || (st & 0x26u)) { return ESP_FAIL; }
    if ((st & 0x08u) || (!initial && !(st & 0x10u))) { return ESP_OK; }

    /* GO initially waits with an empty FIFO. Thereafter AES guarantees at most
     * AE queued bytes: adding AF-AE keeps the queue at or below AF (~65ms).
     * Do not chase AF: playback consumes bytes during I2C transfers, so a healthy
     * FIFO need not reach that threshold after a bounded refill. Split at 60
     * bytes (six complete ADS cycles), below aw_write's 63-byte limit. */
    uint16_t remaining = initial ? af : (uint16_t)(af - ae);
    while (remaining) {
        if (aw_get_sysst(ch, &st) != ESP_OK || (st & 0x26u)) { return ESP_FAIL; }
        if (st & 0x08u) { break; }
        uint8_t bytes[60];
        uint16_t count = remaining < sizeof bytes ? remaining : sizeof bytes;
        uint64_t phase = s_live_phase_us[ch];
        for (uint16_t offset = 0; offset < count; offset += 10) {
            live_ads_cycle(s_live.frequency_x10, live_amplitude(&s_live, phase), bytes + offset);
            phase += 10000000u / s_live.frequency_x10;
        }
        if (aw_rtp_write(ch, bytes, count) != ESP_OK) { return ESP_FAIL; }
        s_live_phase_us[ch] = phase;
        remaining -= count;
    }
    return ESP_OK;
}

static esp_err_t tactile_brake(uint8_t ch)
{
    /* Four bounded brake half-cycles (~12ms), stock gain8. Never alter D2S_GAIN. */
    esp_err_t err = aw_update(ch, 0x20, 0xFFu, 4, NULL, NULL);
    if (err == ESP_OK) { err = aw_update(ch, 0x08, 0x04u, 0x04u, NULL, NULL); }
    if (err == ESP_OK) { err = aw_stop(ch, STOP_MODE_IMMEDIATE); }
    return err;
}

static void live_tick(uint32_t now)
{
    if (!s_live_mask) { return; }
    if ((int32_t)(now - s_live_deadline) >= 0) {
        evt_safety_trip(TRIP_LIVE_LEASE, s_live_mask, 0);
        app_session_note_error(ERR_SAFETY_TIMEOUT);
        (void)live_cancel(PLAY_DONE_SAFETY_TIMEOUT);
        return;
    }
    /* Reduce before raising any channel: the aggregate budget must hold
     * at every serialized I2C write, not merely at the final vector. */
    if (s_tactile) {
        for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
            if (!(s_live_mask & (1u << ch)) || s_tactile_levels[ch] >= s_live_last_amp[ch]) { continue; }
            if ((!s_tactile_levels[ch] && tactile_brake(ch) != ESP_OK) ||
                aw_update(ch, 0x1C, 0x7Fu, s_tactile_levels[ch], NULL, NULL) != ESP_OK) {
                evt_safety_trip(TRIP_BUS_ERROR, s_live_mask, ch);
                (void)live_cancel(PLAY_DONE_ERROR);
                return;
            }
            if (!s_tactile_levels[ch]) {
                s_tactile_braking = true;
                s_tactile_brake_until = (uint32_t)(esp_timer_get_time()/1000) + 16u;
            }
            s_live_last_amp[ch] = s_tactile_levels[ch];
            ch_ctx(ch)->profile.drv1_lvl = s_tactile_levels[ch];
        }
        bool raising = false;
        for (uint8_t ch=0; ch<BOARD_CHANNEL_COUNT; ++ch) {
            if ((s_live_mask & (1u<<ch)) && s_tactile_levels[ch]>s_live_last_amp[ch]) { raising=true; }
        }
        int32_t brake_remaining=(int32_t)(s_tactile_brake_until-(uint32_t)(esp_timer_get_time()/1000));
        if (s_tactile_braking && (raising || brake_remaining<=0)) {
            /* Finish braking even without another rising edge. The safety tick
             * clears idle brake drive after the 16ms window (tick granularity).
             * A sooner rise waits at most 16ms before completing the same cleanup. */
            int32_t remaining=(int32_t)(s_tactile_brake_until-(uint32_t)(esp_timer_get_time()/1000));
            if (remaining>0) { esp_rom_delay_us((uint32_t)remaining*1000u); }
            for (uint8_t ch=0; ch<BOARD_CHANNEL_COUNT; ++ch) {
                if (!(s_live_mask & (1u<<ch)) || s_live_last_amp[ch]) { continue; }
                if (aw_update(ch,0x08,0x04u,0,NULL,NULL)!=ESP_OK ||
                    aw_update(ch,0x20,0xFFu,0,NULL,NULL)!=ESP_OK ||
                    aw_stop(ch,STOP_MODE_IMMEDIATE)!=ESP_OK) {
                    (void)live_cancel(PLAY_DONE_ERROR); return;
                }
            }
            s_tactile_braking=false;
        }
    }
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(s_live_mask & (1u << ch))) { continue; }
        esp_err_t err = ESP_OK;
        if (s_live.tracking) {
            uint8_t st, irq;
            /* This board reports stale GLB values (0/3) even while driving.
             * CONT segment DONE is not a final DONE; only faults terminate it. */
            if (aw_get_sysst(ch, &st) != ESP_OK || aw_get_sysint(ch, &irq) != ESP_OK ||
                (st & 0x26u) || (irq & 0x26u)) { err = ESP_FAIL; }
            uint8_t amp = s_tactile ? s_tactile_levels[ch] : live_amplitude(&s_live, (uint64_t)(now - s_live_started) * 1000u);
            if (err == ESP_OK && amp != s_live_last_amp[ch]) {
                err = aw_update(ch, 0x1C, 0x7Fu, amp, NULL, NULL);
                if (err == ESP_OK && s_tactile && !s_live_last_amp[ch] && amp) {
                    /* Brake only at release, never inside sustained CONT segments. */
                    err = aw_update(ch, 0x08, 0x04u, 0, NULL, NULL);
                    if (err == ESP_OK) { err = aw_update(ch, 0x20, 0xFFu, 0, NULL, NULL); }
                    if (err == ESP_OK) { err = aw_play_go(ch); }
                    if (err == ESP_OK) { esp_rom_delay_us(1000); }
                }
                if (err == ESP_OK) { s_live_last_amp[ch] = amp; ch_ctx(ch)->profile.drv1_lvl = amp; }
            }
        } else { err = live_feed(ch, false); }
        if (err != ESP_OK) {
            evt_safety_trip(TRIP_BUS_ERROR, s_live_mask, ch);
            (void)live_cancel(PLAY_DONE_ERROR);
            return;
        }
    }
}

esp_err_t svc_play_live_keepalive(uint8_t mask)
{
    PLAY_GUARD;
    uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
    if (s_live_mask && (int32_t)(now - s_live_deadline) >= 0) { live_tick(now); }
    if (s_tactile || !mask || mask != s_live_mask) { return ESP_ERR_INVALID_STATE; }
    s_live_deadline = now + 2000u;
    return ESP_OK;
}

esp_err_t svc_play_live_control(uint8_t action, uint8_t mask, const live_config_t *config)
{
    PLAY_GUARD;
    if (action > 1 || !mask || !live_config_valid(config)) { return ESP_ERR_INVALID_ARG; }
    uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
    if (s_live_mask && (int32_t)(now - s_live_deadline) >= 0) { live_tick(now); }
    if (action && (s_tactile || mask != s_live_mask)) { return ESP_ERR_INVALID_STATE; }
    if (action && config->tracking == s_live.tracking) {
        s_live = *config; /* No GO, no implicit lease renewal, no phase reset. */
        return ESP_OK;
    }
    uint32_t deadline = action ? s_live_deadline : now + 2000u;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        ch_ctx_t *c = ch_ctx(ch);
        if (!c || !c->present || ch_is_busy(ch) || s_pending[ch] ||
            (!config->tracking && c->profile.base_addr < 256u)) { return ESP_ERR_INVALID_STATE; }
    }
    s_live_transition = action != 0;
    bool cancelled = !s_live_mask || live_cancel(PLAY_DONE_STOPPED);
    s_live_transition = false;
    if (!cancelled) { return ESP_FAIL; }
    if (svc_play_prepare_config(mask) != ESP_OK) { return ESP_FAIL; }
    s_boot_mask = 0;
    s_tactile = false;
    s_tactile_braking = false;
    s_live = *config; s_live_started = now; s_live_deadline = deadline;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
        if (!(mask & (1u << ch))) { continue; }
        ch_ctx_t *c = ch_ctx(ch);
        aw_profile_t p = c->profile;
        p.mode = config->tracking ? AW_MODE_CONT : AW_MODE_RTP;
        p.gain = 128; p.vbat_mode = 0; p.vbat_ref = DEF_VBAT_REF; p.stop_mode = 1;
        p.en_fir = 1;
        p.en_f0_det = 0; p.brk_en = 0; p.brk_time = 0;

        if (config->tracking) {
            p.f_pre = (uint8_t)((240000u + config->frequency_x10 / 2u) / config->frequency_x10);
            p.drv_width = DEF_DRV_WIDTH; p.track_margin = DEF_TRACK_MARGIN;
            p.brk_gain = DEF_BRK_GAIN; p.wavdat_mode = DEF_WAVDAT_MODE;
            /* Low-amplitude tactile tracking jumped to ~320Hz on this board.
             * Fixed-width drive avoids that feedback path; width80 read ~160Hz.
             * Ordinary live tracking keeps its original settings. */
            p.track_en = s_tactile_starting ? 0 : 1;
            if (s_tactile_starting) { p.drv_width = 80; }
            p.wait_num = 0; p.drv1_time = 255; p.drv2_time = 0;
            p.sin_mode = 1; p.edge_fre = 14;
            p.drv1_lvl = live_amplitude(config, 0); p.drv2_lvl = 0;
        } else {
            p.wavdat_mode = RATE_48K;
            p.rtp_flags = 1; /* AUTO_DYNAMIC_SIN=1, 48k internal oscillator. */
            live_fifo_thresholds(config->frequency_x10, &p.fifo_ae, &p.fifo_af);
        }
        /* Include this channel before configuration/GO: every error must STOP it. */
        reset_run(ch); s_live_mask |= (uint8_t)(1u << ch);
        if (aw_stop(ch, STOP_MODE_IMMEDIATE) != ESP_OK || aw_apply_profile(ch, &p) != ESP_OK ||
            aw_update(ch, 0x17, 0x20u, 0, NULL, NULL) != ESP_OK ||
            aw_update(ch, REG_SYSCTRL1, BIT_EN_RAMINIT | BIT_EN_FIR, BIT_EN_FIR, NULL, NULL) != ESP_OK ||
            aw_clear_sysint(ch) != ESP_OK) { goto fail; }
        c->profile = p; c->mode = p.mode;
        ch_mark_play_started(ch); ch_disarm_safety(ch);
        s_live_last_amp[ch] = p.drv1_lvl; s_live_phase_us[ch] = 0;
        if (!s_tactile_starting && aw_play_go(ch) != ESP_OK) { goto fail; }
        esp_rom_delay_us(1000);
        if (!config->tracking && live_feed(ch, true) != ESP_OK) { goto fail; }
        if (!action) { evt_play_started(ch, c->mode, 0); }
    }
    return ESP_OK;
fail:
    (void)live_cancel(PLAY_DONE_ERROR);
    return ESP_FAIL;
}

esp_err_t svc_play_tactile_frame(uint8_t action, uint8_t mask, uint16_t seq, const uint8_t levels[8])
{
    PLAY_GUARD;
    if (action > 1 || !mask || !levels) { return ESP_ERR_INVALID_ARG; }
    uint32_t energy = 0;
    for (uint8_t ch = 0; ch < 8; ++ch) {
        if (levels[ch] > 60 || (!(mask & (1u << ch)) && levels[ch]) || (!action && levels[ch])) {
            return ESP_ERR_INVALID_ARG;
        }
        energy += (uint32_t)levels[ch] * levels[ch];
    }
    if (energy > 7200u) { return ESP_ERR_INVALID_ARG; }
    uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
    if (s_live_mask && (int32_t)(now - s_live_deadline) >= 0) { live_tick(now); }
    if (!action) {
        /* START cannot steal another session; caller must explicitly stop it. */
        for (uint8_t ch = 0; ch < 8; ++ch) {
            if (svc_play_active(ch)) { return ESP_ERR_INVALID_STATE; }
        }
        live_config_t cfg = {.frequency_x10=1680, .rate_x10=20,
            .amplitude=0, .envelope=0, .depth=0, .tracking=1};
        s_tactile_starting = true;
        esp_err_t err = svc_play_live_control(0, mask, &cfg);
        s_tactile_starting = false;
        if (err != ESP_OK) { return err; }
        s_tactile = true;
        memset(s_tactile_levels, 0, sizeof s_tactile_levels);
    } else {
        uint16_t delta = (uint16_t)(seq - s_tactile_seq);
        if (!s_tactile || mask != s_live_mask || !delta || delta >= 0x8000u) {
            return ESP_ERR_INVALID_STATE;
        }
        memcpy(s_tactile_levels, levels, sizeof s_tactile_levels);
    }
    s_tactile_seq = seq;
    s_live_deadline = now + 300u; /* Ordinary PING / live keepalive cannot extend this. */
    live_tick(now); /* Apply as one logical vector, serialized I2C per channel. */
    return s_tactile && s_live_mask == mask ? ESP_OK : ESP_FAIL;
}
