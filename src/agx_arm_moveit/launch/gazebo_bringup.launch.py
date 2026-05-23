"""Gazebo Harmonic simulation bringup for AGX arm.

Launches Gazebo, spawns the robot URDF, starts ros2_control via the
gz_ros2_control plugin (controller manager inside Gazebo), then brings up
MoveIt move_group and optionally RViz.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    OpaqueFunction,
    RegisterEventHandler,
)
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from moveit_configs_utils.launch_utils import DeclareBooleanLaunchArg

from _moveit_config_builder import (
    ALL_ARM_TYPES,
    ALL_EFFECTOR_TYPES,
    ALL_REVO2_TYPES,
    build_moveit_config,
    build_ros2_controllers_file,
)


# ── Controller lists ──

ACTIVE_CONTROLLERS = ["arm_controller"]
INACTIVE_CONTROLLERS = []


def _controller_spawner(name, *, inactive=False, timeout=60):
    args = [name, "--controller-manager", "/controller_manager"]
    if inactive:
        args.append("--inactive")
    args += ["--controller-manager-timeout", str(timeout)]
    return Node(
        package="controller_manager",
        executable="spawner",
        arguments=args,
        output="screen",
    )


def _launch_setup(context, *args, **kwargs):
    # ── Validate gz_ros2_control is installed ──
    try:
        get_package_share_directory("gz_ros2_control")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "Gazebo simulation requires gz_ros2_control. Install it with:\n"
            "  sudo apt install ros-jazzy-gz-ros2-control\n"
            "then rebuild/source the workspace."
        ) from exc

    # ── Resolve launch arguments ──
    arm_type = LaunchConfiguration("arm_type").perform(context)
    effector_type = LaunchConfiguration("effector_type").perform(context)
    revo2_type = LaunchConfiguration("revo2_type").perform(context)
    namespace = LaunchConfiguration("namespace").perform(context)
    gazebo_gui = LaunchConfiguration("gazebo_gui").perform(context)
    world_file = LaunchConfiguration("world_file").perform(context)
    use_rviz = LaunchConfiguration("use_rviz")
    robot_name = "agx_arm"

    # ── Build controllers YAML (temp file) ──
    controllers_yaml = build_ros2_controllers_file(
        arm_type, effector_type, revo2_type, namespace, for_gazebo=True
    )

    # ── Build MoveIt config using the Gazebo URDF ──
    moveit_config = build_moveit_config(
        context,
        urdf_file="config/agx_arm_gazebo.urdf.xacro",
        extra_urdf_mappings={
            "name": robot_name,
            "simulation_controllers": controllers_yaml,
        },
    )

    robot_description_content = moveit_config.robot_description["robot_description"]

    # ── Set GZ_SIM_RESOURCE_PATH so Gazebo can find meshes ──
    description_share = get_package_share_directory("agx_arm_description")
    os.environ["GZ_SIM_RESOURCE_PATH"] = os.pathsep.join(
        filter(None, [
            os.environ.get("GZ_SIM_RESOURCE_PATH", ""),
            os.path.dirname(description_share),
            description_share,
        ])
    )

    # ── robot_state_publisher (sim time) ──
    robot_state_publisher = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        output="screen",
        parameters=[
            moveit_config.robot_description,
            {"use_sim_time": True},
        ],
    )

    # ── Gazebo Harmonic ──
    gz_args = f"-r -v 3 --render-engine ogre {world_file}"
    if gazebo_gui != "true":
        gz_args = f"-s {gz_args}"

    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            FindPackageShare("ros_gz_sim"), "/launch/gz_sim.launch.py"
        ]),
        launch_arguments={"gz_args": gz_args}.items(),
    )

    # ── Clock bridge (Gazebo → ROS) ──
    clock_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=["/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock"],
        output="screen",
    )

    # ── Spawn robot into Gazebo ──
    spawn_robot = Node(
        package="ros_gz_sim",
        executable="create",
        output="screen",
        arguments=[
            "-string", robot_description_content,
            "-name", robot_name,
            "-allow_renaming", "false",
        ],
    )

    # ── Controller spawners (chained via event handlers) ──
    joint_state_broadcaster = _controller_spawner("joint_state_broadcaster", timeout=60)

    active_controllers = list(ACTIVE_CONTROLLERS)
    if effector_type == "agx_gripper":
        active_controllers.append("gripper_controller")
    elif effector_type == "revo2":
        active_controllers.append(f"{revo2_type}_hand_controller")

    active_spawners = [_controller_spawner(c, timeout=60) for c in active_controllers]
    inactive_spawners = [_controller_spawner(c, inactive=True, timeout=60) for c in INACTIVE_CONTROLLERS]

    # ── MoveIt move_group ──
    move_group_params = [
        moveit_config.to_dict(),
        {"use_sim_time": True},
    ]
    move_group_node = Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=move_group_params,
    )

    # ── RViz ──
    rviz_config = str(moveit_config.package_path / "config" / "moveit.rviz")
    rviz_params = [
        moveit_config.robot_description,
        moveit_config.robot_description_semantic,
        moveit_config.robot_description_kinematics,
        moveit_config.joint_limits,
        {"use_sim_time": True},
    ]
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        output="screen",
        arguments=["-d", rviz_config],
        parameters=rviz_params,
        condition=IfCondition(LaunchConfiguration("use_rviz")),
    )

    # ── Startup sequence ──
    # 1. RSP + Gazebo + clock bridge start immediately
    # 2. After spawn completes → start joint_state_broadcaster
    # 3. After JSB completes → start controllers + MoveIt + RViz
    return [
        robot_state_publisher,
        gz_sim,
        clock_bridge,
        spawn_robot,
        RegisterEventHandler(
            OnProcessExit(
                target_action=spawn_robot,
                on_exit=[joint_state_broadcaster],
            )
        ),
        RegisterEventHandler(
            OnProcessExit(
                target_action=joint_state_broadcaster,
                on_exit=active_spawners + inactive_spawners + [move_group_node, rviz_node],
            )
        ),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            "namespace", default_value="",
            description="ROS namespace for this arm instance.",
        ),
        DeclareLaunchArgument(
            "arm_type", default_value="piper",
            choices=ALL_ARM_TYPES, description="Arm type.",
        ),
        DeclareLaunchArgument(
            "effector_type", default_value="none",
            choices=ALL_EFFECTOR_TYPES, description="Effector type.",
        ),
        DeclareLaunchArgument(
            "revo2_type", default_value="left",
            choices=ALL_REVO2_TYPES,
            description="Revo2 side (used when effector_type is revo2).",
        ),
        DeclareLaunchArgument(
            "tcp_offset", default_value="[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]",
            description="TCP offset [x, y, z, rx, ry, rz] in meters/radians.",
        ),
        DeclareLaunchArgument(
            "follow", default_value="false",
            choices=["true", "false"],
            description="Unused in simulation; kept for interface compatibility.",
        ),
        DeclareLaunchArgument(
            "feedback_topic", default_value="feedback/joint_states",
            description="Unused in simulation; kept for interface compatibility.",
        ),
        DeclareLaunchArgument(
            "control_topic", default_value="control/joint_states",
            description="Unused in simulation; kept for interface compatibility.",
        ),
        DeclareBooleanLaunchArg("use_rviz", default_value=True),
        DeclareLaunchArgument(
            "gazebo_gui", default_value="true",
            choices=["true", "false"],
            description="Start Gazebo with the GUI.",
        ),
        DeclareLaunchArgument(
            "world_file",
            default_value=[
                FindPackageShare("agx_arm_description"), "/worlds/agx_arm_world.sdf"
            ],
            description="Gazebo world file.",
        ),
        OpaqueFunction(function=_launch_setup),
    ])
