#include "imu761_poll.h"
#include "imu761_protocol.h"
#include <stdio.h>
#include <string.h>

#define RX_CAPACITY 256u
#define BOOT_WAIT_MS 3000u
#define STARTUP_QUIET_MS 1000u
#define REPLY_TIMEOUT_MS 500u

static UART_HandleTypeDef *sensor;
static UART_HandleTypeDef *logger;
static uint8_t rx_byte;
static volatile uint8_t rx_head, rx_tail;
static uint8_t rx_ring[RX_CAPACITY];
static volatile uint32_t overflow_count, uart_error_count;
static imu761_parser_t parser;

static void log_text(const char *s) {
    if (logger != NULL) {
        HAL_UART_Transmit(logger, (uint8_t *)s, (uint16_t)strlen(s), 1000);
    }
}

static void log_stats(uint8_t address, const char *status, uint32_t received) {
    char line[160];
    snprintf(line, sizeof line,
             "ID=%02X %s rx=%lu crc_bad=%lu discard=%lu overflow=%lu uart_error=%lu\r\n",
             address, status, (unsigned long)received,
             (unsigned long)parser.crc_errors, (unsigned long)parser.discarded,
             (unsigned long)overflow_count, (unsigned long)uart_error_count);
    log_text(line);
}

static int pop_byte(uint8_t *value) {
    if (rx_head == rx_tail) return 0;
    *value = rx_ring[rx_tail++];
    return 1;
}

static int failure(void) {
    return overflow_count != 0 || uart_error_count != 0;
}

static void log_millidegrees(float value, char *dst, size_t capacity) {
    long milli = (long)(value * 1000.0f);
    unsigned long magnitude = (unsigned long)(milli < 0 ? -milli : milli);
    snprintf(dst, capacity, "%s%lu.%03lu", milli < 0 ? "-" : "",
             magnitude / 1000u, magnitude % 1000u);
}

static int query_one(uint8_t address) {
    uint8_t command[5];
    uint8_t incoming;
    uint32_t received = 0;
    uint32_t start;
    imu761_data_t data;
    char roll[20], pitch[20], yaw[20], line[180];

    if (rx_head != rx_tail) {
        log_stats(address, "FAIL: bytes present before query", received);
        return 0;
    }
    imu761_parser_reset(&parser);
    imu761_build_query(address, command);
    if (HAL_UART_Transmit(sensor, command, sizeof command, 100) != HAL_OK) {
        log_stats(address, "FAIL: transmit", received);
        return 0;
    }
    start = HAL_GetTick();
    while ((uint32_t)(HAL_GetTick() - start) < REPLY_TIMEOUT_MS) {
        if (failure()) {
            log_stats(address, "FAIL: UART/overflow", received);
            return 0;
        }
        if (!pop_byte(&incoming)) continue;
        ++received;
        if (!imu761_parser_feed(&parser, incoming, &data)) continue;
        if (data.address != address) {
            log_stats(address, "FAIL: wrong address", received);
            return 0;
        }
        log_millidegrees(data.roll_deg, roll, sizeof roll);
        log_millidegrees(data.pitch_deg, pitch, sizeof pitch);
        log_millidegrees(data.yaw_deg, yaw, sizeof yaw);
        snprintf(line, sizeof line,
                 "ID=%02X OK t=%lu roll=%s pitch=%s yaw=%s rx=%lu crc_bad=%lu\r\n",
                 address, (unsigned long)data.timestamp_ms, roll, pitch, yaw,
                 (unsigned long)received, (unsigned long)parser.crc_errors);
        log_text(line);
        return 1;
    }
    log_stats(address, "FAIL: timeout", received);
    return 0;
}

void imu761_poll_init(UART_HandleTypeDef *sensor_uart, UART_HandleTypeDef *log_uart) {
    sensor = sensor_uart;
    logger = log_uart;
    rx_head = rx_tail = 0;
    overflow_count = uart_error_count = 0;
    memset(&parser, 0, sizeof parser);
    if (HAL_UART_Receive_IT(sensor, &rx_byte, 1) != HAL_OK) {
        log_text("FAIL: cannot start USART1 RX interrupt\r\n");
        while (1) HAL_Delay(1000);
    }
}

void imu761_poll_run(void) {
    uint32_t start;
    uint8_t incoming;
    uint32_t unsolicited = 0;
    log_text("IMU761 STM32 query 01/02; both UARTs 115200 8N1\r\n");
    HAL_Delay(BOOT_WAIT_MS);
    while (pop_byte(&incoming)) ++unsolicited;
    if (unsolicited || failure()) {
        log_text("STOP: bytes/UART error during sensor boot; check query mode/wiring\r\n");
        while (1) HAL_Delay(1000);
    }
    start = HAL_GetTick();
    while ((uint32_t)(HAL_GetTick() - start) < STARTUP_QUIET_MS) {
        while (pop_byte(&incoming)) ++unsolicited;
        if (failure()) break;
    }
    if (unsolicited || failure()) {
        char line[100];
        snprintf(line, sizeof line, "STOP: unexpected startup bytes=%lu; check query mode/wiring\r\n",
                 (unsigned long)unsolicited);
        log_text(line);
        while (1) HAL_Delay(1000);
    }
    for (;;) {
        if (!query_one(0x01)) break;
        HAL_Delay(20);
        if (!query_one(0x02)) break;
        HAL_Delay(300);
    }
    log_text("STOP: reset STM32 after checking the fault\r\n");
    while (1) HAL_Delay(1000);
}

void imu761_poll_rx_callback(UART_HandleTypeDef *huart) {
    if (huart != sensor) return;
    uint8_t next = (uint8_t)(rx_head + 1u);
    if (next == rx_tail) ++overflow_count;
    else {
        rx_ring[rx_head] = rx_byte;
        rx_head = next;
    }
    if (HAL_UART_Receive_IT(sensor, &rx_byte, 1) != HAL_OK) ++uart_error_count;
}

void imu761_poll_error_callback(UART_HandleTypeDef *huart) {
    if (huart != sensor) return;
    ++uart_error_count;
    (void)HAL_UART_Receive_IT(sensor, &rx_byte, 1);
}
