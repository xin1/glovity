/**
 * @file  svc_selftest.c
 * @brief 自检实现（状态机，由监视任务推进，不阻塞协议任务）。
 */

#include "svc_selftest.h"

#include <stdio.h>
#include <string.h>

#include "app_protocol.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "drv_aw8623x.h"
#include "drv_i2c_bus.h"
#include "drv_tca9548a.h"
#include "esp_timer.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "svc_measure.h"
#include "util_log.h"

typedef enum {
    ST_IDLE = 0,
    ST_BUS,
    ST_TCA,
    ST_CHIPID,
    ST_SOFTRST,
    ST_IMPEDANCE,
    ST_DONE,
} st_step_t;

static struct {
    bool        active;
    st_step_t   step;
    uint8_t     ch;
    uint32_t    next_ms;
    uint8_t     issue_count;
    uint8_t     vreg_mask;
    uint8_t     log_level_pre;      /* 自检前的日志级别，结束时还原 */
    selftest_summary_t summary;
} s_st;

static inline uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void svc_selftest_start(void)
{
    memset(&s_st, 0, sizeof(s_st));
    s_st.active = true;
    s_st.step = ST_BUS;
    s_st.ch = 0;
    s_st.next_ms = now_ms();
    s_st.summary.overall = 2;
    /* ★ 自检的产出（各通道 CHIPID/VREG、LRA 阻抗、汇总）全部是 LOGI，
     *   而默认日志级别是 WARN（DEF_LOG_LEVEL=2）—— 不临时抬级别的话，
     *   用户跑完 selftest 只能看到"CH0~CH3 无应答"这几条告警，
     *   看不到任何结论，会误以为自检没跑或全挂。结束时在 finish() 里还原。 */
    s_st.log_level_pre = log_get_level();
    if (s_st.log_level_pre < LOG_INFO) {
        log_set_level(LOG_INFO);
    }
    LOGI("自检开始");
}

bool svc_selftest_active(void)
{
    return s_st.active;
}

void svc_selftest_summary(selftest_summary_t *out)
{
    if (out) {
        *out = s_st.summary;
    }
}

static void finish(void)
{
    char text[192];
    snprintf(text, sizeof(text),
             "自检汇总：在线通道掩码 0x%02X，I2C=%s，TCA=%s，VREG 掩码 0x%02X，问题 %u 项",
             s_st.summary.present_mask,
             (s_st.summary.bus_sel == 1) ? "LP" : (s_st.summary.bus_sel == 2 ? "HP" : "未知"),
             s_st.summary.tca ? "在线" : "不在线",
             s_st.summary.vreg_mask, s_st.summary.issue_count);
    evt_selftest_result(s_st.summary.overall, s_st.summary.present_mask,
                        s_st.summary.bus_sel, s_st.summary.tca,
                        s_st.summary.vreg_mask, s_st.summary.issue_count, text);
    LOGI("%s", text);
    s_st.active = false;
    s_st.step = ST_IDLE;
    log_set_level(s_st.log_level_pre);   /* 还原 svc_selftest_start() 里抬高过的日志级别 */
}

void svc_selftest_poll(uint32_t now)
{
    if (!s_st.active || now < s_st.next_ms) {
        return;
    }

    switch (s_st.step) {
    case ST_BUS: {
        const bus_info_t *bi = bus_info();
        s_st.summary.bus_sel = bi ? bi->sel_used : 0;
        if (s_st.summary.bus_sel == 0) {
            LOGW("自检：未探测到可用的 I2C 引脚组合（检查 J1 跳线）");
            s_st.issue_count++;
        } else {
            LOGI("自检：I2C 引脚 = %s（SDA=IO%u SCL=IO%u）@%u Hz，内部上拉=%s",
                 (bi->sel_used == 1 ? "LP" : "HP"), bi->sda_gpio, bi->scl_gpio,
                 (unsigned)bi->speed_hz, bi->internal_pullup ? "启用（降级运行）" : "关闭");
            if (bi->internal_pullup) {
                s_st.issue_count++;
            }
            if (!bi->probe_speed_ok) {
                LOGW("自检：400 kHz 下通信不稳，已降到 100 kHz（疑似上游上拉不足，V-1）");
                s_st.issue_count++;
            }
        }
        s_st.step = ST_TCA;
        s_st.next_ms = now + 5;
        break;
    }
    case ST_TCA: {
        esp_err_t err = ch_lock();
        uint8_t ctrl = 0;
        if (err == ESP_OK) {
            err = tca_read_ctrl(&ctrl);
            ch_unlock();
        }
        s_st.summary.tca = (err == ESP_OK) ? 1u : 0u;
        if (err == ESP_OK) {
            LOGI("自检：TCA9548A @0x%02X 在线，控制寄存器 0x%02X",
                 BOARD_TCA9548A_ADDR, ctrl);
        } else {
            LOGE("自检：TCA9548A 无应答（检查跳线/上拉/供电）");
            s_st.issue_count++;
        }
        s_st.step = ST_CHIPID;
        s_st.ch = 0;
        s_st.next_ms = now + 5;
        break;
    }
    case ST_CHIPID: {
        uint8_t ch = s_st.ch;
        ch_ctx_t *c = ch_ctx(ch);
        uint16_t id = 0;
        uint8_t vreg = 0xFF;
        esp_err_t err = aw_read_chipid(ch, &id);
        if (err == ESP_OK) {
            (void)aw_read_vreg_ok(ch, &vreg);
            c->present = 1;
            c->chipid = id;
            c->vreg_ok = vreg;
            c->state = CH_IDLE;
            s_st.summary.present_mask |= (uint8_t)(1u << ch);
            if (vreg == 1u) {
                s_st.vreg_mask |= (uint8_t)(1u << ch);
            } else if (vreg == 0u) {
                s_st.issue_count++;
            }
            if (id != BOARD_AW_CHIPID_EXPECTED) {
                LOGW("自检：CH%u CHIPID = 0x%04X（期望 0x%04X，可能是 AW86234A 等变体）",
                     ch, id, BOARD_AW_CHIPID_EXPECTED);
                s_st.issue_count++;
            } else {
                LOGI("自检：CH%u 在线，CHIPID 0x%04X，VREG %s", ch, id,
                     (vreg == 1u) ? "OK" : (vreg == 0u ? "异常" : "未读到"));
            }
        } else {
            c->present = 0;
            LOGW("自检：CH%u 无应答（0x5A 无 ACK；检查该路线束/驱动板/VDD）", ch);
        }
        s_st.ch++;
        if (s_st.ch >= BOARD_CHANNEL_COUNT) {
            s_st.summary.vreg_mask = s_st.vreg_mask;
            s_st.step = ST_SOFTRST;
            s_st.ch = 0;
        }
        s_st.next_ms = now + 5;
        break;
    }
    case ST_SOFTRST: {
        uint8_t ch = s_st.ch;
        ch_ctx_t *c = ch_ctx(ch);
        if (c->present) {
            esp_err_t err = aw_softrst(ch);
            if (err == ESP_OK) {
                err = ch_reapply_profile(ch);
            }
            if (err != ESP_OK) {
                LOGW("自检：CH%u 软复位或关键位回读失败（TRIG 模式将被拒绝）", ch);
                s_st.issue_count++;
            }
        }
        s_st.ch++;
        if (s_st.ch >= BOARD_CHANNEL_COUNT) {
            s_st.step = ST_IMPEDANCE;
            s_st.ch = 0;
        }
        s_st.next_ms = now + 5;
        break;
    }
    case ST_IMPEDANCE: {
        uint8_t ch = s_st.ch;
        ch_ctx_t *c = ch_ctx(ch);
        if (c->present) {
            uint16_t adc = 0, avg = 0, vx = 0xFFFF;
            uint8_t valid = 0, gain = 8;
            if (svc_measure_det_read(ch, DET_SEQ_RL, &adc, &avg, &vx, &valid, &gain)
                    == ESP_OK && valid) {
                LOGI("自检：CH%u LRA 阻抗 ≈ %.2f Ω（AVG=%u，D2S=%u）",
                     ch, (double)vx / 100.0, avg, gain);
            } else {
                LOGW("自检：CH%u LRA 阻抗读数无效——检查马达是否插好、输出球 A3/C3 是否虚焊、"
                     "芯片是否被置成 WAKE/STANDBY 模式（0x46 bit7/bit6 必须为 0）",
                     ch);
                s_st.issue_count++;
            }
        }
        s_st.ch++;
        if (s_st.ch >= BOARD_CHANNEL_COUNT) {
            s_st.step = ST_DONE;
        }
        s_st.next_ms = now + 5;
        break;
    }
    case ST_DONE:
    default: {
        uint8_t present = s_st.summary.present_mask;
        if (present == 0) {
            s_st.summary.overall = 2;
        } else if (s_st.issue_count > 0) {
            s_st.summary.overall = 1;
        } else {
            s_st.summary.overall = 0;
        }
        s_st.summary.issue_count = s_st.issue_count;
        finish();
        break;
    }
    }
}
