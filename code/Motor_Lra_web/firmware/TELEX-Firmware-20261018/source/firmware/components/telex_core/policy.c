#include "policy.h"
telex_result_t telex_validate_config(const telex_config_t*c){
 if(!c||c->mode>1||c->imu_profile>1||c->group_rate_hz<1||c->group_rate_hz>100)return TELEX_INVALID;
 if(c->mode==0&&(c->haptics_enabled||c->imu_profile!=0||c->group_rate_hz!=30))return TELEX_INVALID;
 return TELEX_OK;
}
bool telex_command_allowed(const telex_config_t*c,uint8_t kind){
 if(telex_validate_config(c)!=TELEX_OK||!telex_kind_valid(kind))return false;
 if(kind>=0x20&&kind<=0x23)return c->mode==1&&c->haptics_enabled;
 return true;
}
