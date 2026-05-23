# 嗨递老 / AgileX NERO Robot Arm Workspace

This workspace is based on the ROS 2 branch of:

https://github.com/agilexrobotics/agx_arm_ros

The current prototype uses the AgileX NERO arm and the ROS 2 driver from
`agx_arm_ros` to perform physical service gestures for the **嗨递老** hotpot
restaurant concept. The real arm communicates through CAN. This computer keeps
the ROS workspace and product documents, but it currently does not have the CAN
hardware communication path available. Real-arm bringup must be done on a
machine with the USB-CAN adapter connected and configured.

Current local workspace:

```bash
/home/yuuki/Documents/ROBOT/agx_arm_ws
```

## Product Vision

**嗨递老** is a hotpot service concept inspired by Haidilao's well-known culture
of extreme service: birthday celebration, attentive care, and a little joyful
awkwardness at the table. The project adds a hard-tech layer to that service
culture: a robot arm that can physically lower, extend, and present the ordering
screen, birthday interaction, and human care to elderly users and visually
impaired users.

The core industry insight is simple: many restaurant robots already have screens,
but the screen is often too far from the actual guest. For older diners,
wheelchair users, and low-vision users, that distance becomes a real barrier.
The arm is not decoration here. It is a physical extension of service: it brings
the big-font menu and the celebration closer to the person who needs it.

The product document is:

```text
嗨递老_产品技术文档.md
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
- Target arm model: AgileX `NERO`.
- End effector target: AgileX gripper, configured with `effector_type:=agx_gripper`.
- Real hardware communication: CAN, tested in the friend's hardware environment.
- Current computer status: software workspace is available, but real robot
  feedback and motion require USB-CAN hardware.

## MVP Scope

The MVP intentionally avoids fragile features and focuses on a controlled,
demonstrable service loop.

### Screen

- Provides a basic menu for normal users.
- Provides a large-font elderly-friendly menu.
- Menu switching is controlled by the upper computer.
- The screen focuses on showing dishes clearly; voice interaction is not included
  in the MVP.

### NERO Arm

- Uses `agx_arm_ros` with `arm_type:=nero`.
- Uses CAN communication in the hardware environment.
- Moves to a fixed, comfortable 3D reference pose for presenting the screen.
- Executes a birthday rhythm motion synchronized with Haidilao-style birthday
  music.
- Motion should favor stability and safety over visual exaggeration.

### Upper Computer

- Provides start/stop control for arm actions.
- Triggers menu switching on the screen.
- Coordinates the birthday mode: start music, switch celebration screen, start
  arm rhythm motion, and stop/reset after the sequence.

### Voice Module

- Not included in the MVP.
- Voice interaction is reserved for the advanced plan.

## Advanced Plan

The advanced plan adds perception and richer interaction after the MVP is stable.

### Screen

- Adds better visual filters and design language for dish photos.
- Keeps the menu focused on dishes instead of crowding the screen with controls.
- Supports voice-triggered switching between normal menu and large-font menu.

### NERO Arm

- Adds camera-based human-position detection.
- Adjusts the arm posture based on the detected user position, so elderly users
  can see the screen without leaning forward or asking for help.
- Keeps preset safe motion envelopes instead of fully free-form arm movement.

### Voice Module

- Adds voice commands for menu switching and simple mode selection.
- Example commands:
  - switch to elderly menu
  - return to normal menu
  - start birthday mode
  - stop arm motion

### Perception

- Uses a camera to estimate user position or upper-body location.
- The output should map to a small set of safe arm presets, not arbitrary IK
  targets, to reduce hardware risk during demos.

## System Architecture

```text
Upper Computer
  ├── ROS 2 environment
  ├── agx_arm_ros driver
  ├── arm action start/stop control
  ├── screen menu switching control
  └── birthday sequence coordinator

Hardware Layer
  ├── AgileX NERO arm
  ├── AgileX gripper
  ├── CAN adapter and can0 interface
  └── ordering display screen

MVP Interaction Layer
  ├── basic menu
  ├── elderly large-font menu
  └── birthday celebration screen

Advanced Interaction Layer
  ├── camera-based user position detection
  ├── voice menu switching
  └── visually optimized dish presentation
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

MoveIt2 configuration package. For real hardware, RViz should follow the actual
feedback from `/feedback/joint_states`.

```bash
follow:=true
```

### `agx_arm_description`

URDF, xacro, and mesh files for the supported AgileX arm variants. The current
prototype targets:

```text
nero
```

### `agx_arm_msgs`

Custom ROS messages for arm, gripper, and hand status/control.

## Hardware Notes

The real arm communicates through CAN. In the hardware environment, the expected
CAN interface name is:

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

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

Verify packages:

```bash
ros2 pkg list | grep agx_arm
```

Expected packages:

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

### 3. Launch Real NERO Arm + AgileX Gripper + MoveIt

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash

ros2 launch agx_arm_ctrl start_single_agx_arm_moveit.launch.py \
  can_port:=can0 \
  arm_type:=nero \
  effector_type:=agx_gripper \
  tcp_offset:='[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]' \
  speed_percent:=20
```

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

6. The NERO arm can move to the fixed comfortable screen-presentation pose.

7. Birthday rhythm motion can start, run, and stop from the upper computer.

8. The screen can switch between basic menu and elderly large-font menu.

## Safety Rules

- Always confirm the arm has free space before executing a trajectory.
- Use low speed for first bringup or after changing parameters.
- Do not command positions outside the configured joint limits.
- Keep access to emergency stop during real hardware testing.
- For the MVP, prefer fixed safe poses and pre-recorded rhythm trajectories over
  free-form motion.
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
