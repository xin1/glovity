#include "config.h"
#include <string.h>
static uint32_t read32(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
telex_result_t telex_settings_encode(const telex_settings_t*s,uint8_t out[TELEX_SETTINGS_BYTES]){
 if(!s||!out||telex_validate_config(&s->config)!=TELEX_OK||s->peer_count>3||!memchr(s->suite_id,0,32))return TELEX_INVALID;
 for(const char*p=s->suite_id;*p;p++)if(!((*p>='A'&&*p<='Z')||(*p>='a'&&*p<='z')||(*p>='0'&&*p<='9')||*p=='_'||*p=='-'))return TELEX_INVALID;
 telex_trigger_config_t t=telex_trigger_defaults(s->trigger);
 unsigned energy=0;for(unsigned i=0;i<5;i++)if(t.mask&(1u<<i))energy+=t.strength*t.strength;
 if(t.preset>3||(t.mask&~31u)||t.strength>40||energy>7200||t.duration_ms>1000||t.active_level>1||t.debounce_ms>100||t.long_ms<200||t.long_ms>3000)return TELEX_INVALID;
 memset(out,0,96);memcpy(out,"TXCF",4);out[4]=1;
 for(unsigned i=0;i<4;i++)out[8+i]=(uint8_t)(s->generation>>(8*i));
 out[12]=s->config.mode;out[13]=s->config.imu_profile;out[14]=s->config.diagnostics_enabled;out[15]=s->config.haptics_enabled;
 out[16]=(uint8_t)s->config.group_rate_hz;out[17]=(uint8_t)(s->config.group_rate_hz>>8);
 memcpy(out+20,s->uid,6);memcpy(out+26,s->suite_id,strlen(s->suite_id));out[58]=s->peer_count;
 memcpy(out+59,s->peers,(size_t)s->peer_count*6);
 out[77]=t.preset;out[78]=t.mask;out[79]=t.strength;out[80]=(uint8_t)t.duration_ms;out[81]=(uint8_t)(t.duration_ms>>8);out[82]=t.active_level;out[83]=t.key_verified;out[84]=(uint8_t)t.debounce_ms;out[85]=(uint8_t)(t.debounce_ms>>8);out[86]=(uint8_t)t.long_ms;out[87]=(uint8_t)(t.long_ms>>8);
 uint16_t crc=telex_crc16(out,94);out[94]=(uint8_t)crc;out[95]=(uint8_t)(crc>>8);return TELEX_OK;
}
telex_result_t telex_settings_decode(const uint8_t*b,size_t n,telex_settings_t*s){
 if(!b||!s||n!=96)return TELEX_SIZE;
 if(memcmp(b,"TXCF",4))return TELEX_INVALID;
 if(telex_crc16(b,94)!=(uint16_t)(b[94]|((uint16_t)b[95]<<8)))return TELEX_CRC;
 if(b[4]!=1)return TELEX_UNSUPPORTED;
 if(b[5]||b[6]||b[7]||b[18]||b[19]||b[14]>1||b[15]>1||b[58]>3||!memchr(b+26,0,32))return TELEX_INVALID;
 for(unsigned i=88;i<94;i++)if(b[i])return TELEX_INVALID;
 memset(s,0,sizeof(*s));s->generation=read32(b+8);
 s->config=(telex_config_t){b[12],b[13],b[14]!=0,b[15]!=0,(uint16_t)(b[16]|((uint16_t)b[17]<<8))};
 if(telex_validate_config(&s->config)!=TELEX_OK)return TELEX_INVALID;
 memcpy(s->uid,b+20,6);memcpy(s->suite_id,b+26,32);s->peer_count=b[58];memcpy(s->peers,b+59,(size_t)s->peer_count*6);
 if(b[83]>1)return TELEX_INVALID;
 s->trigger=(telex_trigger_config_t){b[77],b[78],b[79],(uint16_t)(b[80]|b[81]<<8),b[82],b[83]!=0,(uint16_t)(b[84]|b[85]<<8),(uint16_t)(b[86]|b[87]<<8)};
 uint8_t verify[96];return telex_settings_encode(s,verify);
}
telex_result_t telex_settings_choose(const uint8_t*a,size_t an,const uint8_t*b,size_t bn,telex_settings_t*out){
 if(!out)return TELEX_INVALID;
 telex_settings_t sa,sb;telex_result_t ra=telex_settings_decode(a,an,&sa),rb=telex_settings_decode(b,bn,&sb);
 if(ra==TELEX_UNSUPPORTED||rb==TELEX_UNSUPPORTED)return TELEX_UNSUPPORTED;
 if(ra!=TELEX_OK&&rb!=TELEX_OK)return TELEX_INVALID;
 if(ra!=TELEX_OK){*out=sb;return TELEX_OK;}
 if(rb!=TELEX_OK){*out=sa;return TELEX_OK;}
 uint32_t delta=sb.generation-sa.generation;*out=(delta&&delta<0x80000000u)?sb:sa;return TELEX_OK;
}

telex_trigger_config_t telex_trigger_defaults(telex_trigger_config_t c){
 if(!c.mask)c.mask=31;
 if(!c.strength)c.strength=36;
 if(!c.duration_ms)c.duration_ms=60;
 if(!c.key_verified)c.active_level=1;
 if(!c.debounce_ms)c.debounce_ms=30;
 if(!c.long_ms)c.long_ms=600;
 return c;
}
