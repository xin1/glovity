#pragma once
#include "config.h"
#include "identity.h"
#include "session.h"
typedef struct{bool timing,shutdown;uint64_t low_since_us;}telex_battery_guard_t;
bool telex_battery_update(telex_battery_guard_t*,bool voltage_valid,uint16_t mv,uint64_t now_us);
bool telex_application_fault(bool imu_expected,uint16_t imu_mask,bool haptics_enabled,uint8_t driver_faults);
bool telex_config_request(const telex_settings_t*,const uint8_t uid[6],telex_role_t,telex_ingress_t);
typedef struct{uint32_t high_session,session,sequence;telex_ingress_t owner;bool active;}telex_cal_gate_t;
telex_outcome_t telex_cal_gate(telex_cal_gate_t*,const telex_packet_t*,telex_ingress_t,uint64_t boot_nonce,uint32_t age_us);

telex_result_t telex_config_ack(const telex_settings_t*,uint8_t out[99]);

bool telex_diagnostic_echo(const telex_config_t*,const telex_packet_t*,telex_packet_t*);

typedef bool(*telex_cal_enqueue_t)(void*,const telex_packet_t*,telex_ingress_t);
telex_outcome_t telex_cal_dispatch(telex_cal_gate_t*,const telex_packet_t*,telex_ingress_t,uint64_t,uint32_t,telex_cal_enqueue_t,void*);
