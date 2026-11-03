#include "haptics.h"
#include <string.h>
static telex_outcome_t outcome(telex_outcome_state_t state,telex_reason_t reason){return (telex_outcome_t){state,reason};}
const char *telex_motor_name(unsigned ch){static const char*names[]={"little","ring","middle","index","thumb"};return ch<5?names[ch]:"unknown";}
void telex_haptic_init(telex_haptic_t*h,telex_session_t*s,telex_haptic_io_t io,uint8_t online){
 memset(h,0,sizeof(*h));h->session=s;h->io=io;h->online_mask=online&31;h->stopped=true;
 h->fault_mask=s->config.haptics_enabled?(uint8_t)(31&~online):0;
}
static bool permitted(telex_haptic_t*h){return !h->inhibit&&telex_command_allowed(&h->session->config,0x20)&&h->online_mask;}
static bool prepare(telex_haptic_t*h){
 if(h->prepared)return true;
 if(!h->io.prepare||!h->io.prepare(h->io.ctx,h->online_mask)){h->fault_mask|=h->online_mask;return false;}
 h->prepared=true;return true;
}
bool telex_haptic_stop(telex_haptic_t*h,bool normal,uint64_t now){
 bool was_active=h->session->active;
 telex_session_revoke(h->session);h->cue_count=0;h->click_until_us=0;h->fresh_until_us=0;h->warm_until_us=0;h->ending=false;
 bool ok=true;h->stop_write_failed=false;
 if(h->prepared){
  for(unsigned ch=0;ch<5;ch++)if(h->online_mask&(1u<<ch)){
   bool stopped=false;for(unsigned n=0;n<3&&!stopped;n++)stopped=h->io.stop&&h->io.stop(h->io.ctx,(uint8_t)ch);
   if(!stopped){ok=false;h->fault_mask|=(uint8_t)(1u<<ch);}
  }
 }
 h->stop_write_failed=!ok;
 bool cue=normal&&was_active&&!h->session->test_only&&ok&&permitted(h);
 bool cut=!cue&&h->io.power_off&&h->io.power_off(h->io.ctx);
 h->stopped=ok||cut;if(!cue)h->prepared=false;h->ram_mode=false;h->braking_mask=0;memset(h->actual,0,5);
 if(cue){
  if(!prepare(h)){(void)telex_haptic_stop(h,false,now);return false;}
  if(h->io.now_us)now=h->io.now_us(h->io.ctx);
  h->cue_count=3;h->cue_start_us=now;h->ending=true;
 }
 return ok&&h->stopped;
}
void telex_haptic_inhibit(telex_haptic_t*h,bool inhibit,uint64_t now){if(inhibit&&!h->inhibit)(void)telex_haptic_stop(h,false,now);h->inhibit=inhibit;}
static bool failed(telex_haptic_t*h,unsigned ch,uint64_t now){if(!(h->starting_click&&h->start_expired))h->fault_mask|=(uint8_t)(1u<<ch);(void)telex_haptic_stop(h,false,now);return false;}
static bool output(telex_haptic_t*h,uint8_t ch,uint8_t amp,uint64_t now){
 if(h->ram_mode){
  if(h->io.now_us)now=h->io.now_us(h->io.ctx);
  if(h->starting_click&&(now>h->start_deadline_us||telex_session_tick(h->session,now))){h->start_expired=true;return false;}
  uint16_t ms=h->starting_click?h->click_duration_ms:(now<h->click_until_us?(uint16_t)((h->click_until_us-now+999)/1000):0);
  if(!h->io.finite||!h->io.finite(h->io.ctx,ch,amp,ms))return false;
  if(h->starting_click&&amp&&h->io.now_us){
   now=h->io.now_us(h->io.ctx);
   if(now>h->start_deadline_us||telex_session_tick(h->session,now)){h->start_expired=true;return false;}
   uint64_t end=now+(uint64_t)h->click_duration_ms*1000;
   if(end>h->click_until_us)h->click_until_us=end;
  }
  return true;
 }
 return h->io.level&&h->io.level(h->io.ctx,ch,amp);
}
static bool apply(telex_haptic_t*h,const uint8_t desired[5],bool ram,uint64_t now){
 bool nonzero=false;for(unsigned ch=0;ch<5;ch++)nonzero|=desired[ch]>0;
 if(!nonzero&&!h->prepared)return true;
 if(!prepare(h)){(void)telex_haptic_stop(h,false,now);return false;}
 // Stop every previous output before switching RAM/CONT, even at equal levels.
 if(ram!=h->ram_mode){
  for(unsigned ch=0;ch<5;ch++)if(h->actual[ch]||(h->braking_mask&(1u<<ch))){
   if(!h->io.stop(h->io.ctx,(uint8_t)ch))return failed(h,ch,now);
  }
  memset(h->actual,0,5);h->braking_mask=0;h->ram_mode=ram;
 }
 // Decrease before increase: energy budget holds at every physical I2C write.
 for(unsigned ch=0;ch<5;ch++)if(desired[ch]<h->actual[ch]){
  if(!desired[ch]){
   if(!h->io.brake(h->io.ctx,(uint8_t)ch))return failed(h,ch,now);
   h->braking_mask|=(uint8_t)(1u<<ch);h->brake_until[ch]=now+16000;
  }
  if(!output(h,(uint8_t)ch,desired[ch],now))return failed(h,ch,now);
  h->actual[ch]=desired[ch];
 }
 // Timer cleanup runs even when there is no subsequent rising update.
 for(unsigned ch=0;ch<5;ch++)if((h->braking_mask&(1u<<ch))&&now>=h->brake_until[ch]){
  if(!h->io.clear_brake(h->io.ctx,(uint8_t)ch))return failed(h,ch,now);
  h->braking_mask&=(uint8_t)~(1u<<ch);
 }
 for(unsigned ch=0;ch<5;ch++)if(desired[ch]>h->actual[ch]&&!(h->braking_mask&(1u<<ch))){
  if(!output(h,(uint8_t)ch,desired[ch],now))return failed(h,ch,now);
  h->actual[ch]=desired[ch];
 }
 h->stopped=!nonzero&&!h->braking_mask;return true;
}
void telex_haptic_tick(telex_haptic_t*h,uint64_t now){
 if(h->warm_until_us&&!h->session->active&&now>=h->warm_until_us){(void)telex_haptic_stop(h,false,now);return;}
 if(telex_session_tick(h->session,now)){(void)telex_haptic_stop(h,false,now);return;}
 if(h->inhibit){if(!h->stopped)(void)telex_haptic_stop(h,false,now);return;}
 uint8_t levels[5]={0};
 if(h->cue_count){
  uint64_t age=now-h->cue_start_us;
  if(age>=h->cue_count*100000u){h->cue_count=0;if(h->ending){h->ending=false;(void)telex_haptic_stop(h,false,now);return;}}
  else if(age%100000u<40000u)for(unsigned i=0;i<5;i++)if(h->online_mask&(1u<<i))levels[i]=24;
 }
 if(!h->cue_count){
  if(now<h->click_until_us){
   // Four bounded envelopes: short, double, soft, firm; selected channels only.
   uint64_t left=h->click_until_us-now;
   bool on=h->click_preset!=1||(left/40000u)%2==0;
   for(unsigned i=0;i<5;i++)if(on&&(h->click_mask&(1u<<i)))levels[i]=h->click_amp;
  }else if(h->session->active&&now<h->fresh_until_us)memcpy(levels,h->session->levels,5);
 }
 (void)apply(h,levels,!h->cue_count&&now<h->click_until_us,now);
}
static telex_outcome_t click(telex_haptic_t*h,uint8_t mask,uint8_t preset,uint8_t amp,uint16_t duration,uint64_t now,uint64_t deadline){
 if(!permitted(h))return outcome(TELEX_REJECTED,TELEX_REASON_MODE);
 if(!mask||(mask&~h->online_mask)||preset>3||amp>40||!duration||duration>1000)return outcome(TELEX_REJECTED,TELEX_REASON_PAYLOAD);
 unsigned sum=0;for(unsigned i=0;i<5;i++)if(mask&(1u<<i))sum+=amp*amp;
 if(sum>7200)return outcome(TELEX_REJECTED,TELEX_REASON_BUDGET);
 if(h->cue_count)return outcome(TELEX_REJECTED,TELEX_REASON_OWNER);
 h->start_expired=false;h->warm_until_us=0;
 // All RAM profile writes happen before the pulse timer and final freshness check.
 if(!prepare(h)){(void)telex_haptic_stop(h,false,now);return outcome(TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);}
 for(unsigned ch=0;ch<5;ch++)if(h->actual[ch]||(h->braking_mask&(1u<<ch))){
  if(!h->io.stop(h->io.ctx,(uint8_t)ch)){failed(h,ch,now);return outcome(TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);}
 }
 memset(h->actual,0,5);h->braking_mask=0;h->ram_mode=true;
 uint8_t effective=preset==2?(uint8_t)(amp/2):amp;
 if(!h->io.configure_finite||!h->io.configure_finite(h->io.ctx,mask,effective,duration)){
  h->fault_mask|=mask;
  (void)telex_haptic_stop(h,false,now);return outcome(TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);
 }
 if(h->io.now_us)now=h->io.now_us(h->io.ctx);
 if(now>deadline||telex_session_tick(h->session,now)){
  (void)telex_haptic_stop(h,false,now);return outcome(TELEX_REJECTED,TELEX_REASON_EXPIRED);
 }
 h->start_deadline_us=deadline;h->click_duration_ms=duration;h->starting_click=true;
 h->click_mask=mask;h->click_amp=preset==2?(uint8_t)(amp/2):amp;h->click_preset=preset;h->click_until_us=now+(uint64_t)duration*1000;
 telex_haptic_tick(h,now);h->starting_click=false;
 if(h->start_expired)return outcome(TELEX_REJECTED,TELEX_REASON_EXPIRED);
 return outcome(h->click_until_us?TELEX_APPLIED:TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);
}
telex_outcome_t telex_haptic_click(telex_haptic_t*h,uint8_t mask,uint8_t preset,uint8_t amp,uint16_t duration,uint64_t now){
 return click(h,mask,preset,amp,duration,now,UINT64_MAX);
}
telex_outcome_t telex_haptic_handle(telex_haptic_t*h,const telex_packet_t*p,telex_ingress_t route,uint32_t age,uint64_t now){
 if(p->kind!=0x22&&!permitted(h))return outcome(TELEX_REJECTED,TELEX_REASON_MODE);
 if(telex_session_tick(h->session,now))(void)telex_haptic_stop(h,false,now);
 // Purpose 2 only prepares stopped hardware. It cannot open a session or issue GO.
 if(p->kind==0x20&&p->payload_len==9&&p->payload[8]==2){
  if(h->session->active||!h->stopped||h->cue_count||h->click_until_us>now)return outcome(TELEX_REJECTED,TELEX_REASON_OWNER);
  telex_session_t checked=*h->session;telex_packet_t request=*p;request.payload[8]=1;
  telex_outcome_t valid=telex_session_handle(&checked,&request,route,age,now);
  if(valid.state!=TELEX_APPLIED)return valid;
  if(!prepare(h)){(void)telex_haptic_stop(h,false,now);return outcome(TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);}
  if(h->io.now_us)now=h->io.now_us(h->io.ctx);
  h->warm_until_us=now+1000000;return outcome(TELEX_APPLIED,TELEX_REASON_NONE);
 }
 bool active=h->session->active;
 if(p->kind==0x21&&p->payload_len==5)for(unsigned i=0;i<5;i++)if(p->payload[i]&&!(h->online_mask&(1u<<i)))return outcome(TELEX_REJECTED,TELEX_REASON_PAYLOAD);
 if(p->kind==0x23&&(h->cue_count||(p->payload[0]&~h->online_mask)))return outcome(TELEX_REJECTED,TELEX_REASON_OWNER);
 telex_outcome_t out=telex_session_handle(h->session,p,route,age,now);
 if(out.state!=TELEX_APPLIED)return out;
 if(p->kind==0x22){
  // Empty STOP is emergency. One zero byte explicitly requests normal end cues.
  h->session->active=active;
  bool ok=telex_haptic_stop(h,p->payload_len==1,now);
  return outcome(ok?TELEX_APPLIED:TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);
 }
 if(p->kind==0x20&&!active){
  h->warm_until_us=0;
  if(!h->session->test_only){
   uint64_t deadline=now+150000-age;
   if(!prepare(h)){(void)telex_haptic_stop(h,false,now);return outcome(TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);}
   if(h->io.now_us)now=h->io.now_us(h->io.ctx);
   if(now>deadline||telex_session_tick(h->session,now)){(void)telex_haptic_stop(h,false,now);return outcome(TELEX_REJECTED,TELEX_REASON_EXPIRED);}
  }
  // GLOVITY MOD 2026-10-19: Enter live output silently. The editor's requested
  // finger/level should be the first physical vibration; the former two-pulse
  // session cue made every new contact feel like an unintended double tap.
  h->cue_count=0;h->cue_start_us=now;h->ending=false;h->click_until_us=0;
 }
 if(p->kind==0x21)h->fresh_until_us=now+150000-age;
 if(p->kind==0x23)return click(h,p->payload[0],p->payload[1],p->payload[2],(uint16_t)(p->payload[3]|p->payload[4]<<8),now,now+150000-age);
 telex_haptic_tick(h,now);
 return h->session->active?out:outcome(TELEX_UNKNOWN_RESULT,TELEX_REASON_NONE);
}
