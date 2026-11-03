#include "imu_hal.h"
#include "board_profile.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static i2c_master_bus_handle_t bus;
static i2c_master_dev_handle_t devices[11];
static int slot(uint8_t addr){for(unsigned i=0;i<11;i++)if(telex_imu_addresses[i]==addr)return (int)i;return -1;}
static bool read_regs(void*ctx,uint8_t addr,uint8_t reg,int16_t*out,uint8_t count){
 (void)ctx;int i=slot(addr);uint8_t raw[8];
 if(i<0||!devices[i]||count>4||!out)return false;
 if(i2c_master_transmit_receive(devices[i],&reg,1,raw,(size_t)count*2,5)!=ESP_OK)return false;
 for(unsigned n=0;n<count;n++)out[n]=(int16_t)(raw[n*2]|((uint16_t)raw[n*2+1]<<8));
 return true;
}
static uint64_t time_us(void*ctx){(void)ctx;return (uint64_t)esp_timer_get_time();}
esp_err_t telex_jy_init(telex_imu_source_t*out){
 if(!out||telex_board_chip()!=TELEX_S3)return ESP_ERR_NOT_SUPPORTED;
 telex_board_profile_t pins=telex_board_profile(TELEX_S3);
 i2c_master_bus_config_t cfg={.i2c_port=0,.sda_io_num=pins.lp_sda,.scl_io_num=pins.lp_scl,.clk_source=I2C_CLK_SRC_DEFAULT,.glitch_ignore_cnt=7,.flags.enable_internal_pullup=false};
 esp_err_t err=i2c_new_master_bus(&cfg,&bus);if(err!=ESP_OK)return err;
 gpio_set_level(1,0);vTaskDelay(pdMS_TO_TICKS(100));
 for(unsigned i=0;i<11;i++){
  i2c_device_config_t d={.dev_addr_length=I2C_ADDR_BIT_LEN_7,.device_address=telex_imu_addresses[i],.scl_speed_hz=400000};
  if((err=i2c_master_bus_add_device(bus,&d,&devices[i]))!=ESP_OK)return err;
 }
 *out=(telex_imu_source_t){read_regs,time_us,NULL,true};return ESP_OK;
}
bool telex_jy_probe(uint8_t addr){return bus&&slot(addr)>=0&&i2c_master_probe(bus,addr,5)==ESP_OK;}
bool telex_jy_write(uint8_t addr,uint8_t reg,uint16_t value){
 int i=slot(addr);if(i<0||!devices[i])return false;
 uint8_t raw[]={reg,(uint8_t)value,(uint8_t)(value>>8)};
 return i2c_master_transmit(devices[i],raw,sizeof(raw),5)==ESP_OK;
}
