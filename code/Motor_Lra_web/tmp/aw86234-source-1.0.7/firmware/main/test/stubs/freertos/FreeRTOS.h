#pragma once
#include <assert.h>
#define configASSERT(x) assert(x)
#define portMAX_DELAY 0xffffffffu
#define pdMS_TO_TICKS(ms) (ms)
