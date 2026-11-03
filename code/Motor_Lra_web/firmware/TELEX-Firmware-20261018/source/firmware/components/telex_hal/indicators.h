#pragma once
#include "board.h"
#include "led.h"
esp_err_t telex_indicators_init(void);
void telex_indicators_write(telex_led_levels_t);
int telex_key_level(void);
bool telex_battery_voltage(uint16_t *mv);
void telex_power_off(void);

typedef struct {int write_error;int duty[3];int green_io;int frequency;int green_low_samples;} telex_indicator_diagnostics_t;
telex_indicator_diagnostics_t telex_indicators_diagnostics(void);
