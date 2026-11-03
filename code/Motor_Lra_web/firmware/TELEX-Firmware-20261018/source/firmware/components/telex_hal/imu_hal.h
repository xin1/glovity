#pragma once
#include "board.h"
#include "imu.h"
esp_err_t telex_jy_init(telex_imu_source_t*);
bool telex_jy_probe(uint8_t addr);
bool telex_jy_write(uint8_t addr,uint8_t reg,uint16_t value);
esp_err_t telex_rion_uart_init(uint32_t baud);
void telex_rion_uart_drain(void);
