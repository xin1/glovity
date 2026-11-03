#pragma once
#include "wire.h"
typedef struct {uint8_t mode,imu_profile;bool diagnostics_enabled,haptics_enabled;uint16_t group_rate_hz;} telex_config_t;
telex_result_t telex_validate_config(const telex_config_t*);
bool telex_command_allowed(const telex_config_t*,uint8_t);
