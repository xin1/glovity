#pragma once
// Standalone Arduino delivery; implementation flattened without algorithm changes.
// BEGIN GloveFirmware.h
// BEGIN ArduinoSupport.h
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_timer.h>
#include <esp_arduino_version.h>
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
static const uint8_t V3_LEFT_MAC[6]={0x34,0x85,0x18,0x12,0x34,0x56};
static const uint8_t V3_RECEIVER_MAC[6]={0x48,0x27,0xE2,0x65,0x43,0x21};
static const uint8_t V3_CHANNEL=1;
static const bool V3_SCALES_VERIFIED=false;
static const char V3_LEFT_ID[]="LEFT_TEST_01";
static const char V3_RECEIVER_ID[]="RECEIVER_TEST_01";
static const uint8_t V3_LAYOUT=1; // half glove; no MANO mapping in firmware
static const uint8_t V3_SDA=8,V3_SCL=9,V3_IMU_POWER=1,V3_IR_POWER=42,V3_POWER=41,V3_BAT_ADC=2,V3_LED=36;
static const uint32_t V3_I2C_HZ=400000;
static inline bool V3MacValid(const uint8_t* a) {
  uint8_t any=0;for(uint8_t i=0;i<6;i++)any|=a[i];
  return any && !(a[0]&1) && !(a[0]==0xDE && a[1]==0xAD && a[2]==0xBE && a[3]==0xEF);
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
// BEGIN Control.h

namespace six {
struct ImuIO {
  virtual ~ImuIO(){}
  virtual bool read(uint8_t index,uint8_t reg,int16_t* out,uint8_t count)=0;
  virtual bool write(uint8_t index,uint8_t reg,uint16_t value)=0;
  virtual void waitMs(uint32_t ms)=0;
  virtual uint64_t nowUs()=0;
};
struct Cached {bool used;Command command;uint32_t boot,epoch;Ack ack;};
class Control {
public:
  ImuIO& io;uint32_t boot,epoch=1;State state=PREPARE;
  uint16_t present=0,known=0,axis[11]={},calMask=0;
  uint8_t coreHz=30,diagHz=1;
  bool streaming=false;
  uint64_t transitionUntil=0,calStart=0,nextTimeoutAttempt=0;
  Cached cache[8]={};uint8_t cacheNext=0;
  explicit Control(ImuIO& hw,uint32_t id):io(hw),boot(id?id:1){}
  void discover() {
    present=known=0;
    for(uint8_t i=0;i<11;i++) {
      int16_t a=0;
      if(io.read(i,0x24,&a,1)) {
        present|=1<<i;
        if(a==0 || a==1){known|=1<<i;axis[i]=a;}
      }
    }
  }
  uint16_t modes(uint16_t value)const {
    uint16_t m=0;for(uint8_t i=0;i<11;i++)if((known&(1<<i)) && axis[i]==value)m|=1<<i;return m;
  }
  bool verifiedWrite(uint8_t i,uint8_t reg,uint16_t value) {
    if(!io.write(i,0x69,0xB588))return false;
    io.waitMs(100); // retain the original glove's conservative unlock delay
    if(!io.write(i,reg,value))return false;
    io.waitMs(100);
    int16_t got=-1;
    return io.read(i,reg,&got,1) && uint16_t(got)==value;
  }
  Ack reply(const Command& c,uint16_t detail=OK)const {
    Ack a={};a.request_id=c.request_id;a.opcode=c.opcode;a.requested=c.mask;
    a.state=state;a.epoch=epoch;a.detail=detail;
    a.mode=(modes(1)==ALL?6:(modes(0)==ALL?9:0));
    if(detail){a.result=FAILED;a.unknown=c.mask;}return a;
  }
  void finish(Ack& a)const {
    a.state=state;a.epoch=epoch;a.mode=modes(1)==ALL?6:(modes(0)==ALL?9:0);
    a.result=(a.failed || a.unknown)?(a.success?PARTIAL:FAILED):COMPLETE;
  }
  bool mutating(uint8_t op)const{return op>=START_NINE && op<=SET_RATE;}
  Ack execute(const Command& c,uint32_t expectedBoot,uint32_t expectedEpoch,bool& duplicate) {
    duplicate=false;
    for(const auto& saved:cache)if(saved.used && saved.command.request_id==c.request_id) {
      if(memcmp(&saved.command,&c,sizeof(c)) || saved.boot!=expectedBoot || saved.epoch!=expectedEpoch)return reply(c,ID_CONFLICT);
      duplicate=true;return saved.ack;
    }
    Ack a=reply(c);
    if(!c.request_id || c.flags || c.opcode<GET_STATUS || c.opcode>SET_RATE)a=reply(c,BAD_COMMAND);
    else if(c.mask!=ALL)a=reply(c,BAD_MASK); // this build controls the WHOLE left glove only
    else if(expectedBoot!=boot && !(expectedBoot==0 && !mutating(c.opcode)))a=reply(c,BAD_BOOT);
    else if(mutating(c.opcode) && expectedEpoch!=epoch)a=reply(c,STALE_EPOCH);
    else if(c.opcode!=SET_RATE && (c.arg0 || c.arg1))a=reply(c,BAD_COMMAND);
    else if(calMask && (c.opcode==START_NINE || c.opcode==START_SIX || c.opcode==STOP || c.opcode==SET_RATE || c.opcode==MAG_BEGIN))a=reply(c,BUSY);
    else if(c.opcode==GET_STATUS || c.opcode==GET_CONFIG){a.success=ALL;finish(a);}
    else if(c.opcode==START_NINE || c.opcode==START_SIX) {
      state=SWITCHING;++epoch;uint16_t value=c.opcode==START_SIX?1:0;
      for(uint8_t i=0;i<11;i++) {
        uint16_t bit=1<<i;known&=~bit;
        if(verifiedWrite(i,0x24,value)){axis[i]=value;known|=bit;present|=bit;a.success|=bit;}
        else {a.failed|=bit;int16_t actual=-1;
          if(io.read(i,0x24,&actual,1) && (actual==0 || actual==1)){axis[i]=actual;known|=bit;present|=bit;}}
      }
      streaming=true;transitionUntil=io.nowUs()+2000000ULL;
      state=a.failed?ERROR_STATE:(value==1?SIX_CAPTURE:NINE_PREPARE);
      if(a.failed)a.detail=IO_ERROR;
      finish(a);
    } else if(c.opcode==MAG_BEGIN) {
      if(modes(0)!=ALL)a=reply(c,NOT_NINE);
      else {
        ++epoch;calStart=io.nowUs();nextTimeoutAttempt=calStart+60000000ULL;
        for(uint8_t i=0;i<11;i++) {
          uint16_t bit=1<<i;
          // A failed readback may still have entered calibration: track the
          // attempted device until a verified exit, never silently discard it.
          calMask|=bit;
          if(verifiedWrite(i,0x01,7))a.success|=bit;else a.failed|=bit;
        }
        state=a.failed?ERROR_STATE:MAG_CAL;
        if(a.failed)a.detail=IO_ERROR;
        finish(a);
      }
    } else if(c.opcode==MAG_SAVE || c.opcode==MAG_STOP) {
      if(!calMask)a=reply(c,NOT_CALIBRATING);
      else a=endCalibration(c,c.opcode==MAG_SAVE);
    } else if(c.opcode==STOP) {
      ++epoch;streaming=false;state=PREPARE;transitionUntil=0;a.success=ALL;finish(a);
    } else if(c.opcode==SET_RATE) {
      if((c.arg0!=30 && c.arg0!=50) || c.arg1<1 || c.arg1>10)a=reply(c,BAD_RATE);
      else{++epoch;coreHz=c.arg0;diagHz=c.arg1;a.success=ALL;finish(a);}
    }
    // Failed requests are also cached: retries cannot accidentally execute
    // after another state change. A new intent requires a NEW request_id.
    cache[cacheNext]={true,c,expectedBoot,expectedEpoch,a};cacheNext=(cacheNext+1)%8;
    return a;
  }
  Ack endCalibration(const Command& c,bool save) {
    Ack a=reply(c);++epoch;
    for(uint8_t i=0;i<11;i++) {
      uint16_t bit=1<<i;
      if(!(calMask&bit)){a.success|=bit;continue;}
      if(!verifiedWrite(i,0x01,0)){a.failed|=bit;continue;}
      calMask&=~bit;
      bool ok=true;
      if(save) {
        ok=io.write(i,0x69,0xB588);io.waitMs(100);
        ok=ok && io.write(i,0x00,0);io.waitMs(500);
      }
      if(ok)a.success|=bit;else a.failed|=bit;
    }
    transitionUntil=io.nowUs()+2000000ULL;
    state=(a.failed || calMask)?ERROR_STATE:NINE_PREPARE;
    if(a.failed)a.detail=IO_ERROR;
    finish(a);return a;
  }
  bool tick(Ack& timeoutResult) {
    if(!calMask || io.nowUs()<nextTimeoutAttempt)return false;
    Command c={MAG_STOP,0,ALL,0,0,0};
    timeoutResult=endCalibration(c,false);timeoutResult.detail=TIMED_OUT;
    nextTimeoutAttempt=io.nowUs()+1000000ULL;return true;
  }
};
}

// END Control.h
#include <Wire.h>

using namespace six;
static Radio radio;
static Queue commands;
static volatile uint32_t rxDrop=0,invalidPackets=0;
static uint32_t i2cFail[11]={},packetSequence=0,coreScan=0,diagScan=0,overruns=0;
static uint64_t nextCore=0,nextDiag=0,nextStatus=0,lastBattery=0;
static uint8_t battery=255;
static uint64_t lowBatterySince=0;
static bool boardReady=false;
static void emitStatus(uint32_t request);

class Hardware:public ImuIO {
public:
  bool read(uint8_t i,uint8_t reg,int16_t* out,uint8_t count)override {
    if(i>=11 || !count || count>13)return false;
    Wire.beginTransmission(ADDRESSES[i]);Wire.write(reg);
    if(Wire.endTransmission(false)!=0){++i2cFail[i];return false;}
    size_t bytes=size_t(count)*2;
    if(Wire.requestFrom(ADDRESSES[i],uint8_t(bytes))!=bytes){while(Wire.available())Wire.read();++i2cFail[i];return false;}
    for(uint8_t k=0;k<count;k++){uint8_t lo=Wire.read(),hi=Wire.read();out[k]=int16_t(uint16_t(lo)|(uint16_t(hi)<<8));}
    return true;
  }
  bool write(uint8_t i,uint8_t reg,uint16_t value)override {
    Wire.beginTransmission(ADDRESSES[i]);Wire.write(reg);Wire.write(uint8_t(value));Wire.write(uint8_t(value>>8));
    bool ok=Wire.endTransmission()==0;if(!ok)++i2cFail[i];return ok;
  }
  void waitMs(uint32_t n)override {
    uint64_t end=six::nowUs()+n*1000ULL;
    while(six::nowUs()<end) {
      radio.pump();
      if(six::nowUs()>=nextStatus){nextStatus=six::nowUs()+1000000ULL;emitStatus(0);}
      delay(1);
    }
  }
  uint64_t nowUs()override{return six::nowUs();}
};
static Hardware hardware;
static Control control(hardware,1);

static Header makeHeader(uint8_t type,uint8_t count=0,uint32_t scan=0,uint64_t start=0,uint8_t part=0,uint8_t parts=1) {
  Header h={};h.type=type;h.hand_id=0;h.layout_id=V3_LAYOUT;h.boot_id=control.boot;
  h.packet_seq=packetSequence++;h.scan_id=scan;h.device_time_us=start?start:nowUs();h.config_epoch=control.epoch;
  h.part_index=part;h.part_count=parts;h.record_count=count;h.run_state=control.state;h.battery_percent=battery;h.present_mask=control.present;return h;
}
static void emit(uint8_t type,const void* body,size_t size,bool critical=true) {
  radio.enqueue(pack(makeHeader(type),body,size),critical);
}
static void emitStatus(uint32_t request=0) {
  Status s={};s.node=1;s.request_id=request;s.core_hz=control.coreHz;s.diag_hz=control.diagHz;
  s.six_mask=control.modes(1);s.nine_mask=control.modes(0);s.cal_mask=control.calMask;s.mode_unknown_mask=ALL&~control.known;
  s.scan_overrun=overruns;s.tx_api_fail=radio.apiFail;s.tx_callback_fail=radio.callbackFail;s.tx_queue_drop=radio.queueDrop;
  s.rx_queue_drop=rxDrop;s.invalid_packets=invalidPackets;memcpy(s.i2c_fail,i2cFail,sizeof(i2cFail));emit(six::STATUS,&s,sizeof(s));
}
static void emitEvent(const Command& c,uint8_t phase,const Ack* result=nullptr,uint16_t detail=six::OK) {
  Event e={};e.request_id=c.request_id;e.opcode=c.opcode;e.phase=phase;e.mask=c.mask;e.detail=detail;
  if(result){e.success=result->success;e.failed=result->failed;e.detail=result->detail;}emit(EVENT,&e,sizeof(e));
}
static void emitConfig(uint32_t request=0) {
  uint8_t body[192]={};ConfigPrefix prefix={request,1,1,1,0};DeviceInfo device={};
  strncpy(device.firmware,"six-axis-v3-20261020-r2",31);strncpy(device.device_id,V3_LEFT_ID,31);
  esp_wifi_get_mac(WIFI_IF_STA,device.mac);memcpy(device.peer,V3_RECEIVER_MAC,6);device.channel=V3_CHANNEL;
  device.core_hz=control.coreHz;device.diag_hz=control.diagHz;device.scales_verified=V3_SCALES_VERIFIED;device.i2c_hz=V3_I2C_HZ;
  memcpy(body,&prefix,8);memcpy(body+8,&device,sizeof(device));
  radio.enqueue(pack(makeHeader(CONFIG,0,0,0,0,3),body,8+sizeof(device)),true);
  const uint8_t regs[9]={0x2E,0x24,0x03,0x1F,0x20,0x21,0x25,0x2A,0x01};
  for(uint8_t part=0;part<2;part++) {
    uint8_t begin=part?6:0,count=part?5:6;
    prefix={request,1,2,count,0};memset(body,0,sizeof(body));memcpy(body,&prefix,8);
    for(uint8_t k=0;k<count;k++) {
      uint8_t i=begin+k;ModuleConfig m={};m.address=ADDRESSES[i];m.present=bool(control.present&(1<<i));
      uint16_t values[9];for(uint8_t r=0;r<9;r++) {
        int16_t value=-1;bool ok=m.present && hardware.read(i,regs[r],&value,1);
        values[r]=ok?uint16_t(value):0xFFFF;if(ok)m.valid|=1<<r;
      }
      memcpy(reinterpret_cast<uint8_t*>(&m)+4,values,sizeof(values));memcpy(body+8+k*sizeof(m),&m,sizeof(m));
    }
    radio.enqueue(pack(makeHeader(CONFIG,0,0,0,part+1,3),body,8+count*sizeof(ModuleConfig)),true);radio.pump();
  }
}
static void monitorModes() {
  uint16_t oldPresent=control.present,oldKnown=control.known,oldSix=control.modes(1),oldNine=control.modes(0);
  control.discover();
  if(oldPresent!=control.present || oldKnown!=control.known || oldSix!=control.modes(1) || oldNine!=control.modes(0)) {
    ++control.epoch;control.state=ERROR_STATE;
    Command c={0,0,ALL,0,0,0};emitEvent(c,2,nullptr,MODE_CHANGED);emitConfig();
  }
}
static uint8_t flags(uint8_t i) {
  uint16_t b=1<<i;bool known=control.known&b;
  return (known?8:0)|modeBits(known,control.axis[i])|(nowUs()<control.transitionUntil?64:0)|((control.calMask&b)?128:0);
}
static void collectCore() {
  CoreRecord records[11]={};uint64_t start=nowUs();uint32_t scan=coreScan++;
  for(uint8_t i=0;i<11;i++) {
    CoreRecord& r=records[i];r.address=ADDRESSES[i];r.flags=flags(i);
    uint64_t t=nowUs();r.inertial_start=time16(t-start);int16_t ag[6]={};
    bool ok=hardware.read(i,0x34,ag,6);r.inertial_duration=time16(nowUs()-t);
    if(ok){memcpy(r.accel,ag,6);memcpy(r.gyro,ag+3,6);r.flags|=3;}
    t=nowUs();r.quat_start=time16(t-start);
    int16_t quat[4]={};ok=hardware.read(i,0x51,quat,4);r.quat_duration=time16(nowUs()-t);
    if(ok){memcpy(r.quat,quat,8);r.flags|=4;}
  }
  radio.enqueue(pack(makeHeader(CORE,6,scan,start,0,2),records,6*sizeof(CoreRecord)));
  radio.enqueue(pack(makeHeader(CORE,5,scan,start,1,2),records+6,5*sizeof(CoreRecord)));
}
static void collectDiag() {
  DiagRecord records[11]={};uint64_t start=nowUs();uint32_t scan=diagScan++;
  for(uint8_t i=0;i<11;i++) {
    DiagRecord& r=records[i];r.address=ADDRESSES[i];r.flags=flags(i);r.start=time16(nowUs()-start);
    int16_t values[7]={};
    if(hardware.read(i,0x3A,values,7)){memcpy(r.mag,values,6);memcpy(r.euler,values+3,6);r.temperature=values[6];r.flags|=7;}
  }
  radio.enqueue(pack(makeHeader(DIAG,11,scan,start),records,sizeof(records)));
}
static void received(const esp_now_recv_info_t* info,const uint8_t* data,int length) {
  uint64_t timestamp=nowUs();
  if(!info || memcmp(info->src_addr,V3_RECEIVER_MAC,6))return;
  if(!valid(data,length)){++invalidPackets;return;}
  Header h;memcpy(&h,data,40);
  if(h.type!=COMMAND && h.type!=SYNC_REQ){++invalidPackets;return;}
  Packet p={};p.size=length;memcpy(p.data,data,length);
  // Receive time is attached to the otherwise unused scan_id fields of a
  // PRIVATE queued copy for sync only; never forwarded or CRC-validated again.
  if(h.type==SYNC_REQ)memcpy(p.data+16,&timestamp,8);
  if(!commands.push(p,true))++rxDrop;
}
static void sent(const uint8_t*,esp_now_send_status_t status){radio.complete(status);}
static void handleCommands() {
  Packet p;
  while(commands.pop(p)) {
    Header h;memcpy(&h,p.data,40);
    if(h.type==SYNC_REQ) {
      SyncRequest req;memcpy(&req,p.data+40,sizeof(req));if(req.reserved)continue;
      SyncResponse reply={};reply.request_id=req.request_id;memcpy(&reply.received_us,p.data+16,8);
      reply.sent_us=nowUs();emit(SYNC_RESP,&reply,sizeof(reply));continue;
    }
    Command c;memcpy(&c,p.data+40,16);
    bool duplicate=false;
    // Validate before emitting any operation-start claim or sampling side effect.
    bool likelyNew=true;for(const auto& old:control.cache)if(old.used && old.command.request_id==c.request_id)likelyNew=false;
    bool validIntent=likelyNew && c.mask==ALL && !c.flags && !c.arg0 && !c.arg1 && h.boot_id==control.boot && h.config_epoch==control.epoch && c.opcode>=START_NINE && c.opcode<=STOP;
    if(validIntent){emitEvent(c,0);if(control.streaming)collectCore();radio.pump();}
    Ack a=control.execute(c,h.boot_id,h.config_epoch,duplicate);
    emit(ACK,&a,sizeof(a));
    if(!duplicate && a.result!=FAILED && c.opcode!=GET_STATUS && c.opcode!=GET_CONFIG)emitEvent(c,1,&a);
    else if(!duplicate && validIntent)emitEvent(c,2,&a);
    if(c.opcode==GET_CONFIG && a.result==COMPLETE)emitConfig(c.request_id);
    else if(c.opcode==GET_STATUS && a.result==COMPLETE)emitStatus(c.request_id);
    else if(!duplicate && (a.success || a.epoch!=h.config_epoch)){emitConfig(c.request_id);nextCore=nextDiag=nowUs();}
    radio.pump();
  }
}
static void updateBattery() {
  uint64_t t=nowUs();if(t-lastBattery<1000000ULL)return;lastBattery=t;
  float voltage=analogReadMilliVolts(V3_BAT_ADC)/1000.0f*1.454545f;
  battery=uint8_t(constrain(int(lroundf((voltage-3.30f)*100.f/.90f)),0,100));
  if(voltage<3.5f){if(!lowBatterySince)lowBatterySince=t;}else lowBatterySince=0;
  if(lowBatterySince && t-lowBatterySince>=8000000ULL) {
    // Original board power-latch behaviour, no automatic re-zero/restart.
    digitalWrite(V3_IR_POWER,HIGH);digitalWrite(V3_IMU_POWER,HIGH);digitalWrite(V3_POWER,HIGH);
    while(true)delay(1000);
  }
}
void setup() {
  Serial.begin(921600);
  pinMode(V3_POWER,OUTPUT);digitalWrite(V3_POWER,LOW);
  pinMode(V3_IMU_POWER,OUTPUT);digitalWrite(V3_IMU_POWER,LOW);
  pinMode(V3_IR_POWER,OUTPUT);digitalWrite(V3_IR_POWER,LOW);
  delay(1200);Wire.begin(V3_SDA,V3_SCL,V3_I2C_HZ);Wire.setTimeOut(5);analogReadResolution(12);
  control.boot=esp_random();if(!control.boot)control.boot=1;
  boardReady=radio.init(V3_LEFT_MAC,V3_RECEIVER_MAC);
  if(boardReady){esp_now_register_recv_cb(received);esp_now_register_send_cb(sent);}
  else {Serial.println("V3 RADIO DISABLED: verify BoardConfig pairing, actual STA MAC and compile-check flag");Serial.println(WiFi.macAddress());}
  control.discover();emitConfig();emitStatus();nextStatus=nowUs()+1000000ULL;
}
void loop() {
  updateBattery();if(!boardReady){delay(10);return;}
  handleCommands();Ack timeout;
  if(control.tick(timeout)) {emit(ACK,&timeout,sizeof(timeout));Command c={MAG_STOP,0,ALL,0,0,0};emitEvent(c,2,&timeout);emitConfig();}
  uint64_t t=nowUs();
  if(control.streaming && t>=nextCore) {
    uint64_t period=1000000ULL/control.coreHz;
    if(nextCore && t>nextCore+period)++overruns;
    nextCore=t+period;collectCore();
  }
  radio.pump();t=nowUs();
  if((control.streaming || control.calMask) && t>=nextDiag){nextDiag=t+1000000ULL/control.diagHz;collectDiag();}
  radio.pump();t=nowUs();
  if(t>=nextStatus){nextStatus=t+1000000ULL;monitorModes();emitStatus();}
  radio.pump();delay(1);
}

// END GloveFirmware.h
