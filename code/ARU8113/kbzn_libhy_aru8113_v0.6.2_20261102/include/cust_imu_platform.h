/**
 * @file    cust_imu_platform.h
 * @brief   Customer-implemented platform port for HY IMU SDK (bus, delay, host time).
 * @author  hanlm
 * @note    All customer symbols and this header use the cust_imu_* / cust_ prefix
 *          to distinguish from SDK APIs (hy_imu_*). Independent from vendor demos.
 *          See port/cust_imu_platform_template.c.
 */

#ifndef CUST_IMU_PLATFORM_H
#define CUST_IMU_PLATFORM_H

#include "hy_imu_acq.h"

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize the host serial interface for one IMU entity.
 * @param[in] bus  Non-NULL bus context (id, serif, i2c_addr, port_user).
 * @retval HY_IMU_OK         Success (including "already initialized, nothing to do").
 * @retval HY_IMU_ERR_PARAM  bus is NULL or serif is invalid.
 * @retval HY_IMU_ERR_IO     Platform peripheral init failed.
 * @note Task context only. Customer must implement (cust_imu_*).
 *       hy_imu_init() calls this once per entity. Must be idempotent: the same
 *       SPI or I2C controller may be requested by several entities.
 *       If the application already initialized SPI/I2C and CS before
 *       hy_imu_init(), this function may return HY_IMU_OK without further work.
 *       hy_imu_resume() calls it again for every entity woken from
 *       HY_IMU_SUSPEND_POWER_OFF. If the MCU low-power mode resets the bus
 *       peripheral, either restore it before hy_imu_resume() or re-check the
 *       peripheral here; a plain "already initialised" flag is not enough.
 *
 * Sleep / wake wiring (application side, not called by the SDK):
 *  - POWER_OFF: after hy_imu_suspend(), drive SCLK / MOSI / CS low or Hi-Z
 *    before removing the IMU supply (no back-powering through IO), and disable
 *    the INT pin interrupt. On wake, power up, wait for the rail to settle,
 *    restore the bus, then call hy_imu_resume(). The SDK itself only waits
 *    3 ms after power-up.
 *  - SLEEP: the bus must work during hy_imu_suspend() and hy_imu_resume().
 *    In between the bus clock may be gated, but CS must stay deasserted.
 *    Disabling the INT pin interrupt is recommended (no DRDY while asleep).
 */
hy_imu_err_t cust_imu_bus_init(const hy_imu_bus_ctx_t *bus);

/**
 * @brief Read contiguous IMU register bytes.
 * @param[in]  bus  Non-NULL bus context; select CS / I2C address / controller.
 * @param[in]  reg  Logical start register address (SDK does not set SPI R/W bit).
 *                  4-wire SPI read: first byte on the wire must be (reg | 0x80).
 *                  I2C: use `reg` as the register pointer; do not OR 0x80.
 * @param[out] buf  Output buffer; must not be NULL when len > 0U.
 * @param[in]  len  Number of bytes to read.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  bus is NULL, or buf is NULL while len > 0U.
 * @retval HY_IMU_ERR_BUS    Transfer failed (NACK, timeout, etc.).
 * @note Task context only. Customer must implement (cust_imu_*).
 */
hy_imu_err_t cust_imu_read(const hy_imu_bus_ctx_t *bus, uint8_t reg, uint8_t *buf, uint32_t len);

/**
 * @brief Write contiguous IMU register bytes.
 * @param[in] bus  Non-NULL bus context; select CS / I2C address / controller.
 * @param[in] reg  Logical start register address (SDK does not set SPI R/W bit).
 *                 4-wire SPI write: first byte on the wire is `reg` (bit7 clear).
 *                 Do not OR 0x80 on writes.
 * @param[in] buf  Input buffer; must not be NULL when len > 0U.
 * @param[in] len  Number of bytes to write.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  bus is NULL, or buf is NULL while len > 0U.
 * @retval HY_IMU_ERR_BUS    Transfer failed (NACK, timeout, etc.).
 * @note Task context only. Customer must implement (cust_imu_*).
 */
hy_imu_err_t cust_imu_write(const hy_imu_bus_ctx_t *bus, uint8_t reg, const uint8_t *buf, uint32_t len);

/**
 * @brief Busy-wait or sleep for the requested delay.
 * @param[in] ms  Delay in milliseconds. Zero is a no-op.
 * @note May block. Customer must implement (cust_imu_*).
 */
void cust_imu_delay_ms(uint32_t ms);

/**
 * @brief Read the host clock used to stamp one acquisition sample.
 * @return Host time as a uint64_t. Unit and epoch are application-defined;
 *         the SDK forwards the value without conversion or interpretation.
 * @note Task context only. Customer must implement (cust_imu_*).
 *       hy_imu_read_sample_f32() calls this once immediately before the
 *       register read for that sample.
 */
uint64_t cust_imu_get_host_time(void);

#ifdef __cplusplus
}
#endif

#endif /* CUST_IMU_PLATFORM_H */
