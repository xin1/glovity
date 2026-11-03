#include "indicators.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_rom_sys.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static adc_oneshot_unit_handle_t adc;
static adc_cali_handle_t cal;
static bool led_ready;
static esp_err_t led_write_error;
esp_err_t telex_indicators_init(void){
 bool hand=telex_board_chip()==TELEX_S3;
 ledc_timer_config_t timer={.speed_mode=LEDC_LOW_SPEED_MODE,.duty_resolution=LEDC_TIMER_8_BIT,.timer_num=LEDC_TIMER_0,.freq_hz=5000,.clk_cfg=LEDC_AUTO_CLK};
 esp_err_t err=ledc_timer_config(&timer);if(err!=ESP_OK)return err;
 int pins[3]={hand?13:6,hand?15:7,5};
 for(unsigned i=0;i<(hand?3u:2u);i++){
  ledc_channel_config_t channel={.gpio_num=pins[i],.speed_mode=LEDC_LOW_SPEED_MODE,.channel=(ledc_channel_t)i,.intr_type=LEDC_INTR_DISABLE,.timer_sel=LEDC_TIMER_0,.duty=hand?255:0};
  if((err=ledc_channel_config(&channel))!=ESP_OK)return err;
 }
 led_ready=true;
 if(!hand)return ESP_OK;
 gpio_config_t key={.pin_bit_mask=1ULL<<16,.mode=GPIO_MODE_INPUT,.pull_up_en=GPIO_PULLUP_DISABLE,.pull_down_en=GPIO_PULLDOWN_DISABLE,.intr_type=GPIO_INTR_DISABLE};
 if((err=gpio_config(&key))!=ESP_OK)return err;
 adc_oneshot_unit_init_cfg_t unit={.unit_id=ADC_UNIT_1,.ulp_mode=ADC_ULP_MODE_DISABLE};
 if((err=adc_oneshot_new_unit(&unit,&adc))!=ESP_OK)return err;
 adc_oneshot_chan_cfg_t cfg={.atten=ADC_ATTEN_DB_12,.bitwidth=ADC_BITWIDTH_DEFAULT};
 if((err=adc_oneshot_config_channel(adc,ADC_CHANNEL_1,&cfg))!=ESP_OK)return err;
#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
 adc_cali_curve_fitting_config_t calibration={.unit_id=ADC_UNIT_1,.chan=ADC_CHANNEL_1,.atten=ADC_ATTEN_DB_12,.bitwidth=ADC_BITWIDTH_DEFAULT};
 return adc_cali_create_scheme_curve_fitting(&calibration,&cal);
#else
 return ESP_ERR_NOT_SUPPORTED;
#endif
}
void telex_indicators_write(telex_led_levels_t v){
 if(!led_ready)return;
 bool hand=telex_board_chip()==TELEX_S3;uint8_t values[3]={v.red,v.green,v.blue};
 for(unsigned i=0;i<(hand?3u:2u);i++){esp_err_t err=ledc_set_duty(LEDC_LOW_SPEED_MODE,(ledc_channel_t)i,hand?255-values[i]:values[i]);if(err==ESP_OK)err=ledc_update_duty(LEDC_LOW_SPEED_MODE,(ledc_channel_t)i);if(err!=ESP_OK)led_write_error=err;}
}
int telex_key_level(void){return telex_board_chip()==TELEX_S3?gpio_get_level(16):-1;}
bool telex_battery_voltage(uint16_t*mv){
 if(!mv||!adc||!cal)return false;
 unsigned total=0;
 for(unsigned i=0;i<8;i++){int raw=0,value=0;if(adc_oneshot_read(adc,ADC_CHANNEL_1,&raw)!=ESP_OK||adc_cali_raw_to_voltage(cal,raw,&value)!=ESP_OK||value<0)return false;total+=(unsigned)value;vTaskDelay(pdMS_TO_TICKS(2));}
 // 10k above / 22k below: battery = ADC * 32/22. Presence remains unknown.
 *mv=(uint16_t)((total*32u+88u)/(8u*22u));return true;
}
void telex_power_off(void){if(telex_board_chip()==TELEX_S3){gpio_set_level(12,0);gpio_set_level(4,1);gpio_set_level(1,1);gpio_set_level(14,1);}}

telex_indicator_diagnostics_t telex_indicators_diagnostics(void){
 telex_indicator_diagnostics_t d={.write_error=led_write_error,.duty={-1,-1,-1},.green_io=-1,.frequency=-1,.green_low_samples=-1};
 if(led_ready)for(unsigned i=0;i<(telex_board_chip()==TELEX_S3?3u:2u);i++){
  uint32_t v=ledc_get_duty(LEDC_LOW_SPEED_MODE,(ledc_channel_t)i);d.duty[i]=v<=255?(int)v:-1;
 }
 if(led_ready){
  gpio_io_config_t io;
  if(gpio_get_io_config(telex_board_chip()==TELEX_S3?15:7,&io)==ESP_OK)
   d.green_io=(int)((io.sig_out&511u)|(io.oe?512u:0u)|(io.oe_ctrl_by_periph?1024u:0u)|((io.fun_sel&7u)<<11));
  int pin=telex_board_chip()==TELEX_S3?15:7;
  if(gpio_get_io_config(pin,&io)==ESP_OK&&gpio_input_enable(pin)==ESP_OK){
   d.green_low_samples=0;
   for(unsigned n=0;n<32;n++){if(!gpio_get_level(pin))d.green_low_samples++;esp_rom_delay_us(37);}
   // Keep the input monitor enabled; the LEDC output route is untouched.
  }
  uint32_t frequency=ledc_get_freq(LEDC_LOW_SPEED_MODE,LEDC_TIMER_0);d.frequency=frequency<=65535?(int)frequency:-1;
 }
 return d;
}
