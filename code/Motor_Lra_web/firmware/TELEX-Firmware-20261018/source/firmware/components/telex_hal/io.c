#include "board.h"
#include "board_profile.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static void output(void*ctx,int pin,int level){
 esp_err_t *err=ctx;
 if(*err!=ESP_OK)return;
 *err=gpio_reset_pin(pin);if(*err!=ESP_OK)return;
 *err=gpio_set_level(pin,level);if(*err!=ESP_OK)return;
 *err=gpio_set_direction(pin,GPIO_MODE_OUTPUT);
}
esp_err_t telex_board_init_safe(void){
 esp_err_t err=ESP_OK;telex_safe_outputs(telex_board_chip(),output,&err);return err;
}
telex_role_t telex_board_read_role(void){
 int a,b;telex_board_strap_pins(&a,&b);
 gpio_config_t c={.pin_bit_mask=(1ULL<<a)|(1ULL<<b),.mode=GPIO_MODE_INPUT,.pull_up_en=GPIO_PULLUP_DISABLE,.pull_down_en=GPIO_PULLDOWN_DISABLE,.intr_type=GPIO_INTR_DISABLE};
 if(gpio_config(&c)!=ESP_OK)return TELEX_UNKNOWN;
 int av=gpio_get_level(a),bv=gpio_get_level(b);bool stable=true;
 for(int i=0;i<5;i++){vTaskDelay(pdMS_TO_TICKS(2));if(av!=gpio_get_level(a)||bv!=gpio_get_level(b))stable=false;}
 return telex_role_from_straps(telex_board_chip(),av,bv,stable);
}
