#ifndef IMU761_PROTOCOL_H
#define IMU761_PROTOCOL_H

#include <stdint.h>

#define IMU761_FRAME_LEN 82u

typedef struct {
    uint8_t address;
    uint32_t timestamp_ms;
    float roll_deg;
    float pitch_deg;
    float yaw_deg;
} imu761_data_t;

typedef struct {
    uint8_t bytes[IMU761_FRAME_LEN];
    uint8_t used;
    uint32_t crc_errors;
    uint32_t discarded;
} imu761_parser_t;

void imu761_build_query(uint8_t address, uint8_t command[5]);
void imu761_parser_reset(imu761_parser_t *parser);
/* Returns 1 only for a complete CRC-valid HI91 frame. */
int imu761_parser_feed(imu761_parser_t *parser, uint8_t byte, imu761_data_t *out);

#endif
