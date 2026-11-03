/**
 * @file  util_log.c
 * @brief 日志后端实现。唯一的日志出口。
 */

#include "util_log.h"

#include <stdio.h>
#include <string.h>

#include "cfg_defaults.h"
#include "esp_timer.h"
#include "proto_cmd.h"

static log_sink_t s_sink = NULL;
static uint8_t    s_level = DEF_LOG_LEVEL;
static uint32_t   s_dropped = 0;

/* 速率限制：EVT_LOG 在 debug 级别下建议 ≤100 条/秒 */
#define LOG_MAX_PER_SEC     100u
static uint32_t s_win_start_ms = 0;
static uint32_t s_win_count = 0;

void log_init(log_sink_t sink)
{
    s_sink = sink;
    s_level = DEF_LOG_LEVEL;
    s_dropped = 0;
    s_win_start_ms = 0;
    s_win_count = 0;
}

void log_set_level(uint8_t level)
{
    s_level = (level > LOG_DEBUG) ? LOG_DEBUG : level;
}

uint8_t log_get_level(void)
{
    return s_level;
}

uint32_t log_dropped_count(void)
{
    return s_dropped;
}

void log_emit(uint8_t level, const char *fmt, ...)
{
    if (s_sink == NULL || level > s_level || level == LOG_OFF) {
        return;
    }

    /* 速率限制 */
    uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);
    if (s_win_start_ms == 0 || (now_ms - s_win_start_ms) >= 1000u) {
        s_win_start_ms = now_ms;
        s_win_count = 0;
    }
    if (++s_win_count > LOG_MAX_PER_SEC) {
        s_dropped++;
        return;
    }

    char buf[192];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) {
        return;
    }
    if ((size_t)n >= sizeof(buf)) {
        buf[sizeof(buf) - 1] = '\0';
    }
    s_sink(level, buf);
}
