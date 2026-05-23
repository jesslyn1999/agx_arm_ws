import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace, SetRemap
from moveit_configs_utils.launch_utils import DeclareBooleanLaunchArg

from _moveit_config_builder import (
    ALL_ARM_TYPES,
    ALL_EFFECTOR_TYPES,
    ALL_REVO2_TYPES,
    build_moveit_config,
    build_ros2_controllers_file,
)


def _build_namespaced_moveit_rviz_config(package_path, namespace):
    """Generate a temporary RViz config with namespace-specific MoveGroup target."""
    base_rviz = package_path / "config/moveit.rviz"
    content = base_rviz.read_text(encoding="utf-8")

    ns = namespace.strip("/")
    move_group_ns = f"/{ns}" if ns else ""

    content = content.replace(
        'Move Group Namespace: ""',
        f'Move Group Namespace: "{move_group_ns}"',
    )

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".rviz", prefix="moveit_", delete=False
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


def _build_moveit(context):
    namespace = LaunchConfiguration("namespace").perform(context)
    arm_type = LaunchConfiguration("arm_type").perform(context)
    effector_type = LaunchConfiguration("effector_type").perform(context)
    revo2_type = LaunchConfiguration("revo2_type").perform(context)
    control_topic = LaunchConfiguration("control_topic").perform(context)
    moveit_config = build_moveit_config(context)
    package_path = moveit_config.package_path

    actions = []

    virtual_joints_launch = package_path / "launch/static_virtual_joint_tfs.launch.py"
    if virtual_joints_launch.exists():
        actions.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(str(virtual_joints_launch))
            )
        )

    actions.append(
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(package_path / "launch/rsp.launch.py")
            )
        )
    )

    actions.append(
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(package_path / "launch/move_group.launch.py")
            )
        )
    )

    actions.append(
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(package_path / "launch/moveit_rviz.launch.py")
            ),
            launch_arguments={
                "rviz_config": _build_namespaced_moveit_rviz_config(package_path, namespace),
            }.items(),
            condition=IfCondition(LaunchConfiguration("use_rviz")),
        )
    )

    actions.append(
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(package_path / "launch/warehouse_db.launch.py")
            ),
            condition=IfCondition(LaunchConfiguration("db")),
        )
    )

    ros2_controllers_yaml = build_ros2_controllers_file(
        arm_type, effector_type, revo2_type, namespace
    )
    actions.append(
        Node(
            package="controller_manager",
            executable="ros2_control_node",
            parameters=[
                moveit_config.robot_description,
                ros2_controllers_yaml,
            ],
            remappings=[("joint_states", str(control_topic))],
        )
    )

    actions.append(
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(package_path / "launch/spawn_controllers.launch.py")
            )
        )
    )

    return [
        GroupAction(
            actions=[
                PushRosNamespace(namespace),
                SetRemap(src="/robot_description", dst="robot_description"),
                *actions,
            ]
        )
    ]


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "namespace",
                default_value="",
                description="ROS namespace for this arm instance (e.g. arm1).",
            ),
            DeclareLaunchArgument(
                "arm_type",
                default_value="piper",
                choices=ALL_ARM_TYPES,
                description="Arm type.",
            ),
            DeclareLaunchArgument(
                "effector_type",
                default_value="none",
                choices=ALL_EFFECTOR_TYPES,
                description="Effector type.",
            ),
            DeclareLaunchArgument(
                "revo2_type",
                default_value="left",
                choices=ALL_REVO2_TYPES,
                description="Revo2 side (used when effector_type is revo2).",
            ),
            DeclareLaunchArgument(
                "tcp_offset",
                default_value="[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]",
                description="TCP offset [x, y, z, rx, ry, rz] in meters/radians.",
            ),
            DeclareLaunchArgument(
                "follow",
                default_value="false",
                choices=["true", "false"],
                description="Follow real arm state. "
                "true: move_group subscribes to feedback_topic; "
                "false: subscribes to control_topic (mock hardware).",
            ),
            DeclareBooleanLaunchArg(
                "db",
                default_value=False,
                description="By default, we do not start a database (it can be large)",
            ),
            DeclareBooleanLaunchArg(
                "debug",
                default_value=False,
                description="By default, we are not in debug mode",
            ),
            DeclareLaunchArgument(
                "control_topic",
                default_value="control/joint_states",
                description="Topic the ros2_control_node remaps joint_states to.",
            ),
            DeclareBooleanLaunchArg("use_rviz", default_value=True),
            OpaqueFunction(function=_build_moveit),
        ]
    )
