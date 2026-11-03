#pragma once
#include "legacy_wire.h"
#include "identity.h"
typedef struct {
 void(*usb)(void*,const uint8_t*,size_t);
 bool(*radio)(void*,uint8_t,const uint8_t*,size_t);
 bool(*cal)(void*,uint8_t,telex_ingress_t,uint32_t,uint16_t);
 void(*flush)(void*);
 void*ctx;
} telex_legacy_io_t;
typedef struct {telex_role_t role;telex_legacy_io_t io;bool maintenance,active;uint8_t hand;uint32_t session;uint16_t sequence;} telex_legacy_bridge_t;
bool telex_legacy_line(telex_legacy_bridge_t*,const char*);
void telex_legacy_event(telex_legacy_bridge_t*,const telex_cal_event_t*,uint8_t hand);
void telex_legacy_radio(telex_legacy_bridge_t*,const uint8_t*,size_t);
