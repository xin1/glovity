#ifndef IMU761_POLL_H
#define IMU761_POLL_H

#include "main.h"

void imu761_poll_init(UART_HandleTypeDef *sensor_uart, UART_HandleTypeDef *log_uart);
void imu761_poll_run(void);
void imu761_poll_rx_callback(UART_HandleTypeDef *huart);
void imu761_poll_error_callback(UART_HandleTypeDef *huart);

#endif
