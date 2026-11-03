#pragma once
#include "session.h"
#define TELEX_MOTOR_ALL 31u
#define TELEX_GUI_CAP 40u
typedef struct {
 bool (*prepare)(void*,uint8_t);
 bool (*level)(void*,uint8_t,uint8_t);
 bool (*brake)(void*,uint8_t);
 bool (*clear_brake)(void*,uint8_t);
 bool (*stop)(void*,uint8_t);
 bool (*power_off)(void*);
 void *ctx;
 uint64_t (*now_us)(void*);
 bool (*finite)(void*,uint8_t,uint8_t,uint16_t);
 bool (*configure_finite)(void*,uint8_t,uint8_t,uint16_t);
} telex_haptic_io_t;
typedef struct {
 telex_haptic_io_t io;
 telex_session_t *session;
 uint8_t online_mask,fault_mask,actual[5],braking_mask;
 uint64_t brake_until[5],fresh_until_us,cue_start_us,click_until_us;
 uint8_t cue_count,click_mask,click_amp,click_preset;
 uint16_t click_duration_ms;
 uint64_t start_deadline_us,warm_until_us;
 bool starting_click,start_expired;
 bool inhibit,prepared,stopped,ending,stop_write_failed,ram_mode;
} telex_haptic_t;
const char *telex_motor_name(unsigned channel);
void telex_haptic_init(telex_haptic_t*,telex_session_t*,telex_haptic_io_t,uint8_t online);
telex_outcome_t telex_haptic_handle(telex_haptic_t*,const telex_packet_t*,telex_ingress_t,uint32_t age_us,uint64_t now_us);
telex_outcome_t telex_haptic_click(telex_haptic_t*,uint8_t mask,uint8_t preset,uint8_t strength,uint16_t duration_ms,uint64_t now_us);
bool telex_haptic_stop(telex_haptic_t*,bool normal,uint64_t now_us);
void telex_haptic_inhibit(telex_haptic_t*,bool inhibit,uint64_t now_us);
void telex_haptic_tick(telex_haptic_t*,uint64_t now_us);
