#pragma once
#include "board.h"
#include "router.h"
typedef void (*telex_rx_fn)(const telex_packet_t*,telex_ingress_t,uint64_t);
esp_err_t telex_usb_init(telex_rx_fn callback);
void telex_usb_poll(void);
esp_err_t telex_usb_send(const telex_packet_t*packet);
esp_err_t telex_radio_init(const telex_settings_t*,telex_role_t,telex_rx_fn);
esp_err_t telex_radio_send(const telex_packet_t*);
void telex_radio_poll(void);

typedef void (*telex_legacy_rx_fn)(const uint8_t*,size_t,telex_ingress_t,uint64_t);
void telex_transport_legacy_handler(telex_legacy_rx_fn);
void telex_usb_set_legacy(bool enabled,telex_legacy_rx_fn);
esp_err_t telex_usb_write_raw(const void*,size_t);
esp_err_t telex_radio_send_raw(uint8_t target,const uint8_t*,size_t);

void telex_radio_flush(void);
