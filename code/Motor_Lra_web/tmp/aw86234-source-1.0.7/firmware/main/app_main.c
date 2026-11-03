/**
 * @file  app_main.c
 * @brief 启动顺序（**不可变更**，见 `04_固件需求规格_ESP-IDF.md` §4）。
 *
 * ★ 第 1 步必须是把 TRIG 引脚置低 + 使能内部下拉：
 *   IO18~IO22 复位默认是"输入 + 内部弱上拉（约 45 kΩ）"，而本板 TRIG 线**没有外部下拉**，
 *   A1 会被抬到约 1 V，高于 AW8623X 的 VIH(min) = 0.8 V → 可能被误触发。
 *   这段窗口无法用软件消除，只能尽量缩短（已记录为已知风险 V-3）。
 */

#include <stdio.h>

#include "app_protocol.h"
#include "app_session.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "drv_i2c_bus.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "proto_cmd.h"
#include "svc_channel.h"
#include "svc_bootdemo.h"
#include "svc_measure.h"
#include "svc_play.h"
#include "svc_selftest.h"
#include "svc_sweep.h"
#include "util_log.h"

static inline uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

/** 安全任务（高优先级）：唯一被允许强制停机的任务。 */
static void safety_task(void *arg)
{
    (void)arg;
    while (true) {
        app_session_safety_tick(now_ms());
        vTaskDelay(pdMS_TO_TICKS(SAFETY_TICK_MS));
    }
}

/** 监视任务（低优先级）：F0 轮询、MON 采样、扫描、自检状态机。 */
static void mon_task(void *arg)
{
    (void)arg;
    while (true) {
        uint32_t now = now_ms();
        svc_measure_poll(now);
        svc_sweep_poll(now);
        svc_selftest_poll(now);
        app_session_set_active_mask((uint8_t)(svc_measure_active_mask() |
                                              (svc_sweep_active() ? 0x80u : 0u) |
                                              (svc_selftest_active() ? 0x40u : 0u)));
        vTaskDelay(pdMS_TO_TICKS(MON_TICK_MS));
    }
}

void app_main(void)
{
    /* ── 第 1 步：TRIG 引脚必须最先配置（上电误触发的唯一防线） ── */
    board_trig_safe_init();

    /* ── 第 2 步：基础设施 ── */
    esp_err_t nvs_err = nvs_flash_init();
    if (nvs_err == ESP_ERR_NVS_NO_FREE_PAGES || nvs_err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_err = nvs_flash_init();
    }
    if (nvs_err != ESP_OK) {
        ESP_LOGW("app", "NVS 初始化失败(%s)，安全参数将不持久化", esp_err_to_name(nvs_err));
    }
    proto_crc_init();
    app_session_init();

    /* ── 第 3 步：协议口 + ASCII 引导模式 ── */
    app_protocol_init();
    app_session_set_ascii_mode(true);

    /* ── 第 4 步：I2C 与器件 ── */
    svc_channel_init();
    svc_play_init();
    svc_play_trig_park();
    bus_info_t bi;
    if (bus_probe_auto(BUS_SEL_AUTO, &bi) == ESP_OK) {
        LOGI("I2C 就绪：%s（SDA=IO%u SCL=IO%u）@%u Hz",
             (bi.sel_used == BUS_SEL_LP) ? "LP" : "HP", bi.sda_gpio, bi.scl_gpio,
             (unsigned)bi.speed_hz);
    } else {
        /* 探测失败也要继续跑：ASCII/二进制模式仍可用，用户可手动 bus 命令重试 */
        LOGW("I2C 引脚自动探测失败，将等待上位机下发 BUS_PROBE 或手动 bus 命令");
    }
    svc_selftest_start();       /* 后台跑，结果经事件上报 */
    svc_bootdemo_start();       /* BOOT 键 → 全部通道硬触发（演示，可用宏关掉） */

    /* ── 第 5 步：任务（先起安全任务，最后起协议任务） ── */
    xTaskCreate(safety_task, "safety_task", TASK_STACK_SAFETY, NULL,
                TASK_PRIO_SAFETY, NULL);
    xTaskCreate(mon_task, "mon_task", TASK_STACK_MON, NULL, TASK_PRIO_MON, NULL);
    app_protocol_start_tasks();
}
