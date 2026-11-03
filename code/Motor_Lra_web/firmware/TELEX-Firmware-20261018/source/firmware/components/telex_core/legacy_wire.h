#pragma once
#include "imu.h"
telex_result_t telex_legacy_encode_usb(const telex_imu_group_t*,uint8_t hand,uint16_t frame_id,uint8_t out[168]);
telex_result_t telex_legacy_encode_radio(const telex_imu_group_t*,uint8_t hand,uint16_t frame_id,uint8_t out[174]);
telex_result_t telex_legacy_decode_radio(const uint8_t*,size_t,telex_imu_group_t*,uint8_t*hand,uint16_t*frame_id);
#include "calibration.h"
telex_result_t telex_cal_command_encode(uint8_t op,uint8_t hand,uint16_t sequence,uint32_t session,uint8_t out[15]);
telex_result_t telex_cal_command_decode(const uint8_t*,size_t,uint8_t*op,uint8_t*hand,uint16_t*sequence,uint32_t*session);
telex_result_t telex_cal_event_encode(const telex_cal_event_t*,uint8_t hand,uint8_t out[87]);
telex_result_t telex_cal_event_decode(const uint8_t*,size_t,telex_cal_event_t*,uint8_t*hand);
