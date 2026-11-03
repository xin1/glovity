/**
 * @file  wave_codec.h
 * @brief SRAM 波形库的规划、打包、CRC 与内置波形合成。
 *
 * **纯逻辑，无 ESP 依赖**，可在 PC 上单测（`main/test/`）。
 *
 * 库布局（`02_AW8623X寄存器与模式参考.md` §5）：
 * @verbatim
 *   BASE_ADDR + 0        : 版本号（1 字节）
 *   BASE_ADDR + 1        : 波形 #1 头（4 字节：start_h start_l end_h end_l）
 *   BASE_ADDR + 5        : 波形 #2 头 ...
 *   BASE_ADDR + 4*N + 1  : 波形数据区起点
 * @endverbatim
 * 地址为**绝对 SRAM 地址（含 BASE_ADDR）**，每头高字节在前；样本为有符号 8 位。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define WAVE_LIB_MAX_WAVES      127u
#define WAVE_LIB_MAX_BYTES      3072u   /* SRAM 全量，波形库可用空间由 BASE_ADDR 决定 */

typedef struct {
    uint8_t        wave_id;
    const int8_t  *samples;
    uint16_t       len;         /* 样本数 */
} wave_input_t;

typedef struct {
    uint8_t  lib_version;
    uint8_t  wave_count;
    uint16_t base_addr;
    uint16_t total_bytes;       /* 1 + 4N + Σlen */
    uint16_t start[WAVE_LIB_MAX_WAVES];   /* 绝对地址（含 base） */
    uint16_t end[WAVE_LIB_MAX_WAVES];     /* 含端点 */
} wave_lib_t;

/** 可用空间：base_addr 之上的字节数（波形库在 BASE_ADDR 之上）。 */
uint16_t wave_lib_avail_bytes(uint16_t base_addr);

/** 给定波形条数，返回可容纳的最大样本总数（= avail - 1 - 4N）。 */
uint16_t wave_lib_max_samples(uint16_t base_addr, uint8_t wave_count);

/**
 * 规划库的地址表。
 * @return 0 成功；否则返回协议错误码（ERR_SRAM_OVERFLOW / ERR_BAD_PARAM / ERR_TOO_MANY_CHUNKS）
 */
int wave_lib_plan(uint16_t base_addr, uint8_t lib_version,
                  const wave_input_t *waves, uint8_t n, wave_lib_t *out);

/**
 * 打包成写入 SRAM 的连续字节流。
 * @return 写入字节数；失败返回 0。
 */
size_t wave_lib_pack(const wave_lib_t *lib, const wave_input_t *waves, uint8_t n,
                     uint8_t *out, size_t out_cap);

/** Parse exactly the header table bounded by the first waveform's start. */
int wave_lib_parse_header(uint16_t base, const uint8_t *head, size_t len, wave_lib_t *out);

/** 对打包后的字节流算 CRC16-CCITT-FALSE（与协议同算法）。 */
uint16_t wave_lib_crc(const uint8_t *data, size_t len);

/**
 * 内置波形合成（`WAVE_GEN` / ASCII 的 `wave` 命令）。
 *
 * @param shape       0=正弦 1=方波 2=三角 3=锯齿 4=正弦+上升包络 5=正弦+下降包络 6=点击
 * @param freq_hz     目标频率（Hz）
 * @param amplitude   峰值 0..127
 * @param cycles      周期数（≥1）
 * @param rate_code   采样率编码
 * @param out         输出缓冲（有符号 8 位）
 * @param out_cap     输出缓冲容量（样本数）
 * @param out_len     输出：实际生成的样本数
 * @param actual_hz   输出：可空，实际频率 = rate / 每周期样本数
 * @return 0 成功；否则协议错误码
 */
int wave_gen_samples(uint8_t shape, uint16_t freq_hz, uint8_t amplitude,
                     uint16_t cycles, uint8_t rate_code,
                     int8_t *out, size_t out_cap, uint16_t *out_len,
                     uint16_t *actual_hz);

/** 有符号 8 位 <-> 线上字节（2 的补码，其实就是同一份内存）。 */
static inline uint8_t wave_s8_to_u8(int8_t v) { return (uint8_t)v; }
static inline int8_t  wave_u8_to_s8(uint8_t v) { return (int8_t)v; }

#ifdef __cplusplus
}
#endif
