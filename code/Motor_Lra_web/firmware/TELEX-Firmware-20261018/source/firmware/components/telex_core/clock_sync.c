#include "clock_sync.h"
#include <limits.h>
bool telex_clock_observe(telex_clock_t*c,uint64_t t0,uint64_t t1,uint64_t t2,uint64_t t3){
 if(!c)return false;
 c->valid=false;
 if(t3<t0||t2<t1||t3-t0<t2-t1||t3-t0>150000||t0>INT64_MAX/4||t1>INT64_MAX/4||t2>INT64_MAX/4||t3>INT64_MAX/4)return false;
 c->min_offset_us=(int64_t)t2-(int64_t)t3;c->max_offset_us=(int64_t)t1-(int64_t)t0;
 c->observed_us=t3;c->valid=true;return true;
}
bool telex_clock_max_age(const telex_clock_t*c,uint64_t timestamp,uint64_t now,uint32_t*age){
 if(!c||!age||!c->valid||now<c->observed_us||now-c->observed_us>5000000||now>INT64_MAX/4||timestamp>INT64_MAX/4)return false;
 int64_t worst=(int64_t)now-(int64_t)timestamp+c->max_offset_us;
 // Offset uncertainty may straddle zero for a fresh packet. Reject only
 // definitely future packets; the maximum age remains conservative.
 if(worst<0||worst>UINT32_MAX)return false;
 *age=(uint32_t)worst;return true;
}
bool telex_unwrap_ms(uint32_t timestamp,uint64_t remote_now,uint64_t*out){
 if(!out)return false;
 uint64_t now_ms=remote_now/1000;uint32_t current=(uint32_t)now_ms;
 uint32_t distance=current-timestamp;
 if(distance>=0x80000000u||now_ms<distance)return false;
 *out=(now_ms-distance)*1000;return true;
}

bool telex_clock_install_inverse(telex_clock_t*c,uint64_t t0,uint64_t t1,uint64_t t2,uint64_t t3,uint64_t now){
 if(!c)return false;
 c->valid=false;telex_clock_t forward={0};
 if(now<t2||now-t2>150000||!telex_clock_observe(&forward,t0,t1,t2,t3))return false;
 c->min_offset_us=-forward.max_offset_us;c->max_offset_us=-forward.min_offset_us;
 c->observed_us=now;c->valid=true;return true;
}

bool telex_clock_packet_age(const telex_clock_t*c,uint32_t t,uint64_t now,uint32_t*age){
 if(!c||!c->valid||!age||now>INT64_MAX/4)return false;
 int64_t remote_now=(int64_t)now+c->max_offset_us;
 if(remote_now<0)return false;
 uint64_t timestamp;uint32_t raw;
 if(!telex_unwrap_ms(t,(uint64_t)remote_now,&timestamp)||!telex_clock_max_age(c,timestamp,now,&raw))return false;
 // 1ms packet timestamp quantization and 200ppm relative clock-drift bound.
 uint64_t margin=1000+(now-c->observed_us)/5000;
 if((uint64_t)raw+margin>UINT32_MAX)return false;
 *age=raw+(uint32_t)margin;return true;
}
