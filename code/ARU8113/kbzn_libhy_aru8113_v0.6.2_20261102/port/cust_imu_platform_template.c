/**
 * @file    cust_imu_platform_template.c
 * @brief   Customer platform port template for HY IMU SDK (cust_imu_*).
 * @author  hanlm
 * @note    Copy this file into the customer project, rename as needed, and
 *          implement all cust_imu_* functions. Do NOT compile this template
 *          into the SDK .a.
 *
 * What the SDK passes vs what the wire needs (4-wire SPI, ICM-45686 / 56686):
 *
 *   cust_imu_read / cust_imu_write receive a LOGICAL register address
 *   (e.g. WHO_AM_I = 0x72) plus hy_imu_bus_ctx_t (id, serif, i2c_addr, port_user).
 *   The SDK never sets the SPI R/W bit.
 *
 *   Invensense 4-wire SPI address byte:
 *     bit7 = 1  -> read    first byte = (reg | 0x80)
 *     bit7 = 0  -> write   first byte =  reg
 *   Then `len` data bytes follow in the same CS frame.
 *   On read, the first received byte is a dummy; payload starts at rx[1].
 *
 *   I2C does not use 0x80; send the logical address as a register pointer
 *   and use bus->i2c_addr as the 7-bit slave address.
 *
 * Typical SPI frame (len = 2):
 *
 *   Read:  MOSI  [reg|0x80] [00] [00]
 *          MISO  [ xx     ] [d0] [d1]   -> copy d0..d1 into buf
 *
 *   Write: MOSI  [reg     ] [d0] [d1]
 *          MISO  ignored
 *
 *  - ISR: call hy_imu_irq_notify(id) only; never talk to the IMU bus.
 *  - Drive CS around each SPI transaction; idle CS high.
 *  - cust_imu_bus_init() is called once per entity and must be idempotent
 *    (same SPI/I2C controller may be requested several times).
 *  - hy_imu_resume() after HY_IMU_SUSPEND_POWER_OFF calls cust_imu_bus_init()
 *    again. If sleep resets the SPI/I2C peripheral, re-initialise it here (or
 *    before hy_imu_resume()); do not rely only on an "already done" flag.
 *  - POWER_OFF: before cutting the IMU supply drive SCLK/MOSI/CS low or Hi-Z
 *    and disable the INT interrupt; after power-up wait for the rail to settle.
 *  - SLEEP: keep CS deasserted while the bus clock is gated.
 */

#include "cust_imu_platform.h"

#include <stddef.h>
#include <string.h>

/** Invensense 4-wire SPI: set bit7 of the address byte to read. */
#define CUST_IMU_SPI_READ_BIT   (0x80U)

/** Address byte + payload. Keep large enough for the longest burst you use. */
#define CUST_IMU_SPI_MAX_XFER   (64U)

/**
 * @brief Select CS / controller for this entity (customer fills this in).
 * @param[in] bus  Non-NULL bus context (id and/or port_user).
 * @note Typical: GPIO CS from bus->id, or a cookie in bus->port_user.
 */
static void cust_imu_spi_cs_assert(const hy_imu_bus_ctx_t *bus)
{
    /* TODO: drive the CS for bus->id (or bus->port_user) low. */
    (void)bus;
}

/**
 * @brief Release CS after a SPI transaction (customer fills this in).
 * @param[in] bus  Non-NULL bus context.
 */
static void cust_imu_spi_cs_idle(const hy_imu_bus_ctx_t *bus)
{
    /* TODO: drive the CS for bus->id (or bus->port_user) high. */
    (void)bus;
}

/**
 * @brief Full-duplex SPI transfer of @p len bytes (customer fills this in).
 * @param[in]  bus  Non-NULL; select controller if several SPI units exist.
 * @param[in]  tx   Transmit buffer.
 * @param[out] rx   Receive buffer (may be ignored on write-only silicon).
 * @param[in]  len  Number of bytes (address + payload).
 * @return HY_IMU_OK or HY_IMU_ERR_BUS / HY_IMU_ERR_PARAM.
 */
static hy_imu_err_t cust_imu_spi_xfer(const hy_imu_bus_ctx_t *bus,
                                      const uint8_t *tx,
                                      uint8_t *rx,
                                      uint16_t len)
{
    hy_imu_err_t err = HY_IMU_OK;

    if ((bus == NULL) || (tx == NULL) || (rx == NULL) ||
        (len == 0U) || (len > CUST_IMU_SPI_MAX_XFER))
    {
        err = HY_IMU_ERR_PARAM;
    }
    else
    {
        /* TODO: CS low -> SPI full-duplex of `len` bytes -> CS high.
         *       Map driver timeout / NACK to HY_IMU_ERR_BUS.
         *       Typical: SPI Mode 0, CS idle high. */
        (void)tx;
        (void)rx;
        err = HY_IMU_ERR_NOT_IMPL;
    }

    return err;
}

/**
 * @brief I2C register burst (customer fills this in).
 * @param[in]  bus   Non-NULL; use bus->i2c_addr (7-bit) and bus->port_user.
 * @param[in]  reg   Logical register pointer; do not OR 0x80.
 * @param[in]  tx    Write payload, or NULL on read.
 * @param[out] rx    Read payload, or NULL on write.
 * @param[in]  len   Payload length.
 * @param[in]  is_read  Non-zero for read.
 */
static hy_imu_err_t cust_imu_i2c_xfer(const hy_imu_bus_ctx_t *bus,
                                      uint8_t reg,
                                      const uint8_t *tx,
                                      uint8_t *rx,
                                      uint32_t len,
                                      uint8_t is_read)
{
    hy_imu_err_t err = HY_IMU_OK;

    if ((bus == NULL) || (bus->i2c_addr == 0U) || (bus->i2c_addr > 0x7FU))
    {
        err = HY_IMU_ERR_PARAM;
    }
    else if ((len > 0U) && (((is_read != 0U) && (rx == NULL)) ||
                            ((is_read == 0U) && (tx == NULL))))
    {
        err = HY_IMU_ERR_PARAM;
    }
    else
    {
        /* TODO: I2C write: START, addr, reg, tx[0..len-1], STOP.
         *       I2C read:  START, addr, reg, RESTART, addr|R, rx[0..len-1], STOP.
         *       Do not OR 0x80 into `reg`. */
        (void)reg;
        (void)tx;
        (void)rx;
        (void)len;
        err = HY_IMU_ERR_NOT_IMPL;
    }

    return err;
}

hy_imu_err_t cust_imu_bus_init(const hy_imu_bus_ctx_t *bus)
{
    hy_imu_err_t err = HY_IMU_OK;

    if (bus == NULL)
    {
        err = HY_IMU_ERR_PARAM;
    }
    else
    {
        switch (bus->serif)
        {
            case HY_IMU_SERIF_SPI4:
            case HY_IMU_SERIF_SPI3:
            {
                /*
                 * Called once per entity. Same SPI controller may already be up.
                 * Option A: clocks / SPI / pins already brought up by the
                 *           application; return HY_IMU_OK (idempotent).
                 * Option B: initialise the controller on first call; later
                 *           entities sharing it return HY_IMU_OK immediately.
                 *           Base the check on the peripheral state, so a
                 *           controller reset by MCU sleep is set up again.
                 */
                break;
            }
            case HY_IMU_SERIF_I2C:
            {
                /* Same as SPI: initialise the I2C unit at most once. */
                break;
            }
            default:
            {
                err = HY_IMU_ERR_PARAM;
                break;
            }
        }
    }

    return err;
}

hy_imu_err_t cust_imu_read(const hy_imu_bus_ctx_t *bus, uint8_t reg, uint8_t *buf, uint32_t len)
{
    hy_imu_err_t err = HY_IMU_OK;
    uint8_t tx[CUST_IMU_SPI_MAX_XFER];
    uint8_t rx[CUST_IMU_SPI_MAX_XFER];
    uint16_t xfer_len;

    if (bus == NULL)
    {
        err = HY_IMU_ERR_PARAM;
    }
    else if ((len > 0U) && (buf == NULL))
    {
        err = HY_IMU_ERR_PARAM;
    }
    else if (bus->serif == HY_IMU_SERIF_I2C)
    {
        err = cust_imu_i2c_xfer(bus, reg, NULL, buf, len, 1U);
    }
    else if ((bus->serif != HY_IMU_SERIF_SPI4) && (bus->serif != HY_IMU_SERIF_SPI3))
    {
        err = HY_IMU_ERR_PARAM;
    }
    else if ((len + 1U) > CUST_IMU_SPI_MAX_XFER)
    {
        err = HY_IMU_ERR_PARAM;
    }
    else
    {
        xfer_len = (uint16_t)(len + 1U);
        (void)memset(tx, 0, xfer_len);
        (void)memset(rx, 0, xfer_len);

        /* Logical address from the SDK. 4-wire SPI must OR the read bit. */
        tx[0] = (uint8_t)(reg | CUST_IMU_SPI_READ_BIT);

        cust_imu_spi_cs_assert(bus);
        err = cust_imu_spi_xfer(bus, tx, rx, xfer_len);
        cust_imu_spi_cs_idle(bus);
        if ((err == HY_IMU_OK) && (len > 0U))
        {
            /* Skip the dummy byte clocked out while the address is sent. */
            (void)memcpy(buf, &rx[1], len);
        }
    }

    return err;
}

hy_imu_err_t cust_imu_write(const hy_imu_bus_ctx_t *bus, uint8_t reg, const uint8_t *buf, uint32_t len)
{
    hy_imu_err_t err = HY_IMU_OK;
    uint8_t tx[CUST_IMU_SPI_MAX_XFER];
    uint8_t rx[CUST_IMU_SPI_MAX_XFER];
    uint16_t xfer_len;

    if (bus == NULL)
    {
        err = HY_IMU_ERR_PARAM;
    }
    else if ((len > 0U) && (buf == NULL))
    {
        err = HY_IMU_ERR_PARAM;
    }
    else if (bus->serif == HY_IMU_SERIF_I2C)
    {
        err = cust_imu_i2c_xfer(bus, reg, buf, NULL, len, 0U);
    }
    else if ((bus->serif != HY_IMU_SERIF_SPI4) && (bus->serif != HY_IMU_SERIF_SPI3))
    {
        err = HY_IMU_ERR_PARAM;
    }
    else if ((len + 1U) > CUST_IMU_SPI_MAX_XFER)
    {
        err = HY_IMU_ERR_PARAM;
    }
    else
    {
        xfer_len = (uint16_t)(len + 1U);
        (void)memset(tx, 0, xfer_len);
        (void)memset(rx, 0, xfer_len);

        /* Logical address as-is. Do NOT set bit7 (that would turn this into a read). */
        tx[0] = reg;
        if (len > 0U)
        {
            (void)memcpy(&tx[1], buf, len);
        }

        cust_imu_spi_cs_assert(bus);
        err = cust_imu_spi_xfer(bus, tx, rx, xfer_len);
        cust_imu_spi_cs_idle(bus);
    }

    return err;
}

void cust_imu_delay_ms(uint32_t ms)
{
    if (ms > 0U)
    {
        /* TODO: RTOS / OS tick delay for `ms` milliseconds.
         *       Example: OSIF_TimeDelay(ms); */
        (void)ms;
    }
}

uint64_t cust_imu_get_host_time(void)
{
    /* TODO: return the host clock used to stamp hy_imu_read_sample_f32().
     *       Unit and epoch are application-defined; the SDK forwards the
     *       value without conversion. */
    return 0U;
}
