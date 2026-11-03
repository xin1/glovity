#pragma once
#include "identity.h"
typedef struct {int lp_sda,lp_scl,hp_sda,hp_scl,uart0_tx,uart0_rx,uart1_tx,uart1_rx;} telex_board_profile_t;
telex_board_profile_t telex_board_profile(telex_chip_t);
void telex_safe_outputs(telex_chip_t,void(*write_pin)(void*,int,int),void*);
