#include "live_codec.h"
#include <math.h>
#include <string.h>
#define PI 3.14159265358979323846
bool live_config_valid(const live_config_t *c) {
    return c && c->frequency_x10 >= 600 && c->frequency_x10 <= 3000 &&
        c->amplitude <= 77 && c->envelope <= 4 && c->rate_x10 >= 1 &&
        c->rate_x10 <= 300 && c->depth <= 100 && c->tracking <= 1 &&
        (!c->tracking || c->frequency_x10 == 1680);
}
uint8_t live_amplitude(const live_config_t *c, uint64_t elapsed_us) {
    double phase = fmod((double)elapsed_us * c->rate_x10 / 10000000.0, 1.0);
    double level = 1.0;
    switch (c->envelope) {
    case 1: level = (1.0 - cos(2.0 * PI * phase)) * 0.5; break;
    case 2: level = phase < 0.25 ? 1.0 : 0.0; break;
    case 3: level = (phase < 0.12 || (phase >= 0.25 && phase < 0.37)) ? 1.0 : 0.0; break;
    case 4: level = exp(-8.0 * phase); break;
    default: break;
    }
    return (uint8_t)lround(c->amplitude * (1.0 - c->depth / 100.0 * (1.0 - level)));
}
void live_ads_cycle(uint16_t frequency_x10, uint8_t amplitude, uint8_t out[10]) {
    double angle = 2.0 * PI * frequency_x10 / 480000.0;
    uint16_t s = (uint16_t)floor(65536.0 * sin(angle));
    uint16_t c = (uint16_t)floor(65536.0 * cos(angle));
    out[0] = s >> 8; out[1] = s & 255;
    out[2] = c >> 8; out[3] = c & 255; out[4] = amplitude;
    memcpy(out + 5, out, 5);
}
void live_fifo_thresholds(uint16_t frequency_x10, uint16_t *ae, uint16_t *af) {
    /* Full cycles: roughly 35ms low water / 65ms high water, <=83ms at 60Hz. */
    *ae = (uint16_t)(((uint32_t)frequency_x10 * 35 + 9999) / 10000 * 10);
    *af = (uint16_t)(((uint32_t)frequency_x10 * 65 + 9999) / 10000 * 10);
}
