/**
 * @file    main.c
 * @brief   Single-MCU full SDK demo: multi-IMU read → calib → nav, sleep / wake.
 * @author  hanlm
 * @note    Documentation demo only. Not compiled into the SDK static library.
 *
 * Include path: common/include + acq/include + proc/include
 * Link:         libhy_imu_icm*.a  (or acq.a + proc.a)
 * Port:         implement cust_imu_* (acq/port/cust_imu_platform_template.c)
 *
 * Runtime order: read every IMU first (lower bus latency skew), then calib/nav.
 * Sleep (MCU keeps RAM): hy_imu_suspend → (optional power cut) → MCU sleep →
 * wake rail/bus → hy_imu_resume → hy_imu_proc_restart.
 * If the MCU resets during sleep, cold-start with init + set_factory_cal again.
 *
 * Build with HY_IMU_MAX_INSTANCES >= DEMO_IMU_COUNT for both acq and proc.
 */

#include "hy_imu_acq.h"
#include "hy_imu_proc.h"
#include "cust_imu_platform.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define DEMO_IMU_COUNT  (5U)
#define DEMO_SAMPLE_HZ  (1000.0f)

static const hy_imu_id_t s_ids[DEMO_IMU_COUNT] = {
    (hy_imu_id_t)0U,
    (hy_imu_id_t)1U,
    (hy_imu_id_t)2U,
    (hy_imu_id_t)3U,
    (hy_imu_id_t)4U
};

static uint32_t s_sample_count = 0U;

/* Customer: look up factory parameters for this chip (identity if unknown). */
extern bool app_lookup_factory_cal(const hy_imu_chip_id_t *chip_id,
                                   hy_imu_factory_cal_t *cal);

/* Customer: board / MCU power hooks. */
extern bool app_should_sleep(void);
extern void app_imu_power_off(void);   /* IO low/Hi-Z, INT off, rail off */
extern void app_imu_power_on(void);    /* rail on, wait for settle, bus ready */
extern void app_mcu_enter_sleep(void);
extern void app_wait_period(void);     /* pace the sample loop */

static void demo_build_spi_config(hy_imu_config_t *cfg)
{
    const hy_imu_config_t defaults = HY_IMU_CONFIG_DEFAULT;

    *cfg = defaults;
    cfg->serif       = HY_IMU_SERIF_SPI4;
    cfg->port_user   = NULL;
    cfg->accel_fsr   = HY_IMU_ACCEL_FSR_8G;
    cfg->gyro_fsr    = HY_IMU_GYRO_FSR_2000_DPS;
    cfg->accel_odr   = HY_IMU_ODR_1600_HZ;
    cfg->gyro_odr    = HY_IMU_ODR_1600_HZ;
    cfg->accel_mode  = HY_IMU_SENSOR_MODE_LN;
    cfg->gyro_mode   = HY_IMU_SENSOR_MODE_LN;
    cfg->accel_ln_bw = HY_IMU_LN_BW_ODR_DIV_4;
    cfg->gyro_ln_bw  = HY_IMU_LN_BW_ODR_DIV_4;
    cfg->sample_mode = HY_IMU_SAMPLE_MODE_POLLING;
}

static void demo_print_nav(hy_imu_id_t id, uint32_t index, const hy_imu_nav_f32_t *nav)
{
    (void)printf(
        "[id%u %lu] acc %.3f %.3f %.3f g | gyr %.2f %.2f %.2f dps | "
        "quat %.3f %.3f %.3f %.3f | rpy %.3f %.3f %.3f rad\r\n",
        (unsigned int)id,
        (unsigned long)index,
        (double)nav->accel_x_g,
        (double)nav->accel_y_g,
        (double)nav->accel_z_g,
        (double)nav->gyro_x_dps,
        (double)nav->gyro_y_dps,
        (double)nav->gyro_z_dps,
        (double)nav->quat_w,
        (double)nav->quat_x,
        (double)nav->quat_y,
        (double)nav->quat_z,
        (double)nav->roll_rad,
        (double)nav->pitch_rad,
        (double)nav->yaw_rad);
}

static void demo_on_1ms_task(void)
{
    hy_imu_sample_f32_t samples[DEMO_IMU_COUNT];
    hy_imu_err_t        read_err[DEMO_IMU_COUNT];
    hy_imu_sample_f32_t calib;
    hy_imu_nav_f32_t    nav;
    hy_imu_err_t        err;
    unsigned int        i;

    /* Phase 1: raw reads back to back so sample times stay close. */
    for (i = 0U; i < DEMO_IMU_COUNT; i++)
    {
        read_err[i] = hy_imu_read_sample_f32(s_ids[i], &samples[i]);
    }

    /* Phase 2: factory cal + algorithm, one entity at a time. */
    for (i = 0U; i < DEMO_IMU_COUNT; i++)
    {
        if (read_err[i] != HY_IMU_OK)
        {
            continue;
        }

        err = hy_imu_calib_f32(s_ids[i], &samples[i], &calib);
        if (err != HY_IMU_OK)
        {
            continue;
        }

        err = hy_imu_proc_nav_f32(s_ids[i], &calib, &nav);
        if (err != HY_IMU_OK)
        {
            continue;
        }

        if (i == 0U)
        {
            s_sample_count++;
        }
        demo_print_nav(s_ids[i], s_sample_count, &nav);
    }
}

static int demo_init(void)
{
    hy_imu_config_t     cfg;
    hy_imu_chip_id_t    chip_id;
    hy_imu_factory_cal_t cal;
    hy_imu_err_t        err;
    unsigned int        i;

    demo_build_spi_config(&cfg);

    for (i = 0U; i < DEMO_IMU_COUNT; i++)
    {
        err = hy_imu_init(s_ids[i], &cfg);
        if (err != HY_IMU_OK)
        {
            (void)printf("hy_imu_init(%u) failed: %ld\r\n",
                         (unsigned int)s_ids[i], (long)err);
            return (int)err;
        }

        err = hy_imu_proc_init(s_ids[i], DEMO_SAMPLE_HZ);
        if (err != HY_IMU_OK)
        {
            (void)printf("hy_imu_proc_init(%u) failed: %ld\r\n",
                         (unsigned int)s_ids[i], (long)err);
            return (int)err;
        }

        (void)memset(&chip_id, 0, sizeof(chip_id));
        err = hy_imu_get_chip_id(s_ids[i], &chip_id);
        if (err != HY_IMU_OK)
        {
            (void)printf("hy_imu_get_chip_id(%u) failed: %ld\r\n",
                         (unsigned int)s_ids[i], (long)err);
            return (int)err;
        }
        (void)printf("imu %u chip sn %s\r\n",
                     (unsigned int)s_ids[i], chip_id.str);

        if (app_lookup_factory_cal(&chip_id, &cal))
        {
            err = hy_imu_set_factory_cal(s_ids[i], &cal);
            if (err != HY_IMU_OK)
            {
                (void)printf("hy_imu_set_factory_cal(%u) failed: %ld\r\n",
                             (unsigned int)s_ids[i], (long)err);
                return (int)err;
            }
        }
    }

    return 0;
}

/**
 * @param power_off  true  = HY_IMU_SUSPEND_POWER_OFF (customer cuts the rail)
 *                   false = HY_IMU_SUSPEND_SLEEP (IMU stays powered)
 */
static void demo_sleep(bool power_off)
{
    hy_imu_err_t err;

    if (power_off)
    {
        (void)hy_imu_suspend(HY_IMU_SUSPEND_POWER_OFF);
        app_imu_power_off();
    }
    else
    {
        err = hy_imu_suspend(HY_IMU_SUSPEND_SLEEP);
        if (err != HY_IMU_OK)
        {
            (void)printf("suspend SLEEP: %ld\r\n", (long)err);
            return;
        }
    }

    app_mcu_enter_sleep();

    if (power_off)
    {
        app_imu_power_on();
    }

    /* Factory cal is kept. Attitude and gyro bias start over. */
    err = hy_imu_resume();
    if (err != HY_IMU_OK)
    {
        /* Failed entities stay suspended; retry later or re-init. */
        (void)printf("resume: %ld\r\n", (long)err);
    }

    err = hy_imu_proc_restart();
    if ((err != HY_IMU_OK) && (err != HY_IMU_ERR_STATE))
    {
        (void)printf("proc_restart: %ld\r\n", (long)err);
    }
}

int main(void)
{
    (void)printf("HY IMU SDK demo, acq %s / proc %s, %u IMUs\r\n",
                 hy_imu_get_version_string(),
                 hy_imu_proc_get_version_string(),
                 (unsigned int)DEMO_IMU_COUNT);

    if (demo_init() != 0)
    {
        return 1;
    }

    for (;;)
    {
        demo_on_1ms_task();

        if (app_should_sleep())
        {
            /* SLEEP keeps the rail; pass true for POWER_OFF. */
            demo_sleep(false);
        }

        app_wait_period();
    }
}
