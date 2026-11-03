/**
 * @file  app_protocol.h
 * @brief 协议层：串口收发、帧解析、命令分派、应答与事件发送。
 *
 * ★ **只有协议任务可以写协议串口**。其它任务（安全、监视）只能通过
 *   `proto_post_event()` / `evt_*()` 投递事件帧，由协议任务统一发送，
 *   否则会撕裂正在发送的帧（`04` 文档 §3、§7.2）。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "app_session.h"
#include "proto_frame.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- 生命周期 ---- */
void app_protocol_init(void);        /* 初始化事件队列与串口 */
void app_protocol_task(void *arg);   /* 协议任务：收帧 → 分派 → 发送 */
void app_protocol_start_tasks(void);

/* ---- 串口抽象 ---- */
void   proto_port_write(const uint8_t *data, size_t len);
int    proto_port_read(uint8_t *data, size_t len, uint32_t ticks);
void   proto_port_write_text(const char *text);   /* 仅 ASCII 模式使用 */
bool   proto_port_use_driver(void);

/* ---- 事件投递（任何任务都可调用） ---- */
void proto_post_event(uint8_t cmd, uint8_t chan, const uint8_t *payload, uint8_t len);

void evt_log_text(uint8_t level, const char *text);
void evt_play_started(uint8_t ch, uint8_t mode, uint16_t timeout_ms);
void evt_play_done(uint8_t ch, uint8_t reason, uint16_t elapsed_ms);
void evt_measure_result(uint8_t ch, uint8_t kind, uint8_t valid, uint32_t value,
                        uint8_t raw_h, uint8_t raw_l);
void evt_sweep_progress(uint8_t step, uint8_t total, uint16_t value);
void evt_sweep_done(uint8_t steps, uint8_t status);
void evt_error(uint8_t errcode, uint8_t cmd, const char *text);
void evt_safety_trip(uint8_t reason, uint8_t affected_mask, uint8_t detail);
void evt_test_progress(uint16_t test_id, uint8_t step, uint8_t total, uint8_t status,
                       const char *text);
void evt_selftest_result(uint8_t overall, uint8_t present_mask, uint8_t bus_sel,
                         uint8_t tca, uint8_t vreg_mask, uint8_t issue_count,
                         const char *text);
void evt_bus_recovered(uint8_t method);

/* ---- ASCII 模式 ---- */
void app_ascii_enter(void);
bool app_ascii_is_active(void);
void app_ascii_feed(const uint8_t *data, size_t len);
void app_ascii_prompt_binary_hint(void);

/** 上电问候语（"READY aw86234-testbench fw=... board=1"）。实现见 app_ascii.c。 */
void emit_banner(void);

/** 错误码名称（ASCII 模式输出用）。实现见 app_ascii.c。 */
const char *err_name_str(uint8_t code);

/* ---- 由 log 后端调用：把日志落到正确的地方 ---- */
void app_log_sink(uint8_t level, const char *text);

/* ---- 供 ASCII 模式复用的高层动作 ---- */
void app_request_reboot(uint16_t delay_ms);

#ifdef __cplusplus
}
#endif
