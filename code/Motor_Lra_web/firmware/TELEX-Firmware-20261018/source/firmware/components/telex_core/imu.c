#include "imu.h"
#include <string.h>
const uint8_t telex_imu_addresses[11]={0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,0x58,0x59,0x60};
bool telex_imu_poll(telex_imu_source_t*s,uint64_t now,telex_imu_group_t*g){
 if(!g)return false;
 memset(g,0,sizeof(*g));g->started_us=now;g->completed_us=now;g->battery_percent=255;
 if(!s||!s->implemented||!s->read)return false;
 for(unsigned i=0;i<11;i++){
  int16_t euler[3]={0},quat[4]={0};
  bool a=s->read(s->ctx,telex_imu_addresses[i],0x3d,euler,3);
  bool b=s->read(s->ctx,telex_imu_addresses[i],0x51,quat,4);
  if(i==0&&s->read(s->ctx,telex_imu_addresses[0],0x37,g->wrist_gyro,3))g->status|=1;
  if(a&&b){
   memcpy(g->imu[i],euler,sizeof(euler));memcpy(g->imu[i]+3,quat,sizeof(quat));
   g->valid_mask|=(uint16_t)(1u<<i);g->read_mask|=(uint16_t)(1u<<i);g->read_at_us[i]=s->time?s->time(s->ctx):now;
  }
 }
 g->completed_us=s->time?s->time(s->ctx):now;return true;
}
static void word(uint8_t*p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
telex_result_t telex_imu_encode(const telex_imu_group_t*g,uint8_t out[168]){
 if(!g||!out||(g->valid_mask&~TELEX_IMU_ALL)||(g->read_mask&~TELEX_IMU_ALL)||(g->status&~7u))return TELEX_INVALID;
 memset(out,0,168);
 for(unsigned i=0;i<11;i++)for(unsigned j=0;j<7;j++)word(out+i*14+j*2,(g->valid_mask&(1u<<i))?(uint16_t)g->imu[i][j]:0);
 if(g->status&1)for(unsigned i=0;i<3;i++)word(out+154+i*2,(uint16_t)g->wrist_gyro[i]);
 word(out+160,g->voltage_mv);out[162]=g->battery_percent;out[163]=g->status;
 word(out+164,g->valid_mask);word(out+166,g->read_mask);return TELEX_OK;
}
int telex_rion_uart_for_id(unsigned id){return id<5?1:id<11?0:-1;}
void telex_rion_stub(telex_imu_source_t*s){if(s)memset(s,0,sizeof(*s));}
uint8_t telex_legacy_battery_percent(uint16_t mv){
 if(mv<=3300)return 0;
 if(mv>=4200)return 100;
 return (uint8_t)(((uint32_t)(mv-3300)*100+450)/900);
}
