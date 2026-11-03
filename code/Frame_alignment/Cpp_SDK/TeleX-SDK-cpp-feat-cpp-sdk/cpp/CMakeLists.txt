# TeleX 手套 SDK — C++ 实现
#
# 交付产物: libtelex_glove.so（Linux）/ telex_glove.dll + .lib（Windows）
#           + include/telex_glove.{h,hpp} + CMake package。
# 源码不随产物分发，算法全部编译进库内。
cmake_minimum_required(VERSION 3.16)
project(TelexGlove VERSION 1.2.0 LANGUAGES CXX C)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

option(TELEX_BUILD_TESTS    "构建单元测试"   ON)
option(TELEX_BUILD_EXAMPLES "构建示例"       ON)
option(TELEX_HARDENED       "发布加固（隐藏符号 / strip / 静态链 libstdc++）" OFF)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)

if(WIN32)
  # DLL 与 exe 输出到同一目录，测试/示例无需改 PATH 即可找到 DLL
  set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
  set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
endif()

if(MSVC)
  # 静态链 C/C++ 运行时（/MT）：集成方机器无需安装 VC++ 运行库。
  # 对外只有 C ABI、内存不跨边界释放，因此与宿主用 /MD 也能安全混用。
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  # 源码与中文标签均为 UTF-8；不加 /utf-8 时 MSVC 按系统代码页（GBK）解读
  add_compile_options(/utf-8 /W4 /permissive-)
  add_compile_definitions(_CRT_SECURE_NO_WARNINGS)
endif()
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

find_package(Threads REQUIRED)

set(TELEX_SOURCES
  src/protocol.cpp
  src/rotation.hpp
  src/file_util.hpp
  src/fk.cpp
  src/npy_loader.cpp
  src/calibration.cpp
  src/data_source.cpp
  src/hub.cpp
  src/labels.cpp
  src/c_api.cpp
)
if(WIN32)
  list(APPEND TELEX_SOURCES src/serial_port_win.cpp)
else()
  list(APPEND TELEX_SOURCES src/serial_port.cpp)
endif()

if(WIN32)
  # DLL 的"属性 → 详细信息"里显示版本号，便于集成方现场核对
  configure_file(packaging/telex_glove.rc.in ${CMAKE_CURRENT_BINARY_DIR}/telex_glove.rc @ONLY)
  add_library(telex_glove SHARED ${TELEX_SOURCES} ${CMAKE_CURRENT_BINARY_DIR}/telex_glove.rc)
else()
  add_library(telex_glove SHARED ${TELEX_SOURCES})
endif()
add_library(TelexGlove::telex_glove ALIAS telex_glove)

set_target_properties(telex_glove PROPERTIES
  VERSION   ${PROJECT_VERSION}
  SOVERSION ${PROJECT_VERSION_MAJOR}
  OUTPUT_NAME telex_glove)

target_compile_definitions(telex_glove PRIVATE
  TELEX_GLOVE_BUILDING
  TELEX_GLOVE_VERSION_STR="${PROJECT_VERSION}")

target_include_directories(telex_glove
  PUBLIC  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
          $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

target_link_libraries(telex_glove PRIVATE Threads::Threads)

# 把语言标准要求随包导出。集成方若未设置 CMAKE_CXX_STANDARD，
# 编译器默认可能是 C++98（AppleClang 与部分 GCC 即如此），
# 那样 telex_glove.hpp 会因大括号初始化而解析失败。
# 库本身用 C++17 编译，但对外只要求 C++11。
target_compile_features(telex_glove INTERFACE cxx_std_11)

# 数值可复现性：禁止 FMA 收缩，否则与 Python 金标准的对拍容差会漂移
if(MSVC)
  # /fp:precise 下 MSVC 默认不做 FMA 收缩（VS2022 起需显式 /fp:contract 才会开启）
  target_compile_options(telex_glove PRIVATE /fp:precise)
elseif(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(telex_glove PRIVATE -ffp-contract=off -Wall -Wextra)
endif()

if(APPLE)
  target_link_libraries(telex_glove PRIVATE "-framework IOKit" "-framework CoreFoundation")
endif()

if(TELEX_HARDENED AND CMAKE_SYSTEM_NAME STREQUAL "Linux")
  target_compile_options(telex_glove PRIVATE
    -O2 -g0 -fno-ident -fvisibility=hidden -fvisibility-inlines-hidden)
  target_link_options(telex_glove PRIVATE
    "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/packaging/telex_glove.map"
    -Wl,--exclude-libs,ALL
    -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack
    -static-libstdc++ -static-libgcc
    -Wl,-s)
  # 去掉二进制里的源码路径
  target_compile_options(telex_glove PRIVATE
    "-ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=.")
endif()

if(TELEX_HARDENED AND MSVC)
  # Windows 下导出天然受控：只有标了 __declspec(dllexport) 的 telex_glove_* 会导出。
  #   /GS /guard:cf           栈保护 + 控制流防护
  #   /DYNAMICBASE /NXCOMPAT  ASLR + DEP；/HIGHENTROPYVA 64 位高熵 ASLR
  #   /DEBUG:NONE             DLL 内不写调试目录，不泄露 .pdb 路径
  #   /Brepro                 去掉时间戳，同一份源码产物可复现
  target_compile_options(telex_glove PRIVATE /O2 /GS /guard:cf /Gy /Brepro)
  target_link_options(telex_glove PRIVATE
    /guard:cf /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA
    /DEBUG:NONE /OPT:REF /OPT:ICF /Brepro)
endif()

# ------------------------------------------------------------------ 测试
if(TELEX_BUILD_TESTS)
  enable_testing()
  set(TELEX_GOLDEN "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden")
  foreach(t rotation protocol fk_uncalib fk_calib npy_loader public_api)
    add_executable(test_${t} tests/test_${t}.cpp)
    target_include_directories(test_${t} PRIVATE
      ${CMAKE_CURRENT_SOURCE_DIR}/src ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_compile_definitions(test_${t} PRIVATE
      TELEX_GOLDEN_DIR_DEFAULT="${TELEX_GOLDEN}"
      TELEX_TEST_TMP_DIR="${CMAKE_CURRENT_BINARY_DIR}")
    if(t STREQUAL "public_api")
      # 公开 API 测试必须走最终 .so，验证的是交付物本身
      target_link_libraries(test_${t} PRIVATE telex_glove)
    else()
      target_sources(test_${t} PRIVATE ${TELEX_SOURCES})
      # 实现直接编进测试程序：须按"构建方"展开导出宏，
      # 否则 Windows 上 dllimport 的函数不允许给出定义
      target_compile_definitions(test_${t} PRIVATE
        TELEX_GLOVE_BUILDING
        TELEX_GLOVE_VERSION_STR="${PROJECT_VERSION}")
      target_link_libraries(test_${t} PRIVATE Threads::Threads)
      if(APPLE)
        target_link_libraries(test_${t} PRIVATE "-framework IOKit" "-framework CoreFoundation")
      endif()
    endif()
    add_test(NAME ${t} COMMAND test_${t})
  endforeach()
endif()

# ------------------------------------------------------------------ 示例
if(TELEX_BUILD_EXAMPLES)
  add_executable(monitor_hands examples/monitor_hands.cpp)
  target_link_libraries(monitor_hands PRIVATE telex_glove)

  add_executable(dump_capture examples/dump_capture.cpp)
  target_link_libraries(dump_capture PRIVATE telex_glove)

  add_executable(hw_selftest examples/hw_selftest.cpp)
  target_link_libraries(hw_selftest PRIVATE telex_glove)

  add_executable(minimal_poll_c examples/minimal_poll.c)
  set_target_properties(minimal_poll_c PROPERTIES C_STANDARD 99 C_STANDARD_REQUIRED ON)
  target_link_libraries(minimal_poll_c PRIVATE telex_glove)
endif()

# ------------------------------------------------------------------ 安装
install(TARGETS telex_glove
  EXPORT  TelexGloveTargets
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
  RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

install(FILES include/telex_glove.h include/telex_glove.hpp
  DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

install(EXPORT TelexGloveTargets
  FILE      TelexGloveTargets.cmake
  NAMESPACE TelexGlove::
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/TelexGlove)

configure_package_config_file(
  cmake/TelexGloveConfig.cmake.in
  ${CMAKE_CURRENT_BINARY_DIR}/TelexGloveConfig.cmake
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/TelexGlove)

write_basic_package_version_file(
  ${CMAKE_CURRENT_BINARY_DIR}/TelexGloveConfigVersion.cmake
  VERSION       ${PROJECT_VERSION}
  COMPATIBILITY SameMajorVersion)

install(FILES
  ${CMAKE_CURRENT_BINARY_DIR}/TelexGloveConfig.cmake
  ${CMAKE_CURRENT_BINARY_DIR}/TelexGloveConfigVersion.cmake
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/TelexGlove)

install(DIRECTORY docs/     DESTINATION share/telex-glove/docs)
install(DIRECTORY examples/ DESTINATION share/telex-glove/examples)
install(PROGRAMS tools/npy_to_tgc.py DESTINATION share/telex-glove/tools)

# 随包提供一段录制流，让集成方无硬件即可验证链路
install(FILES tests/golden/sample_capture.bin DESTINATION share/telex-glove)
