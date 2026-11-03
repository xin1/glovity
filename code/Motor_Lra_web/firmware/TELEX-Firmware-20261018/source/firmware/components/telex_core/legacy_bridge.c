#include "legacy_bridge.h"
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <ctype.h>
static const char*side(uint8_t hand){return hand?"right":"left";}
static unsigned count_bits(uint16_t mask){unsigned n=0;for(unsigned i=0;i<11;i++)if(mask&(1u<<i))n++;return n;}
static void text(telex_legacy_bridge_t*b,const char*fmt,...){
 char line[1024];va_list ap;va_start(ap,fmt);int n=vsnprintf(line,sizeof(line),fmt,ap);va_end(ap);
 if(n>0&&(size_t)n<sizeof(line)&&b->io.usb)b->io.usb(b->io.ctx,(const uint8_t*)line,(size_t)n);
}
static bool forward(telex_legacy_bridge_t*b,uint8_t op,uint8_t hand,uint32_t session,uint16_t sequence){
 uint8_t raw[15];return telex_cal_command_encode(op,hand,sequence,session,raw)==TELEX_OK&&b->io.radio&&b->io.radio(b->io.ctx,(uint8_t)(hand+1),raw,15);
}
static void error(telex_legacy_bridge_t*b,uint8_t hand,uint32_t session,uint16_t sequence,const char*why){text(b,"$GLOVITY,RX,CAL,ERROR,%s,%u,%lu,%u,%s\r\n",side(hand),hand,(unsigned long)session,sequence,why);}
bool telex_legacy_line(telex_legacy_bridge_t*b,const char*line){
 if(!b||!line)return false;
 while(*line==' '||*line=='\t')line++;
 if(b->role==TELEX_RECEIVER){
  if(strcmp(line,"$GLOVITY,RX,MAINT,ENTER")==0){
   if(b->active){text(b,"$GLOVITY,RX,MAINT,BUSY\r\n");return true;}
   b->maintenance=true;if(b->io.flush)b->io.flush(b->io.ctx);text(b,"$GLOVITY,RX,MAINT,READY,1\r\n");return true;
  }
  if(strcmp(line,"$GLOVITY,RX,MAINT,EXIT")==0){
   if(b->active)(void)forward(b,4,b->hand,b->session,b->sequence);
   b->active=false;b->maintenance=false;if(b->io.flush)b->io.flush(b->io.ctx);text(b,"$GLOVITY,RX,MAINT,EXITED\r\n");return true;
  }
  if(strncmp(line,"$GLOVITY,RX,CAL,",16)!=0)return false;
  char operation[16]={0},which[12]={0};unsigned long session=0;unsigned sequence=0;
  if(sscanf(line,"$GLOVITY,RX,CAL,%15[^,],%11[^,],%lu,%u",operation,which,&session,&sequence)!=4||session>UINT32_MAX||sequence>UINT16_MAX){error(b,0,0,0,"BAD_COMMAND");return true;}
  uint8_t hand;if(!strcmp(which,"left")||!strcmp(which,"0"))hand=0;else if(!strcmp(which,"right")||!strcmp(which,"1"))hand=1;else{error(b,0,(uint32_t)session,(uint16_t)sequence,"BAD_SIDE");return true;}
  uint8_t op=!strcmp(operation,"STATUS")?1:!strcmp(operation,"START")?2:!strcmp(operation,"STOP")?3:!strcmp(operation,"ABORT")?4:0;
  const char*why=!op?"BAD_OPERATION":!b->maintenance?"NOT_IN_MAINTENANCE":op==2&&b->active?"BUSY":op==3&&!b->active?"NOT_RUNNING":NULL;
  if(why){error(b,hand,(uint32_t)session,(uint16_t)sequence,why);return true;}
  b->hand=hand;b->session=(uint32_t)session;b->sequence=(uint16_t)sequence;
  if(op==2)b->active=true;
  if(op==4)b->active=false;
  if(!forward(b,op,hand,b->session,b->sequence)){b->active=false;error(b,hand,b->session,b->sequence,"SEND_FAILED");return true;}
  text(b,"$GLOVITY,RX,CAL,FORWARD,%s,%s,%lu,%u\r\n",operation,side(hand),session,sequence);return true;
 }
 char upper[96];size_t n=strlen(line);if(n>=sizeof(upper))return false;
 for(size_t i=0;i<=n;i++)upper[i]=(char)toupper((unsigned char)line[i]);
 const char*cmd=strncmp(upper,"$GLOVITY,",9)==0?upper+9:upper;
 if(strcmp(cmd,"NORMAL")==0){if(b->active)return b->io.cal&&b->io.cal(b->io.ctx,3,TELEX_ROUTE_USB,0,0);text(b,"$GLOVITY,MODE,NORMAL\r\n");return true;}
 uint8_t op=!strcmp(cmd,"CAL,STATUS")?1:!strcmp(cmd,"CAL,START")?2:!strcmp(cmd,"CAL,STOP")?3:0;
 if(!op)return false;
 if(op==2)text(b,"$GLOVITY,CAL,STARTING\r\n");
 if(op==3)text(b,"$GLOVITY,CAL,STOPPING\r\n");
 return b->io.cal&&b->io.cal(b->io.ctx,op,TELEX_ROUTE_USB,0,0);
}
void telex_legacy_event(telex_legacy_bridge_t*b,const telex_cal_event_t*e,uint8_t hand){
 if(!b||!e||hand>1)return;
 bool receiver=b->role==TELEX_RECEIVER;
 if(receiver&&(!b->maintenance||hand!=b->hand||e->session_id!=b->session))return;
 if(e->type==2)b->active=true;
 if(e->type==4||e->type==5||e->type==6)b->active=false;
 if(!receiver&&e->type==5&&e->flags==3)b->active=true;
 if(e->type==1){
  if(receiver)text(b,"$GLOVITY,RX,CAL,STATUS,%s,%u,%s,%u,11,%lu,%u\r\n",side(hand),hand,e->flags?"RUNNING":"IDLE",count_bits(e->active_mask),(unsigned long)e->session_id,e->sequence);
  else text(b,"$GLOVITY,CAL,STATUS,%s,%u,11,%s,%u\r\n",e->flags?"RUNNING":"IDLE",count_bits(e->active_mask),side(hand),hand);
 }else if(e->type==3){
  if(receiver)text(b,"$GLOVITY,RX,CAL,MAG,%s,%u,%lu,%u,%lu",side(hand),hand,(unsigned long)e->session_id,e->sequence,(unsigned long)e->timestamp_ms);
  else text(b,"$GLOVITY,CAL,MAG,%lu",(unsigned long)(e->timestamp_ms/1000));
  for(unsigned i=0;i<11;i++)if(e->active_mask&(1u<<i))text(b,",0x%02X:%d:%d:%d",telex_imu_addresses[i],e->mag[i][0],e->mag[i][1],e->mag[i][2]);
  text(b,"\r\n");
 }else{
  const char*name=e->type==2?"STARTED":e->type==4?"COMPLETE":e->type==6?"ABORTED":"ERROR";
  if(receiver){
   if(e->type==5)text(b,"$GLOVITY,RX,CAL,ERROR,%s,%u,%lu,%u,%u\r\n",side(hand),hand,(unsigned long)e->session_id,e->sequence,e->flags);
   else text(b,"$GLOVITY,RX,CAL,%s,%s,%u,%lu,%u\r\n",name,side(hand),hand,(unsigned long)e->session_id,e->sequence);
  }else{
   if(e->type==5){const char*why=e->flags==1?"NO_IMU":e->flags==2?"NOT_RUNNING":e->flags==3?"ALREADY_RUNNING":"SAVE";text(b,"$GLOVITY,CAL,ERROR,%s\r\n",why);}
   else{
    if(e->type==4&&(!e->switch_ok||!e->save_ok))text(b,"$GLOVITY,CAL,ERROR,WRITE_FAILED\r\n");
    text(b,"$GLOVITY,CAL,%s\r\n",name);if(e->type==4)text(b,"$GLOVITY,MODE,NORMAL\r\n");
   }
  }
 }
}
void telex_legacy_radio(telex_legacy_bridge_t*b,const uint8_t*data,size_t len){
 if(!b)return;
 if(b->role==TELEX_RECEIVER){
  if(len==174){telex_imu_group_t g;uint8_t hand;uint16_t frame;if(!b->maintenance&&telex_legacy_decode_radio(data,len,&g,&hand,&frame)==TELEX_OK&&b->io.usb)b->io.usb(b->io.ctx,data+6,168);}
  else if(len==87){telex_cal_event_t e;uint8_t hand;if(telex_cal_event_decode(data,len,&e,&hand)==TELEX_OK)telex_legacy_event(b,&e,hand);}
 }else{
  uint8_t op,hand;uint16_t sequence;uint32_t session;
  if(telex_cal_command_decode(data,len,&op,&hand,&sequence,&session)==TELEX_OK&&hand==b->role-1&&b->io.cal)(void)b->io.cal(b->io.ctx,op,TELEX_ROUTE_RADIO,session,sequence);
 }
}
