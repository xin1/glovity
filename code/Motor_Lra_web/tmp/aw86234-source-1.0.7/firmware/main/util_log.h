/**
 * @file  util_log.h
 * @brief 日志后端：ASCII 模式写文本，二进制模式走 `EVT_LOG` 事件帧。
 *
 * ⚠️ **二进制模式下绝不能有裸 printf 写协议口**——那会撕裂帧流（协议 §7、`04` 文档 §7.2）。
 * 本模块是唯一的日志出口：`log_emit()` 在二进制模式下把文本投进事件队列，
 * 由协议任务统一发送。
 */

#pragma once

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 日志落地函数：ASCII 模式写文本，二进制模式投 EVT_LOG。由 app_main 注册。 */
typedef void (*log_sink_t)(uint8_t level, const char *text);

void    log_init(log_sink_t sink);
void    log_set_level(uint8_t level);
uint8_t log_get_level(void);

/** 丢弃计数（协议 §10：debug 级别下超出 100 条/秒要丢弃并计数，在 GET_STATUS 里体现）。 */
uint32_t log_dropped_count(void);

void log_emit(uint8_t level, const char *fmt, ...)
#if defined(__GNUC__)
    __attribute__((format(printf, 2, 3)))
#endif
    ;

#define LOGE(fmt, ...)  log_emit(LOG_ERROR, fmt, ##__VA_ARGS__)
#define LOGW(fmt, ...)  log_emit(LOG_WARN,  fmt, ##__VA_ARGS__)
#define LOGI(fmt, ...)  log_emit(LOG_INFO,  fmt, ##__VA_ARGS__)
#define LOGD(fmt, ...)  log_emit(LOG_DEBUG, fmt, ##__VA_ARGS__)

/* 供 util_log.c 使用的级别常量（与 proto_cmd.h 保持一致） */
#ifndef LOG_OFF
#define LOG_OFF     0u
#define LOG_ERROR   1u
#define LOG_WARN    2u
#define LOG_INFO    3u
#define LOG_DEBUG   4u
#endif

#ifdef __cplusplus
}
#endif
