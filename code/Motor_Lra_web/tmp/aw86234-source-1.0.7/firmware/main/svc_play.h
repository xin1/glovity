/**
 * @file  svc_play.h
 * @brief 播放服务：四种模式（RAM / RTP / CONT / TRIG）、TRIG 脉冲本地生成、
 *        播放结束轮询、同时满幅路数限制。
 *
 * ★ µs 级脉冲**必须**用忙等（`esp_rom_delay_us`），`vTaskDelay` 的最小分辨率是
 *   1 个 tick（默认 10 ms），完全不能用（`04` 文档 §6.4）。
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "cfg_board.h"      /* board_trig_gpio() / board_trig_is_shared() 声明在此 */
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 把掩码里 CH5/6/7 的任一位补齐成三位（共用 GPIO14，硬触发下必然一起动）。 */
uint8_t svc_play_expand_shared(uint8_t mask);

/** SET_MODE：写 0x08 PLAY_MODE；TRIG 会先做安全位校验。 */
esp_err_t svc_play_set_mode(uint8_t ch, uint8_t mode);

/** 播放前的安全预检：同时满幅路数是否超限。返回 ESP_ERR_INVALID_STATE 表示超限。 */
esp_err_t svc_play_check_safety(uint8_t mask, bool override, uint8_t *violating_mask);

/** PLAY：写 GO + 启动安全计时器。 */
esp_err_t svc_play_start(uint8_t ch, uint16_t timeout_ms, bool override,
                         uint16_t *actual_timeout_ms);

/** STOP。 */
esp_err_t svc_play_stop(uint8_t ch, uint8_t immediate);

/** PLAY_MULTI：软触发（走 I2C GO），8 路可任意组合，不受 CH5/6/7 限制。 */
esp_err_t svc_play_play_multi(uint8_t chan_mask, uint16_t timeout_ms,
                              uint16_t skew_us, bool override,
                              uint8_t *started_mask, uint16_t *actual_skew_us);

/** STOP_ALL。 */
esp_err_t svc_play_stop_all(uint8_t immediate, uint8_t *stopped_mask);

/** 所有 TRIG 输出置低（上电、超时、心跳丢失、STOP_ALL、BUS_RECOVER、进 ASCII 模式都要调）。 */
void svc_play_trig_park(void);

/** TRIG 脉冲串（固件本地产生，count ≤ 32）。 */
esp_err_t svc_play_trig_pulse(uint8_t chan_mask, uint16_t width_us, uint8_t count,
                              uint16_t period_ms, uint16_t initial_delay_us,
                              uint8_t *started_mask);

/** TRIG 电平触发（hold_ms ≥ 1，手册要求电平 ≥0.8 ms）。 */
esp_err_t svc_play_trig_level(uint8_t chan_mask, uint8_t level, uint16_t hold_ms);

/** TRIG 图案（µs 级 on/off 脉冲串），用于 A6/A7/A8 类时序测试。 */
esp_err_t svc_play_trig_pattern(uint8_t chan_mask, uint16_t on_us, uint16_t off_us,
                                uint16_t count, uint16_t period_ms);

/** 由安全任务周期性调用：轮询 GLB_STATE，回到 0 就产生 EVT_PLAY_DONE。 */
void svc_play_poll(uint32_t now_ms);

/** 当前自适应轮询间隔（协议 §6.2）。 */
uint32_t svc_play_poll_interval_ms(void);

/** 读取某通道的播放状态（供 GET_PLAY_STATE）。 */
esp_err_t svc_play_query_state(uint8_t ch, uint8_t *playing, uint8_t *glb_state,
                               uint16_t *elapsed_ms);

/** 触发源置为输出低并静默（TRIG_PARK，同时写 0x08 STOP 作为第二道手段）。 */
void svc_play_trig_park_channels(uint8_t chan_mask);

#ifdef __cplusplus
}
#endif

void svc_play_init(void);
void svc_play_lock(void);
void svc_play_unlock(void);
void svc_play_tick(uint32_t now);
esp_err_t svc_play_timed(uint8_t mask, uint16_t duration_ms, uint8_t *started);
esp_err_t svc_play_boot_arm(uint8_t mask);
void svc_play_boot_invalidate(uint8_t mask);
esp_err_t svc_play_boot_fire(uint8_t *started);

bool svc_play_active(uint8_t ch);
esp_err_t svc_play_prepare_config(uint8_t mask);

#include "live_codec.h"
uint8_t svc_play_live_mask(void);
esp_err_t svc_play_live_control(uint8_t action, uint8_t mask, const live_config_t *config);
esp_err_t svc_play_live_keepalive(uint8_t mask);
/* Atomic per-channel force feedback. START must be silent; fresh UPDATE leases 300ms. */
esp_err_t svc_play_tactile_frame(uint8_t action, uint8_t mask, uint16_t seq, const uint8_t levels[8]);
