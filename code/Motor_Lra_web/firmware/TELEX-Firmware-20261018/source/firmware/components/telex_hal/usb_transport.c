#include "transport.h"
#include "driver/usb_serial_jtag.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
static telex_rx_fn rx;
static telex_usb_decoder_t decoder;
static bool legacy;
static telex_legacy_rx_fn legacy_rx;
static uint8_t line[96];
static size_t line_used;
static bool line_overflow;
void telex_usb_set_legacy(bool enabled,telex_legacy_rx_fn callback){legacy=enabled;legacy_rx=callback;line_used=0;line_overflow=false;}
esp_err_t telex_usb_write_raw(const void*data,size_t n){return usb_serial_jtag_write_bytes(data,n,pdMS_TO_TICKS(5))==(int)n?ESP_OK:ESP_ERR_TIMEOUT;}
esp_err_t telex_usb_init(telex_rx_fn callback){
 usb_serial_jtag_driver_config_t cfg={.tx_buffer_size=2048,.rx_buffer_size=2048};
 esp_err_t err=usb_serial_jtag_driver_install(&cfg);if(err==ESP_OK)rx=callback;return err;
}
void telex_usb_poll(void){
 uint8_t buffer[256];int n=usb_serial_jtag_read_bytes(buffer,sizeof(buffer),0);
 for(int i=0;i<n;i++){
  if(legacy){
   if(buffer[i]=='\r'||buffer[i]=='\n'){
    if(line_used&&!line_overflow&&legacy_rx){line[line_used]=0;legacy_rx(line,line_used,TELEX_ROUTE_USB,(uint64_t)esp_timer_get_time());}
    line_used=0;line_overflow=false;
   }else if(!line_overflow){if(line_used<sizeof(line)-1)line[line_used++]=buffer[i];else line_overflow=true;}
  }else{telex_packet_t p;if(telex_usb_push(&decoder,buffer[i],&p)==1&&rx)rx(&p,TELEX_ROUTE_USB,(uint64_t)esp_timer_get_time());}
 }
}
esp_err_t telex_usb_send(const telex_packet_t*p){
 uint8_t data[252];size_t n;
 if(telex_encode_usb(p,data,sizeof(data),&n)!=TELEX_OK)return ESP_ERR_INVALID_ARG;
 int sent=usb_serial_jtag_write_bytes(data,n,pdMS_TO_TICKS(5));
 return sent==(int)n?ESP_OK:ESP_ERR_TIMEOUT;
}
