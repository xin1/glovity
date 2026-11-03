/**
 * @file  aw_profile.c
 * @brief Profile 的默认值 / 线序编解码 / 校验。纯逻辑。
 */

#include "aw_profile.h"

#include <stdio.h>
#include <string.h>

#include "cfg_defaults.h"
#include "convert.h"
#include "proto_cmd.h"
#include "proto_frame.h"    /* proto_put_u16 / proto_get_u16 */

/* 校验表：{偏移, 最小, 最大, 中文名} */
typedef struct {
    uint8_t off;
    uint8_t lo;
    uint8_t hi;
    const char *name;
} field_rule_t;

static const field_rule_t k_rules[] = {
    {  1,  0,   3, "mode（播放模式）" },
    {  2,  0, 255, "gain（增益 GAIN）" },
    {  3,  0,   1, "brk_en（自动刹车）" },
    {  4,  0,   1, "stop_mode（停止方式）" },
    {  5,  0,   1, "vbat_mode" },
    {  6,  0,   7, "vbat_ref" },
    {  7,  0,   1, "en_fir" },
    { 16,  0,  15, "mainloop（主循环）" },
    { 17,  0,   3, "waitslot（等待单位）" },
    { 28,  0,   1, "en_f0_det" },
    { 29,  0,   1, "sin_mode" },
    { 30,  0,  15, "edge_fre" },
    { 31,  1, 255, "f_pre（不可为 0）" },
    { 32,  0, 255, "drv_width" },
    { 33,  0, 255, "wait_num" },
    { 34,  0,  15, "brk_gain" },
    { 35,  0,   1, "track_en" },
    { 36,  0, 127, "drv1_lvl（7 位字段）" },
    { 37,  0, 127, "drv2_lvl（7 位字段）" },
    { 38,  0, 255, "drv1_time" },
    { 39,  0, 255, "drv2_time" },
    { 40,  0, 255, "brk_time" },
    { 41,  0, 255, "track_margin" },
    { 42,  0,   3, "wavdat_mode" },
    { 43,  0,   3, "rtp_flags" },
    { 50,  0,   1, "trig1_pos_en" },
    { 51,  0, 127, "trig1_seq_p" },
    { 52,  0,   1, "trig1_neg_en" },
    { 53,  0, 127, "trig1_seq_n" },
    { 54,  0,   1, "trig1_polar" },
    { 55,  0,   1, "trig1_lev" },
    { 56,  1,   1, "trig1_brk（TRIG 模式下必须 = 1，不可关）" },
    { 57,  1,   1, "trig1_stop（必须 = 1，否则 TRIG 播放无法立即停止）" },
    { 58,  0,   3, "trig1_prio" },
    { 59,  0, 255, "trg_gain" },
    { 60,  3,   3, "trig1_mode（必须 = 3，默认 7 是 None）" },
    { 61,  0,   0, "intn_pin（本板必须 = 0，A1 作 TRIG1）" },
    { 62,  0, 255, "start_dly" },
    { 63,  0,   0, "reserved1（必须 0）" },
};

void aw_profile_defaults(aw_profile_t *p)
{
    if (p == NULL) {
        return;
    }
    memset(p, 0, sizeof(*p));
    p->profile_ver = AW_PROFILE_VER;
    p->mode        = AW_MODE_RAM;
    p->gain        = DEF_GAIN;
    p->brk_en      = DEF_BRK_EN;
    p->stop_mode   = DEF_STOP_MODE;
    p->vbat_mode   = DEF_VBAT_MODE;
    p->vbat_ref    = DEF_VBAT_REF;
    p->en_fir      = DEF_EN_FIR;

    p->mainloop    = 0;
    p->waitslot    = 0;

    p->en_f0_det   = DEF_EN_F0_DET;
    p->sin_mode    = DEF_SIN_MODE;
    p->edge_fre    = DEF_EDGE_FRE;
    p->f_pre       = DEF_F_PRE;
    p->drv_width   = DEF_DRV_WIDTH;
    p->wait_num    = DEF_WAIT_NUM;
    p->brk_gain    = DEF_BRK_GAIN;
    p->track_en    = DEF_TRACK_EN;
    p->drv1_lvl    = DEF_DRV1_LVL;
    p->drv2_lvl    = DEF_DRV2_LVL;
    p->drv1_time   = DEF_DRV1_TIME;
    p->drv2_time   = DEF_DRV2_TIME;
    p->brk_time    = DEF_BRK_TIME;
    p->track_margin = DEF_TRACK_MARGIN;

    p->wavdat_mode = DEF_WAVDAT_MODE;
    p->rtp_flags   = DEF_RTP_FLAGS;
    p->base_addr   = DEF_BASE_ADDR;
    p->fifo_ae     = DEF_FIFO_AE;
    p->fifo_af     = DEF_FIFO_AF;

    p->trig1_pos_en = DEF_TRIG1_POS_EN;
    p->trig1_seq_p  = DEF_TRIG1_SEQ_P;
    p->trig1_neg_en = DEF_TRIG1_NEG_EN;
    p->trig1_seq_n  = DEF_TRIG1_SEQ_N;
    p->trig1_polar  = DEF_TRIG1_POLAR;
    p->trig1_lev    = DEF_TRIG1_LEV;
    p->trig1_brk    = DEF_TRIG1_BRK;
    p->trig1_stop   = DEF_TRIG1_STOP;
    p->trig1_prio   = DEF_TRIG1_PRIO;
    p->trg_gain     = DEF_TRG_GAIN;
    p->trig1_mode   = DEF_TRIG1_MODE;
    p->intn_pin     = DEF_INTN_PIN;
    p->start_dly    = DEF_START_DLY;
    p->reserved1    = 0;
}

size_t aw_profile_to_bytes(const aw_profile_t *p, uint8_t *out, size_t cap)
{
    if (p == NULL || out == NULL || cap < AW_PROFILE_LEN) {
        return 0;
    }
    size_t i = 0;
    out[i++] = p->profile_ver;
    out[i++] = p->mode;
    out[i++] = p->gain;
    out[i++] = p->brk_en;
    out[i++] = p->stop_mode;
    out[i++] = p->vbat_mode;
    out[i++] = p->vbat_ref;
    out[i++] = p->en_fir;
    memcpy(&out[i], p->wavseq, 8); i += 8;
    out[i++] = p->mainloop;
    out[i++] = p->waitslot;
    memcpy(&out[i], p->seq_loop, 4); i += 4;
    memcpy(&out[i], p->reserved_ram, 6); i += 6;
    out[i++] = p->en_f0_det;
    out[i++] = p->sin_mode;
    out[i++] = p->edge_fre;
    out[i++] = p->f_pre;
    out[i++] = p->drv_width;
    out[i++] = p->wait_num;
    out[i++] = p->brk_gain;
    out[i++] = p->track_en;
    out[i++] = p->drv1_lvl;
    out[i++] = p->drv2_lvl;
    out[i++] = p->drv1_time;
    out[i++] = p->drv2_time;
    out[i++] = p->brk_time;
    out[i++] = p->track_margin;
    out[i++] = p->wavdat_mode;
    out[i++] = p->rtp_flags;
    proto_put_u16(&out[i], p->base_addr); i += 2;
    proto_put_u16(&out[i], p->fifo_ae);   i += 2;
    proto_put_u16(&out[i], p->fifo_af);   i += 2;
    out[i++] = p->trig1_pos_en;
    out[i++] = p->trig1_seq_p;
    out[i++] = p->trig1_neg_en;
    out[i++] = p->trig1_seq_n;
    out[i++] = p->trig1_polar;
    out[i++] = p->trig1_lev;
    out[i++] = p->trig1_brk;
    out[i++] = p->trig1_stop;
    out[i++] = p->trig1_prio;
    out[i++] = p->trg_gain;
    out[i++] = p->trig1_mode;
    out[i++] = p->intn_pin;
    out[i++] = p->start_dly;
    out[i++] = p->reserved1;
    return i;
}

bool aw_profile_from_bytes(aw_profile_t *p, const uint8_t *in, size_t len)
{
    if (p == NULL || in == NULL || len < AW_PROFILE_LEN) {
        return false;
    }
    size_t i = 0;
    p->profile_ver = in[i++];
    p->mode        = in[i++];
    p->gain        = in[i++];
    p->brk_en      = in[i++];
    p->stop_mode   = in[i++];
    p->vbat_mode   = in[i++];
    p->vbat_ref    = in[i++];
    p->en_fir      = in[i++];
    memcpy(p->wavseq, &in[i], 8); i += 8;
    p->mainloop    = in[i++];
    p->waitslot    = in[i++];
    memcpy(p->seq_loop, &in[i], 4); i += 4;
    memcpy(p->reserved_ram, &in[i], 6); i += 6;
    p->en_f0_det   = in[i++];
    p->sin_mode    = in[i++];
    p->edge_fre    = in[i++];
    p->f_pre       = in[i++];
    p->drv_width   = in[i++];
    p->wait_num    = in[i++];
    p->brk_gain    = in[i++];
    p->track_en    = in[i++];
    p->drv1_lvl    = in[i++];
    p->drv2_lvl    = in[i++];
    p->drv1_time   = in[i++];
    p->drv2_time   = in[i++];
    p->brk_time    = in[i++];
    p->track_margin = in[i++];
    p->wavdat_mode = in[i++];
    p->rtp_flags   = in[i++];
    p->base_addr   = proto_get_u16(&in[i]); i += 2;
    p->fifo_ae     = proto_get_u16(&in[i]); i += 2;
    p->fifo_af     = proto_get_u16(&in[i]); i += 2;
    p->trig1_pos_en = in[i++];
    p->trig1_seq_p  = in[i++];
    p->trig1_neg_en = in[i++];
    p->trig1_seq_n  = in[i++];
    p->trig1_polar  = in[i++];
    p->trig1_lev    = in[i++];
    p->trig1_brk    = in[i++];
    p->trig1_stop   = in[i++];
    p->trig1_prio   = in[i++];
    p->trg_gain     = in[i++];
    p->trig1_mode   = in[i++];
    p->intn_pin     = in[i++];
    p->start_dly    = in[i++];
    p->reserved1    = in[i++];
    return true;
}

bool aw_profile_validate(const aw_profile_t *p, uint8_t *offset_out,
                         char *msg, size_t msg_cap)
{
    if (offset_out) {
        *offset_out = 0;
    }
    if (msg && msg_cap) {
        msg[0] = '\0';
    }
    if (p == NULL) {
        return false;
    }
    if (p->profile_ver != AW_PROFILE_VER) {
        if (offset_out) { *offset_out = 0; }
        if (msg && msg_cap) {
            snprintf(msg, msg_cap, "profile_ver 必须为 %u，实际 %u",
                     (unsigned)AW_PROFILE_VER, (unsigned)p->profile_ver);
        }
        return false;
    }
    uint8_t buf[AW_PROFILE_LEN];
    if (aw_profile_to_bytes(p, buf, sizeof(buf)) != AW_PROFILE_LEN) {
        return false;
    }
    for (size_t k = 0; k < sizeof(k_rules) / sizeof(k_rules[0]); k++) {
        const field_rule_t *r = &k_rules[k];
        uint8_t v = buf[r->off];   /* 用线序取值，保证规则与字节偏移严格对应 */
        if (v < r->lo || v > r->hi) {
            if (offset_out) { *offset_out = r->off; }
            if (msg && msg_cap) {
                snprintf(msg, msg_cap, "%s 超出范围 %u..%u，实际 %u",
                         r->name, (unsigned)r->lo, (unsigned)r->hi, (unsigned)v);
            }
            return false;
        }
    }
    /* 保留字节必须为 0（协议 §14：接收方不得因保留字节非 0 报错，但可以记录） */
    for (size_t k = 0; k < 6; k++) {
        if (p->reserved_ram[k] != 0) {
            if (offset_out) { *offset_out = (uint8_t)(22 + k); }
            if (msg && msg_cap) {
                snprintf(msg, msg_cap, "reserved_ram[%u] 必须为 0", (unsigned)k);
            }
            return false;
        }
    }
    /* 48 kHz 在本板不可用 */
    if (p->wavdat_mode == RATE_48K) {
        if (offset_out) { *offset_out = 42; }
        if (msg && msg_cap) {
            snprintf(msg, msg_cap, "wavdat_mode = 48K 在本板不可用（需 ≥1 MHz I2C）");
        }
        return false;
    }
    /* 驱动宽度必须小于 F0 半周期 */
    if (p->f_pre > 0 && p->drv_width > 0) {
        float half_us = 1e6f / (24000.0f / (float)p->f_pre) / 2.0f;
        uint32_t width_us = conv_code_to_us(p->drv_width);
        if ((float)width_us >= half_us) {
            if (offset_out) { *offset_out = 32; }
            if (msg && msg_cap) {
                snprintf(msg, msg_cap,
                         "drv_width = %u µs 不小于 F0 半周期 %.1f µs",
                         (unsigned)width_us, (double)half_us);
            }
            return false;
        }
    }
    return true;
}

int aw_profile_diff(const aw_profile_t *a, const aw_profile_t *b,
                    char *msg, size_t msg_cap)
{
    if (a == NULL || b == NULL) {
        return -1;
    }
    uint8_t ba[AW_PROFILE_LEN];
    uint8_t bb[AW_PROFILE_LEN];
    aw_profile_to_bytes(a, ba, sizeof(ba));
    aw_profile_to_bytes(b, bb, sizeof(bb));
    int n = 0;
    if (msg && msg_cap) {
        msg[0] = '\0';
    }
    for (size_t i = 0; i < AW_PROFILE_LEN; i++) {
        if (ba[i] != bb[i]) {
            n++;
            if (msg && msg_cap) {
                size_t used = strlen(msg);
                if (used + 16 < msg_cap) {
                    snprintf(msg + used, msg_cap - used, "off%u:%u!=%u ",
                             (unsigned)i, (unsigned)ba[i], (unsigned)bb[i]);
                }
            }
        }
    }
    return n;
}

void aw_profile_set_f0(aw_profile_t *p, float f0_hz)
{
    if (p != NULL) {
        p->f_pre = conv_f0_to_f_pre(f0_hz);
    }
}

float aw_profile_f0(const aw_profile_t *p)
{
    return (p == NULL) ? 0.0f : conv_f_pre_to_f0(p->f_pre);
}

bool aw_profile_is_full_amplitude(const aw_profile_t *p, uint8_t mode)
{
    if (p == NULL) {
        return false;
    }
    if (mode == AW_MODE_CONT) {
        return p->drv1_lvl > BOARD_FULL_AMP_CONT_DRV1_LVL;
    }
    if (mode == AW_MODE_RAM || mode == AW_MODE_RTP) {
        return p->gain > BOARD_FULL_AMP_RAM_GAIN;
    }
    /* TRIG 用 trg_gain 判定 */
    if (mode == AW_MODE_TRIG) {
        return p->trg_gain > BOARD_FULL_AMP_RAM_GAIN;
    }
    return false;
}
