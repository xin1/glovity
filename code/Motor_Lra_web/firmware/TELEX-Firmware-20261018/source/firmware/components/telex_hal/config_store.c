#include "board.h"
#include "nvs.h"
#include <string.h>
static size_t read_slot(nvs_handle_t h,const char*key,uint8_t*out){size_t len=96;return nvs_get_blob(h,key,out,&len)==ESP_OK?len:0;}
esp_err_t telex_config_load(telex_settings_t*out){
 nvs_handle_t h;esp_err_t err=nvs_open("telex",NVS_READONLY,&h);if(err!=ESP_OK)return err;
 uint8_t a[96],b[96];size_t an=read_slot(h,"cfg0",a),bn=read_slot(h,"cfg1",b);nvs_close(h);
 telex_result_t r=telex_settings_choose(a,an,b,bn,out);
 return r==TELEX_OK?ESP_OK:r==TELEX_UNSUPPORTED?ESP_ERR_NOT_SUPPORTED:ESP_ERR_INVALID_CRC;
}
esp_err_t telex_config_save(const telex_settings_t*value){
 if(!value)return ESP_ERR_INVALID_ARG;
 telex_settings_t prior,next=*value;esp_err_t load=telex_config_load(&prior);
 if(load==ESP_ERR_NOT_SUPPORTED)return load;
 next.generation=load==ESP_OK?prior.generation+1:1;
 uint8_t data[96],verify[96];if(telex_settings_encode(&next,data)!=TELEX_OK)return ESP_ERR_INVALID_ARG;
 nvs_handle_t h;esp_err_t err=nvs_open("telex",NVS_READWRITE,&h);if(err!=ESP_OK)return err;
 const char*key=(next.generation&1)?"cfg1":"cfg0";
 err=nvs_set_blob(h,key,data,sizeof(data));if(err==ESP_OK)err=nvs_commit(h);
 if(err==ESP_OK&&(read_slot(h,key,verify)!=sizeof(data)||memcmp(data,verify,sizeof(data))))err=ESP_ERR_INVALID_CRC;
 nvs_close(h);return err;
}
