/* BOOT triggers only explicitly armed, uploaded TRIG profiles.
 * No boot-time waveform generation and no implicit mode changes. */
#include "svc_bootdemo.h"
#include "cfg_board.h"
#include "svc_play.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "util_log.h"

static void boot_task(void *arg)
{
    (void)arg;
    int previous = gpio_get_level(BOARD_BOOT_BTN_GPIO);
    for (;;) {
        int current = gpio_get_level(BOARD_BOOT_BTN_GPIO);
        if (previous && !current) {
            vTaskDelay(pdMS_TO_TICKS(20));
            if (!gpio_get_level(BOARD_BOOT_BTN_GPIO)) {
                uint8_t started = 0;
                esp_err_t err = svc_play_boot_fire(&started);
                if (err == ESP_OK) { LOGI("BOOT：播放已上传波形，通道 0x%02X", started); }
                else { LOGW("BOOT 未触发：请在上位机下发 TRIG 并启用 BOOT（%d）", err); }
                while (!gpio_get_level(BOARD_BOOT_BTN_GPIO)) { vTaskDelay(pdMS_TO_TICKS(10)); }
            }
        }
        previous = current;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
void svc_bootdemo_start(void)
{
#if BOARD_BOOTDEMO_ENABLE
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << BOARD_BOOT_BTN_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    xTaskCreate(boot_task, "boot_trigger", 4096, NULL, 3, NULL);
#endif
}
