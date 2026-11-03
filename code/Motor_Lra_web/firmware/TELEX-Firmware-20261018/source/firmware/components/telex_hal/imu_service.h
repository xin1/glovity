#pragma once
#include "imu_hal.h"
esp_err_t telex_imu_service_start(uint8_t profile,uint16_t rate_hz);
bool telex_imu_service_take(telex_imu_group_t*);
#include "calibration.h"
typedef struct {telex_cal_event_t event;telex_ingress_t ingress;} telex_cal_delivery_t;
bool telex_cal_service_request(telex_cal_operation_t,telex_ingress_t,uint32_t session,uint16_t sequence,bool motor_stop_confirmed);
bool telex_cal_service_take(telex_cal_delivery_t*);
bool telex_cal_service_inhibited(void);
bool telex_cal_service_active(void);
