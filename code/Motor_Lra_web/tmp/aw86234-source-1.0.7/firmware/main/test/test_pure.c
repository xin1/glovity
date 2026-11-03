/**
 * @file  test_pure.c
 * @brief 固件纯逻辑的**主机单元测试**（不需要 ESP32 硬件）。
 *
 * 覆盖 `04_固件需求规格_ESP-IDF.md` §11.1 要求的全部对象：
 *   - util_crc16 / CRC 自检与增量一致性
 *   - 帧组帧/解帧（11 条测试向量）与流式解析器健壮性（粘包、逐字节断包、
 *     垃圾字节、VER 错、LEN 超限、CRC 损坏后不阻塞后续帧、200 字节边界）
 *   - 波形库打包（版本字节、头块偏移与端序、数据区拼接、N=127、容量刚好满/超一字节）
 *   - Profile 校验（边界值、profile_ver 错、保留字节非 0、48K 拒绝、drv_width 越界）
 *   - 参数换算（F_PRE↔F0、F0 raw、DRV_LVL→电压、时间↔µs、VDD/RL）
 *
 * 编译运行（Windows，用 pip 装的 zig 作为主机编译器）：
 *     python -m ziglang cc -std=c11 -Wall -Wextra -Werror -O1 \
 *         -I firmware/main firmware/main/test/test_pure.c \
 *         firmware/main/proto_frame.c firmware/main/util_crc16.c \
 *         firmware/main/wave_codec.c firmware/main/aw_profile.c \
 *         firmware/main/convert.c -o build/test_pure.exe -lm
 *
 * 或直接 `pwsh firmware/main/test/run_host_tests.ps1`。
 */

#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "aw_profile.h"
#include "cfg_defaults.h"
#include "convert.h"
#include "proto_frame.h"
#include "util_crc16.h"
#include "wave_codec.h"

#include "vectors_generated.h"

static int g_fail = 0;
static int g_pass = 0;

#define CHECK(cond, fmt, ...)                                                  \
    do {                                                                       \
        if (cond) {                                                            \
            g_pass++;                                                          \
        } else {                                                               \
            g_fail++;                                                          \
            printf("  [FAIL] %s:%d  " fmt "\n", __FILE__, __LINE__, __VA_ARGS__); \
        }                                                                      \
    } while (0)

#define CHECK_TRUE(cond, what) CHECK((cond), "%s", (what))

/* ------------------------------------------------------------------ */
/* 工具                                                                */
/* ------------------------------------------------------------------ */

static size_t hex2bin(const char *hex, uint8_t *out, size_t out_cap)
{
    size_t n = 0;
    int hi = -1;
    for (const char *p = hex; *p; p++) {
        int v;
        if (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') {
            continue;
        }
        if (*p >= '0' && *p <= '9')      { v = *p - '0'; }
        else if (*p >= 'A' && *p <= 'F') { v = *p - 'A' + 10; }
        else if (*p >= 'a' && *p <= 'f') { v = *p - 'a' + 10; }
        else { return 0; }
        if (hi < 0) {
            hi = v;
        } else {
            if (n >= out_cap) { return 0; }
            out[n++] = (uint8_t)((hi << 4) | v);
            hi = -1;
        }
    }
    return (hi < 0) ? n : 0;
}

/**
 * 喂入一段数据并把缓冲区里能解出的帧全部取走，返回帧数。
 * ⚠️ proto_parser_feed 会**追加**数据到内部缓冲，所以"喂一次 + 空喂排空"才是正确用法。
 */
static int feed_and_drain(proto_parser_t *ps, const uint8_t *data, size_t len)
{
    proto_frame_t f;
    int got = 0;
    if (proto_parser_feed(ps, data, len, &f)) {
        got++;
    }
    while (proto_parser_feed(ps, NULL, 0, &f)) {
        got++;
    }
    return got;
}

/* ================================================================== */
/* 1. CRC                                                             */
/* ================================================================== */

static void test_crc(void)
{
    printf("[1] CRC16-CCITT-FALSE\n");
    const char *s = VECTORS_CRC_SELFTEST_INPUT;
    uint16_t crc = util_crc16((const uint8_t *)s, strlen(s), UTIL_CRC16_INIT);
    CHECK(crc == VECTORS_CRC_SELFTEST_EXPECT,
          "自检 %s -> 0x%04X，期望 0x%04X", s, (unsigned)crc,
          (unsigned)VECTORS_CRC_SELFTEST_EXPECT);

    /* 空输入 = init */
    CHECK(util_crc16(NULL, 0, UTIL_CRC16_INIT) == 0xFFFFu, "空输入应为 0xFFFF%s", "");

    /* 不得误用 XMODEM（init=0x0000） */
    CHECK(util_crc16((const uint8_t *)s, strlen(s), 0x0000u) != VECTORS_CRC_SELFTEST_EXPECT,
          "XMODEM 初值不应得到同一结果%s", "");

    /* 增量式 = 一次性 */
    uint8_t data[512];
    for (size_t i = 0; i < sizeof(data); i++) {
        data[i] = (uint8_t)(i * 7u + 3u);
    }
    uint16_t one = util_crc16(data, sizeof(data), UTIL_CRC16_INIT);
    uint16_t inc = UTIL_CRC16_INIT;
    for (size_t i = 0; i < sizeof(data); i += 7) {
        size_t n = (i + 7 <= sizeof(data)) ? 7 : (sizeof(data) - i);
        inc = util_crc16(&data[i], n, inc);
    }
    CHECK(one == inc, "增量式 0x%04X != 一次性 0x%04X", (unsigned)inc, (unsigned)one);
}

/* ================================================================== */
/* 2. 帧向量                                                          */
/* ================================================================== */

static void test_vectors(void)
{
    printf("[2] 协议测试向量（%u 条，与上位机同一份 JSON）\n", (unsigned)VECTORS_COUNT);
    CHECK(VECTORS_COUNT == 11, "向量条数应为 11，实际 %u", (unsigned)VECTORS_COUNT);

    uint8_t raw[VECTORS_MAX_FRAME_LEN];
    for (size_t i = 0; i < VECTORS_COUNT; i++) {
        size_t len = hex2bin(k_proto_vectors[i].frame_hex, raw, sizeof(raw));
        CHECK(len == k_proto_vectors[i].frame_len,
              "向量 %u 长度 %u != %u", (unsigned)i, (unsigned)len,
              (unsigned)k_proto_vectors[i].frame_len);
        if (len == 0) {
            continue;
        }
        uint16_t crc_le = (uint16_t)(raw[len - 2] | ((uint16_t)raw[len - 1] << 8));
        CHECK(crc_le == k_proto_vectors[i].crc16,
              "向量 %u CRC 0x%04X != 0x%04X", (unsigned)i,
              (unsigned)crc_le, (unsigned)k_proto_vectors[i].crc16);

        proto_frame_t f;
        CHECK_TRUE(proto_frame_parse(raw, len, &f), k_proto_vectors[i].desc);

        uint8_t rebuilt[PROTO_MAX_FRAME_LEN];
        size_t rlen = proto_frame_pack(&f, rebuilt, sizeof(rebuilt));
        CHECK(rlen == len && memcmp(rebuilt, raw, len) == 0,
              "向量 %u 组帧往返不一致", (unsigned)i);
    }
}

/* ================================================================== */
/* 3. 流式解析器健壮性                                                */
/* ================================================================== */

static void test_parser(void)
{
    printf("[3] 流式解析器\n");
    uint8_t v0[PROTO_MAX_FRAME_LEN], v1[PROTO_MAX_FRAME_LEN];
    uint8_t v2[PROTO_MAX_FRAME_LEN], v4[PROTO_MAX_FRAME_LEN];
    uint8_t v9[VECTORS_MAX_FRAME_LEN], v10[PROTO_MAX_FRAME_LEN];
    size_t l0 = hex2bin(k_proto_vectors[0].frame_hex, v0, sizeof(v0));
    size_t l1 = hex2bin(k_proto_vectors[1].frame_hex, v1, sizeof(v1));
    size_t l2 = hex2bin(k_proto_vectors[2].frame_hex, v2, sizeof(v2));
    size_t l4 = hex2bin(k_proto_vectors[4].frame_hex, v4, sizeof(v4));
    size_t l9 = hex2bin(k_proto_vectors[9].frame_hex, v9, sizeof(v9));
    size_t l10 = hex2bin(k_proto_vectors[10].frame_hex, v10, sizeof(v10));

    CHECK(l9 == 210u, "最大帧向量长度应为 210，实际 %u", (unsigned)l9);

    /* 粘包 + 垃圾字节 */
    {
        uint8_t stream[512];
        size_t n = 0;
        memcpy(&stream[n], v0, l0); n += l0;
        stream[n++] = 0x00; stream[n++] = 0xFF; stream[n++] = 0xAA;
        memcpy(&stream[n], v1, l1); n += l1;
        memcpy(&stream[n], v2, l2); n += l2;

        proto_parser_t ps;
        proto_parser_init(&ps);
        proto_frame_t f;
        int got = 0;
        /* 逐字节喂入（最恶劣断包）应产出 3 帧 */
        for (size_t i = 0; i < n; i++) {
            while (proto_parser_feed(&ps, &stream[i], 1, &f)) {
                got++;
            }
        }
        CHECK(got == 3, "逐字节喂入应产出 3 帧，实际 %d", got);
    }

    /* 整包喂入与逐字节喂入结果一致 */
    {
        uint8_t stream[256];
        size_t n = 0;
        memcpy(&stream[n], v0, l0); n += l0;
        memcpy(&stream[n], v9, l9); n += l9;
        memcpy(&stream[n], v10, l10); n += l10;

        proto_parser_t pa, pb;
        proto_parser_init(&pa);
        proto_parser_init(&pb);
        int na = feed_and_drain(&pa, stream, n);
        proto_frame_t fb;
        int nb = 0;
        for (size_t i = 0; i < n; i++) {
            while (proto_parser_feed(&pb, &stream[i], 1, &fb)) {
                nb++;
            }
        }
        CHECK(na == 3 && nb == 3, "整包 %d 帧 / 断包 %d 帧，应均为 3", na, nb);
    }

    /* CRC 损坏后不得阻塞后续帧 */
    {
        uint8_t bad[PROTO_MAX_FRAME_LEN];
        memcpy(bad, v1, l1);
        bad[l1 - 1] ^= 0xFFu;

        proto_parser_t ps;
        proto_parser_init(&ps);
        int got = feed_and_drain(&ps, bad, l1);
        got += feed_and_drain(&ps, v4, l4);
        CHECK(got == 1 && ps.crc_errors >= 1,
              "CRC 损坏恢复失败：got=%d crc_errors=%u", got, (unsigned)ps.crc_errors);
    }

    /* VER 错 -> 丢弃 1 字节重新对齐，后续帧仍能解出 */
    {
        uint8_t bad[PROTO_MAX_FRAME_LEN];
        memcpy(bad, v2, l2);
        bad[2] = 0x02;
        proto_parser_t ps;
        proto_parser_init(&ps);
        int got = feed_and_drain(&ps, bad, l2);
        got += feed_and_drain(&ps, v0, l0);
        CHECK(got == 1, "VER 错后重新对齐失败，got=%d", got);
    }

    /* LEN 超限 */
    {
        uint8_t bad[PROTO_MAX_FRAME_LEN];
        memcpy(bad, v2, l2);
        bad[7] = 201;
        proto_parser_t ps;
        proto_parser_init(&ps);
        int got = feed_and_drain(&ps, bad, l2);
        got += feed_and_drain(&ps, v0, l0);
        CHECK(got == 1, "LEN 超限后重新对齐失败，got=%d", got);
    }

    /* 边界：210 字节帧 */
    {
        proto_parser_t ps;
        proto_parser_init(&ps);
        proto_frame_t f;
        int got = 0;
        if (proto_parser_feed(&ps, v9, l9, &f)) {
            got++;
            CHECK(f.payload_len == 200, "200 字节载荷边界失败：len=%u", (unsigned)f.payload_len);
        }
        while (proto_parser_feed(&ps, NULL, 0, &f)) {
            got++;
        }
        CHECK(got == 1, "200 字节载荷边界失败：got=%d", got);
    }

    /* 只有尾部 0xAA 时必须保留 */
    {
        proto_parser_t ps;
        proto_parser_init(&ps);
        proto_frame_t f;
        uint8_t junk[3] = { 0x00, 0x11, 0xAA };
        CHECK(proto_parser_feed(&ps, junk, 3, &f) == false, "垃圾字节不应成帧%s", "");
        CHECK(ps.len == 1, "应保留尾部 0xAA，实际缓冲 %u", (unsigned)ps.len);
    }
}

/* ================================================================== */
/* 4. 波形库打包                                                      */
/* ================================================================== */

static void test_wave_lib(void)
{
    printf("[4] 波形库打包\n");
    const uint16_t base = 0x0800;

    int8_t s1[4] = { 1, -2, 3, -4 };
    int8_t s2[2] = { 100, -100 };
    wave_input_t waves[3] = {
        { 1, s1, 4 },
        { 2, s2, 2 },
    };

    wave_lib_t lib;
    int rc = wave_lib_plan(base, 1, waves, 2, &lib);
    CHECK(rc == 0, "规划失败 rc=%d", rc);
    CHECK(lib.wave_count == 2, "波形条数 %u", (unsigned)lib.wave_count);
    CHECK(lib.total_bytes == (uint16_t)(1 + 8 + 6), "总字节 %u 应为 15",
          (unsigned)lib.total_bytes);

    /* start/end 的绝对地址与端点语义 */
    CHECK(lib.start[0] == base + 9, "波形1 start=0x%04X 应为 0x%04X",
          (unsigned)lib.start[0], (unsigned)(base + 9));
    CHECK(lib.end[0] == base + 12, "波形1 end=0x%04X 应为 0x%04X",
          (unsigned)lib.end[0], (unsigned)(base + 12));
    CHECK(lib.start[1] == base + 13, "波形2 start=0x%04X 应为 0x%04X",
          (unsigned)lib.start[1], (unsigned)(base + 13));
    CHECK(lib.end[1] == base + 14, "波形2 end=0x%04X 应为 0x%04X",
          (unsigned)lib.end[1], (unsigned)(base + 14));
    CHECK((lib.end[0] - lib.start[0] + 1) == 4u, "波形1 长度应为 4%s", "");
    CHECK((lib.end[1] - lib.start[1] + 1) == 2u, "波形2 长度应为 2%s", "");

    uint8_t buf[64];
    size_t n = wave_lib_pack(&lib, waves, 2, buf, sizeof(buf));
    CHECK(n == 15, "打包字节数 %u 应为 15", (unsigned)n);
    CHECK(buf[0] == 1, "版本字节应为 1，实际 %u", (unsigned)buf[0]);
    /* 头块高字节在前 */
    CHECK(buf[1] == (uint8_t)((base + 9) >> 8) && buf[2] == (uint8_t)((base + 9) & 0xFF),
          "波形1 头 start 端序错误 %02X %02X", buf[1], buf[2]);
    CHECK(buf[3] == (uint8_t)((base + 12) >> 8) && buf[4] == (uint8_t)((base + 12) & 0xFF),
          "波形1 头 end 端序错误 %02X %02X", buf[3], buf[4]);
    /* 数据区：版本(1) + 头块(8) 之后 */
    CHECK(memcmp(&buf[9], s1, 4) == 0, "波形1 数据区拼接错误%s", "");
    CHECK(memcmp(&buf[13], s2, 2) == 0, "波形2 数据区拼接错误%s", "");

    /* 容量：刚好满 */
    {
        uint16_t max_samples = wave_lib_max_samples(base, 1);
        static int8_t big[2048];
        memset(big, 0, sizeof(big));
        wave_input_t w[1] = { { 1, big, max_samples } };
        wave_lib_t l2;
        CHECK(wave_lib_plan(base, 1, w, 1, &l2) == 0,
              "刚好满应规划成功（max=%u）", (unsigned)max_samples);
    }
    /* 超一字节 */
    {
        uint16_t max_samples = wave_lib_max_samples(base, 1);
        static int8_t big[4096];
        memset(big, 0, sizeof(big));
        wave_input_t w[1] = { { 1, big, (uint16_t)(max_samples + 1) } };
        wave_lib_t l3;
        CHECK(wave_lib_plan(base, 1, w, 1, &l3) == ERR_SRAM_OVERFLOW,
              "超一字节应报 ERR_SRAM_OVERFLOW%s", "");
    }
    /* N = 127 的容量计算与规划 */
    {
        /* ⚠️ 可用空间 = SRAM 总量(3072) − BASE_ADDR(0x0800=2048) = 1024 字节。
         * 02 文档 §5 的"约 2 kB"是按 BASE_ADDR 以下的字节数说的，与它自己给出的
         * 组库算法（地址 = BASE_ADDR + offset，向上生长）矛盾；协议附录 D 第 12 条
         * 明确要求"按波形库在 BASE_ADDR 之上理解"。此处以算法为准，
         * 并把结论记录在 docs/协议一致性说明.md 里。 */
        uint16_t avail = wave_lib_avail_bytes(base);
        CHECK(avail == 3072u - 0x0800u, "可用空间 %u 应为 1024", (unsigned)avail);

        uint16_t max127 = wave_lib_max_samples(base, 127);
        CHECK(max127 == avail - 1u - 4u * 127u, "N=127 可用样本 %u 计算错误",
              (unsigned)max127);
        static int8_t one[2] = { 1, -1 };
        wave_input_t w[WAVE_LIB_MAX_WAVES];
        for (unsigned i = 0; i < WAVE_LIB_MAX_WAVES; i++) {
            w[i].wave_id = (uint8_t)(i + 1);
            w[i].samples = one;
            w[i].len = 2;
        }
        wave_lib_t l4;
        CHECK(wave_lib_plan(base, 1, w, WAVE_LIB_MAX_WAVES, &l4) == 0,
              "N=127 应规划成功%s", "");
        CHECK(l4.end[126] - l4.start[126] + 1 == 2u, "第 127 条波形长度错误%s", "");
    }
    /* CRC 与库数据一致 */
    {
        uint16_t crc = wave_lib_crc(buf, n);
        CHECK(crc == util_crc16_block(buf, n), "波形库 CRC 与通用 CRC 不一致%s", "");
    }
}

/* ================================================================== */
/* 5. 内置波形合成                                                    */
/* ================================================================== */

static void test_wave_gen(void)
{
    printf("[5] 内置波形合成\n");
    static int8_t buf[4096];
    uint16_t len = 0, actual = 0;

    /* 24 kHz / 175 Hz：样本/周期 = ceil(24000/175) = 138，实际 173 Hz */
    int rc = wave_gen_samples(SHAPE_SINE, 175, 100, 10, RATE_24K, buf, sizeof(buf), &len, &actual);
    CHECK(rc == 0, "合成失败 rc=%d", rc);
    CHECK(len == (uint16_t)(138 * 10), "样本数 %u 应为 1380", (unsigned)len);
    CHECK(actual == (uint16_t)(24000 / 138), "实际频率 %u", (unsigned)actual);

    int peak = 0;
    for (uint16_t i = 0; i < len; i++) {
        int v = buf[i] < 0 ? -buf[i] : buf[i];
        if (v > peak) { peak = v; }
    }
    CHECK(peak <= 100 && peak >= 98, "峰值应在 98..100，实际 %d", peak);

    /* 方波应只有两个电平 */
    rc = wave_gen_samples(SHAPE_SQUARE, 1000, 50, 1, RATE_24K, buf, sizeof(buf), &len, &actual);
    CHECK(rc == 0 && len == 24u, "方波 1 周期应为 24 样本，实际 %u", (unsigned)len);
    for (uint16_t i = 0; i < 24; i++) {
        CHECK(buf[i] == 50 || buf[i] == -50, "方波样本越界 %d", (int)buf[i]);
    }

    /* 容量不足应报错 */
    rc = wave_gen_samples(SHAPE_SINE, 175, 100, 100, RATE_24K, buf, 100, &len, &actual);
    CHECK(rc == ERR_SRAM_OVERFLOW, "容量不足应报 ERR_SRAM_OVERFLOW，实际 %d", rc);

    /* 0 频率非法 */
    rc = wave_gen_samples(SHAPE_SINE, 0, 100, 1, RATE_24K, buf, sizeof(buf), &len, &actual);
    CHECK(rc == ERR_BAD_PARAM, "0 频率应报 ERR_BAD_PARAM，实际 %d", rc);
}

/* ================================================================== */
/* 6. Profile 校验                                                    */
/* ================================================================== */

static void test_profile(void)
{
    printf("[6] Profile 校验\n");
    aw_profile_t p;
    aw_profile_defaults(&p);
    uint8_t off = 0;
    char msg[128];

    CHECK_TRUE(aw_profile_validate(&p, &off, msg, sizeof(msg)), "默认 Profile 应合法");

    uint8_t bytes[AW_PROFILE_LEN];
    CHECK(aw_profile_to_bytes(&p, bytes, sizeof(bytes)) == AW_PROFILE_LEN,
          "打包长度应为 64%s", "");

    /* 往返 */
    aw_profile_t q;
    CHECK(aw_profile_from_bytes(&q, bytes, sizeof(bytes)), "解析失败%s", "");
    CHECK(aw_profile_diff(&p, &q, msg, sizeof(msg)) == 0, "往返后字段不一致：%s", msg);

    /* 线序偏移抽查（协议附录 B） */
    CHECK(bytes[0] == AW_PROFILE_VER, "偏移0 应为 profile_ver%s", "");
    CHECK(bytes[1] == AW_MODE_RAM, "偏移1 应为 mode%s", "");
    CHECK(bytes[2] == DEF_GAIN, "偏移2 应为 gain%s", "");
    CHECK(bytes[28] == DEF_EN_F0_DET, "偏移28 应为 en_f0_det%s", "");
    CHECK(bytes[31] == DEF_F_PRE, "偏移31 应为 f_pre%s", "");
    CHECK(bytes[42] == DEF_WAVDAT_MODE, "偏移42 应为 wavdat_mode%s", "");
    CHECK(bytes[44] == (DEF_BASE_ADDR & 0xFF) && bytes[45] == (DEF_BASE_ADDR >> 8),
          "偏移44/45 应为 base_addr 小端%s", "");
    CHECK(bytes[60] == DEF_TRIG1_MODE, "偏移60 应为 trig1_mode=3%s", "");
    CHECK(bytes[61] == DEF_INTN_PIN, "偏移61 应为 intn_pin=0%s", "");
    CHECK(bytes[63] == 0, "偏移63 应为保留 0%s", "");

    /* 边界：合法最小/最大 */
    p.drv1_lvl = 0;   CHECK_TRUE(aw_profile_validate(&p, &off, msg, sizeof(msg)), "drv1_lvl=0 应合法");
    p.drv1_lvl = 127; CHECK_TRUE(aw_profile_validate(&p, &off, msg, sizeof(msg)), "drv1_lvl=127 应合法");
    /* 越界一个单位 */
    p.drv1_lvl = 128;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 36,
          "drv1_lvl=128 应被拒且偏移为 36，实际 off=%u msg=%s", (unsigned)off, msg);
    p.drv1_lvl = DEF_DRV1_LVL;

    p.f_pre = 0;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 31,
          "f_pre=0 应被拒（偏移 31），实际 off=%u", (unsigned)off);
    p.f_pre = DEF_F_PRE;

    /* profile_ver 错 */
    p.profile_ver = 2;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 0,
          "profile_ver=2 应被拒%s", "");
    p.profile_ver = AW_PROFILE_VER;

    /* 保留字节非 0 */
    p.reserved_ram[2] = 1;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 24,
          "reserved_ram[2] 非 0 应被拒（偏移 24），实际 off=%u", (unsigned)off);
    p.reserved_ram[2] = 0;

    /* TRIG 安全位不可关 */
    p.trig1_stop = 0;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 57,
          "trig1_stop=0 应被拒（偏移 57），实际 off=%u", (unsigned)off);
    p.trig1_stop = 1;
    p.trig1_brk = 0;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 56,
          "trig1_brk=0 应被拒（偏移 56），实际 off=%u", (unsigned)off);
    p.trig1_brk = 1;

    /* 陷阱位：trig1_mode 必须 3、intn_pin 必须 0（默认值 7/1 会被拒） */
    p.trig1_mode = 7;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 60,
          "trig1_mode=7（默认值陷阱 T-1）应被拒%s", "");
    p.trig1_mode = 3;
    p.intn_pin = 1;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 61,
          "intn_pin=1（默认值陷阱 T-2）应被拒%s", "");
    p.intn_pin = 0;

    /* 48K 拒绝 */
    p.wavdat_mode = RATE_48K;
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 42,
          "48K 应被拒（偏移 42），实际 off=%u", (unsigned)off);
    p.wavdat_mode = RATE_12K;

    /* drv_width 不小于 F0 半周期 */
    p.f_pre = 141;                      /* F0 ≈ 170.2 Hz，半周期 ≈ 2938 µs */
    p.drv_width = 255;                  /* 5312 µs，越界 */
    CHECK(!aw_profile_validate(&p, &off, msg, sizeof(msg)) && off == 32,
          "drv_width 越界应被拒（偏移 32），实际 off=%u", (unsigned)off);
    p.drv_width = DEF_DRV_WIDTH;

    /* 满幅判定（协议 §11） */
    p.drv1_lvl = 96;  CHECK_TRUE(!aw_profile_is_full_amplitude(&p, AW_MODE_CONT), "drv1=96 不算满幅");
    p.drv1_lvl = 97;  CHECK_TRUE(aw_profile_is_full_amplitude(&p, AW_MODE_CONT), "drv1=97 算满幅");
    p.gain = 192;     CHECK_TRUE(!aw_profile_is_full_amplitude(&p, AW_MODE_RAM), "gain=192 不算满幅");
    p.gain = 193;     CHECK_TRUE(aw_profile_is_full_amplitude(&p, AW_MODE_RAM), "gain=193 算满幅");
}

/* ================================================================== */
/* 7. 参数换算                                                        */
/* ================================================================== */

static void test_convert(void)
{
    printf("[7] 参数换算\n");
    /* F_PRE ↔ F0 往返 */
    CHECK(fabsf(conv_f_pre_to_f0(141) - 170.2128f) < 0.01f,
          "f_pre=141 -> %.4f Hz", (double)conv_f_pre_to_f0(141));
    CHECK(conv_f0_to_f_pre(170.0f) == 141, "170 Hz -> f_pre=%u",
          (unsigned)conv_f0_to_f_pre(170.0f));
    for (uint8_t c = 1; c < 255; c++) {
        float f0 = conv_f_pre_to_f0(c);
        uint8_t back = conv_f0_to_f_pre(f0);
        CHECK(back == c, "往返 f_pre %u -> %.3f -> %u", (unsigned)c, (double)f0,
              (unsigned)back);
    }
    CHECK(conv_f_pre_to_f0(0) == 0.0f, "f_pre=0 应返回 0%s", "");

    /* F0 换算：一次性检测（mode=0）基准 192000，跟踪（mode=1）基准 384000
     * —— 一次性检测的实测修正见 convert.c 注释 */
    CHECK(fabsf(conv_f0_raw_to_hz(0, 0x0B, 0x70) - 192000.0f / 2928.0f) < 0.001f,
          "一次性 F0 raw 换算 %.4f", (double)conv_f0_raw_to_hz(0, 0x0B, 0x70));
    CHECK(fabsf(conv_f0_raw_to_hz(1, 0x0B, 0x70) - 384000.0f / 2928.0f) < 0.001f,
          "跟踪 F0 raw 换算 %.4f", (double)conv_f0_raw_to_hz(1, 0x0B, 0x70));
    CHECK(conv_f0_raw_to_hz(0, 0, 0) == 0.0f, "raw=0 应返回 0（除零保护）%s", "");

    /* DRV_LVL -> 电压 */
    CHECK(fabsf(conv_drv_lvl_to_volts(128, 5.0f) - 5.0f) < 1e-4f, "128/128 -> 5 V%s", "");
    CHECK(fabsf(conv_drv_lvl_to_volts(64, 5.0f) - 2.5f) < 1e-4f, "64/128 -> 2.5 V%s", "");
    CHECK(fabsf(conv_drv_lvl_to_volts(127, 3.3f) - 3.3f * 127.0f / 128.0f) < 1e-4f,
          "3.3V 满档%s", "");

    /* 时间 code ↔ µs：code/48000 秒 */
    CHECK(conv_code_to_us(48) == 1000u, "48 -> %u µs 应为 1000", (unsigned)conv_code_to_us(48));
    CHECK(conv_code_to_us(8) == 167u, "8 -> %u µs 应为 167", (unsigned)conv_code_to_us(8));
    CHECK(conv_us_to_code(1000) == 48u, "1000 µs -> %u 应为 48", (unsigned)conv_us_to_code(1000));
    CHECK(conv_us_to_code(1000000) == 255u, "超范围应裁剪到 255，实际 %u",
          (unsigned)conv_us_to_code(1000000));

    /* 增益倍率 */
    CHECK(fabsf(conv_gain_x(128) - 1.0f) < 1e-6f, "gain 128 -> 1.0x%s", "");
    CHECK(fabsf(conv_gain_x(64) - 0.5f) < 1e-6f, "gain 64 -> 0.5x%s", "");

    /* VDD / RL */
    CHECK(fabsf(conv_avg_to_vbat(1023) - 6.1f) < 1e-4f, "AVG=1023 -> 6.1 V%s", "");
    CHECK(fabsf(conv_avg_to_vbat(512) - 6.1f * 512.0f / 1023.0f) < 1e-4f, "AVG=512%s", "");
    CHECK(conv_d2s_gain(4) == 8u, "D2S 默认 b100 -> %u 应为 8", (unsigned)conv_d2s_gain(4));
    CHECK(conv_d2s_gain(0) == 1u && conv_d2s_gain(7) == 40u, "D2S 码表端点%s", "");
    CHECK(fabsf(conv_avg_to_rl(512, 8) - 610.0f * 512.0f / (1023.0f * 8.0f)) < 1e-4f,
          "RL 换算%s", "");
    CHECK(fabsf(conv_vbat_ref_volts(2) - 4.0f) < 1e-6f, "VBAT_REF b010 -> 4.0 V%s", "");
    CHECK(conv_rate_hz(RATE_12K) == 12000u && conv_rate_hz(RATE_48K) == 48000u,
          "采样率码表%s", "");

    /* AVG/ADC 位域拼接 */
    CHECK(conv_avg_data(0x02, 0x00) == 512u, "AVG_DATA = b10*256+0 = 512，实际 %u",
          (unsigned)conv_avg_data(0x02, 0x00));
    CHECK(conv_adc_data(0x08, 0x34) == 0x0234u, "ADC_DATA 拼接%s", "");
}

/* ================================================================== */

int main(void)
{
    printf("=== AW86234 固件纯逻辑主机单测 ===\n\n");
    test_crc();
    test_vectors();
    test_parser();
    test_wave_lib();
    test_wave_gen();
    test_profile();
    test_convert();

    printf("\n----------------------------------------\n");
    printf("通过 %d 项，失败 %d 项\n", g_pass, g_fail);
    if (g_fail == 0) {
        printf("全部主机单测通过\n");
    } else {
        printf("*** 有 %d 项失败 ***\n", g_fail);
    }
    return g_fail == 0 ? 0 : 1;
}
