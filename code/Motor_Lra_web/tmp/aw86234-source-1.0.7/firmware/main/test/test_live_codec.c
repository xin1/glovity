#include <stdio.h>
#include <string.h>
#include "live_codec.h"
static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while(0)
int main(void) {
    live_config_t c={.frequency_x10=1680,.rate_x10=10,.amplitude=77,.envelope=0,.depth=100,.tracking=0};
    CHECK(live_config_valid(&c)); c.frequency_x10=599; CHECK(!live_config_valid(&c));
    c.frequency_x10=3001; CHECK(!live_config_valid(&c)); c.frequency_x10=1680;
    c.amplitude=78; CHECK(!live_config_valid(&c)); c.amplitude=77;
    c.tracking=1; c.frequency_x10=1700; CHECK(!live_config_valid(&c)); c.frequency_x10=1680;
    CHECK(live_config_valid(&c));
    uint8_t bytes[10]; live_ads_cycle(1680,77,bytes);
    const uint8_t expected[5]={0x05,0xA1,0xFF,0xF0,77};
    CHECK(memcmp(bytes,expected,5)==0); CHECK(memcmp(bytes,bytes+5,5)==0);
    for (int env=0;env<5;++env) {
        c.envelope=env;
        for (int t=0;t<10000000;t+=713) CHECK(live_amplitude(&c,t)<=77);
    }
    c.envelope=1; CHECK(live_amplitude(&c,0)==0); CHECK(live_amplitude(&c,500000)==77);
    c.envelope=2; CHECK(live_amplitude(&c,0)==77); CHECK(live_amplitude(&c,500000)==0);
    c.envelope=3; CHECK(live_amplitude(&c,300000)==77); CHECK(live_amplitude(&c,200000)==0);
    c.envelope=4; CHECK(live_amplitude(&c,0)==77); CHECK(live_amplitude(&c,900000)==0);
    c.depth=0; CHECK(live_amplitude(&c,900000)==77);
    for (int f=600;f<=3000;f+=1) {
        uint16_t ae,af; live_fifo_thresholds(f,&ae,&af);
        CHECK(ae<af && af<=200 && (double)(af+10)*1000/f<=100.0);
    }
    printf("Live codec: %d failed checks\n", failures);return failures?1:0;
}
