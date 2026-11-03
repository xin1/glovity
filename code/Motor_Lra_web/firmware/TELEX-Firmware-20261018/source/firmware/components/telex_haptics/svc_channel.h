#pragma once
#include "aw_profile.h"
#include "esp_err.h"
typedef struct {aw_profile_t profile;} ch_ctx_t;
ch_ctx_t *ch_ctx(uint8_t ch);
esp_err_t ch_xfer(uint8_t,const uint8_t*,size_t,uint8_t*,size_t,uint32_t);
