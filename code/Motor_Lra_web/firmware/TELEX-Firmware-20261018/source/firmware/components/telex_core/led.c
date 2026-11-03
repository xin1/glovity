#include "led.h"
telex_led_levels_t telex_led_render(telex_led_state_t s,uint64_t now){
 telex_led_levels_t v={0};
 if(s.fault)v.red=255;
 else if(s.calibrating)v.blue=(now/100000)%2?0:255;
 else if(now<s.trigger_until_us)v.red=220;
 else if(s.wireless_selected&&!s.wireless_connected)v.green=(now/100000)%2?0:255;
 else{uint32_t phase=(uint32_t)(now%2400000)/1000;uint32_t ramp=phase<1200?phase:2400-phase;v.blue=(uint8_t)(8+247*ramp*ramp/1440000);}
 return v;
}
telex_led_levels_t telex_led_for_chip(telex_chip_t chip,telex_led_state_t s,uint64_t now){
 telex_led_levels_t v=telex_led_render(s,now);
 if(chip==TELEX_C6){if(v.blue)v.green=v.blue;v.blue=0;}
 return v;
}
