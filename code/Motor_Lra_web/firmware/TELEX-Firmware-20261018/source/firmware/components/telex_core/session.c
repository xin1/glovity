#include "session.h"
#include <string.h>
#include <limits.h>
static telex_outcome_t reject(telex_reason_t reason){return (telex_outcome_t){TELEX_REJECTED,reason};}
static telex_outcome_t applied(void){return (telex_outcome_t){TELEX_APPLIED,TELEX_REASON_NONE};}
bool telex_sequence_newer(uint32_t candidate,uint32_t previous){uint32_t d=candidate-previous;return d!=0&&d<0x80000000u;}
void telex_session_init(telex_session_t*s,const telex_config_t*c,uint64_t nonce){
 memset(s,0,sizeof(*s));s->config=*c;s->boot_nonce=nonce;
}
void telex_session_revoke(telex_session_t*s){s->active=false;s->lease_until_us=0;memset(s->levels,0,sizeof(s->levels));}
bool telex_session_tick(telex_session_t*s,uint64_t now){
 if(s->active&&now>=s->lease_until_us){telex_session_revoke(s);return true;}return false;
}
telex_outcome_t telex_session_handle(telex_session_t*s,const telex_packet_t*p,telex_ingress_t ingress,uint32_t age,uint64_t now){
 if(!s||!p)return reject(TELEX_REASON_PAYLOAD);
 if(!telex_command_allowed(&s->config,p->kind))return reject(TELEX_REASON_MODE);
 if(ingress!=TELEX_ROUTE_USB&&ingress!=TELEX_ROUTE_RADIO)return reject(TELEX_REASON_OWNER);
 telex_session_tick(s,now);
 if(p->kind==0x22){
  if(p->payload_len>1||(p->payload_len==1&&p->payload[0]!=0))return reject(TELEX_REASON_PAYLOAD);
  /* Authenticated STOP is allowed on either path. It closes even a queued START. */
  if(p->session_id==0||p->session_id==s->high_session||s->high_session==0||telex_sequence_newer(p->session_id,s->high_session)){
   if(p->session_id)s->high_session=p->session_id;
   telex_session_revoke(s);return applied();
  }
  return reject(TELEX_REASON_SESSION);
 }
 if(p->kind==0x20){
  if((p->payload_len!=8&&p->payload_len!=9)||(p->payload_len==9&&p->payload[8]>1)||!p->session_id)return reject(TELEX_REASON_PAYLOAD);
  uint64_t nonce=0;for(unsigned i=0;i<8;i++)nonce|=(uint64_t)p->payload[i]<<(i*8);
  if(nonce!=s->boot_nonce)return reject(TELEX_REASON_SESSION);
  if(s->active&&p->session_id==s->session_id)return ingress==s->owner?applied():reject(TELEX_REASON_OWNER);
  if(s->active)return reject(TELEX_REASON_OWNER);
  if(s->high_session&&!telex_sequence_newer(p->session_id,s->high_session))return reject(TELEX_REASON_SESSION);
  if(age>150000)return reject(TELEX_REASON_EXPIRED);
  s->test_only=p->payload_len==9&&p->payload[8]==1;
  s->session_id=p->session_id;s->high_session=p->session_id;s->owner=ingress;
  s->have_update=false;s->have_action=false;s->active=true;s->lease_until_us=now+300000;
  memset(s->levels,0,sizeof(s->levels));return applied();
 }
 if(p->kind!=0x21&&p->kind!=0x23)return reject(TELEX_REASON_PAYLOAD);
 if(!s->active||s->session_id!=p->session_id)return reject(TELEX_REASON_SESSION);
 if(ingress!=s->owner)return reject(TELEX_REASON_OWNER);
 if(age>150000)return reject(TELEX_REASON_EXPIRED);
 uint32_t *seq=p->kind==0x21?&s->update_seq:&s->action_seq;
 bool *seen=p->kind==0x21?&s->have_update:&s->have_action;
 if(*seen&&!telex_sequence_newer(p->sequence,*seq))return reject(TELEX_REASON_DUPLICATE);
 if(p->kind==0x21){
  if(s->test_only)return reject(TELEX_REASON_MODE);
  if(p->payload_len!=5)return reject(TELEX_REASON_PAYLOAD);
  unsigned sum=0;for(unsigned i=0;i<5;i++){if(p->payload[i]>60)return reject(TELEX_REASON_BUDGET);sum+=(unsigned)p->payload[i]*p->payload[i];}
  if(sum>7200)return reject(TELEX_REASON_BUDGET);
  memcpy(s->levels,p->payload,5);s->lease_until_us=now+300000;
 }else{
  /* mask, preset, strength, duration_ms LE; execution belongs to the haptic manager. */
  if(p->payload_len!=5||!p->payload[0]||(p->payload[0]&~31u)||p->payload[1]>3||p->payload[2]>40)return reject(TELEX_REASON_PAYLOAD);
  unsigned duration=p->payload[3]|((unsigned)p->payload[4]<<8);
  if(!duration||duration>1000)return reject(TELEX_REASON_PAYLOAD);
 }
 *seq=p->sequence;*seen=true;return applied();
}
