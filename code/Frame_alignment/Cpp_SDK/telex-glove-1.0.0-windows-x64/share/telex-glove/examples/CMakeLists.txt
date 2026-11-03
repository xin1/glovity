cmake_minimum_required(VERSION 3.20)
project(TelexGloveWindowsExamples LANGUAGES C CXX)

if(NOT WIN32)
    message(FATAL_ERROR "This example project is for the Windows SDK package")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(TelexGlove 1.0 REQUIRED)

add_executable(glovity_v2_monitor glovity_v2_monitor.cpp)
target_link_libraries(glovity_v2_monitor PRIVATE TelexGlove::telex_glove)
if(MSVC)
    target_compile_options(glovity_v2_monitor PRIVATE /utf-8 /W4)
endif()

add_executable(monitor_hands monitor_hands.cpp)
target_link_libraries(monitor_hands PRIVATE TelexGlove::telex_glove)
if(MSVC)
    target_compile_options(monitor_hands PRIVATE /utf-8 /W4)
endif()
