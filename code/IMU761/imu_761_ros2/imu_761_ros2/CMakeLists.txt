cmake_minimum_required(VERSION 3.8)
project(imu_761_ros2)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/ImuPackage.msg"
  DEPENDENCIES std_msgs
)

add_executable(imu_761_node
  src/imu_761_node.cpp
  src/protocol.cpp
)

ament_target_dependencies(imu_761_node
  rclcpp
  sensor_msgs
  std_msgs
) 

rosidl_get_typesupport_target(cpp_typesupport_target
  ${PROJECT_NAME} "rosidl_typesupport_cpp")
target_link_libraries(imu_761_node "${cpp_typesupport_target}")

target_include_directories(imu_761_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

install(TARGETS imu_761_node
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch cfg include
  DESTINATION share/${PROJECT_NAME}
)


ament_package()
