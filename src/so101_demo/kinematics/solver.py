"""placo FK / IK on the URDF (vendored LeRobot RobotKinematics), in MJCF terms.

The solver speaks degrees and returns the URDF tip frame. Both are converted here and
nowhere else: `units` for the angles, `frames.T_URDF_TIP_TO_MJCF_SITE` for the pose.
"""

from __future__ import annotations

import numpy as np

from ..assets import urdf_path
from ..frames import (
    JOINT_NAMES,
    URDF_BASE_LINK,
    URDF_TIP_FRAME,
    mjcf_site_to_urdf_tip,
    urdf_tip_to_mjcf_site,
)
from ..units import deg_to_rad
from ..vendor.lerobot_kinematics import RobotKinematics
from .errors import IkRejected

# LeRobot's defaults for the soft frame task; recorded in docs/evidence/0a.
IK_MAX_ITERS = 8
IK_POSITION_WEIGHT = 1.0
IK_ORIENTATION_WEIGHT = 0.01


class So101Kinematics:
    """FK and raw IK for the five arm joints. No reachability judgement here."""

    def __init__(self, joint_limits_rad) -> None:
        """`joint_limits_rad`: (5, 2) array of [lower, upper] per joint in JOINT_NAMES order,
        e.g. `So101Sim().arm_limits_rad()`. Required: without limits no candidate could be
        checked, and 'not checked' must never read as 'within limits'.
        """
        self._solver = RobotKinematics(
            urdf_path=str(urdf_path()),
            target_frame_name=URDF_TIP_FRAME,
            joint_names=list(JOINT_NAMES),
        )
        limits = np.asarray(joint_limits_rad, dtype=float)
        if limits.shape != (len(JOINT_NAMES), 2) or not np.all(limits[:, 0] < limits[:, 1]):
            raise ValueError(f"joint_limits_rad must be (5, 2) with lower < upper, got {limits}")
        self.joint_limits_rad = limits

    @staticmethod
    def _as_angles(angles_deg) -> np.ndarray:
        angles = np.asarray(angles_deg, dtype=float)
        if angles.shape != (len(JOINT_NAMES),):
            raise ValueError(f"expected {len(JOINT_NAMES)} angles, got {angles.shape}")
        if not np.all(np.isfinite(angles)):
            raise IkRejected("not_finite", f"joint angles {angles}")
        return angles

    def base_pose_urdf(self) -> np.ndarray:
        """URDF `base_link` pose in placo's world (expected identity: fixed base)."""
        return np.array(self._solver.robot.get_T_world_frame(URDF_BASE_LINK))

    def fk_urdf_tip(self, angles_deg) -> np.ndarray:
        """URDF `gripper_frame_link` pose in the base frame (4x4, metres)."""
        return np.array(self._solver.forward_kinematics(self._as_angles(angles_deg)))

    def fk_mjcf_site(self, angles_deg) -> np.ndarray:
        """Tip pose in the MJCF site convention, comparable with MuJoCo's `gripperframe`."""
        return urdf_tip_to_mjcf_site(self.fk_urdf_tip(angles_deg))

    def ik_mjcf_site(
        self, T_mjcf_site: np.ndarray, seed_deg, *, max_iters: int = IK_MAX_ITERS
    ) -> np.ndarray:
        """Run the soft IK from `seed_deg` toward a pose given in MJCF site convention.

        Returns angles in degrees. They are only a candidate: the caller checks
        finiteness, joint ranges, position residual and approach direction.
        """
        seed = self._as_angles(seed_deg)
        # The vendored solver sets the seed joints but linearises the first Newton step
        # on whatever kinematics were last updated (the previous solve's final posture),
        # which made results depend on solve history (2026-09-19). Prime the kinematics
        # at the seed here so a solve depends only on (target, seed).
        for name, value in zip(JOINT_NAMES, deg_to_rad(seed), strict=True):
            self._solver.robot.set_joint(name, float(value))
        self._solver.robot.update_kinematics()
        return np.array(
            self._solver.inverse_kinematics(
                current_joint_pos=seed.copy(),
                desired_ee_pose=mjcf_site_to_urdf_tip(np.asarray(T_mjcf_site, dtype=float)),
                position_weight=IK_POSITION_WEIGHT,
                orientation_weight=IK_ORIENTATION_WEIGHT,
                max_iters=max_iters,
            )
        )

    def joints_within_limits(self, angles_deg) -> np.ndarray:
        """Boolean per joint against the limits given at construction."""
        angles_rad = deg_to_rad(self._as_angles(angles_deg))
        lo, hi = self.joint_limits_rad[:, 0], self.joint_limits_rad[:, 1]
        # 1e-6 rad absorbs the solver's rounding at a limit, not a real violation.
        return (angles_rad >= lo - 1e-6) & (angles_rad <= hi + 1e-6)
