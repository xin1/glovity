#include "imu_hal.h"
#include "board_profile.h"
#include "driver/uart.h"
static bool initialized;
esp_err_t telex_rion_uart_init(uint32_t baud){
 if(telex_board_chip()!=TELEX_S3||baud<1200||baud>2000000)return ESP_ERR_INVALID_ARG;
 telex_board_profile_t b=telex_board_profile(TELEX_S3);
 uart_config_t cfg={.baud_rate=(int)baud,.data_bits=UART_DATA_8_BITS,.parity=UART_PARITY_DISABLE,.stop_bits=UART_STOP_BITS_1,.flow_ctrl=UART_HW_FLOWCTRL_DISABLE,.source_clk=UART_SCLK_DEFAULT};
 for(int port=0;port<=1;port++){
  esp_err_t err=uart_driver_install(port,1024,0,0,NULL,0);if(err!=ESP_OK)return err;
  if((err=uart_param_config(port,&cfg))!=ESP_OK)return err;
  if((err=uart_set_pin(port,port?b.uart1_tx:b.uart0_tx,port?b.uart1_rx:b.uart0_rx,UART_PIN_NO_CHANGE,UART_PIN_NO_CHANGE))!=ESP_OK)return err;
 }
 initialized=true;return ESP_OK;
}
void telex_rion_uart_drain(void){
 if(!initialized)return;
 uint8_t raw[128];for(int port=0;port<=1;port++)(void)uart_read_bytes(port,raw,sizeof(raw),0);
 /* Framework only: no raw bytes are advertised as parsed sensor measurements. */
}
