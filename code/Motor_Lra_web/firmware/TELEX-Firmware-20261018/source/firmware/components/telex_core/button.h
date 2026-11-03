#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef enum{TELEX_BUTTON_NONE,TELEX_BUTTON_SHORT_RELEASE,TELEX_BUTTON_LONG_HOLD}telex_button_event_t;
typedef struct{bool armed,stable,raw,long_sent,initialized;uint64_t changed_us,pressed_us;uint32_t debounce_us,long_us;}telex_button_t;
void telex_button_init(telex_button_t*,uint32_t debounce_ms,uint32_t long_ms);
telex_button_event_t telex_button_update(telex_button_t*,bool pressed,uint64_t now_us);
