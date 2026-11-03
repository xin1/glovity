/**
 * @file  svc_channel.c
 * @brief 通道抽象与原子访问实现。
 *
 * 全局只有**一把** I2C 互斥锁：TCA9548A 的选通状态是所有通道共享的，
 * 两把任务各自"先选通再传输"会交错成"我选了 CH1、你选了 CH3、我的数据发给了 CH3 的芯片"。
 */

#include "svc_channel.h"

#include <string.h>

#include "cfg_board.h"
#include "drv_aw8623x.h"
#include "drv_i2c_bus.h"
#include "drv_tca9548a.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "proto_cmd.h"
#include "util_log.h"

static ch_ctx_t s_ch[BOARD_CHANNEL_COUNT];
static SemaphoreHandle_t s_i2c_mutex = NULL;

static inline uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void svc_channel_init(void)
{
    s_i2c_mutex = xSemaphoreCreateMutex();
    configASSERT(s_i2c_mutex != NULL);

    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        memset(&s_ch[ch], 0, sizeof(s_ch[ch]));
        s_ch[ch].state = CH_ABSENT;
        s_ch[ch].mode = AW_MODE_RAM;
        s_ch[ch].vreg_ok = 0xFF;
        aw_profile_defaults(&s_ch[ch].profile);
        s_ch[ch].profile.mode = AW_MODE_RAM;
    }
}

ch_ctx_t *ch_ctx(uint8_t ch)
{
    return (ch < BOARD_CHANNEL_COUNT) ? &s_ch[ch] : NULL;
}

bool ch_valid(uint8_t ch)
{
    return ch < BOARD_CHANNEL_COUNT;
}

esp_err_t ch_lock(void)
{
    if (s_i2c_mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(s_i2c_mutex, pdMS_TO_TICKS(BOARD_I2C_XFER_TIMEOUT_MS)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

void ch_unlock(void)
{
    if (s_i2c_mutex != NULL) {
        xSemaphoreGive(s_i2c_mutex);
    }
}

esp_err_t ch_xfer(uint8_t ch, const uint8_t *wr, size_t wr_len,
                  uint8_t *rd, size_t rd_len, uint32_t timeout_ms)
{
    if (ch >= BOARD_CHANNEL_COUNT || wr == NULL || wr_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!bus_is_ready()) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t err = ch_lock();
    if (err != ESP_OK) {
        if (s_ch[ch].last_err == ERR_OK) {
            s_ch[ch].last_err = ERR_BUS_BUSY;
        }
        return err;
    }

    /* ★ 选通与传输必须在同一把锁内 */
    err = tca_select(ch);
    if (err == ESP_OK) {
        if (rd != NULL && rd_len > 0) {
            err = bus_aw_txrx(wr, wr_len, rd, rd_len, timeout_ms);
        } else {
            err = bus_aw_tx(wr, wr_len, timeout_ms);
        }
    }
    ch_unlock();

    if (err != ESP_OK) {
        uint8_t code = bus_err_to_proto(err);
        ch_note_error(ch, code);
        if (code == ERR_I2C_TIMEOUT || code == ERR_I2C_NACK) {
            /* 器件无应答 => 该路不在线（也可能是总线问题，交给 BUS_RECOVER 判定） */
            if (code == ERR_NOT_PRESENT) {
                s_ch[ch].present = 0;
            }
        }
    }
    return err;
}

uint8_t ch_present_mask(void)
{
    uint8_t m = 0;
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (s_ch[ch].present) {
            m |= (uint8_t)(1u << ch);
        }
    }
    return m;
}

void ch_note_error(uint8_t ch, uint8_t err)
{
    if (ch >= BOARD_CHANNEL_COUNT || err == ERR_OK) {
        return;
    }
    s_ch[ch].last_err = err;
    if (err == ERR_NOT_PRESENT) {
        s_ch[ch].present = 0;
        s_ch[ch].state = CH_ABSENT;
    } else if (s_ch[ch].state != CH_BUSY) {
        s_ch[ch].state = CH_ERROR;
    }
}

bool ch_is_busy(uint8_t ch)
{
    if (ch >= BOARD_CHANNEL_COUNT) {
        return false;
    }
    return s_ch[ch].long_op || s_ch[ch].wave_session || s_ch[ch].f0_pending;
}

void ch_set_long_op(uint8_t ch, bool busy)
{
    if (ch < BOARD_CHANNEL_COUNT) {
        s_ch[ch].long_op = busy;
        if (busy) {
            s_ch[ch].state = CH_BUSY;
        } else if (s_ch[ch].state == CH_BUSY) {
            s_ch[ch].state = s_ch[ch].playing ? CH_PLAYING : CH_IDLE;
        }
    }
}

/* ------------------------------------------------------------------ */
/* 播放安全超时                                                        */
/* ------------------------------------------------------------------ */

void ch_arm_safety(uint8_t ch, uint16_t timeout_ms)
{
    if (ch >= BOARD_CHANNEL_COUNT) {
        return;
    }
    if (timeout_ms == 0) {
        timeout_ms = BOARD_SAFETY_TIMEOUT_MS_DEFAULT;
    }
    s_ch[ch].safety_armed = true;
    s_ch[ch].safety_timeout_ms = timeout_ms;
    s_ch[ch].safety_deadline_ms = now_ms() + timeout_ms;
}

void ch_disarm_safety(uint8_t ch)
{
    if (ch < BOARD_CHANNEL_COUNT) {
        s_ch[ch].safety_armed = false;
    }
}

int32_t ch_safety_remaining_ms(uint8_t ch)
{
    if (ch >= BOARD_CHANNEL_COUNT || !s_ch[ch].safety_armed) {
        return INT32_MAX;
    }
    uint32_t now = now_ms();
    if (now >= s_ch[ch].safety_deadline_ms) {
        /* 处理 49.7 天回绕 */
        if ((now - s_ch[ch].safety_deadline_ms) > 0x7FFFFFFFu) {
            return (int32_t)(s_ch[ch].safety_deadline_ms - now);
        }
        return -1;
    }
    return (int32_t)(s_ch[ch].safety_deadline_ms - now);
}

bool ch_any_safety_armed(void)
{
    for (uint8_t ch = 0; ch < BOARD_CHANNEL_COUNT; ch++) {
        if (s_ch[ch].safety_armed) {
            return true;
        }
    }
    return false;
}

void ch_mark_play_started(uint8_t ch)
{
    if (ch < BOARD_CHANNEL_COUNT) {
        s_ch[ch].playing = true;
        s_ch[ch].play_started_ms = now_ms();
        s_ch[ch].play_elapsed_ms = 0;
        if (s_ch[ch].state != CH_BUSY) {
            s_ch[ch].state = CH_PLAYING;
        }
    }
}

void ch_mark_play_stopped(uint8_t ch)
{
    if (ch < BOARD_CHANNEL_COUNT) {
        if (s_ch[ch].playing) {
            s_ch[ch].play_elapsed_ms = now_ms() - s_ch[ch].play_started_ms;
        }
        s_ch[ch].playing = false;
        if (s_ch[ch].state == CH_PLAYING || s_ch[ch].state == CH_ARMED) {
            s_ch[ch].state = CH_IDLE;
        }
    }
}

esp_err_t ch_reapply_profile(uint8_t ch)
{
    if (ch >= BOARD_CHANNEL_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    /* 先 WAKE，再整体重放（软复位会清掉全部配置寄存器） */
    esp_err_t err = aw_wake(ch);
    if (err != ESP_OK) {
        return err;
    }
    err = aw_apply_profile(ch, &s_ch[ch].profile);
    if (err != ESP_OK) {
        return err;
    }
    return aw_verify_trap_bits(ch, s_ch[ch].profile.mode);
}
