/**
 * @file  proto_frame.c
 * @brief 协议帧层实现。逻辑与 `reference/protocol_reference.c` 一致（该实现在 PC 上通过自检）。
 *
 * 设计要点：
 *   - 字节流传输，不依赖波特率 / MTU / 时序
 *   - 解析器可从任意字节偏移恢复同步（逐字节寻找 SOF，失败前进一字节）
 *   - 无动态内存分配，适合嵌入式
 */

#include "proto_frame.h"

#include <string.h>

/* ==========================================================================
 * CRC16-CCITT-FALSE
 * ========================================================================== */

static uint16_t s_crc_table[256];
static bool     s_crc_table_ready = false;

void proto_crc_init(void)
{
    for (uint32_t i = 0; i < 256u; i++) {
        uint16_t crc = (uint16_t)(i << 8);
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u)
                                  : (uint16_t)(crc << 1);
        }
        s_crc_table[i] = crc;
    }
    s_crc_table_ready = true;
}

uint16_t proto_crc16_ccitt(const uint8_t *data, size_t len, uint16_t crc)
{
    if (!s_crc_table_ready) {
        proto_crc_init();
    }
    for (size_t i = 0; i < len; i++) {
        crc = (uint16_t)((crc << 8) ^ s_crc_table[((crc >> 8) ^ data[i]) & 0xFFu]);
    }
    return crc;
}

/* ==========================================================================
 * 组帧 / 解帧
 * ========================================================================== */

size_t proto_frame_pack(const proto_frame_t *f, uint8_t *out, size_t out_cap)
{
    if (f->payload_len > PROTO_MAX_PAYLOAD_LEN) {
        return 0;
    }
    size_t total = (size_t)PROTO_HEADER_LEN + f->payload_len + PROTO_CRC_LEN;
    if (out_cap < total) {
        return 0;
    }

    out[0] = PROTO_SOF0;
    out[1] = PROTO_SOF1;
    out[2] = PROTO_VERSION;
    out[3] = f->flags;
    out[4] = f->seq;
    out[5] = f->cmd;
    out[6] = f->chan;
    out[7] = f->payload_len;
    if (f->payload_len > 0) {
        memcpy(&out[PROTO_HEADER_LEN], f->payload, f->payload_len);
    }

    /* CRC 覆盖 VER..载荷末字节，即去掉前两个 SOF 字节 */
    uint16_t crc = proto_crc16_ccitt(&out[2],
                                     (size_t)(PROTO_HEADER_LEN - 2u) + f->payload_len,
                                     0xFFFFu);
    out[PROTO_HEADER_LEN + f->payload_len]     = (uint8_t)(crc & 0xFFu);         /* 小端 */
    out[PROTO_HEADER_LEN + f->payload_len + 1] = (uint8_t)((crc >> 8) & 0xFFu);

    return total;
}

size_t proto_frame_total_len(const uint8_t *header, size_t avail)
{
    if (avail < PROTO_HEADER_LEN) {
        return 0;
    }
    uint8_t plen = header[7];
    if (plen > PROTO_MAX_PAYLOAD_LEN) {
        return 0;
    }
    return (size_t)PROTO_HEADER_LEN + plen + PROTO_CRC_LEN;
}

bool proto_frame_parse(const uint8_t *in, size_t len, proto_frame_t *out)
{
    if (len < PROTO_MIN_FRAME_LEN || out == NULL) {
        return false;
    }
    if (in[0] != PROTO_SOF0 || in[1] != PROTO_SOF1 || in[2] != PROTO_VERSION) {
        return false;
    }

    uint8_t plen = in[7];
    if (plen > PROTO_MAX_PAYLOAD_LEN) {
        return false;
    }
    if (len != (size_t)PROTO_HEADER_LEN + plen + PROTO_CRC_LEN) {
        return false;
    }

    uint16_t want = (uint16_t)(in[PROTO_HEADER_LEN + plen] |
                               ((uint16_t)in[PROTO_HEADER_LEN + plen + 1] << 8));
    uint16_t got  = proto_crc16_ccitt(&in[2], (size_t)(PROTO_HEADER_LEN - 2u) + plen, 0xFFFFu);
    if (want != got) {
        return false;
    }

    out->flags       = in[3];
    out->seq         = in[4];
    out->cmd         = in[5];
    out->chan        = in[6];
    out->payload_len = plen;
    if (plen > 0) {
        memcpy(out->payload, &in[PROTO_HEADER_LEN], plen);
    }
    return true;
}

/* ==========================================================================
 * 流式解析器
 * ========================================================================== */

void proto_parser_init(proto_parser_t *p)
{
    if (p == NULL) {
        return;
    }
    p->len        = 0;
    p->crc_errors = 0;
    p->resyncs    = 0;
    p->frames_ok  = 0;
}

static void parser_drop(proto_parser_t *p, size_t n)
{
    if (n >= p->len) {
        p->len = 0;
        return;
    }
    memmove(p->buf, &p->buf[n], p->len - n);
    p->len -= n;
}

bool proto_parser_feed(proto_parser_t *p, const uint8_t *data, size_t len,
                       proto_frame_t *out)
{
    if (p == NULL || out == NULL) {
        return false;
    }

    /* 缓冲区满且仍无法成帧 -> 丢弃最旧字节保命 */
    if (p->len + len > PROTO_RX_BUF_LEN) {
        size_t drop = (p->len + len) - PROTO_RX_BUF_LEN;
        parser_drop(p, drop);
        p->resyncs++;
    }
    if (len > 0) {
        memcpy(&p->buf[p->len], data, len);
        p->len += len;
    }

    while (true) {
        /* 1) 对齐到 SOF —— 缓冲区再短也要做，否则垃圾字节会一直堆在里面 */
        size_t i = 0;
        while (i + 1u < p->len &&
               !(p->buf[i] == PROTO_SOF0 && p->buf[i + 1u] == PROTO_SOF1)) {
            i++;
        }
        if (i > 0) {
            parser_drop(p, i);
            p->resyncs++;
        }
        if (p->len < 2u) {
            /* 只剩 1 字节：恰是 0xAA 则保留（可能是被截断的 SOF），否则丢弃 */
            if (p->len == 1u && p->buf[0] != PROTO_SOF0) {
                parser_drop(p, 1);
                p->resyncs++;
            }
            break;
        }
        if (p->len < PROTO_MIN_FRAME_LEN) {
            break;                      /* 头部还没到齐，等更多数据 */
        }

        /* 2) 版本号不符 -> 丢一字节重新对齐（不丢整帧：SOF 可能只是恰巧出现在载荷里） */
        if (p->buf[2] != PROTO_VERSION) {
            parser_drop(p, 1);
            p->resyncs++;
            continue;
        }

        /* 3) 长度合法性 */
        uint8_t plen = p->buf[7];
        if (plen > PROTO_MAX_PAYLOAD_LEN) {
            parser_drop(p, 1);
            p->resyncs++;
            continue;
        }

        size_t total = (size_t)PROTO_HEADER_LEN + plen + PROTO_CRC_LEN;
        if (p->len < total) {
            break;                          /* 等更多数据（断包） */
        }

        if (proto_frame_parse(p->buf, total, out)) {
            parser_drop(p, total);
            p->frames_ok++;
            return true;
        }
        /* CRC 错 -> 丢一字节重试，不能阻塞后续帧 */
        p->crc_errors++;
        parser_drop(p, 1);
        p->resyncs++;
    }
    return false;
}
