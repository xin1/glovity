#include "board.h"
#include "application.h"
#include "button.h"
#include "indicators.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "svc_play.h"
#include "transport.h"
#include "clock_sync.h"
#include "imu_service.h"
#include "legacy_wire.h"
#include "legacy_bridge.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include <stdio.h>
#include <string.h>
#include <inttypes.h>

static telex_router_t router;
static telex_role_t role;
static telex_settings_t settings;
static telex_session_t session;
static telex_haptic_t haptic;
static telex_button_t button;
static telex_cal_gate_t cal_gate;
static telex_led_state_t led;
static esp_err_t indicator_init_result;
static telex_battery_guard_t battery;
static uint16_t voltage_mv,imu_valid_mask;
static bool voltage_valid,imu_service_ok;
static uint64_t last_usb_rx,last_radio_rx,trigger_count;
static bool seen_usb,seen_radio;
static uint32_t last_trigger_result;

static telex_clock_t clocks[2];
static uint64_t pending_clock[2][3];
static uint8_t uid[6];
static uint32_t sequence;
static bool config_valid,radio_ready;
static uint32_t pose_sequence;
static telex_legacy_bridge_t legacy_bridge;
static uint64_t now_us(void){return (uint64_t)esp_timer_get_time();}
static void put64(uint8_t*p,uint64_t value){for(unsigned i=0;i<8;i++)p[i]=(uint8_t)(value>>(i*8));}
static uint64_t get64(const uint8_t*p){uint64_t v=0;for(unsigned i=0;i<8;i++)v|=(uint64_t)p[i]<<(i*8);return v;}
static void received(const telex_packet_t*p,telex_ingress_t ingress,uint64_t time){
 if(ingress==TELEX_ROUTE_USB){last_usb_rx=time;seen_usb=true;}else{last_radio_rx=time;seen_radio=true;}
 /* Radio HAL admits only enrolled MACs before this callback. */
 (void)telex_route_receive(&router,p,ingress,true,time);
}
static esp_err_t send_reply(telex_ingress_t ingress,telex_packet_t*p){
 p->source=role;p->target=ingress==TELEX_ROUTE_RADIO?TELEX_RECEIVER:TELEX_UNKNOWN;
 p->timestamp_ms=(uint32_t)(now_us()/1000);
 return ingress==TELEX_ROUTE_USB?telex_usb_send(p):telex_radio_send(p);
}
static void identity(telex_ingress_t ingress,uint32_t request_seq){
 telex_packet_t p={.kind=1,.sequence=request_seq};
 int n=snprintf((char*)p.payload,sizeof(p.payload),
  "{\"uid\":\"%02x%02x%02x%02x%02x%02x\",\"role\":%u,\"chip\":\"%s\",\"fw\":\"0.1.0-dev\",\"boot\":\"%016" PRIx64 "\",\"last_session\":%" PRIu32 ",\"configured\":%s,\"haptics\":%s,\"suite\":\"%s\"}",
  uid[0],uid[1],uid[2],uid[3],uid[4],uid[5],(unsigned)role,
  telex_board_chip()==TELEX_S3?"esp32s3":"esp32c6",session.boot_nonce,session.high_session,config_valid?"true":"false",settings.config.haptics_enabled&&haptic.online_mask?"true":"false",settings.suite_id);
 if(n<0||(size_t)n>=sizeof(p.payload))return;
 p.payload_len=(uint16_t)n;(void)send_reply(ingress,&p);
}
static void capabilities(telex_ingress_t ingress,uint32_t seq){
 telex_packet_t p={.kind=2,.sequence=seq};
 char payload[sizeof(p.payload)+1];
 telex_indicator_diagnostics_t indicators=telex_indicators_diagnostics();
 int n=snprintf(payload,sizeof(payload),"{\"mode\":%u,\"imu\":%u,\"rate\":%u,\"haptics\":%s,\"motors\":%u,\"cal\":%s,\"rion\":\"not_implemented\",\"prewarm\":true,\"key_verified\":%s,\"last_cal\":%" PRIu32 ",\"diag\":%s,\"led\":[%d,%d,%d,%d,%d,%d,%d,%d]}",
 settings.config.mode,settings.config.imu_profile,settings.config.group_rate_hz,settings.config.haptics_enabled?"true":"false",haptic.online_mask,
 role!=TELEX_RECEIVER&&settings.config.imu_profile==0?"true":"false",settings.trigger.key_verified?"true":"false",cal_gate.high_session,settings.config.diagnostics_enabled?"true":"false",indicator_init_result,indicators.write_error,indicators.duty[0],indicators.duty[1],indicators.duty[2],indicators.green_io,indicators.frequency,indicators.green_low_samples);
 if(n>0&&(size_t)n<=sizeof(p.payload)){memcpy(p.payload,payload,(size_t)n);p.payload_len=(uint16_t)n;(void)send_reply(ingress,&p);}
}
static void status(telex_ingress_t ingress){
 telex_packet_t p={.kind=0x11,.sequence=++sequence};
 int n=snprintf((char*)p.payload,sizeof(p.payload),"{\"imu_mask\":%u,\"faults\":%u,\"mv\":%u,\"mv_valid\":%s,\"active\":%s,\"cal\":%s,\"inhibit\":%s,\"dropped\":%" PRIu32 ",\"groups\":%" PRIu32 ",\"triggers\":%" PRIu64 ",\"trigger_result\":%" PRIu32 "}",
 imu_valid_mask,haptic.fault_mask,voltage_mv,voltage_valid?"true":"false",session.active?"true":"false",telex_cal_service_active()?"true":"false",haptic.inhibit?"true":"false",router.dropped,pose_sequence,trigger_count,last_trigger_result);
 if(n>0&&(size_t)n<sizeof(p.payload)){p.payload_len=(uint16_t)n;(void)send_reply(ingress,&p);}
}
static void ack(const telex_envelope_t*e,uint8_t state,uint8_t reason){
 telex_packet_t p={.kind=0x7e,.session_id=e->packet.session_id,.sequence=e->packet.sequence,.payload_len=3};
 p.payload[0]=e->packet.kind;p.payload[1]=state;p.payload[2]=reason;(void)send_reply(e->ingress,&p);
}
static void process_clock(const telex_envelope_t*e){
 unsigned index=e->ingress-1;const telex_packet_t*p=&e->packet;
 if(p->payload_len==8){
  telex_packet_t response={.kind=0x40,.sequence=p->sequence,.payload_len=24};
  uint64_t t0=get64(p->payload),t1=e->received_us,t2=now_us();
  pending_clock[index][0]=t0;pending_clock[index][1]=t1;pending_clock[index][2]=t2;
  put64(response.payload,t0);put64(response.payload+8,t1);put64(response.payload+16,t2);(void)send_reply(e->ingress,&response);
 }else if(p->payload_len==32){
  uint64_t t[4];for(unsigned i=0;i<4;i++)t[i]=get64(p->payload+8*i);
  bool matches=true;for(unsigned i=0;i<3;i++)if(t[i]!=pending_clock[index][i])matches=false;
  if(matches&&telex_clock_install_inverse(&clocks[index],t[0],t[1],t[2],t[3],now_us()))ack(e,TELEX_APPLIED,TELEX_REASON_NONE);
  else ack(e,TELEX_REJECTED,TELEX_REASON_EXPIRED);
  memset(pending_clock[index],0,sizeof(pending_clock[index]));
 }else ack(e,TELEX_REJECTED,TELEX_REASON_PAYLOAD);
}
static void legacy_usb(void*ctx,const uint8_t*data,size_t len){(void)ctx;(void)telex_usb_write_raw(data,len);}
static bool legacy_radio_send(void*ctx,uint8_t target,const uint8_t*data,size_t len){(void)ctx;return telex_radio_send_raw(target,data,len)==ESP_OK;}
static bool legacy_cal(void*ctx,uint8_t op,telex_ingress_t route,uint32_t id,uint16_t seq){
 (void)ctx;
 if(op==TELEX_CAL_START){telex_haptic_inhibit(&haptic,true,now_us());if(!haptic.stopped)return false;}
 return telex_cal_service_request((telex_cal_operation_t)op,route,id,seq,haptic.stopped);
}
static void legacy_flush(void*ctx){(void)ctx;telex_radio_flush();}
static void legacy_received(const uint8_t*data,size_t len,telex_ingress_t ingress,uint64_t time){
 (void)time;
 if(ingress==TELEX_ROUTE_RADIO){telex_legacy_radio(&legacy_bridge,data,len);return;}
 if(ingress==TELEX_ROUTE_USB&&telex_legacy_line(&legacy_bridge,(const char*)data))return;
 const char prefix[]="$GLOVITY,DEVICE,QUERY";
 if(ingress==TELEX_ROUTE_USB&&len>=sizeof(prefix)-1&&memcmp(data,prefix,sizeof(prefix)-1)==0){
  const char*request=len>sizeof(prefix)-1&&data[sizeof(prefix)-1]==','?(const char*)data+sizeof(prefix):"0";
  char line[256];const char*side=role==TELEX_LEFT?"left":role==TELEX_RIGHT?"right":"none";
  int hand=role==TELEX_LEFT?0:role==TELEX_RIGHT?1:-1;
  int n=snprintf(line,sizeof(line),"$GLOVITY,DEVICE,INFO,%s,%s,%s,%s,%d,%s,REMOTE_CAL_1,%02X:%02X:%02X:%02X:%02X:%02X\r\n",
   request,settings.suite_id[0]?settings.suite_id:"UNASSIGNED",role==TELEX_RECEIVER?"RECEIVER":"GLOVE",side,hand,
   role==TELEX_RECEIVER?"RemoteCal_Receiver_v1":"RemoteCal_Glove_v1",uid[0],uid[1],uid[2],uid[3],uid[4],uid[5]);
  if(n>0&&(size_t)n<sizeof(line))(void)telex_usb_write_raw(line,(size_t)n);
 }
}
static bool enqueue_calibration(void*ctx,const telex_packet_t*p,telex_ingress_t ingress){
 (void)ctx;
 if(p->payload[0]==TELEX_CAL_START){
  telex_haptic_inhibit(&haptic,true,now_us());
  if(!haptic.stopped)return false;
 }
 return telex_cal_service_request((telex_cal_operation_t)p->payload[0],ingress,p->session_id,(uint16_t)p->sequence,haptic.stopped);
}
static void process(const telex_envelope_t*e){
 const telex_packet_t*p=&e->packet;
 if(role==TELEX_RECEIVER){
  if(e->ingress==TELEX_ROUTE_RADIO){(void)telex_usb_send(p);return;}
  if(p->target==TELEX_LEFT||p->target==TELEX_RIGHT){
   if(telex_radio_send(p)!=ESP_OK)ack(e,TELEX_UNKNOWN_RESULT,TELEX_REASON_OWNER);
   return;
  }
 }
 if(p->target!=role&&p->target!=TELEX_UNKNOWN)return;
 if(p->kind==1&&p->payload_len==0){identity(e->ingress,p->sequence);return;}
 if(p->kind==2&&p->payload_len==0){capabilities(e->ingress,p->sequence);return;}
 if(p->kind==0x11&&p->payload_len==0){status(e->ingress);return;}
 if(p->kind==3){
  if(p->payload_len==0&&e->ingress==TELEX_ROUTE_USB&&config_valid){
   telex_packet_t reply={.kind=0x7e,.session_id=p->session_id,.sequence=p->sequence,.payload_len=99};
   if(telex_config_ack(&settings,reply.payload)==TELEX_OK)(void)send_reply(e->ingress,&reply);
   return;
  }
  telex_settings_t proposed;
  if(telex_settings_decode(p->payload,p->payload_len,&proposed)!=TELEX_OK||!telex_config_request(&proposed,uid,role,e->ingress)||telex_cal_service_inhibited()){ack(e,TELEX_REJECTED,TELEX_REASON_PAYLOAD);return;}
  if(role!=TELEX_RECEIVER&&!telex_haptic_stop(&haptic,false,now_us())){ack(e,TELEX_UNKNOWN_RESULT,TELEX_REASON_OWNER);return;}
  esp_err_t saved=telex_config_save(&proposed);
  telex_settings_t readback;
  if(saved==ESP_OK)saved=telex_config_load(&readback);
  if(saved==ESP_OK){
   telex_packet_t reply={.kind=0x7e,.session_id=p->session_id,.sequence=p->sequence,.payload_len=99};
   if(telex_config_ack(&readback,reply.payload)==TELEX_OK){(void)send_reply(e->ingress,&reply);vTaskDelay(pdMS_TO_TICKS(100));esp_restart();}
  }
  ack(e,TELEX_UNKNOWN_RESULT,TELEX_REASON_PAYLOAD);
  return;
 }
 if(p->kind==0x41){
  telex_packet_t reply;
  if(telex_diagnostic_echo(&settings.config,p,&reply))(void)send_reply(e->ingress,&reply);
  else ack(e,TELEX_REJECTED,TELEX_REASON_MODE);
  return;
 }
 if(p->kind==0x40){process_clock(e);return;}
 if(p->kind==0x30){
  if(role==TELEX_RECEIVER){ack(e,TELEX_REJECTED,TELEX_REASON_MODE);return;}
  uint32_t age=UINT32_MAX;(void)telex_clock_packet_age(&clocks[e->ingress-1],p->timestamp_ms,now_us(),&age);
  telex_outcome_t gated=telex_cal_dispatch(&cal_gate,p,e->ingress,session.boot_nonce,age,enqueue_calibration,NULL);
  ack(e,gated.state,gated.reason);return;
 }
 if(p->kind>=0x20&&p->kind<=0x23){
  uint32_t age=UINT32_MAX;
  if(p->kind!=0x22)(void)telex_clock_packet_age(&clocks[e->ingress-1],p->timestamp_ms,now_us(),&age);
  telex_outcome_t result=telex_haptic_handle(&haptic,p,e->ingress,age,now_us());
  ack(e,result.state,result.reason);return;
 }
 ack(e,TELEX_REJECTED,TELEX_REASON_PAYLOAD);
}
void app_main(void){
 if(telex_board_init_safe()!=ESP_OK)return;
 role=telex_board_read_role();if(esp_read_mac(uid,ESP_MAC_WIFI_STA)!=ESP_OK)return;
 settings.config=(telex_config_t){.mode=1,.group_rate_hz=30};memcpy(settings.uid,uid,6);
 if(nvs_flash_init()==ESP_OK){
  telex_settings_t loaded;
  if(telex_config_load(&loaded)==ESP_OK&&memcmp(loaded.uid,uid,6)==0){settings=loaded;config_valid=true;}
 }
 settings.trigger=telex_trigger_defaults(settings.trigger);
 indicator_init_result=telex_indicators_init();
 telex_button_init(&button,settings.trigger.debounce_ms,settings.trigger.long_ms);
 uint64_t nonce=((uint64_t)esp_random()<<32)|esp_random();
 telex_session_init(&session,&settings.config,nonce);
 uint8_t motors=0;
 if((role==TELEX_LEFT||role==TELEX_RIGHT)&&config_valid&&settings.config.mode==1&&settings.config.haptics_enabled)motors=telex_motor_probe();
 telex_haptic_init(&haptic,&session,telex_motor_io(),motors);
 if(telex_usb_init(received)!=ESP_OK)return;
 legacy_bridge=(telex_legacy_bridge_t){.role=role,.io={legacy_usb,legacy_radio_send,legacy_cal,legacy_flush,NULL}};
 telex_usb_set_legacy(settings.config.mode==0,legacy_received);telex_transport_legacy_handler(legacy_received);
 if(role!=TELEX_UNKNOWN)radio_ready=telex_radio_init(&settings,role,received)==ESP_OK;
 if(role==TELEX_LEFT||role==TELEX_RIGHT)imu_service_ok=telex_imu_service_start(settings.config.imu_profile,settings.config.group_rate_hz)==ESP_OK;
 uint64_t last_identity=0,last_motor_check=0,last_battery_check=0,last_led_update=0;
 for(;;){
  telex_usb_poll();if(radio_ready)telex_radio_poll();
  telex_envelope_t e;for(unsigned i=0;i<12&&telex_route_pop(&router,&e);i++)process(&e);
  uint64_t tick_now=now_us();
  telex_haptic_inhibit(&haptic,telex_cal_service_inhibited(),tick_now);
  if(session.active){const telex_clock_t*c=&clocks[session.owner-1];if(!c->valid||tick_now<c->observed_us||tick_now-c->observed_us>5000000)(void)telex_haptic_stop(&haptic,false,tick_now);}
  telex_haptic_tick(&haptic,tick_now);
  if(haptic.prepared&&tick_now-last_motor_check>=20000){last_motor_check=tick_now;uint8_t faults=telex_motor_faults(haptic.online_mask);if(faults){haptic.fault_mask|=faults;(void)telex_haptic_stop(&haptic,false,tick_now);}}
  if(role==TELEX_LEFT||role==TELEX_RIGHT){
   if(tick_now-last_battery_check>=1000000){
    last_battery_check=tick_now;voltage_valid=telex_battery_voltage(&voltage_mv);
    if(telex_battery_update(&battery,voltage_valid,voltage_mv,now_us())){
     (void)telex_haptic_stop(&haptic,false,now_us());(void)esp_wifi_stop();telex_power_off();
     for(;;){vTaskDelay(pdMS_TO_TICKS(1000));telex_power_off();}
    }
   }
   telex_button_event_t event=telex_button_update(&button,telex_key_level()==settings.trigger.active_level,tick_now);
   if(event==TELEX_BUTTON_SHORT_RELEASE&&settings.config.mode==1){
    telex_trigger_config_t*t=&settings.trigger;
    telex_outcome_t result=telex_haptic_click(&haptic,t->mask,t->preset,t->strength,t->duration_ms,tick_now);
    last_trigger_result=result.state;trigger_count++;tick_now=now_us();
    if(result.state==TELEX_APPLIED)led.trigger_until_us=tick_now+120000;
   }
  }
  if(tick_now-last_led_update>=10000){
   last_led_update=tick_now;
   led.calibrating=telex_cal_service_active();
   led.fault=role==TELEX_UNKNOWN||telex_application_fault(role!=TELEX_RECEIVER&&settings.config.imu_profile==0,imu_service_ok?imu_valid_mask:0,settings.config.haptics_enabled,haptic.fault_mask);
   led.wireless_selected=settings.peer_count>0&&!(seen_usb&&tick_now-last_usb_rx<2500000);
   led.wireless_connected=radio_ready&&seen_radio&&tick_now-last_radio_rx<2500000;
   telex_indicators_write(telex_led_for_chip(telex_board_chip(),led,tick_now));
  }
  telex_cal_delivery_t delivery;
  while(telex_cal_service_take(&delivery)){
   if(delivery.event.session_id==cal_gate.session&&(delivery.event.type==4||delivery.event.type==6||(delivery.event.type==5&&delivery.event.flags!=3)))cal_gate.active=false;
   uint8_t raw[87];uint8_t hand=(uint8_t)(role-1);
   if(settings.config.mode==0){
    if(delivery.ingress==TELEX_ROUTE_USB)telex_legacy_event(&legacy_bridge,&delivery.event,hand);
    else if(telex_cal_event_encode(&delivery.event,hand,raw)==TELEX_OK)(void)telex_radio_send_raw(TELEX_RECEIVER,raw,87);
   }else{
    telex_packet_t event={.kind=0x31,.session_id=delivery.event.session_id,.sequence=delivery.event.sequence,.payload_len=90};
    if(telex_cal_event_encode(&delivery.event,hand,event.payload)==TELEX_OK){
     event.payload[87]=delivery.event.switch_ok;event.payload[88]=delivery.event.save_ok;event.payload[89]=0;
     (void)send_reply(delivery.ingress,&event);
    }
   }
  }
  telex_imu_group_t group;
  if(telex_imu_service_take(&group)){
   imu_valid_mask=group.valid_mask;
   if(voltage_valid){group.voltage_mv=voltage_mv;group.status|=2;}else{group.voltage_mv=0;group.status&=(uint8_t)~2u;}
   group.battery_percent=255; // Battery presence and chemistry curve are not verified.
   if(settings.config.mode==0){
    uint8_t raw[174];uint16_t frame=(uint16_t)pose_sequence++;
    group.battery_percent=telex_legacy_battery_percent(group.voltage_mv);
    if(telex_legacy_encode_radio(&group,(uint8_t)(role-1),frame,raw)==TELEX_OK&&radio_ready)(void)telex_radio_send_raw(TELEX_RECEIVER,raw,sizeof(raw));
    char summary[128];int n=snprintf(summary,sizeof(summary),"Frame: %u  Bat: %u%%  Hand: %s(%u)  ESP-NOW: on  fail_mask: 0x%03X\r\n",frame,group.battery_percent,role==TELEX_LEFT?"left":"right",(unsigned)(role-1),(unsigned)(TELEX_IMU_ALL&~group.valid_mask));
    if(n>0&&(size_t)n<sizeof(summary))(void)telex_usb_write_raw(summary,(size_t)n);
   }else{
   telex_packet_t pose={.kind=0x10,.source=role,.target=TELEX_RECEIVER,.sequence=++pose_sequence,.timestamp_ms=(uint32_t)(group.started_us/1000),.payload_len=168};
   if(telex_imu_encode(&group,pose.payload)==TELEX_OK){(void)telex_usb_send(&pose);if(radio_ready)(void)telex_radio_send(&pose);}
   }
  }
  if(settings.config.mode==1&&now_us()-last_identity>=1000000){
   last_identity=now_us();identity(TELEX_ROUTE_USB,++sequence);status(TELEX_ROUTE_USB);
   if(radio_ready&&role!=TELEX_RECEIVER){identity(TELEX_ROUTE_RADIO,sequence);status(TELEX_ROUTE_RADIO);}
  }
  vTaskDelay(pdMS_TO_TICKS(2));
 }
}
