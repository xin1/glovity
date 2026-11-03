#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define TELEX_MAX_PAYLOAD 224u
#define TELEX_MAX_PACKET 250u
typedef enum {TELEX_OK=0,TELEX_SIZE,TELEX_INVALID,TELEX_CRC,TELEX_UNSUPPORTED} telex_result_t;
typedef struct {
 uint8_t kind,flags,source,target;
 uint32_t session_id,sequence,timestamp_ms;
 uint16_t payload_len;
 uint8_t payload[TELEX_MAX_PAYLOAD];
} telex_packet_t;
typedef struct {uint8_t buffer[251];size_t used;bool discarding;uint32_t errors;} telex_usb_decoder_t;
uint16_t telex_crc16(const uint8_t *data,size_t len);
bool telex_kind_valid(uint8_t kind);
telex_result_t telex_encode(const telex_packet_t*,uint8_t*,size_t,size_t*);
telex_result_t telex_decode(const uint8_t*,size_t,telex_packet_t*);
telex_result_t telex_encode_usb(const telex_packet_t*,uint8_t*,size_t,size_t*);
int telex_usb_push(telex_usb_decoder_t*,uint8_t,telex_packet_t*);
