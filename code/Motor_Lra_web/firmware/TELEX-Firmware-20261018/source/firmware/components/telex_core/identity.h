#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef enum {TELEX_S3,TELEX_C6} telex_chip_t;
typedef enum {TELEX_UNKNOWN=0,TELEX_LEFT=1,TELEX_RIGHT=2,TELEX_RECEIVER=3} telex_role_t;
telex_role_t telex_role_from_straps(telex_chip_t,uint8_t,uint8_t,bool);
uint8_t telex_battery_percent(bool battery_known,uint16_t voltage_mv);
