/**
 * @file  svc_measure.c
 * @brief 测量服务实现。
 *
 * ★ F0 检测的关键坑：**必须等 `GLB_STATE`（0x3F bit3:0）回到 0（STANDBY）再读结果**，
 *   否则读到的是无效值（`02` 文档 §3.4）。结果寄存器是 `0x24`~`0x27`，
 *   **不是** `0x51`~`0x53`（那是 ADC/电压/阻抗，前置工程文件在此有笔误）。
 */

#include "svc_measure.h"

#include <string.h>

#include "app_protocol.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "convert.h"
#include "drv_aw8623x.h"
#include "esp_timer.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "svc_play.h"
#include "util_log.h"

typedef struct {
    bool     enabled;
    uint8_t  chan_mask;
    uint16_t period_ms;
    uint16_t effective_period_ms;
    uint16_t fields;
    uint32_t last_ms;
} mon_state_t;

static mon_state_t s_mon;

static inline uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

/* ------------------------------------------------------------------ */
/* F0 检测                                                             */
/* ------------------------------------------------------------------ */

static esp_err_t f0_start_locked(uint8_t ch, uint16_t timeout_ms, uint8_t mode,
                               uint8_t *started)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || mode > 1) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!c->present) {
        if (started) { *started = 0; }
        return ESP_ERR_NOT_FOUND;
    }
    if (c->f0_pending || c->wave_session || svc_play_active(ch)) {
        if (started) { *started = 0; }
        return ESP_ERR_INVALID_STATE;   /* -> ERR_BUSY */
    }

    svc_play_boot_invalidate((uint8_t)(1u << ch));
    esp_err_t err = aw_f0_enable(ch, mode);
    if (err != ESP_OK) {
        if (started) { *started = 0; }
        return err;
    }
    /* F0 检测需要时间：手册要求 GO 后 ≥1 ms，实测从 GO 到读数稳定需数百毫秒，
     * 窗口太短必然读到无效值（原实现只等 500 ms 且靠 GLB_STATE 提前返回）。 */
    if (timeout_ms < 400) {
        timeout_ms = 400;
    }
    c->f0_pending = true;
    c->f0_mode = mode;
    c->f0_valid = 0;
    c->f0_deadline_ms = now_ms() + timeout_ms;
    if (started) {
        *started = 1;
    }
    LOGI("CH%u 启动 F0 检测（mode=%u，超时 %u ms）", ch, mode, timeout_ms);
    return ESP_OK;
}

esp_err_t svc_measure_f0_result(uint8_t ch, uint8_t *valid, uint8_t *mode,
                                uint8_t *raw_h, uint8_t *raw_l, uint16_t *hz_x10)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (valid)  { *valid = c->f0_valid; }
    if (mode)   { *mode = c->f0_mode; }
    if (raw_h)  { *raw_h = c->f0_raw_h; }
    if (raw_l)  { *raw_l = c->f0_raw_l; }
    if (hz_x10) { *hz_x10 = c->f0_valid ? c->f0_hz_x10 : 0xFFFFu; }
    return ESP_OK;
}

/** 轮询 F0：等 GLB_STATE 回到 STANDBY 再读结果。 */
static void f0_poll_one(uint8_t ch, uint32_t now)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->f0_pending) {
        return;
    }

    uint8_t glb = 0xFF;
    (void)aw_get_glb_state(ch, &glb);
    c->glb_state = glb;

    /* ★ 必须等满检测窗口再读结果。
     *   手册要求 GO 后延时 ≥1 ms 等检测结束；实测本芯片从 GO 到读数稳定需要数百毫秒。
     *   ⚠️ 不能依赖 GLB_STATE 判"检测结束"：实测本芯片该寄存器不反映播放状态
     *      （WAKE=1 时恒读 3、WAKE=0 时恒读 0），所以这里改成固定等待窗口。 */
    if ((int32_t)(now - c->f0_deadline_ms) < 0) {
        return;
    }

    uint8_t h = 0, l = 0;
    if (aw_read_f0_raw(ch, c->f0_mode, &h, &l) == ESP_OK) {
        uint32_t raw = ((uint32_t)h << 8) | l;
        float f0 = conv_f0_raw_to_hz(c->f0_mode, h, l);
        bool ok = (raw >= 6u) && (f0 >= 50.0f) && (f0 <= 500.0f);
        c->f0_raw_h = h;
        c->f0_raw_l = l;
        c->f0_valid = ok ? 1u : 0u;
        c->f0_hz_x10 = ok ? (uint16_t)(f0 * 10.0f + 0.5f) : 0xFFFFu;
        if (ok) {
            LOGI("CH%u F0 检测完成：raw=0x%02X%02X -> %.1f Hz", ch, h, l, (double)f0);
        } else {
            LOGW("CH%u F0 检测结果无效（raw=0x%02X%02X -> %.1f Hz）", ch, h, l,
                 (double)f0);
        }
        evt_measure_result(ch, 0, c->f0_valid,
                           ok ? (uint32_t)(f0 * 10.0f + 0.5f) : 0u, h, l);
    } else {
        c->f0_valid = 0;
        c->f0_hz_x10 = 0xFFFFu;
        evt_measure_result(ch, 0, 0, 0, 0, 0);
    }
    (void)aw_f0_disable(ch);        /* 停播放 + 清 EN_F0_DET */
    c->f0_pending = false;
}

/* ------------------------------------------------------------------ */
/* DET_READ                                                            */
/* ------------------------------------------------------------------ */

esp_err_t svc_measure_det_read(uint8_t ch, uint8_t seq, uint16_t *adc_data,
                               uint16_t *avg_data, uint16_t *value_x100,
                               uint8_t *valid, uint8_t *d2s_gain)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->present) {
        return ESP_ERR_NOT_FOUND;
    }

    uint16_t adc = 0, avg = 0;
    uint8_t gain = 8;
    esp_err_t err = aw_det_read(ch, seq, &adc, &avg, &gain, 3);
    if (err != ESP_OK) {
        if (valid) { *valid = 0; }
        if (value_x100) { *value_x100 = 0xFFFFu; }
        return err;
    }

    bool ok = (avg > 0u) && (avg < 1023u);
    uint16_t vx100 = 0xFFFFu;
    if (ok) {
        switch (seq) {
        case DET_SEQ_VBAT:
            vx100 = (uint16_t)(conv_avg_to_vbat(avg) * 100.0f + 0.5f);
            break;
        case DET_SEQ_RL:
            vx100 = (uint16_t)(conv_avg_to_rl(avg, gain) * 100.0f + 0.5f);
            break;
        default:
            vx100 = avg;
            break;
        }
    } else {
        ok = false;
    }

    if (adc_data)   { *adc_data = adc; }
    if (avg_data)   { *avg_data = avg; }
    if (value_x100) { *value_x100 = vx100; }
    if (valid)      { *valid = ok ? 1u : 0u; }
    if (d2s_gain)   { *d2s_gain = gain; }

    if (seq == DET_SEQ_RL && !ok) {
        LOGW("CH%u LRA 阻抗读数异常（AVG=%u）——依次检查：马达是否插好、输出球 A3/C3 是否虚焊、"
             "芯片是否被置成 WAKE/STANDBY 模式（0x46 bit7/bit6 必须为 0）",
             ch, avg);
    }
    return ESP_OK;
}

/* ------------------------------------------------------------------ */
/* SNAPSHOT                                                            */
/* ------------------------------------------------------------------ */

esp_err_t svc_measure_snapshot(uint8_t ch, snapshot_t *out)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out, 0, sizeof(*out));
    if (c == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    out->present = c->present;
    out->chipid_h = (uint8_t)(c->chipid >> 8);
    out->chipid_l = (uint8_t)(c->chipid & 0xFFu);
    out->mode = c->mode;
    out->playing = c->playing ? 1u : 0u;
    out->glb_state = c->glb_state;

    if (!c->present) {
        return ESP_OK;
    }
    /* 一次抓取所有关心的寄存器（原始值，由上位机按公式解码） */
    uint8_t regs[4];
    if (aw_read(ch, REG_F_LRA_F0_H, regs, 4) == ESP_OK) {
        out->reg24 = regs[0];
        out->reg25 = regs[1];
        out->reg26 = regs[2];
        out->reg27 = regs[3];
    }
    uint8_t d[3];
    if (aw_read(ch, REG_DETRD1, d, 3) == ESP_OK) {
        out->reg51 = d[0];
        out->reg52 = d[1];
        out->reg53 = d[2];
    }
    (void)aw_get_sysst(ch, &out->sysst);
    (void)aw_get_sysst2(ch, &out->sysst2);
    out->glb_state = c->glb_state;
    return ESP_OK;
}

/* ------------------------------------------------------------------ */
/* 监视                                                                */
/* ------------------------------------------------------------------ */

esp_err_t svc_measure_mon_start(uint8_t chan_mask, uint16_t period_ms, uint16_t fields)
{
    if (chan_mask == 0 || period_ms < 2) {
        return ESP_ERR_INVALID_ARG;
    }
    /* ★ 速率限制：8 路同时以 2 ms 周期采样会跑满 400 kHz 总线。
     * 每个通道一轮 ≈ 1 次选通 + 1 次读 + 开销，按 0.8 ms/通道估。 */
    int n = 0;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (chan_mask & (uint8_t)(1u << ch)) {
            n++;
        }
    }
    uint16_t min_period = (uint16_t)(2 + (uint32_t)n * 1u);
    uint16_t eff = period_ms;
    if (eff < min_period) {
        eff = min_period;
        evt_log_text(LOG_WARN, "监视周期被总线能力限制上调（8 路 2 ms 会跑满 400 kHz 总线）");
        LOGW("MON 周期由 %u ms 上调到 %u ms（%d 路）", period_ms, eff, n);
    }

    s_mon.enabled = true;
    s_mon.chan_mask = chan_mask;
    s_mon.period_ms = period_ms;
    s_mon.effective_period_ms = eff;
    s_mon.fields = fields;
    s_mon.last_ms = 0;
    return ESP_OK;
}

esp_err_t svc_measure_mon_stop(uint8_t chan_mask)
{
    if (chan_mask == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    s_mon.chan_mask &= (uint8_t)~chan_mask;
    if (s_mon.chan_mask == 0) {
        s_mon.enabled = false;
    }
    return ESP_OK;
}

void svc_measure_mon_stop_all(void)
{
    s_mon.enabled = false;
    s_mon.chan_mask = 0;
}

static void mon_poll(uint32_t now)
{
    if (!s_mon.enabled) {
        return;
    }
    if ((now - s_mon.last_ms) < s_mon.effective_period_ms) {
        return;
    }
    s_mon.last_ms = now;

    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (!(s_mon.chan_mask & (uint8_t)(1u << ch))) {
            continue;
        }
        ch_ctx_t *c = ch_ctx(ch);
        if (c == NULL || !c->present) {
            continue;
        }
        uint8_t glb = c->glb_state;
        (void)aw_get_glb_state(ch, &glb);
        c->glb_state = glb;

        if (s_mon.fields & MON_FIELD_DETRD) {
            uint16_t adc = 0, avg = 0, vx = 0xFFFF;
            uint8_t valid = 0, gain = 8;
            if (svc_measure_det_read(ch, DET_SEQ_VBAT, &adc, &avg, &vx, &valid, &gain)
                    == ESP_OK && valid) {
                evt_measure_result(ch, (uint8_t)(0x10u | (s_mon.fields & 0x0Fu)),
                                   1, vx, (uint8_t)(avg >> 8), (uint8_t)(avg & 0xFF));
                continue;
            }
        }
        evt_measure_result(ch, (uint8_t)(0x10u | (s_mon.fields & 0x0Fu)), 1,
                           glb, 0, 0);
    }
}

uint8_t svc_measure_active_mask(void)
{
    uint8_t m = 0;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        ch_ctx_t *c = ch_ctx(ch);
        if (c != NULL && c->f0_pending) {
            m |= (uint8_t)(1u << ch);
        }
    }
    if (s_mon.enabled) {
        m |= s_mon.chan_mask;
    }
    return m;
}

static void measure_poll_locked(uint32_t now)
{
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        f0_poll_one(ch, now);
    }
    mon_poll(now);
}

esp_err_t svc_measure_f0_start(uint8_t ch, uint16_t timeout_ms, uint8_t mode, uint8_t *started)
{
    svc_play_lock();
    esp_err_t err = f0_start_locked(ch, timeout_ms, mode, started);
    svc_play_unlock();
    return err;
}

void svc_measure_poll(uint32_t now)
{
    svc_play_lock();
    measure_poll_locked(now);
    svc_play_unlock();
}
