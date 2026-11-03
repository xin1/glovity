#pragma once
#include <stdint.h>
#include <stdbool.h>
typedef struct {
    uint16_t frequency_x10, rate_x10;
    uint8_t amplitude, envelope, depth, tracking;
} live_config_t;
bool live_config_valid(const live_config_t *c);
uint8_t live_amplitude(const live_config_t *c, uint64_t elapsed_us);
void live_ads_cycle(uint16_t frequency_x10, uint8_t amplitude, uint8_t out[10]);
void live_fifo_thresholds(uint16_t frequency_x10, uint16_t *ae, uint16_t *af);
