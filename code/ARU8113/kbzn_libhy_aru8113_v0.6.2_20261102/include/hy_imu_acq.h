/**
 * @file    hy_imu_acq.h
 * @brief   Public API for HY IMU acquisition library (config, init, sample).
 * @author  hanlm
 * @note    Customer include path: common/include + acq/include.
 *          Shared types come from hy_imu_types.h (same guard as proc.h).
 *          Output is product-frame hy_imu_sample_f32_t; factory cal lives here.
 *          Attitude is in the proc library.
 */

#ifndef HY_IMU_ACQ_H
#define HY_IMU_ACQ_H

#include "hy_imu_types.h"

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------- */
/* Serial interface                                                           */
/* -------------------------------------------------------------------------- */

/**
 * @brief Host serial interface type used by the IMU UI port.
 */
typedef enum
{
    HY_IMU_SERIF_SPI4 = 0, /**< 4-wire SPI */
    HY_IMU_SERIF_SPI3 = 1, /**< 3-wire SPI */
    HY_IMU_SERIF_I2C  = 2  /**< I2C */
} hy_imu_serif_t;

/* -------------------------------------------------------------------------- */
/* Full-scale ranges                                                          */
/* -------------------------------------------------------------------------- */

/**
 * @brief Accelerometer full-scale range.
 */
typedef enum
{
    HY_IMU_ACCEL_FSR_2G  = 0,
    HY_IMU_ACCEL_FSR_4G  = 1,
    HY_IMU_ACCEL_FSR_8G  = 2,
    HY_IMU_ACCEL_FSR_16G = 3,
    HY_IMU_ACCEL_FSR_32G = 4
} hy_imu_accel_fsr_t;

/**
 * @brief Gyroscope full-scale range.
 */
typedef enum
{
    HY_IMU_GYRO_FSR_15_625_DPS = 0,
    HY_IMU_GYRO_FSR_31_25_DPS  = 1,
    HY_IMU_GYRO_FSR_62_5_DPS   = 2,
    HY_IMU_GYRO_FSR_125_DPS    = 3,
    HY_IMU_GYRO_FSR_250_DPS    = 4,
    HY_IMU_GYRO_FSR_500_DPS    = 5,
    HY_IMU_GYRO_FSR_1000_DPS   = 6,
    HY_IMU_GYRO_FSR_2000_DPS   = 7,
    HY_IMU_GYRO_FSR_4000_DPS   = 8
} hy_imu_gyro_fsr_t;

/* -------------------------------------------------------------------------- */
/* ODR                                                                        */
/* -------------------------------------------------------------------------- */

/**
 * @brief Output data rate (shared accel/gyro selector values).
 */
typedef enum
{
    HY_IMU_ODR_1_5625_HZ = 0,
    HY_IMU_ODR_3_125_HZ  = 1,
    HY_IMU_ODR_6_25_HZ   = 2,
    HY_IMU_ODR_12_5_HZ   = 3,
    HY_IMU_ODR_25_HZ     = 4,
    HY_IMU_ODR_50_HZ     = 5,
    HY_IMU_ODR_100_HZ    = 6,
    HY_IMU_ODR_200_HZ    = 7,
    HY_IMU_ODR_400_HZ    = 8,
    HY_IMU_ODR_800_HZ    = 9,
    HY_IMU_ODR_1600_HZ   = 10,
    HY_IMU_ODR_3200_HZ   = 11,
    HY_IMU_ODR_6400_HZ   = 12
} hy_imu_odr_t;

/* -------------------------------------------------------------------------- */
/* Sensor power / filter mode                                                 */
/* -------------------------------------------------------------------------- */

/**
 * @brief Sensor operating mode.
 */
typedef enum
{
    HY_IMU_SENSOR_MODE_OFF = 0, /**< Sensor off / standby */
    HY_IMU_SENSOR_MODE_LN  = 1, /**< Low-noise mode */
    HY_IMU_SENSOR_MODE_LP  = 2  /**< Low-power mode */
} hy_imu_sensor_mode_t;

/**
 * @brief Low-noise mode digital LPF bandwidth selector (device-mapped internally).
 * @note Values are the LPF bandwidth as a fraction of the configured ODR.
 *       NO_FILTER bypasses the UI low-pass filter.
 */
typedef enum
{
    HY_IMU_LN_BW_NO_FILTER   = 0,
    HY_IMU_LN_BW_ODR_DIV_4   = 1,
    HY_IMU_LN_BW_ODR_DIV_8   = 2,
    HY_IMU_LN_BW_ODR_DIV_16  = 3,
    HY_IMU_LN_BW_ODR_DIV_32  = 4,
    HY_IMU_LN_BW_ODR_DIV_64  = 5,
    HY_IMU_LN_BW_ODR_DIV_128 = 6
} hy_imu_ln_bw_t;

/**
 * @brief Low-power mode averaging selector (device-mapped internally).
 */
typedef enum
{
    HY_IMU_LP_AVG_1  = 0,
    HY_IMU_LP_AVG_2  = 1,
    HY_IMU_LP_AVG_4  = 2,
    HY_IMU_LP_AVG_8  = 3,
    HY_IMU_LP_AVG_16 = 4,
    HY_IMU_LP_AVG_32 = 5,
    HY_IMU_LP_AVG_64 = 6
} hy_imu_lp_avg_t;

/* -------------------------------------------------------------------------- */
/* Sample mode (config layout; public path uses HY_IMU_SAMPLE_MODE_POLLING)   */
/* -------------------------------------------------------------------------- */

typedef enum
{
    HY_IMU_INT_PIN_1 = 0,
    HY_IMU_INT_PIN_2 = 1
} hy_imu_int_pin_t;

typedef enum
{
    HY_IMU_INT_DRIVE_PUSH_PULL  = 0,
    HY_IMU_INT_DRIVE_OPEN_DRAIN = 1
} hy_imu_int_drive_t;

typedef enum
{
    HY_IMU_INT_LEVEL_ACTIVE_HIGH = 0,
    HY_IMU_INT_LEVEL_ACTIVE_LOW  = 1
} hy_imu_int_level_t;

typedef struct
{
    hy_imu_int_pin_t   pin;
    hy_imu_int_drive_t drive;
    hy_imu_int_level_t level;
    bool               ui_drdy;
} hy_imu_int_config_t;

typedef enum
{
    HY_IMU_SAMPLE_MODE_POLLING   = 0,
    HY_IMU_SAMPLE_MODE_INTERRUPT = 1
} hy_imu_sample_mode_t;

/* -------------------------------------------------------------------------- */
/* Aggregate runtime configuration                                            */
/* -------------------------------------------------------------------------- */

/**
 * @brief Bus routing passed to the customer port on every transfer.
 * @note SDK fills this from hy_imu_init(id, cfg). The port must not assume
 *       a single CS or a single I2C address.
 */
typedef struct
{
    hy_imu_id_t    id;         /**< Entity id of this transfer */
    hy_imu_serif_t serif;      /**< SPI4 / SPI3 / I2C */
    uint8_t        i2c_addr;   /**< 7-bit I2C address, per entity; ignored for SPI */
    void          *port_user;  /**< Optional customer cookie (CS, controller, ...) */
} hy_imu_bus_ctx_t;

/**
 * @brief Configuration applied by hy_imu_init() for one entity.
 * @note Product selection (ICM-45686 / ICM-56686) is fixed at library build time.
 *       Data path is register polling only (FIFO is out of scope).
 */
typedef struct
{
    hy_imu_serif_t       serif;             /**< Host interface type */
    uint8_t              i2c_addr;          /**< 7-bit I2C address, per entity; ignored for SPI */
    void                *port_user;         /**< Optional cookie forwarded to the port */
    hy_imu_accel_fsr_t   accel_fsr;         /**< Accel full-scale range */
    hy_imu_gyro_fsr_t    gyro_fsr;          /**< Gyro full-scale range */
    hy_imu_odr_t         accel_odr;         /**< Accel ODR */
    hy_imu_odr_t         gyro_odr;          /**< Gyro ODR */
    hy_imu_sensor_mode_t accel_mode;        /**< Accel mode enabled during hy_imu_init() */
    hy_imu_sensor_mode_t gyro_mode;         /**< Gyro mode enabled during hy_imu_init() */
    hy_imu_ln_bw_t       accel_ln_bw;       /**< Accel LN LPF bandwidth */
    hy_imu_ln_bw_t       gyro_ln_bw;        /**< Gyro LN LPF bandwidth */
    hy_imu_lp_avg_t      accel_lp_avg;      /**< Accel LP average */
    hy_imu_lp_avg_t      gyro_lp_avg;       /**< Gyro LP average */
    hy_imu_sample_mode_t sample_mode;
    hy_imu_int_config_t  int_cfg;
    uint16_t             discard_samples;   /**< Samples dropped after start. Default covers the
                                               *   35 ms typical gyro start-up (DS-000489 Table 1):
                                               *   each sample waits at least 1 ms. */
} hy_imu_config_t;

/**
 * @brief Safe default configuration (Accel +/-8 g, Gyro +/-2000 dps, 100 Hz, LN, polling).
 */
#define HY_IMU_CONFIG_DEFAULT                                                  \
    {                                                                          \
        HY_IMU_SERIF_SPI4,                   /* serif */                       \
        0x68U,                               /* i2c_addr */                    \
        NULL,                                /* port_user */                   \
        HY_IMU_ACCEL_FSR_8G,                 /* accel_fsr */                   \
        HY_IMU_GYRO_FSR_2000_DPS,            /* gyro_fsr */                    \
        HY_IMU_ODR_100_HZ,                   /* accel_odr */                   \
        HY_IMU_ODR_100_HZ,                   /* gyro_odr */                    \
        HY_IMU_SENSOR_MODE_LN,               /* accel_mode */                  \
        HY_IMU_SENSOR_MODE_LN,               /* gyro_mode */                   \
        HY_IMU_LN_BW_ODR_DIV_4,              /* accel_ln_bw */                 \
        HY_IMU_LN_BW_ODR_DIV_4,              /* gyro_ln_bw */                  \
        HY_IMU_LP_AVG_1,                     /* accel_lp_avg */                \
        HY_IMU_LP_AVG_1,                     /* gyro_lp_avg */                 \
        HY_IMU_SAMPLE_MODE_POLLING,          /* sample_mode */                 \
        {                                      /* int_cfg */                   \
            HY_IMU_INT_PIN_1,                /* pin */                         \
            HY_IMU_INT_DRIVE_PUSH_PULL,      /* drive */                       \
            HY_IMU_INT_LEVEL_ACTIVE_HIGH,    /* level */                       \
            true                             /* ui_drdy */                     \
        },                                                                     \
        40U                                  /* discard_samples: >= 35 ms gyro start-up */ \
    }

/* -------------------------------------------------------------------------- */
/* Library version (acquisition .a identity; not the chip firmware)           */
/* -------------------------------------------------------------------------- */

#define HY_IMU_VERSION_MAJOR   (0U)
#define HY_IMU_VERSION_MINOR   (6U)
#define HY_IMU_VERSION_PATCH   (1U)
#define HY_IMU_VERSION_STRING  "0.6.1"

/**
 * @brief 16-byte chip SN: 15 ASCII characters plus a terminating NUL.
 * @note Layout matches the protocol SN field:
 *       WHOAMI hex (2) + lot (6) + wafer_id (2) + die_id (5).
 *       Example: whoami 0xE9, lot "G6S191", wafer "01", die "11425"
 *       -> "E9G6S1910111425"
 */
#define HY_IMU_CHIP_ID_LEN  (16U)

typedef struct
{
    char str[HY_IMU_CHIP_ID_LEN];
} hy_imu_chip_id_t;

/**
 * @brief Factory calibration for one IMU entity.
 * @note Layout:
 *       gyro_temp / acce_temp: cubic poly [a3, a2, a1, a0] per axis;
 *       acce_cal / gyro_cal: 3x3 scale/misalign plus column-3 bias.
 *       Identity (output == input): temp all 0, cal = I|0.
 */
typedef struct
{
    float gyro_temp[3][4];
    float acce_temp[3][4];
    float acce_cal[3][4];
    float gyro_cal[3][4];
} hy_imu_factory_cal_t;

/* -------------------------------------------------------------------------- */
/* Version / identity                                                         */
/* -------------------------------------------------------------------------- */

/**
 * @brief Get acquisition-library semantic version compiled into the static library.
 * @param[out] version  Non-NULL; filled with major/minor/patch on success.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  version is NULL.
 */
hy_imu_err_t hy_imu_get_version(hy_imu_version_t *version);

/**
 * @brief Get acquisition-library version string (e.g. "0.6.1").
 * @return Pointer to a static NUL-terminated string; never NULL.
 */
const char *hy_imu_get_version_string(void);

/* -------------------------------------------------------------------------- */
/* Lifecycle                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * @brief Initialize one IMU entity and start sampling per cfg.
 * @param[in] id   Entity identifier (0 .. compiled slot count - 1).
 * @param[in] cfg  Non-NULL configuration; read-only for the duration of the call.
 * @retval HY_IMU_OK              Success; this entity is ready for data read APIs.
 * @retval HY_IMU_ERR_PARAM       id is out of range, cfg is NULL, or fields are invalid.
 * @retval HY_IMU_ERR_STATE       This entity is already initialized.
 * @retval HY_IMU_ERR_IO          Platform bus init failed.
 * @retval HY_IMU_ERR_BUS         Register access failed.
 * @retval HY_IMU_ERR_WHOAMI      Chip does not match this library.
 * @retval HY_IMU_ERR_TIMEOUT     Reset-complete wait timed out.
 * @retval HY_IMU_ERR_UNSUPPORTED Configuration exceeds product capability.
 * @retval HY_IMU_ERR_NOT_IMPL    Skeleton build without full implementation.
 * @note Task context only. Entities are independent. Internally performs bus
 *       init, soft-reset, WHOAMI, apply config, and enable sensors.
 *       Requires customer cust_imu_* at link time. Installs identity factory
 *       calibration; override with hy_imu_set_factory_cal(). Does not run AHRS;
 *       call hy_imu_proc_init() from the proc library for attitude.
 * @note FSR may be changed later via hy_imu_set_accel_fsr() /
 *       hy_imu_set_gyro_fsr() while RUNNING; other fields stay fixed after init.
 */
hy_imu_err_t hy_imu_init(hy_imu_id_t id, const hy_imu_config_t *cfg);

/**
 * @brief Read chip WHO_AM_I register value.
 * @param[in]  id      Entity identifier.
 * @param[out] whoami  Non-NULL; set to WHOAMI byte on success.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or whoami is NULL.
 * @retval HY_IMU_ERR_STATE  Not RUNNING (uninitialised, failed, or suspended).
 * @retval HY_IMU_ERR_BUS    Bus transfer failed.
 * @note Task context only.
 */
hy_imu_err_t hy_imu_get_whoami(hy_imu_id_t id, uint8_t *whoami);

/**
 * @brief Read chip SN string (WHOAMI hex + Lot + WaferID + DieID).
 * @param[in]  id       Entity identifier.
 * @param[out] chip_id  Non-NULL; chip_id->str is 15 ASCII chars plus NUL.
 * @retval HY_IMU_OK              Success.
 * @retval HY_IMU_ERR_PARAM       id is out of range, or chip_id is NULL.
 * @retval HY_IMU_ERR_STATE       Not RUNNING (uninitialised, failed, or suspended).
 * @retval HY_IMU_ERR_BUS         Bus / OTP access failed.
 * @retval HY_IMU_ERR_TIMEOUT     Bounded wait expired.
 * @retval HY_IMU_ERR_UNSUPPORTED Not supported on this compiled product.
 * @retval HY_IMU_ERR_INTERNAL    Decode / invariant failure.
 * @note Task context only. Reads WHOAMI and OTP, then formats the SN inside
 *       the SDK. Temporarily switches the chip clock to RCOSC and powers the
 *       OTP macro; both are restored before return. Avoid concurrent use with
 *       the sample hot path.
 */
hy_imu_err_t hy_imu_get_chip_id(hy_imu_id_t id, hy_imu_chip_id_t *chip_id);

/* -------------------------------------------------------------------------- */
/* Sleep / wake (all initialised entities together)                           */
/* -------------------------------------------------------------------------- */

/**
 * @brief How the IMUs are put to sleep by hy_imu_suspend().
 */
typedef enum
{
    HY_IMU_SUSPEND_POWER_OFF = 0, /**< IMU supply will be removed by the application */
    HY_IMU_SUSPEND_SLEEP     = 1  /**< IMU stays powered; accel and gyro turned off */
} hy_imu_suspend_mode_t;

/**
 * @brief Put every initialised IMU entity to sleep.
 * @param[in] mode  HY_IMU_SUSPEND_POWER_OFF or HY_IMU_SUSPEND_SLEEP.
 * @retval HY_IMU_OK         Every initialised entity is suspended.
 * @retval HY_IMU_ERR_PARAM  mode is invalid.
 * @retval HY_IMU_ERR_STATE  No entity has been initialised.
 * @retval HY_IMU_ERR_BUS    SLEEP only: turning one entity off failed; that
 *                           entity stays running, the others are suspended.
 * @note Task context only. Stop calling the sample APIs first.
 *       POWER_OFF: no bus access; remove the IMU supply after this returns.
 *       SLEEP: writes the power-mode register of each running entity, so the
 *       bus must be usable during the call; the IMU keeps its configuration.
 *       Calling POWER_OFF after SLEEP is allowed (IMU about to lose power).
 *       Configuration, runtime FSR changes and factory calibration are kept.
 *       While suspended the sample / calib / FSR / WHOAMI / chip-id / factory
 *       calibration APIs return HY_IMU_ERR_STATE.
 */
hy_imu_err_t hy_imu_suspend(hy_imu_suspend_mode_t mode);

/**
 * @brief Wake every suspended IMU entity and restart sampling.
 * @retval HY_IMU_OK         Every suspended entity is RUNNING again.
 * @retval HY_IMU_ERR_STATE  No entity has been initialised.
 * @retval other             First error seen; failed entities stay suspended
 *                           and the call may be repeated (running entities
 *                           are skipped).
 * @note Task context only. Before calling: after POWER_OFF, restore the IMU
 *       supply, wait for the rail to settle and restore SPI / I2C / CS; after
 *       SLEEP, make the bus usable again. POWER_OFF entities get the full
 *       hy_imu_init() bring-up (cust_imu_bus_init() is called again). SLEEP
 *       entities are checked first and only re-enabled; if their registers
 *       were lost the full bring-up is used instead. Factory calibration is
 *       kept. cfg->discard_samples startup samples are dropped before return.
 *       Attitude state is not touched: when the algorithm library is linked,
 *       also call hy_imu_proc_restart().
 */
hy_imu_err_t hy_imu_resume(void);

/* -------------------------------------------------------------------------- */
/* Runtime                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * @brief Read one uncorrected sample in physical units (sensor output as-is).
 * @param[in]  id   Entity identifier.
 * @param[out] out  Non-NULL float sample (g / dps / degC), product frame.
 *                  out->host_time is stamped from cust_imu_get_host_time()
 *                  immediately before the register read for this sample.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or out is NULL.
 * @retval HY_IMU_ERR_STATE  Not RUNNING.
 * @retval HY_IMU_ERR_BUS    Bus transfer failed.
 * @retval HY_IMU_ERR_NOT_IMPL Skeleton stub.
 * @note Task context only. Register counts are remapped into the product frame
 *       (X=chip Y, Y=-chip X, Z=chip Z) then scaled with the applied FSR /
 *       HiRes settings. No factory calibration is applied; call
 *       hy_imu_calib_f32() before hy_imu_proc_nav_f32().
 *       Requires customer cust_imu_get_host_time() at link time.
 */
hy_imu_err_t hy_imu_read_sample_f32(hy_imu_id_t id, hy_imu_sample_f32_t *out);

/* -------------------------------------------------------------------------- */
/* Factory calibration                                                        */
/* -------------------------------------------------------------------------- */

/**
 * @brief Load factory calibration used by hy_imu_calib_f32().
 * @param[in] id   Entity identifier.
 * @param[in] cal  Non-NULL calibration copy; applied immediately (RAM).
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or cal is NULL.
 * @retval HY_IMU_ERR_STATE  hy_imu_init() has not completed for this id.
 * @note Task context only. hy_imu_init() installs identity until this is called.
 */
hy_imu_err_t hy_imu_set_factory_cal(hy_imu_id_t id, const hy_imu_factory_cal_t *cal);

/**
 * @brief Read back the factory calibration currently stored for one entity.
 * @param[in]  id   Entity identifier.
 * @param[out] cal  Non-NULL; filled with the active calibration copy.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or cal is NULL.
 * @retval HY_IMU_ERR_STATE  hy_imu_init() has not completed for this id.
 * @note Task context only. Returns identity after init until
 *       hy_imu_set_factory_cal() overrides it.
 */
hy_imu_err_t hy_imu_get_factory_cal(hy_imu_id_t id, hy_imu_factory_cal_t *cal);

/**
 * @brief Apply factory calibration to one scaled sample (no runtime gyro-zero).
 * @param[in]  id   Entity identifier.
 * @param[in]  in   Non-NULL product-frame scaled sample from hy_imu_read_sample_f32().
 * @param[out] out  Non-NULL corrected sample; must not alias in.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or a pointer is NULL, or in==out.
 * @retval HY_IMU_ERR_STATE  Not RUNNING.
 * @note Task context only. No bus access. host_time is copied from in to out.
 *       Pass out to hy_imu_proc_nav_f32() as the calibrated input.
 */
hy_imu_err_t hy_imu_calib_f32(hy_imu_id_t id,
                              const hy_imu_sample_f32_t *in,
                              hy_imu_sample_f32_t *out);

/* -------------------------------------------------------------------------- */
/* Runtime FSR                                                                */
/* -------------------------------------------------------------------------- */

/**
 * @brief Change accelerometer full-scale range at runtime (RUNNING only).
 * @param[in] id   Entity identifier.
 * @param[in] fsr  Requested accel FSR enum.
 * @retval HY_IMU_OK              Success; scale factors refreshed.
 * @retval HY_IMU_ERR_PARAM       id is out of range.
 * @retval HY_IMU_ERR_STATE       Not RUNNING.
 * @retval HY_IMU_ERR_UNSUPPORTED FSR not supported on this product.
 * @retval HY_IMU_ERR_BUS         Register write failed.
 * @note Task context only. Updates cached LSB scale for subsequent reads.
 */
hy_imu_err_t hy_imu_set_accel_fsr(hy_imu_id_t id, hy_imu_accel_fsr_t fsr);

/**
 * @brief Change gyroscope full-scale range at runtime (RUNNING only).
 * @param[in] id   Entity identifier.
 * @param[in] fsr  Requested gyro FSR enum.
 * @retval HY_IMU_OK              Success; scale factors refreshed.
 * @retval HY_IMU_ERR_PARAM       id is out of range.
 * @retval HY_IMU_ERR_STATE       Not RUNNING.
 * @retval HY_IMU_ERR_UNSUPPORTED FSR not supported on this product.
 * @retval HY_IMU_ERR_BUS         Register write failed.
 * @note Task context only. Updates cached LSB scale for subsequent reads.
 */
hy_imu_err_t hy_imu_set_gyro_fsr(hy_imu_id_t id, hy_imu_gyro_fsr_t fsr);

#ifdef __cplusplus
}
#endif

#endif /* HY_IMU_ACQ_H */
