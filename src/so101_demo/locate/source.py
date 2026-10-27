from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class ObjectPose:
    object_id: str
    position_m: np.ndarray  # (3,) base frame
    rotation: np.ndarray  # (3, 3)
    linear_velocity: np.ndarray  # (3,) m/s
    angular_velocity: np.ndarray  # (3,) rad/s
    time_s: float


class PositionSource(Protocol):
    """Object id -> pose. Sim: ground truth. Real robot (stage 4): camera detection
    plus a frame transform. Destinations are fixed and come from the registry."""

    def pose_of(self, object_id: str) -> ObjectPose: ...
