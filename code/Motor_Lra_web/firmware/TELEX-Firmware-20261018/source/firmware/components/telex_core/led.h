#pragma once
#include "identity.h"
typedef struct{bool fault,calibrating,wireless_selected,wireless_connected;uint64_t trigger_until_us;}telex_led_state_t;
typedef struct{uint8_t red,green,blue;}telex_led_levels_t;
telex_led_levels_t telex_led_render(telex_led_state_t,uint64_t now_us);
telex_led_levels_t telex_led_for_chip(telex_chip_t,telex_led_state_t,uint64_t now_us);
