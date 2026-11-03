/**
 * @file  proto_frame.h
 * @brief 协议帧层：CRC16-CCITT-FALSE / 组帧 / 解帧 / 流式解析器。
 *
 * **纯逻辑，无任何 ESP-IDF 依赖**，可直接在 PC 上编译做单元测试
 * （见 `main/test/`）。与上位机 `host/awtest/protocol/framing.py` 逐字节一致。
 *
 * 依据 `03_串口协议规范_v1.0.md` §3/§4/§5。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "proto_cmd.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PROTO_SOF0              0xAAu
#define PROTO_SOF1              0x55u
#define PROTO_VERSION           0x01u   /* 帧头 VER，本版固定 0x01 */

#define PROTO_HEADER_LEN        8u
#define PROTO_CRC_LEN           2u
#define PROTO_MIN_FRAME_LEN     (PROTO_HEADER_LEN + PROTO_CRC_LEN)          /* 10  */
#define PROTO_MAX_PAYLOAD_LEN   200u
#define PROTO_MAX_FRAME_LEN     (PROTO_HEADER_LEN + PROTO_MAX_PAYLOAD_LEN + PROTO_CRC_LEN) /* 210 */

/* FLAGS */
#define PROTO_FLAG_RESPONSE     0x01u
#define PROTO_FLAG_EVENT        0x02u
#define PROTO_FLAG_ERROR        0x04u
#define PROTO_FLAG_MORE         0x08u   /* v1.0 保留：设备不得置位 */
#define PROTO_FLAG_NORESP       0x10u   /* 仅请求帧有效 */

/* CHAN 特殊值 */
#define PROTO_CHAN_ALL          0xFFu
#define PROTO_CHAN_NONE         0xFEu

/* ==========================================================================
 * CRC16-CCITT-FALSE  poly=0x1021 init=0xFFFF 无反转发
 * 自检向量： "123456789" -> 0x29B1
 * ⚠️ 不是 XMODEM（init=0x0000，自检 0x31C3）
 * ========================================================================== */

void     proto_crc_init(void);
uint16_t proto_crc16_ccitt(const uint8_t *data, size_t len, uint16_t crc);

/* ==========================================================================
 * 帧
 * ========================================================================== */

typedef struct {
    uint8_t flags;
    uint8_t seq;
    uint8_t cmd;
    uint8_t chan;
    uint8_t payload[PROTO_MAX_PAYLOAD_LEN];
    uint8_t payload_len;
} proto_frame_t;

/** 组帧。out 容量必须 >= PROTO_MAX_FRAME_LEN。返回写入字节数，失败返回 0。 */
size_t proto_frame_pack(const proto_frame_t *f, uint8_t *out, size_t out_cap);

/** 解析一个完整帧（长度须与头部一致）。 */
bool proto_frame_parse(const uint8_t *in, size_t len, proto_frame_t *out);

/** 求一个完整帧的总长度（读第 8 字节 LEN）。返回 0 表示 LEN 非法。 */
size_t proto_frame_total_len(const uint8_t *header, size_t avail);

/* ==========================================================================
 * 流式解析器
 * ========================================================================== */

#define PROTO_RX_BUF_LEN    (PROTO_MAX_FRAME_LEN * 2u)

typedef struct {
    uint8_t  buf[PROTO_RX_BUF_LEN];
    size_t   len;
    uint32_t crc_errors;
    uint32_t resyncs;
    uint32_t frames_ok;
} proto_parser_t;

void proto_parser_init(proto_parser_t *p);

/**
 * 喂入串口字节，逐个产出完整帧。
 * 用法： while (proto_parser_feed(&ps, chunk, n, &f)) { handle(&f); }
 */
bool proto_parser_feed(proto_parser_t *p, const uint8_t *data, size_t len,
                       proto_frame_t *out);

/* ==========================================================================
 * 小端读写助手（协议要求所有多字节整数 LE）
 * ========================================================================== */

static inline void proto_put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
}

static inline void proto_put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

static inline uint16_t proto_get_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static inline uint32_t proto_get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/** 帧是否为应答 / 事件 / 错误 / 免应答请求。 */
static inline bool proto_is_response(const proto_frame_t *f) { return (f->flags & PROTO_FLAG_RESPONSE) != 0; }
static inline bool proto_is_event(const proto_frame_t *f)    { return (f->flags & PROTO_FLAG_EVENT) != 0; }
static inline bool proto_is_error(const proto_frame_t *f)    { return (f->flags & PROTO_FLAG_ERROR) != 0; }
static inline bool proto_is_noresp(const proto_frame_t *f)   { return (f->flags & PROTO_FLAG_NORESP) != 0; }

#ifdef __cplusplus
}
#endif
