/**
 * @file  drv_aw8623x.c
 * @brief AW86234FCR 驱动实现。
 *
 * 全部寄存器地址与位域取自 `02_AW8623X寄存器与模式参考.md` 与
 * `reference/AW8623X_寄存器全表.md`（数据手册 V1.4 逐位提取）。
 *
 * ★ 三个"默认值陷阱"必须处理（`02` 文档 §2）：
 *   T-1 `0x45 TRG1_MODE` 默认 7（None）  → TRIG 模式必须写 3
 *   T-2 `0x45 INTN_PIN`   默认 1（INTN） → 本板必须写 0
 *   T-3 `0x33 TRG1_POS`   默认 0         → TRIG 模式必须置 bit7
 *   T-4 `0x3A TRG1_STOP`  默认 0         → 必须置 bit2，否则 TRIG 停不下来
 */

#include "drv_aw8623x.h"

#include <string.h>

#include "cfg_board.h"
#include "cfg_defaults.h"
#include "convert.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "util_log.h"

static void delay_ms(uint32_t ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

/* ================================================================== */
/* 基础寄存器访问                                                      */
/* ================================================================== */

esp_err_t aw_read(uint8_t ch, uint8_t reg, uint8_t *out, size_t len)
{
    if (out == NULL || len == 0 || len > BOARD_REG_MAX_LEN) {
        return ESP_ERR_INVALID_ARG;
    }
    return ch_xfer(ch, &reg, 1, out, len, BOARD_I2C_XFER_TIMEOUT_MS);
}

esp_err_t aw_read_u8(uint8_t ch, uint8_t reg, uint8_t *out)
{
    return aw_read(ch, reg, out, 1);
}

esp_err_t aw_write(uint8_t ch, uint8_t reg, const uint8_t *data, size_t len)
{
    if (data == NULL || len == 0 || len > (BOARD_REG_MAX_LEN - 1)) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t buf[BOARD_REG_MAX_LEN];
    buf[0] = reg;
    memcpy(&buf[1], data, len);
    return ch_xfer(ch, buf, len + 1, NULL, 0, BOARD_I2C_XFER_TIMEOUT_MS);
}

esp_err_t aw_write_u8(uint8_t ch, uint8_t reg, uint8_t value)
{
    return aw_write(ch, reg, &value, 1);
}

esp_err_t aw_update(uint8_t ch, uint8_t reg, uint8_t mask, uint8_t value,
                    uint8_t *old_out, uint8_t *new_out)
{
    uint8_t old = 0;
    esp_err_t err = aw_read_u8(ch, reg, &old);
    if (err != ESP_OK) {
        return err;
    }
    uint8_t neu = (uint8_t)((old & (uint8_t)~mask) | (value & mask));
    if (old_out) {
        *old_out = old;
    }
    if (new_out) {
        *new_out = neu;
    }
    if (neu == old) {
        return ESP_OK;
    }
    return aw_write_u8(ch, reg, neu);
}

esp_err_t aw_regs_read(uint8_t ch, uint8_t addr, uint8_t *out, size_t len)
{
    return aw_read(ch, addr, out, len);
}

/* ================================================================== */
/* 器件状态                                                            */
/* ================================================================== */

esp_err_t aw_read_chipid(uint8_t ch, uint16_t *chipid)
{
    uint8_t h = 0, l = 0;
    esp_err_t err = aw_read_u8(ch, REG_IDH, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = aw_read_u8(ch, REG_IDL, &l);
    if (err != ESP_OK) {
        return err;
    }
    if (chipid) {
        *chipid = (uint16_t)(((uint16_t)h << 8) | l);
    }
    return ESP_OK;
}

esp_err_t aw_read_vreg_ok(uint8_t ch, uint8_t *vreg_ok)
{
    uint8_t v = 0;
    if (vreg_ok == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = aw_read_u8(ch, REG_SYSST2, &v);
    if (err != ESP_OK) {
        *vreg_ok = 0xFF;
        return err;
    }
    *vreg_ok = (v & BIT_LDO_OK) ? 1u : 0u;
    return ESP_OK;
}

esp_err_t aw_get_glb_state(uint8_t ch, uint8_t *glb_state)
{
    uint8_t v = 0;
    if (glb_state == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = aw_read_u8(ch, REG_GLBRD5, &v);
    if (err != ESP_OK) {
        return err;
    }
    *glb_state = (uint8_t)(v & 0x0Fu);
    return ESP_OK;
}

esp_err_t aw_get_sysst(uint8_t ch, uint8_t *sysst)
{
    return aw_read_u8(ch, REG_SYSST, sysst);
}

esp_err_t aw_get_sysst2(uint8_t ch, uint8_t *sysst2)
{
    return aw_read_u8(ch, REG_SYSST2, sysst2);
}

/** 读 SYSINT（0x02，RC 读清）。bit0 = DONEI：播放结束事件已发生过。 */
esp_err_t aw_get_sysint(uint8_t ch, uint8_t *sysint)
{
    if (sysint == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    return aw_read_u8(ch, REG_SYSINT, sysint);
}

/** 读一次 SYSINT 以清掉已锁存的事件（RC 语义：读即清）。 */
esp_err_t aw_clear_sysint(uint8_t ch)
{
    uint8_t dummy = 0;
    return aw_read_u8(ch, REG_SYSINT, &dummy);
}

/* ================================================================== */
/* 上电序列                                                            */
/* ================================================================== */

esp_err_t aw_softrst(uint8_t ch)
{
    esp_err_t err = aw_write_u8(ch, REG_RSTCFG, BOARD_AW_SOFTRST_VAL);
    if (err != ESP_OK) {
        return err;
    }
    delay_ms(2);
    /* 软复位清了全部配置寄存器，必须重新 WAKE（Profile 由调用方重放） */
    return aw_wake(ch);
}

esp_err_t aw_wake(uint8_t ch)
{
    return aw_update(ch, REG_SYSCTRL2, BIT_WAKE, BIT_WAKE, NULL, NULL);
}

esp_err_t aw_standby(uint8_t ch)
{
    return aw_update(ch, REG_SYSCTRL2, BIT_STANDBY, BIT_STANDBY, NULL, NULL);
}

/* ================================================================== */
/* Profile 应用                                                        */
/* ================================================================== */

static esp_err_t apply_ram_segment(uint8_t ch, const aw_profile_t *p)
{
    esp_err_t err = aw_write(ch, REG_WAVCFG1, p->wavseq, 8);
    if (err != ESP_OK) {
        return err;
    }
    err = aw_write(ch, REG_WAVCFG9, p->seq_loop, 4);
    if (err != ESP_OK) {
        return err;
    }
    /* 0x16 WAVCFG13：bit3:0 MAINLOOP，bit5:4 WAITSLOT —— 读-改-写保住其他位。
     * ★ MAINLOOP=0xF 才是"无限循环直到 STOP"，0x0~0xE 是"播放 (code+1) 次"；
     *   这是"持续振动"的唯一开关（CONT 模式的时长上限只有 DRV1_TIME 个半周期）。 */
    uint8_t v = (uint8_t)((p->mainloop & 0x0Fu) | ((p->waitslot & 0x03u) << 4));
    return aw_update(ch, REG_WAVCFG13, 0x3Fu, v, NULL, NULL);
}

static esp_err_t apply_cont_segment(uint8_t ch, const aw_profile_t *p)
{
    esp_err_t err;
    /* 0x17 CONTCFG1：bit5 EN_F0_DET、bit4 SIN_MODE、bit3:0 EDGE_FRE */
    uint8_t v17 = (uint8_t)(((p->en_f0_det & 1u) << 5) |
                            ((p->sin_mode & 1u) << 4) |
                            (p->edge_fre & 0x0Fu));
    err = aw_update(ch, REG_CONTCFG1, 0x3Fu, v17, NULL, NULL);
    if (err != ESP_OK) { return err; }

    uint8_t v18 = p->f_pre ? p->f_pre : DEF_F_PRE;
    err = aw_write_u8(ch, 0x18, v18);                    /* F_PRE */
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, 0x19, p->drv_width);           /* DRV_WIDTH */
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, 0x1A, p->wait_num);            /* WAIT_NUM */
    if (err != ESP_OK) { return err; }
    err = aw_update(ch, 0x1B, 0x0Fu, p->brk_gain & 0x0Fu, NULL, NULL);  /* BRK_GAIN */
    if (err != ESP_OK) { return err; }

    /* ★ 0x1C：bit7 TRACK_EN + bit6:0 DRV1_LVL —— 必须读-改-写（`02` 文档 W-2） */
    uint8_t v1c = (uint8_t)(((p->track_en & 1u) << 7) | (p->drv1_lvl & 0x7Fu));
    err = aw_update(ch, 0x1C, 0xFFu, v1c, NULL, NULL);
    if (err != ESP_OK) { return err; }
    /* 0x1D：bit6:0 DRV2_LVL（bit7 也是其他字段，读-改-写） */
    err = aw_update(ch, 0x1D, 0x7Fu, p->drv2_lvl & 0x7Fu, NULL, NULL);
    if (err != ESP_OK) { return err; }

    err = aw_write_u8(ch, 0x1E, p->drv1_time);
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, 0x1F, p->drv2_time);
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, 0x20, p->brk_time);
    if (err != ESP_OK) { return err; }
    return aw_write_u8(ch, 0x21, p->track_margin);
}

static esp_err_t apply_rtp_segment(uint8_t ch, const aw_profile_t *p)
{
    esp_err_t err = aw_update(ch, REG_BASE_ADDR_H, 0x0Fu,
                              (uint8_t)((p->base_addr >> 8) & 0x0Fu), NULL, NULL);
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, REG_BASE_ADDR_L, (uint8_t)(p->base_addr & 0xFFu));
    if (err != ESP_OK) { return err; }
    /* 0x2E：bit7:4 FIFO_AEH，bit3:0 FIFO_AFH */
    uint8_t v2e = (uint8_t)((((p->fifo_ae >> 8) & 0x0Fu) << 4) |
                            ((p->fifo_af >> 8) & 0x0Fu));
    err = aw_update(ch, 0x2E, 0xFFu, v2e, NULL, NULL);
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, 0x2F, (uint8_t)(p->fifo_ae & 0xFFu));
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, 0x30, (uint8_t)(p->fifo_af & 0xFFu));
    if (err != ESP_OK) { return err; }
    /* 0x31：bit2 AUTO_DYNAMIC_SINE、bit3 AUTO_SIN_96K */
    uint8_t v31 = (uint8_t)(((p->rtp_flags & 0x01u) << 2) | ((p->rtp_flags & 0x02u) << 2));
    /* 注：WAVDAT_MODE（0x46 bit1:0）已提到 aw_apply_profile() 的公共段，
     *     它不只服务 RTP —— RAM/TRIG/CONT 播放同样依赖它。 */
    return aw_update(ch, 0x31, 0x0Cu, v31, NULL, NULL);
}

static esp_err_t apply_trig_segment(uint8_t ch, const aw_profile_t *p)
{
    esp_err_t err;
    /* 0x33：bit7 TRG1_POS（正沿使能）、bit6:0 TRG1SEQ_P（正沿波形号）★ T-3 */
    uint8_t v33 = (uint8_t)(((p->trig1_pos_en & 1u) << 7) | (p->trig1_seq_p & 0x7Fu));
    err = aw_write_u8(ch, REG_TRGCFG1, v33);
    if (err != ESP_OK) { return err; }
    uint8_t v36 = (uint8_t)(((p->trig1_neg_en & 1u) << 7) | (p->trig1_seq_n & 0x7Fu));
    err = aw_write_u8(ch, REG_TRGCFG4, v36);
    if (err != ESP_OK) { return err; }
    /* 0x39：bit5 POLAR、bit4 LEV、bit3 BRK（读-改-写，TRG2/TRG3 的位对 FCQFN-9 无意义） */
    uint8_t v39 = (uint8_t)(((p->trig1_polar & 1u) << 5) |
                            ((p->trig1_lev & 1u) << 4) |
                            ((p->trig1_brk & 1u) << 3));
    err = aw_update(ch, REG_TRGCFG7, 0x38u, v39, NULL, NULL);
    if (err != ESP_OK) { return err; }
    /* 0x3A bit2 TRG1_STOP ★ T-4 */
    err = aw_update(ch, REG_TRGCFG8, BIT_TRG1_STOP,
                    (uint8_t)((p->trig1_stop & 1u) << 2), NULL, NULL);
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, REG_GLBCFG2, p->start_dly);
    if (err != ESP_OK) { return err; }
    err = aw_update(ch, REG_GLBCFG4, 0x03u, p->trig1_prio & 0x03u, NULL, NULL);
    if (err != ESP_OK) { return err; }
    err = aw_write_u8(ch, REG_TRGCFG9, p->trg_gain);
    if (err != ESP_OK) { return err; }
    /* 0x45：bit6:4 TRG1_MODE ★ T-1、bit0 INTN_PIN ★ T-2、bit1 EN_FIR 必须保住（W-1） */
    uint8_t v45 = (uint8_t)(((p->trig1_mode & 0x07u) << SHIFT_TRG1_MODE) |
                            (p->intn_pin & 1u));
    err = aw_update(ch, REG_SYSCTRL1, (uint8_t)(MASK_TRG1_MODE | BIT_INTN_PIN), v45,
                    NULL, NULL);
    if (err != ESP_OK) { return err; }
    /* EN_FIR 单独兜底：默认必须是 1 */
    return aw_update(ch, REG_SYSCTRL1, BIT_EN_FIR,
                     (uint8_t)((p->en_fir & 1u) << 1), NULL, NULL);
}

esp_err_t aw_apply_profile(uint8_t ch, const aw_profile_t *p)
{
    if (p == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err;

    /* 播放配置 0x07 GAIN / 0x08 PLAY_MODE|BRK_EN|STOP_MODE */
    err = aw_write_u8(ch, REG_PLAYCFG2, p->gain);
    if (err != ESP_OK) { return err; }
    uint8_t v08 = (uint8_t)((p->mode & 0x03u) |
                            ((p->brk_en & 1u) << 2) |
                            ((p->stop_mode & 1u) << 3));
    err = aw_update(ch, REG_PLAYCFG3, 0x0Fu, v08, NULL, NULL);
    if (err != ESP_OK) { return err; }

    /* 0x46 bit1:0 WAVDAT_MODE（波形数据上采样率）—— **RAM / TRIG / CONT 播放同样生效**，
     * 必须和波形生成时用的采样率一致，否则芯片按错误速率解释波形数据、驱动频率成倍偏移。
     * ★ 原实现只在 RTP 段写这一位：RAM/TRIG 模式下芯片保持上电默认 0（24 kHz），
     *   而波形是按 12 kHz 生成的 ⇒ 166.7 Hz 的波形被放成 ~333 Hz。
     *   实测表现：持续振动"频率不对、发闷、像撞行程末端"（2026-09，AW86234FCR）。
     * 读-改-写保住同寄存器的 WAKE/STANDBY 位。 */
    err = aw_update(ch, REG_SYSCTRL2, 0x03u, (uint8_t)(p->wavdat_mode & 0x03u), NULL, NULL);
    if (err != ESP_OK) { return err; }

    if (p->mode != AW_MODE_RTP) {
        err = aw_update(ch, 0x31, 0x0Cu, 0, NULL, NULL);
        if (err != ESP_OK) { return err; }
    }
    switch (p->mode) {
    case AW_MODE_RAM:
        err = apply_ram_segment(ch, p);
        break;
    case AW_MODE_RTP:
        err = apply_rtp_segment(ch, p);
        break;
    case AW_MODE_CONT:
        err = apply_cont_segment(ch, p);
        break;
    case AW_MODE_TRIG:
        err = apply_trig_segment(ch, p);
        break;
    default:
        err = ESP_ERR_INVALID_ARG;
        break;
    }
    if (err != ESP_OK) { return err; }

    /* WAIT_NUM also controls the RAM/TRIG finishing phase. CONT's zero
     * value must not leak across mode changes (board A/B/A test 2026-09-30). */
    if (p->mode == AW_MODE_RAM || p->mode == AW_MODE_TRIG) {
        err = aw_write_u8(ch, 0x1A, p->wait_num);
        if (err != ESP_OK) { return err; }
    }

    /* 电压补偿 0x4E bit6 VBAT_MODE / 0x4F bit6:4 VBAT_REF */
    err = aw_update(ch, REG_VBATCTRL, 0x40u, (uint8_t)((p->vbat_mode & 1u) << 6),
                    NULL, NULL);
    if (err != ESP_OK) { return err; }
    return aw_update(ch, REG_DETCFG1, 0x70u, (uint8_t)((p->vbat_ref & 0x07u) << 4),
                     NULL, NULL);
}

esp_err_t aw_apply_mode(uint8_t ch, uint8_t mode, const aw_profile_t *p)
{
    if (mode > AW_MODE_TRIG) {
        return ESP_ERR_INVALID_ARG;
    }
    aw_profile_t tmp;
    if (p != NULL) {
        tmp = *p;
    } else {
        ch_ctx_t *c = ch_ctx(ch);
        if (c == NULL) {
            return ESP_ERR_INVALID_ARG;
        }
        tmp = c->profile;
    }
    tmp.mode = mode;

    /* ★ 进入 TRIG 前必须先把"能停下"的位写好并校验（协议 §11.1） */
    if (mode == AW_MODE_TRIG) {
        esp_err_t err = aw_ensure_trig_safety(ch);
        if (err != ESP_OK) {
            LOGE("CH%u：TRG1_STOP/TRG1_BRK 写后回读校验失败，拒绝进入 TRIG 模式", ch);
            return err;
        }
    }
    return aw_apply_profile(ch, &tmp);
}

esp_err_t aw_ensure_trig_safety(uint8_t ch)
{
    uint8_t v = 0;
    /* 0x39 bit3 TRG1_BRK = 1 */
    esp_err_t err = aw_update(ch, REG_TRGCFG7, BIT_TRG1_BRK, BIT_TRG1_BRK, NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    /* 0x3A bit2 TRG1_STOP = 1 */
    err = aw_update(ch, REG_TRGCFG8, BIT_TRG1_STOP, BIT_TRG1_STOP, NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    /* 回读校验：这是"能否把马达停下来"的唯一软件手段，失败就拒绝进 TRIG */
    err = aw_read_u8(ch, REG_TRGCFG8, &v);
    if (err != ESP_OK) {
        return err;
    }
    if ((v & BIT_TRG1_STOP) == 0u) {
        return ESP_ERR_INVALID_STATE;
    }
    err = aw_read_u8(ch, REG_TRGCFG7, &v);
    if (err != ESP_OK) {
        return err;
    }
    if ((v & BIT_TRG1_BRK) == 0u) {
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_OK;
}

esp_err_t aw_verify_trap_bits(uint8_t ch, uint8_t mode)
{
    uint8_t v45 = 0, v33 = 0, v3a = 0;
    esp_err_t err = aw_read_u8(ch, REG_SYSCTRL1, &v45);
    if (err != ESP_OK) { return err; }
    if ((v45 & BIT_EN_FIR) == 0u) {
        LOGW("CH%u：0x45 EN_FIR 被清掉了（默认应为 1）", ch);
    }
    err = aw_read_u8(ch, REG_TRGCFG8, &v3a);
    if (err != ESP_OK) { return err; }
    if (mode == AW_MODE_TRIG) {
        if ((v3a & BIT_TRG1_STOP) == 0u) {
            LOGE("CH%u：0x3A bit2 TRG1_STOP = 0，TRIG 播放无法立即停止", ch);
            return ESP_ERR_INVALID_STATE;
        }
        err = aw_read_u8(ch, REG_TRGCFG1, &v33);
        if (err != ESP_OK) { return err; }
        if ((v33 & BIT_TRG1_POS) == 0u) {
            LOGE("CH%u：0x33 bit7 TRG1_POS = 0，正沿触发未使能", ch);
            return ESP_ERR_INVALID_STATE;
        }
        if (((v45 & MASK_TRG1_MODE) >> SHIFT_TRG1_MODE) != 3u) {
            LOGE("CH%u：0x45 TRG1_MODE != 3（默认值陷阱 T-1）", ch);
            return ESP_ERR_INVALID_STATE;
        }
        if ((v45 & BIT_INTN_PIN) != 0u) {
            LOGE("CH%u：0x45 INTN_PIN = 1，A1 被配成中断输出（默认值陷阱 T-2）", ch);
            return ESP_ERR_INVALID_STATE;
        }
    }
    return ESP_OK;
}

/* ================================================================== */
/* 播放                                                                */
/* ================================================================== */

esp_err_t aw_play_go(uint8_t ch)
{
    /* 0x09 bit0 GO —— 读-改-写，避免误清 STOP 等其他位 */
    return aw_update(ch, REG_PLAYCFG4, BIT_GO, BIT_GO, NULL, NULL);
}

esp_err_t aw_stop(uint8_t ch, uint8_t immediate)
{
    esp_err_t err;
    /* 0x08 bit3 STOP_MODE：0 = 播完再停，1 = 立即停 */
    err = aw_update(ch, REG_PLAYCFG3, BIT_STOP_MODE,
                    immediate ? BIT_STOP_MODE : 0u, NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    /* 0x09 bit1 STOP */
    esp_err_t e2 = aw_update(ch, REG_PLAYCFG4, BIT_STOP, BIT_STOP, NULL, NULL);
    if (e2 != ESP_OK) {
        return e2;
    }
    /* 第二道保险：同时写 0x08 的 STOP 位（协议 §11.1 要求 TRIG_PARK 也这么做） */
    return aw_update(ch, REG_PLAYCFG3, BIT_STOP_MODE, BIT_STOP_MODE, NULL, NULL);
}

esp_err_t aw_write_wave_queue(uint8_t ch, uint8_t count, const uint8_t *ids)
{
    if (count == 0 || count > 8 || ids == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t buf[8];
    for (uint8_t i = 0; i < 8; i++) {
        buf[i] = (i < count) ? ids[i] : 0;
    }
    return aw_write(ch, REG_WAVCFG1, buf, 8);
}

esp_err_t aw_set_ram_loop(uint8_t ch, uint8_t mainloop, uint8_t waitslot,
                          const uint8_t seq_loop[4])
{
    esp_err_t err = aw_write(ch, REG_WAVCFG9, seq_loop, 4);
    if (err != ESP_OK) {
        return err;
    }
    uint8_t v = (uint8_t)((mainloop & 0x0Fu) | ((waitslot & 0x03u) << 4));
    /* ★ MAINLOOP/WAITSLOT 在 0x16（WAVCFG13），不是 0x15 */
    return aw_update(ch, REG_WAVCFG13, 0x3Fu, v, NULL, NULL);
}

/* ================================================================== */
/* SRAM / 波形库                                                       */
/* ================================================================== */

esp_err_t aw_sram_write_stream(uint8_t ch, uint16_t addr, const uint8_t *data, size_t len)
{
    if (data == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = aw_write_u8(ch, REG_RAMADDRH, (uint8_t)((addr >> 8) & 0x0Fu));
    if (err != ESP_OK) {
        return err;
    }
    err = aw_write_u8(ch, REG_RAMADDRL, (uint8_t)(addr & 0xFFu));
    if (err != ESP_OK) {
        return err;
    }
    /* 连续写 0x42 RAMDATA（每次事务写 1 字节，400 kHz 下约 25 µs/字节） */
    for (size_t i = 0; i < len; i++) {
        err = aw_write_u8(ch, REG_RAMDATA, data[i]);
        if (err != ESP_OK) {
            return err;
        }
    }
    return ESP_OK;
}

esp_err_t aw_sram_read(uint8_t ch, uint16_t addr, uint8_t *out, size_t len)
{
    if (out == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    /* 单次事务读不超过 64 字节；每段都重新设一次地址，避免依赖芯片的地址自增行为 */
    size_t done = 0;
    while (done < len) {
        size_t chunk = len - done;
        if (chunk > BOARD_REG_MAX_LEN) {
            chunk = BOARD_REG_MAX_LEN;
        }
        uint16_t a = (uint16_t)(addr + done);
        esp_err_t err = aw_write_u8(ch, REG_RAMADDRH, (uint8_t)((a >> 8) & 0x0Fu));
        if (err != ESP_OK) {
            return err;
        }
        err = aw_write_u8(ch, REG_RAMADDRL, (uint8_t)(a & 0xFFu));
        if (err != ESP_OK) {
            return err;
        }
        err = aw_read(ch, REG_RAMDATA, &out[done], chunk);
        if (err != ESP_OK) {
            return err;
        }
        done += chunk;
    }
    return ESP_OK;
}

esp_err_t aw_wave_lib_write(uint8_t ch, uint16_t base_addr,
                            const uint8_t *lib, size_t len)
{
    if (lib == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    /* 1) 确保芯片 STANDBY */
    uint8_t glb = 0;
    esp_err_t err = aw_get_glb_state(ch, &glb);
    if (err != ESP_OK) {
        return err;
    }
    if (glb != GLB_STANDBY) {
        err = aw_stop(ch, STOP_MODE_IMMEDIATE);
        if (err != ESP_OK) {
            return err;
        }
        delay_ms(5);
    }

    /* 2) 0x45 bit2 EN_RAMINIT = 1（读-改-写保住 EN_FIR 等位） */
    err = aw_update(ch, REG_SYSCTRL1, BIT_EN_RAMINIT, BIT_EN_RAMINIT, NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    /* 3) 延时 1 ms（手册明确要求） */
    delay_ms(1);

    /* 4) base address */
    err = aw_update(ch, REG_BASE_ADDR_H, 0x0Fu, (uint8_t)((base_addr >> 8) & 0x0Fu),
                    NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    err = aw_write_u8(ch, REG_BASE_ADDR_L, (uint8_t)(base_addr & 0xFFu));
    if (err != ESP_OK) {
        return err;
    }

    /* 5) 写地址 + 6) 连续写数据 */
    err = aw_sram_write_stream(ch, base_addr, lib, len);
    if (err != ESP_OK) {
        return err;
    }

    /* 7) 0x45 bit2 EN_RAMINIT = 0 */
    return aw_update(ch, REG_SYSCTRL1, BIT_EN_RAMINIT, 0u, NULL, NULL);
}

esp_err_t aw_wave_lib_read(uint8_t ch, uint16_t base_addr, uint8_t *out, size_t len)
{
    return aw_sram_read(ch, base_addr, out, len);
}

esp_err_t aw_rtp_write(uint8_t ch, const uint8_t *data, size_t len)
{
    if (data == NULL || len == 0 || len > (BOARD_REG_MAX_LEN - 1)) {
        return ESP_ERR_INVALID_ARG;
    }
    return aw_write(ch, REG_RTPDATA, data, len);
}

/* ================================================================== */
/* 测量                                                                */
/* ================================================================== */

esp_err_t aw_f0_enable(uint8_t ch, uint8_t mode)
{
    /* mode: 0 = F_LRA_F0 检测（读 0x24/0x25），1 = CONT 连续检测（读 0x26/0x27）。
     * 两者启动方式相同。 */
    (void)mode;
    /* 手册 §F0 检测流程（寄存器全表 §6.9）：
     *   ① PLAY_MODE = b10(CONT)，BRK_EN = 1
     *   ② EN_F0_DET = 1（0x17 bit5）
     *   ③ GO = 1（0x09 bit0）   ← ★ 原实现漏了这一步，导致 0x24/0x25 恒为 0x0000
     *   ④ 延时 ≥1 ms，等检测结束后读结果
     * 实测本芯片的 GLB_STATE 不反映播放状态，故"检测已结束"由上层用固定等待窗口判定。 */
    esp_err_t err = aw_write_u8(ch, REG_PLAYCFG3, (uint8_t)(BIT_BRK_EN | 0x02u));
    if (err != ESP_OK) {
        return err;
    }
    err = aw_update(ch, REG_CONTCFG1, BIT_EN_F0_DET, BIT_EN_F0_DET, NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    /* 启动瞬间 DONE 事件也会被置起，先清掉，避免后续误判为"检测已结束" */
    (void)aw_clear_sysint(ch);
    return aw_play_go(ch);
}

/** F0 检测收尾：停播放 + 清 EN_F0_DET。 */
esp_err_t aw_f0_disable(uint8_t ch)
{
    (void)aw_stop(ch, STOP_MODE_IMMEDIATE);
    return aw_update(ch, REG_CONTCFG1, BIT_EN_F0_DET, 0u, NULL, NULL);
}

esp_err_t aw_read_f0_raw(uint8_t ch, uint8_t mode, uint8_t *h, uint8_t *l)
{
    uint8_t reg_h = (mode == 1) ? REG_CONT_F0_H : REG_F_LRA_F0_H;
    uint8_t reg_l = (mode == 1) ? REG_CONT_F0_L : REG_F_LRA_F0_L;
    esp_err_t err = aw_read_u8(ch, reg_h, h);
    if (err != ESP_OK) {
        return err;
    }
    return aw_read_u8(ch, reg_l, l);
}

esp_err_t aw_read_cont_raw(uint8_t ch, uint8_t *h, uint8_t *l)
{
    return aw_read_f0_raw(ch, 1, h, l);
}

esp_err_t aw_det_read(uint8_t ch, uint8_t seq, uint16_t *adc, uint16_t *avg,
                      uint8_t *d2s_gain, uint32_t wait_ms)
{
    /* 严格按手册 §电池电压检测 / §LRA 阻抗检测 的步骤序列，顺序不可变：
     *   EN_RAMINIT=1 →（RL：保存 D2S_GAIN 并设为合适值）→ DET_SEQ0=seq
     *   → DET_GO=1 → 等 3 ms → DET_GO=0 → EN_RAMINIT=0 →（RL：恢复 D2S_GAIN）
     *   → 读 DETRD1~3 → 换算
     *
     * ★ 坑 1（2026-09 实测，AW86234FCR ×4，双向 5/5 可复现）：
     *   检测必须在"普通模式"下做。SYSCTRL2(0x46) 的 WAKE(bit7) 或 STANDBY(bit6)
     *   只要置 1，DETRD1~3 就恒读 0 —— RL 恒 0 Ω、VBAT 读到约 VDD 的一半
     *   （3.05 V vs 真实 5.05 V）。而 aw_softrst() 和 ch_reapply_profile() 都会
     *   把 WAKE 置 1，所以自检里"软复位 → 阻抗检测"必然报 AVG=0，还会被误判成
     *   "马达未接 / 输出球虚焊"。这里保存原状态、清零、检测完还原。
     * ★ 坑 2：手册第 3 步要求"把 D2S_GAIN 设为合适的值"，漏做会沿用默认 ×8（b100），
     *   换算出的 RL 偏大约 2.5 倍（本板实测 37 Ω vs 真实 8 Ω）。
     * ★ 读 DETRD 的时机无关紧要（DET_GO=0 之后，清 EN_RAMINIT 前后、还原 D2S_GAIN
     *   前后读到的值完全一致，实测 270/271/272），这里按手册顺序在收尾之后读。 */
    const bool is_rl = (seq == DET_SEQ_RL);
    const uint8_t gain_code = (uint8_t)(DEF_RL_D2S_GAIN & 0x07u);

    /* ① 切到普通模式（WAKE=0 / STANDBY=0），保存原值以便还原 */
    uint8_t sysctrl2_pre = 0;
    const bool have_mode = (aw_read_u8(ch, REG_SYSCTRL2, &sysctrl2_pre) == ESP_OK);
    if (have_mode) {
        (void)aw_update(ch, REG_SYSCTRL2,
                        (uint8_t)(BIT_WAKE | BIT_STANDBY), 0u, NULL, NULL);
    }

    esp_err_t err = aw_update(ch, REG_SYSCTRL1, BIT_EN_RAMINIT, BIT_EN_RAMINIT, NULL, NULL);
    if (err != ESP_OK) {
        if (have_mode) {
            (void)aw_update(ch, REG_SYSCTRL2, (uint8_t)(BIT_WAKE | BIT_STANDBY),
                            (uint8_t)(sysctrl2_pre & (BIT_WAKE | BIT_STANDBY)), NULL, NULL);
        }
        return err;
    }
    delay_ms(1);

    /* ② RL 需要保存并改写 D2S_GAIN；VBAT 保持不动 */
    uint8_t d2s_pre = 0;
    if (is_rl) {
        err = aw_read_u8(ch, REG_DETCFG2, &d2s_pre);
        if (err != ESP_OK) {
            (void)aw_update(ch, REG_SYSCTRL1, BIT_EN_RAMINIT, 0u, NULL, NULL);
            if (have_mode) {
                (void)aw_update(ch, REG_SYSCTRL2, (uint8_t)(BIT_WAKE | BIT_STANDBY),
                                (uint8_t)(sysctrl2_pre & (BIT_WAKE | BIT_STANDBY)), NULL, NULL);
            }
            return err;
        }
    }

    /* ③ DET_SEQ0 = seq；RL 同时把 D2S_GAIN 设为合适值（mask 0x7F 覆盖 bit6:0） */
    const uint8_t mask50 = is_rl ? 0x7Fu : 0x78u;
    const uint8_t val50 = (uint8_t)(((seq & 0x0Fu) << 3) | (is_rl ? gain_code : 0u));
    err = aw_update(ch, REG_DETCFG2, mask50, val50, NULL, NULL);
    if (err == ESP_OK) {
        err = aw_update(ch, REG_DETCFG1, 0x03u, 0x01u, NULL, NULL);   /* DET_GO = b01 */
    }
    if (err == ESP_OK) {
        delay_ms(wait_ms ? wait_ms : 3);
        err = aw_update(ch, REG_DETCFG1, 0x03u, 0x00u, NULL, NULL);   /* DET_GO = b00 */
    }

    /* ④ 收尾：还原 D2S_GAIN → 清 EN_RAMINIT → 还原 SYSCTRL2 */
    if (is_rl) {
        (void)aw_update(ch, REG_DETCFG2, 0x07u, (uint8_t)(d2s_pre & 0x07u), NULL, NULL);
    }
    (void)aw_update(ch, REG_SYSCTRL1, BIT_EN_RAMINIT, 0u, NULL, NULL);
    if (have_mode) {
        (void)aw_update(ch, REG_SYSCTRL2, (uint8_t)(BIT_WAKE | BIT_STANDBY),
                        (uint8_t)(sysctrl2_pre & (BIT_WAKE | BIT_STANDBY)), NULL, NULL);
    }
    if (err != ESP_OK) {
        return err;
    }

    uint8_t r51 = 0, r52 = 0, r53 = 0, r50 = 0;
    err = aw_read_u8(ch, REG_DETRD1, &r51);
    if (err != ESP_OK) { return err; }
    err = aw_read_u8(ch, REG_DETRD2, &r52);
    if (err != ESP_OK) { return err; }
    err = aw_read_u8(ch, REG_DETRD3, &r53);
    if (err != ESP_OK) { return err; }
    err = aw_read_u8(ch, REG_DETCFG2, &r50);
    if (err != ESP_OK) { return err; }

    if (adc) { *adc = conv_adc_data(r51, r53); }
    if (avg) { *avg = conv_avg_data(r51, r52); }
    if (d2s_gain) {
        /* ★ 必须回传"检测时实际用的"增益：RL 用我们设进去的 ×20，而不是还原后的旧值，
         *   否则 conv_avg_to_rl() 会按 ×8 换算，结果偏大 2.5 倍。 */
        *d2s_gain = is_rl ? conv_d2s_gain(gain_code) : conv_d2s_gain((uint8_t)(r50 & 0x07u));
    }
    return ESP_OK;
}

/* ================================================================== */
/* 恢复数据手册默认                                                    */
/* ================================================================== */

esp_err_t aw_apply_register_defaults(uint8_t ch)
{
    /* 只写手册有定义、且默认值已知的关键寄存器；其余保持不动。 */
    static const struct { uint8_t reg; uint8_t val; } k_def[] = {
        { REG_PLAYCFG2, 0x80u },    /* 0x07 GAIN = 128 */
        { REG_PLAYCFG3, 0x04u },    /* 0x08 PLAY_MODE=0 BRK_EN=1 */
        { REG_CONTCFG1, 0x00u },    /* 0x17 */
        { 0x18, DEF_F_PRE },
        { 0x19, DEF_DRV_WIDTH },
        { 0x1A, DEF_WAIT_NUM },
        { 0x1B, DEF_BRK_GAIN },
        { 0x1C, (uint8_t)(0x80u | DEF_DRV1_LVL) },
        { 0x1D, DEF_DRV2_LVL },
        { 0x1E, DEF_DRV1_TIME },
        { 0x1F, DEF_DRV2_TIME },
        { 0x20, DEF_BRK_TIME },
        { 0x21, DEF_TRACK_MARGIN },
        { REG_VBATCTRL, 0x00u },
        { REG_DETCFG1, (uint8_t)(DEF_VBAT_REF << 4) },
    };
    for (size_t i = 0; i < sizeof(k_def) / sizeof(k_def[0]); i++) {
        esp_err_t err = aw_write_u8(ch, k_def[i].reg, k_def[i].val);
        if (err != ESP_OK) {
            return err;
        }
    }
    /* 0x45 只改 TRG1_MODE/INTN_PIN，保住 EN_FIR */
    esp_err_t err = aw_update(ch, REG_SYSCTRL1,
                              (uint8_t)(MASK_TRG1_MODE | BIT_INTN_PIN),
                              (uint8_t)((7u << SHIFT_TRG1_MODE) | BIT_INTN_PIN),
                              NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    return ESP_OK;
}
