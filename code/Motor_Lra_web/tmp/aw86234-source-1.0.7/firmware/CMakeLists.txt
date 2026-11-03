# ESP-IDF 工程定义
#
# 目标：ESP32-C6-MINI-1（板卡 Vibration_Test_Board_A_V1.0）
# 用法：
#     cd firmware
#     idf.py set-target esp32c6
#     idf.py build flash monitor

cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

project(aw86234_testbench)
