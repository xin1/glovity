/**
 * @file  svc_wave.c
 * @brief 波形下载会话实现。
 */

#include "svc_wave.h"

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

static uint8_t s_session_seq = 0;

/** 会话中每条波形的写入进度（保证 WAVE_ADD 连续）。 */
static uint16_t s_written[WAVE_LIB_MAX_WAVES];

esp_err_t svc_wave_begin(uint8_t ch, uint8_t rate_code, uint8_t wave_count,
                         uint8_t flags, uint8_t *session_id,
                         uint16_t *max_total_samples, uint16_t *sram_free)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || wave_count == 0 || wave_count > WAVE_LIB_MAX_WAVES) {
        return ESP_ERR_INVALID_ARG;
    }
    if (rate_code == RATE_48K) {
        return ESP_ERR_NOT_SUPPORTED;   /* -> ERR_UNSUPPORTED：本板只到 400 kHz */
    }
    if (!c->present) {
        return ESP_ERR_NOT_FOUND;
    }
    if (c->playing) {
        return ESP_ERR_INVALID_STATE;
    }
    if (c->wave_session && !(flags & 0x01u)) {
        return ESP_ERR_INVALID_STATE;   /* 已有会话且未允许覆盖 */
    }

    uint16_t avail = wave_lib_avail_bytes(c->profile.base_addr);
    uint16_t max_total = wave_lib_max_samples(c->profile.base_addr, wave_count);

    memset(c->wave_len, 0, sizeof(c->wave_len));
    memset(s_written, 0, sizeof(s_written));
    c->wave_session = true;
    c->wave_session_id = ++s_session_seq;
    c->wave_rate_code = rate_code;
    /* ★ WAVDAT_MODE（0x46 bit1:0）必须与波形生成时用的采样率一致，否则芯片会按
     *   错误速率解释波形数据，驱动频率成倍偏移（12 kHz 波形当成 24 kHz 播 = 2 倍频）。
     *   这里把会话选定的采样率同步进 profile，aw_apply_profile() 会写进芯片。 */
    c->profile.wavdat_mode = (uint8_t)(rate_code & 0x03u);
    c->wave_count = wave_count;
    c->wave_total_samples = 0;
    c->long_op = true;
    c->state = CH_BUSY;

    if (session_id)        { *session_id = c->wave_session_id; }
    if (max_total_samples) { *max_total_samples = max_total; }
    if (sram_free)         { *sram_free = avail; }
    LOGI("CH%u 进入波形下载会话 #%u：%u 条波形，可用样本上限 %u（SRAM 余量 %u 字节）",
         ch, c->wave_session_id, wave_count, max_total, avail);
    return ESP_OK;
}

esp_err_t svc_wave_add(uint8_t ch, uint8_t wave_id, uint16_t offset,
                       uint16_t total_samples, uint8_t flags,
                       const int8_t *samples, uint8_t len, uint16_t *offset_ack)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->wave_session) {
        return ESP_ERR_INVALID_STATE;   /* -> ERR_NOT_ARMED */
    }
    if (wave_id == 0 || wave_id > WAVE_LIB_MAX_WAVES || samples == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    /* offset 必须连续（协议 §9.1）；不连续直接拒绝，便于早发现上位机 bug */
    if (offset != s_written[wave_id - 1]) {
        LOGW("CH%u 波形 %u 分片不连续：offset=%u 期望 %u", ch, wave_id,
             offset, s_written[wave_id - 1]);
        return ESP_ERR_INVALID_ARG;
    }
    if (offset + len > WAVE_BUF_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    uint16_t new_total = (uint16_t)(c->wave_total_samples + len);
    if (new_total > WAVE_BUF_BYTES) {
        return ESP_ERR_INVALID_SIZE;    /* -> ERR_SRAM_OVERFLOW */
    }

    memcpy(&c->wave_buf[offset], samples, len);
    s_written[wave_id - 1] = (uint16_t)(offset + len);
    c->wave_len[wave_id - 1] = (flags & 0x01u) ? total_samples
                                               : (uint16_t)(offset + len);
    c->wave_total_samples = new_total;
    if (offset_ack) {
        *offset_ack = offset;
    }
    return ESP_OK;
}

esp_err_t svc_wave_commit(uint8_t ch, uint8_t lib_version, uint16_t *total_bytes,
                          uint16_t *lib_crc16, uint16_t *base_addr,
                          wave_lib_t *lib_out)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->wave_session) {
        return ESP_ERR_INVALID_STATE;
    }

    /* 组库：只把真正收到的波形计入 */
    wave_input_t inputs[WAVE_LIB_MAX_WAVES];
    uint8_t n = 0;
    uint16_t cursor = 0;
    for (uint8_t i = 0; i < WAVE_LIB_MAX_WAVES; i++) {
        if (c->wave_len[i] == 0) {
            continue;
        }
        inputs[n].wave_id = (uint8_t)(i + 1);
        inputs[n].samples = &c->wave_buf[cursor];
        inputs[n].len = c->wave_len[i];
        cursor = (uint16_t)(cursor + c->wave_len[i]);
        n++;
    }
    if (n == 0) {
        return ESP_ERR_INVALID_STATE;
    }

    wave_lib_t lib;
    int rc = wave_lib_plan(c->profile.base_addr, lib_version, inputs, n, &lib);
    if (rc != 0) {
        LOGW("CH%u 波形库规划失败：样本总数 %u 超过可用容量", ch, c->wave_total_samples);
        return (rc == ERR_SRAM_OVERFLOW) ? ESP_ERR_INVALID_SIZE : ESP_ERR_INVALID_ARG;
    }

    static uint8_t s_lib_buf[WAVE_LIB_MAX_BYTES];
    size_t packed = wave_lib_pack(&lib, inputs, n, s_lib_buf, sizeof(s_lib_buf));
    if (packed == 0) {
        return ESP_ERR_INVALID_SIZE;
    }

    int64_t t0 = esp_timer_get_time();
    esp_err_t err = aw_wave_lib_write(ch, lib.base_addr, s_lib_buf, packed);
    if (err != ESP_OK) {
        return err;
    }

    /* ★ 第 8 步不能省：回读 SRAM 并校验 CRC（验收标准第 5 条的技术保障） */
    static uint8_t s_read_buf[WAVE_LIB_MAX_BYTES];
    err = aw_wave_lib_read(ch, lib.base_addr, s_read_buf, packed);
    if (err != ESP_OK) {
        return err;
    }
    if (memcmp(s_read_buf, s_lib_buf, packed) != 0) {
        uint16_t want = wave_lib_crc(s_lib_buf, packed);
        uint16_t got = wave_lib_crc(s_read_buf, packed);
        LOGE("CH%u 波形库回读校验失败：CRC 0x%04X != 0x%04X", ch, want, got);
        return ESP_ERR_INVALID_CRC;     /* -> ERR_CRC_MISMATCH */
    }

    uint32_t elapsed_ms = (uint32_t)((esp_timer_get_time() - t0) / 1000);
    LOGI("CH%u 波形库下发完成：%u 条 / %u 字节 / CRC 0x%04X / 耗时 %u ms",
         ch, n, (unsigned)packed, wave_lib_crc(s_lib_buf, packed),
         (unsigned)elapsed_ms);

    c->wave_session = false;
    c->long_op = false;
    c->state = c->playing ? CH_PLAYING : CH_IDLE;

    if (total_bytes) { *total_bytes = (uint16_t)packed; }
    if (lib_crc16)   { *lib_crc16 = wave_lib_crc(s_lib_buf, packed); }
    if (base_addr)   { *base_addr = lib.base_addr; }
    if (lib_out)     { *lib_out = lib; }
    return ESP_OK;
}

esp_err_t svc_wave_abort(uint8_t ch)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    c->wave_session = false;
    c->long_op = false;
    c->wave_total_samples = 0;
    memset(c->wave_len, 0, sizeof(c->wave_len));
    if (c->state == CH_BUSY) {
        c->state = c->playing ? CH_PLAYING : CH_IDLE;
    }
    return ESP_OK;
}

esp_err_t svc_wave_erase(uint8_t ch)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->present) {
        return ESP_ERR_NOT_FOUND;
    }
    /* 重建一个空库：版本字节 + 0 条波形 */
    uint8_t empty = 1;
    esp_err_t err = aw_wave_lib_write(ch, c->profile.base_addr, &empty, 1);
    if (err == ESP_OK) {
        err = svc_wave_abort(ch);
    }
    LOGI("CH%u 波形库已清空（注意 SRAM 是易失的，芯片断电即丢）", ch);
    return err;
}

esp_err_t svc_wave_get_library(uint8_t ch, uint8_t *lib_version,
                               uint8_t *wave_count, uint16_t *total_bytes,
                               uint16_t *base_addr, wave_lib_t *lib_out)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->present) {
        return ESP_ERR_NOT_FOUND;
    }
    uint16_t base = c->profile.base_addr;
    /* 头块最长 = 1 + 4*127 = 509 字节，分段读 */
    static uint8_t head[512];
    size_t head_len = 1 + 4u * WAVE_LIB_MAX_WAVES;
    esp_err_t err = aw_wave_lib_read(ch, base, head, head_len);
    if (err != ESP_OK) {
        return err;
    }

    uint8_t ver = head[0];
    uint8_t n = 0;
    uint16_t max_end = 0;
    wave_lib_t lib;
    memset(&lib, 0, sizeof(lib));
    lib.lib_version = ver;
    lib.base_addr = base;

    /* 逐条解析头块，直到出现全 0（未使用）或地址倒退 */
    for (uint8_t i = 0; i < WAVE_LIB_MAX_WAVES; i++) {
        const uint8_t *h = &head[1 + 4u * i];
        uint16_t start = (uint16_t)(((uint16_t)h[0] << 8) | h[1]);
        uint16_t end = (uint16_t)(((uint16_t)h[2] << 8) | h[3]);
        if (start == 0 && end == 0) {
            break;
        }
        if (start < base || end < start) {
            break;
        }
        lib.start[i] = start;
        lib.end[i] = end;
        n++;
        if (end > max_end) {
            max_end = end;
        }
    }
    lib.wave_count = n;
    lib.total_bytes = (n == 0) ? 1u : (uint16_t)(max_end - base + 1u);

    if (lib_version) { *lib_version = ver; }
    if (wave_count)  { *wave_count = n; }
    if (base_addr)   { *base_addr = base; }
    if (total_bytes) { *total_bytes = lib.total_bytes; }
    if (lib_out)     { *lib_out = lib; }
    return ESP_OK;
}

esp_err_t svc_wave_readback(uint8_t ch, uint8_t wave_id, uint16_t offset,
                            uint8_t len, int8_t *out)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL || !c->present || out == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (wave_id == 0 || wave_id > WAVE_LIB_MAX_WAVES) {
        return ESP_ERR_INVALID_ARG;
    }
    uint16_t base = c->profile.base_addr;
    /* 先读头块拿到该波形的起始地址 */
    uint8_t head[5];
    esp_err_t err = aw_wave_lib_read(ch, (uint16_t)(base + 1u + 4u * (wave_id - 1u)),
                                     head, 4);
    if (err != ESP_OK) {
        return err;
    }
    uint16_t start = (uint16_t)(((uint16_t)head[0] << 8) | head[1]);
    uint16_t end = (uint16_t)(((uint16_t)head[2] << 8) | head[3]);
    if (start < base || end < start) {
        return ESP_ERR_INVALID_ARG;
    }
    uint16_t wlen = (uint16_t)(end - start + 1u);
    if ((uint32_t)offset + len > wlen) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t tmp[BOARD_REG_MAX_LEN];
    err = aw_wave_lib_read(ch, (uint16_t)(start + offset), tmp, len);
    if (err != ESP_OK) {
        return err;
    }
    for (uint8_t i = 0; i < len; i++) {
        out[i] = (int8_t)tmp[i];
    }
    return ESP_OK;
}

esp_err_t svc_wave_gen(uint8_t ch, uint8_t wave_id, uint8_t shape,
                       uint16_t freq_hz, uint8_t amplitude, uint16_t cycles,
                       uint8_t rate_code, uint16_t *generated)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    /* 协议 §9.8：WAVE_GEN「把合成结果加入**当前会话**」——必须先 WAVE_BEGIN。
     * （ASCII 的 `wave` 命令走 svc_wave_quick_play()，由它自己 begin/commit。） */
    if (!c->wave_session) {
        return ESP_ERR_INVALID_STATE;
    }
    if (wave_id == 0 || wave_id > WAVE_LIB_MAX_WAVES) {
        return ESP_ERR_INVALID_ARG;
    }

    static int8_t gen_buf[WAVE_BUF_BYTES];
    uint16_t len = 0, actual_hz = 0;
    int rc = wave_gen_samples(shape, freq_hz, amplitude, cycles, rate_code,
                              gen_buf, sizeof(gen_buf), &len, &actual_hz);
    if (rc != 0) {
        return (rc == ERR_SRAM_OVERFLOW) ? ESP_ERR_INVALID_SIZE : ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = svc_wave_add(ch, wave_id, 0, len, 0x01u, gen_buf, (uint8_t)len, NULL);
    if (err != ESP_OK) {
        return err;
    }
    if (generated) {
        *generated = len;
    }
    LOGI("CH%u 合成波形 %u：shape=%u 目标 %u Hz 实际 %u Hz 幅度 %u 周期 %u -> %u 样本",
         ch, wave_id, shape, freq_hz, actual_hz, amplitude, cycles, len);
    return ESP_OK;
}

/** 单周期样本数（四舍五入），用于容器容量估算。 */
static uint16_t wave_samples_per_cycle(uint16_t freq_hz, uint8_t rate_code)
{
    uint32_t r = conv_rate_hz(rate_code);
    if (freq_hz == 0 || r == 0) {
        return 0;
    }
    uint32_t n = (r + (uint32_t)freq_hz / 2u) / (uint32_t)freq_hz;
    return (uint16_t)(n ? n : 1u);
}

esp_err_t svc_wave_quick_play(uint8_t ch, uint8_t wave_id, uint8_t shape,
                              uint16_t freq_hz, uint8_t amplitude, uint16_t cycles,
                              uint16_t *generated)
{
    ch_ctx_t *c = ch_ctx(ch);
    if (c == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t rate = (c->wave_rate_code != 0) ? c->wave_rate_code : RATE_24K;

    /* ★ 采样率自适应 + 周期数收敛。
     *   默认 24 kHz 下 175 Hz 每周期约 137 个样本，而波形库只有约 1 kB 预算，
     *   因此 README 里的 `wave <ch> 1 175 90 40` 必然 ERR_SRAM_OVERFLOW
     *   （40 周期需约 5486 样本）。这里先降速到 12 kHz，仍放不下就把周期数
     *   收敛到能放下的最大值，保证命令可用，而不是直接失败。 */
    if (rate == RATE_24K) {
        uint16_t budget = wave_lib_max_samples(c->profile.base_addr, 1);
        uint32_t need24 = (uint32_t)wave_samples_per_cycle(freq_hz, RATE_24K) * cycles;
        if (need24 > budget) {
            rate = RATE_12K;
        }
    }
    {
        uint16_t budget = wave_lib_max_samples(c->profile.base_addr, 1);
        uint16_t spc = wave_samples_per_cycle(freq_hz, rate);
        if (spc > 0 && (uint32_t)spc * cycles > budget) {
            uint16_t max_cycles = (uint16_t)(budget / spc);
            if (max_cycles == 0) {
                return ESP_ERR_INVALID_SIZE;
            }
            LOGW("CH%u wave：%u 周期 @ %u Hz 采样率超出波形库预算，自动收敛为 %u 周期",
                 ch, cycles, (unsigned)conv_rate_hz(rate), max_cycles);
            cycles = max_cycles;
        }
    }

    /* 一次性"生成 → 提交 → 配 RAM → 排队"，服务 ASCII 的 `wave` 命令（M2 里程碑工具）。
     * 全部步骤显式调用，不依赖 WAVE_GEN 的隐式会话。 */
    esp_err_t err = svc_wave_begin(ch, rate, 1, 0x01u, NULL, NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    err = svc_wave_gen(ch, wave_id, shape, freq_hz, amplitude, cycles, rate, generated);
    if (err != ESP_OK) {
        (void)svc_wave_abort(ch);
        return err;
    }
    err = svc_wave_commit(ch, 1, NULL, NULL, NULL, NULL);
    if (err != ESP_OK) {
        return err;
    }
    err = svc_play_set_mode(ch, AW_MODE_RAM);
    if (err != ESP_OK) {
        return err;
    }
    uint8_t ids[8] = { wave_id, 0, 0, 0, 0, 0, 0, 0 };
    return aw_write_wave_queue(ch, 1, ids);
}
