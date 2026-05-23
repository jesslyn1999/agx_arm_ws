#!/usr/bin/env python3
"""MCP server exposing AGX arm control tools for Codex CLI.

Starts a lightweight ROS 2 node (joint states, JTC action clients, TF2)
and serves robot-control tools over STDIO using the MCP protocol.
Codex CLI starts this automatically via .codex/config.toml.

Supports any AGX arm type (NERO 7-DOF, Piper 6-DOF, etc.) — joint names
and limits are discovered dynamically from the URDF on /robot_description.
"""

import os
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from std_msgs.msg import String
from sensor_msgs.msg import JointState
from rcl_interfaces.msg import Log
from control_msgs.action import FollowJointTrajectory
from trajectory_msgs.msg import JointTrajectoryPoint
from builtin_interfaces.msg import Duration as DurationMsg
from tf2_ros import Buffer, TransformListener

from fastmcp import FastMCP


# ── MCP server instance ───────────────────────────────────────────────────────

mcp = FastMCP("agx_arm")

_node: "AgxArmMCPNode | None" = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_duration(sec: float) -> DurationMsg:
    return DurationMsg(sec=int(sec), nanosec=int((sec % 1) * 1e9))


def _quat_to_rpy(x: float, y: float, z: float, w: float
                 ) -> tuple[float, float, float]:
    """Quaternion to (roll, pitch, yaw) in radians."""
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = np.arctan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    pitch = np.arcsin(np.clip(sinp, -1.0, 1.0))

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = np.arctan2(siny_cosp, cosy_cosp)

    return float(roll), float(pitch), float(yaw)


# ── ROS 2 Node ─────────────────────────────────────────────────────────────────

class AgxArmMCPNode(Node):
    """Minimal ROS 2 node for MCP: joint states, JTC, TF2, URDF."""

    def __init__(self):
        super().__init__("agx_arm_mcp_server")
        self._cb_group = ReentrantCallbackGroup()

        # ── Joint-state cache ──
        self._positions: dict[str, float] = {}
        self._velocities: dict[str, float] = {}
        self._efforts: dict[str, float] = {}
        self.create_subscription(
            JointState, "/joint_states", self._joint_state_cb, 10,
            callback_group=self._cb_group)

        # ── URDF from robot_state_publisher ──
        self._urdf_string: str | None = None
        self._urdf_model = None
        self._arm_joint_names: list[str] = []
        self._arm_joint_limits: dict[str, tuple[float, float]] = {}
        self._gripper_joint_names: list[str] = []
        self._gripper_joint_limits: dict[str, tuple[float, float]] = {}

        desc_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE)
        self.create_subscription(
            String, "/robot_description", self._robot_description_cb,
            desc_qos, callback_group=self._cb_group)

        # ── TF2 for end-effector FK ──
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        # ── /rosout log buffer ──
        self._log_buffer: deque[str] = deque(maxlen=200)
        self.create_subscription(
            Log, "/rosout", self._rosout_cb, 50,
            callback_group=self._cb_group)

        # ── JTC action clients ──
        self._arm_ac = ActionClient(
            self, FollowJointTrajectory,
            "/arm_controller/follow_joint_trajectory",
            callback_group=self._cb_group)
        self._gripper_ac = ActionClient(
            self, FollowJointTrajectory,
            "/gripper_controller/follow_joint_trajectory",
            callback_group=self._cb_group)

        self.get_logger().info("AGX Arm MCP node ready")

    # ── Callbacks ──────────────────────────────────────────────────────────

    def _joint_state_cb(self, msg: JointState):
        for name, pos, vel, eff in zip(
                msg.name, msg.position, msg.velocity, msg.effort):
            self._positions[name] = pos
            self._velocities[name] = vel
            self._efforts[name] = eff

    def _robot_description_cb(self, msg: String):
        if self._urdf_string is not None:
            return
        self._urdf_string = msg.data
        try:
            from urdf_parser_py.urdf import Robot as UrdfRobot
            self._urdf_model = UrdfRobot.from_xml_string(self._urdf_string)
            self._discover_joints()
            self.get_logger().info(
                f"URDF loaded: {self._urdf_model.name}, "
                f"arm joints: {self._arm_joint_names}, "
                f"gripper joints: {self._gripper_joint_names}")
        except Exception as e:
            self.get_logger().error(f"Failed to parse URDF: {e}")

    _LOG_LEVEL_NAMES = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "FATAL"}

    def _rosout_cb(self, msg: Log):
        if msg.name == self.get_name():
            return
        level = self._LOG_LEVEL_NAMES.get(msg.level, str(msg.level))
        self._log_buffer.append(f"[{level}] [{msg.name}] {msg.msg}")

    # ── Joint discovery from URDF ──────────────────────────────────────────

    def _discover_joints(self):
        """Walk the URDF kinematic tree to find arm and gripper joints."""
        model = self._urdf_model
        if model is None:
            return

        # Walk chain from base_link → tcp_link for arm joints
        chain = self._find_chain("base_link", "tcp_link")
        for jname, _, _ in chain:
            joint = model.joint_map[jname]
            if joint.type in ("revolute", "continuous"):
                self._arm_joint_names.append(jname)
                if joint.limit:
                    self._arm_joint_limits[jname] = (
                        joint.limit.lower, joint.limit.upper)

        # Find gripper joints (prismatic joints with "gripper" in name)
        for joint in model.joints:
            if joint.type == "prismatic" and "gripper" in joint.name:
                self._gripper_joint_names.append(joint.name)
                if joint.limit:
                    self._gripper_joint_limits[joint.name] = (
                        joint.limit.lower, joint.limit.upper)
        self._gripper_joint_names.sort()

    # ── Kinematic helpers ──────────────────────────────────────────────────

    def _find_chain(self, start_link: str, end_link: str
                    ) -> list[tuple[str, str, str]]:
        """Trace joint chain between two links. Returns [(jname, parent, child), ...]."""
        if self._urdf_model is None:
            return []
        parent_map: dict[str, tuple[str, str]] = {}
        for j in self._urdf_model.joints:
            parent_map[j.child] = (j.name, j.parent)
        chain: list[tuple[str, str, str]] = []
        current = end_link
        while current != start_link:
            if current not in parent_map:
                return []
            jname, plink = parent_map[current]
            chain.append((jname, plink, current))
            current = plink
        chain.reverse()
        return chain

    def build_kinematic_summary(self) -> str:
        if self._urdf_model is None:
            return "URDF not yet received from /robot_description."
        model = self._urdf_model
        chain = self._find_chain("base_link", "tcp_link")
        if not chain:
            return "Could not trace chain from base_link to tcp_link."

        lines = [f"Robot: {model.name}",
                 "Kinematic chain (base_link -> tcp_link):"]
        for jname, plink, clink in chain:
            joint = model.joint_map[jname]
            parts = [jname, joint.type]
            if joint.type == "revolute" and joint.axis is not None:
                parts.append(f"axis=[{', '.join(f'{a:g}' for a in joint.axis)}]")
            if joint.limit:
                parts.append(f"limits=[{joint.limit.lower:.3f}, {joint.limit.upper:.3f}]")
            origin = ""
            if joint.origin and joint.origin.xyz:
                x, y, z = joint.origin.xyz
                origin = f" ({x:.3f}, {y:.3f}, {z:.3f})m"
            lines.append(f"  {plink} --[{', '.join(parts)}]--> {clink}{origin}")

        if self._gripper_joint_names:
            lines.append("\nGripper joints:")
            for jn in self._gripper_joint_names:
                lo, hi = self._gripper_joint_limits.get(jn, (0, 0))
                lines.append(f"  {jn}: prismatic [{lo:.4f}, {hi:.4f}] m")
        return "\n".join(lines)

    # ── Trajectory execution ───────────────────────────────────────────────

    def send_arm_trajectory(self, positions: list[float],
                            duration_sec: float = 3.0,
                            timeout: float = 15.0) -> tuple[bool, str]:
        return self._send_trajectory(
            self._arm_ac, self._arm_joint_names, positions,
            duration_sec, timeout)

    def send_gripper_trajectory(self, positions: list[float],
                                duration_sec: float = 1.5,
                                timeout: float = 10.0) -> tuple[bool, str]:
        return self._send_trajectory(
            self._gripper_ac, self._gripper_joint_names, positions,
            duration_sec, timeout)

    def _send_trajectory(self, ac: ActionClient, joint_names: list[str],
                         positions: list[float], duration_sec: float,
                         timeout: float) -> tuple[bool, str]:
        try:
            if not ac.wait_for_server(timeout_sec=5.0):
                return False, "Action server not available — is the controller active?"
        except Exception as e:
            return False, f"Error waiting for action server: {e}"

        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = list(joint_names)
        pt = JointTrajectoryPoint()
        pt.positions = [float(p) for p in positions]
        pt.time_from_start = _make_duration(duration_sec)
        goal.trajectory.points = [pt]

        event = threading.Event()
        result_box: list = [None]

        def _on_goal_response(future):
            try:
                handle = future.result()
                if not handle.accepted:
                    result_box[0] = "rejected"
                    event.set()
                    return
                handle.get_result_async().add_done_callback(_on_result)
            except Exception as e:
                result_box[0] = f"goal_error:{e}"
                event.set()

        def _on_result(future):
            try:
                result_box[0] = future.result()
            except Exception as e:
                result_box[0] = f"result_error:{e}"
            event.set()

        try:
            ac.send_goal_async(goal).add_done_callback(_on_goal_response)
        except Exception as e:
            return False, f"Error sending trajectory goal: {e}"

        pos_str = ", ".join(
            f"{joint_names[i]}={positions[i]:+.4f}"
            for i in range(len(joint_names)))
        self.get_logger().info(f"Trajectory sent: {pos_str}")

        if not event.wait(timeout=duration_sec + timeout):
            return False, "Timeout — motion did not complete in time."
        if result_box[0] == "rejected":
            return False, "Goal rejected by controller."
        if isinstance(result_box[0], str) and result_box[0].startswith(
                ("goal_error:", "result_error:")):
            return False, f"Trajectory failed: {result_box[0]}"
        return True, (
            f"Motion completed ({duration_sec:.1f}s trajectory). "
            f"Targets: {pos_str}")


# ── MCP tool definitions ──────────────────────────────────────────────────────

# ── Robot state tools ──

@mcp.tool()
def get_joint_states() -> str:
    """Get current positions, velocities, and efforts of all joints (arm + gripper)."""
    try:
        if not _node or not _node._positions:
            return "No joint-state data available yet — is the robot launched?"
        all_joints = _node._arm_joint_names + _node._gripper_joint_names
        if not all_joints:
            all_joints = list(_node._positions.keys())
        lines = []
        for jn in all_joints:
            p = _node._positions.get(jn, float("nan"))
            v = _node._velocities.get(jn, float("nan"))
            e = _node._efforts.get(jn, float("nan"))
            unit = "m" if jn in _node._gripper_joint_names else "rad"
            lines.append(
                f"  {jn}: pos={p:+.4f} {unit}"
                + (f" ({np.degrees(p):+.1f}°)" if unit == "rad" else "")
                + f"  vel={v:+.4f}  effort={e:+.3f}")
        return "Current joint states:\n" + "\n".join(lines)
    except Exception as e:
        return f"Error reading joint states: {e}"


@mcp.tool()
def get_end_effector_pose() -> str:
    """Get current end-effector (tcp_link) pose in the base_link frame.

    Returns Cartesian position (x, y, z) in meters and orientation
    as quaternion + roll/pitch/yaw.
    """
    try:
        if not _node:
            return "ROS 2 node not ready."
        t = _node._tf_buffer.lookup_transform(
            "base_link", "tcp_link", rclpy.time.Time())
        pos = t.transform.translation
        rot = t.transform.rotation
        roll, pitch, yaw = _quat_to_rpy(rot.x, rot.y, rot.z, rot.w)
        return (
            f"End-effector pose (tcp_link in base_link frame):\n"
            f"  Position: x={pos.x:.4f}, y={pos.y:.4f}, z={pos.z:.4f} m\n"
            f"  Orientation (quat): x={rot.x:.4f}, y={rot.y:.4f}, "
            f"z={rot.z:.4f}, w={rot.w:.4f}\n"
            f"  Orientation (RPY): roll={np.degrees(roll):.1f}, "
            f"pitch={np.degrees(pitch):.1f}, yaw={np.degrees(yaw):.1f} deg")
    except Exception as e:
        return f"Error reading end-effector pose: {e}"


@mcp.tool()
def get_robot_description() -> str:
    """Get the robot's kinematic chain from the URDF (links, joints, limits, origins)."""
    try:
        if not _node:
            return "ROS 2 node not ready."
        return _node.build_kinematic_summary()
    except Exception as e:
        return f"Error reading robot description: {e}"


# ── Arm motion tools ──

@mcp.tool()
def move_joint(joint_name: str, position_rad: float,
               duration_sec: float = 3.0) -> str:
    """Move a single arm joint to target position (radians). Other joints hold.

    joint_name: e.g. joint1, joint2, ... joint7
    position_rad: target angle in radians (must be within joint limits)
    duration_sec: trajectory duration (>= 2.0 recommended for safety)
    """
    try:
        if not _node:
            return "ROS 2 node not ready."
        if not _node._arm_joint_names:
            return "Arm joints not yet discovered — URDF not received."
        if joint_name not in _node._arm_joint_names:
            return (f"Unknown joint '{joint_name}'. "
                    f"Valid arm joints: {_node._arm_joint_names}")
        lo, hi = _node._arm_joint_limits.get(joint_name, (-999, 999))
        if not (lo <= position_rad <= hi):
            return (f"Target {position_rad:.3f} rad is outside limits "
                    f"[{lo:.3f}, {hi:.3f}] for {joint_name}. Refusing to move.")
        if not _node._positions:
            return "No joint-state data — cannot determine current positions."

        target = [_node._positions.get(jn, 0.0) for jn in _node._arm_joint_names]
        idx = _node._arm_joint_names.index(joint_name)
        target[idx] = position_rad

        ok, msg = _node.send_arm_trajectory(target, duration_sec)
        return msg
    except Exception as e:
        return f"Error in move_joint: {e}"


@mcp.tool()
def move_all_joints(positions_rad: list[float],
                    duration_sec: float = 3.0) -> str:
    """Move all arm joints to specified positions (radians).

    positions_rad: list of target angles, one per arm joint in order.
        For NERO (7-DOF): [joint1, joint2, joint3, joint4, joint5, joint6, joint7]
        For Piper (6-DOF): [joint1, joint2, joint3, joint4, joint5, joint6]
    duration_sec: trajectory duration (>= 2.0 recommended for safety)
    """
    try:
        if not _node:
            return "ROS 2 node not ready."
        if not _node._arm_joint_names:
            return "Arm joints not yet discovered — URDF not received."
        if len(positions_rad) != len(_node._arm_joint_names):
            return (f"Expected {len(_node._arm_joint_names)} positions "
                    f"for joints {_node._arm_joint_names}, "
                    f"got {len(positions_rad)}.")
        for jn, pos in zip(_node._arm_joint_names, positions_rad):
            lo, hi = _node._arm_joint_limits.get(jn, (-999, 999))
            if not (lo <= pos <= hi):
                return (f"{jn} target {pos:.3f} rad is outside "
                        f"[{lo:.3f}, {hi:.3f}]. Refusing to move.")
        ok, msg = _node.send_arm_trajectory(positions_rad, duration_sec)
        return msg
    except Exception as e:
        return f"Error in move_all_joints: {e}"


# ── Gripper tools ──

@mcp.tool()
def open_gripper(duration_sec: float = 1.5) -> str:
    """Open the gripper fully (maximum width ~0.1m)."""
    try:
        if not _node:
            return "ROS 2 node not ready."
        if not _node._gripper_joint_names:
            return "No gripper joints discovered — gripper may not be configured."
        # gripper_joint1: 0→0.05, gripper_joint2: 0→-0.05
        targets = []
        for jn in _node._gripper_joint_names:
            _, hi = _node._gripper_joint_limits.get(jn, (0, 0.05))
            targets.append(hi)
        ok, msg = _node.send_gripper_trajectory(targets, duration_sec)
        return msg
    except Exception as e:
        return f"Error in open_gripper: {e}"


@mcp.tool()
def close_gripper(duration_sec: float = 1.5) -> str:
    """Close the gripper fully."""
    try:
        if not _node:
            return "ROS 2 node not ready."
        if not _node._gripper_joint_names:
            return "No gripper joints discovered — gripper may not be configured."
        # gripper_joint1: →0, gripper_joint2: →0
        targets = [0.0] * len(_node._gripper_joint_names)
        ok, msg = _node.send_gripper_trajectory(targets, duration_sec)
        return msg
    except Exception as e:
        return f"Error in close_gripper: {e}"


@mcp.tool()
def move_gripper(width_meters: float, duration_sec: float = 1.5) -> str:
    """Move the gripper to a specific opening width.

    width_meters: total opening width in meters (0.0 = closed, 0.1 = fully open)
    duration_sec: trajectory duration
    """
    try:
        if not _node:
            return "ROS 2 node not ready."
        if not _node._gripper_joint_names:
            return "No gripper joints discovered — gripper may not be configured."
        if not (0.0 <= width_meters <= 0.1):
            return f"Width {width_meters:.3f}m is outside [0.0, 0.1]. Refusing."
        # Each finger moves half the width; joint2 is mirrored (negative)
        half = width_meters / 2.0
        targets = []
        for jn in _node._gripper_joint_names:
            lo, hi = _node._gripper_joint_limits.get(jn, (0, 0.05))
            if hi > 0:
                targets.append(half)
            else:
                targets.append(-half)
        ok, msg = _node.send_gripper_trajectory(targets, duration_sec)
        return msg
    except Exception as e:
        return f"Error in move_gripper: {e}"


# ── ROS 2 introspection tools ──

@mcp.tool()
def get_ros2_info() -> str:
    """Get ROS 2 environment info: distribution name and domain ID."""
    distro = os.getenv("ROS_DISTRO", "(unknown)")
    domain_id = os.getenv("ROS_DOMAIN_ID", "0")
    return f"ROS 2 distribution: {distro}\nROS_DOMAIN_ID: {domain_id}"


@mcp.tool()
def list_topics() -> str:
    """List all available ROS 2 topics."""
    try:
        r = subprocess.run(
            ["ros2", "topic", "list"],
            capture_output=True, text=True, check=True, timeout=10)
        return f"ROS 2 topics:\n{r.stdout}"
    except Exception as e:
        return f"Error listing topics: {e}"


@mcp.tool()
def list_nodes() -> str:
    """List all running ROS 2 nodes."""
    try:
        r = subprocess.run(
            ["ros2", "node", "list"],
            capture_output=True, text=True, check=True, timeout=10)
        return f"ROS 2 nodes:\n{r.stdout}"
    except Exception as e:
        return f"Error listing nodes: {e}"


@mcp.tool()
def list_services() -> str:
    """List all available ROS 2 services."""
    try:
        r = subprocess.run(
            ["ros2", "service", "list"],
            capture_output=True, text=True, check=True, timeout=10)
        return f"ROS 2 services:\n{r.stdout}"
    except Exception as e:
        return f"Error listing services: {e}"


@mcp.tool()
def list_actions() -> str:
    """List all available ROS 2 actions."""
    try:
        r = subprocess.run(
            ["ros2", "action", "list"],
            capture_output=True, text=True, check=True, timeout=10)
        return f"ROS 2 actions:\n{r.stdout}"
    except Exception as e:
        return f"Error listing actions: {e}"


# ── Diagnostics ──

@mcp.tool()
def get_recent_logs(count: int = 30) -> str:
    """Get recent ROS 2 log messages from all running nodes.

    Reads from /rosout. count: number of recent messages (default 30, max 200).
    """
    try:
        if not _node:
            return "ROS 2 node not ready."
        count = min(count, 200)
        logs = list(_node._log_buffer)
        if not logs:
            return "No log messages captured yet."
        recent = logs[-count:]
        return f"Recent ROS 2 logs ({len(recent)} messages):\n" + "\n".join(recent)
    except Exception as e:
        return f"Error reading logs: {e}"


# ── Entry point ────────────────────────────────────────────────────────────────

def _safe_spin(executor):
    try:
        executor.spin()
    except Exception:
        pass


def main():
    global _node

    # ── Protect the MCP STDIO channel ──
    # Redirect C-level fd 1 (stdout) to stderr so rclpy / DDS noise
    # cannot corrupt the JSON-RPC protocol on stdout.
    _real_stdout_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(_real_stdout_fd, "w")

    # ── Disable rclpy's signal handlers (conflict with FastMCP asyncio) ──
    from rclpy.signals import SignalHandlerOptions
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)

    _node = AgxArmMCPNode()

    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(_node)
    spin_thread = threading.Thread(target=_safe_spin, args=(executor,),
                                   daemon=True)
    spin_thread.start()

    mcp.run()


if __name__ == "__main__":
    main()
