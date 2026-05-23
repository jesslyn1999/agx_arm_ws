#!/usr/bin/env bash
# Wrapper that sources the ROS 2 workspace before starting the MCP server.
# Codex CLI spawns MCP servers in a clean environment, so we need this.
set -e
source /opt/ros/jazzy/setup.bash
source "$(dirname "$0")/../install/setup.bash" 2>/dev/null || true
exec ros2 run agx_arm_agent agx_arm_mcp_server "$@"
