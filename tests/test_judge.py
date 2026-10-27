"""The 0b judge on fixed synthetic trajectories: every class and the success path."""

from dataclasses import replace

import numpy as np
import pytest

from so101_demo.execution import criteria as C
from so101_demo.execution.judge import Sample, inside_tray, judge
from so101_demo.plan import load_registry

REG = load_registry()
OBJ, DEST, OTHER = REG.objects["red_cube"], REG.destinations["box_a"], REG.destinations["box_b"]
DT = 0.02


IN_POCKET = (0.0, 0.0, 0.0295)  # object centre in the tip-site frame, measured 2026-09-19


def traj(spec, in_tip=IN_POCKET, constraints=None):
    """spec: list of (duration_s, obj_xyz, gripper, tcp_xyz, lin_v, ang_v) segments.

    Every sample carries the mandatory audit inputs: the object in the tip-site frame
    (`in_tip`, the measured carry pose by default) and the active-constraint count
    (`constraints(gripper)`; by default 1 while the jaws are closed, 0 once open, as
    the simplified grasp behaves)."""
    if constraints is None:

        def constraints(grip):
            return 1 if grip <= C.GRIPPER_CLOSED_MAX else 0

    out, t = [], 0.0
    for dur, xyz, grip, tcp, lv, av in spec:
        for _ in range(int(dur / DT)):
            t += DT
            out.append(
                Sample(
                    t,
                    np.array(xyz, float),
                    np.eye(3),
                    np.array(lv, float),
                    np.array(av, float),
                    grip,
                    np.array(tcp, float),
                    obj_in_tip=np.array(in_tip, float),
                    constraints_active=constraints(grip),
                )
            )
    return out


# Tray poses come from the registry (ADR 0008 moved the trays; the judge does not care where).
A = (*DEST.center_xy_m, 0.02)
A_TCP_LOW = (A[0], A[1] - 0.02, 0.02)  # TCP 20 mm beside the seated cube, as at lift
A_TCP_HIGH = (A[0], A[1] - 0.02, 0.07)
LIFTED = (0.19, 0.0, 0.07)


GOOD = [
    (1.0, (0.19, 0, 0.02), 100, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
    (1.0, LIFTED, 30, (0.19, -0.02, 0.07), (0, 0, 0), (0, 0, 0)),
    (1.0, A, 30, A_TCP_LOW, (0, 0, 0), (0, 0, 0)),
    (2.5, A, 100, A_TCP_HIGH, (0, 0, 0), (0, 0, 0)),
]


def good():
    return traj(GOOD)


def good_with(**kw):
    return traj(GOOD, **kw)


def test_success():
    v = judge(good(), OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.0)
    assert v.success and v.failure is None and v.lift_time is not None and v.settle_time is not None


def test_rejected_wins_over_everything():
    v = judge(
        good(), OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.0, rejected="outside_workspace"
    )
    assert v.failure == "rejected"


def test_no_lift_when_gripper_open_or_too_low():
    s = traj([(3.0, (0.19, 0, 0.049), 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0))])
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=2.0).failure == "no_lift"
    s = traj([(3.0, LIFTED, 100, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0))])
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=2.0).failure == "no_lift"


def test_dropped_is_separation_from_tcp_not_descent():
    s = traj(
        [
            (1.0, LIFTED, 30, (0.19, -0.02, 0.07), (0, 0, 0), (0, 0, 0)),
            (
                1.0,
                (0.19, 0, 0.02),
                30,
                (0.19, -0.02, 0.07),
                (0, 0, 0),
                (0, 0, 0),
            ),  # fell while the TCP stayed up
        ]
    )
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=2.0).failure == "dropped"
    s = traj(
        [
            (1.0, LIFTED, 30, (0.19, -0.02, 0.07), (0, 0, 0), (0, 0, 0)),
            (
                1.0,
                A,
                30,
                A_TCP_LOW,
                (0, 0, 0),
                (0, 0, 0),
            ),  # descended together: not a drop
            (2.5, A, 100, A_TCP_HIGH, (0, 0, 0), (0, 0, 0)),
        ]
    )
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=2.0).success


def test_wrong_destination_not_seated_not_settled_timeout():
    B = (*OTHER.center_xy_m, 0.02)
    s = traj(
        [
            (1.0, LIFTED, 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
            (2.5, B, 100, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
        ]
    )
    assert (
        judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=1.0).failure == "wrong_destination"
    )
    wall = (*DEST.center_xy_m, 0.0258)
    s = traj(
        [
            (1.0, LIFTED, 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
            (2.5, wall, 100, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
        ]
    )
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=1.0).failure == "not_seated"
    s = traj(
        [
            (1.0, LIFTED, 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
            (2.5, A, 100, (0.19, 0, 0.07), (0.02, 0, 0), (0, 0, 0)),
        ]
    )
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=1.0).failure == "not_settled"
    s = traj(
        [
            (1.0, LIFTED, 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
            (2.5, A, 100, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
        ]
    )
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=-C.TIMEOUT_S, release_t=1.0).failure == "timeout"


def test_deadline_is_the_first_failure_after_60s():
    # lift ok, release after the deadline, never settled -> timeout (not not_settled)
    s = traj([(1.0, LIFTED, 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0))])
    late = traj([(2.0, A, 100, A, (0.02, 0, 0), (0, 0, 0))])  # carried, not dropped
    for x in late:
        x.__dict__["t"] = x.t + 60.0
    v = judge(s + late, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=61.0)
    assert v.failure == "timeout" and v.details["would_be"] == "not_settled"
    # never lifted, samples run past the deadline -> timeout
    s = traj([(61.0, (0.19, 0, 0.02), 100, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0))])
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=None).failure == "timeout"
    # unsettled but the window closes well before the deadline -> not_settled
    s = traj(
        [
            (1.0, LIFTED, 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
            (2.5, A, 100, (0.19, 0, 0.07), (0.02, 0, 0), (0, 0, 0)),
        ]
    )
    assert judge(s, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=1.0).failure == "not_settled"
    # settled exactly at the deadline -> timeout
    v = judge(good(), OBJ, DEST, [OTHER], pick_cmd_t=-(C.TIMEOUT_S - 3.5), release_t=3.0)
    assert v.failure == "timeout"


def with_missing(samples, index, **fields):
    """Copy of `samples` with the audit fields of one sample replaced (e.g. by None)."""
    out = list(samples)
    out[index] = replace(out[index], **fields)
    return out


def carry_indices(samples, release_t):
    v = judge(samples, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=release_t)
    assert v.lift_time is not None and v.release_time is not None
    return [i for i, s in enumerate(samples) if v.lift_time <= s.t < v.release_time], v


def test_audit_passes_on_the_measured_carry_pose_with_every_sample_audited():
    samples = good()
    v = judge(samples, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.0)
    assert v.success and v.failure is None
    carry, _ = carry_indices(samples, 3.0)
    assert v.details["carry_samples_audited"] == len(carry) > 1
    assert v.details["release_samples_audited"] == sum(s.t >= v.release_time for s in samples)
    assert v.details["carry_max_zone_excursion_m"] == 0.0


def test_not_contained_when_the_object_leaves_the_grasp_zone():
    # 50 mm along the jaw-opening axis: 5 mm past GRASP_ZONE_Z_MAX_M
    v = judge(
        good_with(in_tip=(0.0, 0.0, 0.050)), OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.0
    )
    assert v.failure == "not_contained" and v.details["audit"] == "grasp_zone"
    assert v.details["zone_excursion_m"] == pytest.approx(0.005)


def test_not_contained_catches_the_far_object_the_drop_rule_cannot():
    """The drop rule only measures growth from the distance at lift time, so an object
    that was never near the gripper never trips it. Audit B is the absolute bound."""
    far = traj(
        [
            (1.0, (1.19, 0, 0.07), 30, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
            (2.5, (1.19, 0, 0.07), 100, (0.19, 0, 0.07), (0, 0, 0), (0, 0, 0)),
        ]
    )
    v = judge(far, OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=1.0)
    assert v.failure == "not_contained" and v.details["audit"] == "tcp_distance"
    assert v.details["tcp_distance_m"] == pytest.approx(1.0)


def test_still_constrained_when_a_constraint_survives_the_release():
    v = judge(
        good_with(constraints=lambda grip: 1), OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.0
    )
    assert v.failure == "still_constrained" and v.details["constraints_active"] == 1


def test_unaudited_when_every_sample_lacks_the_zone_input():
    v = judge(good_with(in_tip=None), OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.0)
    assert not v.success and v.failure == "unaudited" and v.details["audit"] == "grasp_zone"


def test_unaudited_when_one_carry_tick_lacks_the_zone_input():
    samples = good()
    carry, _ = carry_indices(samples, 3.0)
    for bad in (None, np.array([np.nan, 0.0, 0.0]), np.zeros(2)):
        v = judge(
            with_missing(samples, carry[len(carry) // 2], obj_in_tip=bad),
            OBJ,
            DEST,
            [OTHER],
            pick_cmd_t=0.0,
            release_t=3.0,
        )
        assert v.failure == "unaudited" and v.details["audit"] == "grasp_zone"
        assert v.details["at_s"] == pytest.approx(samples[carry[len(carry) // 2]].t)


def test_unaudited_when_every_sample_lacks_the_constraint_input():
    v = judge(
        good_with(constraints=lambda grip: None), OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.0
    )
    assert not v.success and v.failure == "unaudited" and v.details["audit"] == "constraints"


def test_unaudited_when_one_release_tick_lacks_the_constraint_input():
    samples = good()
    _, v0 = carry_indices(samples, 3.0)
    after = [i for i, s in enumerate(samples) if s.t >= v0.release_time]
    for bad in (None, -1, True):
        v = judge(
            with_missing(samples, after[len(after) // 2], constraints_active=bad),
            OBJ,
            DEST,
            [OTHER],
            pick_cmd_t=0.0,
            release_t=3.0,
        )
        assert v.failure == "unaudited" and v.details["audit"] == "constraints"
    # the last tick is audited too
    v = judge(
        with_missing(samples, after[-1], constraints_active=None),
        OBJ,
        DEST,
        [OTHER],
        pick_cmd_t=0.0,
        release_t=3.0,
    )
    assert v.failure == "unaudited"


def test_unaudited_is_never_reported_as_success_class():
    assert "unaudited" in C.FAILURE_CLASSES


def test_true_release_is_the_measured_jaw_opening_not_the_trace_label():
    """The trace label is logged one gripper wait after the open command; the windows
    are anchored on the first tick at which the measured jaws are open."""
    v = judge(good(), OBJ, DEST, [OTHER], pick_cmd_t=0.0, release_t=3.5)
    assert v.details["release_trace_s"] == 3.5
    assert v.details["release_open_s"] == pytest.approx(3.02)
    assert v.release_time == pytest.approx(3.02)  # 0.48 s earlier than the label


def test_inside_tray_uses_bottom_corners_not_centre():
    rot45 = np.array(
        [
            [np.cos(np.pi / 4), -np.sin(np.pi / 4), 0],
            [np.sin(np.pi / 4), np.cos(np.pi / 4), 0],
            [0, 0, 1],
        ]
    )
    centre = np.array([*DEST.center_xy_m, 0.02])
    assert inside_tray(centre, np.eye(3), OBJ, DEST)
    assert inside_tray(centre + [0.009, 0, 0], np.eye(3), OBJ, DEST)
    assert not inside_tray(centre + [0.011, 0, 0], np.eye(3), OBJ, DEST)
    assert inside_tray(centre, rot45, OBJ, DEST)  # 28.3 mm half-diagonal < 30 mm
    assert not inside_tray(centre + [0.003, 0, 0], rot45, OBJ, DEST)
