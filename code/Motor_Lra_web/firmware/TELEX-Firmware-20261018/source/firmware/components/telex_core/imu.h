#pragma once
#include "wire.h"
#define TELEX_IMU_COUNT 11
#define TELEX_IMU_ALL 0x7ffu
typedef struct {
 int16_t imu[11][7],wrist_gyro[3];
 uint16_t voltage_mv,valid_mask,read_mask;
 uint8_t battery_percent,status;
 uint64_t started_us,completed_us,read_at_us[11];
} telex_imu_group_t;
typedef bool(*telex_imu_read_fn)(void*,uint8_t addr,uint8_t reg,int16_t*out,uint8_t count);
typedef uint64_t(*telex_imu_time_fn)(void*);
typedef struct {telex_imu_read_fn read;telex_imu_time_fn time;void*ctx;bool implemented;} telex_imu_source_t;
extern const uint8_t telex_imu_addresses[11];
bool telex_imu_poll(telex_imu_source_t*,uint64_t now_us,telex_imu_group_t*);
telex_result_t telex_imu_encode(const telex_imu_group_t*,uint8_t out[168]);
int telex_rion_uart_for_id(unsigned id);
void telex_rion_stub(telex_imu_source_t*);
uint8_t telex_legacy_battery_percent(uint16_t voltage_mv);
