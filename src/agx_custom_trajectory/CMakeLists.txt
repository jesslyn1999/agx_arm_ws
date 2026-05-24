cmake_minimum_required(VERSION 3.8)
project(agx_custom_trajectory)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/PlayTrajectory.srv"
  "srv/GoHome.srv"
  "srv/SmartHeight.srv"
)

install(PROGRAMS
  scripts/trajectory_player_node
  scripts/smart-height_node
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
