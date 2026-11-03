/**
 * @file  drv_i2c_bus.h
 * @brief I2C 总线驱动：引脚自动探测、速率切换、内部上拉开关、三级总线恢复。
 *
 * ⚠️ **本模块只提供"裸传输"原语**，"选通 TCA + 传输"的原子性由 `svc_channel.c`
 * 用一把全局互斥锁保证（协议 §12、`04` 文档 §5.4）。任何绕过 `ch_xfer()` 的
 * 通路都是 bug。
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t  sel_used;          /* 0=未探测 1=LP 2=HP */
    uint8_t  sda_gpio;
    uint8_t  scl_gpio;
    uint32_t speed_hz;
    uint8_t  internal_pullup;   /* 1 = 正在使用内部弱上拉（降级运行，对应 V-1） */
    uint8_t  tca_present;
    uint8_t  tca_addr;
    uint8_t  ctrl_readback;
    uint8_t  probe_speed_ok;    /* 1 = 400 kHz 下也确认通信正常 */
} bus_info_t;

/** 总线引脚组合选择（与协议 BUS_PROBE.bus_sel 一致）。 */
#define BUS_SEL_AUTO    0u
#define BUS_SEL_LP      1u
#define BUS_SEL_HP      2u

/** 探测 I2C 引脚组合并初始化总线（先 LP 再 HP；探测期 100 kHz，成功后升 400 kHz）。 */
esp_err_t bus_probe_auto(uint8_t bus_sel, bus_info_t *out);

/** 切换速率。只接受 100000 / 400000。 */
esp_err_t bus_set_speed(uint32_t hz, uint32_t *actual_hz);

/** 开关 ESP32-C6 内部弱上拉（外部上拉缺失时的降级手段）。 */
esp_err_t bus_set_pullup(bool enable);

/** 三级递进的总线恢复。返回 0=失败 1=9clock+STOP 2=TCA RESET 3=总线重建。 */
uint8_t bus_recover(void);

/** 总线是否已就绪（探测成功）。 */
bool bus_is_ready(void);

/** 当前总线信息（未探测时 sel_used = 0）。 */
const bus_info_t *bus_info(void);

/** 释放并重建总线（引脚变更时用）。 */
esp_err_t bus_recreate(uint8_t sda_gpio, uint8_t scl_gpio, uint32_t speed_hz,
                       bool internal_pullup);

/** TCA9548A 的 RESET 脚脉冲（低有效，≥10 µs）。 */
void bus_tca_reset_pulse(uint16_t hold_us);

/* ---- 裸传输原语（调用者必须已持有 I2C 互斥锁） ---- */

esp_err_t bus_tca_write_byte(uint8_t value, uint32_t timeout_ms);
esp_err_t bus_tca_read_byte(uint8_t *out, uint32_t timeout_ms);
esp_err_t bus_aw_tx(const uint8_t *wr, size_t wr_len, uint32_t timeout_ms);
esp_err_t bus_aw_txrx(const uint8_t *wr, size_t wr_len,
                      uint8_t *rd, size_t rd_len, uint32_t timeout_ms);
esp_err_t bus_probe_dev(uint8_t addr7, uint32_t timeout_ms);

/** 把 ESP-IDF 的 esp_err_t 映射成协议错误码（协议 §6.1）。 */
uint8_t bus_err_to_proto(esp_err_t err);

#ifdef __cplusplus
}
#endif
