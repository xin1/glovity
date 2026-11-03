#include "wire.h"
#include <string.h>
static uint16_t get16(const uint8_t *p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
static uint32_t get32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void put16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void put32(uint8_t *p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
uint16_t telex_crc16(const uint8_t *data,size_t len){
 uint16_t crc=0xffff;
 for(size_t i=0;i<len;i++){
  crc^=(uint16_t)data[i]<<8;
  for(unsigned j=0;j<8;j++)crc=(uint16_t)((crc<<1)^((crc&0x8000)?0x1021:0));
 }
 return crc;
}
bool telex_kind_valid(uint8_t k){
 switch(k){case 1:case 2:case 3:case 0x10:case 0x11:case 0x20:case 0x21:case 0x22:case 0x23:case 0x30:case 0x31:case 0x40:case 0x41:case 0x7e:case 0x7f:return true;default:return false;}
}
telex_result_t telex_encode(const telex_packet_t*p,uint8_t*out,size_t capacity,size_t*written){
 if(!p||!out||!written)return TELEX_INVALID;
 *written=0;
 if(p->payload_len>TELEX_MAX_PAYLOAD||capacity<26u+p->payload_len)return TELEX_SIZE;
 if(!telex_kind_valid(p->kind)||p->flags||p->source>3||p->target>3)return TELEX_INVALID;
 memset(out,0,24);out[0]='T';out[1]='X';out[2]=1;out[3]=p->kind;out[5]=p->source;out[6]=p->target;
 put32(out+8,p->session_id);put32(out+12,p->sequence);put32(out+16,p->timestamp_ms);put16(out+20,p->payload_len);
 memcpy(out+24,p->payload,p->payload_len);put16(out+24+p->payload_len,telex_crc16(out,24+p->payload_len));
 *written=26+p->payload_len;return TELEX_OK;
}
telex_result_t telex_decode(const uint8_t*data,size_t len,telex_packet_t*out){
 if(!data||!out)return TELEX_INVALID;
 if(len<26||len>TELEX_MAX_PACKET)return TELEX_SIZE;
 if(data[0]!='T'||data[1]!='X'||data[2]!=1||data[4]||data[7]||data[22]||data[23])return TELEX_INVALID;
 if(!telex_kind_valid(data[3])||data[5]>3||data[6]>3)return TELEX_INVALID;
 uint16_t n=get16(data+20);if(n!=len-26)return TELEX_SIZE;
 if(telex_crc16(data,len-2)!=get16(data+len-2))return TELEX_CRC;
 memset(out,0,sizeof(*out));out->kind=data[3];out->source=data[5];out->target=data[6];
 out->session_id=get32(data+8);out->sequence=get32(data+12);out->timestamp_ms=get32(data+16);out->payload_len=n;
 memcpy(out->payload,data+24,n);return TELEX_OK;
}
telex_result_t telex_encode_usb(const telex_packet_t*p,uint8_t*out,size_t capacity,size_t*written){
 uint8_t raw[TELEX_MAX_PACKET];size_t n=0;
 if(!out||!written)return TELEX_INVALID;
 *written=0;telex_result_t status=telex_encode(p,raw,sizeof(raw),&n);if(status!=TELEX_OK)return status;
 /* n <= 250, so COBS never needs a 254-byte nonzero block. */
 if(capacity<n+2)return TELEX_SIZE;
 size_t start=0,pos=1;uint8_t count=1;
 for(size_t i=0;i<n;i++){
  if(raw[i]==0){out[start]=count;start=pos++;count=1;}
  else{out[pos++]=raw[i];count++;}
 }
 out[start]=count;out[pos++]=0;*written=pos;return TELEX_OK;
}
int telex_usb_push(telex_usb_decoder_t*d,uint8_t value,telex_packet_t*out){
 if(!d||!out)return -1;
 if(value){
  if(!d->discarding){
   if(d->used==sizeof(d->buffer)){d->used=0;d->discarding=true;d->errors++;}
   else d->buffer[d->used++]=value;
  }
  return 0;
 }
 if(d->discarding||!d->used){d->discarding=false;d->used=0;return 0;}
 uint8_t raw[250];size_t pos=0,n=0;bool bad=false;
 while(pos<d->used){
  uint8_t count=d->buffer[pos++];
  if(!count||pos+count-1>d->used||n+count-1>sizeof(raw)){bad=true;break;}
  memcpy(raw+n,d->buffer+pos,count-1);n+=count-1;pos+=count-1;
  if(count!=255&&pos<d->used){if(n==sizeof(raw)){bad=true;break;}raw[n++]=0;}
 }
 d->used=0;
 if(bad||telex_decode(raw,n,out)!=TELEX_OK){d->errors++;return -1;}
 return 1;
}
