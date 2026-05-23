from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="agx_arm_agent",
            executable="agx_arm_mcp_server",
            name="agx_arm_mcp_server",
            output="screen",
            emulate_tty=True,
        )
    ])
