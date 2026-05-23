from setuptools import setup
from glob import glob
import os

package_name = "agx_arm_agent"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.py")),
    ],
    install_requires=[
        "setuptools",
        "numpy",
        "pyyaml",
        "fastmcp",
    ],
    zip_safe=True,
    maintainer="root",
    maintainer_email="root@todo.todo",
    description="MCP server exposing AGX arm control tools for Codex CLI",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "agx_arm_mcp_server = agx_arm_agent.agx_arm_mcp_server:main",
        ],
    },
)
