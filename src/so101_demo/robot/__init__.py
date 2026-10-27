"""Replacement boundary 1: the RobotBackend contract and its MuJoCo adapter.

Contract (docs/robot-backend.md): five arm joints in `frames.JOINT_NAMES` order,
degrees; gripper 0-100 (0 closed, 100 open); state; stop; a single command source
enforced by a token. The backend calls no IK: the execution layer computes joint
targets and hands them over.
"""

from .backend import CommandToken, NotCommandOwner, RobotBackend, RobotState
from .mujoco_backend import MujocoBackend

__all__ = ["CommandToken", "MujocoBackend", "NotCommandOwner", "RobotBackend", "RobotState"]
