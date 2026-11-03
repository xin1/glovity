# Install script for directory: D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "C:/Program Files/TelexGlove")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "Release")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "FALSE")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  if(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Dd][Ee][Bb][Uu][Gg])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib" TYPE STATIC_LIBRARY OPTIONAL FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/lib/Debug/telex_glove.lib")
  elseif(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Rr][Ee][Ll][Ee][Aa][Ss][Ee])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib" TYPE STATIC_LIBRARY OPTIONAL FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/lib/Release/telex_glove.lib")
  elseif(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Mm][Ii][Nn][Ss][Ii][Zz][Ee][Rr][Ee][Ll])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib" TYPE STATIC_LIBRARY OPTIONAL FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/lib/MinSizeRel/telex_glove.lib")
  elseif(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Rr][Ee][Ll][Ww][Ii][Tt][Hh][Dd][Ee][Bb][Ii][Nn][Ff][Oo])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib" TYPE STATIC_LIBRARY OPTIONAL FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/lib/RelWithDebInfo/telex_glove.lib")
  endif()
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  if(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Dd][Ee][Bb][Uu][Gg])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/bin" TYPE SHARED_LIBRARY FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/bin/Debug/telex_glove.dll")
  elseif(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Rr][Ee][Ll][Ee][Aa][Ss][Ee])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/bin" TYPE SHARED_LIBRARY FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/bin/Release/telex_glove.dll")
  elseif(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Mm][Ii][Nn][Ss][Ii][Zz][Ee][Rr][Ee][Ll])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/bin" TYPE SHARED_LIBRARY FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/bin/MinSizeRel/telex_glove.dll")
  elseif(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Rr][Ee][Ll][Ww][Ii][Tt][Hh][Dd][Ee][Bb][Ii][Nn][Ff][Oo])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/bin" TYPE SHARED_LIBRARY FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/bin/RelWithDebInfo/telex_glove.dll")
  endif()
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include" TYPE FILE FILES
    "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/include/telex_glove.h"
    "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/include/telex_glove.hpp"
    )
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  if(EXISTS "$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove/TelexGloveTargets.cmake")
    file(DIFFERENT _cmake_export_file_changed FILES
         "$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove/TelexGloveTargets.cmake"
         "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/CMakeFiles/Export/ae1887094ea355d222f8ebe66409db8c/TelexGloveTargets.cmake")
    if(_cmake_export_file_changed)
      file(GLOB _cmake_old_config_files "$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove/TelexGloveTargets-*.cmake")
      if(_cmake_old_config_files)
        string(REPLACE ";" ", " _cmake_old_config_files_text "${_cmake_old_config_files}")
        message(STATUS "Old export file \"$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove/TelexGloveTargets.cmake\" will be replaced.  Removing files [${_cmake_old_config_files_text}].")
        unset(_cmake_old_config_files_text)
        file(REMOVE ${_cmake_old_config_files})
      endif()
      unset(_cmake_old_config_files)
    endif()
    unset(_cmake_export_file_changed)
  endif()
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove" TYPE FILE FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/CMakeFiles/Export/ae1887094ea355d222f8ebe66409db8c/TelexGloveTargets.cmake")
  if(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Dd][Ee][Bb][Uu][Gg])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove" TYPE FILE FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/CMakeFiles/Export/ae1887094ea355d222f8ebe66409db8c/TelexGloveTargets-debug.cmake")
  endif()
  if(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Mm][Ii][Nn][Ss][Ii][Zz][Ee][Rr][Ee][Ll])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove" TYPE FILE FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/CMakeFiles/Export/ae1887094ea355d222f8ebe66409db8c/TelexGloveTargets-minsizerel.cmake")
  endif()
  if(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Rr][Ee][Ll][Ww][Ii][Tt][Hh][Dd][Ee][Bb][Ii][Nn][Ff][Oo])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove" TYPE FILE FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/CMakeFiles/Export/ae1887094ea355d222f8ebe66409db8c/TelexGloveTargets-relwithdebinfo.cmake")
  endif()
  if(CMAKE_INSTALL_CONFIG_NAME MATCHES "^([Rr][Ee][Ll][Ee][Aa][Ss][Ee])$")
    file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove" TYPE FILE FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/CMakeFiles/Export/ae1887094ea355d222f8ebe66409db8c/TelexGloveTargets-release.cmake")
  endif()
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/cmake/TelexGlove" TYPE FILE FILES
    "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/TelexGloveConfig.cmake"
    "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/TelexGloveConfigVersion.cmake"
    )
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/share/telex-glove/docs" TYPE DIRECTORY FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/docs/")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/share/telex-glove/examples" TYPE DIRECTORY FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/examples/")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/share/telex-glove/tools" TYPE PROGRAM FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/tools/npy_to_tgc.py")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/share/telex-glove" TYPE FILE FILES "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/tests/golden/sample_capture.bin")
endif()

string(REPLACE ";" "\n" CMAKE_INSTALL_MANIFEST_CONTENT
       "${CMAKE_INSTALL_MANIFEST_FILES}")
if(CMAKE_INSTALL_LOCAL_ONLY)
  file(WRITE "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/install_local_manifest.txt"
     "${CMAKE_INSTALL_MANIFEST_CONTENT}")
endif()
if(CMAKE_INSTALL_COMPONENT)
  if(CMAKE_INSTALL_COMPONENT MATCHES "^[a-zA-Z0-9_.+-]+$")
    set(CMAKE_INSTALL_MANIFEST "install_manifest_${CMAKE_INSTALL_COMPONENT}.txt")
  else()
    string(MD5 CMAKE_INST_COMP_HASH "${CMAKE_INSTALL_COMPONENT}")
    set(CMAKE_INSTALL_MANIFEST "install_manifest_${CMAKE_INST_COMP_HASH}.txt")
    unset(CMAKE_INST_COMP_HASH)
  endif()
else()
  set(CMAKE_INSTALL_MANIFEST "install_manifest.txt")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  file(WRITE "D:/Desktop/github/glovity/code/Frame_alignment/Cpp_SDK/TeleX-SDK-cpp-feat-cpp-sdk/cpp/build-win/${CMAKE_INSTALL_MANIFEST}"
     "${CMAKE_INSTALL_MANIFEST_CONTENT}")
endif()
