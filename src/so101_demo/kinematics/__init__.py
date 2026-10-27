"""placo-based FK/IK for the SO-101 arm, reported in the MJCF frame convention."""

from .errors import IkRejected
from .solver import So101Kinematics
from .top_down import (
    WORKSPACE_MAX,
    WORKSPACE_MIN,
    grasp_direction_error_deg,
    grasp_yaw_deg,
    grasp_yaw_error_deg,
    solve_top_down,
    top_down_target,
    workspace_contains,
)

__all__ = [
    "WORKSPACE_MAX",
    "WORKSPACE_MIN",
    "IkRejected",
    "So101Kinematics",
    "grasp_direction_error_deg",
    "grasp_yaw_deg",
    "grasp_yaw_error_deg",
    "solve_top_down",
    "top_down_target",
    "workspace_contains",
]
