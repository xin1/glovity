# main 组件
#
# 分层：
#   proto_frame / util_crc16 / wave_codec / aw_profile / convert  —— 纯逻辑（主机可单测）
#   drv_*                                                        —— 器件驱动，不含协议解析
#   svc_*                                                        —— 业务服务
#   app_*                                                        —— 协议/会话/ASCII/启动

idf_component_register(
    SRCS
        "app_main.c"
        "app_protocol.c"
        "app_ascii.c"
        "app_session.c"
        "svc_channel.c"
        "svc_play.c"
        "svc_wave.c"
        "svc_measure.c"
        "svc_sweep.c"
        "svc_selftest.c"
        "svc_bootdemo.c"
        "drv_i2c_bus.c"
        "drv_tca9548a.c"
        "drv_aw8623x.c"
        "proto_frame.c"
        "util_crc16.c"
        "util_log.c"
        "wave_codec.c"
        "live_codec.c"
        "aw_profile.c"
        "convert.c"
    INCLUDE_DIRS
        "."
    REQUIRES
        esp_driver_i2c
        esp_driver_gpio
        esp_driver_usb_serial_jtag
        esp_timer
        nvs_flash
        esp_system
)

# 编译要求：无警告（任务书 §3.1）。主机单测（test/）不参与固件构建。
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Wno-unused-parameter)
