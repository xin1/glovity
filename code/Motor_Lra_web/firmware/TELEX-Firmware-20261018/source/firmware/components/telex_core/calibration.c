#include "calibration.h"
#include <string.h>
static telex_outcome_t result(bool ok,telex_reason_t reason){return (telex_outcome_t){ok?TELEX_APPLIED:TELEX_REJECTED,reason};}
static void event(telex_calibration_t*c,uint8_t type,uint8_t flags,uint64_t now){
 telex_cal_event_t e={.type=type,.flags=flags,.sequence=c->sequence,.session_id=c->session_id,.timestamp_ms=(uint32_t)(now/1000),.active_mask=c->active_mask,.switch_ok=c->switch_ok,.save_ok=c->save_ok,.effect_verified=false};
 if(type==3&&c->io.read){
  for(unsigned i=0;i<11;i++)if(e.active_mask&(1u<<i))if(!c->io.read(c->io.ctx,telex_imu_addresses[i],0x3a,e.mag[i],3))e.active_mask&=(uint16_t)~(1u<<i);
 }
 if(c->io.emit)c->io.emit(c->io.ctx,&e);
}
static void scan(telex_calibration_t*c){
 c->active_mask=0;for(unsigned i=0;i<11;i++)if(c->io.probe&&c->io.probe(c->io.ctx,telex_imu_addresses[i]))c->active_mask|=(uint16_t)(1u<<i);
}
static void unlock(telex_calibration_t*c){
 for(unsigned i=0;i<11;i++)if(c->active_mask&(1u<<i))(void)c->io.write(c->io.ctx,telex_imu_addresses[i],0x69,0xb588);
 c->io.delay_ms(c->io.ctx,100);
}
static bool set_switch(telex_calibration_t*c,uint16_t value){
 unlock(c);bool ok=true;
 for(unsigned i=0;i<11;i++)if(c->active_mask&(1u<<i))if(!c->io.write(c->io.ctx,telex_imu_addresses[i],1,value))ok=false;
 c->io.delay_ms(c->io.ctx,100);c->switch_ok=ok;return ok;
}
static bool save(telex_calibration_t*c){
 unlock(c);bool ok=true;
 for(unsigned i=0;i<11;i++)if(c->active_mask&(1u<<i))if(!c->io.write(c->io.ctx,telex_imu_addresses[i],0,0))ok=false;
 c->io.delay_ms(c->io.ctx,500);c->save_ok=ok;return ok;
}
void telex_calibration_init(telex_calibration_t*c,telex_cal_io_t io,bool supported){memset(c,0,sizeof(*c));c->io=io;c->supported=supported;}
telex_outcome_t telex_calibration_command(telex_calibration_t*c,telex_cal_operation_t op,telex_ingress_t route,uint32_t session,uint16_t seq,bool stopped,uint64_t now){
 if(!c||!c->supported||!c->io.write||!c->io.delay_ms)return result(false,TELEX_REASON_MODE);
 uint16_t previous_seq=c->sequence;uint32_t previous_session=c->session_id;
 c->sequence=seq;c->session_id=session;
 if(op==TELEX_CAL_STATUS){scan(c);event(c,1,c->active?1:0,now);c->sequence=previous_seq;c->session_id=previous_session;return result(true,TELEX_REASON_NONE);}
 if(op==TELEX_CAL_START){
  if(c->active){event(c,5,3,now);return result(false,TELEX_REASON_SESSION);}
  if(!stopped)return result(false,TELEX_REASON_OWNER);
  c->inhibit_haptics=true;scan(c);if(!c->active_mask){event(c,5,1,now);return result(false,TELEX_REASON_PAYLOAD);}
  c->remote=route==TELEX_ROUTE_RADIO;c->save_ok=false;
  bool ok=set_switch(c,7);
  if(!ok&&c->remote){event(c,5,1,now);return result(false,TELEX_REASON_PAYLOAD);}
  c->active=true;c->started_us=now;c->last_sample_us=0;event(c,2,0,now);return result(true,TELEX_REASON_NONE);
 }
 if(op==TELEX_CAL_ABORT&&route==TELEX_ROUTE_USB)return result(false,TELEX_REASON_MODE);
 if(op!=TELEX_CAL_STOP&&op!=TELEX_CAL_ABORT){event(c,5,5,now);return result(false,TELEX_REASON_PAYLOAD);}
 if(!c->active){event(c,5,2,now);return result(false,TELEX_REASON_SESSION);}
 bool cleared=set_switch(c,0);bool saved=op==TELEX_CAL_ABORT||save(c);
 /* Preserve good's unconditional SAVE after failed CALSW=0. */
 c->active=false;c->inhibit_haptics=!cleared;
 if(!cleared||!saved){event(c,c->remote?5:4,c->remote?4:0,now);return result(false,TELEX_REASON_PAYLOAD);}
 event(c,op==TELEX_CAL_ABORT?6:4,0,now);return result(true,TELEX_REASON_NONE);
}
void telex_calibration_tick(telex_calibration_t*c,uint64_t now){
 if(!c||!c->active)return;
 if(c->remote&&now-c->started_us>=60000000){
  bool cleared=set_switch(c,0);c->active=false;c->inhibit_haptics=!cleared;event(c,5,6,now);return;
 }
 if(now-c->last_sample_us>=500000){c->last_sample_us=now;event(c,3,0,now);}
}
bool telex_calibration_active(const telex_calibration_t*c){return c&&c->active;}
