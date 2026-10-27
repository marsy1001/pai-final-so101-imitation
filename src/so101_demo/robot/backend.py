from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

GRIPPER_MIN = 0.0  # fully closed (LeRobot RANGE_0_100)
GRIPPER_MAX = 100.0  # fully open


class NotCommandOwner(Exception):
    """A command arrived without the token of the current owner (single command source)."""


@dataclass(frozen=True)
class CommandToken:
    owner: str
    serial: int


@dataclass(frozen=True)
class RobotState:
    joints_deg: np.ndarray  # five arm joints, frames.JOINT_NAMES order
    gripper: float  # 0-100
    time_s: float
    moving: bool


class RobotBackend(Protocol):
    def acquire(self, owner: str) -> CommandToken: ...
    def release(self, token: CommandToken) -> None: ...
    def command_joints_deg(self, token: CommandToken, q_deg) -> None: ...
    def command_gripper(self, token: CommandToken, value: float) -> None: ...
    def state(self) -> RobotState: ...
    def stop(self, token: CommandToken) -> None: ...
    def reset(self) -> None: ...
    def tick(self, dt_s: float) -> None: ...
