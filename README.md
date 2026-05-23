# Acemate / AgileX Robot Arm Workspace

This workspace is based on the ROS 2 branch of:

https://github.com/agilexrobotics/agx_arm_ros

It records the current ROS 2 integration for the AgileX arm and the product
prototype built around it. The real hardware setup uses CAN communication and a
real AgileX gripper, but this computer currently does not have the CAN hardware
communication path available. Real-arm bringup must be done on a machine with
the USB-CAN adapter connected and configured.

The current local workspace is:

```bash
/home/yuuki/Documents/ROBOT/agx_arm_ws
```

## Current Status

- ROS distribution: Jazzy.
- Source repository: `src/agx_arm_ros`.
- Source branch: `ros2`.
- Main upstream remote: `https://github.com/agilexrobotics/agx_arm_ros.git`.
- Workspace has already been built into `install/`.
- ROS packages available after sourcing the workspace:
  - `agx_arm_ctrl`
  - `agx_arm_description`
  - `agx_arm_moveit`
  - `agx_arm_msgs`
- Real hardware setup target: AgileX arm with AgileX gripper.
- Real hardware communication: CAN, tested in the friend's hardware environment.
- Current computer status: ROS workspace is available, but CAN communication is
  not available until a USB-CAN adapter is connected and configured.

## Product Feature Summary

The product document is:

```text
食光破壁者_产品技术文档.md
```

Product name:

```text
食光破壁者（Barrier Breaker）
```

Positioning:

- Track: accessibility technology / embodied AI application.
- Target users: elderly people over 65, wheelchair users, and visually impaired
  users.
- Core idea: instead of forcing users to adapt to restaurant digital systems,
  the robot actively adapts the ordering interface to the user.

Main functions:

1. Active approach and adaptive screen height

   A staff member or user calls the robot from an H5 interface. The mobile base
   approaches the table, the camera estimates the user's upper-body or
   wheelchair height, and the arm moves the screen down to a comfortable viewing
   height. The screen can tilt forward to improve readability.

2. Multimodal accessible ordering

   After the arm reaches the target position, the screen enters an elderly-user
   mode with large text, high contrast, and simplified layout. The user can order
   through voice or touch. The H5 page uses speech input and text-to-speech for
   accessible interaction.

3. Health-restriction based menu recommendation

   The user can say restrictions such as "I have high blood pressure" or "I
   cannot eat sweet food". The backend calls an LLM API, such as DeepSeek or GPT,
   to extract dietary restrictions, then hides or disables unsuitable dishes and
   highlights safer recommendations. This is the product's key differentiated
   feature.

4. Birthday mode

   A birthday mode can play local birthday music, show a celebration animation,
   trigger a pre-recorded arm dance trajectory, rotate the camera toward the
   customer, take a countdown photo, and show a QR code for downloading the
   photo.

5. H5 and robot collaboration

   The H5 page handles voice input, TTS, local audio, large-font menu UI, and
   remote operation controls. The central service uses Python Flask and
   WebSocket to exchange JSON commands with the robot-control side.

6. Hardware and software architecture

   The prototype combines an AgileX mobile base, a 6-DOF arm, an electronic
   screen, an RDK X5 or laptop-side central service, a USB camera, YOLOv8-based
   user detection, LLM-based menu filtering, and a preset arm trajectory library.

Important JSON command examples from the product design:

```json
{"action": "approach_and_lower", "target_height": 0.6, "tilt_angle": 15}
{"action": "filter_menu", "restrictions": ["高盐", "高糖"], "user_input": "我有高血压"}
{"action": "start_birthday_dance", "song": "happy_birthday.mp3"}
{"action": "capture_photo", "countdown": 3}
```

## Package Overview

### `agx_arm_ctrl`

Runtime control package for the real robot. It connects to the arm through CAN
using `pyAgxArm`.

Important launch file:

```bash
ros2 launch agx_arm_ctrl start_single_agx_arm_moveit.launch.py
```

Main feedback topics:

- `/feedback/joint_states`
- `/feedback/tcp_pose`
- `/feedback/arm_status`
- `/feedback/gripper_status`

Main control topics:

- `/control/joint_states`
- `/control/move_j`
- `/control/move_p`
- `/control/move_l`
- `/control/move_c`

Useful services:

- `/enable_agx_arm`
- `/move_home`
- `/emergency_stop`

### `agx_arm_moveit`

MoveIt2 configuration package. It supports real hardware operation with RViz
following the actual feedback from `/feedback/joint_states`.

For real hardware, use:

```bash
follow:=true
```

The combined launch file below already uses this default.

### `agx_arm_description`

URDF, xacro, and mesh files for the supported AgileX arm variants.

Supported arm types include:

- `piper`
- `piper_h`
- `piper_l`
- `piper_x`
- `nero`

### `agx_arm_msgs`

Custom ROS messages for arm, gripper, and hand status/control.

## Hardware Notes

The real arm communicates through a CAN interface. The expected interface name
in the hardware environment is:

```bash
can0
```

The AgileX gripper must be launched with:

```bash
effector_type:=agx_gripper
```

If the CAN device is not visible, the real robot launch will fail before the arm
can provide feedback. On the current computer, this is expected unless a USB-CAN
adapter is connected and activated.

## Bringup Procedure

These steps are for the hardware computer with CAN communication available. On
the current computer, use the sourcing and package checks for software
verification, but do not expect real robot feedback without CAN.

### 1. Source ROS and the workspace

Open a terminal:

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

Verify that the packages are visible:

```bash
ros2 pkg list | grep agx_arm
```

Expected packages include:

```text
agx_arm_ctrl
agx_arm_description
agx_arm_moveit
agx_arm_msgs
```

### 2. Activate CAN

Plug in the USB-CAN adapter, then run:

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws/src/agx_arm_ros/scripts
bash can_activate.sh can0 1000000
```

Check that `can0` exists:

```bash
ip -br link show type can
ip -details link show can0
```

If multiple CAN adapters are connected, the script may ask for a USB hardware
address. In that case, follow the address printed by the script and rerun it in
the form:

```bash
bash can_activate.sh can0 1000000 <usb-bus-address>
```

### 3. Launch Real Arm + AgileX Gripper + MoveIt

Use this as the normal bringup command for the current hardware:

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash

ros2 launch agx_arm_ctrl start_single_agx_arm_moveit.launch.py \
  can_port:=can0 \
  arm_type:=piper \
  effector_type:=agx_gripper \
  tcp_offset:='[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]' \
  speed_percent:=20
```

If the physical arm model is not `piper`, replace `arm_type:=piper` with the
correct model, such as `piper_x` or `nero`.

Start with a low `speed_percent` for real hardware testing. Increase it only
after feedback and motion behavior are confirmed.

## Validation Criteria

The system is considered brought up correctly when all of the following pass:

1. CAN interface exists and is up:

   ```bash
   ip -br link show type can
   ```

2. The launch terminal prints firmware and feedback readiness logs, such as:

   ```text
   firmware version: ...
   Agx_arm feedback is ready, control is now enabled
   ```

3. Joint feedback is available:

   ```bash
   ros2 topic echo /feedback/joint_states --once
   ```

4. Arm status is available:

   ```bash
   ros2 topic echo /feedback/arm_status --once
   ```

5. Gripper status is available:

   ```bash
   ros2 topic echo /feedback/gripper_status --once
   ```

6. RViz model follows the real robot state.

7. A small MoveIt `Plan & Execute` test moves the real arm smoothly and stops at
   the expected target.

## Safety Rules

- Always confirm the arm has free space before executing a trajectory.
- Use low speed for first bringup or after changing parameters.
- Do not command positions outside the configured joint limits.
- Keep access to emergency stop during real hardware testing.
- If feedback is missing or unstable, stop and fix CAN/driver status before
  sending motion commands.

## Rebuild

After changing source files:

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

If Python/ROS environment issues appear, make sure Conda is not active before
building.

