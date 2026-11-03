/**
 * @file  aw_profile.h
 * @brief AW86234 的 64 字节参数 Profile（协议附录 B）：结构、默认值、线序编解码、范围校验。
 *
 * **纯逻辑，无 ESP 依赖**，可在 PC 上单测。
 * 与上位机 `host/awtest/profiles.py` 的 `Profile` **逐字段一一对应**，
 * 两边的范围规则必须一致。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AW_PROFILE_VER      0x01u
#define AW_PROFILE_LEN      64u

/** 播放模式 */
#define AW_MODE_RAM         0u
#define AW_MODE_RTP         1u
#define AW_MODE_CONT        2u
#define AW_MODE_TRIG        3u

typedef struct {
    /* ---- 通用 / 播放（偏移 0..7） ---- */
    uint8_t profile_ver;
    uint8_t mode;
    uint8_t gain;               /* 0x07，倍率 = code/128 */
    uint8_t brk_en;             /* 0x08 bit2 */
    uint8_t stop_mode;          /* 0x08 bit3 */
    uint8_t vbat_mode;          /* 0x4E */
    uint8_t vbat_ref;           /* 0x4F bit6:4 */
    uint8_t en_fir;             /* 0x45 bit1，默认必须 1 */

    /* ---- RAM 段（偏移 8..27） ---- */
    uint8_t wavseq[8];          /* 偏移 8..15，0x0A..0x11 */
    uint8_t mainloop;           /* 偏移 16，0x16 bit3:0；0xF = 无限循环（持续振动） */
    uint8_t waitslot;           /* 偏移 17，0x16 bit5:4 */
    uint8_t seq_loop[4];        /* 偏移 18..21，0x12..0x15（SEQ1~8LOOP） */
    uint8_t reserved_ram[6];    /* 偏移 22..27，必须 0 */

    /* ---- CONT 段（偏移 28..41） ---- */
    uint8_t en_f0_det;          /* 0x17 bit5 */
    uint8_t sin_mode;           /* 0x17 bit4 */
    uint8_t edge_fre;           /* 0x17 bit3:0 */
    uint8_t f_pre;              /* 0x18，F0 = 24000/f_pre */
    uint8_t drv_width;          /* 0x19，t = code/48000 s */
    uint8_t wait_num;           /* 0x1A */
    uint8_t brk_gain;           /* 0x1B bit3:0 */
    uint8_t track_en;           /* 0x1C bit7 */
    uint8_t drv1_lvl;           /* 0x1C bit6:0，0..127 */
    uint8_t drv2_lvl;           /* 0x1D bit6:0 */
    uint8_t drv1_time;          /* 0x1E */
    uint8_t drv2_time;          /* 0x1F */
    uint8_t brk_time;           /* 0x20，=0 自动刹车失效 */
    uint8_t track_margin;       /* 0x21 */

    /* ---- RTP 段（偏移 42..49） ---- */
    uint8_t wavdat_mode;        /* 0x46 bit1:0；48K(1) 本板不可用 */
    uint8_t rtp_flags;          /* 0x31 */
    uint16_t base_addr;         /* 0x2C/0x2D，默认 0x0800 */
    uint16_t fifo_ae;           /* 0x2E bit7:4 / 0x2F，默认 512 */
    uint16_t fifo_af;           /* 0x2E bit3:0 / 0x30，默认 1536 */

    /* ---- TRIG 段（偏移 50..63） ---- */
    uint8_t trig1_pos_en;       /* 0x33 bit7，默认 0 = 不触发（陷阱 T-3） */
    uint8_t trig1_seq_p;        /* 0x33 bit6:0 */
    uint8_t trig1_neg_en;       /* 0x36 bit7 */
    uint8_t trig1_seq_n;        /* 0x36 bit6:0 */
    uint8_t trig1_polar;        /* 0x39 bit5 */
    uint8_t trig1_lev;          /* 0x39 bit4 */
    uint8_t trig1_brk;          /* 0x39 bit3，TRIG 模式必须 1 */
    uint8_t trig1_stop;         /* 0x3A bit2，必须 1（陷阱 T-4） */
    uint8_t trig1_prio;         /* 0x3E bit1:0 */
    uint8_t trg_gain;           /* 0x43，增益不是刹车位 */
    uint8_t trig1_mode;         /* 0x45 bit6:4，必须 3（陷阱 T-1） */
    uint8_t intn_pin;           /* 0x45 bit0，本板必须 0（陷阱 T-2） */
    uint8_t start_dly;          /* 0x3C，单位 1/48000 s */
    uint8_t reserved1;          /* 必须 0 */
} aw_profile_t;

/** 填入数据手册默认值 / 本板安全默认值。 */
void aw_profile_defaults(aw_profile_t *p);

/** 打包成线上 64 字节（多字节字段小端）。返回写入字节数（应为 64）。 */
size_t aw_profile_to_bytes(const aw_profile_t *p, uint8_t *out, size_t cap);

/** 从 64 字节解析。长度不足返回 false。 */
bool aw_profile_from_bytes(aw_profile_t *p, const uint8_t *in, size_t len);

/**
 * 校验。返回第一个非法字段的**字节偏移**（0 = 全部合法；0 也可能是"合法"，
 * 所以用 *ok 输出合法性），并在 msg 里给出中文原因。
 *
 * @param offset_out 可空，输出非法字段在 Profile 中的偏移
 * @param msg        可空，输出中文原因（建议 >=64 字节）
 * @return true = 合法
 */
bool aw_profile_validate(const aw_profile_t *p, uint8_t *offset_out,
                         char *msg, size_t msg_cap);

/** 逐字段比较，返回不一致的字段数。 */
int aw_profile_diff(const aw_profile_t *a, const aw_profile_t *b,
                    char *msg, size_t msg_cap);

/** 便捷：设置目标 F0（Hz），内部换算 F_PRE。 */
void aw_profile_set_f0(aw_profile_t *p, float f0_hz);

/** 便捷：当前 F0（Hz）。 */
float aw_profile_f0(const aw_profile_t *p);

/** 满幅判定（协议 §11）：CONT 看 drv1_lvl，RAM/RTP 看 gain。 */
bool aw_profile_is_full_amplitude(const aw_profile_t *p, uint8_t mode);

#ifdef __cplusplus
}
#endif
