"""placo FK on the URDF agrees with MuJoCo's tip site through one fixed transform.

Tolerances: so101_demo.tolerances (fixed by the user on 2026-09-19, Issue #3).
"""

import xml.etree.ElementTree as ET

import numpy as np
import pytest

from so101_demo.assets import urdf_path
from so101_demo.frames import (
    T_URDF_TIP_TO_MJCF_SITE,
    TIP_LOCAL_POS,
    position_error_m,
    rot_y,
    rotation_angle_deg,
)
from so101_demo.kinematics import So101Kinematics
from so101_demo.kinematics.postures import agreement_postures_deg, posture_names
from so101_demo.sim import So101Sim
from so101_demo.tolerances import FK_POS_TOL_M, FK_ROT_TOL_DEG


def _urdf_tip_joint_origin() -> tuple[np.ndarray, np.ndarray]:
    root = ET.parse(urdf_path()).getroot()
    joint = next(j for j in root.iter("joint") if j.get("name") == "gripper_frame_joint")
    origin = joint.find("origin")
    xyz = np.array([float(v) for v in origin.get("xyz").split()])
    rpy = np.array([float(v) for v in origin.get("rpy").split()])
    return xyz, rpy


def test_both_files_declare_the_same_local_tip_position(sim: So101Sim):
    xyz, _ = _urdf_tip_joint_origin()
    np.testing.assert_allclose(xyz, TIP_LOCAL_POS, atol=1e-9)
    np.testing.assert_allclose(sim.tip_site_local_pos(), TIP_LOCAL_POS, atol=1e-9)


def test_fixed_transform_matches_the_declared_local_rotations(sim: So101Sim):
    # URDF: rpy="0 pi 0" -> Ry(pi). MJCF: quat=(0.707107, 0, 0.707107, 0) -> Ry(pi/2).
    _, rpy = _urdf_tip_joint_origin()
    assert rpy[0] == 0 and rpy[2] == 0
    R_urdf = rot_y(rpy[1])
    w, x, y, z = sim.model.site_quat[sim._site_id]
    R_mjcf = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    expected = R_urdf.T @ R_mjcf
    assert rotation_angle_deg(expected, T_URDF_TIP_TO_MJCF_SITE[:3, :3]) < 1e-3
    assert np.allclose(T_URDF_TIP_TO_MJCF_SITE[:3, 3], 0.0)


def test_both_bases_are_at_the_world_origin(sim: So101Sim, kin: So101Kinematics):
    np.testing.assert_allclose(sim.base_pose(), np.eye(4), atol=1e-9)
    np.testing.assert_allclose(kin.base_pose_urdf(), np.eye(4), atol=1e-9)


@pytest.mark.parametrize("name", posture_names())
def test_fk_agrees_within_tolerance(sim: So101Sim, kin: So101Kinematics, name: str):
    q_deg = dict(agreement_postures_deg(sim.arm_limits_rad()))[name]
    sim.set_arm_joints_deg(q_deg)
    T_mj = np.linalg.inv(sim.base_pose()) @ sim.tip_pose()
    T_pl = np.linalg.inv(kin.base_pose_urdf()) @ kin.fk_mjcf_site(q_deg)
    assert position_error_m(T_pl, T_mj) <= FK_POS_TOL_M
    assert rotation_angle_deg(T_pl[:3, :3], T_mj[:3, :3]) <= FK_ROT_TOL_DEG


def test_at_least_five_postures_are_checked(sim: So101Sim):
    postures = agreement_postures_deg(sim.arm_limits_rad())
    assert len(postures) >= 5
    assert [n for n, _ in postures] == posture_names()
