#pragma once
#include "policy.h"
#define TELEX_SETTINGS_BYTES 96
typedef struct {uint8_t preset,mask,strength;uint16_t duration_ms;uint8_t active_level;bool key_verified;uint16_t debounce_ms,long_ms;} telex_trigger_config_t;
typedef struct {telex_trigger_config_t trigger;telex_config_t config;uint32_t generation;uint8_t uid[6];char suite_id[32];uint8_t peer_count;uint8_t peers[3][6];} telex_settings_t;
telex_result_t telex_settings_encode(const telex_settings_t*,uint8_t out[TELEX_SETTINGS_BYTES]);
telex_result_t telex_settings_decode(const uint8_t*,size_t,telex_settings_t*);
telex_result_t telex_settings_choose(const uint8_t*,size_t,const uint8_t*,size_t,telex_settings_t*);

telex_trigger_config_t telex_trigger_defaults(telex_trigger_config_t);
