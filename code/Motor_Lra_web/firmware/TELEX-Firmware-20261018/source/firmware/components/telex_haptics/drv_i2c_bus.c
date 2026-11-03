#include "drv_i2c_bus.h"
#include "cfg_board.h"
#include "driver/i2c_master.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
static i2c_master_bus_handle_t bus;
static i2c_master_dev_handle_t mux,aw;
static SemaphoreHandle_t lock;
static ch_ctx_t channels[5];
ch_ctx_t *ch_ctx(uint8_t ch){return ch<5?&channels[ch]:NULL;}
bool telex_motor_power(bool on){
 if(gpio_set_level(BOARD_TRIG_GPIO_SHARED,0)!=ESP_OK)return false;
 if(gpio_set_level(BOARD_TCA_RST_GPIO,on?1:0)!=ESP_OK)return false;
 if(gpio_set_level(BOARD_MOTOR_POWER,on?0:1)!=ESP_OK)return false;
 if(on)vTaskDelay(pdMS_TO_TICKS(20));
 return true;
}
esp_err_t telex_motor_bus_init(void){
#if !CONFIG_IDF_TARGET_ESP32S3
 return ESP_ERR_NOT_SUPPORTED;
#else
 if(bus)return ESP_OK;
 lock=xSemaphoreCreateMutex();if(!lock)return ESP_ERR_NO_MEM;
 i2c_master_bus_config_t c={.i2c_port=1,.sda_io_num=BOARD_MOTOR_SDA,.scl_io_num=BOARD_MOTOR_SCL,.clk_source=I2C_CLK_SRC_DEFAULT,.glitch_ignore_cnt=7,.flags.enable_internal_pullup=false};
 esp_err_t err=i2c_new_master_bus(&c,&bus);if(err!=ESP_OK)return err;
 i2c_device_config_t d={.dev_addr_length=I2C_ADDR_BIT_LEN_7,.device_address=0x70,.scl_speed_hz=400000};
 err=i2c_master_bus_add_device(bus,&d,&mux);if(err!=ESP_OK)return err;
 d.device_address=0x5a;return i2c_master_bus_add_device(bus,&d,&aw);
#endif
}
esp_err_t ch_xfer(uint8_t ch,const uint8_t*wr,size_t wn,uint8_t*rd,size_t rn,uint32_t timeout){
 if(ch>=5||!bus||!mux||!aw||!wr||!wn)return ESP_ERR_INVALID_ARG;
 if(xSemaphoreTake(lock,pdMS_TO_TICKS(10))!=pdTRUE)return ESP_ERR_TIMEOUT;
 uint8_t selected=(uint8_t)(1u<<ch);
 esp_err_t err=i2c_master_transmit(mux,&selected,1,(int)timeout);
 if(err==ESP_OK)err=rn?i2c_master_transmit_receive(aw,wr,wn,rd,rn,(int)timeout):i2c_master_transmit(aw,wr,wn,(int)timeout);
 xSemaphoreGive(lock);return err;
}
