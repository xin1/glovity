#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef struct {bool valid;int64_t min_offset_us,max_offset_us;uint64_t observed_us;} telex_clock_t;
bool telex_clock_observe(telex_clock_t*,uint64_t t0,uint64_t t1,uint64_t t2,uint64_t t3);
bool telex_clock_max_age(const telex_clock_t*,uint64_t remote_timestamp_us,uint64_t now_us,uint32_t *age_us);
bool telex_unwrap_ms(uint32_t timestamp_ms,uint64_t remote_now_us,uint64_t *timestamp_us);

bool telex_clock_install_inverse(telex_clock_t*,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t);

bool telex_clock_packet_age(const telex_clock_t*,uint32_t remote_timestamp_ms,uint64_t now_us,uint32_t *age_us);
