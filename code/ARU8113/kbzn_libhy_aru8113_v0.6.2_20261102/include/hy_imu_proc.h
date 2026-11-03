/**
 * @file    hy_imu_proc.h
 * @brief   Public API for HY IMU processing library (AHRS).
 * @author  hanlm
 * @note    Customer include path: common/include + proc/include.
 *          Shared types come from hy_imu_types.h (same guard as acq.h).
 *          Input is factory-calibrated product-frame hy_imu_sample_f32_t
 *          from hy_imu_calib_f32() (or an equivalent sample from another MCU).
 *          This library does not touch the IMU bus and does not need cust_imu_*.
 */

#ifndef HY_IMU_PROC_H
#define HY_IMU_PROC_H

#include "hy_imu_types.h"

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------- */
/* Library version (processing .a identity)                                   */
/* -------------------------------------------------------------------------- */

#define HY_IMU_PROC_VERSION_MAJOR   (0U)
#define HY_IMU_PROC_VERSION_MINOR   (6U)
#define HY_IMU_PROC_VERSION_PATCH   (2U)
#define HY_IMU_PROC_VERSION_STRING  "0.6.2"

/**
 * @brief Navigation snapshot: calibrated IMU plus AHRS quaternion and attitude.
 * @note Accel XYZ are copied from the factory-calibrated input (product frame).
 *       Gyro XYZ are the AHRS runtime-zeroed rates, converted back to dps.
 *       host_time is copied from the input sample (opaque host clock).
 *       Roll/pitch/yaw are in radians (same convention as internal AHRS fRpy).
 *       Call hy_imu_proc_nav_f32() at the rate passed to hy_imu_proc_init().
 */
typedef struct
{
    float accel_x_g;
    float accel_y_g;
    float accel_z_g;
    float gyro_x_dps;
    float gyro_y_dps;
    float gyro_z_dps;
    float temp_c;
    float quat_w;
    float quat_x;
    float quat_y;
    float quat_z;
    float roll_rad;
    float pitch_rad;
    float yaw_rad;
    uint8_t ahrs_init_state; /**< Internal AhrsInitStateType; 0=wait, 1=static, 2=dynamic */
    uint64_t host_time;      /**< Copied from the input sample; opaque host clock */
} hy_imu_nav_f32_t;

/* -------------------------------------------------------------------------- */
/* Version                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * @brief Get processing-library semantic version compiled into the static library.
 * @param[out] version  Non-NULL; filled with major/minor/patch on success.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  version is NULL.
 */
hy_imu_err_t hy_imu_proc_get_version(hy_imu_version_t *version);

/**
 * @brief Get processing-library version string (e.g. "0.6.1").
 * @return Pointer to a static NUL-terminated string; never NULL.
 */
const char *hy_imu_proc_get_version_string(void);

/* -------------------------------------------------------------------------- */
/* Lifecycle                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * @brief Initialise processing state for one entity (no bus access).
 * @param[in] id         Entity identifier (0 .. compiled slot count - 1).
 * @param[in] sample_hz  Expected call rate of nav (Hz). Must be > 0.
 * @retval HY_IMU_OK         Success.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or sample_hz is not finite / <= 0.
 * @retval HY_IMU_ERR_STATE  This entity is already initialised.
 * @note Task context only. Call once per entity before process APIs.
 *       Does not require hy_imu_init(); a second MCU can call this alone.
 *       Factory calibration lives in the acq library (hy_imu_calib_f32).
 */
hy_imu_err_t hy_imu_proc_init(hy_imu_id_t id, float sample_hz);

/**
 * @brief Clear processing state and close the algorithm for one entity.
 * @param[in] id  Entity identifier (0 .. compiled slot count - 1).
 * @retval HY_IMU_OK         Success; entity state and algorithm memory are cleared.
 * @retval HY_IMU_ERR_PARAM  id is out of range.
 * @retval HY_IMU_ERR_STATE  This entity is not initialised.
 * @note Task context only. After this call the entity may be re-initialised
 *       with hy_imu_proc_init(). Clears the entity's AHRS algorithm memory.
 *       With several initialised entities, resetting and re-initialising only
 *       one of them leaves it without valid algorithm configuration. To
 *       restart the algorithm (e.g. after sleep / wake) use
 *       hy_imu_proc_restart(), which handles all entities together.
 */
hy_imu_err_t hy_imu_proc_reset(hy_imu_id_t id);

/**
 * @brief Restart the algorithm for every initialised entity (no bus access).
 * @retval HY_IMU_OK         Success; every entity that was initialised is
 *                           initialised again from a clean state.
 * @retval HY_IMU_ERR_STATE  No entity is initialised; nothing was done.
 * @note Task context only. Same as hy_imu_proc_reset() then hy_imu_proc_init()
 *       on all initialised entities, with the sample_hz used at first init.
 *       Attitude, gyro bias and the static / dynamic init state start over.
 *       Call after the IMU wakes (hy_imu_resume()). Do not call nav/data
 *       APIs from another task while this runs.
 */
hy_imu_err_t hy_imu_proc_restart(void);

/* -------------------------------------------------------------------------- */
/* Runtime (consume a factory-calibrated product-frame float sample)          */
/* -------------------------------------------------------------------------- */

/**
 * @brief Run one AHRS step on a factory-calibrated sample.
 * @param[in]  id   Entity identifier.
 * @param[in]  in   Non-NULL factory-calibrated sample (from hy_imu_calib_f32 or IPC).
 * @param[out] out  Non-NULL; filled with g/dps/degC, quaternion and roll/pitch/yaw.
 * @retval HY_IMU_OK         Success; out contains a fresh snapshot.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or a pointer is NULL.
 * @retval HY_IMU_ERR_STATE  hy_imu_proc_init() has not completed for this id.
 * @note No bus access. Does not apply factory cal or mutate in.
 *       Accel/temp/host_time are copied from in. Gyro in out is the AHRS
 *       runtime-zeroed rate (dps). host_time is not converted.
 */
hy_imu_err_t hy_imu_proc_nav_f32(hy_imu_id_t id,
                                 const hy_imu_sample_f32_t *in,
                                 hy_imu_nav_f32_t *out);

/**
 * @brief Algorithm-layer data processing for one IMU sample.
 * @param[in]  id   Entity identifier.
 * @param[in]  in   Non-NULL factory-calibrated sample (from hy_imu_calib_f32 or IPC).
 * @param[out] out  Non-NULL; filled with processed IMU data (g / dps / degC).
 * @retval HY_IMU_OK         Success; out contains a fresh snapshot.
 * @retval HY_IMU_ERR_PARAM  id is out of range, or a pointer is NULL.
 * @retval HY_IMU_ERR_STATE  hy_imu_proc_init() has not completed for this id.
 * @note No bus access. Does not apply factory cal or mutate in.
 *       Does not fill attitude. Call this instead of hy_imu_proc_nav_f32()
 *       when quaternion and Euler angles are not required. Same call rate
 *       as hy_imu_proc_init(). Do not call both APIs on the same sample.
 */
hy_imu_err_t hy_imu_proc_data_f32(hy_imu_id_t id,
                                  const hy_imu_sample_f32_t *in,
                                  hy_imu_sample_f32_t *out);

#ifdef __cplusplus
}
#endif

#endif /* HY_IMU_PROC_H */
