import ast
import tempfile

import yaml
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from moveit_configs_utils import MoveItConfigsBuilder

ALL_ARM_TYPES = ["piper", "piper_x", "piper_l", "piper_h", "nero"]
ALL_EFFECTOR_TYPES = ["none", "agx_gripper", "revo2"]
ALL_REVO2_TYPES = ["left", "right"]


def declare_common_args():
    return [
        DeclareLaunchArgument(
            "namespace",
            default_value="",
            description="ROS namespace for this arm instance (e.g. arm1).",
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
        DeclareLaunchArgument(
            "feedback_topic",
            default_value="feedback/joint_states",
            description="Joint states feedback topic (used when follow:=true).",
        ),
        DeclareLaunchArgument(
            "control_topic",
            default_value="control/joint_states",
            description="Joint states control topic (used when follow:=false, and for ros2_control_node).",
        ),
    ]


def _select_profile(effector_type: str, revo2_type: str) -> str:
    if effector_type == "agx_gripper":
        return "gripper"
    if effector_type == "revo2":
        return f"revo2_{revo2_type}"
    return "none"


def build_moveit_config(context, *, urdf_file="config/agx_arm.urdf.xacro",
                        extra_urdf_mappings=None):
    """Build a MoveItConfigs from launch context.

    Args:
        urdf_file: URDF xacro path relative to agx_arm_moveit package share.
        extra_urdf_mappings: Additional xacro mappings merged into the URDF
            processing call (e.g. simulation_controllers for Gazebo).
    """
    arm_type = LaunchConfiguration("arm_type").perform(context)
    effector_type = LaunchConfiguration("effector_type").perform(context)
    revo2_type = LaunchConfiguration("revo2_type").perform(context)
    tcp_offset = ast.literal_eval(
        LaunchConfiguration("tcp_offset").perform(context)
    )

    profile = _select_profile(effector_type, revo2_type)
    urdf_mappings = {
        "arm_type": arm_type,
        "effector_type": effector_type,
        "revo2_type": revo2_type,
        "tcp_offset_xyz": f"{tcp_offset[0]} {tcp_offset[1]} {tcp_offset[2]}",
        "tcp_offset_rpy": f"{tcp_offset[3]} {tcp_offset[4]} {tcp_offset[5]}",
    }
    if extra_urdf_mappings:
        urdf_mappings.update(extra_urdf_mappings)

    srdf_mappings = {
        "arm_type": arm_type,
        "effector_type": effector_type,
        "revo2_type": revo2_type,
    }

    moveit_config = (
        MoveItConfigsBuilder("agx_arm", package_name="agx_arm_moveit")
        .robot_description(file_path=urdf_file, mappings=urdf_mappings)
        .robot_description_semantic(
            file_path="config/agx_arm.srdf.xacro", mappings=srdf_mappings
        )
        .robot_description_kinematics(file_path="config/kinematics.yaml")
        .joint_limits(file_path="config/joint_limits.yaml")
        .sensors_3d(file_path="config/sensors_3d.yaml")
        .trajectory_execution(file_path=f"config/moveit_controllers_{profile}.yaml")
        .to_moveit_configs()
    )

    if arm_type == "nero":
        moveit_config.trajectory_execution[
            "moveit_simple_controller_manager"
        ]["arm_controller"]["joints"] = [
            "joint1", "joint2", "joint3", "joint4",
            "joint5", "joint6", "joint7",
        ]

    return moveit_config


def build_ros2_controllers_file(arm_type, effector_type, revo2_type, namespace,
                                *, for_gazebo=False):
    """Build ros2_controllers config YAML and return the path to a temp file.

    When for_gazebo=True, adds a gz_ros_control section with sim-specific
    tuning and omits absolute namespace prefixes (Gazebo plugin resolves them
    relative to its own namespace).
    """
    arm_joints = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"]
    if arm_type == "nero":
        arm_joints.append("joint7")

    cm_controllers = {
        "arm_controller": {
            "type": "joint_trajectory_controller/JointTrajectoryController",
        },
        "joint_state_broadcaster": {
            "type": "joint_state_broadcaster/JointStateBroadcaster",
        },
    }

    ns = namespace.strip("/")
    cm_node = f"/{ns}/controller_manager" if ns else "/controller_manager"
    arm_key = f"/{ns}/arm_controller" if ns else "/arm_controller"

    config = {
        cm_node: {
            "ros__parameters": {"update_rate": 200, **cm_controllers},
        },
        arm_key: {
            "ros__parameters": {
                "joints": arm_joints,
                "command_interfaces": ["position"],
                "state_interfaces": ["position", "velocity"],
            },
        },
    }

    if effector_type == "agx_gripper":
        cm_controllers["gripper_controller"] = {
            "type": "joint_trajectory_controller/JointTrajectoryController",
        }
        config[cm_node]["ros__parameters"].update(cm_controllers)
        grip_key = f"/{ns}/gripper_controller" if ns else "/gripper_controller"
        config[grip_key] = {
            "ros__parameters": {
                "joints": ["gripper_joint1", "gripper_joint2"],
                "command_interfaces": ["position"],
                "state_interfaces": ["position", "velocity"],
            },
        }
    elif effector_type == "revo2":
        side = revo2_type
        ctrl_name = f"{side}_hand_controller"
        cm_controllers[ctrl_name] = {
            "type": "joint_trajectory_controller/JointTrajectoryController",
        }
        config[cm_node]["ros__parameters"].update(cm_controllers)
        hand_key = f"/{ns}/{ctrl_name}" if ns else f"/{ctrl_name}"
        config[hand_key] = {
            "ros__parameters": {
                "joints": [
                    f"{side}_thumb_metacarpal_joint",
                    f"{side}_thumb_proximal_joint",
                    f"{side}_index_proximal_joint",
                    f"{side}_middle_proximal_joint",
                    f"{side}_ring_proximal_joint",
                    f"{side}_pinky_proximal_joint",
                ],
                "command_interfaces": ["position"],
                "state_interfaces": ["position", "velocity"],
            },
        }

    if for_gazebo:
        gz_key = f"/{ns}/gz_ros_control" if ns else "/gz_ros_control"
        config[gz_key] = {
            "ros__parameters": {
                "hold_joints": True,
                "position_proportional_gain": 0.1,
            },
        }

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="ros2_controllers_", delete=False
    )
    yaml.dump(config, tmp, default_flow_style=False)
    tmp.close()
    return tmp.name
