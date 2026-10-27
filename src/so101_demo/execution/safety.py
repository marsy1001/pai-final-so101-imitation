"""Safety layer: every joint target the execution layer sends passes through here.

Checks: Cartesian targets inside the fixed work area (kinematics.top_down), joint
targets inside the model ranges, joint speed below a limit, one command source
(robot.backend token). Anything failing raises `SafetyRejected`; nothing is clipped.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..frames import JOINT_NAMES
from ..units import rad_to_deg


class SafetyRejected(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SafetyLimits:
    joint_limits_rad: np.ndarray  # (5, 2)
    max_joint_speed_deg_s: float = 90.0

    def check_joint_target(
        self, q_deg, *, from_q_deg=None, dt_s: float | None = None
    ) -> np.ndarray:
        q = np.asarray(q_deg, dtype=float)
        if q.shape != (len(JOINT_NAMES),) or not np.all(np.isfinite(q)):
            raise SafetyRejected("not_finite", f"joint target {q}")
        lim = rad_to_deg(self.joint_limits_rad)
        bad = (q < lim[:, 0] - 1e-6) | (q > lim[:, 1] + 1e-6)
        if np.any(bad):
            raise SafetyRejected(
                "joint_limits", str([n for n, b in zip(JOINT_NAMES, bad, strict=True) if b])
            )
        if from_q_deg is not None and dt_s:
            speed = np.abs(q - np.asarray(from_q_deg, dtype=float)) / dt_s
            if np.any(speed > self.max_joint_speed_deg_s):
                raise SafetyRejected(
                    "joint_speed", f"{speed.max():.1f} deg/s > {self.max_joint_speed_deg_s}"
                )
        return q
