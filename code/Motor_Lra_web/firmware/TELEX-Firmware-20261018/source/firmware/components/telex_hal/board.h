#pragma once
#include "esp_err.h"
#include "identity.h"
#include "config.h"
telex_chip_t telex_board_chip(void);
void telex_board_strap_pins(int *a,int *b);
esp_err_t telex_board_init_safe(void);
telex_role_t telex_board_read_role(void);
esp_err_t telex_config_load(telex_settings_t *out);
esp_err_t telex_config_save(const telex_settings_t *value);
