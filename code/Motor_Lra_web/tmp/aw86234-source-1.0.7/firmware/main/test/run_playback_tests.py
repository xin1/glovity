"""Compile the actual playback service against a deterministic fake chip.

No serial access. Generated files stay alongside this script.
"""
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
main = here.parent
build = main.parent / 'build' / 'playback_tests'
build.mkdir(parents=True, exist_ok=True)
stubs = build / 'stubs'
headers = {
    'esp_err.h': '''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_NOT_FOUND 3
#define ESP_FAIL 4
#define ESP_ERR_NOT_SUPPORTED 5
''',
    'driver/gpio.h': '''#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef int gpio_num_t;
typedef struct { uint64_t pin_bit_mask; int mode, pull_up_en, pull_down_en, intr_type; } gpio_config_t;
#define GPIO_MODE_INPUT_OUTPUT 3
#define GPIO_PULLUP_DISABLE 0
#define GPIO_PULLDOWN_DISABLE 0
#define GPIO_PULLDOWN_ENABLE 1
#define GPIO_INTR_DISABLE 0
esp_err_t gpio_config(const gpio_config_t *);
esp_err_t gpio_set_level(gpio_num_t, uint32_t);
''' + '\n'.join(f'#define GPIO_NUM_{i} {i}' for i in range(24)),
    'esp_rom_sys.h': '#pragma once\nvoid esp_rom_delay_us(unsigned int);\n',
    'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
    'freertos/FreeRTOS.h': '#pragma once\n#include <assert.h>\n#define configASSERT(x) assert(x)\n#define portMAX_DELAY 0xffffffffu\n#define pdMS_TO_TICKS(ms) (ms)\n',
    'freertos/semphr.h': '''#pragma once
typedef int *SemaphoreHandle_t;
static int fake_mutex;
static inline SemaphoreHandle_t xSemaphoreCreateRecursiveMutex(void) { return &fake_mutex; }
static inline int xSemaphoreTakeRecursive(SemaphoreHandle_t m, unsigned int ticks) { (void)ticks; ++*m; return 1; }
static inline int xSemaphoreGiveRecursive(SemaphoreHandle_t m) { --*m; return 1; }
''',
    'freertos/task.h': '#pragma once\nvoid vTaskDelay(unsigned int);\n',
}
for name, content in headers.items():
    target = stubs / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
exe = build / 'test_playback.exe'
source = Path(sys.argv[1]) if len(sys.argv) > 1 else main / 'svc_play.c'
subprocess.run([sys.executable, '-m', 'ziglang', 'cc', '-std=c11', '-Wall',
                '-Wextra', '-Wno-unused-parameter', '-I', str(stubs), '-I', str(source.parent), '-I', str(main),
                str(here / 'test_playback.c'), str(source), '-o', str(exe)], check=True)
raise SystemExit(subprocess.run([str(exe)]).returncode)
