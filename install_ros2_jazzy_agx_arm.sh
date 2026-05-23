#!/usr/bin/env bash

set -euo pipefail

WORKSPACE="${WORKSPACE:-$HOME/Documents/ROBOT/agx_arm_ws}"
REPO_DIR="$WORKSPACE/src/agx_arm_ros"

if [[ -n "${CONDA_PREFIX:-}" ]]; then
  echo "[ERROR] Detected active Conda env: $CONDA_PREFIX"
  echo "Please run: conda deactivate"
  echo "Then re-run this script."
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "[ERROR] Cannot detect OS version."
  exit 1
fi

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "noble" ]]; then
  echo "[WARN] This script is written for Ubuntu 24.04 (noble)."
  echo "Detected: ${PRETTY_NAME:-unknown}"
fi

echo "[1/8] Configure locale and base tools..."
sudo apt update
sudo apt install -y locales software-properties-common curl gnupg python3-pip
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
sudo add-apt-repository -y universe

echo "[2/8] Configure ROS 2 apt source (official ros-apt-source package)..."
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME}}"
SOURCE_CONFIGURED=0

# Try GitHub API first.
ROS_APT_SOURCE_VERSION="$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest 2>/dev/null | grep -F 'tag_name' | awk -F '"' '{print $4}' || true)"

# If API is rate-limited, fall back to resolving the latest release tag via redirect.
if [[ -z "${ROS_APT_SOURCE_VERSION}" ]]; then
  LATEST_RELEASE_URL="$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/ros-infrastructure/ros-apt-source/releases/latest 2>/dev/null || true)"
  ROS_APT_SOURCE_VERSION="$(basename "${LATEST_RELEASE_URL}")"
fi

if [[ -n "${ROS_APT_SOURCE_VERSION}" ]]; then
  DEB_FILE="/tmp/ros2-apt-source_${ROS_APT_SOURCE_VERSION}_${CODENAME}.deb"
  DEB_URL="https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.${CODENAME}_all.deb"
  if curl -fL -o "${DEB_FILE}" "${DEB_URL}"; then
    sudo dpkg -i "${DEB_FILE}"
    sudo apt update
    SOURCE_CONFIGURED=1
  fi
fi

# Fallback: configure ROS apt source directly.
if [[ "${SOURCE_CONFIGURED}" -ne 1 ]]; then
  echo "[WARN] ros-apt-source package fetch failed (likely GitHub API/network issue). Falling back to direct ROS apt source."
  curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key | sudo gpg --dearmor -o /usr/share/keyrings/ros-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu ${CODENAME} main" | sudo tee /etc/apt/sources.list.d/ros2.list >/dev/null
  sudo apt update
fi

echo "[3/8] Install ROS 2 Jazzy desktop and dev tools..."
sudo apt install -y ros-jazzy-desktop ros-dev-tools

if ! grep -q "source /opt/ros/jazzy/setup.bash" "$HOME/.bashrc"; then
  echo "source /opt/ros/jazzy/setup.bash" >> "$HOME/.bashrc"
fi

echo "[4/8] Install Python dependencies required by agx_arm_ros..."
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "[INFO] python3-pip is missing, installing..."
  sudo apt update
  sudo apt install -y python3-pip
fi
python3 -m pip install --user python-can scipy numpy --break-system-packages

echo "[5/8] Prepare agx_arm_ros workspace..."
mkdir -p "$WORKSPACE/src"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone -b ros2 --recurse-submodules https://github.com/agilexrobotics/agx_arm_ros.git "$REPO_DIR"
else
  git -C "$REPO_DIR" fetch --all --prune
  git -C "$REPO_DIR" checkout ros2
  git -C "$REPO_DIR" pull --ff-only
  git -C "$REPO_DIR" submodule update --init --recursive
fi
git -C "$REPO_DIR" submodule update --remote --recursive

echo "[6/8] Install agx_arm_ros apt dependencies..."
set +u
source /opt/ros/jazzy/setup.bash
set -u
export ROS_DISTRO=jazzy
sudo apt install -y \
  can-utils ethtool \
  ros-$ROS_DISTRO-ros2-control \
  ros-$ROS_DISTRO-ros2-controllers \
  ros-$ROS_DISTRO-controller-manager \
  ros-$ROS_DISTRO-topic-tools \
  ros-$ROS_DISTRO-joint-state-publisher-gui \
  ros-$ROS_DISTRO-robot-state-publisher \
  ros-$ROS_DISTRO-xacro \
  python3-colcon-common-extensions \
  ros-$ROS_DISTRO-moveit* \
  ros-$ROS_DISTRO-control* \
  ros-$ROS_DISTRO-joint-trajectory-controller \
  ros-$ROS_DISTRO-joint-state-* \
  ros-$ROS_DISTRO-gripper-controllers \
  ros-$ROS_DISTRO-trajectory-msgs

if ! grep -q "LC_NUMERIC=en_US.UTF-8" "$HOME/.bashrc"; then
  echo "export LC_NUMERIC=en_US.UTF-8" >> "$HOME/.bashrc"
fi

echo "[7/8] Build workspace..."
cd "$WORKSPACE"
set +u
source /opt/ros/jazzy/setup.bash
set -u
unset PYTHONHOME
colcon build --cmake-force-configure --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3

echo "[8/8] Done. Quick verification:"
echo "  source /opt/ros/jazzy/setup.bash"
echo "  source $WORKSPACE/install/setup.bash"
echo "  ros2 pkg list | grep agx_arm"
echo "  ros2 launch agx_arm_ctrl start_single_agx_arm.launch.py can_port:=can0 arm_type:=piper effector_type:=none tcp_offset:='[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]'"
