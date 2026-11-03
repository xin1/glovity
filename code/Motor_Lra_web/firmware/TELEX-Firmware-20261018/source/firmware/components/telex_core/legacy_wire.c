#include "legacy_wire.h"
#include <string.h>
static void word(uint8_t*p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static uint16_t readword(const uint8_t*p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
telex_result_t telex_legacy_encode_usb(const telex_imu_group_t*g,uint8_t hand,uint16_t id,uint8_t out[168]){
 if(!g||!out||hand>1)return TELEX_INVALID;
 memset(out,0,168);out[0]=0xaa;out[1]=0x55;out[2]=hand;out[3]=11;out[4]=g->battery_percent<=100?g->battery_percent:0;word(out+5,id);
 for(unsigned i=0;i<11;i++)if(g->valid_mask&(1u<<i))for(unsigned j=0;j<7;j++)word(out+7+i*14+j*2,(uint16_t)g->imu[i][j]);
 for(unsigned i=0;i<3;i++)word(out+161+i*2,(uint16_t)g->wrist_gyro[i]);
 for(unsigned i=0;i<167;i++)out[167]^=out[i];
 return TELEX_OK;
}
telex_result_t telex_legacy_encode_radio(const telex_imu_group_t*g,uint8_t hand,uint16_t id,uint8_t out[174]){
 if(!g||!out)return TELEX_INVALID;
 out[0]=0x49;out[1]=0x4d;out[2]=0x44;out[3]=0x48;word(out+4,(uint16_t)(TELEX_IMU_ALL&~g->valid_mask));
 return telex_legacy_encode_usb(g,hand,id,out+6);
}
telex_result_t telex_legacy_decode_radio(const uint8_t*b,size_t n,telex_imu_group_t*g,uint8_t*hand,uint16_t*id){
 if(!b||!g||!hand||!id||n!=174)return TELEX_SIZE;
 const uint8_t prefix[]={0x49,0x4d,0x44,0x48};
 if(memcmp(b,prefix,4)||b[6]!=0xaa||b[7]!=0x55||b[8]>1||b[9]!=11||(readword(b+4)&~TELEX_IMU_ALL))return TELEX_INVALID;
 uint8_t xor=0;for(unsigned i=6;i<174;i++)xor^=b[i];if(xor)return TELEX_CRC;
 memset(g,0,sizeof(*g));*hand=b[8];*id=readword(b+11);g->battery_percent=b[10];g->valid_mask=(uint16_t)(TELEX_IMU_ALL&~readword(b+4));g->read_mask=g->valid_mask;
 for(unsigned i=0;i<11;i++)for(unsigned j=0;j<7;j++)g->imu[i][j]=(int16_t)readword(b+13+i*14+j*2);
 for(unsigned i=0;i<3;i++)g->wrist_gyro[i]=(int16_t)readword(b+167+i*2);
 return TELEX_OK;
}

static void dword(uint8_t*p,uint32_t value){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(value>>(8*i));}
static uint32_t readdword(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static uint8_t checksum(const uint8_t*p,size_t n){uint8_t v=0;for(size_t i=0;i<n;i++)v^=p[i];return v;}
telex_result_t telex_cal_command_encode(uint8_t op,uint8_t hand,uint16_t seq,uint32_t session,uint8_t out[15]){
 if(!out||op<1||op>4||hand>1)return TELEX_INVALID;
 memset(out,0,15);memcpy(out,"GCMD",4);out[4]=1;out[5]=op;out[6]=hand;word(out+8,seq);dword(out+10,session);out[14]=checksum(out,14);return TELEX_OK;
}
telex_result_t telex_cal_command_decode(const uint8_t*b,size_t n,uint8_t*op,uint8_t*hand,uint16_t*seq,uint32_t*session){
 if(!b||!op||!hand||!seq||!session||n!=15)return TELEX_SIZE;
 if(memcmp(b,"GCMD",4)||b[4]!=1||b[5]<1||b[5]>4||b[6]>1||b[7])return TELEX_INVALID;
 if(checksum(b,n))return TELEX_CRC;
 *op=b[5];*hand=b[6];*seq=readword(b+8);*session=readdword(b+10);return TELEX_OK;
}
telex_result_t telex_cal_event_encode(const telex_cal_event_t*e,uint8_t hand,uint8_t out[87]){
 if(!e||!out||hand>1||e->type<1||e->type>6||(e->active_mask&~TELEX_IMU_ALL))return TELEX_INVALID;
 memset(out,0,87);memcpy(out,"GCAL",4);out[4]=1;out[5]=e->type;out[6]=hand;out[7]=e->flags;
 word(out+8,e->sequence);dword(out+10,e->session_id);dword(out+14,e->timestamp_ms);word(out+18,e->active_mask);
 for(unsigned i=0;i<11;i++)for(unsigned j=0;j<3;j++)word(out+20+i*6+j*2,(uint16_t)e->mag[i][j]);
 out[86]=checksum(out,86);return TELEX_OK;
}
telex_result_t telex_cal_event_decode(const uint8_t*b,size_t n,telex_cal_event_t*e,uint8_t*hand){
 if(!b||!e||!hand||n!=87)return TELEX_SIZE;
 if(memcmp(b,"GCAL",4)||b[4]!=1||b[5]<1||b[5]>6||b[6]>1||(readword(b+18)&~TELEX_IMU_ALL))return TELEX_INVALID;
 if(checksum(b,n))return TELEX_CRC;
 memset(e,0,sizeof(*e));*hand=b[6];e->type=b[5];e->flags=b[7];e->sequence=readword(b+8);e->session_id=readdword(b+10);e->timestamp_ms=readdword(b+14);e->active_mask=readword(b+18);
 for(unsigned i=0;i<11;i++)for(unsigned j=0;j<3;j++)e->mag[i][j]=(int16_t)readword(b+20+i*6+j*2);
 return TELEX_OK;
}
