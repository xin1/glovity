#include "identity.h"
telex_role_t telex_role_from_straps(telex_chip_t chip,uint8_t a,uint8_t b,bool stable){
 if(!stable||a>1||b>1)return TELEX_UNKNOWN;
 if(chip==TELEX_S3){if(a==1&&b==0)return TELEX_LEFT;if(a==0&&b==1)return TELEX_RIGHT;}
 if(chip==TELEX_C6&&a==0&&b==0)return TELEX_RECEIVER;
 return TELEX_UNKNOWN;
}
uint8_t telex_battery_percent(bool known,uint16_t mv){
 (void)known;(void)mv;
 /* No verified chemistry/charging curve: never derive SOC from charger voltage. */
 return 255;
}
