#pragma once
#include "haptics.h"
uint8_t telex_motor_probe(void);
telex_haptic_io_t telex_motor_io(void);
uint8_t telex_motor_faults(uint8_t mask);
