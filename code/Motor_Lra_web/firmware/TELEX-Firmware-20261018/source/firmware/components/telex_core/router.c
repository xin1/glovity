#include "router.h"
#include <string.h>
bool telex_route_receive(telex_router_t*r,const telex_packet_t*p,telex_ingress_t ingress,bool paired,uint64_t now){
 if(!r||!p||(ingress!=TELEX_ROUTE_USB&&ingress!=TELEX_ROUTE_RADIO))return false;
 if(ingress==TELEX_ROUTE_RADIO&&!paired)return false;
 telex_envelope_t item={.packet=*p,.ingress=ingress,.received_us=now};
 if(p->kind==0x22){
  if(p->target>3)return false;
  uint8_t bit=(uint8_t)(1u<<p->target);
  telex_envelope_t *previous=&r->stop[p->target];
  if(!(r->stop_pending&bit)||p->session_id==0||(previous->packet.session_id!=0&&telex_sequence_newer(p->session_id,previous->packet.session_id))){*previous=item;r->stop_pending|=bit;}
  return true;
 }
 if(p->kind==0x21||p->kind==0x10||p->kind==0x11){
  for(size_t i=0;i<r->count;i++){
   telex_envelope_t*old=&r->queue[i];
   if(old->ingress==ingress&&old->packet.kind==p->kind&&old->packet.session_id==p->session_id&&old->packet.source==p->source&&old->packet.target==p->target){
    if(!telex_sequence_newer(p->sequence,old->packet.sequence)){r->dropped++;return false;}
    *old=item;return true;
   }
  }
 }
 if(r->count==TELEX_QUEUE_CAPACITY){r->dropped++;return false;}
 r->queue[r->count++]=item;return true;
}
bool telex_route_pop(telex_router_t*r,telex_envelope_t*out){
 if(!r||!out)return false;
 for(unsigned i=0;i<4;i++){uint8_t bit=(uint8_t)(1u<<i);if(r->stop_pending&bit){*out=r->stop[i];r->stop_pending&=(uint8_t)~bit;return true;}}
 if(!r->count)return false;
 *out=r->queue[0];r->count--;memmove(r->queue,r->queue+1,r->count*sizeof(r->queue[0]));return true;
}
