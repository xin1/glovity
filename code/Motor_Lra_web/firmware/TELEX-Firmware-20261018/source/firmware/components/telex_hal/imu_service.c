#include "imu_service.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <stdatomic.h>
static QueueHandle_t latest,commands,events;
static telex_imu_source_t source;
static uint32_t interval_us;
static telex_calibration_t calibration;
static atomic_bool inhibited,cal_active;
static telex_ingress_t event_route;
typedef struct {telex_cal_operation_t operation;telex_ingress_t route;uint32_t session;uint16_t sequence;bool stopped;} cal_request_t;
static bool probe(void*ctx,uint8_t addr){(void)ctx;return telex_jy_probe(addr);}
static bool write_reg(void*ctx,uint8_t addr,uint8_t reg,uint16_t value){(void)ctx;return telex_jy_write(addr,reg,value);}
static bool read_reg(void*ctx,uint8_t addr,uint8_t reg,int16_t*out,uint8_t n){(void)ctx;return source.read&&source.read(source.ctx,addr,reg,out,n);}
static void delay(void*ctx,uint32_t ms){(void)ctx;vTaskDelay(pdMS_TO_TICKS(ms));}
static void emit(void*ctx,const telex_cal_event_t*event){
 (void)ctx;telex_cal_delivery_t delivery={.event=*event,.ingress=event_route};
 delivery.event.timestamp_ms=(uint32_t)(esp_timer_get_time()/1000);
 if(xQueueSend(events,&delivery,0)!=pdTRUE&&event->type!=3){
  telex_cal_delivery_t discard;(void)xQueueReceive(events,&discard,0);(void)xQueueSend(events,&delivery,0);
 }
}
static void worker(void*unused){
 (void)unused;uint64_t next=(uint64_t)esp_timer_get_time();
 for(;;){
  cal_request_t request;
  if(xQueueReceive(commands,&request,0)==pdTRUE){
   event_route=request.route;
   telex_outcome_t result=telex_calibration_command(&calibration,request.operation,request.route,request.session,request.sequence,request.stopped,(uint64_t)esp_timer_get_time());
   if(request.operation==TELEX_CAL_START&&calibration.active)calibration.started_us=(uint64_t)esp_timer_get_time();
   if(result.state==TELEX_REJECTED&&!calibration.supported){
    telex_cal_event_t e={.type=5,.flags=5,.session_id=request.session,.sequence=request.sequence};emit(NULL,&e);
   }
   atomic_store(&inhibited,calibration.inhibit_haptics);atomic_store(&cal_active,calibration.active);
  }
  uint64_t now=(uint64_t)esp_timer_get_time();
  if(calibration.active){
   event_route=calibration.remote?TELEX_ROUTE_RADIO:TELEX_ROUTE_USB;telex_calibration_tick(&calibration,now);
   atomic_store(&inhibited,calibration.inhibit_haptics);atomic_store(&cal_active,calibration.active);
  }else if(now>=next){
   telex_imu_group_t group;
   if(telex_imu_poll(&source,now,&group))(void)xQueueOverwrite(latest,&group);
   next+=interval_us;now=(uint64_t)esp_timer_get_time();if(next<now)next=now+interval_us;
  }
  vTaskDelay(pdMS_TO_TICKS(1));
 }
}
esp_err_t telex_imu_service_start(uint8_t profile,uint16_t rate){
 if(rate<1||rate>100)return ESP_ERR_INVALID_ARG;
 if(profile!=0)telex_rion_stub(&source);
 else {esp_err_t err=telex_jy_init(&source);if(err!=ESP_OK)return err;}
 interval_us=1000000u/rate;
 latest=xQueueCreate(1,sizeof(telex_imu_group_t));commands=xQueueCreate(4,sizeof(cal_request_t));events=xQueueCreate(8,sizeof(telex_cal_delivery_t));
 if(!latest||!commands||!events)return ESP_ERR_NO_MEM;
 telex_cal_io_t io={probe,write_reg,read_reg,delay,emit,NULL};telex_calibration_init(&calibration,io,profile==0);
 return xTaskCreate(worker,"telex_imu",6144,NULL,4,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM;
}
bool telex_imu_service_take(telex_imu_group_t*out){return latest&&out&&!atomic_load(&cal_active)&&xQueueReceive(latest,out,0)==pdTRUE;}
bool telex_cal_service_request(telex_cal_operation_t op,telex_ingress_t route,uint32_t session,uint16_t seq,bool stopped){
 if(!commands)return false;
 cal_request_t request={op,route,session,seq,stopped};
 if(op==TELEX_CAL_START&&!stopped)return false;
 bool prior=atomic_load(&inhibited);
 if(op==TELEX_CAL_START)atomic_store(&inhibited,true);
 if(xQueueSend(commands,&request,0)!=pdTRUE){if(op==TELEX_CAL_START)atomic_store(&inhibited,prior);return false;}
 return true;
}
bool telex_cal_service_take(telex_cal_delivery_t*out){return events&&out&&xQueueReceive(events,out,0)==pdTRUE;}
bool telex_cal_service_inhibited(void){return atomic_load(&inhibited);}
bool telex_cal_service_active(void){return atomic_load(&cal_active);}
