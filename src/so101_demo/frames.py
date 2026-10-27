"""Names and the one fixed transform between the URDF tip frame and the MJCF tip site.

Both files place the tip at the same local position under the gripper body, but the
URDF fixed joint `gripper_frame_joint` uses rpy="0 pi 0" (Ry(180 deg)) while the MJCF
site `gripperframe` uses quat=(0.707107, 0, 0.707107, 0) (Ry(90 deg)). Hence
    T_world_mjcf_site = T_world_urdf_tip @ T_URDF_TIP_TO_MJCF_SITE,   R = Ry(-90 deg).
Derived from the two model files (2026-09-19); verified numerically by
tests/test_kinematics_fk_agreement.py.
"""

from __future__ import annotations

import numpy as np

# Five arm joints in the order used everywhere (matched by name, never by file order).
JOINT_NAMES = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
GRIPPER_JOINT = "gripper"

URDF_BASE_LINK = "base_link"
URDF_TIP_FRAME = "gripper_frame_link"
MJCF_BASE_BODY = "base"
MJCF_TIP_SITE = "gripperframe"

# Local tip position stated by both files (metres, in the gripper body frame).
TIP_LOCAL_POS = np.array([-0.0079, -0.000218121, -0.0981274])


def rot_y(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def make_transform(rotation: np.ndarray, translation=(0.0, 0.0, 0.0)) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = rotation
    T[:3, 3] = translation
    return T


T_URDF_TIP_TO_MJCF_SITE = make_transform(rot_y(-np.pi / 2))
T_MJCF_SITE_TO_URDF_TIP = np.linalg.inv(T_URDF_TIP_TO_MJCF_SITE)


def urdf_tip_to_mjcf_site(T_urdf_tip: np.ndarray) -> np.ndarray:
    return T_urdf_tip @ T_URDF_TIP_TO_MJCF_SITE


def mjcf_site_to_urdf_tip(T_mjcf_site: np.ndarray) -> np.ndarray:
    return T_mjcf_site @ T_MJCF_SITE_TO_URDF_TIP


def rotation_angle_deg(R_a: np.ndarray, R_b: np.ndarray) -> float:
    """Angle of the rotation taking R_a to R_b (degrees)."""
    R = R_a.T @ R_b
    cos = np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)))


def position_error_m(T_a: np.ndarray, T_b: np.ndarray) -> float:
    return float(np.linalg.norm(T_a[:3, 3] - T_b[:3, 3]))
