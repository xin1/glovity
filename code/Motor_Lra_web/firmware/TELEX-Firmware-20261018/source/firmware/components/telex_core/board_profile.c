#include "board_profile.h"
telex_board_profile_t telex_board_profile(telex_chip_t chip){
 if(chip==TELEX_S3)return (telex_board_profile_t){10,11,6,7,43,44,17,18};
 return (telex_board_profile_t){-1,-1,-1,-1,-1,-1,-1,-1};
}
void telex_safe_outputs(telex_chip_t chip,void(*write_pin)(void*,int,int),void*ctx){
 if(!write_pin)return;
 if(chip==TELEX_S3){
  const int pins[]={4,1,12,21,14,13,15,5};const int levels[]={1,1,0,0,0,1,1,1};
  for(unsigned i=0;i<8;i++)write_pin(ctx,pins[i],levels[i]);
 }else if(chip==TELEX_C6){write_pin(ctx,6,0);write_pin(ctx,7,0);}
}
