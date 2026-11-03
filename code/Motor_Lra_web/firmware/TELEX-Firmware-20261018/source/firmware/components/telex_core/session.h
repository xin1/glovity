#pragma once
#include "policy.h"
typedef enum {TELEX_ROUTE_USB=1,TELEX_ROUTE_RADIO=2} telex_ingress_t;
typedef enum {TELEX_ACCEPTED,TELEX_APPLIED,TELEX_REJECTED,TELEX_UNKNOWN_RESULT} telex_outcome_state_t;
typedef enum {TELEX_REASON_NONE,TELEX_REASON_MODE,TELEX_REASON_SESSION,TELEX_REASON_EXPIRED,TELEX_REASON_DUPLICATE,TELEX_REASON_OWNER,TELEX_REASON_PAYLOAD,TELEX_REASON_BUDGET} telex_reason_t;
typedef struct {telex_outcome_state_t state;telex_reason_t reason;} telex_outcome_t;
typedef struct {
 telex_config_t config;
 uint64_t boot_nonce,lease_until_us;
 uint32_t high_session,session_id,update_seq,action_seq;
 bool active,have_update,have_action,test_only;
 telex_ingress_t owner;
 uint8_t levels[5];
} telex_session_t;
void telex_session_init(telex_session_t*,const telex_config_t*,uint64_t nonce);
telex_outcome_t telex_session_handle(telex_session_t*,const telex_packet_t*,telex_ingress_t,uint32_t max_source_age_us,uint64_t now_us);
bool telex_session_tick(telex_session_t*,uint64_t now_us);
void telex_session_revoke(telex_session_t*);
bool telex_sequence_newer(uint32_t candidate,uint32_t previous);
