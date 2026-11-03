#include "svc_play.h"
#include "drv_i2c_bus.h"
#include "drv_aw8623x.h"
#include "cfg_board.h"
#include "cfg_defaults.h"
#include "proto_cmd.h"
#include "esp_timer.h"
#include "wave_codec.h"
#include <string.h>
static uint8_t previous[5];
static bool ram_mode[5];
// The archived BOOT sine is 72 samples at 12 kHz (166.67 Hz actual).
// Store one exact cycle and repeat it ten times: same 720 samples, less cold I2C work.
static bool load_boot_wave(uint8_t ch){
 int8_t samples[72];uint16_t n=0;uint8_t packed[77],readback[77];wave_lib_t lib;
 if(wave_gen_samples(0,168,127,1,DEF_WAVDAT_MODE,samples,sizeof samples,&n,NULL)||n!=72)return false;
 wave_input_t wave={1,samples,n};
 if(wave_lib_plan(DEF_BASE_ADDR,1,&wave,1,&lib)||wave_lib_pack(&lib,&wave,1,packed,sizeof packed)!=sizeof packed)return false;
 if(aw_wave_lib_write(ch,DEF_BASE_ADDR,packed,sizeof packed)!=ESP_OK||
    aw_update(ch,REG_SYSCTRL1,BIT_EN_RAMINIT,BIT_EN_RAMINIT,NULL,NULL)!=ESP_OK)return false;
 bool ok=aw_wave_lib_read(ch,DEF_BASE_ADDR,readback,sizeof readback)==ESP_OK&&!memcmp(packed,readback,sizeof packed);
 return aw_update(ch,REG_SYSCTRL1,BIT_EN_RAMINIT,0,NULL,NULL)==ESP_OK&&ok;
}
uint8_t telex_motor_probe(void){
 if(telex_motor_bus_init()!=ESP_OK||!telex_motor_power(true))return 0;
 uint8_t mask=0;
 for(unsigned i=0;i<5;i++){uint16_t id=0;if(aw_read_chipid(i,&id)==ESP_OK&&id==BOARD_AW_CHIPID_EXPECTED)mask|=(uint8_t)(1u<<i);}
 (void)telex_motor_power(false);return mask;
}
static bool prepare(void*ctx,uint8_t mask){
 (void)ctx;if(!telex_motor_power(true))return false;
 for(unsigned ch=0;ch<5;ch++)if(mask&(1u<<ch)){
  aw_profile_t p;aw_profile_defaults(&p);
  p.mode=AW_MODE_CONT;p.gain=128;p.vbat_mode=0;p.vbat_ref=DEF_VBAT_REF;p.stop_mode=1;
  p.en_fir=1;p.en_f0_det=0;p.brk_en=0;p.brk_time=0;
  p.f_pre=(240000u+1680u/2)/1680u;p.drv_width=80;p.track_margin=DEF_TRACK_MARGIN;
  p.brk_gain=8;p.wavdat_mode=DEF_WAVDAT_MODE;p.track_en=0;p.wait_num=0;p.drv1_time=255;p.drv2_time=0;
  p.sin_mode=1;p.edge_fre=14;p.drv1_lvl=0;p.drv2_lvl=0;
  if(aw_softrst(ch)!=ESP_OK||aw_wake(ch)!=ESP_OK||aw_stop(ch,STOP_MODE_IMMEDIATE)!=ESP_OK||aw_apply_profile(ch,&p)!=ESP_OK||
     aw_update(ch,0x17,0x20,0,NULL,NULL)!=ESP_OK||aw_update(ch,REG_SYSCTRL1,BIT_EN_RAMINIT|BIT_EN_FIR,BIT_EN_FIR,NULL,NULL)!=ESP_OK||aw_clear_sysint(ch)!=ESP_OK)return false;
  // Shared physical TRIG is always parked; only explicit I2C GO can activate a channel.
  if(aw_update(ch,0x33,0x80,0,NULL,NULL)!=ESP_OK)return false;
  if(!load_boot_wave(ch))return false;
  ch_ctx(ch)->profile=p;previous[ch]=0;ram_mode[ch]=false;
 }
 return true;
}
static bool level(void*ctx,uint8_t ch,uint8_t amp){
 (void)ctx;if(ch>=5||amp>60)return false;
 if(ram_mode[ch]){
  if(aw_stop(ch,STOP_MODE_IMMEDIATE)!=ESP_OK||aw_apply_profile(ch,&ch_ctx(ch)->profile)!=ESP_OK)return false;
  ram_mode[ch]=false;previous[ch]=0;
 }
 if(aw_update(ch,0x1c,0x7f,amp,NULL,NULL)!=ESP_OK)return false;
 if(amp&&!previous[ch]){
  if(aw_update(ch,0x08,4,0,NULL,NULL)!=ESP_OK||aw_write_u8(ch,0x20,0)!=ESP_OK||aw_play_go(ch)!=ESP_OK)return false;
 }
 previous[ch]=amp;return true;
}
static bool brake(void*ctx,uint8_t ch){
 (void)ctx;return aw_write_u8(ch,0x20,4)==ESP_OK&&aw_update(ch,0x08,4,4,NULL,NULL)==ESP_OK&&aw_stop(ch,STOP_MODE_IMMEDIATE)==ESP_OK;
}
static bool clear(void*ctx,uint8_t ch){
 (void)ctx;return aw_update(ch,0x08,4,0,NULL,NULL)==ESP_OK&&aw_write_u8(ch,0x20,0)==ESP_OK&&aw_stop(ch,STOP_MODE_IMMEDIATE)==ESP_OK;
}
static bool stop(void*ctx,uint8_t ch){previous[ch]=0;return clear(ctx,ch);}
static bool ram_profile(uint8_t ch,uint8_t amp,uint16_t duration){
 aw_profile_t p;aw_profile_defaults(&p);p.mode=AW_MODE_RAM;
 p.gain=(uint8_t)(amp*128u/40u);p.wavseq[0]=1;
 // Finite hardware loops only (0xF means infinite and is never used).
 unsigned cycles=(duration+5u)/6u,inner=cycles<14?cycles:14,outer=(cycles+inner-1)/inner;
 p.seq_loop[0]=(uint8_t)((inner-1)<<4);p.mainloop=(uint8_t)(outer-1);
 if(aw_stop(ch,STOP_MODE_IMMEDIATE)!=ESP_OK||aw_apply_profile(ch,&p)!=ESP_OK||
    aw_update(ch,REG_SYSCTRL1,BIT_EN_RAMINIT|BIT_EN_FIR,BIT_EN_FIR,NULL,NULL)!=ESP_OK||
    aw_clear_sysint(ch)!=ESP_OK)return false;
 ram_mode[ch]=true;previous[ch]=0;return true;
}
static bool configure_finite(void*ctx,uint8_t mask,uint8_t amp,uint16_t duration){
 (void)ctx;if(!mask||(mask&~31)||amp>40||!duration||duration>1000)return false;
 for(unsigned ch=0;ch<5;ch++)if(mask&(1u<<ch))if(!ram_profile(ch,amp,duration))return false;
 return true;
}
static bool ram_level(void*ctx,uint8_t ch,uint8_t amp,uint16_t duration){
 (void)ctx;if(ch>=5||amp>40||duration>1000||(!duration&&amp))return false;
 if(!amp){previous[ch]=0;return true;} // core already issued BRAKE; preserve its 16ms cleanup
 if(!ram_mode[ch])return false;
 if(previous[ch])return aw_write_u8(ch,REG_PLAYCFG2,(uint8_t)(amp*128u/40u))==ESP_OK;
 // The profile is already configured. GO is the only work in the first launch.
 // Restore BRK_EN after an envelope gap's STOP/clear, without changing CONT state.
 if(aw_update(ch,REG_PLAYCFG3,4,4,NULL,NULL)!=ESP_OK||aw_play_go(ch)!=ESP_OK)return false;
 previous[ch]=amp;return true;
}
static bool power_off(void*ctx){(void)ctx;return telex_motor_power(false);}
static uint64_t clock_now(void*ctx){(void)ctx;return (uint64_t)esp_timer_get_time();}
telex_haptic_io_t telex_motor_io(void){return (telex_haptic_io_t){prepare,level,brake,clear,stop,power_off,NULL,clock_now,ram_level,configure_finite};}
uint8_t telex_motor_faults(uint8_t mask){
 uint8_t faults=0;for(unsigned ch=0;ch<5;ch++)if(mask&(1u<<ch)){
  uint8_t st=0,irq=0;if(aw_get_sysst(ch,&st)!=ESP_OK||aw_get_sysint(ch,&irq)!=ESP_OK||(st&0x26)||(irq&0x26))faults|=(uint8_t)(1u<<ch);
 }
 return faults;
}
