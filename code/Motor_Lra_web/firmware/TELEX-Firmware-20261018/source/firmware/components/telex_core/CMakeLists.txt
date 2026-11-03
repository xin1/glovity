idf_component_register(SRCS "application.c" "button.c" "led.c" "haptics.c" "wire.c" "policy.c" "identity.c" "board_profile.c" "config.c" "session.c" "clock_sync.c" "router.c" "imu.c" "legacy_wire.c" "calibration.c" "legacy_bridge.c" INCLUDE_DIRS ".")
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)
