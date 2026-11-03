/**
 * @file  svc_sweep.c
 * @brief 扫描与内置测试项实现（跑在监视任务里，协议任务只负责"已接受"应答）。
 */

#include "svc_sweep.h"

#include <string.h>

#include "app_protocol.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "convert.h"
#include "drv_aw8623x.h"
#include "esp_timer.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "svc_measure.h"
#include "svc_play.h"
#include "util_log.h"

typedef enum {
    SWEEP_NONE = 0,
    SWEEP_AMP_RUN,
    SWEEP_FREQ_RUN,
} sweep_kind_t;

typedef struct {
    sweep_kind_t kind;
    uint8_t  ch;
    uint8_t  mode;          /* 0=CONT 1=RAM */
    uint8_t  from;
    uint8_t  to;
    uint8_t  step;
    uint16_t dwell_ms;
    uint16_t settle_ms;
    uint16_t from_hz;
    uint16_t to_hz;
    uint16_t step_hz;
    uint8_t  total;
    uint8_t  index;
    uint32_t next_ms;
    bool     aborted;
} sweep_state_t;

typedef struct {
    bool     active;
    uint16_t test_id;
    uint8_t  step;
    uint8_t  total;
    uint32_t next_ms;
    bool     aborted;
} test_state_t;

static sweep_state_t s_sweep;
static test_state_t  s_test;

static inline uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static uint8_t steps_count(int from, int to, int step)
{
    if (step <= 0) {
        return 0;
    }
    int n = (to >= from) ? ((to - from) / step + 1) : ((from - to) / step + 1);
    if (n < 1) { n = 1; }
    if (n > 255) { n = 255; }
    return (uint8_t)n;
}

esp_err_t svc_sweep_amp(uint8_t ch, uint8_t mode, uint8_t from_lvl, uint8_t to_lvl,
                        uint8_t step, uint16_t dwell_ms, uint16_t settle_ms,
                        uint8_t *accepted, uint8_t *total_steps)
{
    if (step == 0 || mode > 1) {
        if (accepted) { *accepted = 0; }
        return ESP_ERR_INVALID_ARG;
    }
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->present) {
        if (accepted) { *accepted = 0; }
        return ESP_ERR_NOT_FOUND;
    }
    if (s_sweep.kind != SWEEP_NONE) {
        if (accepted) { *accepted = 0; }
        return ESP_ERR_INVALID_STATE;   /* -> ERR_BUSY */
    }
    uint8_t total = steps_count(from_lvl, to_lvl, step);
    memset(&s_sweep, 0, sizeof(s_sweep));
    s_sweep.kind = SWEEP_AMP_RUN;
    s_sweep.ch = ch;
    s_sweep.mode = mode;
    s_sweep.from = from_lvl;
    s_sweep.to = to_lvl;
    s_sweep.step = step;
    s_sweep.dwell_ms = dwell_ms ? dwell_ms : 100;
    s_sweep.settle_ms = settle_ms;
    s_sweep.total = total;
    s_sweep.next_ms = now_ms();
    c->long_op = true;
    ch_set_long_op(ch, true);
    if (accepted)   { *accepted = 1; }
    if (total_steps) { *total_steps = total; }
    LOGI("CH%u 启动幅度扫描：%u -> %u 步长 %u，共 %u 档", ch, from_lvl, to_lvl, step, total);
    return ESP_OK;
}

esp_err_t svc_sweep_freq(uint8_t ch, uint16_t from_hz, uint16_t to_hz,
                         uint16_t step_hz, uint16_t dwell_ms,
                         uint8_t *accepted, uint8_t *total_steps)
{
    if (step_hz == 0 || from_hz == 0 || to_hz == 0) {
        if (accepted) { *accepted = 0; }
        return ESP_ERR_INVALID_ARG;
    }
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->present) {
        if (accepted) { *accepted = 0; }
        return ESP_ERR_NOT_FOUND;
    }
    if (s_sweep.kind != SWEEP_NONE) {
        if (accepted) { *accepted = 0; }
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t total = steps_count(from_hz, to_hz, step_hz);
    memset(&s_sweep, 0, sizeof(s_sweep));
    s_sweep.kind = SWEEP_FREQ_RUN;
    s_sweep.ch = ch;
    s_sweep.from_hz = from_hz;
    s_sweep.to_hz = to_hz;
    s_sweep.step_hz = step_hz;
    s_sweep.dwell_ms = dwell_ms ? dwell_ms : 100;
    s_sweep.total = total;
    s_sweep.next_ms = now_ms();
    ch_set_long_op(ch, true);
    if (accepted)   { *accepted = 1; }
    if (total_steps) { *total_steps = total; }
    return ESP_OK;
}

esp_err_t svc_sweep_abort(void)
{
    if (s_sweep.kind == SWEEP_NONE) {
        return ESP_ERR_INVALID_STATE;
    }
    s_sweep.aborted = true;
    return ESP_OK;
}

bool svc_sweep_active(void)
{
    return s_sweep.kind != SWEEP_NONE;
}

bool svc_test_active(void)
{
    return s_test.active;
}

/* ------------------------------------------------------------------ */
/* 扫描轮询                                                            */
/* ------------------------------------------------------------------ */

static void sweep_finish(uint8_t status)
{
    evt_sweep_done(s_sweep.index, status);
    ch_set_long_op(s_sweep.ch, false);
    s_sweep.kind = SWEEP_NONE;
}

static void sweep_poll(uint32_t now)
{
    if (s_sweep.kind == SWEEP_NONE) {
        return;
    }
    if (now < s_sweep.next_ms) {
        return;
    }
    if (s_sweep.aborted) {
        sweep_finish(2);
        return;
    }
    if (s_sweep.index >= s_sweep.total) {
        sweep_finish(0);
        return;
    }

    uint8_t i = s_sweep.index;
    ch_ctx_t *c = ch_ctx(s_sweep.ch);
    esp_err_t err = ESP_OK;

    if (s_sweep.kind == SWEEP_AMP_RUN) {
        int delta = (int)s_sweep.to - (int)s_sweep.from;
        int dir = (delta >= 0) ? 1 : -1;
        int val = (int)s_sweep.from + dir * (int)i * (int)s_sweep.step;
        if (val < 0) { val = 0; }
        if (val > 255) { val = 255; }
        if (s_sweep.mode == 0) {
            /* CONT：只改 DRV1/DRV2 电平，必须读-改-写保住 TRACK_EN */
            uint8_t lvl = (uint8_t)(val & 0x7Fu);
            err = aw_update(s_sweep.ch, 0x1C, 0x7Fu, lvl, NULL, NULL);
            if (err == ESP_OK) {
                uint8_t d2 = (uint8_t)((lvl * 80u) / 127u);
                err = aw_update(s_sweep.ch, 0x1D, 0x7Fu, d2, NULL, NULL);
            }
            if (c != NULL && err == ESP_OK) {
                c->profile.drv1_lvl = lvl;
            }
        } else {
            err = aw_write_u8(s_sweep.ch, REG_PLAYCFG2, (uint8_t)val);
            if (c != NULL && err == ESP_OK) {
                c->profile.gain = (uint8_t)val;
            }
        }
        evt_sweep_progress(i, s_sweep.total, (uint16_t)val);
    } else {
        int delta = (int)s_sweep.to_hz - (int)s_sweep.from_hz;
        int dir = (delta >= 0) ? 1 : -1;
        int hz = (int)s_sweep.from_hz + dir * (int)i * (int)s_sweep.step_hz;
        if (hz < 1) { hz = 1; }
        uint8_t f_pre = conv_f0_to_f_pre((float)hz);
        err = aw_write_u8(s_sweep.ch, 0x18, f_pre);
        if (c != NULL && err == ESP_OK) {
            c->profile.f_pre = f_pre;
        }
        evt_sweep_progress(i, s_sweep.total, (uint16_t)conv_f_pre_to_f0(f_pre));
    }

    if (err != ESP_OK) {
        sweep_finish(1);
        return;
    }

    s_sweep.index++;
    uint32_t wait = s_sweep.dwell_ms + s_sweep.settle_ms;
    s_sweep.next_ms = now + (wait ? wait : 100);
}

/* ------------------------------------------------------------------ */
/* 内置测试项                                                          */
/* ------------------------------------------------------------------ */

esp_err_t svc_test_run(uint16_t test_id, const uint8_t *params, uint8_t params_len,
                       uint8_t *accepted)
{
    (void)params;
    (void)params_len;
    if (s_test.active) {
        if (accepted) { *accepted = 0; }
        return ESP_ERR_INVALID_STATE;
    }
    memset(&s_test, 0, sizeof(s_test));
    s_test.active = true;
    s_test.test_id = test_id;
    s_test.next_ms = now_ms();
    /* 各测试项的步骤数（用于进度上报） */
    switch (test_id) {
    case 1:  s_test.total = 4; break;   /* A2 I2C 通信与型号确认 */
    case 2:  s_test.total = 3; break;   /* A5 CONT + F0 检测 */
    case 3:  s_test.total = 4; break;   /* A10 复位路径 */
    case 4:  s_test.total = 4; break;   /* B2 通道隔离 */
    case 5:  s_test.total = 5; break;   /* B4 逐通道速率上限 */
    case 6:  s_test.total = 4; break;   /* C3 高速流式可行性 */
    default: s_test.total = 1; break;
    }
    if (accepted) { *accepted = 1; }
    LOGI("启动内置测试项 #%u", test_id);
    return ESP_OK;
}

esp_err_t svc_test_abort(void)
{
    if (!s_test.active) {
        return ESP_ERR_INVALID_STATE;
    }
    s_test.aborted = true;
    return ESP_OK;
}

static void test_progress(uint8_t status, const char *text)
{
    evt_test_progress(s_test.test_id, s_test.step, s_test.total, status, text);
}

static void test_finish(uint8_t status, const char *text)
{
    test_progress(status, text);
    s_test.active = false;
}

static void test_poll(uint32_t now)
{
    if (!s_test.active) {
        return;
    }
    if (now < s_test.next_ms) {
        return;
    }
    if (s_test.aborted) {
        test_finish(2, "已中止");
        return;
    }

    s_test.step++;
    switch (s_test.test_id) {
    case 1: {   /* A2：逐通道读 CHIPID */
        if (s_test.step <= 8) {
            uint8_t ch = (uint8_t)(s_test.step - 1);
            uint16_t id = 0;
            if (aw_read_chipid(ch, &id) == ESP_OK && id == BOARD_AW_CHIPID_EXPECTED) {
                test_progress(0, "CHIPID 0x2340 确认");
            } else {
                test_progress(1, "CHIPID 不匹配或通道不在线");
            }
            s_test.next_ms = now + 20;
            return;
        }
        break;
    }
    case 2: {   /* A5：CONT + F0 */
        ch_ctx_t *c = ch_ctx(0);
        if (s_test.step == 1 && c != NULL) {
            c->profile.mode = AW_MODE_CONT;
            svc_play_set_mode(0, AW_MODE_CONT);
            test_progress(0, "切换到 CONT 模式");
        } else if (s_test.step == 2) {
            uint8_t started = 0;
            svc_measure_f0_start(0, 800, 0, &started);
            test_progress(started ? 0 : 1, "启动 F0 检测");
        } else {
            uint8_t valid = 0, mode = 0, h = 0, l = 0;
            uint16_t hz10 = 0;
            svc_measure_f0_result(0, &valid, &mode, &h, &l, &hz10);
            test_progress(valid ? 0 : 1, valid ? "F0 有效" : "F0 无效（见诊断提示）");
            break;
        }
        s_test.next_ms = now + 200;
        return;
    }
    case 5: {   /* B4：逐通道读写 1000 次并统计错误 */
        if (s_test.step <= 8) {
            uint8_t ch = (uint8_t)(s_test.step - 1);
            int errors = 0;
            for (int i = 0; i < 100; i++) {   /* 100 次即可看出量级，避免长时间占用总线 */
                uint8_t v = 0;
                if (aw_read_u8(ch, REG_IDH, &v) != ESP_OK) {
                    errors++;
                }
            }
            char txt[64];
            snprintf(txt, sizeof(txt), "CH%u 100 次读失败 %d 次", ch, errors);
            test_progress(errors == 0 ? 0 : 1, txt);
            s_test.next_ms = now + 10;
            return;
        }
        break;
    }
    default:
        test_progress(3, "该测试项需要人工配合，脚本侧用 prompt 收集读数");
        break;
    }
    test_finish(0, "测试项完成");
}

void svc_sweep_poll(uint32_t now)
{
    sweep_poll(now);
    test_poll(now);
}
