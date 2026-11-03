#pragma once
#include "imu.h"
#include "session.h"
typedef enum {TELEX_CAL_STATUS=1,TELEX_CAL_START=2,TELEX_CAL_STOP=3,TELEX_CAL_ABORT=4} telex_cal_operation_t;
typedef struct {uint8_t type,flags;uint16_t sequence;uint32_t session_id,timestamp_ms;uint16_t active_mask;int16_t mag[11][3];bool switch_ok,save_ok,effect_verified;} telex_cal_event_t;
typedef struct {
 bool(*probe)(void*,uint8_t);
 bool(*write)(void*,uint8_t,uint8_t,uint16_t);
 bool(*read)(void*,uint8_t,uint8_t,int16_t*,uint8_t);
 void(*delay_ms)(void*,uint32_t);
 void(*emit)(void*,const telex_cal_event_t*);
 void*ctx;
} telex_cal_io_t;
typedef struct {
 telex_cal_io_t io;bool supported,active,remote,inhibit_haptics,switch_ok,save_ok;
 uint16_t active_mask,sequence;uint32_t session_id;
 uint64_t started_us,last_sample_us;
} telex_calibration_t;
void telex_calibration_init(telex_calibration_t*,telex_cal_io_t,bool supported);
telex_outcome_t telex_calibration_command(telex_calibration_t*,telex_cal_operation_t,telex_ingress_t,uint32_t session,uint16_t sequence,bool motor_stop_confirmed,uint64_t now_us);
void telex_calibration_tick(telex_calibration_t*,uint64_t now_us);
bool telex_calibration_active(const telex_calibration_t*);
