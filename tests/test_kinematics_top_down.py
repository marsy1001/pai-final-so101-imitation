"""Top-down IK inside the fixed work area solves; everything else is rejected explicitly."""

import itertools

import numpy as np
import pytest

from so101_demo.frames import JOINT_NAMES
from so101_demo.kinematics import (
    WORKSPACE_MAX,
    WORKSPACE_MIN,
    IkRejected,
    So101Kinematics,
    grasp_direction_error_deg,
    grasp_yaw_deg,
    grasp_yaw_error_deg,
    solve_top_down,
    top_down,
    top_down_target,
    workspace_contains,
)
from so101_demo.kinematics.top_down import evaluate_candidate
from so101_demo.sim import So101Sim
from so101_demo.tolerances import (
    IK_GRASP_DIR_TOL_DEG,
    IK_GRASP_YAW_TOL_DEG,
    IK_POS_RESIDUAL_TOL_M,
)

CENTRE = (WORKSPACE_MIN + WORKSPACE_MAX) / 2
CORNERS = [np.array(c) for c in itertools.product(*zip(WORKSPACE_MIN, WORKSPACE_MAX, strict=True))]


def test_approach_axis_is_site_local_x(sim: So101Sim):
    # The site rotation Ry(90 deg) maps site-local +X onto the gripper body's -Z, the
    # axis along which the tip site sits (local position mostly -Z). That body axis
    # is the direction the jaws face, i.e. the approach axis.
    sim.set_arm_joints_deg(np.zeros(5))
    R_body = sim.body_pose("gripper")[:3, :3]
    R_site = sim.tip_pose()[:3, :3]
    approach_in_body = R_body.T @ R_site @ top_down.APPROACH_AXIS_LOCAL
    np.testing.assert_allclose(approach_in_body, [0.0, 0.0, -1.0], atol=1e-6)
    tip_in_body = sim.tip_site_local_pos()
    assert tip_in_body[2] < 0 and abs(tip_in_body[2]) > 5 * np.linalg.norm(tip_in_body[:2])


def test_top_down_target_points_straight_down():
    T = top_down_target([0.2, 0.05, 0.04])
    assert grasp_direction_error_deg(T) < 1e-9
    assert np.allclose(T[:3, 3], [0.2, 0.05, 0.04])
    R = T[:3, :3]
    assert np.allclose(R @ R.T, np.eye(3)) and np.isclose(np.linalg.det(R), 1.0)


@pytest.mark.parametrize("yaw", [0.0, 30.0, -22.0, 90.0])
def test_top_down_target_jaw_axis_follows_the_yaw_not_the_azimuth(yaw):
    # Issue #21: the jaw-opening direction (site +Z) is the requested yaw in the base
    # frame, independent of where the target is (it used to point radially outward).
    for p in ([0.2, 0.05, 0.04], [0.16, -0.09, 0.02], [0.22, 0.09, 0.07]):
        T = top_down_target(p, yaw)
        assert grasp_yaw_error_deg(T, yaw) < 1e-9
        assert abs(((grasp_yaw_deg(T) - yaw + 180) % 360) - 180) < 1e-9
        assert grasp_direction_error_deg(T) < 1e-9
        assert np.isclose(T[2, 2], 0.0)  # the jaw axis is horizontal


def test_top_down_target_default_yaw_is_along_base_x():
    T = top_down_target([0.19, 0.0, 0.07])
    np.testing.assert_allclose(T[:3, 2], [1.0, 0.0, 0.0], atol=1e-12)


@pytest.mark.parametrize(
    "point", [CENTRE, *CORNERS], ids=["centre"] + [f"corner{i}" for i in range(8)]
)
def test_top_down_ik_solves_inside_workspace(sim: So101Sim, kin: So101Kinematics, point):
    q = solve_top_down(kin, point)
    assert q.shape == (len(JOINT_NAMES),)
    assert np.all(np.isfinite(q))
    assert np.all(kin.joints_within_limits(q))
    residual, direction, yaw_err = evaluate_candidate(kin, q, point)
    assert residual <= IK_POS_RESIDUAL_TOL_M
    assert direction <= IK_GRASP_DIR_TOL_DEG
    assert yaw_err <= IK_GRASP_YAW_TOL_DEG
    # MuJoCo agrees on where those angles put the tip, and on the jaw direction.
    sim.set_arm_joints_deg(q)
    assert np.linalg.norm(sim.tip_pose()[:3, 3] - point) <= IK_POS_RESIDUAL_TOL_M + 1e-3
    assert grasp_yaw_error_deg(sim.tip_pose(), 0.0) <= IK_GRASP_YAW_TOL_DEG + 1.0


@pytest.mark.parametrize("yaw", [0.0, 15.0, -25.0])
@pytest.mark.parametrize(
    "point", [CENTRE, *CORNERS], ids=["centre"] + [f"corner{i}" for i in range(8)]
)
def test_top_down_ik_reaches_the_requested_yaw(kin: So101Kinematics, point, yaw):
    # Issue #21: pick uses the cube's yaw, place the tray's; the transfer interpolates
    # between them, so the solver must hold any yaw in this range across the work area.
    q = solve_top_down(kin, point, yaw)
    _, _, yaw_err = evaluate_candidate(kin, q, point, yaw)
    assert yaw_err <= IK_GRASP_YAW_TOL_DEG
    assert grasp_yaw_error_deg(kin.fk_mjcf_site(q), yaw) == pytest.approx(yaw_err)


def test_wrong_yaw_is_rejected_as_grasp_yaw(kin: So101Kinematics):
    # A solution for yaw 0 checked against yaw 30: position and approach are fine, only
    # the rotation about the approach axis is off, and that is named in the rejection.
    q = solve_top_down(kin, CENTRE, 0.0)
    with pytest.raises(IkRejected) as e:
        evaluate_candidate(kin, q, CENTRE, 30.0)
    assert e.value.reason == "grasp_yaw"
    assert "30" in e.value.detail


def test_wrist_roll_seed_follows_yaw_minus_azimuth(kin: So101Kinematics):
    # In a top-down pose a positive wrist_roll turns the jaw axis toward +y by the same
    # angle (measured 2026-09-20); the seed uses that sign so the local solver starts
    # in the right basin.
    home = solve_top_down(kin, (0.19, 0.0, 0.07), 0.0)
    T0 = kin.fk_mjcf_site(home)
    T1 = kin.fk_mjcf_site(home + np.array([0.0, 0.0, 0.0, 0.0, 30.0]))
    turned = grasp_yaw_deg(T1) - grasp_yaw_deg(T0)
    assert np.sign(turned) == np.sign(top_down.WRIST_ROLL_PER_YAW * 30.0)
    assert abs(abs(turned) - 30.0) < 3.0
    p = np.array([0.20, 0.08, 0.03])
    azimuth = np.degrees(np.arctan2(p[1], p[0]))
    q = solve_top_down(kin, p, 0.0)
    assert abs(q[4] - top_down.WRIST_ROLL_PER_YAW * (0.0 - azimuth)) < 10.0


def test_workspace_contains_is_closed_box():
    assert workspace_contains(WORKSPACE_MIN) and workspace_contains(WORKSPACE_MAX)
    assert not workspace_contains(WORKSPACE_MAX + [0.001, 0, 0])
    # rounding tolerance only (Issue #21): a settled cube at y = -0.09 reads -0.09 - 2e-11
    assert workspace_contains(WORKSPACE_MIN - [0, 2e-11, 0])
    assert not workspace_contains(WORKSPACE_MIN - [0, 1e-5, 0])
    assert not workspace_contains(WORKSPACE_MAX + [0, 1e-5, 0])


@pytest.mark.parametrize(
    "point",
    [
        WORKSPACE_MAX + [0.05, 0.0, 0.0],
        WORKSPACE_MIN - [0.0, 0.05, 0.0],
        [CENTRE[0], CENTRE[1], WORKSPACE_MAX[2] + 0.10],
        [0.45, 0.0, 0.02],
    ],
    ids=["beyond_x", "beyond_y", "above_z", "far_out_of_reach"],
)
def test_target_outside_workspace_is_rejected(kin: So101Kinematics, point):
    with pytest.raises(IkRejected) as e:
        solve_top_down(kin, point)
    assert e.value.reason == "outside_workspace"


def test_nan_target_is_rejected(kin: So101Kinematics):
    with pytest.raises(IkRejected) as e:
        solve_top_down(kin, [np.nan, 0.0, 0.05])
    assert e.value.reason == "not_finite"
    with pytest.raises(IkRejected) as e:
        solve_top_down(kin, CENTRE, np.nan)
    assert e.value.reason == "not_finite"


def test_nan_joint_angles_are_rejected(kin: So101Kinematics):
    with pytest.raises(IkRejected) as e:
        kin.fk_mjcf_site([0.0, np.nan, 0.0, 0.0, 0.0])
    assert e.value.reason == "not_finite"


def test_joint_angles_outside_limits_are_rejected(kin: So101Kinematics):
    bad = np.zeros(5)
    bad[1] = 150.0  # shoulder_lift range is +-100 deg
    assert not kin.joints_within_limits(bad)[1]
    with pytest.raises(IkRejected) as e:
        evaluate_candidate(kin, bad, CENTRE)
    assert e.value.reason == "joint_limits"
    assert "shoulder_lift" in e.value.detail


def test_position_residual_over_tolerance_is_rejected(kin: So101Kinematics):
    # The zero posture leaves the tip far from the work-area centre.
    with pytest.raises(IkRejected) as e:
        evaluate_candidate(kin, np.zeros(5), CENTRE)
    assert e.value.reason == "position_residual"


def test_grasp_direction_over_tolerance_is_rejected(kin: So101Kinematics, monkeypatch):
    # Same candidate, but with the position check disabled the horizontal approach of
    # the zero posture must still be refused by the direction check.
    monkeypatch.setattr(top_down, "IK_POS_RESIDUAL_TOL_M", 10.0)
    with pytest.raises(IkRejected) as e:
        evaluate_candidate(kin, np.zeros(5), CENTRE)
    assert e.value.reason == "grasp_direction"


def test_shoulder_pan_seed_follows_target_yaw(kin: So101Kinematics):
    # pan and tip yaw have opposite signs on this model; the seed must use that sign.
    T = kin.fk_mjcf_site([30.0, 0.0, 0.0, 0.0, 0.0])
    tip_yaw = np.degrees(np.arctan2(T[1, 3], T[0, 3]))
    assert np.sign(tip_yaw) == np.sign(top_down.SHOULDER_PAN_PER_YAW * 30.0)
    p = np.array([0.20, 0.08, 0.03])
    q = solve_top_down(kin, p)
    yaw = np.degrees(np.arctan2(p[1], p[0]))
    assert abs(q[0] - top_down.SHOULDER_PAN_PER_YAW * yaw) < 10.0


def test_all_seeds_failing_is_reported_as_rejection(kin: So101Kinematics, monkeypatch):
    # Inside the box, but no candidate can pass an impossible direction tolerance: the
    # seed loop must end in IkRejected naming the check and the number of seeds tried.
    monkeypatch.setattr(top_down, "IK_GRASP_DIR_TOL_DEG", -1.0)
    with pytest.raises(IkRejected) as e:
        solve_top_down(kin, CENTRE)
    assert e.value.reason == "grasp_direction"
    assert f"(all {len(top_down.IK_SEEDS_DEG)} seeds)" in e.value.detail


def test_kinematics_requires_joint_limits():
    with pytest.raises((ValueError, TypeError)):
        So101Kinematics(None)


def test_returned_angles_are_not_assumed_reachable(kin: So101Kinematics):
    # The raw solver returns angles for an unreachable target; solve_top_down does not.
    far = np.array([0.45, 0.0, 0.02])
    raw = kin.ik_mjcf_site(top_down_target(far), np.zeros(5))
    assert np.all(np.isfinite(raw))
    with pytest.raises(IkRejected):
        solve_top_down(kin, far)
