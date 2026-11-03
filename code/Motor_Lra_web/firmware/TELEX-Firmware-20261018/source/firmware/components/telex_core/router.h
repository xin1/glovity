#pragma once
#include "session.h"
#define TELEX_QUEUE_CAPACITY 8
typedef struct {telex_packet_t packet;telex_ingress_t ingress;uint64_t received_us;} telex_envelope_t;
typedef struct {telex_envelope_t queue[TELEX_QUEUE_CAPACITY],stop[4];size_t count;uint8_t stop_pending;uint32_t dropped;} telex_router_t;
bool telex_route_receive(telex_router_t*,const telex_packet_t*,telex_ingress_t,bool paired,uint64_t now_us);
bool telex_route_pop(telex_router_t*,telex_envelope_t*);
