#include "application.h"
#include <string.h>
bool telex_battery_update(telex_battery_guard_t*b,bool valid,uint16_t mv,uint64_t now){
 if(b->shutdown)return true;
 if(!valid||mv>=3500){b->timing=false;return false;}
 if(!b->timing){b->timing=true;b->low_since_us=now;}
 if(now-b->low_since_us>=8000000)b->shutdown=true;
 return b->shutdown;
}
bool telex_application_fault(bool imu_expected,uint16_t mask,bool haptics,uint8_t faults){return (imu_expected&&mask!=0x7ff)||(haptics&&faults);}
bool telex_config_request(const telex_settings_t*s,const uint8_t uid[6],telex_role_t role,telex_ingress_t route){
 if(!s||!uid||route!=TELEX_ROUTE_USB||role==TELEX_UNKNOWN||memcmp(s->uid,uid,6)||telex_validate_config(&s->config)!=TELEX_OK)return false;
 if(s->peer_count>(role==TELEX_RECEIVER?2:1))return false;
 for(unsigned i=0;i<s->peer_count;i++){
  static const uint8_t zero[6]={0};
  if((s->peers[i][0]&1)||!memcmp(s->peers[i],zero,6)||!memcmp(s->peers[i],uid,6))return false;
  for(unsigned j=0;j<i;j++)if(!memcmp(s->peers[i],s->peers[j],6))return false;
 }
 return role!=TELEX_RECEIVER||!s->config.haptics_enabled;
}
static telex_outcome_t reject(telex_reason_t reason){return (telex_outcome_t){TELEX_REJECTED,reason};}
telex_outcome_t telex_cal_gate(telex_cal_gate_t*g,const telex_packet_t*p,telex_ingress_t route,uint64_t boot,uint32_t age){
 if(!g||!p||p->kind!=0x30||p->sequence>65535||!p->payload_len)return reject(TELEX_REASON_PAYLOAD);
 uint8_t op=p->payload[0];
 if(op==1)return p->payload_len==1?(telex_outcome_t){TELEX_ACCEPTED,0}:reject(TELEX_REASON_PAYLOAD);
 if(!p->session_id)return reject(TELEX_REASON_SESSION);
 if(op==2){
  if(p->payload_len!=9)return reject(TELEX_REASON_PAYLOAD);
  uint64_t nonce=0;for(unsigned i=0;i<8;i++)nonce|=(uint64_t)p->payload[1+i]<<(i*8);
  if(nonce!=boot)return reject(TELEX_REASON_SESSION);
  if(g->active){
   if(g->session==p->session_id&&g->owner==route&&g->sequence==p->sequence)return (telex_outcome_t){TELEX_ACCEPTED,TELEX_REASON_DUPLICATE};
   return reject(TELEX_REASON_OWNER);
  }
  if(g->high_session&&!telex_sequence_newer(p->session_id,g->high_session))return reject(TELEX_REASON_SESSION);
  if(age>150000)return reject(TELEX_REASON_EXPIRED);
  g->high_session=g->session=p->session_id;g->sequence=p->sequence;g->owner=route;g->active=true;
  return (telex_outcome_t){TELEX_ACCEPTED,0};
 }
 if((op!=3&&op!=4)||p->payload_len!=1)return reject(TELEX_REASON_PAYLOAD);
 if(g->active&&g->owner!=route)return reject(TELEX_REASON_OWNER);
 if(g->active&&g->session==p->session_id){
  uint16_t delta=(uint16_t)(p->sequence-g->sequence);
  if(!delta||delta>=0x8000u)return reject(TELEX_REASON_DUPLICATE);
  g->active=false;g->sequence=p->sequence;return (telex_outcome_t){TELEX_ACCEPTED,0};
 }
 // A STOP that overtakes a queued START still closes that session.
 if(!g->high_session||telex_sequence_newer(p->session_id,g->high_session))g->high_session=p->session_id;
 return (telex_outcome_t){TELEX_APPLIED,TELEX_REASON_DUPLICATE};
}

telex_result_t telex_config_ack(const telex_settings_t*s,uint8_t out[99]){if(!out)return TELEX_INVALID;out[0]=3;out[1]=TELEX_APPLIED;out[2]=0;return telex_settings_encode(s,out+3);}

bool telex_diagnostic_echo(const telex_config_t*c,const telex_packet_t*p,telex_packet_t*out){
 if(!c||!p||!out||telex_validate_config(c)!=TELEX_OK||c->mode!=1||!c->diagnostics_enabled||p->kind!=0x41||p->payload_len>32)return false;
 *out=*p;return true;
}

telex_outcome_t telex_cal_dispatch(telex_cal_gate_t*g,const telex_packet_t*p,telex_ingress_t route,uint64_t boot,uint32_t age,telex_cal_enqueue_t enqueue,void*ctx){
 if(!g||!enqueue)return reject(TELEX_REASON_PAYLOAD);
 telex_cal_gate_t previous=*g;
 telex_outcome_t result=telex_cal_gate(g,p,route,boot,age);
 if(result.state==TELEX_ACCEPTED&&result.reason!=TELEX_REASON_DUPLICATE&&!enqueue(ctx,p,route)){
  *g=previous;return reject(TELEX_REASON_OWNER);
 }
 return result;
}
