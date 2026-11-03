#pragma once
// Standalone Arduino delivery; implementation flattened without algorithm changes.
// BEGIN ReceiverFirmware.h
// BEGIN ArduinoSupport.h
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_timer.h>
#include <esp_arduino_version.h>
#include <Preferences.h>
#include <stdio.h>
#include <string.h>
// BEGIN BoardConfig.h
// Fill these TWO STA MACs from the CURRENT paired devices before Arduino upload.
// Never copy unknown MACs from another glove. No Wi-Fi station MAC is changed.
#define V3_PAIRING_CONFIGURED 1
#ifndef V3_COMPILE_CHECK
#define V3_COMPILE_CHECK 0
#endif
#if !V3_PAIRING_CONFIGURED && !V3_COMPILE_CHECK
#error "Fill actual LEFT/RECEIVER STA MACs in this header and set V3_PAIRING_CONFIGURED=1"
#endif
static uint8_t V3_LEFT_MAC[6]={};
static uint8_t V3_RECEIVER_MAC[6]={};
static const uint8_t V3_CHANNEL=1;
static const bool V3_SCALES_VERIFIED=false;
static const char V3_LEFT_ID[]="LEFT_SIXAXIS_UNASSIGNED";
static const char V3_RECEIVER_ID[]="RECEIVER_SIXAXIS_UNASSIGNED";
static const uint8_t V3_LAYOUT=1; // half glove; no MANO mapping in firmware
static const uint8_t V3_SDA=8,V3_SCL=9,V3_IMU_POWER=1,V3_IR_POWER=42,V3_POWER=41,V3_BAT_ADC=2,V3_LED=36;
static const uint32_t V3_I2C_HZ=400000;
static inline bool V3MacValid(const uint8_t* a) {
  uint8_t any=0;for(uint8_t i=0;i<6;i++)any|=a[i];
  return any && !(a[0]&1) && !(a[0]==0xDE && a[1]==0xAD && a[2]==0xBE && a[3]==0xEF);
}
static inline bool V3ProvisionPairing(const char* role,uint8_t* own,uint8_t* peer) {
  WiFi.mode(WIFI_STA);WiFi.disconnect();esp_wifi_get_mac(WIFI_IF_STA,own);
  Preferences prefs;if(!prefs.begin("sixaxis",false))return false;
  if(prefs.getBytesLength("peer")==6)prefs.getBytes("peer",peer,6);
  if(V3MacValid(peer) && memcmp(own,peer,6)){prefs.end();return true;}
  Serial.print("PAIR_WAIT ");Serial.print(role);Serial.print(" ");Serial.println(WiFi.macAddress());
  while(true) {
    if(!Serial.available()){delay(10);continue;}
    String line=Serial.readStringUntil('\n');line.trim();
    unsigned int b[6];
    if(sscanf(line.c_str(),"PAIR=%02x:%02x:%02x:%02x:%02x:%02x",&b[0],&b[1],&b[2],&b[3],&b[4],&b[5])!=6){Serial.println("PAIR_BAD");continue;}
    for(uint8_t i=0;i<6;i++)peer[i]=uint8_t(b[i]);
    if(!V3MacValid(peer) || !memcmp(own,peer,6)){Serial.println("PAIR_BAD");continue;}
    if(prefs.putBytes("peer",peer,6)!=6){Serial.println("PAIR_SAVE_FAIL");continue;}
    prefs.end();Serial.println("PAIR_OK");delay(300);return true;
  }
}

// END BoardConfig.h
// BEGIN Protocol.h
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace six {
static const uint8_t ADDRESSES[11]={0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,0x58,0x59,0x60};
static const uint16_t ALL=0x07FF,MAX_PACKET=250;
enum Type:uint8_t { CORE=1,CONFIG=2,STATUS=3,COMMAND=4,ACK=5,EVENT=6,SYNC_REQ=7,SYNC_RESP=8,DIAG=9 };
enum State:uint8_t { BOOT=0,PREPARE=1,SIX_CAPTURE=2,SWITCHING=3,MAG_CAL=4,ERROR_STATE=5,NINE_PREPARE=6 };
enum Op:uint8_t { GET_STATUS=1,GET_CONFIG=2,START_NINE=3,START_SIX=4,MAG_BEGIN=5,MAG_SAVE=6,MAG_STOP=7,STOP=8,SET_RATE=9 };
enum Result:uint8_t { COMPLETE=0,PARTIAL=1,FAILED=2,PENDING=3 };
enum Detail:uint16_t { OK=0,BAD_BOOT=1,STALE_EPOCH=2,BAD_MASK=3,BAD_COMMAND=4,BUSY=5,NOT_NINE=6,NOT_CALIBRATING=7,IO_ERROR=8,ID_CONFLICT=9,TIMED_OUT=10,BAD_RATE=11,NO_SPACE=12,MODE_CHANGED=13 };
#pragma pack(push,1)
struct Header {
  uint8_t magic[2],version,type;uint16_t frame_bytes;uint8_t hand_id,layout_id;
  uint32_t boot_id,packet_seq,scan_id;uint64_t device_time_us;uint32_t config_epoch;
  uint8_t part_index,part_count,record_count,run_state,battery_percent,reserved;uint16_t present_mask;
};
struct CoreRecord {
  uint8_t address,flags;uint16_t inertial_start,inertial_duration,quat_start,quat_duration;
  int16_t accel[3],gyro[3],quat[4];uint16_t reserved;
};
struct DiagRecord {uint8_t address,flags;uint16_t start;int16_t mag[3],temperature,euler[3];};
struct Command {uint8_t opcode,flags;uint16_t mask;uint32_t request_id,arg0,arg1;};
struct Ack {
  uint32_t request_id;uint8_t opcode,result,state,mode;uint16_t requested,success,failed,unknown;
  uint32_t epoch;uint16_t detail,reserved;
};
struct Event {uint32_t request_id;uint8_t opcode,phase;uint16_t mask,success,failed,detail,reserved;};
struct ConfigPrefix {uint32_t request_id;uint8_t schema,kind,count,reserved;};
struct ModuleConfig {uint8_t address,present;uint16_t valid,version,axis6,rate,bandwidth,gyro_range,acc_range,filtk,accfilt,calsw;};
struct DeviceInfo {
  char firmware[32],device_id[32];uint8_t mac[6],peer[6],channel,core_hz,diag_hz,scales_verified;
  uint32_t i2c_hz;
};
struct Status {
  uint32_t request_id;uint8_t node,core_hz,diag_hz,reserved;
  uint16_t six_mask,nine_mask,cal_mask,mode_unknown_mask;
  uint32_t scan_overrun,tx_api_fail,tx_callback_fail,tx_queue_drop,rx_queue_drop,invalid_packets;
  uint32_t i2c_fail[11];
};
struct SyncRequest {uint32_t request_id,reserved;};
struct SyncResponse {uint32_t request_id,reserved;uint64_t received_us,sent_us;};
#pragma pack(pop)
static_assert(sizeof(Header)==40,"Header 40B");
static_assert(sizeof(CoreRecord)==32,"CORE 32B");
static_assert(sizeof(DiagRecord)==18,"DIAG 18B");
static_assert(sizeof(Command)==16 && sizeof(Ack)==24,"Control layout");
static_assert(sizeof(ModuleConfig)==22 && sizeof(Event)==16,"Metadata layout");
static_assert(sizeof(Status)==84 && sizeof(DeviceInfo)==84,"Status/info layout");
struct Packet {uint16_t size;uint8_t data[MAX_PACKET];};
inline uint32_t crc32(const uint8_t* data,size_t size) {
  uint32_t c=0xFFFFFFFF;
  for(size_t i=0;i<size;i++){c^=data[i];for(uint8_t b=0;b<8;b++)c=(c>>1)^((c&1)?0xEDB88320:0);}
  return c^0xFFFFFFFF;
}
inline bool valid(const uint8_t* data,size_t n) {
  if(n<44 || n>MAX_PACKET)return false;
  Header h;memcpy(&h,data,40);
  if(h.magic[0]!=0xAA || h.magic[1]!=0x57 || h.version!=3 || h.frame_bytes!=n || h.hand_id!=0 || h.layout_id>2 || h.reserved || !h.part_count || h.part_index>=h.part_count || h.type<1 || h.type>9)return false;
  if(h.type==CORE){
    if(h.part_count!=2 || h.record_count!=(h.part_index==0?6:5) || n!=size_t(44+32*h.record_count))return false;
    for(uint8_t i=0;i<h.record_count;i++)if(data[40+i*32]!=ADDRESSES[i+(h.part_index?6:0)])return false;
  }
  else if(h.type==DIAG){
    if(h.part_count!=1 || h.record_count!=11 || n!=242)return false;
    for(uint8_t i=0;i<11;i++)if(data[40+i*18]!=ADDRESSES[i])return false;
  }
  else {
    if(h.record_count)return false;
    if(h.type==COMMAND && (n!=60 || h.part_count!=1))return false;
    if(h.type==ACK && (n!=68 || h.part_count!=1))return false;
    if(h.type==EVENT && n!=60)return false;
    if(h.type==SYNC_REQ && n!=52)return false;
    if(h.type==SYNC_RESP && n!=68)return false;
    if(h.type==STATUS && n!=44+sizeof(Status))return false;
    if(h.type==CONFIG){
      if(n<52)return false;
      ConfigPrefix prefix;memcpy(&prefix,data+40,8);
      if(prefix.schema!=1 || prefix.reserved)return false;
      if(prefix.kind==1 || prefix.kind==3){if(h.part_index!=0 || h.part_count!=(prefix.kind==1?3:1) || prefix.count!=1 || n!=52+sizeof(DeviceInfo))return false;}
      else if(prefix.kind==2){if(h.part_count!=3 || h.part_index==0 || prefix.count!=(h.part_index==1?6:5) || n!=52+prefix.count*sizeof(ModuleConfig))return false;}
      else return false;
    }
  }
  uint32_t supplied;memcpy(&supplied,data+n-4,4);return supplied==crc32(data,n-4);
}
inline Packet pack(Header h,const void* payload,size_t count) {
  Packet p={};if(count+44>MAX_PACKET)return p;
  p.size=count+44;h.magic[0]=0xAA;h.magic[1]=0x57;h.version=3;h.frame_bytes=p.size;
  memcpy(p.data,&h,40);if(count)memcpy(p.data+40,payload,count);
  uint32_t c=crc32(p.data,p.size-4);memcpy(p.data+p.size-4,&c,4);return p;
}
inline uint16_t time16(uint64_t value){return value<65535?uint16_t(value):0xFFFF;}
inline uint8_t modeBits(bool known,uint16_t axis6){return known?uint8_t((axis6==1?1:2)<<4):0;}
class Parser {
  uint8_t bytes[512];size_t size=0;
public:
  uint32_t rejected=0;
  void feed(uint8_t b){if(size==sizeof(bytes)){memmove(bytes,bytes+1,--size);++rejected;}bytes[size++]=b;}
  bool pop(Packet& p) {
    while(size>=2) {
      if(bytes[0]!=0xAA || bytes[1]!=0x57){memmove(bytes,bytes+1,--size);continue;}
      if(size<6)return false;
      uint16_t n=uint16_t(bytes[4])|(uint16_t(bytes[5])<<8);
      if(n<44 || n>250){memmove(bytes,bytes+1,--size);++rejected;continue;}
      if(size<n)return false;
      if(!valid(bytes,n)){memmove(bytes,bytes+1,--size);++rejected;continue;}
      p.size=n;memcpy(p.data,bytes,n);size-=n;memmove(bytes,bytes+n,size);return true;
    }
    return false;
  }
};
}

// END Protocol.h
#if ESP_ARDUINO_VERSION_MAJOR != 3 || ESP_ARDUINO_VERSION_MINOR != 0 || ESP_ARDUINO_VERSION_PATCH != 7
#error "This delivery is pinned to Arduino-ESP32 3.0.7 (ESP-NOW callback API)"
#endif
namespace six {
inline uint64_t nowUs(){return uint64_t(esp_timer_get_time());}
class Queue {
  Packet slots[32];uint8_t head=0,tail=0,count=0;
  portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
public:
  bool push(const Packet& p,bool critical=false) {
    bool ok=false;portENTER_CRITICAL(&mux);
    if(count<(critical?32:24)){slots[head]=p;head=(head+1)%32;++count;ok=true;}
    portEXIT_CRITICAL(&mux);return ok;
  }
  bool pop(Packet& p) {
    bool ok=false;portENTER_CRITICAL(&mux);
    if(count){p=slots[tail];tail=(tail+1)%32;--count;ok=true;}
    portEXIT_CRITICAL(&mux);return ok;
  }
};
// No busy retrying data. A lost packet has its own sequence and is counted.
class Radio {
public:
  Queue outgoing;volatile bool busy=false;volatile uint32_t callbackFail=0;
  uint32_t apiFail=0,queueDrop=0;
  uint8_t peer[6]={};bool ready=false;
  bool enqueue(const Packet& p,bool critical=false){if(!p.size || !ready || !outgoing.push(p,critical)){++queueDrop;return false;}return true;}
  void pump(){
    if(!ready || busy)return;Packet p;
    if(outgoing.pop(p)){
      if(p.data[3]==SYNC_RESP && p.size==68){
        uint64_t t=nowUs();memcpy(p.data+56,&t,8);
        uint32_t crc=crc32(p.data,p.size-4);memcpy(p.data+p.size-4,&crc,4);
      }
      busy=true;if(esp_now_send(peer,p.data,p.size)!=ESP_OK){busy=false;++apiFail;}
    }
  }
  void complete(esp_now_send_status_t s){if(s!=ESP_NOW_SEND_SUCCESS)++callbackFail;busy=false;}
  bool init(const uint8_t* own,const uint8_t* other) {
    WiFi.mode(WIFI_STA);WiFi.disconnect();uint8_t actual[6];esp_wifi_get_mac(WIFI_IF_STA,actual);
    if(!V3_PAIRING_CONFIGURED || V3_COMPILE_CHECK || !V3MacValid(own) || !V3MacValid(other) || !memcmp(own,other,6) || memcmp(actual,own,6))return false;
    if(esp_wifi_set_channel(V3_CHANNEL,WIFI_SECOND_CHAN_NONE)!=ESP_OK || esp_now_init()!=ESP_OK)return false;
    memcpy(peer,other,6);esp_now_peer_info_t p={};memcpy(p.peer_addr,other,6);p.channel=V3_CHANNEL;p.encrypt=false;
    ready=esp_now_add_peer(&p)==ESP_OK;return ready;
  }
};
}

// END ArduinoSupport.h
using namespace six;
static Radio radio;
static Queue usbOutput;
static Parser usbInput;
static volatile uint32_t invalidPackets=0,rxDrop=0,lastGloveMs=0;
static uint32_t localSequence=0,receiverBoot=1,usbDrop=0;
static bool boardReady=false;
static void sent(const uint8_t*,esp_now_send_status_t result){radio.complete(result);}
static void received(const esp_now_recv_info_t* info,const uint8_t* data,int length) {
  if(!info || memcmp(info->src_addr,V3_LEFT_MAC,6))return;
  if(!valid(data,length)){++invalidPackets;return;}
  Header h;memcpy(&h,data,40);
  if(h.type==COMMAND || h.type==SYNC_REQ){++invalidPackets;return;}
  Packet p={};p.size=length;memcpy(p.data,data,length);lastGloveMs=millis();
  if(!usbOutput.push(p,h.type!=CORE && h.type!=DIAG))++rxDrop;
}
static void emitReceiverStatus() {
  Header h={};h.type=six::STATUS;h.hand_id=0;h.layout_id=V3_LAYOUT;h.boot_id=receiverBoot;
  h.packet_seq=localSequence++;h.device_time_us=nowUs();h.part_count=1;h.run_state=boardReady?PREPARE:ERROR_STATE;h.battery_percent=255;
  Status s={};s.node=2;s.tx_api_fail=radio.apiFail;s.tx_callback_fail=radio.callbackFail;
  s.tx_queue_drop=radio.queueDrop;s.rx_queue_drop=rxDrop;s.invalid_packets=invalidPackets+usbInput.rejected;s.scan_overrun=usbDrop;
  if(!usbOutput.push(pack(h,&s,sizeof(s)),true))++usbDrop;
}
static void emitReceiverInfo() {
  Header h={};h.type=CONFIG;h.layout_id=V3_LAYOUT;h.boot_id=receiverBoot;h.packet_seq=localSequence++;
  h.device_time_us=nowUs();h.part_count=1;h.run_state=boardReady?PREPARE:ERROR_STATE;h.battery_percent=255;
  ConfigPrefix prefix={0,1,3,1,0};DeviceInfo info={};
  strncpy(info.firmware,"six-axis-v3-20261020-r2",31);strncpy(info.device_id,V3_RECEIVER_ID,31);
  esp_wifi_get_mac(WIFI_IF_STA,info.mac);memcpy(info.peer,V3_LEFT_MAC,6);info.channel=V3_CHANNEL;
  uint8_t body[8+sizeof(info)];memcpy(body,&prefix,8);memcpy(body+8,&info,sizeof(info));
  if(!usbOutput.push(pack(h,body,sizeof(body)),true))++usbDrop;
}
static void pumpUsb() {
  static Packet current={};static uint16_t offset=0;
  if(!Serial)return;
  if(!current.size){if(!usbOutput.pop(current))return;offset=0;}
  int room=Serial.availableForWrite();
  if(room>0) {
    size_t n=min(size_t(room),size_t(current.size-offset));
    size_t wrote=Serial.write(current.data+offset,n);offset+=wrote;
    if(offset==current.size)current.size=0;
  }
}
void setup() {
  Serial.begin(2000000);pinMode(V3_LED,OUTPUT);digitalWrite(V3_LED,LOW);
  V3ProvisionPairing("RECEIVER",V3_RECEIVER_MAC,V3_LEFT_MAC);
  receiverBoot=esp_random();if(!receiverBoot)receiverBoot=1;
  boardReady=radio.init(V3_RECEIVER_MAC,V3_LEFT_MAC);
  if(boardReady){esp_now_register_recv_cb(received);esp_now_register_send_cb(sent);}
  // Binary STATUS is emitted even for bad pairing. No text contaminates USB.
  emitReceiverStatus();emitReceiverInfo();
}
void loop() {
  // Bound work per loop so USB bursts do not starve radio/serial draining.
  for(uint16_t k=0;k<512 && Serial.available();k++) {
    usbInput.feed(uint8_t(Serial.read()));Packet p;
    while(usbInput.pop(p)) {
      Header h;memcpy(&h,p.data,40);
      if(h.type==COMMAND || h.type==SYNC_REQ)radio.enqueue(p,true);
      else ++invalidPackets;
    }
  }
  radio.pump();pumpUsb();
  static uint32_t lastStatus=0;uint32_t t=millis();
  if(t-lastStatus>=1000){lastStatus=t;emitReceiverStatus();}
  static uint32_t lastInfo=0;if(t-lastInfo>=5000){lastInfo=t;emitReceiverInfo();}
  bool connected=boardReady && lastGloveMs && t-lastGloveMs<1500;
  digitalWrite(V3_LED,connected?HIGH:((t/150)%2?HIGH:LOW));
  delay(1);
}

// END ReceiverFirmware.h
