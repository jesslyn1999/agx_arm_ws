# AGENTS

1. Think thoroughly before applying changes. If you are in doubt, do not stay silent, say it.
2. If you disagree with the requested change, do not make changes yet. Say so clearly and challenge the request: explain the concern, ask why the user thinks this approach is right, and wait for clarification before editing. This is one of the most important rules.
3. Simpler is better. If a solution only needs 50 lines, do not write or change 200.
4. Do not change irrelevant files. If there is no problem, do not touch other files.
5. Define clear validation criteria, then let the agent run the loop until everything passes.
6. Work like an experienced software engineer with strong ROS2 expertise. Prioritize system design and architecture, and prefer clean, structured, modular code with meaningful functions and clear responsibilities.
7. Reject solutions that introduce messy code or poor architecture. Favor maintainable designs with clear structure, strong boundaries, and readable implementations.
8. If asked to explain a function's inputs and outputs, include the input output data format and provide a concrete example of the input output data.
9. Add short comments in understandable English on parts that are not obvious or hard to follow. Use section dividers (e.g. `// ── Section name ──`) to separate logical groups. Do not over-comment obvious code.
10. Do not remove or rephrase existing comments unless explicitly asked. Preserve the user's original comments as-is.

---

# Acemate Robot Arm — MCP Tools Guide

You have access to MCP tools that control the Acemate robot arm — a real 6-DOF
manipulator with Damiao motors, connected via ros2_control and a
JointTrajectoryController.

## SAFETY RULES (always obey)

- This is REAL hardware. Use `duration_sec >= 2.0` for every movement.
- Never command positions outside joint limits.
- When uncertain about safety, ask the user before moving.
- Always call `get_joint_states()` first so you know where the arm is.

## Joint limits

| Joint   | Label            | Min (rad) | Max (rad) |
|---------|------------------|-----------|-----------|
| joint_1 | base rotation    | -3.14     | +3.14     |
| joint_2 | shoulder pitch   | -1.57     | +1.57     |
| joint_3 | elbow pitch      | -2.70     | +2.70     |
| joint_4 | wrist pitch      | -1.50     | +1.50     |
| joint_5 | wrist roll       | -0.50     | +0.50     |
| joint_6 | end-effector rot | -3.14     | +3.14     |

Note: exact limits are loaded from `joint_limits.yaml` at runtime. The table
above is approximate — the tools enforce the real values.

## Workflow when asked to move

1. Call `get_joint_states()` to read current positions.
2. Validate the target is within limits.
3. Call `move_joint()` or `move_all_joints()` to command the motion.
4. Report the result.

## Available MCP tools

- `get_joint_states` — current positions, velocities, and efforts
- `move_joint` — move one joint (others hold)
- `move_all_joints` — move all six joints at once
- `get_end_effector_pose` — Cartesian pose of tool0 via TF2
- `get_robot_description` — URDF kinematic chain
- `get_ros2_info` — ROS 2 distro and domain ID
- `list_topics`, `list_nodes`, `list_services`, `list_actions` — ROS 2 introspection
