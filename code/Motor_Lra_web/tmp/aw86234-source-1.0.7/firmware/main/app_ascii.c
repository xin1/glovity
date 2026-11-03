/**
 * @file  app_ascii.c
 * @brief ASCII 引导模式：上电默认模式，人可读的行文本命令（协议附录 A）。
 *
 * 目的：裸板在没有上位机的情况下也能点亮、排查。
 * ★ `wave` 命令是 M2 里程碑（"板子到底能不能振"）的关键工具，**不能省略**。
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app_protocol.h"
#include "app_session.h"
#include "aw_profile.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "convert.h"
#include "drv_aw8623x.h"
#include "drv_i2c_bus.h"
#include "drv_tca9548a.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "svc_measure.h"
#include "svc_play.h"
#include "svc_selftest.h"
#include "svc_wave.h"
#include "util_log.h"

static char    s_line[ASCII_LINE_MAX];
static size_t  s_line_len = 0;

const char *err_name_str(uint8_t code);
static void app_ascii_handle_line(char *line);

/* ------------------------------------------------------------------ */

static void out(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    proto_port_write_text(buf);
    proto_port_write_text("\n");
}

static void out_ok(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    proto_port_write_text("OK ");
    proto_port_write_text(buf);
    proto_port_write_text("\n");
}

static void out_err(uint8_t code, const char *text)
{
    char buf[256];
    snprintf(buf, sizeof(buf), "ERR %u %s %s", (unsigned)code, err_name_str(code),
             text ? text : "");
    proto_port_write_text(buf);
    proto_port_write_text("\n");
}

const char *err_name_str(uint8_t code)
{
    switch (code) {
    case ERR_OK:              return "OK";
    case ERR_UNKNOWN_CMD:     return "ERR_UNKNOWN_CMD";
    case ERR_BAD_LENGTH:      return "ERR_BAD_LENGTH";
    case ERR_BAD_PARAM:       return "ERR_BAD_PARAM";
    case ERR_BAD_CHANNEL:     return "ERR_BAD_CHANNEL";
    case ERR_NOT_PRESENT:     return "ERR_NOT_PRESENT";
    case ERR_I2C_TIMEOUT:     return "ERR_I2C_TIMEOUT";
    case ERR_I2C_NACK:        return "ERR_I2C_NACK";
    case ERR_BUS_BUSY:        return "ERR_BUS_BUSY";
    case ERR_NOT_INITIALIZED: return "ERR_NOT_INITIALIZED";
    case ERR_WRONG_MODE:      return "ERR_WRONG_MODE";
    case ERR_BUSY:            return "ERR_BUSY";
    case ERR_NOT_PLAYING:     return "ERR_NOT_PLAYING";
    case ERR_SRAM_OVERFLOW:   return "ERR_SRAM_OVERFLOW";
    case ERR_CRC_MISMATCH:    return "ERR_CRC_MISMATCH";
    case ERR_UNSUPPORTED:     return "ERR_UNSUPPORTED";
    case ERR_SAFETY_LIMIT:    return "ERR_SAFETY_LIMIT";
    case ERR_SAFETY_TIMEOUT:  return "ERR_SAFETY_TIMEOUT";
    case ERR_HEARTBEAT_LOST:  return "ERR_HEARTBEAT_LOST";
    case ERR_INTERNAL:        return "ERR_INTERNAL";
    case ERR_VERIFY_FAILED:   return "ERR_VERIFY_FAILED";
    case ERR_STORAGE:         return "ERR_STORAGE";
    case ERR_TCA_NOT_FOUND:   return "ERR_TCA_NOT_FOUND";
    case ERR_CHIPID_MISMATCH: return "ERR_CHIPID_MISMATCH";
    case ERR_NOT_ARMED:       return "ERR_NOT_ARMED";
    case ERR_TOO_MANY_CHUNKS: return "ERR_TOO_MANY_CHUNKS";
    default:                  return "ERR_UNKNOWN";
    }
}

void emit_banner(void)
{
    char buf[128];
    snprintf(buf, sizeof(buf), "READY aw86234-testbench fw=%s board=%u",
             FW_VERSION_STR, (unsigned)BOARD_ID_VIBRATION_TEST_BOARD_A);
    proto_port_write_text(buf);
    proto_port_write_text("\n");
    proto_port_write_text("输入 help 查看命令；发送 \"MODE BIN\" 切换到二进制协议模式\n");
}

void app_ascii_enter(void)
{
    app_session_set_ascii_mode(true);
    svc_play_trig_park();
    s_line_len = 0;
    proto_port_write_text("OK proto=ascii\n");
}

bool app_ascii_is_active(void)
{
    return app_session_ascii_mode();
}

/* ------------------------------------------------------------------ */
/* 命令实现                                                            */
/* ------------------------------------------------------------------ */

static int hexval(const char *s, unsigned *out)
{
    if (s == NULL) {
        return -1;
    }
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
    }
    unsigned v = 0;
    int n = 0;
    while (*s) {
        int d;
        if (*s >= '0' && *s <= '9')      { d = *s - '0'; }
        else if (*s >= 'a' && *s <= 'f') { d = *s - 'a' + 10; }
        else if (*s >= 'A' && *s <= 'F') { d = *s - 'A' + 10; }
        else { return -1; }
        v = (v << 4) | (unsigned)d;
        n++;
        s++;
    }
    if (n == 0) {
        return -1;
    }
    *out = v;
    return 0;
}

static int decval(const char *s, long *out)
{
    if (s == NULL) {
        return -1;
    }
    char *end = NULL;
    long v = strtol(s, &end, 10);
    if (end == s) {
        return -1;
    }
    *out = v;
    return 0;
}

static void cmd_help(void)
{
    proto_port_write_text(
        "命令（不区分大小写；十六进制可写 0x57 或 57）：\n"
        "  help / ver / info / status\n"
        "  mode bin                    切换到二进制协议模式\n"
        "  log <0..4>                  日志级别\n"
        "  scan                        扫描 TCA 地址 0x70~0x77\n"
        "  bus <auto|lp|hp>            I2C 引脚探测\n"
        "  probe <ch|all>              探测通道，打印 CHIPID\n"
        "  r <ch> <reg> [len]          读寄存器（hex）\n"
        "  w <ch> <reg> <val>          写单字节（hex）\n"
        "  dump <ch> <start> <end>     打印寄存器范围（hex）\n"
        "  softrst <ch>                软复位\n"
        "  default <ch>                恢复默认 Profile\n"
        "  playmode <ch> <ram|rtp|cont|trig>\n"
        "  play <ch> [timeout_ms]      开始播放\n"
        "  stop <ch|all>               停止\n"
        "  park                        所有 TRIG 拉低\n"
        "  trig <mask> <width_us> [count] [period_ms]   硬触发脉冲串\n"
        "  cont <ch> <f0_hz> <lvl> [ms]   持续振动（CONT 内部发生器，无限循环，默认按会话超时停）\n"
        "  f0 <ch>                     触发 F0 检测并打印结果\n"
        "  wave <ch> <id> <freq_hz> <amp> <cycles>      内置正弦生成 + RAM 播放\n"
        "  selftest                    运行完整自检\n");
}

static void cmd_scan(void)
{
    const bus_info_t *bi = bus_info();
    if (bi == NULL || bi->sel_used == 0) {
        out_err(ERR_NOT_INITIALIZED, "请先执行 bus auto");
        return;
    }
    proto_port_write_text("OK 扫描 0x70~0x77:\n");
    for (uint8_t a = 0x70; a <= 0x77; a++) {
        esp_err_t err = bus_probe_dev(a, BOARD_I2C_XFER_TIMEOUT_MS);
        if (err == ESP_OK) {
            out("  0x%02X  在线", a);
        }
    }
    out("  扫描完成（本板 TCA 地址固定 0x70，A0=A1=A2=GND）");
}

static void cmd_probe(const char *arg)
{
    if (arg == NULL) {
        out_err(ERR_BAD_PARAM, "probe <ch|all>");
        return;
    }
    if (strcasecmp(arg, "all") == 0) {
        uint8_t mask = 0;
        for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
            uint16_t id = 0;
            ch_ctx_t *c = ch_ctx(ch);
            if (aw_read_chipid(ch, &id) == ESP_OK) {
                uint8_t vreg = 0xFF;
                (void)aw_read_vreg_ok(ch, &vreg);
                c->present = 1;
                c->chipid = id;
                c->vreg_ok = vreg;
                if (c->state == CH_ABSENT) {
                    c->state = CH_IDLE;
                }
                mask |= (uint8_t)(1u << ch);
                out("  CH%u CHIPID=0x%04X%s VREG=%s", ch, id,
                    (id == BOARD_AW_CHIPID_EXPECTED) ? "" : "（与预期 0x2340 不同）",
                    (vreg == 1) ? "OK" : (vreg == 0 ? "异常" : "未读到"));
            } else {
                c->present = 0;
                out("  CH%u 无应答", ch);
            }
        }
        out_ok("present_mask=0x%02X", mask);
        return;
    }
    unsigned ch = 0;
    if (hexval(arg, &ch) != 0 || ch >= BOARD_CHANNEL_COUNT) {
        out_err(ERR_BAD_CHANNEL, "通道号 0..7");
        return;
    }
    uint16_t id = 0;
    ch_ctx_t *c = ch_ctx((uint8_t)ch);
    if (aw_read_chipid((uint8_t)ch, &id) != ESP_OK) {
        c->present = 0;
        out_err(ERR_NOT_PRESENT, "0x5A 无 ACK（检查该路线束/驱动板/VDD）");
        return;
    }
    uint8_t vreg = 0xFF;
    (void)aw_read_vreg_ok((uint8_t)ch, &vreg);
    c->present = 1;
    c->chipid = id;
    c->vreg_ok = vreg;
    if (c->state == CH_ABSENT) {
        c->state = CH_IDLE;
    }
    out_ok("ch=%u chipid=0x%04X vreg_ok=%u", ch, id, vreg);
}

static void cmd_read(const char *a1, const char *a2, const char *a3)
{
    unsigned ch = 0, reg = 0, len = 1;
    if (hexval(a1, &ch) != 0 || hexval(a2, &reg) != 0 ||
        (a3 != NULL && hexval(a3, &len) != 0)) {
        out_err(ERR_BAD_PARAM, "r <ch> <reg> [len]，均为十六进制");
        return;
    }
    if (ch >= BOARD_CHANNEL_COUNT || len < 1 || len > BOARD_REG_MAX_LEN) {
        out_err(ERR_BAD_PARAM, "ch 0..7，len 1..64");
        return;
    }
    uint8_t buf[BOARD_REG_MAX_LEN];
    if (aw_read((uint8_t)ch, (uint8_t)reg, buf, len) != ESP_OK) {
        out_err(ERR_I2C_NACK, "读寄存器失败");
        return;
    }
    char line[220];
    int n = snprintf(line, sizeof(line), "OK ch=%u reg=0x%02X:", ch, reg);
    for (unsigned i = 0; i < len && n < (int)sizeof(line) - 4; i++) {
        n += snprintf(line + n, sizeof(line) - (size_t)n, " %02X", buf[i]);
    }
    proto_port_write_text(line);
    proto_port_write_text("\n");
}

static void cmd_write(const char *a1, const char *a2, const char *a3)
{
    unsigned ch = 0, reg = 0, val = 0;
    if (hexval(a1, &ch) != 0 || hexval(a2, &reg) != 0 || hexval(a3, &val) != 0) {
        out_err(ERR_BAD_PARAM, "w <ch> <reg> <val>，均为十六进制");
        return;
    }
    if (ch >= BOARD_CHANNEL_COUNT) {
        out_err(ERR_BAD_CHANNEL, "通道号 0..7");
        return;
    }
    if (aw_write_u8((uint8_t)ch, (uint8_t)reg, (uint8_t)val) != ESP_OK) {
        out_err(ERR_I2C_NACK, "写寄存器失败");
        return;
    }
    out_ok("ch=%u reg=0x%02X <= 0x%02X", ch, reg, val & 0xFFu);
}

static void cmd_dump(const char *a1, const char *a2, const char *a3)
{
    unsigned ch = 0, start = 0, end = 0;
    if (hexval(a1, &ch) != 0 || hexval(a2, &start) != 0 || hexval(a3, &end) != 0) {
        out_err(ERR_BAD_PARAM, "dump <ch> <start> <end>，均为十六进制");
        return;
    }
    if (ch >= BOARD_CHANNEL_COUNT || end < start || (end - start) > 63) {
        out_err(ERR_BAD_PARAM, "ch 0..7，范围 ≤64 字节");
        return;
    }
    uint8_t buf[64];
    uint8_t len = (uint8_t)(end - start + 1);
    if (aw_read((uint8_t)ch, (uint8_t)start, buf, len) != ESP_OK) {
        out_err(ERR_I2C_NACK, "dump 失败");
        return;
    }
    for (unsigned i = 0; i < len; i += 16) {
        char line[128];
        int n = snprintf(line, sizeof(line), "%02X:", (unsigned)(start + i));
        for (unsigned j = i; j < len && j < i + 16; j++) {
            n += snprintf(line + n, sizeof(line) - (size_t)n, " %02X", buf[j]);
        }
        proto_port_write_text(line);
        proto_port_write_text("\n");
    }
    out_ok("dump 完成");
}

static void cmd_playmode(const char *a1, const char *a2)
{
    unsigned ch = 0;
    if (hexval(a1, &ch) != 0 || a2 == NULL || ch >= BOARD_CHANNEL_COUNT) {
        out_err(ERR_BAD_PARAM, "playmode <ch> <ram|rtp|cont|trig>");
        return;
    }
    uint8_t mode;
    if (strcasecmp(a2, "ram") == 0)       { mode = AW_MODE_RAM; }
    else if (strcasecmp(a2, "rtp") == 0)  { mode = AW_MODE_RTP; }
    else if (strcasecmp(a2, "cont") == 0) { mode = AW_MODE_CONT; }
    else if (strcasecmp(a2, "trig") == 0) { mode = AW_MODE_TRIG; }
    else {
        out_err(ERR_BAD_PARAM, "模式只接受 ram/rtp/cont/trig");
        return;
    }
    esp_err_t err = svc_play_set_mode((uint8_t)ch, mode);
    if (err != ESP_OK) {
        out_err((err == ESP_ERR_INVALID_STATE) ? ERR_VERIFY_FAILED : ERR_INTERNAL,
                "切换模式失败（TRIG 会先校验 0x3A bit2 / 0x33 bit7）");
        return;
    }
    out_ok("ch=%u mode=%s", ch, a2);
}

static void cmd_play(const char *a1, const char *a2)
{
    unsigned ch = 0;
    long to = 0;
    if (hexval(a1, &ch) != 0 || ch >= BOARD_CHANNEL_COUNT) {
        out_err(ERR_BAD_PARAM, "play <ch> [timeout_ms]");
        return;
    }
    if (a2 != NULL && decval(a2, &to) != 0) {
        out_err(ERR_BAD_PARAM, "timeout_ms 必须是十进制");
        return;
    }
    uint16_t actual = 0;
    esp_err_t err = svc_play_start((uint8_t)ch, (uint16_t)to, false, &actual);
    if (err == ESP_ERR_INVALID_STATE) {
        out_err(ERR_SAFETY_LIMIT, "超过同时满幅路数限制");
        return;
    }
    if (err != ESP_OK) {
        out_err((err == ESP_ERR_NOT_FOUND) ? ERR_NOT_PRESENT : ERR_INTERNAL, "播放失败");
        return;
    }
    out_ok("ch=%u 已启动，安全超时 %u ms", ch, actual);
}

static void cmd_stop(const char *a1)
{
    if (a1 == NULL) {
        out_err(ERR_BAD_PARAM, "stop <ch|all>");
        return;
    }
    if (strcasecmp(a1, "all") == 0) {
        uint8_t stopped = 0;
        svc_play_stop_all(STOP_MODE_IMMEDIATE, &stopped);
        out_ok("已停止全部通道（mask=0x%02X），TRIG 已静默", stopped);
        return;
    }
    unsigned ch = 0;
    if (hexval(a1, &ch) != 0 || ch >= BOARD_CHANNEL_COUNT) {
        out_err(ERR_BAD_CHANNEL, "通道号 0..7 或 all");
        return;
    }
    (void)svc_play_stop((uint8_t)ch, STOP_MODE_IMMEDIATE);
    out_ok("ch=%u 已停止", ch);
}

static void cmd_trig(const char *a1, const char *a2, const char *a3, const char *a4)
{
    unsigned mask = 0;
    long w = 0, c = 1, p = 100;
    if (a1 == NULL || hexval(a1, &mask) != 0) {
        out_err(ERR_BAD_PARAM, "trig <mask(hex)> <width_us> [count] [period_ms]");
        return;
    }
    if (a2 == NULL) {
        out_err(ERR_BAD_PARAM, "trig <mask(hex)> <width_us> [count] [period_ms]");
        return;
    }
    /* width_us 允许十进制或十六进制 */
    unsigned wu = 0;
    if (decval(a2, &w) == 0) {
        wu = (unsigned)w;
    } else if (hexval(a2, &wu) != 0) {
        out_err(ERR_BAD_PARAM, "width_us 解析失败");
        return;
    }
    if (a3 != NULL && decval(a3, &c) == 0) { /* 使用十进制 */ }
    if (a4 != NULL && decval(a4, &p) == 0) { /* 使用十进制 */ }
    if (c < 1 || c > 32 || p < 0 || wu < 1 || wu > 65535) {
        out_err(ERR_BAD_PARAM, "width 1..65535 µs，count 1..32，period ≥0");
        return;
    }
    uint8_t started = 0;
    esp_err_t err = svc_play_trig_pulse((uint8_t)mask, (uint16_t)wu, (uint8_t)c,
                                        (uint16_t)p, 0, &started);
    if (err == ESP_ERR_INVALID_STATE) {
        out_err(ERR_SAFETY_LIMIT, "超过同时满幅路数限制");
        return;
    }
    if (err != ESP_OK) {
        out_err(ERR_BAD_PARAM, "参数越界（width ≥1 µs，count ≤32）");
        return;
    }
    out_ok("trig mask=0x%02X width=%u µs count=%ld period=%ld ms started=0x%02X",
           mask, wu, c, p, started);
    if (mask & BOARD_TRIG_SHARED_MASK) {
        out("  注意：掩码含 CH5/6/7 —— 三者共用 GPIO14，必然一起动");
    }
}

static void cmd_cont(const char *a1, const char *a2, const char *a3, const char *a4)
{
    unsigned ch = 0;
    long f0 = 0, lvl = 0, timeout = 0;
    if (hexval(a1, &ch) != 0 || decval(a2, &f0) != 0 || decval(a3, &lvl) != 0 ||
        (a4 != NULL && decval(a4, &timeout) != 0)) {
        out_err(ERR_BAD_PARAM, "cont <ch> <f0_hz> <lvl> [timeout_ms]");
        return;
    }
    if (ch >= BOARD_CHANNEL_COUNT || f0 <= 0 || lvl < 0 || lvl > 127 ||
        timeout < 0) {
        out_err(ERR_BAD_PARAM, "ch 0..7，f0 >0，lvl 0..127，timeout >=0");
        return;
    }
    ch_ctx_t *c = ch_ctx((uint8_t)ch);
    if (!c->present) {
        out_err(ERR_NOT_PRESENT, "该通道无器件应答，请先 probe");
        return;
    }
    /* ★ "持续振动"的正确配置（2026-09 实测，AW86234FCR）：
     *   CONT 模式用的是**芯片内部方波发生器**（不读 SRAM 波形库），因此没有波形
     *   循环接缝 —— 实测连续平稳；而 RAM 模式循环播放波形库会"一块一块"地断续
     *   （每轮波形边界一次，10 周期约 59 ms ⇒ ~17 Hz 的顿挫感）。
     *
     *   三个必须改的参数（外加一个关键的 WAIT_NUM）：
     *     · DRV1_TIME=255：CONT 的驱动段长度上限（半周期数）。默认 4 只够 12 ms。
     *     · DRV2_TIME=0 / BRK_TIME=0：默认 6/8 会在稳态里插入低幅度段和**主动刹车段**
     *       （18 个半周期里有 8 个在刹车），听感是"发闷、像撞行程末端"。
     *     · WAIT_NUM=0：★ 真正的"断续"元凶。默认 6（环衰减时间，单位 1/48k s）会在
     *       **每个驱动段之间插入停顿**，听感是"一块一块的"（周期正好等于 DRV1_TIME；
     *       D1=255 @169 Hz ≈ 754 ms，实测就是每 ~0.8 s 一段）。置 0 后连续平稳。
     *     · MAINLOOP=0xF（0x16 bit3:0）：无限循环。默认 0 = 只播一轮就停。
     *   （MAINLOOP 之前被写到 0x15，是个寄存器偏移 bug，已修。CONT 段本身不写它，
     *     但保持 profile 一致以免切到 RAM 时行为异常。） */
    c->profile.mode = AW_MODE_CONT;
    aw_profile_set_f0(&c->profile, (float)f0);
    c->profile.drv1_lvl = (uint8_t)lvl;
    c->profile.track_en = 1;
    c->profile.drv1_time = 255;
    c->profile.drv2_time = 0;
    c->profile.brk_time = 0;
    c->profile.wait_num = 0;        /* ★ 见上：不置 0 就会一段一段 */
    c->profile.mainloop = 0x0Fu;
    esp_err_t err = aw_apply_profile((uint8_t)ch, &c->profile);
    if (err == ESP_OK) {
        err = svc_play_start((uint8_t)ch, (uint16_t)timeout, false, NULL);
    }
    if (err != ESP_OK) {
        out_err(ERR_INTERNAL, "CONT 配置或播放失败");
        return;
    }
    out_ok("ch=%u CONT 持续振动已启动：目标 %ld Hz（实际 %.1f Hz），DRV1_LVL=%ld，"
           "时长 %ld ms（0=用会话默认）",
           ch, f0, (double)aw_profile_f0(&c->profile), lvl, timeout);
}

static void cmd_f0(const char *a1)
{
    unsigned ch = 0;
    if (hexval(a1, &ch) != 0 || ch >= BOARD_CHANNEL_COUNT) {
        out_err(ERR_BAD_PARAM, "f0 <ch>");
        return;
    }
    uint8_t started = 0;
    esp_err_t err = svc_measure_f0_start((uint8_t)ch, 800, 0, &started);
    if (err != ESP_OK || !started) {
        out_err(ERR_BUSY, "F0 检测未能启动（通道忙或不在线）");
        return;
    }
    proto_port_write_text("OK F0 检测已启动，等待结果...\n");
    /* ASCII 模式下同步等待，方便裸板排查 */
    int64_t deadline = esp_timer_get_time() + 2000000;
    while (esp_timer_get_time() < deadline) {
        ch_ctx_t *c = ch_ctx((uint8_t)ch);
        if (!c->f0_pending) {
            if (c->f0_valid) {
                out_ok("ch=%u F0 = %.1f Hz（raw=0x%02X%02X）", ch,
                       c->f0_hz_x10 / 10.0, c->f0_raw_h, c->f0_raw_l);
            } else {
                out_err(ERR_INTERNAL,
                        "F0 检测失败：多为马达未接/接触不良，或该芯片输出球 A3(HDP)/C3(HDN) 虚焊。"
                        "可用上位机 DET_READ(seq=3) 读 RL 佐证");
            }
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    out_err(ERR_I2C_TIMEOUT, "F0 检测等待超时");
}

static void cmd_wave(const char *a1, const char *a2, const char *a3,
                     const char *a4, const char *a5)
{
    unsigned ch = 0, id = 0, freq = 0, amp = 0, cycles = 0;
    long v = 0;
    if (hexval(a1, &ch) != 0 || hexval(a2, &id) != 0 ||
        decval(a3, &v) != 0) {
        out_err(ERR_BAD_PARAM, "wave <ch> <id> <freq_hz> <amp> <cycles>");
        return;
    }
    freq = (unsigned)v;
    if (decval(a4, &v) != 0) { out_err(ERR_BAD_PARAM, "amp 必须是十进制"); return; }
    amp = (unsigned)v;
    if (decval(a5, &v) != 0) { out_err(ERR_BAD_PARAM, "cycles 必须是十进制"); return; }
    cycles = (unsigned)v;
    if (ch >= BOARD_CHANNEL_COUNT || id < 1 || id > 127 || freq == 0 ||
        amp > 127 || cycles == 0) {
        out_err(ERR_BAD_PARAM, "ch 0..7 / id 1..127 / freq>0 / amp 0..127 / cycles>0");
        return;
    }
    ch_ctx_t *c = ch_ctx((uint8_t)ch);
    if (!c->present) {
        out_err(ERR_NOT_PRESENT, "该通道无器件应答，请先 probe");
        return;
    }

    uint16_t generated = 0;
    esp_err_t err = svc_wave_quick_play((uint8_t)ch, (uint8_t)id, SHAPE_SINE,
                                        (uint16_t)freq, (uint8_t)amp,
                                        (uint16_t)cycles, &generated);
    if (err != ESP_OK) {
        out_err((err == ESP_ERR_INVALID_SIZE) ? ERR_SRAM_OVERFLOW : ERR_INTERNAL,
                "波形合成/下发失败（减小 cycles 或幅度）");
        return;
    }
    err = svc_play_start((uint8_t)ch, 0, false, NULL);
    if (err != ESP_OK) {
        out_err(ERR_INTERNAL, "播放启动失败");
        return;
    }
    out_ok("ch=%u 已生成 %u 个样本（%u Hz 正弦，幅度 %u，%u 周期）并开始播放",
           ch, generated, freq, amp, cycles);
    out("  如果完全不振：先查该通道芯片的输出球 A3(HDP)/C3(HDN) 焊接与该路座子焊点");
}

static void cmd_status(void)
{
    app_safety_cfg_t cfg;
    app_session_get_safety(&cfg);
    out_ok("uptime=%u ms safety_state=%u last_err=%u heartbeat_age=%u ms",
           (unsigned)app_session_uptime_ms(), (unsigned)app_session_safety_state(),
           (unsigned)app_session_last_err(),
           (unsigned)app_session_heartbeat_age_ms());
    out("  play_timeout=%u ms max_simul_full_amp=%u heartbeat_timeout=%u ms flags=0x%02X",
        cfg.play_timeout_ms, cfg.max_simul_full_amp, cfg.heartbeat_timeout_ms, cfg.flags);
    const bus_info_t *bi = bus_info();
    if (bi && bi->sel_used) {
        out("  I2C=%s SDA=IO%u SCL=IO%u %u Hz internal_pullup=%u",
            (bi->sel_used == 1) ? "LP" : "HP", bi->sda_gpio, bi->scl_gpio,
            (unsigned)bi->speed_hz, bi->internal_pullup);
    } else {
        out("  I2C 未探测（执行 bus auto）");
    }
    out("  在线通道掩码 = 0x%02X", ch_present_mask());
}

static void cmd_info(void)
{
    uint32_t caps;
    const bus_info_t *bi = bus_info();
    caps = CAP_RAM | CAP_RTP | CAP_CONT | CAP_TRIG | CAP_WAVE_DOWNLOAD |
           CAP_F0_DETECT | CAP_SWEEP | CAP_LOCAL_TRIG_PULSE | CAP_REG_ACCESS |
           CAP_ASCII_MODE;
    /* 2026-09 硬件更新：每路一条独立 TRIG → 不再上报联动位 */
#if BOARD_TRIG_SHARED_MASK != 0
    caps |= CAP_SHARED_TRIG_567;
#endif
    if (bi && bi->tca_present) {
        caps |= CAP_TCA_ONLINE;
    }
    if (bi && bi->internal_pullup) {
        caps |= CAP_INTERNAL_PULLUP;
    }
    out_ok("board=%u ch=%u max_payload=%u caps=0x%08X",
           (unsigned)BOARD_ID_VIBRATION_TEST_BOARD_A, (unsigned)BOARD_CHANNEL_COUNT,
           (unsigned)PROTO_MAX_PAYLOAD_LEN, (unsigned)caps);
    out("  RTP 48K 不可用（需 ≥1 MHz I2C）；马达电源与 RSTN 均不受 GPIO 控制");
}

/* ------------------------------------------------------------------ */

static void app_ascii_handle_line(char *line)
{
    /* 去掉首尾空白 */
    while (*line == ' ' || *line == '\t') {
        line++;
    }
    size_t n = strlen(line);
    while (n > 0 && (line[n - 1] == ' ' || line[n - 1] == '\t' || line[n - 1] == '\r')) {
        line[--n] = '\0';
    }
    if (n == 0) {
        return;
    }

    /* 切成最多 6 个 token */
    char *tok[6] = { 0 };
    int ntok = 0;
    char *p = line;
    while (*p && ntok < 6) {
        while (*p == ' ' || *p == '\t') {
            p++;
        }
        if (!*p) {
            break;
        }
        tok[ntok++] = p;
        while (*p && *p != ' ' && *p != '\t') {
            p++;
        }
        if (*p) {
            *p++ = '\0';
        }
    }
    if (ntok == 0) {
        return;
    }

    const char *c0 = tok[0];
    if (strcasecmp(c0, "help") == 0 || strcmp(c0, "?") == 0) {
        cmd_help();
    } else if (strcasecmp(c0, "ver") == 0) {
        out_ok("aw86234-testbench fw=%s proto=%u board=%u",
               FW_VERSION_STR, (unsigned)PROTO_VERSION,
               (unsigned)BOARD_ID_VIBRATION_TEST_BOARD_A);
    } else if (strcasecmp(c0, "info") == 0) {
        cmd_info();
    } else if (strcasecmp(c0, "status") == 0) {
        cmd_status();
    } else if (strcasecmp(c0, "mode") == 0) {
        if (ntok >= 2 && strcasecmp(tok[1], "bin") == 0) {
            proto_port_write_text("OK proto=binary\n");
            vTaskDelay(pdMS_TO_TICKS(20));
            app_session_set_ascii_mode(false);
            app_session_enable_heartbeat(false);
            s_line_len = 0;
            /* 二进制模式下禁止 ESP_LOG 直接写协议口（默认级别降到 NONE） */
            esp_log_level_set("*", ESP_LOG_NONE);
        } else if (ntok >= 2 && strcasecmp(tok[1], "ascii") == 0) {
            app_ascii_enter();
        } else {
            out_err(ERR_BAD_PARAM, "mode <bin|ascii>");
        }
    } else if (strcasecmp(c0, "log") == 0) {
        long lv = 0;
        if (ntok < 2 || decval(tok[1], &lv) != 0 || lv < 0 || lv > 4) {
            out_err(ERR_BAD_PARAM, "log <0..4>");
            return;
        }
        log_set_level((uint8_t)lv);
        out_ok("log level = %ld", lv);
    } else if (strcasecmp(c0, "scan") == 0) {
        cmd_scan();
    } else if (strcasecmp(c0, "bus") == 0) {
        uint8_t sel = BUS_SEL_AUTO;
        if (ntok >= 2) {
            if (strcasecmp(tok[1], "lp") == 0)      { sel = BUS_SEL_LP; }
            else if (strcasecmp(tok[1], "hp") == 0) { sel = BUS_SEL_HP; }
            else if (strcasecmp(tok[1], "auto") != 0) {
                out_err(ERR_BAD_PARAM, "bus <auto|lp|hp>");
                return;
            }
        }
        bus_info_t info;
        esp_err_t err = bus_probe_auto(sel, &info);
        if (err != ESP_OK) {
            out_err(ERR_TCA_NOT_FOUND,
                    "0x70 无应答：检查 J1 跳线（推荐 3-5 + 4-6）、TCA 供电、上游上拉");
            return;
        }
        tca_forget();
        out_ok("sel=%s sda=%u scl=%u speed=%u tca=%u ctrl=0x%02X pullup=%u",
               (info.sel_used == 1) ? "LP" : "HP", info.sda_gpio, info.scl_gpio,
               (unsigned)info.speed_hz, info.tca_present, info.ctrl_readback,
               info.internal_pullup);
        if (info.internal_pullup) {
            out("  警告：正在使用内部弱上拉降级运行，建议补焊 4.7 kΩ 到 3.3 V（硬件问题 V-1）");
        }
    } else if (strcasecmp(c0, "probe") == 0) {
        cmd_probe(ntok >= 2 ? tok[1] : NULL);
    } else if (strcasecmp(c0, "r") == 0) {
        cmd_read(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL,
                 ntok >= 4 ? tok[3] : NULL);
    } else if (strcasecmp(c0, "w") == 0) {
        cmd_write(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL,
                  ntok >= 4 ? tok[3] : NULL);
    } else if (strcasecmp(c0, "dump") == 0) {
        cmd_dump(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL,
                 ntok >= 4 ? tok[3] : NULL);
    } else if (strcasecmp(c0, "softrst") == 0) {
        unsigned ch = 0;
        if (ntok < 2 || hexval(tok[1], &ch) != 0 || ch >= BOARD_CHANNEL_COUNT) {
            out_err(ERR_BAD_PARAM, "softrst <ch>");
            return;
        }
        esp_err_t err = aw_softrst((uint8_t)ch);
        if (err == ESP_OK) {
            err = ch_reapply_profile((uint8_t)ch);
        }
        if (err != ESP_OK) {
            out_err(ERR_VERIFY_FAILED, "软复位后关键位回读失败");
            return;
        }
        out_ok("ch=%u 软复位完成，Profile 已重放（本板无硬件复位）", ch);
    } else if (strcasecmp(c0, "default") == 0) {
        unsigned ch = 0;
        if (ntok < 2 || hexval(tok[1], &ch) != 0 || ch >= BOARD_CHANNEL_COUNT) {
            out_err(ERR_BAD_PARAM, "default <ch>");
            return;
        }
        ch_ctx_t *c = ch_ctx((uint8_t)ch);
        aw_profile_defaults(&c->profile);
        esp_err_t err = aw_apply_register_defaults((uint8_t)ch);
        if (err == ESP_OK) {
            err = ch_reapply_profile((uint8_t)ch);
        }
        if (err != ESP_OK) {
            out_err(ERR_INTERNAL, "恢复默认失败");
            return;
        }
        out_ok("ch=%u 已恢复默认 Profile", ch);
    } else if (strcasecmp(c0, "playmode") == 0) {
        cmd_playmode(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL);
    } else if (strcasecmp(c0, "play") == 0) {
        cmd_play(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL);
    } else if (strcasecmp(c0, "stop") == 0) {
        cmd_stop(ntok >= 2 ? tok[1] : NULL);
    } else if (strcasecmp(c0, "park") == 0) {
        svc_play_trig_park();
        out_ok("所有 TRIG 输出已置低");
    } else if (strcasecmp(c0, "trig") == 0) {
        cmd_trig(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL,
                 ntok >= 4 ? tok[3] : NULL, ntok >= 5 ? tok[4] : NULL);
    } else if (strcasecmp(c0, "cont") == 0) {
        cmd_cont(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL,
                 ntok >= 4 ? tok[3] : NULL, ntok >= 5 ? tok[4] : NULL);
    } else if (strcasecmp(c0, "f0") == 0) {
        cmd_f0(ntok >= 2 ? tok[1] : NULL);
    } else if (strcasecmp(c0, "wave") == 0) {
        cmd_wave(ntok >= 2 ? tok[1] : NULL, ntok >= 3 ? tok[2] : NULL,
                 ntok >= 4 ? tok[3] : NULL, ntok >= 5 ? tok[4] : NULL,
                 ntok >= 6 ? tok[5] : NULL);
    } else if (strcasecmp(c0, "selftest") == 0) {
        if (svc_selftest_active()) {
            out_err(ERR_BUSY, "自检已在运行");
            return;
        }
        svc_selftest_start();
        out_ok("自检已启动，结果将通过日志与汇总上报");
    } else {
        out_err(ERR_UNKNOWN_CMD, "未知命令，输入 help 查看全部命令");
    }
}

void app_ascii_prompt_binary_hint(void)
{
    proto_port_write_text("检测到二进制帧，请先发送 MODE BIN\n");
}

void app_ascii_feed(const uint8_t *data, size_t len)
{
    /* 检测到二进制帧首字节后，本行剩余字节整帧丢弃。
     * ★ 必须这样：原实现只丢掉 0xAA 本身，帧里其余的 0x55/0x01/… 会当成 ASCII
     *   文本残留在行缓冲里，把紧随其后的 "MODE BIN" 拼成 "<乱码>MODE BIN"，
     *   固件回 ERR_UNKNOWN_CMD → **上位机的连接流程永远走不通**（实测踩到过）。 */
    static bool s_discard_binary = false;

    for (size_t i = 0; i < len; i++) {
        uint8_t b = data[i];

        if (b == 0xAA && s_line_len == 0 && !s_discard_binary) {
            s_discard_binary = true;
            s_line_len = 0;
            app_ascii_prompt_binary_hint();
            continue;
        }
        if (b == '\n') {
            s_discard_binary = false;       /* 本行结束，恢复正常解析 */
            if (s_line_len > 0) {
                s_line[s_line_len] = '\0';
                app_ascii_handle_line(s_line);
                s_line_len = 0;
            }
            continue;
        }
        if (b == '\r') {
            continue;
        }
        if (s_discard_binary) {
            continue;                       /* 丢弃二进制帧正文 */
        }
        if (s_line_len + 1 < sizeof(s_line)) {
            s_line[s_line_len++] = (char)b;
        } else {
            s_line_len = 0;
            out_err(ERR_BAD_LENGTH, "命令行过长（上限 255 字节）");
        }
    }
}
