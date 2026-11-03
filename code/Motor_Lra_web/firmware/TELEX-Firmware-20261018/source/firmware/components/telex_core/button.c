#include "button.h"
#include <string.h>
void telex_button_init(telex_button_t*b,uint32_t d,uint32_t l){memset(b,0,sizeof(*b));b->debounce_us=d*1000;b->long_us=l*1000;}
telex_button_event_t telex_button_update(telex_button_t*b,bool pressed,uint64_t now){
 if(!b->initialized){b->initialized=true;b->raw=pressed;b->stable=pressed;b->changed_us=now;return TELEX_BUTTON_NONE;}
 if(pressed!=b->raw){b->raw=pressed;b->changed_us=now;}
 if(!b->armed){if(!pressed&&now-b->changed_us>=b->debounce_us){b->armed=true;b->stable=false;}return TELEX_BUTTON_NONE;}
 if(b->raw!=b->stable&&now-b->changed_us>=b->debounce_us){
  b->stable=b->raw;
  if(b->stable){b->pressed_us=b->changed_us;b->long_sent=false;}
  else{
   bool short_press=!b->long_sent&&b->changed_us-b->pressed_us<b->long_us;
   b->long_sent=false;return short_press?TELEX_BUTTON_SHORT_RELEASE:TELEX_BUTTON_NONE;
  }
 }
 if(b->stable&&pressed&&!b->long_sent&&now-b->pressed_us>=b->long_us){b->long_sent=true;return TELEX_BUTTON_LONG_HOLD;}
 return TELEX_BUTTON_NONE;
}
