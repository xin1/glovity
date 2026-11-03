/**
 * @file  app_protocol.c
 * @brief 协议层实现：帧解析、命令分派、应答与事件发送。
 *
 * 分层纪律：本文件负责"协议 ↔ 服务"的翻译，**不直接碰寄存器地址**
 * （寄存器动作都在 `svc_*`/`drv_*` 里）。
 */

#include "app_protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "svc_measure.h"
#include "svc_play.h"
#include "svc_selftest.h"
#include "svc_sweep.h"
#include "svc_wave.h"
#include "util_log.h"

#if BOARD_PROTO_USE_USB_JTAG
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#else
#include "driver/uart.h"
#endif

/* ================================================================== */
/* 串口抽象                                                            */
/* ================================================================== */

static bool s_use_driver = false;

#if BOARD_PROTO_USE_USB_JTAG
static void port_init(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        .tx_buffer_size = BOARD_PROTO_TX_BUF,
        .rx_buffer_size = BOARD_PROTO_RX_BUF,
    };
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err != ESP_OK) {
        /* 控制台可能已经占了 USB-Serial-JTAG：切到驱动模式再试一次 */
        usb_serial_jtag_vfs_use_driver();
        err = usb_serial_jtag_driver_install(&cfg);
    }
    s_use_driver = (err == ESP_OK);
    if (!s_use_driver) {
        ESP_LOGW("proto", "USB-Serial-JTAG 驱动安装失败(%s)，回落到控制台 VFS 读写",
                 esp_err_to_name(err));
    }
}
#else
static void port_init(void)
{
    uart_config_t uc = {
        .baud_rate = BOARD_PROTO_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    uart_driver_install(BOARD_PROTO_UART_NUM, BOARD_PROTO_RX_BUF, BOARD_PROTO_TX_BUF, 0, NULL, 0);
    uart_param_config(BOARD_PROTO_UART_NUM, &uc);
    uart_set_pin(BOARD_PROTO_UART_NUM, BOARD_PROTO_UART_TX, BOARD_PROTO_UART_RX,
                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    s_use_driver = true;
}
#endif

bool proto_port_use_driver(void)
{
    return s_use_driver;
}

void proto_port_write(const uint8_t *data, size_t len)
{
    if (len == 0) {
        return;
    }
#if BOARD_PROTO_USE_USB_JTAG
    if (s_use_driver) {
        size_t off = 0;
        while (off < len) {
            int n = usb_serial_jtag_write_bytes(&data[off], len - off, pdMS_TO_TICKS(100));
            if (n <= 0) {
                break;
            }
            off += (size_t)n;
        }
        return;
    }
    (void)fwrite(data, 1, len, stdout);
    (void)fflush(stdout);
#else
    uart_write_bytes(BOARD_PROTO_UART_NUM, (const char *)data, len);
#endif
}

int proto_port_read(uint8_t *data, size_t len, uint32_t ticks)
{
#if BOARD_PROTO_USE_USB_JTAG
    if (s_use_driver) {
        return usb_serial_jtag_read_bytes(data, (uint32_t)len, ticks);
    }
    size_t got = fread(data, 1, len, stdin);
    if (got == 0) {
        vTaskDelay(ticks ? ticks : 1);
    }
    return (int)got;
#else
    return uart_read_bytes(BOARD_PROTO_UART_NUM, data, len, ticks);
#endif
}

void proto_port_write_text(const char *text)
{
    if (text) {
        proto_port_write((const uint8_t *)text, strlen(text));
    }
}

/* ================================================================== */
/* 事件队列（多个任务投递，协议任务唯一发送）                          */
/* ================================================================== */

typedef struct {
    uint8_t  buf[PROTO_MAX_FRAME_LEN];
    uint16_t len;
} evt_item_t;

static evt_item_t s_evtq[EVENT_QUEUE_DEPTH];
static volatile uint32_t s_evt_head = 0;
static volatile uint32_t s_evt_tail = 0;
static SemaphoreHandle_t s_evt_mutex = NULL;
static uint32_t s_evt_dropped = 0;

void proto_post_event(uint8_t cmd, uint8_t chan, const uint8_t *payload, uint8_t len)
{
    proto_frame_t f = {
        .flags = PROTO_FLAG_EVENT,
        .seq = 0,
        .cmd = cmd,
        .chan = chan,
        .payload_len = len,
    };
    if (len > 0 && payload != NULL) {
        memcpy(f.payload, payload, len);
    }

    uint8_t raw[PROTO_MAX_FRAME_LEN];
    size_t n = proto_frame_pack(&f, raw, sizeof(raw));
    if (n == 0) {
        return;
    }
    if (s_evt_mutex == NULL || xSemaphoreTake(s_evt_mutex, 0) != pdTRUE) {
        s_evt_dropped++;
        return;
    }
    uint32_t next = (s_evt_head + 1u) % EVENT_QUEUE_DEPTH;
    if (next == s_evt_tail) {
        s_evt_dropped++;            /* 队列满：丢弃并计数 */
    } else {
        memcpy(s_evtq[s_evt_head].buf, raw, n);
        s_evtq[s_evt_head].len = (uint16_t)n;
        s_evt_head = next;
    }
    xSemaphoreGive(s_evt_mutex);
}

static bool evt_pop(uint8_t *out, uint16_t *out_len)
{
    if (s_evt_tail == s_evt_head) {
        return false;
    }
    memcpy(out, s_evtq[s_evt_tail].buf, s_evtq[s_evt_tail].len);
    *out_len = s_evtq[s_evt_tail].len;
    s_evt_tail = (s_evt_tail + 1u) % EVENT_QUEUE_DEPTH;
    return true;
}

/* ---- evt_* 便捷封装 ---- */

void evt_log_text(uint8_t level, const char *text)
{
    if (text == NULL) {
        return;
    }
    uint8_t buf[PROTO_MAX_PAYLOAD_LEN];
    size_t n = strlen(text);
    if (n > sizeof(buf) - 1u) {
        n = sizeof(buf) - 1u;
    }
    buf[0] = level;
    memcpy(&buf[1], text, n);
    proto_post_event(EVT_LOG, PROTO_CHAN_NONE, buf, (uint8_t)(n + 1u));
}

void evt_play_started(uint8_t ch, uint8_t mode, uint16_t timeout_ms)
{
    uint8_t p[3];
    p[0] = mode;
    proto_put_u16(&p[1], timeout_ms);
    proto_post_event(EVT_PLAY_STARTED, ch, p, sizeof(p));
}

void evt_play_done(uint8_t ch, uint8_t reason, uint16_t elapsed_ms)
{
    uint8_t p[3];
    p[0] = reason;
    proto_put_u16(&p[1], elapsed_ms);
    proto_post_event(EVT_PLAY_DONE, ch, p, sizeof(p));
}

void evt_measure_result(uint8_t ch, uint8_t kind, uint8_t valid, uint32_t value,
                        uint8_t raw_h, uint8_t raw_l)
{
    uint8_t p[8];
    p[0] = kind;
    p[1] = valid;
    proto_put_u32(&p[2], value);
    p[6] = raw_h;
    p[7] = raw_l;
    proto_post_event(EVT_MEASURE_RESULT, ch, p, sizeof(p));
}

void evt_sweep_progress(uint8_t step, uint8_t total, uint16_t value)
{
    uint8_t p[4] = { step, total, 0, 0 };
    proto_put_u16(&p[2], value);
    proto_post_event(EVT_SWEEP_PROGRESS, PROTO_CHAN_NONE, p, sizeof(p));
}

void evt_sweep_done(uint8_t steps, uint8_t status)
{
    uint8_t p[2] = { steps, status };
    proto_post_event(EVT_SWEEP_DONE, PROTO_CHAN_NONE, p, sizeof(p));
}

void evt_error(uint8_t errcode, uint8_t cmd, const char *text)
{
    uint8_t buf[PROTO_MAX_PAYLOAD_LEN];
    size_t n = text ? strlen(text) : 0;
    if (n > sizeof(buf) - 2u) {
        n = sizeof(buf) - 2u;
    }
    buf[0] = errcode;
    buf[1] = cmd;
    if (n) {
        memcpy(&buf[2], text, n);
    }
    proto_post_event(EVT_ERROR, PROTO_CHAN_NONE, buf, (uint8_t)(n + 2u));
}

void evt_safety_trip(uint8_t reason, uint8_t affected_mask, uint8_t detail)
{
    uint8_t p[3] = { reason, affected_mask, detail };
    proto_post_event(EVT_SAFETY_TRIP, PROTO_CHAN_NONE, p, sizeof(p));
}

void evt_test_progress(uint16_t test_id, uint8_t step, uint8_t total, uint8_t status,
                       const char *text)
{
    uint8_t buf[PROTO_MAX_PAYLOAD_LEN];
    size_t n = text ? strlen(text) : 0;
    if (n > sizeof(buf) - 5u) {
        n = sizeof(buf) - 5u;
    }
    proto_put_u16(buf, test_id);
    buf[2] = step;
    buf[3] = total;
    buf[4] = status;
    if (n) {
        memcpy(&buf[5], text, n);
    }
    proto_post_event(EVT_TEST_PROGRESS, PROTO_CHAN_NONE, buf, (uint8_t)(n + 5u));
}

void evt_selftest_result(uint8_t overall, uint8_t present_mask, uint8_t bus_sel,
                         uint8_t tca, uint8_t vreg_mask, uint8_t issue_count,
                         const char *text)
{
    uint8_t buf[PROTO_MAX_PAYLOAD_LEN];
    size_t n = text ? strlen(text) : 0;
    if (n > sizeof(buf) - 6u) {
        n = sizeof(buf) - 6u;
    }
    buf[0] = overall;
    buf[1] = present_mask;
    buf[2] = bus_sel;
    buf[3] = tca;
    buf[4] = vreg_mask;
    buf[5] = issue_count;
    if (n) {
        memcpy(&buf[6], text, n);
    }
    proto_post_event(EVT_SELFTEST_RESULT, PROTO_CHAN_NONE, buf, (uint8_t)(n + 6u));
}

void evt_bus_recovered(uint8_t method)
{
    proto_post_event(EVT_BUS_RECOVERED, PROTO_CHAN_NONE, &method, 1);
}

/* ================================================================== */
/* 应答发送                                                            */
/* ================================================================== */

static void send_frame_now(uint8_t flags, uint8_t seq, uint8_t cmd, uint8_t chan,
                           const uint8_t *payload, uint8_t len)
{
    proto_frame_t f = {
        .flags = flags,
        .seq = seq,
        .cmd = cmd,
        .chan = chan,
        .payload_len = len,
    };
    if (len > 0 && payload != NULL) {
        memcpy(f.payload, payload, len);
    }
    uint8_t raw[PROTO_MAX_FRAME_LEN];
    size_t n = proto_frame_pack(&f, raw, sizeof(raw));
    if (n == 0) {
        return;
    }
    /* ★ 一次写出整帧，不要分多次写（避免被其它任务的输出插进来） */
    proto_port_write(raw, n);
}

static void reply(const proto_frame_t *req, const uint8_t *payload, uint8_t len)
{
    if (proto_is_noresp(req)) {
        return;         /* NORESP 请求不得回应答 */
    }
    send_frame_now(PROTO_FLAG_RESPONSE, req->seq, req->cmd, req->chan, payload, len);
}

static void reply_ok(const proto_frame_t *req)
{
    reply(req, NULL, 0);
}

static void reply_err(const proto_frame_t *req, uint8_t errcode, const char *text)
{
    app_session_note_error(errcode);
    if (proto_is_noresp(req)) {
        /* NORESP 出错：用 EVT_ERROR 上报（协议 §12） */
        evt_error(errcode, req->cmd, text);
        return;
    }
    uint8_t buf[PROTO_MAX_PAYLOAD_LEN];
    size_t n = text ? strlen(text) : 0;
    if (n > sizeof(buf) - 1u) {
        n = sizeof(buf) - 1u;
    }
    buf[0] = errcode;
    if (n) {
        memcpy(&buf[1], text, n);
    }
    send_frame_now((uint8_t)(PROTO_FLAG_RESPONSE | PROTO_FLAG_ERROR), req->seq,
                   req->cmd, req->chan, buf, (uint8_t)(n + 1u));
}

/** 把 esp_err_t 映射成协议错误码。 */
static uint8_t err_to_proto(esp_err_t err)
{
    switch (err) {
    case ESP_OK:                return ERR_OK;
    case ESP_ERR_INVALID_ARG:   return ERR_BAD_PARAM;
    case ESP_ERR_INVALID_STATE: return ERR_BUSY;
    case ESP_ERR_TIMEOUT:       return ERR_I2C_TIMEOUT;
    case ESP_ERR_NOT_FOUND:     return ERR_NOT_PRESENT;
    case ESP_ERR_NOT_SUPPORTED: return ERR_UNSUPPORTED;
    case ESP_ERR_INVALID_SIZE:  return ERR_SRAM_OVERFLOW;
    case ESP_ERR_INVALID_CRC:   return ERR_CRC_MISMATCH;
    default:                    return ERR_INTERNAL;
    }
}

/* ================================================================== */
/* 能力位                                                              */
/* ================================================================== */

static uint32_t build_caps(void)
{
    const bus_info_t *bi = bus_info();
    uint32_t caps = CAP_RAM | CAP_RTP | CAP_CONT | CAP_TRIG | CAP_WAVE_DOWNLOAD |
                    CAP_F0_DETECT | CAP_SWEEP | CAP_LOCAL_TRIG_PULSE |
                    CAP_REG_ACCESS | CAP_ASCII_MODE | CAP_TIMED_PLAY | CAP_BOOT_TRIGGER | CAP_LIVE_LAB | CAP_TACTILE;
    /* 2026-09 硬件更新：每路一条独立 TRIG（BOARD_TRIG_SHARED_MASK == 0）
     * → 不再上报"CH5/6/7 共用 TRIG"能力位，上位机据此停用联动加锁。 */
#if BOARD_TRIG_SHARED_MASK != 0
    caps |= CAP_SHARED_TRIG_567;
#endif
    if (bi && bi->tca_present) {
        caps |= CAP_TCA_ONLINE;
    }
    if (bi && bi->internal_pullup) {
        caps |= CAP_INTERNAL_PULLUP;
    }
    /* CAP_MOTOR_POWER_GPIO / CAP_RSTN_GPIO / CAP_RTP_48K 本板恒为 0 */
    return caps;
}

/* ================================================================== */
/* Profile 回读（下发 → 回读 → 比对 的关键）                           */
/* ================================================================== */

static bool read_back_profile(uint8_t ch, aw_profile_t *out)
{
    uint8_t v = 0;
    aw_profile_defaults(out);

#define RD(reg, field)                                            \
    do {                                                          \
        if (aw_read_u8(ch, (reg), &v) != ESP_OK) { return false; } \
        (out)->field = v;                                         \
    } while (0)

    RD(REG_PLAYCFG2, gain);
    if (aw_read_u8(ch, REG_PLAYCFG3, &v) != ESP_OK) { return false; }
    out->mode = (uint8_t)(v & 0x03u);
    out->brk_en = (uint8_t)((v >> 2) & 1u);
    out->stop_mode = (uint8_t)((v >> 3) & 1u);

    if (aw_read(ch, REG_WAVCFG1, out->wavseq, 8) != ESP_OK) { return false; }
    if (aw_read(ch, REG_WAVCFG9, out->seq_loop, 4) != ESP_OK) { return false; }
    if (aw_read_u8(ch, REG_WAVCFG13, &v) != ESP_OK) { return false; }
    out->mainloop = (uint8_t)(v & 0x0Fu);
    out->waitslot = (uint8_t)((v >> 4) & 0x03u);

    if (aw_read_u8(ch, REG_CONTCFG1, &v) != ESP_OK) { return false; }
    out->en_f0_det = (uint8_t)((v >> 5) & 1u);
    out->sin_mode = (uint8_t)((v >> 4) & 1u);
    out->edge_fre = (uint8_t)(v & 0x0Fu);
    RD(0x18, f_pre);
    RD(0x19, drv_width);
    RD(0x1A, wait_num);
    if (aw_read_u8(ch, 0x1B, &v) != ESP_OK) { return false; }
    out->brk_gain = (uint8_t)(v & 0x0Fu);
    if (aw_read_u8(ch, 0x1C, &v) != ESP_OK) { return false; }
    out->track_en = (uint8_t)((v >> 7) & 1u);
    out->drv1_lvl = (uint8_t)(v & 0x7Fu);
    if (aw_read_u8(ch, 0x1D, &v) != ESP_OK) { return false; }
    out->drv2_lvl = (uint8_t)(v & 0x7Fu);
    RD(0x1E, drv1_time);
    RD(0x1F, drv2_time);
    RD(0x20, brk_time);
    RD(0x21, track_margin);

    if (aw_read_u8(ch, REG_BASE_ADDR_H, &v) != ESP_OK) { return false; }
    out->base_addr = (uint16_t)((uint16_t)(v & 0x0Fu) << 8);
    if (aw_read_u8(ch, REG_BASE_ADDR_L, &v) != ESP_OK) { return false; }
    out->base_addr |= v;
    if (aw_read_u8(ch, 0x2E, &v) != ESP_OK) { return false; }
    out->fifo_ae = (uint16_t)((uint16_t)((v >> 4) & 0x0Fu) << 8);
    out->fifo_af = (uint16_t)((uint16_t)(v & 0x0Fu) << 8);
    if (aw_read_u8(ch, 0x2F, &v) != ESP_OK) { return false; }
    out->fifo_ae |= v;
    if (aw_read_u8(ch, 0x30, &v) != ESP_OK) { return false; }
    out->fifo_af |= v;
    if (aw_read_u8(ch, 0x31, &v) != ESP_OK) { return false; }
    out->rtp_flags = (uint8_t)(((v >> 2) & 1u) | (((v >> 3) & 1u) << 1));
    if (aw_read_u8(ch, REG_SYSCTRL2, &v) != ESP_OK) { return false; }
    out->wavdat_mode = (uint8_t)(v & 0x03u);

    if (aw_read_u8(ch, REG_TRGCFG1, &v) != ESP_OK) { return false; }
    out->trig1_pos_en = (uint8_t)((v >> 7) & 1u);
    out->trig1_seq_p = (uint8_t)(v & 0x7Fu);
    if (aw_read_u8(ch, REG_TRGCFG4, &v) != ESP_OK) { return false; }
    out->trig1_neg_en = (uint8_t)((v >> 7) & 1u);
    out->trig1_seq_n = (uint8_t)(v & 0x7Fu);
    if (aw_read_u8(ch, REG_TRGCFG7, &v) != ESP_OK) { return false; }
    out->trig1_polar = (uint8_t)((v >> 5) & 1u);
    out->trig1_lev = (uint8_t)((v >> 4) & 1u);
    out->trig1_brk = (uint8_t)((v >> 3) & 1u);
    if (aw_read_u8(ch, REG_TRGCFG8, &v) != ESP_OK) { return false; }
    out->trig1_stop = (uint8_t)((v >> 2) & 1u);
    RD(REG_GLBCFG2, start_dly);
    if (aw_read_u8(ch, REG_GLBCFG4, &v) != ESP_OK) { return false; }
    out->trig1_prio = (uint8_t)(v & 0x03u);
    RD(REG_TRGCFG9, trg_gain);
    if (aw_read_u8(ch, REG_SYSCTRL1, &v) != ESP_OK) { return false; }
    out->trig1_mode = (uint8_t)((v >> SHIFT_TRG1_MODE) & 0x07u);
    out->intn_pin = (uint8_t)(v & 1u);
    out->en_fir = (uint8_t)((v >> 1) & 1u);
    if (aw_read_u8(ch, REG_VBATCTRL, &v) != ESP_OK) { return false; }
    out->vbat_mode = (uint8_t)((v >> 6) & 1u);
    if (aw_read_u8(ch, REG_DETCFG1, &v) != ESP_OK) { return false; }
    out->vbat_ref = (uint8_t)((v >> 4) & 0x07u);
#undef RD
    out->profile_ver = AW_PROFILE_VER;
    out->reserved1 = 0;
    return true;
}

/* ================================================================== */
/* 命令分派                                                            */
/* ================================================================== */

static bool chan_ok(const proto_frame_t *req)
{
    return req->chan < BOARD_CHANNEL_COUNT;
}

static void handle_system(const proto_frame_t *req)
{
    uint8_t p[64];
    switch (req->cmd) {
    case CMD_PING: {
        p[0] = PROTO_VERSION;
        p[1] = FW_VERSION_MAJOR;
        p[2] = FW_VERSION_MINOR;
        p[3] = FW_VERSION_PATCH;
        p[4] = BOARD_ID_VIBRATION_TEST_BOARD_A;
        proto_put_u32(&p[5], app_session_uptime_ms());
        reply(req, p, 9);
        break;
    }
    case CMD_GET_CAPS: {
        uint32_t caps = build_caps();
        p[0] = BOARD_CHANNEL_COUNT;
        p[1] = PROTO_MAX_PAYLOAD_LEN;
        proto_put_u32(&p[2], caps);
        reply(req, p, 6);
        break;
    }
    case CMD_GET_STATUS: {
        proto_put_u32(&p[0], app_session_uptime_ms());
        p[4] = app_session_safety_state();
        /* Live/tactile sessions use svc_play state, not the legacy active mask.
         * Include pending failed STOPs so status cannot incorrectly say idle. */
        p[5] = svc_measure_active_mask();
        for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ++ch) {
            if (svc_play_active(ch)) { p[5] |= (uint8_t)(1u << ch); }
        }
        p[6] = app_session_last_err();
        uint32_t age = app_session_heartbeat_age_ms();
        proto_put_u16(&p[7], (uint16_t)(age > 0xFFFFu ? 0xFFFFu : age));
        reply(req, p, 9);
        break;
    }
    case CMD_MCU_RESET: {
        if (req->payload_len < 2) { reply_err(req, ERR_BAD_LENGTH, "需要 delay_ms(2)"); break; }
        uint16_t delay = proto_get_u16(req->payload);
        if (delay > 2000) { reply_err(req, ERR_BAD_PARAM, "delay_ms 上限 2000"); break; }
        reply_ok(req);
        svc_play_stop_all(STOP_MODE_IMMEDIATE, NULL);
        svc_play_trig_park();
        vTaskDelay(pdMS_TO_TICKS(delay));
        esp_restart();
        break;
    }
    case CMD_SET_LOG_LEVEL: {
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 level(1)"); break; }
        if (req->payload[0] > LOG_DEBUG) { reply_err(req, ERR_BAD_PARAM, "level 0..4"); break; }
        log_set_level(req->payload[0]);
        reply_ok(req);
        break;
    }
    case CMD_SET_SAFETY: {
        if (req->payload_len < 6) { reply_err(req, ERR_BAD_LENGTH, "需要 6 字节"); break; }
        app_safety_cfg_t cfg = {
            .play_timeout_ms = proto_get_u16(&req->payload[0]),
            .max_simul_full_amp = req->payload[2],
            .heartbeat_timeout_ms = proto_get_u16(&req->payload[3]),
            .flags = req->payload[5],
        };
        if (app_session_set_safety(&cfg) != ESP_OK) {
            reply_err(req, ERR_BAD_PARAM, "安全参数越界（超时 50..60000 / 上限 1..8 / 心跳 200..60000）");
            break;
        }
        reply_ok(req);
        break;
    }
    case CMD_GET_SAFETY: {
        app_safety_cfg_t cfg;
        app_session_get_safety(&cfg);
        proto_put_u16(&p[0], cfg.play_timeout_ms);
        p[2] = cfg.max_simul_full_amp;
        proto_put_u16(&p[3], cfg.heartbeat_timeout_ms);
        p[5] = cfg.flags;
        reply(req, p, 6);
        break;
    }
    case CMD_CLEAR_ERRORS: {
        app_session_clear_errors();
        reply_ok(req);
        break;
    }
    case CMD_SET_PROTO_MODE: {
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 mode(1)"); break; }
        uint8_t mode = req->payload[0];
        reply_ok(req);
        if (mode == 1) {
            vTaskDelay(pdMS_TO_TICKS(20));   /* 先把应答发出去 */
            app_ascii_enter();
        }
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_bus(const proto_frame_t *req)
{
    if (req->cmd == CMD_BUS_SET_SPEED || req->cmd == CMD_BUS_SET_PULLUP ||
        req->cmd == CMD_BUS_RECOVER || req->cmd == CMD_TCA_RESET) {
        if (svc_play_live_mask() && svc_play_prepare_config(0xFFu) != ESP_OK) {
            reply_err(req, ERR_BUSY, "live stop pending before bus configuration"); return;
        }
    }

    uint8_t p[64];
    switch (req->cmd) {
    case CMD_BUS_PROBE: {
        uint8_t sel = (req->payload_len >= 1) ? req->payload[0] : BUS_SEL_AUTO;
        if (sel > BUS_SEL_HP) {
            reply_err(req, ERR_BAD_PARAM, "bus_sel 只接受 0/1/2");
            break;
        }
        bus_info_t info;
        esp_err_t err = bus_probe_auto(sel, &info);
        if (err != ESP_OK) {
            reply_err(req, ERR_TCA_NOT_FOUND,
                      "0x70 无应答：检查 J1 跳线（推荐 3-5+4-6）、TCA 供电、上游上拉");
            break;
        }
        tca_forget();
        p[0] = info.sel_used;
        p[1] = info.sda_gpio;
        p[2] = info.scl_gpio;
        proto_put_u32(&p[3], info.speed_hz);
        p[7] = info.internal_pullup;
        p[8] = info.tca_present;
        p[9] = info.tca_addr;
        p[10] = info.ctrl_readback;
        p[11] = info.probe_speed_ok;
        reply(req, p, 12);
        break;
    }
    case CMD_BUS_SET_SPEED: {
        if (req->payload_len < 4) { reply_err(req, ERR_BAD_LENGTH, "需要 speed_hz(4)"); break; }
        uint32_t hz = proto_get_u32(req->payload);
        uint32_t actual = 0;
        esp_err_t err = bus_set_speed(hz, &actual);
        if (err == ESP_ERR_INVALID_ARG) {
            reply_err(req, ERR_BAD_PARAM, "只接受 100000 或 400000");
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "切换速率失败"); break; }
        proto_put_u32(p, actual);
        reply(req, p, 4);
        break;
    }
    case CMD_BUS_SET_PULLUP: {
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 enable(1)"); break; }
        esp_err_t err = bus_set_pullup(req->payload[0] != 0);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "内部上拉开关失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_BUS_RECOVER: {
        uint8_t method = bus_recover();
        if (method != 0) {
            tca_forget();       /* 恢复后必须把"当前选通通道"置为未选通 */
            svc_play_trig_park();
            evt_bus_recovered(method);
        }
        p[0] = method;
        reply(req, p, 1);
        break;
    }
    case CMD_TCA_SET_MASK: {
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 mask(1)"); break; }
        uint8_t mask = req->payload[0];
        /* ★ 硬校验：只允许 0x00 或恰好一个 bit（防止两颗 0x5A 同时应答） */
        if (!tca_mask_is_valid(mask)) {
            reply_err(req, ERR_BAD_PARAM, "mask 只允许 0x00 或恰好一个 bit");
            break;
        }
        esp_err_t err = ch_lock();
        if (err != ESP_OK) { reply_err(req, ERR_BUS_BUSY, "总线忙"); break; }
        err = tca_write_mask(mask);
        ch_unlock();
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "写 TCA 失败"); break; }
        p[0] = mask;
        reply(req, p, 1);
        break;
    }
    case CMD_TCA_GET_MASK: {
        uint8_t mask = 0;
        esp_err_t err = ch_lock();
        if (err == ESP_OK) {
            err = tca_read_ctrl(&mask);
            ch_unlock();
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "读 TCA 失败"); break; }
        p[0] = mask;
        reply(req, p, 1);
        break;
    }
    case CMD_TCA_RESET: {
        uint16_t hold = (req->payload_len >= 2) ? proto_get_u16(req->payload)
                                                : BOARD_TCA_RST_HOLD_US;
        if (hold < BOARD_TCA_RST_HOLD_US || hold > 10000) {
            reply_err(req, ERR_BAD_PARAM, "hold_us 范围 10..10000");
            break;
        }
        bus_tca_reset_pulse(hold);
        tca_forget();
        reply_ok(req);
        break;
    }
    case CMD_TCA_READ_CTRL: {
        uint8_t ctrl = 0;
        esp_err_t err = ch_lock();
        if (err == ESP_OK) {
            err = tca_read_ctrl(&ctrl);
            ch_unlock();
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "读 TCA 控制寄存器失败"); break; }
        p[0] = ctrl;
        reply(req, p, 1);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_channel(const proto_frame_t *req)
{
    uint8_t p[160];
    switch (req->cmd) {
    case CMD_CH_PROBE: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        uint8_t ch = req->chan;
        ch_ctx_t *c = ch_ctx(ch);
        uint16_t id = 0;
        uint8_t vreg = 0xFF;
        esp_err_t err = aw_read_chipid(ch, &id);
        if (err != ESP_OK) {
            c->present = 0;
            p[0] = 0; p[1] = 0; p[2] = 0; p[3] = 0;
            reply(req, p, 4);
            break;
        }
        (void)aw_read_vreg_ok(ch, &vreg);
        c->present = 1;
        c->chipid = id;
        c->vreg_ok = vreg;
        if (c->state == CH_ABSENT) {
            c->state = CH_IDLE;
        }
        if (id != BOARD_AW_CHIPID_EXPECTED) {
            app_session_note_error(ERR_CHIPID_MISMATCH);
        }
        p[0] = 1;
        p[1] = (uint8_t)(id >> 8);
        p[2] = (uint8_t)(id & 0xFFu);
        p[3] = vreg;
        reply(req, p, 4);
        break;
    }
    case CMD_CH_PROBE_ALL: {
        uint8_t present_mask = 0;
        uint8_t count = 0;
        uint8_t off = 2;
        for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
            ch_ctx_t *c = ch_ctx(ch);
            uint16_t id = 0;
            uint8_t status = 0;
            esp_err_t err = aw_read_chipid(ch, &id);
            if (err == ESP_OK) {
                c->present = 1;
                c->chipid = id;
                if (c->state == CH_ABSENT) {
                    c->state = CH_IDLE;
                }
                present_mask |= (uint8_t)(1u << ch);
            } else {
                c->present = 0;
                c->state = CH_ABSENT;
                status = err_to_proto(err);
                id = 0;
            }
            p[off++] = ch;
            p[off++] = (uint8_t)(id >> 8);
            p[off++] = (uint8_t)(id & 0xFFu);
            p[off++] = status;
            count++;
        }
        p[0] = present_mask;
        p[1] = count;
        reply(req, p, off);
        break;
    }
    case CMD_CH_GET_INFO: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        ch_ctx_t *c = ch_ctx(req->chan);
        p[0] = c->present;
        p[1] = (uint8_t)(c->chipid >> 8);
        p[2] = (uint8_t)(c->chipid & 0xFFu);
        p[3] = c->mode;
        p[4] = c->playing ? 1u : 0u;
        p[5] = c->glb_state;
        p[6] = c->last_err;
        reply(req, p, 7);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_register_locked(const proto_frame_t *req)
{
    uint8_t p[200];
    switch (req->cmd) {
    case CMD_REG_READ: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 2) { reply_err(req, ERR_BAD_LENGTH, "需要 addr+len"); break; }
        uint8_t addr = req->payload[0];
        uint8_t len = req->payload[1];
        if (len < 1 || len > BOARD_REG_MAX_LEN) {
            reply_err(req, ERR_BAD_PARAM, "len 范围 1..64");
            break;
        }
        esp_err_t err = aw_read(req->chan, addr, p, len);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "寄存器读失败"); break; }
        reply(req, p, len);
        break;
    }
    case CMD_REG_WRITE: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 2 || req->payload_len > BOARD_REG_MAX_LEN + 1) {
            reply_err(req, ERR_BAD_LENGTH, "需要 addr + 1..64 字节数据");
            break;
        }
        esp_err_t err = aw_write(req->chan, req->payload[0], &req->payload[1],
                                 req->payload_len - 1);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "寄存器写失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_REG_UPDATE: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 3) { reply_err(req, ERR_BAD_LENGTH, "需要 addr+mask+value"); break; }
        uint8_t oldv = 0, newv = 0;
        esp_err_t err = aw_update(req->chan, req->payload[0], req->payload[1],
                                  req->payload[2], &oldv, &newv);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "读-改-写失败"); break; }
        p[0] = oldv;
        p[1] = newv;
        reply(req, p, 2);
        break;
    }
    case CMD_REG_READ_MULTI: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 n"); break; }
        uint8_t n = req->payload[0];
        if (n < 1 || n > 16 || req->payload_len < (size_t)(1 + 2 * n)) {
            reply_err(req, ERR_BAD_LENGTH, "n 范围 1..16 且需要 n×(addr,len)");
            break;
        }
        uint16_t total = 0;
        uint8_t off = 0;
        for (uint8_t i = 0; i < n; i++) {
            uint8_t addr = req->payload[1 + 2 * i];
            uint8_t len = req->payload[2 + 2 * i];
            if (len < 1 || len > BOARD_REG_MAX_LEN || (uint16_t)(total + len) > 128) {
                reply_err(req, ERR_BAD_PARAM, "单项 len 1..64 且 Σlen ≤128");
                off = 0xFF;
                break;
            }
            esp_err_t err = aw_read(req->chan, addr, &p[total], len);
            if (err != ESP_OK) {
                reply_err(req, err_to_proto(err), "寄存器读失败");
                off = 0xFF;
                break;
            }
            total = (uint16_t)(total + len);
            off = (uint8_t)total;
        }
        if (off == 0xFF) {
            break;
        }
        reply(req, p, (uint8_t)total);
        break;
    }
    case CMD_REG_DUMP: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 2) { reply_err(req, ERR_BAD_LENGTH, "需要 start+len"); break; }
        uint8_t start = req->payload[0];
        uint8_t len = req->payload[1];
        if (len < 1 || len > BOARD_REG_MAX_LEN) {
            reply_err(req, ERR_BAD_PARAM, "len 范围 1..64");
            break;
        }
        esp_err_t err = aw_read(req->chan, start, &p[1], len);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "整片 dump 失败"); break; }
        p[0] = start;
        reply(req, p, (uint8_t)(len + 1));
        break;
    }
    case CMD_STREAM_WRITE: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 5) { reply_err(req, ERR_BAD_LENGTH, "需要 addr+offset+len+数据"); break; }
        uint8_t addr = req->payload[0];
        uint8_t len = req->payload[3];
        if (len < 1 || len > BOARD_REG_MAX_LEN || req->payload_len < (size_t)(4 + len)) {
            reply_err(req, ERR_BAD_LENGTH, "len 1..64 且载荷长度匹配");
            break;
        }
        esp_err_t err = ESP_OK;
        for (uint8_t i = 0; i < len; i++) {
            err = aw_write_u8(req->chan, addr, req->payload[4 + i]);
            if (err != ESP_OK) {
                break;
            }
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "连续写失败"); break; }
        reply_ok(req);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_register(const proto_frame_t *req)
{
    svc_play_lock();
    /* Configuration changes revoke physical-button authorization. */
    bool mutation = req->cmd == CMD_REG_WRITE || req->cmd == CMD_REG_UPDATE || req->cmd == CMD_STREAM_WRITE;
    if (mutation) {
        uint8_t mask = req->chan < 8 ? (uint8_t)(1u << req->chan) : 0xFFu;
        svc_play_boot_invalidate(mask);
        if ((svc_play_live_mask() & mask) && svc_play_prepare_config(mask) != ESP_OK) {
            reply_err(req, ERR_BUSY, "live stop pending"); svc_play_unlock(); return;
        }
    }
    handle_register_locked(req);
    svc_play_unlock();
}

static void handle_device_locked(const proto_frame_t *req)
{
    uint8_t p[16];
    if (!chan_ok(req)) {
        reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7");
        return;
    }
    uint8_t ch = req->chan;
    ch_ctx_t *c = ch_ctx(ch);
    switch (req->cmd) {
    case CMD_AW_SOFTRST: {
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_softrst(ch);
        if (err == ESP_OK) {
            /* ★ 软复位清掉全部配置寄存器，必须重新应用一遍 Profile */
            err = ch_reapply_profile(ch);
        }
        if (err != ESP_OK) {
            reply_err(req, (err == ESP_ERR_INVALID_STATE) ? ERR_VERIFY_FAILED
                                                          : err_to_proto(err),
                      "软复位后关键位回读失败（本板无硬件复位，软复位是唯一手段）");
            break;
        }
        ch_mark_play_stopped(ch);
        reply_ok(req);
        break;
    }
    case CMD_AW_WAKE: {
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_wake(ch);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "WAKE 失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_AW_STANDBY: {
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_standby(ch);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "STANDBY 失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_AW_INIT_DEFAULT: {
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        uint8_t ok = 0, total = 6;
        esp_err_t err = aw_softrst(ch);
        if (err == ESP_OK) { ok++; }
        if (err == ESP_OK && aw_wake(ch) == ESP_OK) { ok++; }
        if (err == ESP_OK && aw_apply_register_defaults(ch) == ESP_OK) { ok++; }
        if (err == ESP_OK && aw_verify_trap_bits(ch, AW_MODE_RAM) == ESP_OK) { ok++; }
        if (err == ESP_OK) {
            uint16_t id = 0;
            if (aw_read_chipid(ch, &id) == ESP_OK && id == BOARD_AW_CHIPID_EXPECTED) { ok++; }
            esp_err_t e2 = ch_reapply_profile(ch);
            if (e2 == ESP_OK) { ok++; }
        }
        p[0] = ok;
        p[1] = total;
        reply(req, p, 2);
        break;
    }
    case CMD_AW_GET_STATE: {
        uint8_t glb = 0, sysst = 0, sysst2 = 0;
        uint8_t ok = 0;
        esp_err_t err = ESP_OK;
        if (c->present) {
            err = aw_get_glb_state(ch, &glb);
            if (err == ESP_OK) { err = aw_get_sysst(ch, &sysst); }
            if (err == ESP_OK) { err = aw_get_sysst2(ch, &sysst2); }
            if (err == ESP_OK) {
                ok = 1;
                c->glb_state = glb;
            }
        }
        p[0] = glb;
        p[1] = sysst;
        p[2] = sysst2;
        /* play_mode：0=RAM 1=RTP 2=CONT 3=不播放 */
        p[3] = (glb == GLB_STANDBY) ? 3u : (uint8_t)c->mode;
        p[4] = ok;
        reply(req, p, 5);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_device(const proto_frame_t *req)
{
    svc_play_lock();
    /* Configuration changes revoke physical-button authorization. */
    bool mutation = req->cmd != CMD_AW_GET_STATE;
    if (mutation) {
        uint8_t mask = req->chan < 8 ? (uint8_t)(1u << req->chan) : 0xFFu;
        svc_play_boot_invalidate(mask);
        if ((svc_play_live_mask() & mask) && svc_play_prepare_config(mask) != ESP_OK) {
            reply_err(req, ERR_BUSY, "live stop pending"); svc_play_unlock(); return;
        }
    }
    if (mutation) {
        uint8_t mask = req->chan < 8 ? (uint8_t)(1u << req->chan) : 0xFFu;
        if (svc_play_prepare_config(mask) != ESP_OK) {
            reply_err(req, ERR_BUSY, "停止尚未完成，拒绝重配");
            svc_play_unlock();
            return;
        }
    }
    handle_device_locked(req);
    svc_play_unlock();
}

static void handle_play(const proto_frame_t *req)
{
    uint8_t p[16];
    switch (req->cmd) {
    case CMD_SET_MODE: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 mode(1)"); break; }
        uint8_t mode = req->payload[0];
        if (mode > 3) { reply_err(req, ERR_BAD_PARAM, "mode 0=RAM 1=RTP 2=CONT 3=TRIG"); break; }
        if (mode == MODE_RTP && ch_ctx(req->chan)->profile.wavdat_mode == RATE_48K) {
            reply_err(req, ERR_UNSUPPORTED, "RTP 48K 需 ≥1 MHz I2C，本板只到 400 kHz");
            break;
        }
        esp_err_t err = svc_play_set_mode(req->chan, mode);
        if (err != ESP_OK) {
            uint8_t code = (err == ESP_ERR_INVALID_STATE) ? ERR_VERIFY_FAILED
                                                          : err_to_proto(err);
            const char *txt = (err == ESP_ERR_INVALID_STATE)
                ? "进入 TRIG 模式前 0x3A bit2/0x33 bit7 回读校验失败，已拒绝"
                : "切换播放模式失败";
            reply_err(req, code, txt);
            break;
        }
        reply_ok(req);
        break;
    }
    case CMD_PLAY: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        uint16_t to = 0;
        uint8_t override = 0;
        if (req->payload_len >= 2) { to = proto_get_u16(req->payload); }
        if (req->payload_len >= 3) { override = req->payload[2]; }
        uint16_t actual = 0;
        esp_err_t err = svc_play_start(req->chan, to, override != 0, &actual);
        if (err == ESP_ERR_INVALID_STATE) {
            reply_err(req, ERR_SAFETY_LIMIT, "超过同时满幅路数限制（可显式 override）");
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "播放启动失败"); break; }
        proto_put_u16(p, actual);
        reply(req, p, 2);
        break;
    }
    case CMD_STOP: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        uint8_t immediate = (req->payload_len >= 1) ? req->payload[0] : 0;
        esp_err_t err = svc_play_stop(req->chan, immediate);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "停止失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_PLAY_TIMED: {
        if (req->payload_len != 3) { reply_err(req, ERR_BAD_LENGTH, "需要 mask+duration_ms"); break; }
        uint8_t started = 0;
        esp_err_t err = svc_play_timed(req->payload[0], proto_get_u16(&req->payload[1]), &started);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "定时播放失败"); break; }
        p[0] = started;
        reply(req, p, 1);
        break;
    }
    case CMD_LIVE_CONTROL: {
        if (req->payload_len != 10) { reply_err(req, ERR_BAD_LENGTH, "live requires 10 bytes"); break; }
        const uint8_t *v = req->payload;
        live_config_t cfg = {.frequency_x10 = proto_get_u16(v + 2), .amplitude = v[4],
            .envelope = v[5], .rate_x10 = proto_get_u16(v + 6), .depth = v[8], .tracking = v[9]};
        esp_err_t err = svc_play_live_control(v[0], v[1], &cfg);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "live control rejected or stopped"); break; }
        reply(req, v, 10); /* Accepted configuration; not a mechanical frequency measurement. */
        break;
    }
    case CMD_TACTILE_FRAME: {
        if (req->payload_len != 12) { reply_err(req, ERR_BAD_LENGTH, "tactile requires 12 bytes"); break; }
        const uint8_t *v = req->payload;
        esp_err_t err = svc_play_tactile_frame(v[0], v[1], proto_get_u16(v + 2), v + 4);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "tactile rejected or lease expired"); break; }
        reply(req, v, 12);
        break;
    }
    case CMD_LIVE_KEEPALIVE: {
        if (req->payload_len != 1) { reply_err(req, ERR_BAD_LENGTH, "live keepalive requires mask"); break; }
        esp_err_t err = svc_play_live_keepalive(req->payload[0]);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "live session expired or inactive"); break; }
        reply(req, req->payload, 1);
        break;
    }
    case CMD_BOOT_ARM: {
        if (req->payload_len != 1) { reply_err(req, ERR_BAD_LENGTH, "需要 BOOT 通道掩码"); break; }
        esp_err_t err = svc_play_boot_arm(req->payload[0]);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "BOOT 通道需已部署有效 TRIG 波形"); break; }
        p[0] = req->payload[0];
        reply(req, p, 1);
        break;
    }
    case CMD_PLAY_MULTI: {
        if (req->payload_len < 6) { reply_err(req, ERR_BAD_LENGTH, "需要 6 字节"); break; }
        uint8_t mask = req->payload[0];
        uint16_t to = proto_get_u16(&req->payload[1]);
        uint16_t skew = proto_get_u16(&req->payload[3]);
        uint8_t override = req->payload[5];
        uint8_t started = 0;
        uint16_t actual_skew = 0;
        esp_err_t err = svc_play_play_multi(mask, to, skew, override != 0,
                                            &started, &actual_skew);
        if (err == ESP_ERR_INVALID_STATE) {
            reply_err(req, ERR_SAFETY_LIMIT, "超过同时满幅路数限制（可显式 override）");
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "多路启动失败"); break; }
        p[0] = started;
        proto_put_u16(&p[1], actual_skew);
        reply(req, p, 3);
        break;
    }
    case CMD_TRIG_PULSE: {
        if (req->payload_len < 8) { reply_err(req, ERR_BAD_LENGTH, "需要 8 字节"); break; }
        uint8_t mask = req->payload[0];
        uint16_t width = proto_get_u16(&req->payload[1]);
        uint8_t count = req->payload[3];
        uint16_t period = proto_get_u16(&req->payload[4]);
        uint16_t delay = proto_get_u16(&req->payload[6]);
        uint8_t started = 0;
        esp_err_t err = svc_play_trig_pulse(mask, width, count, period, delay, &started);
        if (err == ESP_ERR_INVALID_STATE) {
            reply_err(req, ERR_SAFETY_LIMIT, "硬触发多路会同时满幅，超过限制（可显式 override）");
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "TRIG 脉冲失败：需正极性上升沿配置（禁用负沿），脉宽 1..1000 us"); break; }
        p[0] = started;
        reply(req, p, 1);
        break;
    }
    case CMD_TRIG_LEVEL: {
        if (req->payload_len < 4) { reply_err(req, ERR_BAD_LENGTH, "需要 4 字节"); break; }
        uint8_t mask = req->payload[0];
        uint8_t level = req->payload[1];
        uint16_t hold = proto_get_u16(&req->payload[2]);
        if (hold < BOARD_TRIG_LEVEL_MIN_MS) {
            reply_err(req, ERR_BAD_PARAM, "hold_ms 必须 ≥1（手册要求电平 ≥0.8 ms）");
            break;
        }
        esp_err_t err = svc_play_trig_level(mask, level, hold);
        if (err == ESP_ERR_INVALID_STATE) {
            reply_err(req, ERR_SAFETY_LIMIT, "超过同时满幅路数限制");
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "TRIG 电平失败：需正极性电平配置，level=1"); break; }
        reply_ok(req);
        break;
    }
    case CMD_TRIG_PATTERN: {
        if (req->payload_len < 9) { reply_err(req, ERR_BAD_LENGTH, "需要 9 字节"); break; }
        uint8_t mask = req->payload[0];
        uint16_t on_us = proto_get_u16(&req->payload[1]);
        uint16_t off_us = proto_get_u16(&req->payload[3]);
        uint16_t count = proto_get_u16(&req->payload[5]);
        uint16_t period = proto_get_u16(&req->payload[7]);
        esp_err_t err = svc_play_trig_pattern(mask, on_us, off_us, count, period);
        if (err == ESP_ERR_INVALID_STATE) {
            reply_err(req, ERR_SAFETY_LIMIT, "超过同时满幅路数限制");
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "TRIG 图案失败：需正极性上升沿配置（禁用负沿）"); break; }
        reply_ok(req);
        break;
    }
    case CMD_GET_PLAY_STATE: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        uint8_t playing = 0, glb = 0;
        uint16_t elapsed = 0;
        svc_play_query_state(req->chan, &playing, &glb, &elapsed);
        p[0] = playing;
        p[1] = glb;
        proto_put_u16(&p[2], elapsed);
        reply(req, p, 4);
        break;
    }
    case CMD_STOP_ALL: {
        uint8_t immediate = (req->payload_len >= 1) ? req->payload[0] : 1;
        uint8_t stopped = 0;
        esp_err_t stop_err = svc_play_stop_all(immediate, &stopped);
        if (stop_err != ESP_OK) { reply_err(req, err_to_proto(stop_err), "部分通道停止失败，固件继续重试"); break; }
        p[0] = stopped;
        reply(req, p, 1);
        break;
    }
    case CMD_TRIG_PARK: {
        svc_play_trig_park();
        reply_ok(req);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_param_locked(const proto_frame_t *req)
{
    uint8_t p[PROTO_MAX_PAYLOAD_LEN];
    if (!chan_ok(req)) {
        reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7");
        return;
    }
    uint8_t ch = req->chan;
    ch_ctx_t *c = ch_ctx(ch);
    switch (req->cmd) {
    case CMD_SET_PROFILE: {
        if (req->payload_len < AW_PROFILE_LEN) {
            reply_err(req, ERR_BAD_LENGTH, "SET_PROFILE 必须整块 64 字节");
            break;
        }
        aw_profile_t prof;
        if (!aw_profile_from_bytes(&prof, req->payload, req->payload_len)) {
            reply_err(req, ERR_BAD_LENGTH, "Profile 解析失败");
            break;
        }
        uint8_t off = 0;
        char msg[96];
        /* 整块校验：全部合法才下发，不要下发一半（协议 §9.7） */
        if (!aw_profile_validate(&prof, &off, msg, sizeof(msg))) {
            char full[160];
            snprintf(full, sizeof(full), "Profile 字段 %u（偏移）被拒：%s", (unsigned)off, msg);
            reply_err(req, ERR_BAD_PARAM, full);
            break;
        }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        if (prof.mode == AW_MODE_TRIG) {
            esp_err_t e = aw_ensure_trig_safety(ch);
            if (e != ESP_OK) {
                reply_err(req, ERR_VERIFY_FAILED,
                          "0x3A bit2 TRG1_STOP 回读仍为 0，拒绝进入 TRIG 模式");
                break;
            }
        }
        esp_err_t err = aw_apply_profile(ch, &prof);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "Profile 下发失败"); break; }
        c->profile = prof;
        c->mode = prof.mode;
        p[0] = 0;
        p[1] = 0;
        reply(req, p, 2);
        break;
    }
    case CMD_GET_PROFILE: {
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        aw_profile_t prof;
        if (!read_back_profile(ch, &prof)) {
            reply_err(req, ERR_I2C_TIMEOUT, "Profile 回读失败（I2C 读寄存器出错）");
            break;
        }
        aw_profile_to_bytes(&prof, p, sizeof(p));
        reply(req, p, AW_PROFILE_LEN);
        break;
    }
    case CMD_SET_CONT_LIVE: {
        if (req->payload_len < 2) { reply_err(req, ERR_BAD_LENGTH, "需要 drv1+drv2"); break; }
        uint8_t d1 = req->payload[0];
        uint8_t d2 = req->payload[1];
        if (d1 > 127 || d2 > 127) { reply_err(req, ERR_BAD_PARAM, "DRV*_LVL 0..127"); break; }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        /* 高频快路径：不做日志、不做额外校验；必须读-改-写保住 TRACK_EN */
        esp_err_t err = aw_update(ch, 0x1C, 0x7Fu, d1, NULL, NULL);
        if (err == ESP_OK) {
            err = aw_update(ch, 0x1D, 0x7Fu, d2, NULL, NULL);
        }
        if (err != ESP_OK) {
            reply_err(req, err_to_proto(err), "实时幅度写入失败");
            break;
        }
        c->profile.drv1_lvl = d1;
        c->profile.drv2_lvl = d2;
        reply_ok(req);
        break;
    }
    case CMD_SET_GAIN: {
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 gain(1)"); break; }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_write_u8(ch, REG_PLAYCFG2, req->payload[0]);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "写 GAIN 失败"); break; }
        c->profile.gain = req->payload[0];
        reply_ok(req);
        break;
    }
    case CMD_SET_VBAT: {
        if (req->payload_len < 2) { reply_err(req, ERR_BAD_LENGTH, "需要 vbat_mode+vbat_ref"); break; }
        if (req->payload[1] > 7) { reply_err(req, ERR_BAD_PARAM, "vbat_ref 0..7"); break; }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_update(ch, REG_VBATCTRL, 0x40u,
                                  (uint8_t)((req->payload[0] & 1u) << 6), NULL, NULL);
        if (err == ESP_OK) {
            err = aw_update(ch, REG_DETCFG1, 0x70u,
                            (uint8_t)((req->payload[1] & 0x07u) << 4), NULL, NULL);
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "电压补偿写入失败"); break; }
        c->profile.vbat_mode = req->payload[0] & 1u;
        c->profile.vbat_ref = req->payload[1] & 7u;
        reply_ok(req);
        break;
    }
    case CMD_SET_RAM_PARAMS: {
        if (req->payload_len < 14) { reply_err(req, ERR_BAD_LENGTH, "需要 14 字节"); break; }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_write(ch, REG_WAVCFG1, req->payload, 8);
        if (err == ESP_OK) {
            err = aw_write(ch, REG_WAVCFG9, &req->payload[10], 4);
        }
        if (err == ESP_OK) {
            uint8_t v16 = (uint8_t)((req->payload[8] & 0x0Fu) |
                                    ((req->payload[9] & 0x03u) << 4));
            /* ★ MAINLOOP/WAITSLOT 在 0x16（WAVCFG13）：0xF = 无限循环（持续振动） */
            err = aw_update(ch, REG_WAVCFG13, 0x3Fu, v16, NULL, NULL);
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "RAM 参数写入失败"); break; }
        memcpy(c->profile.wavseq, req->payload, 8);
        c->profile.mainloop = req->payload[8];
        c->profile.waitslot = req->payload[9];
        memcpy(c->profile.seq_loop, &req->payload[10], 4);
        reply_ok(req);
        break;
    }
    case CMD_SET_CONT_PARAMS: {
        if (req->payload_len < 14) { reply_err(req, ERR_BAD_LENGTH, "需要 14 字节"); break; }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        if (req->payload[3] == 0) { reply_err(req, ERR_BAD_PARAM, "f_pre 不可为 0"); break; }
        aw_profile_t t = c->profile;
        t.en_f0_det = req->payload[0];
        t.sin_mode = req->payload[1];
        t.edge_fre = req->payload[2];
        t.f_pre = req->payload[3];
        t.drv_width = req->payload[4];
        t.wait_num = req->payload[5];
        t.brk_gain = req->payload[6];
        t.track_en = req->payload[7];
        t.drv1_lvl = req->payload[8];
        t.drv2_lvl = req->payload[9];
        t.drv1_time = req->payload[10];
        t.drv2_time = req->payload[11];
        t.brk_time = req->payload[12];
        t.track_margin = req->payload[13];
        t.mode = AW_MODE_CONT;
        uint8_t off = 0;
        char msg[96];
        if (!aw_profile_validate(&t, &off, msg, sizeof(msg))) {
            reply_err(req, ERR_BAD_PARAM, msg);
            break;
        }
        esp_err_t err = aw_apply_profile(ch, &t);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "CONT 参数写入失败"); break; }
        if (t.brk_time == 0) {
            evt_log_text(LOG_WARN, "BRK_TIME = 0：自动刹车引擎将不工作");
        }
        if (t.en_f0_det) {
            evt_log_text(LOG_WARN, "EN_F0_DET = 1：自动刹车引擎将不工作");
        }
        c->profile = t;
        c->mode = AW_MODE_CONT;
        reply_ok(req);
        break;
    }
    case CMD_SET_RTP_PARAMS: {
        if (req->payload_len < 8) { reply_err(req, ERR_BAD_LENGTH, "需要 8 字节"); break; }
        if (req->payload[0] == RATE_48K) {
            reply_err(req, ERR_UNSUPPORTED, "48K RTP 需 ≥1 MHz I2C，本板只到 400 kHz");
            break;
        }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        aw_profile_t t = c->profile;
        t.wavdat_mode = req->payload[0];
        t.rtp_flags = req->payload[1];
        t.base_addr = proto_get_u16(&req->payload[2]);
        t.fifo_ae = proto_get_u16(&req->payload[4]);
        t.fifo_af = proto_get_u16(&req->payload[6]);
        t.mode = AW_MODE_RTP;
        esp_err_t err = aw_apply_profile(ch, &t);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "RTP 参数写入失败"); break; }
        c->profile = t;
        c->mode = AW_MODE_RTP;
        reply_ok(req);
        break;
    }
    case CMD_RTP_DATA: {
        if (req->payload_len < 4) { reply_err(req, ERR_BAD_LENGTH, "需要 offset+len+数据"); break; }
        uint8_t len = req->payload[2];
        if (len < 1 || len > BOARD_REG_MAX_LEN || req->payload_len < (size_t)(3 + len)) {
            reply_err(req, ERR_BAD_LENGTH, "len 1..64 且载荷匹配");
            break;
        }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_rtp_write(ch, &req->payload[3], len);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "RTP 数据写入失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_SET_TRIG_PARAMS: {
        if (req->payload_len < 13) { reply_err(req, ERR_BAD_LENGTH, "需要 13 字节"); break; }
        aw_profile_t t = c->profile;
        t.trig1_pos_en = req->payload[0];
        t.trig1_seq_p = req->payload[1];
        t.trig1_neg_en = req->payload[2];
        t.trig1_seq_n = req->payload[3];
        t.trig1_polar = req->payload[4];
        t.trig1_lev = req->payload[5];
        t.trig1_brk = req->payload[6];
        t.trig1_stop = req->payload[7];
        t.trig1_prio = req->payload[8];
        t.trg_gain = req->payload[9];
        t.trig1_mode = req->payload[10];
        t.intn_pin = req->payload[11];
        t.start_dly = req->payload[12];
        /* ★ 不允许上位机关闭这两个安全位（协议 §11.1） */
        if (t.trig1_brk == 0 || t.trig1_stop == 0) {
            reply_err(req, ERR_BAD_PARAM,
                      "TRG1_BRK 与 TRG1_STOP 必须为 1：关闭后 TRIG 播放将无法被停止，安全机制失效");
            break;
        }
        if (t.trig1_mode != 3) {
            reply_err(req, ERR_BAD_PARAM, "trig1_mode 必须为 3（b011 才是 TRIG）");
            break;
        }
        if (t.intn_pin != 0) {
            reply_err(req, ERR_BAD_PARAM, "本板 A1 必须作 TRIG1 输入，intn_pin 必须为 0");
            break;
        }
        uint8_t off = 0;
        char msg[96];
        if (!aw_profile_validate(&t, &off, msg, sizeof(msg))) {
            reply_err(req, ERR_BAD_PARAM, msg);
            break;
        }
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_ensure_trig_safety(ch);
        if (err == ESP_OK) {
            err = aw_apply_profile(ch, &t);
        }
        if (err != ESP_OK) {
            reply_err(req, ERR_VERIFY_FAILED, "TRIG 参数写入或安全位回读校验失败");
            break;
        }
        c->profile = t;
        reply_ok(req);
        break;
    }
    case CMD_SET_DEFAULT_PROFILE: {
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_apply_register_defaults(ch);
        if (err == ESP_OK) {
            aw_profile_defaults(&c->profile);
            c->mode = AW_MODE_RAM;
            err = ch_reapply_profile(ch);
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "恢复默认 Profile 失败"); break; }
        reply_ok(req);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_param(const proto_frame_t *req)
{
    svc_play_lock();
    /* Configuration changes revoke physical-button authorization. */
    bool mutation = req->cmd != CMD_GET_PROFILE;
    if (mutation) {
        uint8_t mask = req->chan < 8 ? (uint8_t)(1u << req->chan) : 0xFFu;
        svc_play_boot_invalidate(mask);
        if ((svc_play_live_mask() & mask) && svc_play_prepare_config(mask) != ESP_OK) {
            reply_err(req, ERR_BUSY, "live stop pending"); svc_play_unlock(); return;
        }
    }
    if (mutation && req->cmd != CMD_SET_CONT_LIVE && req->cmd != CMD_SET_GAIN && req->cmd != CMD_SET_VBAT && req->cmd != CMD_RTP_DATA) {
        uint8_t mask = req->chan < 8 ? (uint8_t)(1u << req->chan) : 0xFFu;
        if (svc_play_prepare_config(mask) != ESP_OK) {
            reply_err(req, ERR_BUSY, "停止尚未完成，拒绝重配");
            svc_play_unlock();
            return;
        }
    }
    handle_param_locked(req);
    svc_play_unlock();
}

static void handle_wave_locked(const proto_frame_t *req)
{
    uint8_t p[200];
    if (!chan_ok(req)) {
        reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7");
        return;
    }
    uint8_t ch = req->chan;
    switch (req->cmd) {
    case CMD_WAVE_BEGIN: {
        if (req->payload_len < 3) { reply_err(req, ERR_BAD_LENGTH, "需要 3 字节"); break; }
        uint8_t sid = 0;
        uint16_t max_total = 0, free_bytes = 0;
        esp_err_t err = svc_wave_begin(ch, req->payload[0], req->payload[1],
                                       req->payload[2], &sid, &max_total, &free_bytes);
        if (err == ESP_ERR_NOT_SUPPORTED) {
            reply_err(req, ERR_UNSUPPORTED, "48K 采样率本板不可用（需 ≥1 MHz I2C）");
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "波形会话开启失败"); break; }
        p[0] = sid;
        proto_put_u16(&p[1], max_total);
        proto_put_u16(&p[3], free_bytes);
        reply(req, p, 5);
        break;
    }
    case CMD_WAVE_ADD: {
        if (req->payload_len < 6) { reply_err(req, ERR_BAD_LENGTH, "需要头部 6 字节 + 样本"); break; }
        uint8_t wave_id = req->payload[0];
        uint16_t offset = proto_get_u16(&req->payload[1]);
        uint16_t total = proto_get_u16(&req->payload[3]);
        uint8_t flags = req->payload[5];
        uint8_t len = (uint8_t)(req->payload_len - 6);
        if (len == 0 || len > BOARD_WAVE_MAX_CHUNK) {
            reply_err(req, ERR_BAD_PARAM, "单片样本数 1..184");
            break;
        }
        uint16_t ack = 0;
        esp_err_t err = svc_wave_add(ch, wave_id, offset, total, flags,
                                     (const int8_t *)&req->payload[6], len, &ack);
        if (err == ESP_ERR_INVALID_STATE) { reply_err(req, ERR_NOT_ARMED, "请先 WAVE_BEGIN"); break; }
        if (err == ESP_ERR_INVALID_SIZE) { reply_err(req, ERR_SRAM_OVERFLOW, "波形缓冲已满"); break; }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "波形分片接收失败"); break; }
        p[0] = wave_id;
        proto_put_u16(&p[1], ack);
        reply(req, p, 3);
        break;
    }
    case CMD_WAVE_COMMIT: {
        uint8_t ver = (req->payload_len >= 1) ? req->payload[0] : 1;
        uint16_t total_bytes = 0, crc = 0, base = 0;
        static wave_lib_t s_lib;         /* 静态：让应答组装时地址表仍然有效 */
        esp_err_t err = svc_wave_commit(ch, ver, &total_bytes, &crc, &base, &s_lib);
        if (err == ESP_ERR_INVALID_STATE) { reply_err(req, ERR_NOT_ARMED, "请先 WAVE_BEGIN"); break; }
        if (err == ESP_ERR_INVALID_SIZE) { reply_err(req, ERR_SRAM_OVERFLOW, "波形库超过 SRAM 容量"); break; }
        if (err == ESP_ERR_INVALID_CRC) { reply_err(req, ERR_CRC_MISMATCH, "SRAM 回读校验失败，请重试下发"); break; }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "波形库写入失败"); break; }
        const wave_lib_t *lib = &s_lib;
        uint8_t n = lib->wave_count;
        p[0] = 0;
        proto_put_u16(&p[1], total_bytes);
        proto_put_u16(&p[3], crc);
        p[5] = n;
        proto_put_u16(&p[6], base);
        for (uint8_t i = 0; i < n; i++) {
            uint8_t off = (uint8_t)(8 + 5 * i);
            p[off] = lib->start[i] ? (uint8_t)(i + 1) : 0;
            proto_put_u16(&p[off + 1], lib->start[i]);
            proto_put_u16(&p[off + 3], lib->end[i]);
        }
        reply(req, p, (uint8_t)(8 + 5 * n));
        break;
    }
    case CMD_WAVE_ABORT: {
        esp_err_t err = svc_wave_abort(ch);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "中止失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_WAVE_ERASE: {
        esp_err_t err = svc_wave_erase(ch);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "清空波形库失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_WAVE_GET_LIBRARY: {
        uint8_t ver = 0, n = 0;
        uint16_t total = 0, base = 0;
        wave_lib_t lib;
        esp_err_t err = svc_wave_get_library(ch, &ver, &n, &total, &base, &lib);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "回读波形库失败"); break; }
        p[0] = ver;
        p[1] = n;
        proto_put_u16(&p[2], total);
        proto_put_u16(&p[4], base);
        for (uint8_t i = 0; i < n && i < lib.wave_count; i++) {
            uint8_t off = (uint8_t)(6 + 5 * i);
            p[off] = (uint8_t)(i + 1);
            proto_put_u16(&p[off + 1], lib.start[i]);
            proto_put_u16(&p[off + 3], lib.end[i]);
        }
        reply(req, p, (uint8_t)(6 + 5 * n));
        break;
    }
    case CMD_WAVE_READBACK: {
        if (req->payload_len < 4) { reply_err(req, ERR_BAD_LENGTH, "需要 wave_id+offset+len"); break; }
        uint8_t wave_id = req->payload[0];
        uint16_t offset = proto_get_u16(&req->payload[1]);
        uint8_t len = req->payload[3];
        if (len < 1 || len > BOARD_REG_MAX_LEN) {
            reply_err(req, ERR_BAD_PARAM, "len 1..64");
            break;
        }
        p[0] = wave_id;
        proto_put_u16(&p[1], offset);
        esp_err_t err = svc_wave_readback(ch, wave_id, offset, len, (int8_t *)&p[3]);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "波形回读失败（先确认库已提交）"); break; }
        reply(req, p, (uint8_t)(3 + len));
        break;
    }
    case CMD_WAVE_QUEUE: {
        if (req->payload_len < 2) { reply_err(req, ERR_BAD_LENGTH, "需要 count+ids"); break; }
        uint8_t count = req->payload[0];
        if (count < 1 || count > 8 || req->payload_len < (size_t)(1 + count)) {
            reply_err(req, ERR_BAD_PARAM, "count 1..8");
            break;
        }
        ch_ctx_t *c = ch_ctx(ch);
        if (!c->present) { reply_err(req, ERR_NOT_PRESENT, "该通道无器件应答"); break; }
        esp_err_t err = aw_write_wave_queue(ch, count, &req->payload[1]);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "写播放队列失败"); break; }
        memset(c->profile.wavseq, 0, sizeof(c->profile.wavseq));
        memcpy(c->profile.wavseq, &req->payload[1], count);
        reply_ok(req);
        break;
    }
    case CMD_WAVE_GEN: {
        /* 协议 §9.8 载荷共 8 字节：wave_id(1) shape(1) freq(2) amp(1) cycles(2) rate(1)。
         * ★ 这里原写成 `< 9`，比实际多要 1 字节，导致上位机按规范发的 8 字节
         *   永远被 ERR_BAD_LENGTH 拒绝 —— WAVE_GEN 从主机侧完全不可用。 */
        if (req->payload_len < 8) { reply_err(req, ERR_BAD_LENGTH, "需要 8 字节"); break; }
        uint8_t wave_id = req->payload[0];
        uint8_t shape = req->payload[1];
        uint16_t freq = proto_get_u16(&req->payload[2]);
        uint8_t amp = req->payload[4];
        uint16_t cycles = proto_get_u16(&req->payload[5]);
        uint8_t rate = req->payload[7];
        if (shape > 6) { reply_err(req, ERR_BAD_PARAM, "shape 0..6"); break; }
        uint16_t gen = 0;
        esp_err_t err = svc_wave_gen(ch, wave_id, shape, freq, amp, cycles, rate, &gen);
        if (err == ESP_ERR_INVALID_STATE) { reply_err(req, ERR_NOT_ARMED, "请先 WAVE_BEGIN"); break; }
        if (err == ESP_ERR_INVALID_SIZE) { reply_err(req, ERR_SRAM_OVERFLOW, "波形超出 SRAM 余量"); break; }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "波形合成失败"); break; }
        p[0] = wave_id;
        proto_put_u16(&p[1], gen);
        reply(req, p, 3);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_wave(const proto_frame_t *req)
{
    svc_play_lock();
    /* Configuration changes revoke physical-button authorization. */
    bool mutation = req->cmd != CMD_WAVE_GET_LIBRARY && req->cmd != CMD_WAVE_READBACK;
    if (mutation) {
        uint8_t mask = req->chan < 8 ? (uint8_t)(1u << req->chan) : 0xFFu;
        svc_play_boot_invalidate(mask);
        if ((svc_play_live_mask() & mask) && svc_play_prepare_config(mask) != ESP_OK) {
            reply_err(req, ERR_BUSY, "live stop pending"); svc_play_unlock(); return;
        }
    }
    if (mutation) {
        uint8_t mask = req->chan < 8 ? (uint8_t)(1u << req->chan) : 0xFFu;
        if (svc_play_prepare_config(mask) != ESP_OK) {
            reply_err(req, ERR_BUSY, "停止尚未完成，拒绝重配");
            svc_play_unlock();
            return;
        }
    }
    handle_wave_locked(req);
    svc_play_unlock();
}

static void handle_measure(const proto_frame_t *req)
{
    uint8_t p[32];
    switch (req->cmd) {
    case CMD_F0_START: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 3) { reply_err(req, ERR_BAD_LENGTH, "需要 timeout_ms+mode"); break; }
        uint16_t to = proto_get_u16(req->payload);
        uint8_t mode = req->payload[2];
        if (mode > 1) { reply_err(req, ERR_BAD_PARAM, "mode 0=F_LRA_F0 1=CONT"); break; }
        uint8_t started = 0;
        esp_err_t err = svc_measure_f0_start(req->chan, to, mode, &started);
        if (err == ESP_ERR_NOT_FOUND) {
            p[0] = 0;
            reply(req, p, 1);
            break;
        }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "F0 检测启动失败"); break; }
        p[0] = started;
        reply(req, p, 1);
        break;
    }
    case CMD_F0_RESULT: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        uint8_t valid = 0, mode = 0, h = 0, l = 0;
        uint16_t hz10 = 0xFFFFu;
        svc_measure_f0_result(req->chan, &valid, &mode, &h, &l, &hz10);
        p[0] = valid;
        p[1] = mode;
        p[2] = h;
        p[3] = l;
        proto_put_u16(&p[4], valid ? hz10 : 0xFFFFu);
        reply(req, p, 6);
        break;
    }
    case CMD_DET_READ: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        uint8_t seq = (req->payload_len >= 1) ? req->payload[0] : 0;
        uint16_t adc = 0, avg = 0, vx = 0xFFFF;
        uint8_t valid = 0, gain = 8;
        esp_err_t err = svc_measure_det_read(req->chan, seq, &adc, &avg, &vx, &valid, &gain);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "DET_READ 失败"); break; }
        proto_put_u16(&p[0], adc);
        proto_put_u16(&p[2], avg);
        proto_put_u16(&p[4], vx);
        p[6] = valid;
        p[7] = seq;
        reply(req, p, 8);
        break;
    }
    case CMD_MON_START: {
        if (req->payload_len < 5) { reply_err(req, ERR_BAD_LENGTH, "需要 mask+period+fields"); break; }
        esp_err_t err = svc_measure_mon_start(req->payload[0],
                                              proto_get_u16(&req->payload[1]),
                                              proto_get_u16(&req->payload[3]));
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "监视启动失败（周期下限 2 ms）"); break; }
        reply_ok(req);
        break;
    }
    case CMD_MON_STOP: {
        if (req->payload_len < 1) { reply_err(req, ERR_BAD_LENGTH, "需要 mask"); break; }
        esp_err_t err = svc_measure_mon_stop(req->payload[0]);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "监视停止失败"); break; }
        reply_ok(req);
        break;
    }
    case CMD_SNAPSHOT: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        snapshot_t s;
        esp_err_t err = svc_measure_snapshot(req->chan, &s);
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "SNAPSHOT 失败"); break; }
        p[0] = s.present;
        p[1] = s.chipid_h;
        p[2] = s.chipid_l;
        p[3] = s.mode;
        p[4] = s.playing;
        p[5] = s.glb_state;
        p[6] = s.sysst;
        p[7] = s.sysst2;
        p[8] = s.reg24;
        p[9] = s.reg25;
        p[10] = s.reg26;
        p[11] = s.reg27;
        p[12] = s.reg51;
        p[13] = s.reg52;
        p[14] = s.reg53;
        reply(req, p, 15);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void handle_test(const proto_frame_t *req)
{
    uint8_t p[8];
    switch (req->cmd) {
    case CMD_SWEEP_AMP: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 8) { reply_err(req, ERR_BAD_LENGTH, "需要 8 字节"); break; }
        uint8_t accepted = 0, total = 0;
        esp_err_t err = svc_sweep_amp(req->chan, req->payload[0], req->payload[1],
                                      req->payload[2], req->payload[3],
                                      proto_get_u16(&req->payload[4]),
                                      proto_get_u16(&req->payload[6]),
                                      &accepted, &total);
        if (err == ESP_ERR_INVALID_STATE) { reply_err(req, ERR_BUSY, "已有扫描在进行"); break; }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "幅度扫描启动失败（step 不可为 0）"); break; }
        p[0] = accepted;
        p[1] = total;
        reply(req, p, 2);
        break;
    }
    case CMD_SWEEP_FREQ: {
        if (!chan_ok(req)) { reply_err(req, ERR_BAD_CHANNEL, "通道号须在 0..7"); break; }
        if (req->payload_len < 8) { reply_err(req, ERR_BAD_LENGTH, "需要 8 字节"); break; }
        uint8_t accepted = 0, total = 0;
        esp_err_t err = svc_sweep_freq(req->chan,
                                       proto_get_u16(&req->payload[0]),
                                       proto_get_u16(&req->payload[2]),
                                       proto_get_u16(&req->payload[4]),
                                       proto_get_u16(&req->payload[6]),
                                       &accepted, &total);
        if (err == ESP_ERR_INVALID_STATE) { reply_err(req, ERR_BUSY, "已有扫描在进行"); break; }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "频率扫描启动失败"); break; }
        p[0] = accepted;
        p[1] = total;
        reply(req, p, 2);
        break;
    }
    case CMD_SWEEP_ABORT: {
        esp_err_t err = svc_sweep_abort();
        if (err != ESP_OK) { reply_err(req, ERR_INTERNAL, "当前没有扫描在运行"); break; }
        reply_ok(req);
        break;
    }
    case CMD_TEST_RUN: {
        if (req->payload_len < 2) { reply_err(req, ERR_BAD_LENGTH, "需要 test_id(2)"); break; }
        uint8_t accepted = 0;
        esp_err_t err = svc_test_run(proto_get_u16(req->payload),
                                     req->payload_len > 2 ? &req->payload[2] : NULL,
                                     (uint8_t)(req->payload_len > 2 ? req->payload_len - 2 : 0),
                                     &accepted);
        if (err == ESP_ERR_INVALID_STATE) { reply_err(req, ERR_BUSY, "已有测试项在运行"); break; }
        if (err != ESP_OK) { reply_err(req, err_to_proto(err), "测试项启动失败"); break; }
        p[0] = accepted;
        reply(req, p, 1);
        break;
    }
    case CMD_TEST_ABORT: {
        esp_err_t err = svc_test_abort();
        if (err != ESP_OK) { reply_err(req, ERR_INTERNAL, "当前没有测试项在运行"); break; }
        reply_ok(req);
        break;
    }
    case CMD_SELFTEST: {
        if (svc_selftest_active()) { reply_err(req, ERR_BUSY, "自检已在运行"); break; }
        svc_selftest_start();
        p[0] = 1;
        reply(req, p, 1);
        break;
    }
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码");
        break;
    }
}

static void dispatch(const proto_frame_t *req)
{
    /* 收到任何合法帧都刷新心跳（协议 §11） */
    app_session_note_frame();
    app_session_set_safety_state(SAFETY_NORMAL);

    /* 通道组是 0x18~0x1F：与总线组（0x10~0x17）同处于 0x1x 段，必须单独判定 */
    if (req->cmd >= 0x18u && req->cmd <= 0x1Fu) {
        handle_channel(req);
        return;
    }

    switch (req->cmd & 0xF0u) {
    case 0x00: handle_system(req); break;
    case 0x10: handle_bus(req); break;
    case 0x20: handle_register(req); break;
    case 0x30: handle_device(req); break;
    case 0x40: handle_play(req); break;
    case 0x50: handle_param(req); break;
    case 0x60: handle_wave(req); break;
    case 0x70: handle_measure(req); break;
    case 0x80: handle_test(req); break;
    default:
        reply_err(req, ERR_UNKNOWN_CMD, "未知命令码组");
        break;
    }
}

/* ================================================================== */
/* 任务                                                                */
/* ================================================================== */

static void app_log_sink_impl(uint8_t level, const char *text)
{
    if (app_session_ascii_mode()) {
        proto_port_write_text(text);
        proto_port_write_text("\n");
    } else {
        evt_log_text(level, text);
    }
}

void app_log_sink(uint8_t level, const char *text)
{
    app_log_sink_impl(level, text);
}

void app_protocol_init(void)
{
    proto_crc_init();
    s_evt_mutex = xSemaphoreCreateMutex();
    configASSERT(s_evt_mutex != NULL);
    port_init();
    log_init(app_log_sink_impl);
}

void app_protocol_task(void *arg)
{
    (void)arg;
    static uint8_t rx[256];
    static proto_frame_t f;
    proto_parser_t *ps = (proto_parser_t *)malloc(sizeof(proto_parser_t));
    if (ps == NULL) {
        LOGE("协议任务：解析器缓冲分配失败");
        vTaskDelete(NULL);
        return;
    }
    proto_parser_init(ps);

    /* 上电问候（ASCII 模式） */
    emit_banner();

    while (true) {
        /* 1) 先把待发事件发出去（事件不打断正在发送的帧：整帧一次写） */
        uint8_t evt[PROTO_MAX_FRAME_LEN];
        uint16_t evt_len = 0;
        if (!app_session_ascii_mode()) {
            int budget = 8;
            while (budget-- > 0 && evt_pop(evt, &evt_len)) {
                proto_port_write(evt, evt_len);
            }
        }

        /* 2) 收字节 */
        int n = proto_port_read(rx, sizeof(rx), pdMS_TO_TICKS(20));
        if (n > 0) {
            if (app_session_ascii_mode()) {
                app_ascii_feed(rx, (size_t)n);
            } else {
                /* 先喂入整块，再把缓冲区里能解出的帧全部取走
                 * （proto_parser_feed 每次调用都会追加数据，不能重复喂同一块） */
                bool got = proto_parser_feed(ps, rx, (size_t)n, &f);
                while (got) {
                    dispatch(&f);
                    got = proto_parser_feed(ps, NULL, 0, &f);
                }
            }
        }
    }
}

void app_protocol_start_tasks(void)
{
    xTaskCreate(app_protocol_task, "proto_task", TASK_STACK_PROTO, NULL,
                TASK_PRIO_PROTO, NULL);
}
