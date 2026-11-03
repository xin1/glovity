#include "transport.h"
#include "legacy_wire.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include <stdatomic.h>
#include <string.h>
typedef struct {uint8_t peer;telex_packet_t packet;uint64_t received_us;bool legacy;uint16_t length;uint8_t raw[250];} radio_rx_t;
static uint8_t peers[3][6],roles[3],peer_count;
static QueueHandle_t inbox,stopboxes[4];
static telex_rx_fn rx;
static atomic_bool busy;
static telex_router_t outgoing;
static bool ready,legacy_mode;
static telex_legacy_rx_fn legacy_rx;
void telex_transport_legacy_handler(telex_legacy_rx_fn callback){legacy_rx=callback;}
static void sent(const esp_now_send_info_t*info,esp_now_send_status_t status){(void)info;(void)status;atomic_store(&busy,false);}
static void received(const esp_now_recv_info_t*info,const uint8_t*data,int len){
 if(!info||len<0||len>250)return;
 for(uint8_t i=0;i<peer_count;i++)if(memcmp(peers[i],info->src_addr,6)==0){
  radio_rx_t event={.peer=i,.received_us=(uint64_t)esp_timer_get_time()};
  if(legacy_mode){
   event.legacy=true;event.length=(uint16_t)len;memcpy(event.raw,data,(size_t)len);
   (void)xQueueSend(inbox,&event,0);return;
  }
  if(telex_decode(data,(size_t)len,&event.packet)==TELEX_OK){
   if(event.packet.kind==0x22&&event.packet.target<4){
    QueueHandle_t box=stopboxes[event.packet.target];radio_rx_t previous;
    if(xQueuePeek(box,&previous,0)!=pdTRUE||event.packet.session_id==0||
       (previous.packet.session_id!=0&&telex_sequence_newer(event.packet.session_id,previous.packet.session_id)))
        (void)xQueueOverwrite(box,&event);
   }else (void)xQueueSend(inbox,&event,0);
  }
  return;
 }
}
esp_err_t telex_radio_init(const telex_settings_t*s,telex_role_t role,telex_rx_fn callback){
 if(!s||s->peer_count>3||(role!=TELEX_RECEIVER&&s->peer_count>1))return ESP_ERR_INVALID_ARG;
 legacy_mode=s->config.mode==0;rx=callback;peer_count=s->peer_count;memcpy(peers,s->peers,sizeof(peers));
 for(unsigned i=0;i<peer_count;i++)roles[i]=role==TELEX_RECEIVER?0:TELEX_RECEIVER;
 inbox=xQueueCreate(8,sizeof(radio_rx_t));if(!inbox)return ESP_ERR_NO_MEM;
 for(unsigned i=0;i<4;i++){stopboxes[i]=xQueueCreate(1,sizeof(radio_rx_t));if(!stopboxes[i])return ESP_ERR_NO_MEM;}
 esp_err_t err=esp_netif_init();if(err!=ESP_OK)return err;
 err=esp_event_loop_create_default();if(err!=ESP_OK)return err;
 wifi_init_config_t cfg=WIFI_INIT_CONFIG_DEFAULT();err=esp_wifi_init(&cfg);if(err!=ESP_OK)return err;
 if((err=esp_wifi_set_storage(WIFI_STORAGE_RAM))!=ESP_OK)return err;
 if((err=esp_wifi_set_mode(WIFI_MODE_STA))!=ESP_OK)return err;
 if((err=esp_wifi_start())!=ESP_OK)return err;
 if((err=esp_wifi_set_ps(WIFI_PS_NONE))!=ESP_OK)return err;
 if((err=esp_wifi_set_channel(1,WIFI_SECOND_CHAN_NONE))!=ESP_OK)return err;
 if((err=esp_now_init())!=ESP_OK)return err;
 if((err=esp_now_register_recv_cb(received))!=ESP_OK)return err;
 if((err=esp_now_register_send_cb(sent))!=ESP_OK)return err;
 for(unsigned i=0;i<peer_count;i++){
  esp_now_peer_info_t peer={.channel=1,.ifidx=WIFI_IF_STA};memcpy(peer.peer_addr,peers[i],6);
  if((err=esp_now_add_peer(&peer))!=ESP_OK)return err;
 }
 ready=true;return ESP_OK;
}
void telex_radio_poll(void){
 if(!ready)return;
 if(!legacy_mode&&!atomic_load(&busy)){
  telex_envelope_t next;
  if(telex_route_pop(&outgoing,&next)){
   uint8_t data[250];size_t n;
   if(telex_encode(&next.packet,data,sizeof(data),&n)==TELEX_OK)(void)telex_radio_send_raw(next.packet.target,data,n);
  }
 }
 radio_rx_t event;
 for(unsigned n=0;n<12;n++){
  bool found=false;
  for(unsigned i=0;i<4;i++)if(xQueueReceive(stopboxes[i],&event,0)==pdTRUE){found=true;break;}
  if(!found&&xQueueReceive(inbox,&event,0)!=pdTRUE)break;
  if(event.legacy){
   if(event.length==174){
    telex_imu_group_t group;uint8_t hand;uint16_t frame;
    if(telex_legacy_decode_radio(event.raw,event.length,&group,&hand,&frame)!=TELEX_OK)continue;
    bool conflict=false;for(unsigned i=0;i<peer_count;i++)if(i!=event.peer&&roles[i]==hand+1)conflict=true;
    if(conflict)continue;
    roles[event.peer]=(uint8_t)(hand+1);
   }
   if(legacy_rx)legacy_rx(event.raw,event.length,TELEX_ROUTE_RADIO,event.received_us);
   continue;
  }
  if(event.packet.kind==1&&(event.packet.source==TELEX_LEFT||event.packet.source==TELEX_RIGHT)){
   bool conflict=false;for(unsigned i=0;i<peer_count;i++)if(i!=event.peer&&roles[i]==event.packet.source)conflict=true;
   if(conflict)continue;
   roles[event.peer]=event.packet.source;
  }
  if(roles[event.peer]&&event.packet.source!=0&&roles[event.peer]!=event.packet.source)continue;
  if(rx)rx(&event.packet,TELEX_ROUTE_RADIO,event.received_us);
 }
}
esp_err_t telex_radio_send_raw(uint8_t target,const uint8_t*data,size_t len){
 if(!ready)return ESP_ERR_INVALID_STATE;
 if(!data||!len||len>250)return ESP_ERR_INVALID_ARG;
 const uint8_t *address=NULL;
 for(unsigned i=0;i<peer_count;i++)if(roles[i]==target&&target!=0){address=peers[i];break;}
 if(!address)return ESP_ERR_NOT_FOUND;
 if(atomic_exchange(&busy,true))return ESP_ERR_TIMEOUT;
 esp_err_t err=esp_now_send(address,data,len);if(err!=ESP_OK)atomic_store(&busy,false);return err;
}
esp_err_t telex_radio_send(const telex_packet_t*p){
 if(!ready||legacy_mode||!p)return ESP_ERR_INVALID_STATE;
 uint8_t data[250];size_t n;
 if(telex_encode(p,data,sizeof(data),&n)!=TELEX_OK)return ESP_ERR_INVALID_ARG;
 bool target=false;for(unsigned i=0;i<peer_count;i++)if(roles[i]==p->target&&p->target)target=true;
 if(!target)return ESP_ERR_NOT_FOUND;
 // Queue acceptance is not an execution ACK. Preserve packet timestamps and
 // use the tested bounded router's latest-value queues and per-target STOPs.
 return telex_route_receive(&outgoing,p,TELEX_ROUTE_USB,true,(uint64_t)esp_timer_get_time())?ESP_OK:ESP_ERR_TIMEOUT;
}

void telex_radio_flush(void){if(inbox)(void)xQueueReset(inbox);}
