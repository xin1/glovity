/**
 * @file    hy_imu_types.h
 * @brief   Shared public types for HY IMU acq and proc libraries (header-only).
 * @author  hanlm
 * @note    Not a customer entry header. hy_imu_acq.h and hy_imu_proc.h include
 *          this file (same guard). Keep acq/proc include paths able to see it.
 *          Do not put .c in common/: both .a archives would export the same objects.
 */

#ifndef HY_IMU_SHARED_TYPES_H
#define HY_IMU_SHARED_TYPES_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------- */
/* Entity slots (compile-time; rebuild both libraries after changing)         */
/* -------------------------------------------------------------------------- */

/**
 * @brief Number of IMU entity slots reserved in BSS (acq and proc).
 * @note Override with -DHY_IMU_MAX_INSTANCES=N when compiling each library.
 *       Legal range is 1..13. Keep acq and proc builds matched on one MCU.
 */
#ifndef HY_IMU_MAX_INSTANCES
#define HY_IMU_MAX_INSTANCES  (11U)
#endif

#if (HY_IMU_MAX_INSTANCES < 1U) || (HY_IMU_MAX_INSTANCES > 13U)
#error "HY_IMU_MAX_INSTANCES must be in the range 1..13"
#endif

/**
 * @brief IMU entity identifier (0 .. compiled slot count - 1).
 */
typedef uint8_t hy_imu_id_t;

/* -------------------------------------------------------------------------- */
/* Error codes                                                                */
/* -------------------------------------------------------------------------- */

/**
 * @brief Fixed-width API status / error code.
 */
typedef int32_t hy_imu_err_t;

#define HY_IMU_OK                (0)   /**< Success */
#define HY_IMU_ERR_PARAM        (-1)   /**< NULL pointer, invalid enum, or length */
#define HY_IMU_ERR_STATE        (-2)   /**< Illegal state-machine transition */
#define HY_IMU_ERR_BUS          (-3)   /**< Platform bus transfer failure */
#define HY_IMU_ERR_WHOAMI       (-4)   /**< Chip WHOAMI does not match this library */
#define HY_IMU_ERR_TIMEOUT      (-5)   /**< Bounded wait expired */
#define HY_IMU_ERR_UNSUPPORTED  (-6)   /**< Config not supported on this product */
#define HY_IMU_ERR_IO           (-7)   /**< Platform IO setup failure */
#define HY_IMU_ERR_NOT_IMPL     (-8)   /**< Stub / not implemented yet */
#define HY_IMU_ERR_INTERNAL     (-9)   /**< Internal invariant violated */

/* -------------------------------------------------------------------------- */
/* Enable AHRS algorithm, [#define RUN_AHRS] is opened to enable AHRS algorithm default. */
/* -------------------------------------------------------------------------- */

#define RUN_AHRS

/* -------------------------------------------------------------------------- */
/* Version type (numeric fields; each library has its own get_version API)    */
/* -------------------------------------------------------------------------- */

/**
 * @brief Semantic version of one SDK static library.
 */
typedef struct
{
    uint8_t major;
    uint8_t minor;
    uint8_t patch;
} hy_imu_version_t;

/* -------------------------------------------------------------------------- */
/* Sample contract (product-frame physical units)                             */
/* -------------------------------------------------------------------------- */

/**
 * @brief One IMU sample in physical units (single-precision float).
 * @note This is the stable hand-off between acq and proc (and between two
 *       MCUs). XYZ are already in the product frame and scaled with the
 *       applied FSR. hy_imu_read_sample_f32() returns that scaled sample
 *       without factory cal. hy_imu_proc_nav_f32() expects the factory-
 *       calibrated sample from hy_imu_calib_f32() (or an equivalent IPC copy).
 *       host_time is the opaque uint64_t host clock stamped at acquisition
 *       (see cust_imu_get_host_time). Changing the layout requires a major
 *       version bump.
 *       Layout: chip Y -> product X, chip X rotated 180 deg -> product Y,
 *       chip Z unchanged.
 */
typedef struct
{
    float    accel_x_g;     /**< Accel X in g, product frame */
    float    accel_y_g;     /**< Accel Y in g, product frame */
    float    accel_z_g;     /**< Accel Z in g, product frame */
    float    gyro_x_dps;    /**< Gyro X in degrees per second, product frame */
    float    gyro_y_dps;    /**< Gyro Y in degrees per second, product frame */
    float    gyro_z_dps;    /**< Gyro Z in degrees per second, product frame */
    float    temp_c;        /**< Temperature in degrees Celsius */
    uint16_t timestamp;     /**< Device timestamp when available; else 0 */
    uint64_t host_time;     /**< Host clock from cust_imu_get_host_time(); opaque */
} hy_imu_sample_f32_t;

#ifdef __cplusplus
}
#endif

#endif /* HY_IMU_SHARED_TYPES_H */
