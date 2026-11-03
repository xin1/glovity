idf_component_register(SRCS "drv_i2c_bus.c" "drv_aw8623x.c" "aw_profile.c" "convert.c" "live_codec.c" "wave_codec.c" "proto_frame.c" "util_crc16.c" "svc_play.c" INCLUDE_DIRS "." REQUIRES telex_core esp_driver_i2c esp_driver_gpio esp_timer)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)
